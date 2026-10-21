"""Observer contracts only: the heavy matrix is opt-in, never skipped tests."""
import importlib.util
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location("measure_overhead", Path(__file__).parents[1] / "scripts/measure_overhead.py")
measurement = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(measurement)


def test_forwarder_preserves_identity_arguments_and_exceptions():
    observer = measurement.Observer()
    result = object()
    calls = []
    error = ValueError("exact failure")
    def original(*args, **kwargs):
        calls.append((args, kwargs))
        if kwargs.get("fail"):
            raise error
        return result
    wrapped = observer.forward("operation", original)
    assert wrapped(1, name="value") is result
    with pytest.raises(ValueError) as caught:
        wrapped(2, fail=True)
    assert caught.value is error
    assert calls == [((1,), {"name": "value"}), ((2,), {"fail": True})]
    assert observer.counts["operation"] == 2


def test_hash_observer_forwards_and_restores():
    original = hashlib.sha256
    expected = original(b"abcdef").digest()
    observer = measurement.Observer()
    with observer.installed():
        digest = hashlib.sha256(b"abc")
        assert digest.update(b"def") is None
        assert digest.digest() == expected
        assert digest.hexdigest() == expected.hex()
        assert observer.counts["sha256_bytes"] == 6
    assert hashlib.sha256 is original


def test_read_observer_and_bytes_witness(tmp_path):
    path = tmp_path / "evidence"
    path.write_bytes(b"a\xff\0")
    observer = measurement.Observer()
    with observer.installed():
        assert path.read_bytes() == b"a\xff\0"
        assert observer.counts["path_read_bytes"] == 3
    first = measurement.witness_digest(dict(index=(b"a",), files={}))
    assert len(first) == 64
    assert first != measurement.witness_digest(dict(index=(b"b",), files={}))


def test_matrix_has_exact_sparse_cells():
    rows = measurement.matrix()
    assert len(rows) == 21
    assert rows[:3] == [("idle", 1, 10, i) for i in range(3)]
    for case in ("routed", "findings"):
        assert sum(row[0] == case and row[1:3] == (1, 10) for row in rows) == 3
        for cell in ((10, 10), (50, 10), (10, 1000)):
            assert sum(row[0] == case and row[1:3] == cell for row in rows) == 2


def test_failed_phase_preserves_partial_counts(tmp_path):
    path = tmp_path / "phases"
    observer = measurement.Observer()
    with pytest.raises(RuntimeError):
        with measurement.phase(path, "operation", observer):
            observer.counts["forwarded"] += 1
            raise RuntimeError("controlled")
    start, terminal = map(json.loads, path.read_text().splitlines())
    assert start["event"] == "start"
    assert terminal["exception"] == "RuntimeError"
    assert terminal["counters"] == {"forwarded": 1}


CONTINUATION = [
    ("routed", 20, 10, 0), ("routed", 20, 10, 1),
    ("routed", 10, 1000, 0), ("routed", 10, 1000, 1),
    ("findings", 10, 10, 0), ("findings", 10, 10, 1),
    ("findings", 20, 10, 0), ("findings", 20, 10, 1),
    ("findings", 10, 1000, 0), ("findings", 10, 1000, 1),
]


COMPARISON = [
    ("idle", 1, 10, 0), ("idle", 1, 10, 1), ("idle", 1, 10, 2),
    ("routed", 1, 10, 0), ("routed", 1, 10, 1), ("routed", 1, 10, 2),
    ("findings", 1, 10, 0), ("findings", 1, 10, 1), ("findings", 1, 10, 2),
    ("routed", 10, 10, 0), ("routed", 10, 10, 1),
    ("routed", 20, 10, 0), ("routed", 20, 10, 1),
    ("routed", 10, 1000, 0), ("routed", 10, 1000, 1),
    ("findings", 10, 10, 0), ("findings", 10, 10, 1),
    ("findings", 20, 10, 0), ("findings", 20, 10, 1),
    ("findings", 10, 1000, 0), ("findings", 10, 1000, 1),
]


def test_comparison_exact_order():
    assert measurement.comparison_matrix() == COMPARISON


@pytest.mark.parametrize("other", [["--continuation"], ["--case", "idle"], ["--profile"]])
def test_comparison_mode_exclusion(monkeypatch, other):
    monkeypatch.setattr(sys, "argv", ["measure_overhead.py", "--comparison", *other])
    monkeypatch.setattr(measurement.verify, "observe", lambda *a, **kw: pytest.fail("launched child"))
    with pytest.raises(SystemExit) as caught:
        measurement.main()
    assert caught.value.code == 2


def test_batch_modes_cannot_be_combined():
    with pytest.raises(ValueError, match="mutually exclusive"):
        measurement.batch(continuation=True, comparison=True)


def test_continuation_is_exact_outstanding_selection():
    assert measurement.continuation_matrix() == CONTINUATION
    assert len(set(CONTINUATION)) == 10
    assert not set(CONTINUATION) & set(measurement.matrix()[:9])
    assert not any(case == "routed" and (units, files) == (10, 10)
                   or units == 50 for case, units, files, _ in CONTINUATION)


@pytest.mark.parametrize("failure_at,disposition", [
    (None, "success"), (0, "nonzero"), (0, "timeout"),
    (4, "nonzero"), (9, "missing_terminal"),
])
@pytest.mark.parametrize("mode,selection,expected", [
    ("continuation", "q038-continuation", CONTINUATION),
    ("comparison", "s05-comparison", COMPARISON),
])
def test_completion_and_stop_counts(tmp_path, monkeypatch, failure_at, disposition, mode, selection, expected):
    """Exercise the real selection loop with cheap controlled process outcomes."""
    monkeypatch.setattr(measurement.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(measurement.verify._workspace, "resolve_runtime",
                        lambda *a: (sys.executable, {"python": "3.14.3"}))
    monkeypatch.setattr(measurement, "identities", lambda: {})
    monkeypatch.setattr(measurement, "retained", lambda: {})
    monkeypatch.setattr(measurement.shutil, "which", lambda name: sys.executable)
    calls, cases = [], []
    def observed(argv, *, cwd, env, timeout, journal):
        calls.append(argv)
        result = SimpleNamespace(returncode=0, timed_out=False, stdout=b"controlled")
        if "--case" not in argv:
            return result
        assert timeout == 90
        assert "--profile" not in argv
        case = argv[argv.index("--case") + 1]
        units = int(argv[argv.index("--units") + 1])
        files = int(argv[argv.index("--files") + 1])
        cases.append((case, units, files))
        index = len(cases) - 1
        directory = Path(argv[argv.index("--directory") + 1])
        if index == failure_at and disposition in ("nonzero", "timeout"):
            result.returncode = 7 if disposition == "nonzero" else None
            result.timed_out = disposition == "timeout"
        else:
            terminal = dict(phase="bookkeeping", event="terminal", exception=None, elapsed=1.25)
            (directory / "phases.jsonl").write_text(
                "" if index == failure_at else json.dumps(terminal) + "\n")
        return result
    monkeypatch.setattr(measurement.verify, "observe", observed)
    assert measurement.batch(**{mode: True}) == (0 if failure_at is None else 2)
    expected_attempts = len(expected) if failure_at is None else failure_at + 1
    assert cases == [row[:3] for row in expected[:expected_attempts]]
    assert len(calls) == expected_attempts + 2  # One Git identity and one recorder smoke.
    assert sum("recorder smoke" in " ".join(call) for call in calls) == 1
    journals = list(tmp_path.glob("overhead-*/batch.jsonl"))
    assert len(journals) == 1
    records = [json.loads(line) for line in journals[0].read_text().splitlines()]
    selected = next(row for row in records if row["phase"] == "selection")
    assert selected["cases"] == [list(row) + [False] for row in expected]
    terminal = records[-1]
    assert terminal["selection"] == selection
    assert terminal["expected"] == len(expected)
    assert terminal["attempted"] == expected_attempts
    assert terminal["completed"] == (len(expected) if failure_at is None else failure_at)
    assert terminal["failed_or_incomplete"] == (0 if failure_at is None else 1)
    assert terminal["not_launched"] == len(expected) - expected_attempts
    assert terminal["complete"] is (failure_at is None)
