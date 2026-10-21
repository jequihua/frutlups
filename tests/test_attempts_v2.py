"""Materialized, owned /2 operation tests; public dispatch remains disabled."""
import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import yaml
import test_loop
from test_runtime_v2 import bound_case
from frutlups import _attempts, _preflight, ledger, loop, receipt, roadmap, runtime
from frutlups.seats import Job, Role, pi, claude

legacy_project = test_loop.project


@pytest.fixture
def project(legacy_project, monkeypatch):
    # The pinned fake CLI predates agent_settled. Adapt its synthetic output,
    # leaving the immutable CLI/sample bytes and all lifecycle assertions intact.
    original = pi._argv
    code = ('import runpy,sys; script=sys.argv.pop(1); '
            'runpy.run_path(script,run_name="__main__"); '
            "print('{\"type\":\"agent_settled\"}')")
    monkeypatch.setattr(pi, "_argv", lambda job, cfg: (
        original(job, cfg)[0], "-B", "-c", code, *original(job, cfg)[1:]))
    return legacy_project


def materialize(project, stage="coder", seconds=100):
    root, cfg, _ = project
    model, rows, write = bound_case(root, "completed-holistic-history-survives-done-roadmap")
    data = yaml.safe_load((root / "roadmap.yaml").read_text("utf-8"))
    data["milestones"][0]["status"] = "active"
    data["runtime"] = {"python": platform.python_version()}
    data["verification"]["full"] = [sys.executable, "-c", "print('verified')"]
    (root / "roadmap.yaml").write_text(yaml.safe_dump(data), encoding="utf-8", newline="\n")
    model = roadmap.load(root / "roadmap.yaml")
    for row in rows:
        if "envelope" in row:
            value = json.loads((root / row["envelope"]["path"]).read_bytes())
            item = model.milestones[0].slices[0]
            value.update(full=list(item.verification or model.verification_full),
                         focused=[list(a) for a in (item.focused or model.focused_default)],
                         runtime=dict(model.runtime))
            row["envelope"] = write(row["envelope"]["path"], value)
    rows = rows[:{"probe": 1, "coder": 2, "verification": 3, "reviewer": 4, "holistic": 6}[stage]]
    rows[0]["allowance"].update(jobs=20, seconds=seconds)
    # All source material is committed in this disposable project. The ledger
    # itself is then the only changed tracked path at operation entry.
    test_loop.git(root, "add", ".")
    test_loop.git(root, "commit", "-qm", "owned profile materialization")
    save(root, cfg, rows)
    ledger.validate_history(root, ledger.read(root / cfg.ledger), model)
    return root, cfg, model, rows, write


def save(root, cfg, rows):
    (root / cfg.ledger).write_bytes(b"".join(json.dumps(r).encode() + b"\n" for r in rows))


def job(root, cfg, name="coder", role=Role.coder):
    seat = cfg.seats[name]
    adapter = pi if seat.adapter == "pi" else claude
    prompt = root / "prompt.md"
    return Job(uuid4().hex, role, name, seat.adapter, seat.provider, seat.model, seat.effort,
               prompt, ledger.evidence_sha(prompt.read_bytes()), root, adapter.tools_for(role),
               10, None)


def operation(root, cfg, model, **kwargs):
    current = job(root, cfg)
    return _attempts.Operation(root, cfg, model, "coder", 10, job=current,
                               prompt=current.prompt_path.read_bytes(), **kwargs)


def state(root, cfg, model):
    return ledger.validate_history(root, ledger.read(root / cfg.ledger), model)


def test_start_and_finish_are_fsynced_and_charge_full_local_lifetime(project, monkeypatch):
    root, cfg, model, _, _ = materialize(project, seconds=3)
    calls = []
    original = ledger.os.fsync
    monkeypatch.setattr(ledger.os, "fsync", lambda fd: (calls.append("fsync"), original(fd))[1])
    ticks = iter([10, 10.2])
    with operation(root, cfg, model, clock=lambda: next(ticks)) as op:
        assert calls == ["fsync"]
        assert op.timeout == 3
        assert state(root, cfg, model).runs["R1"]["remaining"]["seconds"] == 0
        op.finish(True, SimpleNamespace(secs=999, usage_completeness="partial", tokens_in=7))
    finish = ledger.read(root / cfg.ledger)[-1].data
    assert calls == ["fsync", "fsync"]
    assert finish["usage"]["secs"] == pytest.approx(0.2)
    assert finish["usage"]["tokens_in"] == 7
    assert "tokens_out" not in finish["usage"]
    assert "cost_usd" not in finish["usage"]
    assert state(root, cfg, model).runs["R1"]["remaining"]["seconds"] == 2


@pytest.mark.parametrize("phase", ["start", "finish"])
def test_fsync_failure_never_exposes_an_uncommitted_refund(project, monkeypatch, phase):
    root, cfg, model, rows, _ = materialize(project)
    original = ledger.os.fsync
    count = 0
    def fail(fd):
        nonlocal count
        count += 1
        if count == (1 if phase == "start" else 2):
            raise OSError("owned fsync failure")
        original(fd)
    monkeypatch.setattr(ledger.os, "fsync", fail)
    entered = []
    with pytest.raises(OSError, match="fsync"):
        with operation(root, cfg, model) as op:
            entered.append(True)
            op.finish(True)
    events = ledger.read(root / cfg.ledger)
    assert len(events) == len(rows) + (phase == "finish")
    assert entered == ([True] if phase == "finish" else [])
    if phase == "finish":
        assert state(root, cfg, model).attempts[op.invocation]["finish"] is None


@pytest.mark.parametrize("after_dispatch", [False, True])
def test_fresh_interpreter_crash_and_clean_restart_cannot_refund(project, after_dispatch):
    root, cfg, model, _, _ = materialize(project)
    code = '''
import os, sys
from pathlib import Path
from frutlups import roadmap
from test_attempts_v2 import operation
import conftest
root=Path(sys.argv[1]); cfg=conftest.offline_config(); model=roadmap.load(root/'roadmap.yaml')
with operation(root,cfg,model):
 (root/'local_state/crash.log').write_bytes(b'owned log')
 if sys.argv[2]=='True':
  import subprocess
  subprocess.run([sys.executable,'-c',"print('owned child completed')"],check=True)
 os._exit(23)
'''
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parent)}
    result = subprocess.run([sys.executable, "-B", "-c", code, str(root), str(after_dispatch)],
                            env=env, capture_output=True, timeout=20)
    assert result.returncode == 23, result.stderr
    (root / "local_state/crash.log").unlink()
    before = (root / cfg.ledger).read_bytes()
    test_loop.git(root, "add", cfg.ledger)
    test_loop.git(root, "commit", "-qm", "preserve charged crash")
    inflated = replace(cfg, max_jobs=99999, max_wall_minutes=99999)
    with pytest.raises(ValueError, match="unresolved"):
        with operation(root, inflated, model):
            pytest.fail("redispatched")
    assert (root / cfg.ledger).read_bytes() == before
    charged = state(root, cfg, model)
    assert charged.runs["R1"]["consumed"]["jobs"] == 1
    assert charged.runs["R1"]["consumed"]["seconds"] == 10


@pytest.mark.parametrize("mutation", ["authority_missing", "authority_corrupt", "product", "prompt", "model", "route"])
def test_restart_revalidates_evidence_and_original_retry_contract(project, mutation):
    root, cfg, model, rows, _ = materialize(project)
    with operation(root, cfg, model) as first:
        first.finish(False)
    if mutation.startswith("authority"):
        path = root / rows[0]["authority"][0]["path"]
        if mutation.endswith("missing"):
            path.unlink()
        else:
            path.write_bytes(b"Authority\n")
    elif mutation == "product":
        (root / "07_app/CONTEXT.md").write_bytes(b"changed product")
    elif mutation == "prompt":
        (root / "prompt.md").write_bytes(b"changed prompt")
    elif mutation == "model":
        cfg = replace(cfg, seats={**cfg.seats, "coder": replace(cfg.seats["coder"], model="different")})
    else:
        model = replace(model, review_routing={**model.review_routing, "low": ("coder",)})
    with pytest.raises(ValueError):
        with operation(root, cfg, model, retry="transport", parent=first.invocation):
            pytest.fail("stale retry dispatched")


def test_overrun_is_not_clamped_and_retires_run(project):
    root, cfg, model, _, _ = materialize(project, seconds=3)
    ticks = iter([0, 4.1])
    with operation(root, cfg, model, clock=lambda: next(ticks)) as op:
        op.finish(True)
    run = state(root, cfg, model).runs["R1"]
    assert run["remaining"]["seconds"] == -2
    assert run["closed"] == "overrun"
    with pytest.raises(ValueError):
        with operation(root, cfg, model):
            pytest.fail("retired run dispatched")


def test_real_probe_uses_clipped_timeout_and_reviewer_tools(project, monkeypatch):
    root, cfg, model, _, _ = materialize(project, "probe", seconds=3)
    actual, calls = pi.PiSeat.run, []
    def invoke(self, current, config):
        events = ledger.read(root / cfg.ledger)
        assert events[-1].ev == ledger.Ev.attempt_started
        assert events[-1].data["role"] == "probe"
        assert current.timeout == 3
        assert current.role == Role.reviewer
        assert current.tools == pi.tools_for(Role.reviewer)
        calls.append(current)
        return actual(self, current, config)
    monkeypatch.setattr(pi.PiSeat, "run", invoke)
    assert _preflight._probe(root, cfg, "coder", _roadmap=model) is None
    events = ledger.read(root / cfg.ledger)
    assert events[-1].ev == ledger.Ev.attempt_finished
    assert events[-1].data["status"] == "completed"
    assert len(calls) == 1
    assert not calls[0].prompt_path.parent.exists()


def internal_loop(root, cfg, model):
    # Exercise private methods with the real /2 model, without changing schemas
    # or enabling the public constructor/dispatch gates.
    obj = object.__new__(loop.Loop)
    obj.root, obj.cfg, obj.roadmap = root, cfg, model
    obj.runtime = runtime.resolve_runtime(root, model)
    obj.clock, obj.started, obj.jobs, obj.emit = time.monotonic, time.monotonic(), 0, None
    return obj


def test_real_coder_transport_is_one_durable_chain(project):
    root, cfg, model, _, _ = materialize(project)
    test_loop.plan(root, coder=[{"failure": "transport"}, {}])
    obj = internal_loop(root, cfg, model)
    obj._call("coder", Role.coder, "prompt.md")
    starts = [e.data for e in ledger.read(root / cfg.ledger) if e.ev == ledger.Ev.attempt_started]
    assert len(starts) == 2
    assert starts[1]["retry"] == "transport"
    assert starts[1]["parent"] == starts[0]["invocation"]
    assert starts[1]["contract"] == starts[0]["contract"]


@pytest.mark.parametrize("failure", ["save", "finish", None])
def test_verification_save_finish_verified_order(project, monkeypatch, failure):
    root, cfg, model, rows, write = materialize(project, "verification")
    obj = internal_loop(root, cfg, model)
    original_write, original_finish = receipt.write, _attempts.Operation.finish
    seen = []
    def save_receipt(value, path):
        assert ledger.read(root / cfg.ledger)[-1].ev == ledger.Ev.attempt_started
        seen.append("save")
        if failure == "save":
            raise OSError("owned save failure")
        result = original_write(value, path)
        data = json.loads(path.read_bytes())
        assert data["schema"] == "frutlups.receipt/2"
        assert data["manifest"] == rows[2]["manifest"]
        assert "changed_files" not in data
        return result
    def finish(self, *args, **kwargs):
        assert seen == ["save"]
        seen.append("finish")
        if failure == "finish":
            raise OSError("owned finish failure")
        return original_finish(self, *args, **kwargs)
    monkeypatch.setattr(receipt, "write", save_receipt)
    monkeypatch.setattr(_attempts.Operation, "finish", finish)
    item = model.milestones[0].slices[0]
    current = state(root, cfg, model).slices[item.id]
    if failure:
        with pytest.raises(loop._Stop, match="owned"):
            obj._slice(item, current, ledger.read(root / cfg.ledger))
    else:
        obj._slice(item, current, ledger.read(root / cfg.ledger))
    events = ledger.read(root / cfg.ledger)
    if failure:
        assert events[-1].ev == ledger.Ev.attempt_started
        assert state(root, cfg, model).runs["R1"]["consumed"]["seconds"] == 10
    else:
        assert [e.ev.value for e in events[-3:]] == ["attempt_started", "attempt_finished", "verified"]
        assert events[-3].data["role"] == "verification"
        assert state(root, cfg, model).runs["R1"]["consumed"]["jobs"] == 0


def test_private_writer_cannot_grant_or_resolve_and_public_stays_refused(project):
    root, cfg, model, rows, _ = materialize(project)
    before = (root / cfg.ledger).read_bytes()
    with pytest.raises(ValueError):
        ledger._append_operation(root, root / cfg.ledger, model, rows[0])
    # Public admission and direct iteration refuse a dataclass-only configuration.
    refusal = "admission configuration differs from canonical project configuration"
    assert loop.preflight(root, cfg, model) == (refusal,)
    assert loop.run(root, cfg, model, emit=lambda _: None) == loop.StopReason.preflight_failed
    notices = []
    assert loop.Loop(root, cfg, model, emit=notices.append).iteration() == loop.StopReason.preflight_failed
    assert notices == [dict(action="stop", reason="preflight_failed", detail=refusal)]
    with pytest.raises(ValueError, match="not enabled"):
        ledger.append(root / cfg.ledger, rows[0], model)
    assert (root / cfg.ledger).read_bytes() == before


def extension(write, revision=1):
    return {**_attempts.event("run_extended", run="R1", milestone="M001", revision=revision,
        allowance=dict(jobs=2, seconds=30, transport_retries=1, format_retries=1),
        reason="Owned explicit extension", authority=[write(f"authority/E{revision}.md", b"extension\n")]),
        "by": "human"}


@pytest.mark.parametrize("kind", ["valid", "fake", "stale", "reused"])
def test_materialized_extensions_cannot_reset_chain(project, kind):
    root, cfg, model, rows, write = materialize(project)
    with operation(root, cfg, model) as first:
        first.finish(False)
    rows = [ledger._event_dict(e) for e in ledger.read(root / cfg.ledger)]
    added = extension(write)
    if kind == "fake":
        added["authority"][0]["sha"] = "0" * 64
    elif kind == "stale":
        added["revision"] = 2
    elif kind == "reused":
        added["authority"] = rows[0]["authority"]
    save(root, cfg, rows + [added])
    if kind != "valid":
        with pytest.raises(ValueError):
            with operation(root, cfg, model, retry="transport", parent=first.invocation):
                pytest.fail("invalid extension dispatched")
        return
    with operation(root, cfg, model, retry="transport", parent=first.invocation) as second:
        second.finish(False)
    assert state(root, cfg, model).runs["R1"]["revision"] == 1
    rows = [ledger._event_dict(e) for e in ledger.read(root / cfg.ledger)]
    save(root, cfg, rows + [extension(write, 2)])
    with pytest.raises(ValueError, match="retry"):
        with operation(root, cfg, model, retry="transport", parent=second.invocation):
            pytest.fail("extension reset used transport retry")


def test_unfinished_overlap_and_safe_child_resolution_bind_new_evidence(project):
    root, cfg, model, _, write = materialize(project)
    with operation(root, cfg, model) as first:
        child = subprocess.run([sys.executable, "-c", "print('owned completion')"],
                               capture_output=True, timeout=10)
        assert child.returncode == 0
        with pytest.raises(ValueError, match="unresolved"):
            with operation(root, cfg, model):
                pytest.fail("overlapping operation")
    rows = [ledger._event_dict(e) for e in ledger.read(root / cfg.ledger)]
    recovery = {**_attempts.event("attempt_finished", invocation=first.invocation, status="resolved",
        usage={"completeness": "unknown"}, reason="Human observed owned child completion",
        evidence=[write("authority/recovery.md", child.stdout)]), "by": "human"}
    with pytest.raises(ValueError):
        ledger._append_operation(root, root / cfg.ledger, model, recovery)
    # Materialize the human's direct finish, without a fabricated checkpoint.
    save(root, cfg, rows + [recovery])
    assert state(root, cfg, model).runs["R1"]["consumed"]["seconds"] == 10
    with operation(root, cfg, model, retry="transport", parent=first.invocation) as retry:
        retry.finish(True)
    (root / recovery["evidence"][0]["path"]).unlink()
    with pytest.raises(ValueError):
        state(root, cfg, model)


@pytest.mark.parametrize("transport_first", [False, True])
def test_real_readonly_format_chain_preserves_original_contract(project, monkeypatch, transport_first):
    root, cfg, model, _, _ = materialize(project, "reviewer")
    name = model.review_routing[model.milestones[0].slices[0].risk][0]
    planned = [{"text": "broken"}, {"text": "broken again"}]
    if transport_first:
        planned.insert(0, {"failure": "transport"})
    test_loop.plan(root, **{name: planned})
    obj = internal_loop(root, cfg, model)
    with pytest.raises(ledger.LedgerError, match="review format retry allowance exhausted"):
        obj._review(name, Role.reviewer, "prompt.md", "M001-S01", 1)
    starts = [e.data for e in ledger.read(root / cfg.ledger) if e.ev == ledger.Ev.attempt_started]
    assert len(starts) == (3 if transport_first else 2)
    assert [e["retry"] for e in starts] == (["initial", "transport", "format"] if transport_first
                                           else ["initial", "format"])
    assert all(e["role"] == "reviewer" for e in starts)
    assert all(e["contract"] == starts[0]["contract"] for e in starts)
    assert all(b["parent"] == a["invocation"] for a, b in zip(starts, starts[1:]))


def test_verifier_ledger_mutation_leaves_unfinished_reservation(project, monkeypatch):
    root, cfg, model, _, _ = materialize(project, "verification")
    original = receipt.run
    def run(*args, **kwargs):
        result = original(*args, **kwargs)
        with (root / cfg.ledger).open("ab") as stream:
            stream.write(b"\n")
        return result
    monkeypatch.setattr(receipt, "run", run)
    obj = internal_loop(root, cfg, model)
    item = model.milestones[0].slices[0]
    with pytest.raises(loop._Stop, match="changed the ledger"):
        obj._slice(item, state(root, cfg, model).slices[item.id], ledger.read(root / cfg.ledger))
    assert ledger.read(root / cfg.ledger)[-1].ev == ledger.Ev.attempt_started


def test_actual_probe_child_is_terminated_at_reserved_deadline(project, monkeypatch):
    root, cfg, model, _, _ = materialize(project, "probe", seconds=1)
    monkeypatch.setattr(pi, "_argv", lambda job, config:
        (sys.executable, "-c", "import time; time.sleep(10)"))
    started = time.monotonic()
    error = _preflight._probe(root, cfg, "coder", _roadmap=model)
    assert error is not None
    assert time.monotonic() - started < 5
    events = ledger.read(root / cfg.ledger)
    assert events[-2].data["allowance_seconds"] == 1
    assert events[-1].data["status"] == "failed"
    assert events[-1].data["usage"]["secs"] >= 1
    assert state(root, cfg, model).runs["R1"]["remaining"]["seconds"] <= 0


def test_completed_operation_survives_fresh_done_history_replay(project):
    root, cfg, model, _, _ = materialize(project, "holistic")
    with _attempts.Operation(root, cfg, model, "holistic", 10,
            job=job(root, cfg, "holistic", Role.holistic), prompt=(root / "prompt.md").read_bytes()) as op:
        op.finish(True)
    # The same owned, materialized reports close the existing history.
    _, complete, _ = bound_case(root, "completed-holistic-history-survives-done-roadmap")
    rows = [ledger._event_dict(e) for e in ledger.read(root / cfg.ledger)]
    # bound_case has rewritten the envelope; restore its original bound content.
    value = json.loads((root / rows[1]["envelope"]["path"]).read_bytes())
    item = model.milestones[0].slices[0]
    value.update(full=list(item.verification or model.verification_full),
                 focused=[list(a) for a in (item.focused or model.focused_default)], runtime=dict(model.runtime))
    (root / rows[1]["envelope"]["path"]).write_bytes(json.dumps(value).encode())
    save(root, cfg, rows + complete[6:])
    code = "from pathlib import Path; from frutlups import ledger,roadmap; import sys; r=Path(sys.argv[1]); m=roadmap.load(r/'roadmap.yaml'); s=ledger.validate_history(r,ledger.read(r/sys.argv[2]),m); assert 'M001' in s.milestones_done; assert len(s.attempts)==1"
    result = subprocess.run([sys.executable, "-B", "-c", code, str(root), cfg.ledger],
                            capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("mutation", ["tools", "role", "parser", "envelope"])
def test_complete_seat_binding_rejects_stale_retry(project, monkeypatch, mutation):
    root, cfg, model, _, write = materialize(project)
    with operation(root, cfg, model) as first:
        first.finish(False)
    current = job(root, cfg)
    if mutation == "tools":
        current = replace(current, tools=())
    elif mutation == "role":
        current = replace(current, role=Role.reviewer, tools=pi.READ_TOOLS)
    elif mutation == "parser":
        parser = root / "local_state/parser.py"
        parser.write_bytes(b"changed parser")
        monkeypatch.setattr(pi, "__file__", str(parser))
    else:
        rows = [ledger._event_dict(e) for e in ledger.read(root / cfg.ledger)]
        value = json.loads((root / rows[1]["envelope"]["path"]).read_bytes())
        value["acceptance"] = ["Changed current approval"]
        rows[1]["envelope"] = write(rows[1]["envelope"]["path"], value)
        save(root, cfg, rows)
    with pytest.raises(ValueError):
        with _attempts.Operation(root, cfg, model, "coder", 10, job=current,
                prompt=current.prompt_path.read_bytes(), retry="transport", parent=first.invocation):
            pytest.fail("changed contract launched")


def test_materialized_extension_cannot_revive_overrun(project):
    root, cfg, model, _, write = materialize(project, seconds=1)
    ticks = iter([0, 2])
    with operation(root, cfg, model, clock=lambda: next(ticks)) as op:
        op.finish(True)
    rows = [ledger._event_dict(e) for e in ledger.read(root / cfg.ledger)]
    save(root, cfg, rows + [extension(write)])
    with pytest.raises(ValueError, match="closed run"):
        with operation(root, cfg, model, retry="transport", parent=op.invocation):
            pytest.fail("revived retired run")


@pytest.mark.parametrize("adapter", ["pi", "claude"])
def test_probe_construction_uses_normal_reviewer_tools(project, monkeypatch, adapter):
    root, cfg, model, _, _ = materialize(project, "probe", seconds=3)
    name = "coder" if adapter == "pi" else "reviewer"
    module, seat = (pi, pi.PiSeat) if adapter == "pi" else (claude, claude.ClaudeSeat)
    if adapter == "claude":
        # Its pinned fake CLI recognizes the old unframed probe text only.
        # Keep those bytes immutable and fake transport at the real adapter.
        from test_seats import _fake_process, SAMPLES
        monkeypatch.setattr(claude, "run_process", _fake_process(SAMPLES / "claude_opus_ok.json"))
    actual, seen = seat.run, []
    def invoke(self, current, config):
        assert current.tools == module.tools_for(Role.reviewer)
        assert current.pi_record_bytes == cfg.pi_record_bytes
        assert ledger.read(root / cfg.ledger)[-1].data["role"] == "probe"
        seen.append(current)
        return actual(self, current, config)
    monkeypatch.setattr(seat, "run", invoke)
    assert _preflight._probe(root, cfg, name, _roadmap=model) is None
    assert len(seen) == 1
    assert ledger.read(root / cfg.ledger)[-1].data["status"] == "completed"


@pytest.mark.parametrize(("adapter", "message"), [
    ("pi", "Pi job tools do not match its role"),
    ("claude", "Claude job tools do not match its role"),
])
def test_fixed_probe_tools_mismatch_retains_distinct_guards(project, monkeypatch, adapter, message):
    root, cfg, model, _, _ = materialize(project, "probe")
    name = "coder" if adapter == "pi" else "reviewer"
    module = pi if adapter == "pi" else claude
    current = replace(job(root, cfg, name, Role.reviewer), tools=())
    before = (root / cfg.ledger).read_bytes()
    # Call the real adapter guard even though the project fixture substitutes CLI argv.
    import importlib.util
    spec = importlib.util.spec_from_file_location("frutlups.seats.guard", module.__file__)
    original = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(original)
    with pytest.raises(ValueError, match=f"^{message}$"):
        original._argv(current, cfg)
    with pytest.raises(ValueError, match="^job tools differ from role$"):
        with _attempts.Operation(root, cfg, model, "probe", 60, job=current,
                                 prompt=current.prompt_path.read_bytes()):
            pytest.fail("inconsistent fixed job reserved")
    assert (root / cfg.ledger).read_bytes() == before


@pytest.mark.parametrize("change", ["pi-limit", "framing"])
def test_changed_invocation_bytes_refuse_stale_retry(project, monkeypatch, change):
    root, cfg, model, _, _ = materialize(project)
    with operation(root, cfg, model) as first:
        first.finish(False)
    current = job(root, cfg)
    if change == "pi-limit":
        cfg = replace(cfg, pi_record_bytes=32 * 1024 * 1024)
        current = replace(current, pi_record_bytes=cfg.pi_record_bytes)
    else:
        from frutlups import render
        monkeypatch.setattr(render, "MAIN_FRAMING", render.MAIN_FRAMING + " changed")
    before = (root / cfg.ledger).read_bytes()
    with pytest.raises(ValueError, match="^stale retry contract$"):
        with _attempts.Operation(root, cfg, model, "coder", 10, job=current,
                                 prompt=current.prompt_path.read_bytes(), retry="transport",
                                 parent=first.invocation):
            pytest.fail("changed contract reserved")
    assert (root / cfg.ledger).read_bytes() == before


def test_omitted_and_explicit_default_pi_limit_bind_identically(project):
    root, cfg, model, _, _ = materialize(project)
    events = ledger.read(root / cfg.ledger)
    current = job(root, cfg)
    args = (model, events, state(root, cfg, model), "M001-S01", 1, "coder")
    first = _attempts._binding(root, cfg, *args, current, current.prompt_path.read_bytes())
    explicit = replace(cfg, pi_record_bytes=2 * 1024 * 1024)
    second = _attempts._binding(root, explicit, *args, current, current.prompt_path.read_bytes())
    assert first == second
    with pytest.raises(ValueError, match="Pi job record limit differs"):
        _attempts._binding(root, replace(cfg, pi_record_bytes=4 * 1024 * 1024),
                           *args, current, current.prompt_path.read_bytes())


@pytest.mark.parametrize(("role", "remaining", "expected"), [
    ("coder", 10000, 7200), ("reviewer", 10000, 7200),
    ("verification", 10000, 600), ("coder", 100, 100), ("verification", 100, 100),
    ("probe", 10000, 60),
])
def test_verification_only_envelope_cap_and_role_run_bounds(project, role, remaining, expected):
    root, cfg, model, rows, write = materialize(project, role, seconds=remaining)
    prompt_row = next((r for r in rows if r.get("ev") == "prompt"), None)
    if prompt_row:
        value = json.loads((root / prompt_row["envelope"]["path"]).read_bytes())
        value["timeout_seconds"] = 600
        prompt_row["envelope"] = write(prompt_row["envelope"]["path"], value)
        save(root, cfg, rows)
    cfg = replace(cfg, timeouts=replace(cfg.timeouts, coder_seconds=7200, reviewer_seconds=7200))
    current = (None if role == "verification" else job(root, cfg,
               "coder" if role in ("coder", "probe") else "reviewer",
               Role.reviewer if role in ("reviewer", "probe") else Role.coder))
    with _attempts.Operation(root, cfg, model, role, 60 if role == "probe" else 7200,
                             job=current, prompt=current.prompt_path.read_bytes() if current else b"") as op:
        assert op.timeout == expected
        assert ledger.read(root / cfg.ledger)[-1].data["allowance_seconds"] == expected
        if role == "verification":
            item = model.milestones[0].slices[0]
            checked = receipt.run(root, item, 1, model, cfg, False, timeout=op.timeout)
            assert checked.ok
            path = root / "owned-limit-receipt.json"
            ref = dict(path=path.name, sha=receipt.write(checked, path))
            op.finish(True, evidence=[ref])
        else:
            op.finish(True)


def test_recorded_clipped_allowance_replays_without_recomputation(project):
    root, cfg, model, _, _ = materialize(project, seconds=10000)
    with _attempts.Operation(root, cfg, model, "coder", 600, job=job(root, cfg),
                             prompt=(root / "prompt.md").read_bytes()) as old:
        old.finish(True)
    before = (root / cfg.ledger).read_bytes()
    assert state(root, cfg, model).attempts[old.invocation]["start"]["allowance_seconds"] == 600
    changed = replace(cfg, timeouts=replace(cfg.timeouts, coder_seconds=7200))
    replayed = state(root, changed, model)
    assert replayed.attempts[old.invocation]["start"]["allowance_seconds"] == 600
    assert (root / cfg.ledger).read_bytes() == before
