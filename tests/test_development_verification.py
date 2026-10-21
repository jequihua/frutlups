"""Cheap failure-path contracts for the maintained manual reporting path."""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import verify
import check_project


@pytest.fixture
def manual(tmp_path, monkeypatch):
    argv = ["python", "-c", "print('controlled')"]
    frozen = dict(full=argv, runtime={"python": "3.14.3"},
                  timeout_seconds=5, observation="process")
    current = dict(step="verifying", round=1, changed=[], envelope={},
                   manifest={"path": "manifest.json", "sha": "a" * 64})
    monkeypatch.setattr(verify.roadmap, "load", lambda root: dict(
        schema="frutlups.roadmap/2", verification=frozen))
    monkeypatch.setattr(verify.roadmap, "slice_by_id", lambda *a: (None, {}))
    monkeypatch.setattr(verify.ledger, "read", lambda *a: [])
    monkeypatch.setattr(verify.ledger, "require_artifacts", lambda *a: None)
    monkeypatch.setattr(verify.ledger, "require_product", lambda *a: None)
    monkeypatch.setattr(verify.ledger, "fold", lambda *a: {"slices": {"M001-S01": current}})
    monkeypatch.setattr(verify._integrity, "require_active", lambda *a: None)
    monkeypatch.setattr(verify._integrity, "envelope", lambda *a: frozen)
    monkeypatch.setattr(verify._integrity, "product_digest", lambda *a: "b" * 64)
    monkeypatch.setattr(verify._workspace, "resolve_runtime", lambda *a: (
        sys.executable, dict(python="3.14.3", implementation="CPython", source="invoking_python", sha="c" * 64)))
    monkeypatch.setattr(verify.c, "status_bytes", lambda *a: b"")
    monkeypatch.setattr(verify._workspace, "snapshot", lambda *a: dict(head="d" * 40, index="e" * 64, files={}))
    appended = []
    monkeypatch.setattr(verify.ledger, "append", lambda path, row, rm: appended.append(row))
    return tmp_path, frozen, appended


@pytest.mark.parametrize("code,timeout,exit_code,timed_out", [
    ("print('controlled')", 5, 0, False),
    ("print('999 passed');raise SystemExit(7)", 5, 7, False),
    ("import time;time.sleep(10)", .15, None, True),
])
def test_real_child_disposition_and_native_wire(manual, code, timeout, exit_code, timed_out):
    root, frozen, appended = manual
    frozen["full"][-1] = code
    journal = root / "progress.jsonl"
    result, relative = verify.run(root, "M001-S01", timeout, journal=journal)
    assert set(result) == {"schema", "slice", "round", "t", "base_commit",
        "tree_dirty_before", "commands", "tree_clean_after", "ok", "manifest",
        "witness", "runtime", "observation"}
    assert result["commands"][0]["exit"] == exit_code
    assert result["commands"][0]["timed_out"] is timed_out
    assert result["ok"] is (exit_code == 0 and not timed_out)
    assert json.loads((root / relative).read_bytes()) == result
    assert appended[0]["ok"] is result["ok"]
    terminal = next(r for r in map(json.loads, journal.read_text().splitlines()) if r["phase"] == "process_terminal")
    assert terminal["exit"] == exit_code
    assert terminal["timed_out"] is timed_out


def test_unstable_witness_is_failed_receipt(manual, monkeypatch):
    root, _, appended = manual
    values = iter([dict(head="d" * 40, index="e" * 64), dict(head="f" * 40, index="e" * 64)])
    monkeypatch.setattr(verify._workspace, "snapshot", lambda *a: next(values))
    receipt, _ = verify.run(root, "M001-S01", journal=root / "progress.jsonl")
    assert receipt["commands"][0]["exit"] == 0
    assert receipt["witness"]["stable"] is False
    assert receipt["ok"] is False
    assert appended[0]["ok"] is False


@pytest.mark.parametrize("failure", ["serialization", "witness", "append"])
def test_terminal_survives_later_error(manual, monkeypatch, failure):
    root, _, appended = manual
    if failure == "serialization":
        monkeypatch.setattr(verify, "_public_argv", lambda *a: [object()])
    elif failure == "witness":
        original = verify._workspace.snapshot
        calls = []
        def snapshot(*a):
            calls.append(1)
            if len(calls) == 2:
                raise OSError("controlled witness failure")
            return original(*a)
        monkeypatch.setattr(verify._workspace, "snapshot", snapshot)
    else:
        def fail(*a):
            raise OSError("controlled append failure")
        monkeypatch.setattr(verify.ledger, "append", fail)
    journal = root / "progress.jsonl"
    with pytest.raises((TypeError, OSError)):
        verify.run(root, "M001-S01", journal=journal)
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    terminal = next(row for row in rows if row["phase"] == "process_terminal")
    assert terminal["exit"] == 0
    assert terminal["timed_out"] is False
    assert appended == []


def test_collision_prevents_child_and_preserves_receipt(manual, monkeypatch):
    root, _, _ = manual
    path = root / "05_governance/reviews/m001/M001-S01_r1_verification.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"original")
    def forbidden(*a, **k):
        pytest.fail("child must not start on collision")
    monkeypatch.setattr(verify._process, "run", forbidden)
    with pytest.raises(ValueError, match="identity already exists"):
        verify.run(root, "M001-S01", journal=root / "progress.jsonl")
    assert path.read_bytes() == b"original"


def test_recording_failure_before_dispatch_and_explicit_json(tmp_path, monkeypatch):
    path = tmp_path / "journal"
    verify.record(path, path=Path("relative"), data=b"\xff\0")
    assert json.loads(path.read_text())["data"] == {"base64": "/wA="}
    with pytest.raises(TypeError):
        verify.record(path, data=object())
    def fail(*a, **k):
        raise OSError("controlled recording failure")
    monkeypatch.setattr(verify, "record", fail)
    with pytest.raises(OSError, match="recording"):
        verify.observe([sys.executable, "-c", "raise SystemExit(99)"], cwd=tmp_path,
                       env=os.environ, timeout=1, journal=path)


def test_discovery_timing_keeps_failed_and_incomplete_progress(tmp_path):
    path = tmp_path / "phases.jsonl"
    guard = check_project.DiscoveryGuard(path)
    guard.pytest_runtest_logstart("tests/test_example.py::test_one", None)
    guard.pytest_runtest_logreport(SimpleNamespace(nodeid="tests/test_example.py::test_one",
        when="call", duration=.125, outcome="failed", skipped=False))
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["event"] for row in rows] == ["start", "phase"]
    assert rows[1]["duration"] == .125
    assert rows[1]["outcome"] == "failed"
    assert guard.unexpected_skips == []
    guard.pytest_runtest_logreport(SimpleNamespace(nodeid="unexpected", when="setup",
        duration=.001, outcome="skipped", skipped=True, longrepr=("file", 1, "Skipped: reason")))
    assert guard.unexpected_skips == ["unexpected"]
    with pytest.raises(pytest.UsageError):
        guard.pytest_collection_modifyitems([])


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), -1, 0])
def test_invalid_deadlines_refuse_before_launch(manual, monkeypatch, timeout):
    root, _, _ = manual
    def forbidden(*a, **k):
        pytest.fail("invalid deadline dispatched a child")
    monkeypatch.setattr(verify._process, "run", forbidden)
    with pytest.raises(ValueError, match="timeout"):
        verify.run(root, "M001-S01", timeout, journal=root / "journal")


def test_recording_margin_and_frozen_timeout_are_clipped(manual, monkeypatch):
    root, _, _ = manual
    observed = []
    def child(*a, **kwargs):
        observed.append(kwargs["timeout"])
        return verify._process.Result(0, b"", b"", False, 0, 0)
    monkeypatch.setattr(verify._process, "run", child)
    verify.run(root, "M001-S01", 100, journal=root / "journal",
               deadline=verify.time.monotonic() + 32)
    assert 0 < observed[0] <= 2
