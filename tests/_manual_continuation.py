"""Reusable public T/F handoff scenario; callers supply their exact installed runner.

All mutation and complete-file snapshots are confined to a fresh disposable project.
No repository governance or historical installed-pair pins are inputs.
"""
import hashlib
import json
import os
from pathlib import Path

import yaml

from frutlups import gitws, ledger, procs, roadmap


FROZEN = ("title", "objective", "acceptance", "non_goals", "read_first",
          "allowed_prefixes", "forbidden", "focused", "full", "runtime",
          "timeout_seconds", "observation", "notes", "memory")


def snapshot(root, cfg):
    files = {}
    # This root is the caller's disposable test project, including its ignored
    # fake-seat counters and jobs. The Git object store is not recursively read.
    for folder, directories, names in os.walk(root):
        if Path(folder) == root:
            directories.remove(".git")
        for name in names:
            path = Path(folder) / name
            assert not path.is_symlink()
            data = path.read_bytes()
            files[path.relative_to(root).as_posix()] = [len(data), hashlib.sha256(data).hexdigest()]
    return dict(head=gitws.head(root, executable=cfg.git),
                index=gitws._git(root, "ls-files", "--stage", "-z", executable=cfg.git).stdout.hex(),
                raw_index=hashlib.sha256((root / ".git/index").read_bytes()).hexdigest(), files=files)


def run_case(root, cfg, output, *, python, runner, first_issuer, resumed_issuer,
             blocked_result, expected_version="0.4.2", forbidden_write=False, env=None):
    """Run real public commands; no patched runner or synthetic ledger events.

    `root` is a final-template project with configured local fake executables,
    an autonomy profile and committed approval-R001.md. `runner` is argv for
    the source public CLI or the isolated installed console executable.
    """
    root, output = Path(root), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    env = gitws.controlled_environment(os.environ if env is None else env)
    env.pop("FRUTLUPS_SEAT", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["TEMPLATE_GIT_EXECUTABLE"] = cfg.git
    transitions = []
    preserved = {}

    def execute(label, argv, *, stable=False):
        folder = output / label
        folder.mkdir()
        before = snapshot(root, cfg)
        (folder / "before.json").write_text(json.dumps(before, sort_keys=True), encoding="utf-8")
        result = procs.run_process([str(a) for a in argv], root, env, 600,
                                   folder / "stdout.txt", folder / "stderr.txt")
        after = snapshot(root, cfg)
        (folder / "after.json").write_text(json.dumps(after, sort_keys=True), encoding="utf-8")
        assert not result.timed_out and result.exception is None
        assert not result.stdout_truncated and not result.stderr_truncated
        text = result.stdout_path.read_text("utf-8")
        error = result.stderr_path.read_text("utf-8")
        transitions.append(dict(label=label, argv=list(map(str, argv)), exit=result.exit,
                                stable=before == after))
        (output / "transitions.json").write_text(json.dumps(transitions, indent=2), encoding="utf-8")
        if stable:
            assert before == after, label
        return result.exit, text, error

    def t(label, *args, script="ledger.py", stable=False):
        return execute(label, [python, "-B", f"scripts/{script}", "--root", root, *args], stable=stable)

    def f(label, *args, stable=False):
        return execute(label, [*runner, *args], stable=stable)

    def state():
        for path, raw in preserved.items():
            assert (root / path).read_bytes() == raw, path
        events = ledger.read(root / cfg.ledger)
        assert not any(e.ev == "artifact" and e.data.get("path", "").endswith("_r1_dispatch.json")
                       for e in events)
        return events, ledger.validate_history(root, events, roadmap.load(root / "roadmap.yaml"), executable=cfg.git)

    def opposite(label):
        assert t(label + "-t-check", "check", stable=True) == (0, "ledger: ok\n", "")
        code, text, error = f(label + "-f-status", "status", root, "--json", stable=True)
        assert code == 0, (text, error)
        run = next(row for row in map(json.loads, text.splitlines()) if row["action"] == "run_status")
        code, text, error = t(label + "-t-status", "status", stable=True)
        assert code == 0, (text, error)
        remaining = " ".join(f"{key}={run['remaining'][key]}" for key in
                             ("jobs", "transport_retries", "format_retries", "seconds"))
        assert (f"run R001 revision={run['revision']} stop=M001 state={run['state']} "
                f"remaining {remaining}") in text

    def prompt(label, issuer):
        if issuer == "T":
            code, text, error = t(label, "M001-S01", script="prompt.py")
        else:
            code, text, error = f(label, "run", root, "--once", "--json")
        assert code == 0, (text, error)
        events, current = state()
        item = current.slices["M001-S01"]
        assert item.step == "coding"
        return ledger.envelope(root, ledger._thaw(item.envelope), item.id, item.round)

    assert execute("version", [python, "-B", "-c",
        "import frutlups; print(frutlups.__version__)"], stable=True) == (0, expected_version + "\n", "")
    code, text, error = t("admit", "run", "admit", "M001", "--run-id", "R001",
        "--slice", "M001-S01", "--stop", "M001", "--jobs", 20,
        "--transport-retries", 1, "--format-retries", 1, "--seconds", 900,
        "--max-corrective-rounds", 2, "--authority", "05_governance/approval-R001.md",
        "--reason", "Owner approved offline continuation")
    assert code == 0, (text, error)
    initial = prompt("initial-prompt", first_issuer)
    assert len(FROZEN) == 14
    _, issued = state()
    consumed = dict(issued.runs["R001"]["consumed"])
    assert not any(a["start"]["role"] == "coder" for a in issued.attempts.values())
    assert t("manual-block", "blocked", "M001-S01", "--result", blocked_result,
        "--requirement", "Wait for owned authority", "--reason", "Missing external input",
        "--actor", "human", "--action", "Supply authority")[0] == 0
    events, blocked = state()
    issued = next(e for e in events if e.ev == "prompt")
    paths = [issued.data["path"], issued.data["envelope"]["path"],
             blocked.slices["M001-S01"].outcome["path"], blocked.slices["M001-S01"].manifest["path"]]
    preserved.update({path: (root / path).read_bytes() for path in paths})
    opposite("blocked")
    code, text, error = f("unresolved-refusal", "run", root, "--once", "--json", stable=True)
    assert code == 3, (code, text, error)
    assert json.loads(text)["reason"] == "blocked_verdict"
    assert state()[1].runs["R001"]["consumed"] == consumed

    # Change live roadmap instructions while preserving a valid roadmap. The
    # resumed contract must still use every original field, including its fence.
    data = yaml.safe_load((root / "roadmap.yaml").read_text("utf-8"))
    item = data["milestones"][0]["slices"][0]
    item.update(title="Unadmitted replacement", objective="Unadmitted objective",
                acceptance=["Do not adopt this replacement"], non_goals=["Replacement non-goal"],
                read_first=["00_brief/decisions.md"], allowed_prefixes=["07_app/", "08_other/"],
                notes="Replacement notes", focused=[["python", "-c", "print('replacement')"]])
    data["allowed_prefixes"] = ["07_app/", "08_other/"]
    data["verification"].update(full=["python", "-c", "raise SystemExit('unadmitted')"])
    (root / "roadmap.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    authority = "questions/answered/manual-resolution.md"
    (root / authority).write_text("Owner authorizes continuation of the frozen work.\n", encoding="utf-8")
    gitws._git(root, "add", ".", executable=cfg.git)
    gitws._git(root, "commit", "-qm", "owned resolution authority and roadmap steering", executable=cfg.git)
    assert t("resolve", "resolve", "M001-S01", "--reason", "Owned requirement resolved",
             "--authority", authority)[0] == 0
    continued = prompt("resumed-prompt", resumed_issuer)
    assert continued["round"] == 2
    for key in FROZEN:
        assert continued[key] == initial[key], key
    opposite("resumed")
    _, before = state()
    assert not any(a["start"]["role"] == "coder" for a in before.attempts.values())
    if forbidden_write:
        (root / "local_state/plan.json").write_text(json.dumps({
            "coder": [{"writes": {"08_other/unadmitted.txt": "forbidden\n"}}]}), encoding="utf-8")
    code, text, error = f("dispatch", "run", root, "--once", "--json")
    events, coded = state()
    if forbidden_write:
        assert code == 3, (code, text, error)
        assert json.loads(text)["reason"] == "path_violation"
        assert "forbidden" in text
        assert not any(e.ev == "coded" and e.round == 2 for e in events)
        assert (root / "08_other/unadmitted.txt").read_text() == "forbidden\n"
        assert len([a for a in coded.attempts.values() if a["start"]["role"] == "coder"]) == 1
        return
    assert code == 0, (text, error)
    assert coded.slices["M001-S01"].step == "verifying"
    dispatch = next(e for e in events if e.ev == "artifact" and e.data["path"].endswith("_r2_dispatch.json"))
    policy = ledger.read_reference(root, dict(path=dispatch.data["path"], sha=dispatch.data["sha"]))
    outcome = ledger.read_reference(root, ledger._thaw(coded.slices["M001-S01"].outcome))
    assert outcome["invocation"] == policy["invocation"]
    assert coded.attempts[outcome["invocation"]]["finish"]["status"] == "completed"
    opposite("coded")
    code, text, error = f("finish-slice", "run", root, "--until", "slice", "--json")
    assert code == 0, (text, error)
    events, accepted = state()
    assert accepted.slices["M001-S01"].step == "accepted"
    verified = next(e for e in events if e.ev == "verified" and e.round == 2)
    receipt = ledger.read_reference(root, dict(path=verified.data["receipt"], sha=verified.data["sha"]))
    assert receipt["ok"] and receipt["round"] == 2
    assert receipt["commands"][0]["argv"] == initial["full"]
    assert receipt["manifest"] == ledger._thaw(accepted.slices["M001-S01"].manifest)
    verification = [a for a in accepted.attempts.values() if a["start"]["role"] == "verification"]
    assert len(verification) == 1
    attempt = verification[0]
    assert attempt["start"]["scope"] == "M001-S01" and attempt["start"]["round"] == 2
    assert attempt["finish"]["status"] == "completed"
    assert ledger._thaw(attempt["finish"]["evidence"]) == [dict(path=verified.data["receipt"], sha=verified.data["sha"])]
    assert Path(verified.data["receipt"]).name == attempt["start"]["invocation"] + "_verification.json"
    opposite("accepted")
    code, text, error = f("finish-milestone", "run", root, "--json")
    assert code == 0, (text, error)
    final_events, final = state()
    assert final.milestones_done == {"M001"}
    assert len([a for a in final.attempts.values() if a["start"]["role"] == "coder"]) == 1
    assert (root / "local_state/coder-count.txt").read_text() == "1"
    charged = len([a for a in final.attempts.values() if a["start"]["role"] != "verification"])
    assert final.runs["R001"]["consumed"]["jobs"] == charged
    assert len([e for e in final_events if e.ev == "accepted"]) == 1
    assert f("idle-restart", "run", root, "--json", stable=True)[0] == 0
    opposite("final")
