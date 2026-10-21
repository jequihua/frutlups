"""Durable usage projection: known values, coverage labels and unresolved attempts."""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_loop
from test_attempts_v2 import materialize, operation

from frutlups import cli, gitws, ledger
from frutlups.procs import run_process

project = test_loop.project
Ev = ledger.Ev
FULL = dict(secs=2, tokens_in=10, tokens_out=5, cost_usd=0.25, completeness="complete")
MODEL = SimpleNamespace(milestones=(
    SimpleNamespace(id="M001", slices=(SimpleNamespace(id="M001-S01"), SimpleNamespace(id="M001-S02"))),
    SimpleNamespace(id="M002", slices=()),
))


def start(invocation, scope="M001-S01", role="coder", round_no=1):
    return SimpleNamespace(ev=Ev.attempt_started, round=round_no, slice=None,
                           data=dict(invocation=invocation, scope=scope, role=role))


def finish(invocation, status="completed", **usage):
    return SimpleNamespace(ev=Ev.attempt_finished, round=None, slice=None,
                           data=dict(invocation=invocation, status=status, usage=usage))


def lifecycle(kind, round_no=1, scope="M001-S01", **values):
    return SimpleNamespace(ev=kind, round=round_no, slice=scope, data=values)


def usage(secs=None, tokens_in=None, tokens_out=None, cost_usd=None, completeness="unknown", unresolved=0):
    return dict(secs=secs, tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost_usd,
                completeness=completeness, unresolved_attempts=unresolved)


UNKNOWN = usage()
ONE = usage(2, 10, 5, 0.25, "complete")
TWO = usage(4, 20, 10, 0.5, "complete")
BIG = 2**63 - 1

# name, events, expected S01, expected M001, expected S02
CASES = [
    ("no-history", [], UNKNOWN, UNKNOWN, UNKNOWN),
    ("complete", [start("a"), finish("a", **FULL)], ONE, ONE, UNKNOWN),
    ("known-zero", [start("a"), finish("a", secs=0, tokens_in=0, tokens_out=0, cost_usd=0,
                                       completeness="complete")],
     usage(0, 0, 0, 0, "complete"), usage(0, 0, 0, 0, "complete"), UNKNOWN),
    ("adapter-partial", [start("a"), finish("a", **{**FULL, "completeness": "partial"})],
     usage(2, 10, 5, 0.25, "partial"), usage(2, 10, 5, 0.25, "partial"), UNKNOWN),
    ("complete-label-missing-cost",
     [start("a"), finish("a", secs=2, tokens_in=10, tokens_out=5, completeness="complete")],
     usage(2, 10, 5, None, "partial"), usage(2, 10, 5, None, "partial"), UNKNOWN),
    ("failed-then-retry", [start("a"), finish("a", "failed", **FULL), start("b"), finish("b", **FULL)],
     TWO, TWO, UNKNOWN),
    ("resolved-unknown", [start("a"), finish("a", "resolved", completeness="unknown")],
     UNKNOWN, UNKNOWN, UNKNOWN),
    ("unresolved-only", [start("a")], usage(unresolved=1), usage(unresolved=1), UNKNOWN),
    ("finished-and-unresolved-retry", [start("a"), finish("a", "failed", **FULL), start("b")],
     usage(2, 10, 5, 0.25, "partial", 1), usage(2, 10, 5, 0.25, "partial", 1), UNKNOWN),
    ("verification-seconds-only", [start("v", role="verification"), finish("v", secs=3, completeness="unknown")],
     usage(3, completeness="partial"), usage(3, completeness="partial"), UNKNOWN),
    ("provider-plus-verification",
     [start("a"), finish("a", **FULL), start("v", role="verification"), finish("v", secs=3, completeness="unknown")],
     usage(5, 10, 5, 0.25, "complete"), usage(5, 10, 5, 0.25, "complete"), UNKNOWN),
    ("lifecycle-only", [lifecycle(Ev.coded, secs=4, tokens_in=7, tokens_out=2, cost_usd=0.25)],
     usage(4, 7, 2, 0.25, "partial"), usage(4, 7, 2, 0.25, "partial"), UNKNOWN),
    ("lifecycle-copy-suppressed",
     [start("a"), finish("a", **FULL), lifecycle(Ev.coded, secs=99, tokens_in=99, tokens_out=99, cost_usd=99)],
     ONE, ONE, UNKNOWN),
    ("lifecycle-other-round",
     [start("a"), finish("a", **FULL), lifecycle(Ev.coded, 2, secs=1, tokens_in=1, tokens_out=1, cost_usd=0.25)],
     usage(3, 11, 6, 0.5, "partial"), usage(3, 11, 6, 0.5, "partial"), UNKNOWN),
    ("routed-reviewers",
     [start("r1", role="reviewer"), finish("r1", **FULL), start("r2", role="reviewer"), finish("r2", **FULL),
      lifecycle(Ev.reviewed, secs=99, tokens_in=99, tokens_out=99, cost_usd=99)],
     TWO, TWO, UNKNOWN),
    ("holistic-and-slice",
     [start("h", "M001", "holistic", "holistic"), finish("h", **FULL), start("a"), finish("a", **FULL)],
     ONE, TWO, UNKNOWN),
    ("probe-on-milestone", [start("p", "M001", "probe", "holistic"), finish("p", **FULL)],
     UNKNOWN, ONE, UNKNOWN),
    ("child-unresolved", [start("a"), finish("a", **FULL), start("c", "M001-S02")],
     ONE, usage(2, 10, 5, 0.25, "partial", 1), usage(unresolved=1)),
    ("float-overflow", [start("a"), finish("a", **{**FULL, "cost_usd": 1e308}),
                        start("b"), finish("b", **{**FULL, "cost_usd": 1e308})],
     usage(4, 20, 10, None, "partial"), usage(4, 20, 10, None, "partial"), UNKNOWN),
    ("large-integers", [start("a"), finish("a", **{**FULL, "tokens_in": BIG}),
                        start("b"), finish("b", **{**FULL, "tokens_in": BIG})],
     usage(4, 2 * BIG, 10, 0.5, "complete"), usage(4, 2 * BIG, 10, 0.5, "complete"), UNKNOWN),
]


@pytest.mark.parametrize(("events", "s01", "m001", "s02"), [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_usage_projection_table(events, s01, m001, s02):
    rows = list(cli._usage(None, MODEL, events))
    assert rows == [
        {"action": "usage", "slice": "M001-S01", **s01},
        {"action": "usage", "slice": "M001-S02", **s02},
        {"action": "usage", "milestone": "M001", **m001},
        {"action": "usage", "milestone": "M002", **UNKNOWN},
    ]
    for row in rows:
        json.dumps(row, allow_nan=False)  # No NaN/Infinity output.


def test_usage_text_marks_missing_values_and_coverage():
    rows = list(cli._usage(None, MODEL, [start("a"), finish("a", "failed", **FULL), start("b")]))
    assert cli._text(rows[0]) == ("M001-S01 usage secs=2 tokens_in=10 tokens_out=5 cost_usd=0.25 "
                                  "completeness=partial unresolved_attempts=1")
    assert cli._text(rows[1]) == ("M001-S02 usage secs=? tokens_in=? tokens_out=? cost_usd=? "
                                  "completeness=unknown unresolved_attempts=0")


def _status(root, *options):
    """One fresh managed process running the real status verb."""
    out = root.parent / f"status-{len(list(root.parent.glob('status-*.out')))}"
    result = run_process(
        [sys.executable, "-B", "-c", "from frutlups.cli import main; main()", "status", str(root), *options],
        root, dict(os.environ), 120, out.with_suffix(".out"), out.with_suffix(".err"))
    assert (result.timed_out, result.exception) == (False, None)
    return result.exit, result.stdout_path.read_text("utf-8")


def _snapshot(root, cfg):
    jobs = root / "local_state/frutlups/jobs"
    return ((root / cfg.ledger).read_bytes(), gitws.workspace_witness(root, executable=cfg.git),
            sorted(os.listdir(jobs)) if jobs.is_dir() else None)


def test_fresh_process_status_rebuilds_durable_usage_read_only(project, monkeypatch):
    root, cfg, model, rows, _ = materialize(project)
    with operation(root, cfg, model) as first:
        first.finish(False, SimpleNamespace(usage_completeness="complete",
                                            tokens_in=7, tokens_out=3, cost_usd=0.5))
    with operation(root, cfg, model, retry="transport", parent=first.invocation) as interrupted:
        pass  # Interrupted before completion: a durable unresolved reservation.
    events = ledger.read(root / cfg.ledger)
    secs = next(e.data["usage"]["secs"] for e in events if e.ev == Ev.attempt_finished
                and e.data["invocation"] == first.invocation)
    assert not any(e.ev in (Ev.attempt_started, Ev.attempt_finished, Ev.coded, Ev.reviewed)
                   for e in events[:len(rows)])
    # Fresh processes load the project's own configuration; the local file is ignored.
    (root / "frutlups.local.toml").write_text("\n".join(['schema = "frutlups.local/1"', *(
        f"{name} = {json.dumps(getattr(cfg, name))}"
        for name in ("pi", "claude", "git", "path_dirs", "env_passthrough"))]), encoding="utf-8")

    before = _snapshot(root, cfg)
    first_pair = (_status(root, "--usage", "--json"), _status(root, "--usage"))
    assert _snapshot(root, cfg) == before
    code, output = first_pair[0]
    assert code == 0, output
    lines = [json.loads(line) for line in output.splitlines()]
    expected = usage(secs, 7, 3, 0.5, "partial", 1)
    assert [row for row in lines if row["action"] == "usage"] == [
        {"action": "usage", "slice": "M001-S01", **expected},
        {"action": "usage", "milestone": "M001", **expected},
    ]
    assert {"action": "unresolved_invocation", "invocation": interrupted.invocation} in lines
    code, text = first_pair[1]
    assert code == 0, text
    assert (f"M001-S01 usage secs={secs} tokens_in=7 tokens_out=3 cost_usd=0.5 "
            "completeness=partial unresolved_attempts=1") in text.splitlines()

    # Restart with missing and misleading local results: durable history alone counts.
    for invocation in (first.invocation, interrupted.invocation, "unrelated"):
        directory = root / "local_state/frutlups/jobs" / invocation
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "result.json").write_text(json.dumps(dict(
            role="coder", invocation=invocation, secs=999, tokens_in=999, tokens_out=999, cost_usd=99,
            usage_completeness="complete")), encoding="utf-8")
    (root / "local_state/frutlups/jobs/unrelated/result.json").unlink()
    before = _snapshot(root, cfg)
    assert (_status(root, "--usage", "--json"), _status(root, "--usage")) == first_pair
    assert _snapshot(root, cfg) == before

    # In-process status does not enumerate or read the job store.
    jobs = str(root / "local_state/frutlups/jobs")
    def refuse(original):
        def guarded(path, *args, **kwargs):
            if str(path).startswith(jobs):
                pytest.fail(f"status touched the job store: {path}")
            return original(path, *args, **kwargs)
        return guarded
    written = []
    with monkeypatch.context() as patch:
        for owner, name in ((os, "scandir"), (os, "listdir"), (os, "walk"), (Path, "iterdir"),
                            (Path, "glob"), (Path, "rglob"), (Path, "read_text"), (Path, "read_bytes"),
                            (Path, "open")):
            patch.setattr(owner, name, refuse(getattr(owner, name)))
        cli._status(root, True, True, written.append)
    assert written == output.splitlines()

    # Malformed durable history refuses before any usage output and changes nothing.
    with (root / cfg.ledger).open("ab") as stream:
        stream.write(b'{"ev": "attempt_finished"}\n')
    before = _snapshot(root, cfg)
    code, output = _status(root, "--usage", "--json")
    assert code == 1
    (row,) = [json.loads(line) for line in output.splitlines()]
    assert row["action"] == "internal"
    assert _snapshot(root, cfg) == before
