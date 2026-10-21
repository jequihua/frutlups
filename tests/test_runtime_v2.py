"""Owned offline qualification of admission, runtime and shared exclusion."""
import copy
import hashlib
import json
import os
import platform
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_autonomy
import test_loop
from _native_support import Controller, grant_project, probe_errors
from frutlups import _preflight, cli, findings, ledger, loop, runtime
from frutlups.config import Config
from frutlups.ownership import writer_lock

project = test_loop.project
PIN = Path(__file__).parents[3] / "02_analysis/template_reference/q001-read-169a74c/scripts"


def selector(root, executable=sys.executable):
    # Owned fixture's local exclude has the same ignored/untracked semantics.
    with (root / ".git/info/exclude").open("a", encoding="utf-8") as stream:
        stream.write("\nproject.local.toml\n")
    (root / "project.local.toml").write_text(
        'schema = "template.local/1"\npython = ' + json.dumps(Path(executable).as_posix()),
        encoding="utf-8")


@pytest.mark.parametrize("local", [False, True])
def test_runtime_identity_and_literal_substitution(project, local):
    root, cfg, model = project
    if local:
        selector(root)
    selected = runtime.resolve_runtime(root, model, git=cfg.git)
    expected = {"python": platform.python_version(), "implementation": platform.python_implementation(),
                "source": "project.local" if local else "invoking_python"}
    expected["sha"] = hashlib.sha256(json.dumps(
        {"runtime": expected, "declaration": dict(model.runtime) or None},
        ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert dict(selected.identity) == expected
    assert Path(selected.executable).resolve() == Path(sys.executable).resolve()
    for argv, expected_argv in [(["python", "python"], [selected.executable, "python"]),
                                (["python3", "python"], ["python3", "python"]),
                                (["./python"], ["./python"]), ([], [])]:
        assert runtime.runtime_argv(argv, selected.executable) == expected_argv


@pytest.mark.parametrize("failure", ["unignored", "tracked", "fields", "schema", "relative", "missing", "version"])
def test_runtime_selector_refuses_before_probes(project, monkeypatch, failure):
    root, cfg, model = project
    selector(root)
    path = root / "project.local.toml"
    if failure == "unignored":
        (root / ".git/info/exclude").write_text("", encoding="utf-8")
        # The pinned native fixture ignores this path in its tracked policy;
        # Git gives that file priority over the local exclude file.
        with (root / ".gitignore").open("a", encoding="utf-8") as stream:
            stream.write("\n!project.local.toml\n")
        test_loop.git(root, "add", ".gitignore")
        test_loop.git(root, "commit", "-qm", "owned selector visibility")
        assert subprocess.run(["git", "check-ignore", "-q", "project.local.toml"], cwd=root,
                              capture_output=True).returncode == 1
    elif failure == "tracked":
        test_loop.git(root, "add", "-f", "project.local.toml")
    elif failure == "fields":
        path.write_text(path.read_text("utf-8") + '\nextra = true', encoding="utf-8")
    elif failure == "schema":
        path.write_text('schema="wrong"\npython="x"', encoding="utf-8")
    elif failure in ("relative", "missing"):
        selector(root, "absent.exe")
        if failure == "missing":
            selector(root, root / "absent.exe")
    else:
        model = replace(model, runtime={"python": "3.99"})
    calls = []
    monkeypatch.setattr(_preflight, "_probe", lambda *a: calls.append(a))
    before = (root / cfg.ledger).read_bytes()
    assert probe_errors(root, cfg, model)
    assert calls == []
    assert (root / cfg.ledger).read_bytes() == before
    assert not (root / "local_state/frutlups/jobs").exists()


@pytest.mark.parametrize("response", [b'{"python":"3.14.3","implementation":"CPython"}', b'{}', b'broken'])
def test_identity_query_is_managed_and_external(project, monkeypatch, response):
    root, cfg, model = project
    # A non-interpreter file selects the identity-query branch; the owned managed
    # executor double supplies bounded output without launching that file.
    executable = root / "local_state/identity.exe"
    executable.write_bytes(b"fixture")
    selector(root, executable)
    calls = []
    def managed(argv, cwd, env, timeout, stdout, stderr):
        assert not stdout.is_relative_to(root)
        assert stdout.parent == stderr.parent
        assert argv[:3] == [str(executable.resolve()), "-I", "-S"]
        assert cwd == root
        assert timeout == 10
        assert runtime.procs.stdout_observer.get() is None
        calls.append(argv)
        stdout.write_bytes(response)
        stderr.write_bytes(b"")
        return SimpleNamespace(exit=0, timed_out=False, exception=None,
                               stdout_truncated=False, stderr_truncated=False, stdout_path=stdout)
    monkeypatch.setattr(runtime.procs, "run_process", managed)
    # Avoid replacing managed Git's executor while testing the identity executor.
    monkeypatch.setattr(runtime.gitws, "_git", lambda root, *a, **kw:
                        SimpleNamespace(returncode=0 if "check-ignore" in a else 1))
    if response.startswith(b'{"python"'):
        assert runtime.resolve_runtime(root, model).identity["python"] == "3.14.3"
    else:
        with pytest.raises(ValueError, match="invalid runtime identity"):
            runtime.resolve_runtime(root, model)
    assert len(calls) == 1


def test_all_static_errors_precede_probes(project, monkeypatch):
    root, cfg, model = project
    (root / "STOP").touch()
    cfg = replace(cfg, claude=str(root / "absent.exe"))
    model = replace(model, verification_full=("missing-verifier",))
    calls = []
    monkeypatch.setattr(_preflight, "_probe", lambda *a: calls.append(a))
    errors = probe_errors(root, cfg, model)
    assert any("STOP" in e for e in errors)
    assert any("verification executable" in e for e in errors)
    assert any("seat executable missing: claude" in e for e in errors)
    assert calls == []
    assert not (root / "local_state/frutlups/jobs").exists()


@pytest.mark.parametrize("failure", ["dirty", "ledger", "seat"])
def test_independent_static_failure_preserves_tree_without_probes(project, monkeypatch, failure):
    root, cfg, model = project
    if failure == "dirty":
        (root / "07_app/CONTEXT.md").write_bytes(b"unrecorded owned edit\n")
    elif failure == "ledger":
        (root / cfg.ledger).write_bytes(b"malformed ledger\n")
    else:
        cfg = replace(cfg, seats={"coder": cfg.seats["coder"]})
    before = runtime.gitws.workspace_witness(root)
    monkeypatch.setattr(_preflight, "_probe", lambda *a: pytest.fail("static failure dispatched probe"))
    assert probe_errors(root, cfg, model)
    assert runtime.gitws.workspace_witness(root) == before
    assert not (root / "local_state/frutlups/jobs").exists()


def test_real_managed_runtime_identity_child(project):
    root, cfg, model = project
    assert Path(sys._base_executable).resolve() != Path(sys.executable).resolve()
    selector(root, sys._base_executable)
    selected = runtime.resolve_runtime(root, model)
    assert selected.identity["python"] == platform.python_version()
    assert selected.identity["implementation"] == platform.python_implementation()
    assert selected.identity["source"] == "project.local"
    assert not (root / "local_state/frutlups/jobs").exists()


def test_direct_iteration_static_refusal_preserves_evidence(project, monkeypatch):
    root, cfg, model = project
    cfg = replace(cfg, claude=str(root / "absent.exe"))
    before = (root / cfg.ledger).read_bytes()
    monkeypatch.setattr(loop.Loop, "_iteration_owned", lambda self, **_: pytest.fail("effects reached"))
    # Treat the owned scratch configuration as canonical so the static refusal itself shows.
    monkeypatch.setattr(_preflight.Config, "load", lambda root: cfg)
    assert loop.preflight(root, cfg, model) == ("seat executable missing: claude",)
    assert loop.run(root, cfg, model, emit=lambda _: None) == loop.StopReason.preflight_failed
    assert probe_errors(root, cfg, model) == ("seat executable missing: claude",)
    assert (root / cfg.ledger).read_bytes() == before
    assert not (root / "local_state/frutlups/jobs").exists()


def test_authority_changed_during_lock_acquisition_refuses(project, monkeypatch):
    root, cfg, model = project
    calls = []
    before = (root / "roadmap.yaml").read_bytes()
    ledger_before = (root / cfg.ledger).read_bytes()
    @contextmanager
    def changed(*args, **kwargs):
        calls.append(True)
        with writer_lock(*args, **kwargs):
            with (root / "roadmap.yaml").open("a", encoding="utf-8") as stream:
                stream.write("\n# concurrent authority\n")
            yield
    monkeypatch.setattr(_preflight, "writer_lock", changed)
    monkeypatch.setattr(_preflight, "_probe", lambda *a: pytest.fail("probe reached"))
    assert _preflight.prepare(root, cfg, model)[2] == ("admission authority or ownership changed",)
    assert calls == [True]
    # The concurrent edit is preserved for inspection, never reverted or adopted.
    assert (root / "roadmap.yaml").read_bytes().startswith(before)
    assert (root / "roadmap.yaml").read_text("utf-8").endswith("\n# concurrent authority\n")
    assert (root / cfg.ledger).read_bytes() == ledger_before


def test_run_resolves_once_and_keeps_ownership_through_exceptions(project, monkeypatch):
    root, cfg, model = project
    selected = runtime.resolve_runtime(root, model)
    resolutions, probes = [], []
    monkeypatch.setattr(runtime, "resolve_runtime", lambda *a, **kw: resolutions.append(a) or selected)
    monkeypatch.setattr(_preflight, "_probe", lambda *a: probes.append(a[2]))
    def fail(self):
        assert self.runtime is selected
        assert contender(root, "manual").returncode == 2
        raise RuntimeError("owned failure")
    monkeypatch.setattr(loop.Loop, "_iteration_owned", fail)
    with pytest.raises(RuntimeError, match="owned failure"):
        Controller(root, cfg, model).iteration()
    assert len(resolutions) == 1
    assert probes == []
    assert contender(root, "manual").returncode == 0


def lock_script(kind, hold=False):
    setup = ("from frutlups.ownership import writer_lock" if kind == "runner" else
             "sys.path.insert(0, sys.argv[2]); from _common import writer_lock")
    return ("import sys\nfrom pathlib import Path\n" + setup + "\ntry:\n"
            " with writer_lock(Path(sys.argv[1])):\n" +
            ("  print('owned', flush=True)\n  sys.stdin.readline()\n" if hold else "  pass\n") +
            "except ValueError as exc:\n print(str(exc)); sys.exit(2)\n")


def contender(root, kind):
    return subprocess.run([sys.executable, "-B", "-c", lock_script(kind), str(root), str(PIN)],
                          capture_output=True, text=True, timeout=20)


@contextmanager
def held(root, kind):
    child = subprocess.Popen([sys.executable, "-B", "-c", lock_script(kind, True), str(root), str(PIN)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "owned"
        yield
    finally:
        out, err = child.communicate("release\n", timeout=20)
        assert child.returncode == 0, out + err


@pytest.mark.parametrize("kind", ["manual", "runner"])
@pytest.mark.parametrize("linked", [False, True])
def test_fresh_writers_share_template_lock_and_status(project, monkeypatch, tmp_path, kind, linked):
    root, cfg, model = project
    other = root
    if linked:
        other = tmp_path / "linked"
        test_loop.git(root, "worktree", "add", "--detach", str(other), "HEAD")
    monkeypatch.setattr(Config, "load", lambda root: cfg)
    before = (root / cfg.ledger).read_bytes()
    with held(root, kind):
        result = contender(other, "runner" if kind == "manual" else "manual")
        assert result.returncode == 2, result.stdout + result.stderr
        output = []
        cli._status(root, False, False, output.append)
        assert "next: M001-S01" in output[0]
        with pytest.raises(ValueError, match="writer"):
            Controller(other, cfg, model).iteration()
        with pytest.raises(ValueError, match="append is not enabled"):
            ledger.append(root / cfg.ledger, stop_event(), model)
        assert _preflight.prepare(other, cfg, model)[2]
        assert not (other / "local_state/frutlups/jobs").exists()
    assert (root / cfg.ledger).read_bytes() == before
    assert contender(other, "runner").returncode == 0


def stop_event():
    return {"schema": "frutlups.ledger/2", "t": "2026-09-15T00:00:00Z", "by": "frutlups",
            "ev": "stop", "reason": "boundary", "detail": "owned test"}


def test_nested_append_keeps_outer_lock_and_exception_releases(project, monkeypatch):
    root, cfg, model = project
    assert model.autonomy is None
    notices = []
    runner = Controller(root, cfg, model, emit=notices.append)
    def forbidden(*args, **kwargs):
        pytest.fail("probe, seat or job after profile-free refusal")
    monkeypatch.setattr(_preflight, "_probe", forbidden)
    monkeypatch.setattr(loop.pi.PiSeat, "run", forbidden)
    monkeypatch.setattr(loop.claude.ClaudeSeat, "run", forbidden)
    monkeypatch.setattr(loop.Loop, "_job", forbidden)
    before = runtime.gitws.workspace_witness(root, executable=cfg.git)
    authority = _preflight._authority_bytes(root)
    saved = (root / cfg.ledger).read_bytes()
    with pytest.raises(RuntimeError, match="^release$"):
        with writer_lock(root, model):
            with pytest.raises(ValueError, match="append is not enabled"):
                ledger.append(root / cfg.ledger, stop_event(), model)
            assert runner.iteration() == loop.StopReason.internal
            assert notices[-1]["reason"] == "internal"
            assert notices[-1]["detail"] == (
                "M001-S01: LedgerError: fresh dispatch requires an autonomy profile; owner must inspect")
            assert contender(root, "manual").returncode == 2
            with writer_lock(root, model):
                assert contender(root, "manual").returncode == 2
            assert contender(root, "manual").returncode == 2
            raise RuntimeError("release")
    assert (root / cfg.ledger).read_bytes() == saved
    assert runtime.gitws.workspace_witness(root, executable=cfg.git) == before
    assert _preflight._authority_bytes(root) == authority
    assert test_loop.events_of(root, cfg, "prompt") == []
    assert not test_loop.state(root, cfg, model).attempts
    assert runner.jobs == 0
    for path in ("local_state/frutlups/jobs", "local_state/coder-count.txt", "07_app/result.txt"):
        assert not (root / path).exists()
    assert contender(root, "manual").returncode == 0


def test_granted_nested_prompt_append_keeps_outer_lock_and_exception_releases(project):
    root, cfg, model = grant_project(project)
    saved = (root / cfg.ledger).read_bytes()
    prior = ledger.read(root / cfg.ledger)
    baseline = list(loop.evidence.changes(root, cfg))
    runner = Controller(root, cfg, model)
    with pytest.raises(RuntimeError, match="^release$"):
        with writer_lock(root, model):
            assert runner.iteration() is None
            appended = (root / cfg.ledger).read_bytes()
            assert appended.startswith(saved)
            rows = appended[len(saved):].splitlines()
            assert len(rows) == 1
            row = json.loads(rows[0])
            prompt = f"{cfg.prompt_dir}/M001-S01_r1.md"
            envelope = f"{cfg.reviews_dir}/m001/M001-S01_r1_envelope.json"
            assert row == dict(
                schema="frutlups.ledger/2", t=row["t"], by="frutlups", ev="prompt",
                slice="M001-S01", round=1, path=prompt,
                sha=ledger.evidence_sha((root / prompt).read_bytes()),
                envelope=dict(path=envelope, sha=ledger.evidence_sha((root / envelope).read_bytes())),
                baseline=baseline)
            events = ledger.read(root / cfg.ledger)
            assert events[:-1] == prior
            state = ledger.validate_history(root, events, model, executable=cfg.git)
            assert state.slices["M001-S01"].last_prompt == prompt
            assert not state.attempts
            assert runner.jobs == 0
            assert contender(root, "manual").returncode == 2
            with writer_lock(root, model):
                assert contender(root, "manual").returncode == 2
            assert contender(root, "manual").returncode == 2
            raise RuntimeError("release")
    assert (root / cfg.ledger).read_bytes() == appended
    assert not (root / "local_state/frutlups/jobs").exists()
    assert contender(root, "manual").returncode == 0


def test_direct_append_custom_location_cannot_bypass_writer(project):
    root, cfg, model = project
    with held(root, "manual"):
        for path in (root / "ledger.jsonl", root / "deep/new/ledger.jsonl"):
            with pytest.raises(ValueError, match="append is not enabled"):
                ledger.append(path, stop_event(), model)
            assert not path.exists()


def bound_case(root, name):
    case = copy.deepcopy(next(c for c in test_autonomy.CORPUS["cases"] if c["name"] == name))
    model = test_autonomy.case_roadmap(case, root)
    def write(path, value):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        data = value if isinstance(value, bytes) else json.dumps(value).encode()
        target.write_bytes(data)
        return {"path": path, "sha": ledger.evidence_sha(data)}
    envelope = {key: [] for key in ledger.ENVELOPE_FIELDS}
    envelope.update(schema="frutlups.envelope/2", slice="M001-S01", round=1, title="Owned",
                    objective="Owned qualification", acceptance=["check"], full=["python"], runtime={},
                    timeout_seconds=10, observation="process", notes="", findings="", memory="")
    manifest = {"schema": "frutlups.manifest/2", "slice": "M001-S01", "round": 1, "changed": []}
    for row in case["events"]:
        for key in ("authority", "evidence"):
            for ref in row.get(key, []):
                ref.update(write(ref["path"], b"authority\n"))
        if "envelope" in row:
            row["envelope"] = write(row["envelope"]["path"], {**envelope, "round": row["round"]})
        if "manifest" in row:
            row["manifest"] = write(row["manifest"]["path"], {**manifest, "round": row["round"]})
            manifest_ref = row["manifest"]
        for key in ("path", "report", "receipt"):
            if key in row:
                value = b"evidence\n"
                if key == "receipt":
                    value = dict(schema="frutlups.receipt/2", slice=row["slice"], round=row["round"],
                                 manifest=manifest_ref, ok=row["ok"])
                elif key == "report":
                    scope = row.get("slice", row.get("milestone"))
                    status = {"pass": "achieved", "override": "achieved",
                              "needs_work": "not_achieved", "blocked": "indeterminate"}[row["verdict"]]
                    finding_rows = "".join(f"| {identity} | P2 | open | Owned finding. |\n"
                                           for identity in row["open"])
                    value = (
                        f"# Review: {scope} round {row.get('round', 'holistic')}\n\n## Findings\n"
                        "| id | severity | disposition | summary |\n| --- | --- | --- | --- |\n"
                        f"{finding_rows}\n## Closure Decision\nObjective status: {status}\n"
                        "Objective evidence: Owned fixture.\n\n## Verdict\n"
                        f"Verdict: {row['verdict']} - next: Follow the owned event.\n").encode()
                row["sha"] = write(row[key], value)["sha"]
    return model, case["events"], write


def parse(rows):
    return tuple(ledger._parse_event(row) for row in rows)


@pytest.mark.parametrize("name", ["completed-nonholistic-history-survives-done-roadmap",
                                  "completed-holistic-history-survives-done-roadmap"])
def test_bound_case_reports_match_events_and_malformed_reports_refuse(tmp_path, name):
    original = copy.deepcopy(test_autonomy.CORPUS)
    model, rows, write = bound_case(tmp_path, name)
    assert test_autonomy.CORPUS == original
    reports = [row for row in rows if "report" in row]
    assert len(reports) == (2 if name == "completed-holistic-history-survives-done-roadmap" else 1)
    for row in reports:
        raw = (tmp_path / row["report"]).read_bytes()
        assert row["sha"] == ledger.evidence_sha(raw)
        _, report = findings.read_report(tmp_path, {"path": row["report"], "sha": row["sha"]})
        assert report["identity"] == row.get("slice", row.get("milestone"))
        assert report["round"] == row.get("round")
        assert report["verdict"] == row["verdict"]
        assert report["open"] == row["open"]
        assert report["findings"] == []
        assert report["objective_status"] == "achieved"
        assert report["objective_evidence"] == "Owned fixture."
    assert (tmp_path / "prompt.md").read_bytes() == b"evidence\n"
    ledger.validate_history(tmp_path, parse(rows), model)
    # Rebind malformed bytes so the grammar guard, rather than hash drift, refuses.
    row = reports[-1]
    row["sha"] = write(row["report"], b"evidence\n")["sha"]
    with pytest.raises(ledger.LedgerError, match="exactly one Findings, Closure Decision, and Verdict"):
        ledger.validate_history(tmp_path, parse(rows), model)


@pytest.mark.parametrize("name", ["completed-nonholistic-history-survives-done-roadmap",
                                  "completed-holistic-history-survives-done-roadmap"])
def test_done_history_status_validates_before_output(tmp_path, monkeypatch, name):
    model, rows, write = bound_case(tmp_path, name)
    write("ledger.jsonl", b"\n".join(json.dumps(row).encode() for row in rows) + b"\n")
    monkeypatch.setattr(Config, "load", lambda root: SimpleNamespace(
        ledger="ledger.jsonl", git=test_loop.shutil.which("git")))
    output = []
    cli._status(tmp_path, False, False, output.append)
    assert "accepted" in output[0]
    with pytest.raises(ValueError, match="active"):
        _preflight._profile_gate(model, ledger.fold(parse(rows), model))
    (tmp_path / rows[0]["authority"][0]["path"]).write_bytes(b"Authority\n")
    output.clear()
    with pytest.raises(ledger.LedgerError, match="drift"):
        cli._status(tmp_path, False, False, output.append)
    assert output == []


@pytest.mark.parametrize("field", ["authority", "envelope", "manifest", "path", "receipt", "report",
                                    "notes_sha", "outcome", "nested", "commit_intent"])
@pytest.mark.parametrize("failure", ["missing", "same_size"])
def test_every_historical_reference_is_revalidated(tmp_path, field, failure):
    model, rows, write = bound_case(tmp_path, "completed-nonholistic-history-survives-done-roadmap")
    coded = rows[2]
    coded["notes_path"] = "notes.md"
    coded["notes_sha"] = write("notes.md", b"notes\n")["sha"]
    nested = write("nested.md", b"nested\n")
    coded["outcome"] = write("outcome.json", dict(schema="frutlups.outcome/2", slice="M001-S01", round=1,
        invocation="I1", outcome="implemented", requirement="test", reason="done", actor="", action="",
        remaining=[], evidence=[nested], authority=[]))
    intent_ref = write("intent.json", dict(schema="frutlups.commit-manifest/1", id="c" * 32, parent="a" * 40,
                                           scope="M001-S01"))
    rows[-1]["commit_intent"] = dict(id="c" * 32, parent="a" * 40, manifest=intent_ref, message="Owned")
    # Pending commit history is valid for replay but cannot start new admission.
    events = parse(rows)
    ledger.validate_history(tmp_path, events, model)
    targets = {"authority": rows[0]["authority"][0]["path"], "envelope": rows[1]["envelope"]["path"],
               "manifest": coded["manifest"]["path"], "path": rows[1]["path"], "receipt": rows[3]["receipt"],
               "report": rows[4]["report"], "notes_sha": "notes.md", "outcome": "outcome.json",
               "nested": "nested.md", "commit_intent": "intent.json"}
    target = tmp_path / targets[field]
    if failure == "missing":
        target.unlink()
    else:
        original = target.stat()
        data = target.read_bytes()
        target.write_bytes(bytes([data[0] ^ 1]) + data[1:])
        os.utime(target, ns=(original.st_atime_ns, original.st_mtime_ns))
    with pytest.raises(ledger.LedgerError):
        ledger.validate_history(tmp_path, events, model)


@pytest.mark.parametrize("limit", [16 * 1024, 128 * 1024, 2 * 1024 * 1024])
def test_bounded_json_and_normalized_stream_hashes(tmp_path, limit):
    path = tmp_path / "evidence.json"
    path.write_bytes(b'"' + b'x' * (limit - 2) + b'"')
    ref = {"path": path.name, "sha": ledger.evidence_sha(path.read_bytes())}
    assert len(ledger.read_reference(tmp_path, ref, limit)) == limit - 2
    path.write_bytes(path.read_bytes() + b" ")
    ref["sha"] = ledger.evidence_sha(path.read_bytes())
    with pytest.raises(ledger.LedgerError, match="read bound"):
        ledger.read_reference(tmp_path, ref, limit)
    for data in (b'x' * 65535 + b'\r\nnext\r', b'\0\r\n'):
        path.write_bytes(data)
        ledger._check_reference(tmp_path, {"path": path.name, "sha": ledger.evidence_sha(data)})


@pytest.mark.parametrize("name,error", [("admission-is-the-only-initial-allowance", None),
    ("unfinished-start-debits-before-launch", "unresolved"),
    ("truthful-overrun-finish-closes-run", "closed"),
    ("successor-explicitly-supersedes", None)])
def test_profile_history_and_current_run_gates(tmp_path, name, error):
    model, rows, write = bound_case(tmp_path, name)
    events = parse(rows)
    ledger.validate_history(tmp_path, events, model)
    if error:
        with pytest.raises(ValueError, match=error):
            _preflight._profile_gate(model, ledger.fold(events, model))
    else:
        _preflight._profile_gate(model, ledger.fold(events, model))
        names, _ = _preflight._routes(model, ledger.fold(events, model))
        assert "holistic" not in names
    # Even a superseded run's authority remains bound.
    (tmp_path / rows[0]["authority"][0]["path"]).unlink()
    with pytest.raises(ledger.LedgerError):
        ledger.validate_history(tmp_path, events, model)


@pytest.mark.parametrize("kind", ["file", "ancestor", "escape"])
def test_reference_symlinks_and_escaping_paths_refuse(tmp_path, monkeypatch, kind):
    real = tmp_path / "real"
    real.mkdir()
    (real / "evidence").write_bytes(b"bound")
    # Classification injection requires no Windows symlink-creation privilege.
    original = Path.is_symlink
    refused = real / "evidence" if kind == "file" else real
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == refused or original(path))
    relative = "../evidence" if kind == "escape" else "real/evidence"
    with pytest.raises(ValueError):
        ledger._check_reference(tmp_path, {"path": relative, "sha": ledger.evidence_sha(b"bound")})


@pytest.mark.parametrize("escape", [False, True])
def test_native_directory_link_refusal(tmp_path, escape):
    root = tmp_path / "repo"
    root.mkdir()
    target = (tmp_path if escape else root) / "real"
    target.mkdir()
    (target / "evidence").write_bytes(b"bound")
    link = root / "link"
    if os.name == "nt":
        subprocess.run([os.environ["COMSPEC"], "/c", "mklink", "/J", str(link), str(target)],
                       capture_output=True, check=True, timeout=10)
    else:
        link.symlink_to(target, target_is_directory=True)
    try:
        with pytest.raises(ledger.LedgerError):
            ledger._check_reference(root, {"path": "link/evidence", "sha": ledger.evidence_sha(b"bound")})
    finally:
        if os.name == "nt":
            link.rmdir()  # Remove only the owned junction, never its target tree.
        else:
            link.unlink()


def test_profile_static_accepts_bound_files_without_public_dispatch(project, monkeypatch):
    root, cfg, _ = project
    model, rows, write = bound_case(root, "admission-is-the-only-initial-allowance")
    write(cfg.ledger, b"\n".join(json.dumps(row).encode() for row in rows) + b"\n")
    test_loop.git(root, "add", "roadmap.yaml")
    test_loop.git(root, "commit", "-qm", "owned profile")
    model = replace(model, runtime={"python": platform.python_version()},
                    verification_full=(sys.executable, "-c", "pass"))
    with writer_lock(root, model):
        selected, probes, errors = _preflight._static(root, cfg, model)
    assert errors == ()
    assert selected.identity["python"] == platform.python_version()
    assert probes == ["coder", "reviewer"]
    monkeypatch.setattr(_preflight, "_probe", lambda *a: pytest.fail("public /2 probe"))
    # The runtime/verification changes exist only in the dataclass, not on disk.
    assert _preflight.prepare(root, cfg, model)[2] == ("admission roadmap differs from canonical authority",)


@pytest.mark.parametrize("gate", ["missing_run", "blocked", "pending", "selection", "planned"])
def test_profile_refuses_ungranted_lifecycle_states(tmp_path, gate):
    model, rows, _ = bound_case(tmp_path, "admission-is-the-only-initial-allowance")
    state = ledger.fold(parse(rows), model)
    if gate == "missing_run":
        state = replace(state, run_head=None)
    elif gate == "blocked":
        state = replace(state, slices={key: replace(value, step="blocked") for key, value in state.slices.items()})
    elif gate == "pending":
        state = replace(state, intents={"C1": {}})
    elif gate == "selection":
        runs = ledger._thaw(state.runs)
        runs["R1"]["admission"]["slices"] = []
        state = replace(state, runs=runs)
    else:
        model = replace(model, milestones=(replace(model.milestones[0], status="planned"),))
    with pytest.raises(ValueError):
        _preflight._profile_gate(model, state)


@pytest.mark.parametrize("artifact", ["envelope", "manifest", "receipt"])
def test_valid_hash_does_not_hide_wrong_json_identity(tmp_path, artifact):
    model, rows, write = bound_case(tmp_path, "completed-nonholistic-history-survives-done-roadmap")
    row = rows[{"envelope": 1, "manifest": 2, "receipt": 3}[artifact]]
    path = row[artifact] if artifact == "receipt" else row[artifact]["path"]
    value = json.loads((tmp_path / path).read_bytes())
    value["slice"] = "M001-S02"
    ref = write(path, value)
    if artifact == "receipt":
        row["sha"] = ref["sha"]
    else:
        row[artifact] = ref
    with pytest.raises(ledger.LedgerError, match="identity mismatch"):
        ledger.validate_history(tmp_path, parse(rows), model)


def test_profile_history_error_is_a_static_refusal(project):
    root, cfg, _ = project
    model, rows, write = bound_case(root, "completed-nonholistic-history-survives-done-roadmap")
    rows[2]["manifest"] = write("manifest.json", [])
    write(cfg.ledger, b"\n".join(json.dumps(row).encode() for row in rows) + b"\n")
    with writer_lock(root, model):
        _, _, errors = _preflight._static(root, cfg, model)
    assert any("invalid manifest fields" in error for error in errors)


def test_commit_completion_unfreezes_profile_gate(project, monkeypatch):
    from test_commit_v2 import materialize, pending, approve
    from frutlups import commit
    root, cfg, model, _, _ = materialize(project)
    event, _ = pending(root, cfg, model, monkeypatch)
    events = ledger.read(root / cfg.ledger)
    state = ledger.validate_history(root, events, model)
    with pytest.raises(ValueError, match="commit pending"):
        _preflight._profile_gate(model, state, commit.status(root, events, model))
    rows = approve(root, cfg, model)
    _preflight._profile_gate(model, state, rows)
    assert state.runs["R1"]["consumed"]["jobs"] == 0
    # No new run grant or reservation is required to finish the approved Git work.
    assert len(ledger.read(root / cfg.ledger)) == len(events)


VERSION_PROBE = ("import platform, sys; "
                 "print(f'venv={sys.prefix != sys.base_prefix} python={platform.python_version()}')")


@pytest.mark.parametrize("launcher", ["venv", "base"])
def test_selected_launcher_executes_substituted_python_argv(project, tmp_path, launcher):
    # The verifier's own substitution: only a leading literal python becomes the selected launcher.
    from frutlups import procs
    root, cfg, model = project
    executable = sys.executable if launcher == "venv" else sys._base_executable
    assert Path(sys._base_executable).resolve() != Path(sys.executable).resolve()
    selector(root, executable)
    selected = runtime.resolve_runtime(root, model, git=cfg.git)
    assert Path(selected.executable).resolve() == Path(executable).resolve()
    assert selected.identity["source"] == "project.local"
    argv = runtime.runtime_argv(["python", "-I", "-c", VERSION_PROBE], selected.executable)
    assert argv[1:] == ["-I", "-c", VERSION_PROBE]
    result = procs.run_process(argv, root, dict(os.environ), 30, tmp_path / "out", tmp_path / "err")
    assert (result.exit, result.timed_out, result.exception) == (0, False, None)
    assert result.stdout_path.read_text("utf-8").strip() == (
        f"venv={launcher == 'venv'} python={platform.python_version()}")


# The base launcher lacks the template checker's PyYAML on this host, so its private
# run would stop at the project checker; that path is excluded, not inferred.
@pytest.mark.parametrize(("launcher", "command"), [("venv", "python"), ("venv", "git")])
def test_selected_runtime_reaches_private_coder_and_verifier(project, monkeypatch, launcher, command):
    # Distinct launchers of one interpreter version: not a second Python version, POSIX or minimum.
    import inspect
    from frutlups import _attempts
    executable = sys.executable if launcher == "venv" else sys._base_executable
    assert Path(sys._base_executable).resolve() != Path(sys.executable).resolve()
    full = (("python", "-c", VERSION_PROBE) if command == "python"
            else ("git", "rev-parse", "--is-inside-work-tree"))
    root, cfg, model = test_loop.canonical_project(project, verification_full=full)
    selector(root, executable)
    root, cfg, model = grant_project((root, cfg, model))
    bound = []
    original = _attempts._binding
    signature = inspect.signature(original)

    def spy(*args, **kwargs):
        values = signature.bind(*args, **kwargs).arguments
        bound.append((values["role"], dict(values["runtime_identity"])))
        return original(*args, **kwargs)

    monkeypatch.setattr(_attempts, "_binding", spy)
    assert test_loop.run_transitions(root, cfg, model) == loop.StopReason.done
    verified = test_loop.events_of(root, cfg, "verified")
    assert len(verified) == 1
    value = json.loads((root / verified[0].data["receipt"]).read_text("utf-8"))
    assert value["ok"] is True
    selected = value["runtime"]
    assert selected["source"] == "project.local"
    assert selected["python"] == platform.python_version()
    assert selected["implementation"] == platform.python_implementation()
    assert {role for role, _ in bound} == {"coder", "reviewer", "verification"}
    assert all(identity == selected for _, identity in bound)
    (result,) = value["commands"]
    # The declaration stays literal; only a leading literal python is substituted.
    assert result["argv"] == list(full)
    assert result["exit"] == 0
    if command == "python":
        assert result["stdout_tail"].strip() == (
            f"venv={launcher == 'venv'} python={platform.python_version()}")
    else:
        assert result["stdout_tail"].strip() == "true"
