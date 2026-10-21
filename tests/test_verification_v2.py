"""Owned native producer and immutable opposite-consumer qualification."""
import hashlib
import json
import os
import platform
import subprocess
import sys
import tarfile
from dataclasses import replace
from pathlib import Path, PurePosixPath

import pytest
import yaml
import test_loop
import test_attempts_v2 as attempts
from frutlups import _attempts, _loop_evidence, ledger, loop, receipt, roadmap

project = test_loop.project
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def consumer(tmp_path_factory):
    manifest = json.loads((FIXTURES / "v5_verification_de35316.manifest.json").read_bytes())
    archive = FIXTURES / "v5_verification_de35316.tar"
    raw = archive.read_bytes()
    assert len(raw) == manifest["archive"]["bytes"] == 450560
    assert hashlib.sha256(raw).hexdigest() == manifest["archive"]["raw_sha256"] == "5945e19b11f1bbd4a3df6ac9f6b2e93689101f9fc4de5aa5eee1035d8d1f63d3"
    expected = {r["path"]: r for r in manifest["members"]}
    with tarfile.open(archive) as source:
        members = source.getmembers()
        assert len({m.name for m in members}) == len(members)
        for member in members:
            path = PurePosixPath(member.name)
            assert not path.is_absolute() and ".." not in path.parts and "\\" not in member.name and ":" not in member.name
            assert member.isfile() or member.isdir()
        files = {m.name: source.extractfile(m).read() for m in members if m.isfile()}
        assert set(files) == set(expected) and len(files) == 68
        for name, data in files.items():
            assert len(data) == expected[name]["bytes"]
            assert hashlib.sha256(data).hexdigest() == expected[name]["raw_sha256"]
    root = tmp_path_factory.mktemp("native-consumer")
    for name, data in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return root / "scripts"


def prepare(project, variant=1, command=None, timeout=None):
    root, cfg, model, rows, write = attempts.materialize(project, "verification", seconds=300)
    envelope = ledger.read_reference(root, rows[1]["envelope"])
    envelope.update(full=command or ["python", "-c", f"print('full-{variant}')"],
        runtime={"python": "3.14" if variant == 1 else "3.14.3"},
        timeout_seconds=timeout or 20 + variant, observation="process" if variant == 1 else "fresh_process")
    rows[1]["envelope"] = write(rows[1]["envelope"]["path"], envelope)
    attempts.save(root, cfg, rows)
    cfg = replace(cfg, timeouts=replace(cfg.timeouts, verification_seconds=60))
    return root, cfg, model, rows, write


def step(root, cfg, model):
    obj = attempts.internal_loop(root, cfg, replace(model, runtime={"python": platform.python_version()}))
    obj.roadmap = model
    events = ledger.read(root / cfg.ledger)
    current = ledger.validate_history(root, events, model).slices["M001-S01"]
    obj._slice(model.milestones[0].slices[0], current, events)
    return ledger.validate_history(root, ledger.read(root / cfg.ledger), model)


def opposite(consumer, root, cfg, code):
    result = subprocess.run([sys.executable, "-B", "-c",
        "import sys,json;from pathlib import Path;sys.path.insert(0,sys.argv[2]);r=Path(sys.argv[1]);" + code,
        str(root), str(consumer)], env={**os.environ, "TEMPLATE_GIT_EXECUTABLE": cfg.git},
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize("variant", [1, 2, 3])
def test_actual_frozen_producer_and_opposite_consumer(project, consumer, monkeypatch, variant):
    root, cfg, model, rows, _ = prepare(project, variant)
    prefix = (root / cfg.ledger).read_bytes()
    envelope_path = root / rows[1]["envelope"]["path"]
    envelope_bytes = envelope_path.read_bytes()
    frozen = json.loads(envelope_bytes)
    data = yaml.safe_load((root / "roadmap.yaml").read_text("utf-8"))
    data["runtime"] = {"python": "3.99"}
    data["verification"].update(full=["python", "-c", "raise SystemExit(91)"],
                                timeout_seconds=1, observation="inspection")
    (root / "roadmap.yaml").write_text(yaml.safe_dump(data), encoding="utf-8", newline="\n")
    test_loop.git(root, "add", "--", "roadmap.yaml")
    test_loop.git(root, "commit", "-qm", "owned divergent verification policy", "--", "roadmap.yaml")
    assert test_loop.git(root, "diff", "--cached", "--name-only").stdout == b""
    assert (root / cfg.ledger).read_bytes() == prefix
    assert envelope_path.read_bytes() == envelope_bytes
    assert data["runtime"] != frozen["runtime"]
    for field in ("full", "timeout_seconds", "observation"):
        assert data["verification"][field] != frozen[field]
    model = roadmap.load(root / "roadmap.yaml")
    original = receipt.native_snapshot
    def snapshot(root, config):
        value = original(root, config)
        expected = json.loads(opposite(consumer, root, cfg, "import _workspace;print(json.dumps(_workspace.snapshot(r)))"))
        assert value == expected
        return value
    monkeypatch.setattr(receipt, "native_snapshot", snapshot)
    state = step(root, cfg, model)
    assert state.slices["M001-S01"].step == "reviewing"
    assert (root / cfg.ledger).read_bytes().startswith(prefix)
    assert envelope_path.read_bytes() == envelope_bytes
    events = ledger.read(root / cfg.ledger)
    assert [e.ev.value for e in events[-3:]] == ["attempt_started", "attempt_finished", "verified"]
    assert events[-3].data["allowance_seconds"] == 20 + variant
    value = ledger.read_reference(root, dict(path=events[-1].data["receipt"], sha=events[-1].data["sha"]))
    assert set(value) == {"schema", "slice", "round", "t", "base_commit", "tree_dirty_before", "commands",
                          "tree_clean_after", "ok", "manifest", "witness", "runtime", "observation"}
    assert value["commands"][0]["argv"] == ["python", "-c", f"print('full-{variant}')"]
    assert value["commands"][0]["stdout_tail"] == f"full-{variant}{os.linesep}"
    assert value["commands"][0]["exit"] == 0
    assert value["commands"][0]["timed_out"] is False
    assert value["manifest"] == rows[2]["manifest"]
    assert value["observation"] == ("process" if variant == 1 else "fresh_process")
    assert state.runs["R1"]["consumed"]["jobs"] == 0
    _loop_evidence.review_receipt(root, cfg, events, state.slices["M001-S01"],
        ledger.envelope(root, rows[1]["envelope"], "M001-S01", 1), value)
    original_bytes = (root / events[-1].data["receipt"]).read_bytes()
    opposite(consumer, root, cfg,
        "import ledger,roadmap,_integrity;rm=roadmap.load(r);e=ledger.read(r/'05_governance/ledger.jsonl');"
        "assert not _integrity.errors(r,e,rm);s=ledger.fold(e,rm);"
        "_integrity.require_active(r,s['slices']['M001-S01'],e)")
    assert (root / events[-1].data["receipt"]).read_bytes() == original_bytes


@pytest.mark.parametrize("command,timeout,exit_code,timed_out", [
    (["python", "-c", "raise SystemExit(7)"], 21, 7, False),
    (["python", "-c", "import time;time.sleep(30)"], 2, None, True),
])
def test_native_failed_receipt_is_truthful(project, consumer, command, timeout, exit_code, timed_out):
    root, cfg, model, _, _ = prepare(project, command=command, timeout=timeout)
    state = step(root, cfg, model)
    assert state.slices["M001-S01"].step == "fix"
    events = ledger.read(root / cfg.ledger)
    value = ledger.read_reference(root, dict(path=events[-1].data["receipt"], sha=events[-1].data["sha"]))
    assert value["ok"] is False
    assert value["commands"][0]["exit"] == exit_code
    assert value["commands"][0]["timed_out"] == timed_out
    assert events[-2].data["status"] == "failed"
    opposite(consumer, root, cfg,
        "import ledger,roadmap,_integrity;assert not _integrity.errors(r,ledger.read(r/'05_governance/ledger.jsonl'),roadmap.load(r))")


@pytest.mark.parametrize("phase", ["launch", "save", "saved", "settled", "verified"])
def test_fresh_process_interruption_recovery(project, monkeypatch, phase):
    root, cfg, model, _, _ = prepare(project)
    original_write, original_finish, original_append = receipt.write, _attempts.Operation.finish, ledger._append_operation
    saved_receipt_paths = []
    def stop():
        raise RuntimeError("owned interruption")
    def write(value, path):
        saved_receipt_paths.append(path)
        if phase == "save": stop()
        result = original_write(value, path)
        if phase == "saved": stop()
        return result
    def finish(self, *a, **k):
        result = original_finish(self, *a, **k)
        if phase == "settled": stop()
        return result
    def append(root, path, model, raw, **kwargs):
        result = original_append(root, path, model, raw, **kwargs)
        if phase == "verified" and raw["ev"] == "verified": stop()
        return result
    with monkeypatch.context() as patch:
        patch.setattr(receipt, "write", write)
        patch.setattr(_attempts.Operation, "finish", finish)
        patch.setattr(ledger, "_append_operation", append)
        if phase == "launch": patch.setattr(receipt, "_execute", lambda *a, **k: stop())
        with pytest.raises(RuntimeError, match="owned interruption"):
            step(root, cfg, model)
    saved = (root / cfg.ledger).read_bytes()
    receipt_before = {path: path.read_bytes() if path.exists() else None for path in saved_receipt_paths}
    assert bool(saved_receipt_paths) == (phase != "launch")
    assert all((raw is not None) == (phase != "save") for raw in receipt_before.values())
    code = '''
import sys
from pathlib import Path
from dataclasses import replace
sys.path.insert(0,sys.argv[2])
import conftest,test_verification_v2 as t
from frutlups import ledger,roadmap,receipt,loop
r=Path(sys.argv[1]);cfg=conftest.offline_config();model=roadmap.load(r/'roadmap.yaml')
cfg=replace(cfg,timeouts=replace(cfg.timeouts,verification_seconds=60))
def forbidden(*a,**k):raise AssertionError('duplicate verifier')
receipt._execute=forbidden
state=ledger.validate_history(r,ledger.read(r/cfg.ledger),model)
if sys.argv[3] in ('launch','save','saved'):
    prefix=(r/cfg.ledger).read_bytes()
    try:t.step(r,cfg,model)
    except ValueError as exc:
        assert str(exc)=='unresolved invocation prevents new work', str(exc)
    else:raise AssertionError('unknown attempt resumed')
    assert (r/cfg.ledger).read_bytes()==prefix
    events=ledger.read(r/cfg.ledger)
    assert not any(e.ev in (ledger.Ev.attempt_finished,ledger.Ev.verified) for e in events)
else:
    if state.slices['M001-S01'].step=='verifying':t.step(r,cfg,model)
    events=ledger.read(r/cfg.ledger)
    assert sum(e.ev==ledger.Ev.verified for e in events)==1
    assert sum(e.ev==ledger.Ev.attempt_finished for e in events)==1
assert sum(e.ev==ledger.Ev.attempt_started for e in ledger.read(r/cfg.ledger))==1
'''
    child = subprocess.run([sys.executable, "-B", "-c", code, str(root), str(Path(__file__).parent), phase],
                           capture_output=True, text=True, timeout=40)
    assert child.returncode == 0, child.stderr
    assert {path: path.read_bytes() if path.exists() else None for path in saved_receipt_paths} == receipt_before
    after = (root / cfg.ledger).read_bytes()
    assert after.startswith(saved)
    if phase != "settled": assert after == saved


@pytest.mark.parametrize("drift", ["receipt", "missing", "head", "index", "product", "runtime", "contract"])
def test_settled_verification_refuses_drift(project, monkeypatch, drift):
    root, cfg, model, _, _ = prepare(project)
    original = ledger._append_operation
    def append(root, path, model, raw, **kwargs):
        if raw["ev"] == "verified": raise RuntimeError("owned stop")
        return original(root, path, model, raw, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(ledger, "_append_operation", append)
        with pytest.raises(RuntimeError): step(root, cfg, model)
    events = ledger.read(root / cfg.ledger)
    target = root / events[-1].data["evidence"][0]["path"]
    if drift == "receipt": target.write_bytes(b"{}\n")
    elif drift == "missing": target.unlink()
    elif drift == "head": test_loop.git(root, "commit", "--allow-empty", "-qm", "owned head change")
    elif drift == "index": test_loop.git(root, "add", cfg.ledger)
    elif drift == "product": (root / "07_app/CONTEXT.md").write_bytes(b"unrecorded\n")
    elif drift == "runtime":
        from test_runtime_v2 import selector
        selector(root)
    else: model = replace(model, verification_timeout_seconds=99)
    saved = (root / cfg.ledger).read_bytes()
    monkeypatch.setattr(receipt, "_execute", lambda *a, **k: pytest.fail("duplicate verifier"))
    with pytest.raises((ValueError, loop._Stop)):
        step(root, cfg, model)
    assert (root / cfg.ledger).read_bytes() == saved


def test_exit_zero_overrun_leaves_unresolved_reservation(project, monkeypatch):
    root, cfg, model, _, _ = prepare(project)
    original = receipt._execute
    def execute(*args, **kwargs):
        result = original(*args, **kwargs)
        now = receipt.time.monotonic()
        monkeypatch.setattr(receipt.time, "monotonic", lambda: now + 100)
        return result
    monkeypatch.setattr(receipt, "_execute", execute)
    with pytest.raises(loop._Stop, match="exceeded its deadline"):
        step(root, cfg, model)
    assert ledger.read(root / cfg.ledger)[-1].ev == ledger.Ev.attempt_started


def test_unknown_process_disposition_is_not_settled(project, monkeypatch):
    root, cfg, model, _, _ = prepare(project)
    original = receipt.run_process
    def uncertain(*args, **kwargs):
        result = original(*args, **kwargs)
        return replace(result, exception="owned uncertain cleanup")
    monkeypatch.setattr(receipt, "run_process", uncertain)
    with pytest.raises(loop._Stop, match="disposition unresolved"):
        step(root, cfg, model)
    assert ledger.read(root / cfg.ledger)[-1].ev == ledger.Ev.attempt_started


def test_raw_product_drift_produces_failed_receipt(project, consumer):
    root, cfg, model, _, _ = prepare(project, command=["python", "-c",
        "from pathlib import Path;Path('07_app/CONTEXT.md').write_bytes(b'changed\\r\\n')"])
    state = step(root, cfg, model)
    assert state.slices["M001-S01"].step == "fix"
    event = ledger.read(root / cfg.ledger)[-1]
    value = ledger.read_reference(root, dict(path=event.data["receipt"], sha=event.data["sha"]))
    assert value["commands"][0]["exit"] == 0
    assert value["commands"][0]["timed_out"] is False
    assert value["witness"]["stable"] is False
    assert value["ok"] is False
    opposite(consumer, root, cfg,
        "import ledger,roadmap,_integrity;assert not _integrity.errors(r,ledger.read(r/'05_governance/ledger.jsonl'),roadmap.load(r))")


def test_receipt_collision_preserves_existing_bytes(project, monkeypatch):
    root, cfg, model, _, _ = prepare(project)
    original = receipt.write
    retained = []
    def collision(value, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"owned preexisting bytes\n")
        retained.append(path)
        return original(value, path)
    monkeypatch.setattr(receipt, "write", collision)
    with pytest.raises(loop._Stop): step(root, cfg, model)
    assert retained[0].read_bytes() == b"owned preexisting bytes\n"
    assert ledger.read(root / cfg.ledger)[-1].ev == ledger.Ev.attempt_started


def test_ignored_receipt_destination_refuses_before_reservation(project, monkeypatch):
    root, cfg, model, _, _ = prepare(project)
    with (root / ".git/info/exclude").open("a", encoding="utf-8") as stream:
        stream.write("\n*_verification.json\n")
    monkeypatch.setattr(receipt, "_execute", lambda *a, **k: pytest.fail("ignored destination dispatched"))
    saved = (root / cfg.ledger).read_bytes()
    with pytest.raises(loop._Stop, match="ignored destination"): step(root, cfg, model)
    assert (root / cfg.ledger).read_bytes() == saved


def test_unrecorded_roadmap_drift_refuses_before_reservation(project, monkeypatch):
    root, cfg, model, rows, _ = prepare(project)
    prefix = (root / cfg.ledger).read_bytes()
    envelope_path = root / rows[1]["envelope"]["path"]
    envelope_bytes = envelope_path.read_bytes()
    data = yaml.safe_load((root / "roadmap.yaml").read_text("utf-8"))
    data["verification"]["full"] = ["python", "-c", "raise SystemExit(91)"]
    (root / "roadmap.yaml").write_text(yaml.safe_dump(data), encoding="utf-8", newline="\n")
    model = roadmap.load(root / "roadmap.yaml")
    monkeypatch.setattr(receipt, "_execute", lambda *a, **k: pytest.fail("unrecorded roadmap dispatched"))
    with pytest.raises(loop._Stop, match="unrecorded change: roadmap.yaml"):
        step(root, cfg, model)
    assert (root / cfg.ledger).read_bytes() == prefix
    assert envelope_path.read_bytes() == envelope_bytes


@pytest.mark.parametrize("raw", [b"a\r\nb\rc", b"a\r\n" + b"x" * 70000 + b"\0"],
                         ids=["text-crlf-lone-cr", "binary-late-nul"])
def test_receipt_evidence_hash_domains(raw):
    assert ledger.evidence_sha(raw) == hashlib.sha256(raw if b"\0" in raw else raw.replace(b"\r\n", b"\n")).hexdigest()
