"""Actual public native runner in fresh processes on the final template export.

The public ``frutlups`` entry point runs unpatched. Fake Pi/Claude seats are real
configured executables, verification is the frozen receipt command, and the
final template's own ``scripts/ledger.py`` grants authority and validates the
emitted /2 history as the opposite consumer. Crash cases inject one exception
at a named durable boundary in a separate process, then restart unpatched.
"""
import json
import os
import shutil
import stat
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from frutlups import commit, gitws, ledger, loop, procs, roadmap
from frutlups.config import Config
from frutlups.ownership import writer_lock
# Fixtures and a helper only; importing no test function keeps original nodes single.
from test_final_template_v2 import checker_pin, assert_final_project  # noqa: F401
from test_checker_v2 import checker_project  # noqa: F401
from test_historical_deadlines_v2 import owned_history  # noqa: F401

HERE = Path(__file__).parent
STOP_BOUNDARY = "milestone completed; mark roadmap milestone done separately"
# A fresh process that finds the admitted roadmap complete stops as done.
DONE = {"action": "stop", "reason": "done", "detail": "all admitted roadmap milestones completed"}

SHIMS = {
    # Pi receives the bound prompt as ``@path``; the shared fake reads that path.
    "pi": ("import runpy, sys\nsys.path.insert(0, {tests!r})\n"
           "prompt = next(a[1:] for a in sys.argv[1:] if a.startswith('@'))\n"
           "sys.argv = [{script!r}, prompt]\nrunpy.run_path({script!r}, run_name='__main__')\n"),
    # Claude receives the prompt plus exact dispatch framing on stdin.
    "claude": ("import runpy, sys\nsys.path.insert(0, {tests!r})\n"
               "sys.argv = [{script!r}]\nrunpy.run_path({script!r}, run_name='__main__')\n"),
}

# One exception at the approved-commit boundary, in its own process; restarts are unpatched.
CRASH = r"""
import sys
from frutlups import commit
from frutlups.cli import main
recover = commit._recover
def interrupted(*args, **kwargs):
    rows = commit.status(args[0], args[1], args[2], executable=kwargs.get("executable", "git"))
    if any(row["state"] == "pending" and row["scope"] == sys.argv[2] for row in rows):
        raise RuntimeError("owned crash before approved commit")
    return recover(*args, **kwargs)
commit._recover = interrupted
main(["run", sys.argv[1], "--json"])
"""


def fake_seat(directory, name):
    """A real configured seat executable that runs the shared fake CLI."""
    directory.mkdir(exist_ok=True)
    body = SHIMS[name].format(tests=str(HERE), script=str(HERE / f"fake_{name}.py"))
    if os.name == "nt":
        import setuptools
        launcher = directory / f"{name}.exe"
        shutil.copyfile(Path(setuptools.__file__).parent / "cli-64.exe", launcher)
        (directory / f"{name}-script.py").write_text(f'#!"{sys.executable}" -B\n' + body, encoding="utf-8")
        return launcher
    path = directory / name
    path.write_text(f"#!{sys.executable} -B\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def environment():
    env = gitws.controlled_environment(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("FRUTLUPS_SEAT", None)
    return env


@pytest.fixture
def evidence(tmp_path_factory, request):
    """Append observed elapsed public/seat/verifier times to external scratch."""
    path = tmp_path_factory.getbasetemp() / "public_timings.jsonl"
    def record(**row):
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"test": request.node.name, **row}) + "\n")
    return record


def cli(root, output, *args, record=None, timeout=600, argv=None):
    """One fresh public CLI process: the installed console script when present."""
    script = Path(sys.executable).with_name("frutlups.exe" if os.name == "nt" else "frutlups")
    command = argv or ([str(script)] if script.is_file()
                       else [sys.executable, "-B", "-c", "from frutlups.cli import main; main()"])
    output.mkdir(parents=True)
    started = time.monotonic()
    result = procs.run_process([*command, *map(str, args)], root, environment(), timeout,
                               output / "stdout", output / "stderr")
    elapsed = round(time.monotonic() - started, 3)
    if record:
        record(label=output.name, args=[str(a) for a in args if str(a) != str(root)],
               exit=result.exit, timed_out=result.timed_out, elapsed=elapsed)
    if timeout == 600:
        assert (result.timed_out, result.exception) == (False, None)
    return result.exit, result.stdout_path.read_text("utf-8"), result.stderr_path.read_text("utf-8")


def rows(output):
    return [json.loads(line) for line in output.splitlines()]


def template(root, cfg, output, *args):
    """The final template's own ledger CLI: owner authority and opposite consumer."""
    env = environment()
    env["TEMPLATE_GIT_EXECUTABLE"] = cfg.git
    output.mkdir(parents=True)
    result = procs.run_process([sys.executable, "-B", "scripts/ledger.py", "--root", str(root), *map(str, args)],
                               root, env, 120, output / "stdout", output / "stderr")
    assert (result.timed_out, result.exception) == (False, None)
    return result.exit, result.stdout_path.read_text("utf-8") + result.stderr_path.read_text("utf-8")


def snapshot(root, cfg):
    jobs = root / "local_state/frutlups/jobs"
    return ((root / cfg.ledger).read_bytes(), gitws.workspace_witness(root, executable=cfg.git),
            sorted(os.listdir(jobs)) if jobs.is_dir() else None)


def history(root, cfg, model):
    events = ledger.read(root / cfg.ledger)
    return events, ledger.validate_history(root, events, model, executable=cfg.git)


def roles(state):
    counts = {}
    for attempt in state.attempts.values():
        role = attempt["start"]["role"]
        counts[role] = counts.get(role, 0) + 1
    return counts


def seat_seconds(state):
    """Durable measured operation seconds per role (fake seats, real verifier)."""
    totals = {}
    for attempt in state.attempts.values():
        secs = (attempt["finish"] or {}).get("usage", {}).get("secs")
        if secs is not None:
            role = attempt["start"]["role"]
            totals[role] = round(totals.get(role, 0) + secs, 3)
    return totals


def use_fake_seats(root, cfg, directory):
    """Point the ignored local configuration at real fake seat executables."""
    local = root / "frutlups.local.toml"
    text = local.read_text("utf-8")
    for name in ("pi", "claude"):
        path = fake_seat(directory, name)
        text = text.replace(f"{name} = {json.dumps(getattr(cfg, name))}", f"{name} = {json.dumps(str(path))}")
    local.write_text(text, encoding="utf-8")
    (root / "local_state").mkdir(exist_ok=True)
    if not (root / "local_state/plan.json").exists():
        (root / "local_state/plan.json").write_text("{}", encoding="utf-8")


def commit_all(root, cfg, message):
    for args in (("add", "."), ("commit", "-qm", message)):
        gitws._git(root, *args, executable=cfg.git)


@pytest.fixture
def public_project(checker_project, tmp_path):
    root, cfg, _ = checker_project
    assert_final_project(root)
    use_fake_seats(root, cfg, tmp_path / "seats")
    data = yaml.safe_load((root / "roadmap.yaml").read_text("utf-8"))
    data["autonomy"] = {"schema": "frutlups.autonomy/1"}
    # Release risk routes every slice to both configured reviewers.
    data["milestones"][0].update(holistic_review=True, risk="release")
    (root / "roadmap.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    main = root / "frutlups.toml"
    main.write_text(main.read_text("utf-8").replace("commit_on_accept = false", "commit_on_accept = true")
                    .replace('until = "roadmap"', 'until = "milestone"'), encoding="utf-8")
    (root / "local_state/plan.json").write_text(json.dumps({
        "coder": [{"writes": {"07_app/public.txt": "useful public output\n"}}]}), encoding="utf-8")
    (root / "05_governance/approval-R001.md").write_text("Owner approved public qualification R001.\n",
                                                       encoding="utf-8")
    commit_all(root, cfg, "owned public authority")
    cfg = Config.load(root)
    assert cfg.commit_on_accept and cfg.until == "milestone"
    assert Path(cfg.pi).name.startswith("pi") and Path(cfg.claude).name.startswith("claude")
    return root, cfg, roadmap.load(root / "roadmap.yaml"), tmp_path / "out"


def admit(root, cfg, out, *, jobs=20, seconds=900):
    code, output = template(root, cfg, out / "admit", "run", "admit", "M001", "--run-id", "R001",
                            "--slice", "M001-S01", "--stop", "M001",
                            "--jobs", jobs, "--transport-retries", 1, "--format-retries", 1,
                            "--seconds", seconds, "--max-corrective-rounds", 2,
                            "--authority", "05_governance/approval-R001.md",
                            "--reason", "Owner approved public qualification")
    assert (code, output) == (0, "run_admitted R001\n")


def balances(root, cfg, out, label):
    """F status and the template consumer report the same durable run balances."""
    code, output, _ = cli(root, out / f"{label}-status", "status", root, "--json")
    assert code == 0
    run = next(row for row in rows(output) if row["action"] == "run_status")
    code, text = template(root, cfg, out / f"{label}-template-status", "status")
    assert code == 0
    remaining = " ".join(f"{k}={run['remaining'][k]}" for k in
                         ("jobs", "transport_retries", "format_retries", "seconds"))
    assert f"run R001 revision={run['revision']} stop=M001 state={run['state']} remaining {remaining}" in text
    assert template(root, cfg, out / f"{label}-template-check", "check") == (0, "ledger: ok\n")
    return run


def test_public_lifecycle_handoffs_and_opposite_consumer(public_project, evidence):
    root, cfg, model, out = public_project
    started = time.monotonic()
    admit(root, cfg, out)
    code, output, _ = cli(root, out / "preflight", "preflight", root, record=evidence)
    assert (code, output) == (0, "preflight: ok\n")
    events, state = history(root, cfg, model)
    assert roles(state) == {"probe": 2}  # one Pi and one Claude identity
    code, output, _ = cli(root, out / "once", "run", root, "--once", "--json", record=evidence)
    assert (code, output) == (0, "")
    events, state = history(root, cfg, model)
    assert state.slices["M001-S01"].step == "coding"
    assert [e.ev for e in events].count("prompt") == 1
    assert roles(state) == {"probe": 2}  # unchanged context: no repeated probe

    # Runner to manual: stop after the slice; the template consumer sees the same state.
    code, output, _ = cli(root, out / "slice", "run", root, "--until", "slice", "--json", record=evidence)
    assert code == 0
    assert [r["action"] for r in rows(output)] == ["verified", "accepted", "stop"]
    assert rows(output)[-1] == {"action": "stop", "reason": "boundary", "detail": "one slice accepted"}
    head = gitws.head(root, executable=cfg.git)
    assert gitws._git(root, "show", f"{head}:07_app/public.txt", executable=cfg.git).stdout == b"useful public output\n"
    first = balances(root, cfg, out, "manual")
    assert first["state"] == "open"
    events, state = history(root, cfg, model)
    # Probe contexts change with each new prompt; seat work is never repeated.
    assert roles(state) == {"probe": 4, "coder": 1, "verification": 1, "reviewer": 2}

    # Manual owner action: extend the same run through the template, committed first.
    (root / "05_governance/approval-R001-ext.md").write_text("Owner approved an R001 extension.\n", encoding="utf-8")
    commit_all(root, cfg, "owned extension authority")
    code, text = template(root, cfg, out / "extend", "run", "extend", "R001", "--revision", 1,
                          "--jobs", 2, "--seconds", 60, "--authority", "05_governance/approval-R001-ext.md",
                          "--reason", "Owner approved an extension")
    assert (code, text) == (0, "run_extended R001\n")
    extended = balances(root, cfg, out, "extended")
    assert extended["remaining"]["jobs"] == first["remaining"]["jobs"] + 2
    assert extended["remaining"]["seconds"] == first["remaining"]["seconds"] + 60

    # Manual to runner: the public run replays the same balances and closes the milestone.
    code, output, _ = cli(root, out / "milestone", "run", root, "--json", record=evidence)
    assert code == 0
    assert rows(output)[-1] == {"action": "stop", "reason": "boundary", "detail": STOP_BOUNDARY}
    events, state = history(root, cfg, model)
    assert state.milestones_done == {"M001"}
    # Only the still-routed holistic identity is probed for the new context.
    assert roles(state) == {"probe": 5, "coder": 1, "verification": 1, "reviewer": 2, "holistic": 1}
    assert [e.ev for e in events].count("accepted") == 1
    assert [e.ev for e in events].count("milestone_done") == 1
    statuses = commit.status(root, events, model, executable=cfg.git)
    assert [(row["scope"], row["state"]) for row in statuses] == [("M001-S01", "completed"), ("M001", "completed")]
    final = balances(root, cfg, out, "closed")
    assert final["state"] == "boundary"
    charged = sum(1 for a in state.attempts.values() if a["start"]["role"] != "verification")
    assert final["remaining"]["jobs"] == 20 + 2 - charged

    # Idle restart: a completed grant stops immediately with no new work.
    before = snapshot(root, cfg)
    code, output, _ = cli(root, out / "idle", "run", root, "--json", record=evidence)
    assert code == 0
    assert rows(output) == [DONE]
    assert snapshot(root, cfg) == before
    code, output, _ = cli(root, out / "usage", "status", root, "--usage", "--json", record=evidence)
    assert code == 0
    usage = [r for r in rows(output) if r["action"] == "usage"]
    assert [(r.get("slice") or r["milestone"], r["unresolved_attempts"]) for r in usage] == [("M001-S01", 0), ("M001", 0)]
    # Seats (coder, two reviewers, holistic) plus probes (two Pi, three Claude).
    assert usage[1]["tokens_in"] == (17 + 13 * 3) + (17 * 2 + 13 * 3)
    assert template(root, cfg, out / "final-check", "check") == (0, "ledger: ok\n")
    evidence(label="lifecycle-total", elapsed=round(time.monotonic() - started, 3),
             seat_seconds=seat_seconds(state), attempts=roles(state))


@pytest.mark.parametrize("scope", ["M001-S01", "M001"])
def test_public_restart_recovers_interrupted_commit_exactly(public_project, evidence, scope):
    root, cfg, model, out = public_project
    admit(root, cfg, out)
    crash = [sys.executable, "-B", "-c", CRASH]
    if scope == "M001":
        code, output, _ = cli(root, out / "slice", "run", root, "--until", "slice", "--json")
        assert rows(output)[-1]["detail"] == "one slice accepted"
    code, output, _ = cli(root, out / "crash", root, scope, argv=crash, record=evidence)
    assert code == 1
    assert rows(output)[-1]["reason"] == "internal"
    assert "owned crash before approved commit" in rows(output)[-1]["detail"]
    events, state = history(root, cfg, model)
    pending = commit.status(root, events, model, executable=cfg.git)
    assert [(row["scope"], row["state"]) for row in pending][-1] == (scope, "pending")
    head, attempts, prefix = gitws.head(root, executable=cfg.git), dict(state.attempts), (root / cfg.ledger).read_bytes()

    # A fresh unpatched process recovers exactly that commit before any new work.
    code, output, _ = cli(root, out / "restart", "run", root, "--json", record=evidence)
    assert code == 0
    # Slice recovery continues to holistic closure in-process; closure recovery completes the roadmap.
    assert rows(output)[-1] == ({"action": "stop", "reason": "boundary", "detail": STOP_BOUNDARY}
                                if scope == "M001-S01" else DONE)
    events, state = history(root, cfg, model)
    assert (root / cfg.ledger).read_bytes().startswith(prefix)
    assert all(state.attempts[key] == value for key, value in attempts.items())
    statuses = commit.status(root, events, model, executable=cfg.git)
    assert [(row["scope"], row["state"]) for row in statuses] == [("M001-S01", "completed"), ("M001", "completed")]
    subjects = gitws._git(root, "log", "--format=%s", executable=cfg.git).stdout.decode().splitlines()
    assert len(subjects) == len(set(subjects))  # each approved commit exists once
    assert gitws.head(root, executable=cfg.git) != head
    # Pi plus Claude before the slice seats, then only Claude for holistic; recovery alone does not probe.
    assert roles(state) == {"probe": 3, "coder": 1, "verification": 1, "reviewer": 2, "holistic": 1}
    assert [e.ev for e in events].count("accepted") == [e.ev for e in events].count("milestone_done") == 1

    # Replaying the restart changes nothing: no duplicate seat, verifier, approval, commit or usage.
    before = snapshot(root, cfg)
    code, output, _ = cli(root, out / "replay", "run", root, "--json", record=evidence)
    assert code == 0 and rows(output) == [DONE]
    assert snapshot(root, cfg) == before
    assert template(root, cfg, out / "check", "check") == (0, "ledger: ok\n")


def test_public_unfinished_attempt_refuses_without_redispatch(public_project, evidence):
    root, cfg, model, out = public_project
    (root / "local_state/plan.json").write_text(json.dumps({"coder": [{"failure": "timeout"}]}), encoding="utf-8")
    admit(root, cfg, out)
    assert cli(root, out / "once", "run", root, "--once")[0] == 0
    # The owner kills the whole public process tree while the coder seat is running.
    code, _, _ = cli(root, out / "killed", "run", root, "--json", record=evidence, timeout=20)
    events, state = history(root, cfg, model)
    unfinished = [key for key, a in state.attempts.items() if a["finish"] is None]
    assert len(unfinished) == 1 and state.attempts[unfinished[0]]["start"]["role"] == "coder"
    count = (root / "local_state/coder-count.txt").read_text()
    before = snapshot(root, cfg)
    for verb in ("run", "preflight"):
        code, output, _ = cli(root, out / f"refused-{verb}", verb, root, record=evidence)
        assert code == 2
        assert output == "preflight: unresolved invocation prevents admission\n"
    assert snapshot(root, cfg) == before
    assert (root / "local_state/coder-count.txt").read_text() == count
    code, output, _ = cli(root, out / "status", "status", root)
    assert code == 0 and f"unresolved invocation: {unfinished[0]}" in output


REFUSALS = {
    "stop": (2, "STOP exists; the owner must remove it to run"),
    "ownership": (2, "another writer owns the repository; status remains available"),
    "until": (2, "until: must be one of milestone, roadmap, slice"),
    "missing-grant": (1, "profile requires current grant"),
    "exhausted-grant": (1, "run allowance exhausted"),
    "no-profile": (1, "fresh dispatch requires an autonomy profile"),
    "drift": (2, "unrecorded change: 07_app/drift.txt"),
    "half-selection": (2, "--historical-assessment and --historical-assessment-sha must be given together"),
    "malformed-selection": (2, "historical assessment:"),
    "legacy-schema": (2, "unsupported roadmap schema"),
}


@pytest.mark.parametrize("case", list(REFUSALS))
def test_public_refusals_have_no_effects(public_project, evidence, case):
    root, cfg, model, out = public_project
    args = ["run", root, "--json"]
    if case == "no-profile" or case == "legacy-schema":
        data = yaml.safe_load((root / "roadmap.yaml").read_text("utf-8"))
        data.pop("autonomy")
        if case == "legacy-schema":
            data["schema"] = "frutlups.roadmap/1"
        (root / "roadmap.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
        commit_all(root, cfg, "owned authority change")
    elif case != "missing-grant":
        admit(root, cfg, out, jobs=2 if case == "exhausted-grant" else 20)
    if case == "exhausted-grant":
        assert cli(root, out / "preflight", "preflight", root)[0] == 0  # two probes spend both jobs
    elif case == "stop":
        (root / "STOP").touch()
    elif case == "drift":
        (root / "07_app/drift.txt").write_text("unrecorded\n", encoding="utf-8")
    elif case == "half-selection":
        args += ["--historical-assessment", "00_brief/selection.json"]
    elif case == "malformed-selection":
        args += ["--historical-assessment", "00_brief/missing.json", "--historical-assessment-sha", "0" * 64]
    before = snapshot(root, cfg)
    if case == "until":
        # The parser refuses an invalid flag; programmatic Config is validated before effects too.
        assert cli(root, out / "flag", "run", root, "--until", "forever")[0] == 2
        notices = []
        assert loop.run(root, replace(cfg, until="forever"), emit=notices.append) == loop.StopReason.preflight_failed
        assert notices == [{"action": "preflight", "ok": False, "detail": REFUSALS[case][1]}]
        with pytest.raises(ValueError, match="^until: must be one of"):
            loop.Loop(root, cfg, model, until="forever")
        code, output = 2, json.dumps(notices[0])
    elif case == "ownership":
        with writer_lock(root, model, executable=cfg.git):
            code, output, _ = cli(root, out / case, *args, record=evidence)
    else:
        code, output, _ = cli(root, out / case, *args, record=evidence)
    assert code == REFUSALS[case][0], output
    assert REFUSALS[case][1] in output
    assert snapshot(root, cfg) == before
    assert not (root / "local_state/frutlups/jobs").exists() or case == "exhausted-grant"
    assert cli(root, out / "status-after", "status", root)[0] == (0 if case != "legacy-schema" else 1)


def test_public_historical_selection_pair(owned_history, tmp_path, evidence):
    root, cfg, model, pair, _ = owned_history
    use_fake_seats(root, cfg, tmp_path / "seats")
    out = tmp_path / "out"
    flags = ["--historical-assessment", pair[0], "--historical-assessment-sha", pair[1]]
    before = snapshot(root, cfg)
    code, output, _ = cli(root, out / "half", "run", root, flags[0], flags[1], record=evidence)
    assert (code, output) == (2, "preflight: --historical-assessment and --historical-assessment-sha must be given together\n")
    code, output, _ = cli(root, out / "mismatch", "run", root, flags[0], flags[1], flags[2], "0" * 64)
    assert (code, output) == (2, "preflight: historical assessment: raw hash mismatch\n")
    assert snapshot(root, cfg) == before
    # Without a selection the public runner stays strict and dispatches nothing.
    code, output, _ = cli(root, out / "strict", "run", root, "--json", record=evidence)
    assert code == 1
    assert "accepted receipt exceeded frozen deadline" in rows(output)[-1]["detail"]
    assert (root / cfg.ledger).read_bytes() == before[0]
    assert not (root / "local_state/frutlups/jobs").exists()
    # The same raw pair is validated under ownership and consumed by the public run.
    code, output, _ = cli(root, out / "preflight", "preflight", root, *flags, record=evidence)
    assert (code, output) == (0, "preflight: ok\n")
    code, output, _ = cli(root, out / "selected", "run", root, "--json", *flags, record=evidence)
    assert code == 0, output
    events, state = history(root, cfg, model)
    assert state.milestones_done == {"M001"}
    assert roles(state).get("holistic") == 1
    assert template(root, cfg, out / "check", "check") == (0, "ledger: ok\n")


def semantic_index(root, cfg):
    return gitws._git(root, "ls-files", "--stage", "-v", "-z", executable=cfg.git).stdout


def test_public_preflight_checks_project_before_first_probe(public_project, monkeypatch):
    from frutlups import _preflight
    root, cfg, model, out = public_project
    admit(root, cfg, out)
    order = []
    checker, probe = _preflight._checker, _preflight._probe
    def forwarded_checker(*args, **kwargs):  # Forwarding only: the real checker runs.
        result = checker(*args, **kwargs)
        order.append("checker")
        return result
    def forwarded_probe(root, cfg, name, *args, **kwargs):
        order.append(f"probe:{name}")
        return probe(root, cfg, name, *args, **kwargs)
    monkeypatch.setattr(_preflight, "_checker", forwarded_checker)
    monkeypatch.setattr(_preflight, "_probe", forwarded_probe)
    assert loop.preflight(root) == ()
    # One real checker pass under ownership, before the first probe reservation.
    assert order == ["checker", "probe:claude_reviewer", "probe:coder"]
    events, state = history(root, cfg, model)
    assert roles(state) == {"probe": 2}
    order.clear()
    # Still fresh work: the checker runs again; completed probe contexts are not repeated.
    assert loop.preflight(root) == ()
    assert order == ["checker"]


def test_public_preflight_checker_refusal_precedes_any_probe(public_project, monkeypatch):
    from frutlups import _preflight
    root, cfg, model, out = public_project
    admit(root, cfg, out)
    # The owner commits a stricter project checker; F's own static admission still passes.
    script = root / "scripts/ledger.py"
    script.write_text('import sys\nif sys.argv[-1] == "check":\n    sys.exit("owner check refuses")\n'
                      + script.read_text("utf-8"), encoding="utf-8")
    commit_all(root, cfg, "owned stricter project checker")
    before = snapshot(root, cfg) + (semantic_index(root, cfg),)
    probes = []
    probe = _preflight._probe
    monkeypatch.setattr(_preflight, "_probe", lambda *a, **k: (probes.append(a[2]), probe(*a, **k))[1])
    assert loop.preflight(root) == ("project checker refused the project",)
    assert probes == []
    assert snapshot(root, cfg) + (semantic_index(root, cfg),) == before
    code, output, _ = cli(root, out / "refused", "preflight", root)
    assert (code, output) == (2, "preflight: project checker refused the project\n")
    assert snapshot(root, cfg) + (semantic_index(root, cfg),) == before
    events, state = history(root, cfg, model)
    assert roles(state) == {}
    assert not (root / "local_state/frutlups/jobs").exists()


def frontier_project(public_project, out):
    """M001 awaits holistic closure while active M002 holds a later frozen 3.99 runtime."""
    root, cfg, _, _ = public_project
    data = yaml.safe_load((root / "roadmap.yaml").read_text("utf-8"))
    later = json.loads(json.dumps(data["milestones"][0]))
    later["id"], later["slices"][0]["id"] = "M002", "M002-S01"
    data["milestones"].append(later)
    (root / "roadmap.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    commit_all(root, cfg, "owned two-milestone authority")
    model = roadmap.load(root / "roadmap.yaml")
    admit(root, cfg, out)
    code, output, _ = cli(root, out / "slice", "run", root, "--until", "slice", "--json")
    assert rows(output)[-1]["detail"] == "one slice accepted", output
    events, state = history(root, cfg, model)
    item = model.milestones[1].slices[0]
    # A separate manual issuance for M002, not a rewrite of any recorded contract.
    frozen = loop.render.coding_envelope(root, model, item, state.slices[item.id], events, executable=cfg.git)
    frozen["runtime"] = {"python": "3.99"}
    refs = []
    for path, raw in (("05_governance/reviews/m002/later-envelope.json", json.dumps(frozen).encode()),
                      ("prompts/for_coding_agent/later.md", b"Later milestone, separate authority required.\n")):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(raw)
        refs.append(dict(path=path, sha=ledger.evidence_sha(raw)))
    row = dict(loop._attempts.event("prompt", slice=item.id, round=1, **refs[1], envelope=refs[0]),
               by="architect")
    with (root / cfg.ledger).open("ab") as stream:
        stream.write(json.dumps(row).encode() + b"\n")
    state = ledger.validate_history(root, ledger.read(root / cfg.ledger), model, executable=cfg.git)
    assert state.slices["M001-S01"].step == "accepted" and "M001" not in state.milestones_done
    assert ledger.envelope(root, dict(state.slices[item.id].envelope), item.id, 1)["runtime"] == {"python": "3.99"}
    return root, cfg, model, frozen


def test_public_constructor_uses_milestone_first_runtime(public_project):
    from frutlups import receipt, runtime
    root, cfg, model, out = public_project
    root, cfg, model, frozen = frontier_project(public_project, out)
    with pytest.raises(ValueError, match="3.99"):
        receipt.native_runtime(root, model, frozen, cfg)
    expected = runtime.resolve_runtime(root, model, git=cfg.git)
    # Public construction selects the pending M001 closure's project runtime.
    notices = []
    runner = loop.Loop(root, cfg, model, emit=notices.append)
    assert dict(runner.runtime.identity) == dict(expected.identity)
    assert Path(runner.runtime.executable).resolve() == Path(expected.executable).resolve()
    # The pending M001 closure completes under the project runtime; M002 is not dispatched.
    assert runner.iteration() is None, notices
    assert notices == []
    # The same runner then stops at its ordinary milestone boundary: the ungranted
    # later 3.99 declaration is neither dispatched nor required for stopping.
    assert runner.iteration() == loop.StopReason.boundary, notices
    assert notices == [{"action": "stop", "reason": "boundary", "detail": STOP_BOUNDARY}]
    events, state = history(root, cfg, model)
    assert state.milestones_done == {"M001"}
    assert roles(state)["holistic"] == 1
    assert state.slices["M002-S01"].step == "coding"
    assert template(root, cfg, out / "check", "check") == (0, "ledger: ok\n")


GRANT_DONE = {"action": "stop", "reason": "boundary", "detail": "current grant boundary completed"}


def test_public_completed_grant_restart_ignores_ungranted_runtime(public_project):
    from frutlups import runtime
    root, cfg, model, out = public_project
    root, cfg, model, _ = frontier_project(public_project, out)
    code, output, _ = cli(root, out / "close", "run", root, "--json")
    assert (code, rows(output)[-1]) == (0, {"action": "stop", "reason": "boundary", "detail": STOP_BOUNDARY})
    events, state = history(root, cfg, model)
    assert state.milestones_done == {"M001"} and state.runs["R001"]["closed"] == "boundary"
    attempts = roles(state)
    before = snapshot(root, cfg)
    # Fresh public processes read the completed R001 history without resolving M002 for dispatch.
    code, output, _ = cli(root, out / "restart", "run", root, "--json")
    assert (code, rows(output)) == (0, [GRANT_DONE])
    code, output, _ = cli(root, out / "preflight", "preflight", root)
    assert (code, output) == (0, "preflight: ok\n")
    notices = []
    runner = loop.Loop(root, cfg, model, emit=notices.append)
    assert dict(runner.runtime.identity) == dict(runtime.resolve_runtime(root, model, git=cfg.git).identity)
    assert runner.iteration() == loop.StopReason.boundary
    assert notices == [GRANT_DONE]
    # No new probe, seat, verification, approval, commit or usage; no repair.
    assert snapshot(root, cfg) == before
    assert roles(history(root, cfg, model)[1]) == attempts
    assert template(root, cfg, out / "check", "check") == (0, "ledger: ok\n")


@pytest.mark.parametrize("evidence", ["valid", "tampered"])
def test_public_pending_closure_recovers_despite_ungranted_runtime(public_project, evidence):
    root, cfg, model, out = public_project
    root, cfg, model, _ = frontier_project(public_project, out)
    crash = [sys.executable, "-B", "-c", CRASH]
    code, output, _ = cli(root, out / "crash", root, "M001", argv=crash)
    assert code == 1 and "owned crash before approved commit" in rows(output)[-1]["detail"]
    events, state = history(root, cfg, model)
    assert [(row["scope"], row["state"]) for row in commit.status(root, events, model, executable=cfg.git)] == [
        ("M001-S01", "completed"), ("M001", "pending")]
    head, prefix, attempts = gitws.head(root, executable=cfg.git), (root / cfg.ledger).read_bytes(), roles(state)
    if evidence == "tampered":
        # Invalid pending evidence still refuses before any recovery or new work.
        report = root / next(e for e in events if e.ev == ledger.Ev.holistic_reviewed).data["report"]
        report.write_bytes(report.read_bytes() + b"tampered\n")
        before = snapshot(root, cfg)
        code, output, _ = cli(root, out / "refused", "run", root, "--json")
        assert code == 2, output
        assert rows(output)[0]["detail"].endswith("immutable evidence drift: " + report.relative_to(root).as_posix())
        assert snapshot(root, cfg) == before
        assert gitws.head(root, executable=cfg.git) == head
        return
    # Exact recovery under the project runtime: the ungranted M002 declaration cannot veto it.
    code, output, _ = cli(root, out / "restart", "run", root, "--json")
    assert (code, rows(output)) == (0, [GRANT_DONE])
    events, state = history(root, cfg, model)
    assert (root / cfg.ledger).read_bytes() == prefix
    assert roles(state) == attempts
    assert [(row["scope"], row["state"]) for row in commit.status(root, events, model, executable=cfg.git)] == [
        ("M001-S01", "completed"), ("M001", "completed")]
    subjects = gitws._git(root, "log", "--format=%s", executable=cfg.git).stdout.decode().splitlines()
    assert gitws.head(root, executable=cfg.git) != head and len(subjects) == len(set(subjects))
    assert state.slices["M002-S01"].step == "coding"
    before = snapshot(root, cfg)
    code, output, _ = cli(root, out / "replay", "run", root, "--json")
    assert (code, rows(output)) == (0, [GRANT_DONE])
    assert snapshot(root, cfg) == before
    assert template(root, cfg, out / "check", "check") == (0, "ledger: ok\n")


def test_public_current_frozen_runtime_drift_still_refuses(public_project):
    root, cfg, model, out = public_project
    root, cfg, model, _ = frontier_project(public_project, out)
    assert loop.Loop(root, cfg, model, emit=lambda _: None).iteration() is None  # close M001
    # Once M002 is granted and current, its frozen runtime is enforced.
    (root / "05_governance/approval-R002.md").write_text("Owner approved R002.\n", encoding="utf-8")
    commit_all(root, cfg, "owned R002 authority")
    code, text = template(root, cfg, out / "admit2", "run", "admit", "M002", "--run-id", "R002",
                          "--slice", "M002-S01", "--stop", "M002", "--jobs", 10, "--seconds", 600,
                          "--authority", "05_governance/approval-R002.md", "--reason", "Owner approved M002")
    assert (code, text) == (0, "run_admitted R002\n")
    before = snapshot(root, cfg)
    with pytest.raises(ValueError, match="3.99"):
        loop.Loop(root, cfg, model)
    code, output, _ = cli(root, out / "drift", "run", root, "--json")
    assert code == 2
    assert "3.99" in rows(output)[0]["detail"]
    assert snapshot(root, cfg) == before


def admit_run(root, cfg, out, run, *, jobs=20, seconds=900, transport_retries=1):
    """Owner admission through the final template for M001, successor-aware."""
    code, output = template(root, cfg, out / f"admit-{run}", "run", "admit", "M001", "--run-id", run,
                            "--slice", "M001-S01", "--stop", "M001", "--jobs", jobs,
                            "--transport-retries", transport_retries, "--format-retries", 1,
                            "--seconds", seconds, "--max-corrective-rounds", 2,
                            "--authority", f"05_governance/approval-{run}.md",
                            "--reason", f"Owner approved {run}")
    assert (code, output) == (0, f"run_admitted {run}\n")


def probe_attempts(state):
    return {key: a for key, a in state.attempts.items() if a["start"]["role"] == "probe"}


@pytest.mark.parametrize("arrival", ["existing", "during-checker"])
def test_public_direct_iteration_stop_precedes_pending_recovery(public_project, monkeypatch, arrival):
    from frutlups import _preflight
    root, cfg, model, out = public_project
    admit(root, cfg, out)
    crash = [sys.executable, "-B", "-c", CRASH]
    code, output, _ = cli(root, out / "crash", root, "M001-S01", argv=crash)
    assert code == 1 and "owned crash before approved commit" in rows(output)[-1]["detail"]
    events, _ = history(root, cfg, model)
    assert [r["state"] for r in commit.status(root, events, model, executable=cfg.git)] == ["pending"]
    payload = (root / "07_app/public.txt").read_bytes()
    head, index, ledger_bytes = gitws.head(root, executable=cfg.git), semantic_index(root, cfg), (root / cfg.ledger).read_bytes()
    calls = {"checker": 0, "recover": 0}
    checker, recover = _preflight._checker, commit.recover
    def forwarded_checker(*args, **kwargs):  # Forwarding observer; the real checker runs.
        result = checker(*args, **kwargs)
        calls["checker"] += 1
        if arrival == "during-checker" and calls["checker"] == 1:
            (root / "STOP").touch()  # STOP arrives while admission observes the checker.
        return result
    def forwarded_recover(*args, **kwargs):
        calls["recover"] += 1
        return recover(*args, **kwargs)
    monkeypatch.setattr(_preflight, "_checker", forwarded_checker)
    monkeypatch.setattr(commit, "recover", forwarded_recover)
    if arrival == "existing":
        (root / "STOP").touch()
    before = snapshot(root, cfg) if arrival == "existing" else None
    notices = []
    assert loop.Loop(root, cfg, model, emit=notices.append).iteration() == loop.StopReason.kill_switch
    assert notices == [{"action": "stop", "reason": "kill_switch",
                        "detail": "STOP exists; owner must remove it to resume"}]
    # No recovery and no dispatch: HEAD, index, ledger and approved payload unchanged.
    assert calls == {"checker": 0 if arrival == "existing" else 1, "recover": 0}
    assert gitws.head(root, executable=cfg.git) == head
    assert semantic_index(root, cfg) == index
    assert (root / cfg.ledger).read_bytes() == ledger_bytes
    assert (root / "07_app/public.txt").read_bytes() == payload
    if before is not None:
        assert snapshot(root, cfg) == before
    events, _ = history(root, cfg, model)
    assert [r["state"] for r in commit.status(root, events, model, executable=cfg.git)] == ["pending"]
    assert not (root / "local_state/frutlups/jobs").exists() or not any(
        p.name.startswith("probe-") for p in (root / "local_state/frutlups/jobs").iterdir())
    # Removing STOP restores ordinary exact recovery before any fresh-grant work.
    (root / "STOP").unlink()
    loop.Loop(root, cfg, model, emit=notices.append).iteration()
    events, _ = history(root, cfg, model)
    assert calls["recover"] >= 1  # exact slice recovery; holistic closure may follow in-process
    statuses = commit.status(root, events, model, executable=cfg.git)
    assert statuses[0]["scope"] == "M001-S01" and statuses[0]["state"] == "completed"
    subjects = gitws._git(root, "log", "--format=%s", executable=cfg.git).stdout.decode().splitlines()
    assert gitws.head(root, executable=cfg.git) != head and len(subjects) == len(set(subjects))


def test_public_failed_readiness_records_no_coder_start(public_project, evidence):
    root, cfg, model, out = public_project
    plan = root / "local_state/plan.json"
    plan.write_text(json.dumps({"coder": [{"writes": {"07_app/public.txt": "useful public output\n"}}],
                                "probe": [{"failure": "auth"}]}), encoding="utf-8")
    (root / "05_governance/approval-R002.md").write_text("Owner approved R002 after repair.\n", encoding="utf-8")
    commit_all(root, cfg, "owned successor authority")
    admit_run(root, cfg, out, "R001")
    assert cli(root, out / "once", "run", root, "--once")[0] == 0  # prompt issuance needs no probe
    marker = root / "local_state/frutlups/coding/M001-S01_r1.json"
    started = "05_governance/reviews/m001/M001-S01_r1_started.json"
    for attempt in ("failed", "same-run"):
        code, output, _ = cli(root, out / attempt, "run", root, "--json", record=evidence)
        assert code == 2, output
        assert rows(output)[-1]["reason"] == "preflight_failed"
        assert "model probe failed" in rows(output)[-1]["detail"]
        if attempt == "same-run":  # The consumed context is not reset to manufacture a retry.
            assert "initial model context already attempted" in rows(output)[-1]["detail"]
        events, state = history(root, cfg, model)
        assert not marker.exists() and not (root / started).exists()
        assert not any(e.ev == ledger.Ev.artifact and e.data.get("path") == started for e in events)
        assert not (root / "local_state/coder-count.txt").exists()
        assert "coder" not in roles(state)
    failed = probe_attempts(state)
    assert len(failed) == 2 and all(a["finish"]["status"] == "failed" for a in failed.values())
    charged = state.runs["R001"]["consumed"]["jobs"]
    assert charged == 2  # the failed readiness stays charged
    # Legitimate repair plus a new owner grant: the fresh runner dispatches once.
    plan.write_text(json.dumps({"coder": [{"writes": {"07_app/public.txt": "useful public output\n"}}]}),
                    encoding="utf-8")
    admit_run(root, cfg, out, "R002")
    code, output, _ = cli(root, out / "repaired", "run", root, "--until", "slice", "--json", record=evidence)
    assert code == 0, output
    assert rows(output)[-1] == {"action": "stop", "reason": "boundary", "detail": "one slice accepted"}
    events, state = history(root, cfg, model)
    assert (root / "local_state/coder-count.txt").read_text() == "1"
    assert [e.data.get("path") for e in events if e.ev == ledger.Ev.artifact].count(started) == 1
    assert roles(state)["coder"] == 1
    assert {k: v for k, v in probe_attempts(state).items() if k in failed} == failed
    assert state.runs["R001"]["consumed"]["jobs"] == charged
    assert template(root, cfg, out / "check", "check") == (0, "ledger: ok\n")


def probe_seat(root, cfg, model, events, state, contract):
    """The configured seat whose exact probe contract matches, by the production binding."""
    from frutlups import _attempts, _preflight
    return next(name for name in ("claude_reviewer", "coder")
                if _attempts._binding(root, cfg, model, events, state, "M001", "holistic", "probe",
                                      _preflight._probe_job(root, cfg, name), _preflight.PROBE_PROMPT,
                                      None) == contract)


def test_public_probe_reuse_is_scoped_to_current_run(public_project):
    from frutlups import _attempts, _preflight
    root, cfg, model, out = public_project
    plan = root / "local_state/plan.json"
    original = plan.read_text("utf-8")
    def approve(run, **allowance):
        # Created only when granted: a newly referenced authority file is excluded from
        # the probe product, so successor probe contracts stay identical to R001's.
        (root / f"05_governance/approval-{run}.md").write_text(f"Owner approved {run}.\n", encoding="utf-8")
        admit_run(root, cfg, out, run, **allowance)
    admit_run(root, cfg, out, "R001")
    # Current-run reuse: the second preflight probes nothing new.
    assert loop.preflight(root) == ()
    earlier = probe_attempts(history(root, cfg, model)[1])
    assert len(earlier) == 2 and all(a["finish"]["status"] == "completed" for a in earlier.values())
    assert loop.preflight(root) == ()
    assert probe_attempts(history(root, cfg, model)[1]) == earlier
    # Successor R002 has the identical probe contracts, and its own probes fail.
    approve("R002")
    plan.write_text(json.dumps({"probe": [{"failure": "auth"}]}), encoding="utf-8")
    errors = loop.preflight(root)
    assert len(errors) == 2 and all("model probe failed" in e for e in errors)
    state = history(root, cfg, model)[1]
    current = {k: a for k, a in probe_attempts(state).items() if k in state.runs["R002"]["roots"]}
    assert {a["start"]["contract"] for a in current.values()} == {a["start"]["contract"] for a in earlier.values()}
    assert all(a["finish"]["status"] == "failed" for a in current.values())
    # Even with the seats repaired, the earlier run's success is not borrowed.
    plan.write_text(original, encoding="utf-8")
    errors = loop.preflight(root)
    assert len(errors) == 2 and all("initial model context already attempted" in e for e in errors)
    assert len(probe_attempts(history(root, cfg, model)[1])) == 4
    # A legitimate current-run transport-retry success is reused without another probe.
    approve("R003", transport_retries=2)  # one retry for each probed seat identity
    plan.write_text(json.dumps({"probe": [{"failure": "auth"}]}), encoding="utf-8")
    assert len(loop.preflight(root)) == 2
    plan.write_text(original, encoding="utf-8")
    events, state = history(root, cfg, model)
    for parent, attempt in [(k, a) for k, a in probe_attempts(state).items() if k in state.runs["R003"]["roots"]]:
        name = probe_seat(root, cfg, model, events, state, attempt["start"]["contract"])
        seat = cfg.seats[name]
        adapter = _preflight.pi.PiSeat() if seat.adapter == "pi" else _preflight.claude.ClaudeSeat()
        job = _preflight._probe_job(root, cfg, name)
        with _attempts.Operation(root, cfg, model, "probe", 60, job=job, prompt=_preflight.PROBE_PROMPT,
                                 retry="transport", parent=parent) as operation:
            job.prompt_path.parent.mkdir(parents=True)
            job.prompt_path.write_bytes(_preflight.PROBE_PROMPT)
            result = _preflight._probe_owned(root, cfg, name, adapter, replace(job, timeout=operation.timeout),
                                             job.prompt_path.parent)
            operation.finish(result.status == "completed", result)
        assert result.status == "completed"
        events, state = history(root, cfg, model)
    retries = {k: a for k, a in probe_attempts(state).items() if a["start"]["retry"] == "transport"}
    assert len(retries) == 2 and all(k in state.runs["R003"]["roots"] for k in retries)
    assert loop.preflight(root) == ()
    assert len(probe_attempts(history(root, cfg, model)[1])) == 8  # no further probe
