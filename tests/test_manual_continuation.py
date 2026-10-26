"""Manual blocked work and runner provenance at the unchanged frozen boundary."""
import json
import sys
from dataclasses import replace

import pytest

import test_attempts_v2 as attempts
import test_outcomes_v2 as outcomes
import test_loop
from _manual_continuation import run_case
from frutlups import _attempts, ledger, loop
from test_public_v2 import public_project  # noqa: F401
from test_final_template_v2 import checker_pin  # noqa: F401
from test_checker_v2 import checker_project  # noqa: F401

project = test_loop.project


@pytest.mark.parametrize("first,resumed", [("F", "T"), ("T", "F")])
@pytest.mark.parametrize("result", ["blocked_authority", "blocked_environment"])
def test_public_manual_block_interchanges_and_completes(public_project, first, resumed, result):
    root, cfg, _, output = public_project
    run_case(root, cfg, output, python=sys.executable,
             runner=[sys.executable, "-B", "-c", "from frutlups.cli import main; main()"],
             first_issuer=first, resumed_issuer=resumed, blocked_result=result)


def test_public_manual_continuation_keeps_original_write_fence(public_project):
    root, cfg, _, output = public_project
    run_case(root, cfg, output, python=sys.executable,
             runner=[sys.executable, "-B", "-c", "from frutlups.cli import main; main()"],
             first_issuer="F", resumed_issuer="T", blocked_result="blocked_authority",
             forbidden_write=True)


def manual_block(root, cfg, model, write, *, result="blocked_authority", invocation="manual-owner"):
    rows = [ledger._event_dict(e) for e in ledger.read(root / cfg.ledger)]
    current = ledger.validate_history(root, ledger.read(root / cfg.ledger), model).slices["M001-S01"]
    value = dict(schema="frutlups.outcome/2", slice=current.id, round=current.round,
                 invocation=invocation, outcome=result, requirement="Retain frozen work",
                 reason="External decision needed", actor="human", action="Supply authority",
                 remaining=[], evidence=[], authority=[])
    ref = write(f"05_governance/reviews/m001/manual-r{current.round}.json", value)
    manifest = write(f"05_governance/reviews/m001/manual-manifest-r{current.round}.json",
                     dict(schema="frutlups.manifest/2", slice=current.id, round=current.round, changed=[]))
    rows.append(dict(_attempts.event("coded", slice=current.id, round=current.round,
                     changed=[], result=result, outcome=ref, manifest=manifest), by="human"))
    attempts.save(root, cfg, rows)
    return ledger.validate_history(root, ledger.read(root / cfg.ledger), model)


def resolve(root, cfg, model, write):
    rows = [ledger._event_dict(e) for e in ledger.read(root / cfg.ledger)]
    count = len([r for r in rows if r["ev"] == "resolved"])
    ref = write(f"questions/answered/resolution-{count}.md", b"Owner permits frozen continuation.\n")
    rows.append(dict(_attempts.event("resolved", scope="M001-S01", reason="Owned decision",
                                  authority=[ref]), by="human"))
    attempts.save(root, cfg, rows)
    return outcomes.step(attempts.internal_loop(root, cfg, model))


@pytest.mark.parametrize("invocation", ["manual-owner", "arbitrary-external-identifier"])
def test_manual_eligibility_comes_from_history_not_invocation_spelling(project, monkeypatch, invocation):
    root, cfg, model, _, write = outcomes.materialize(project)
    manual_block(root, cfg, model, write, invocation=invocation)
    resolve(root, cfg, model, write)
    calls = []
    outcomes.adapter(monkeypatch, calls, change=False)
    state = outcomes.step(attempts.internal_loop(root, cfg, model))
    assert state.slices["M001-S01"].step == "verifying"
    assert len(calls) == state.runs["R1"]["consumed"]["jobs"] == 1


@pytest.mark.parametrize("change", [None, "model", "effort", "runtime", "missing", "tampered", "invocation"])
def test_runner_block_preserves_exact_policy(project, monkeypatch, change):
    root, cfg, model, _, write = outcomes.materialize(project)
    calls = []
    outcomes.adapter(monkeypatch, calls, "blocked_environment", change=False)
    blocked = outcomes.step(attempts.internal_loop(root, cfg, model))
    rows = [ledger._event_dict(e) for e in ledger.read(root / cfg.ledger)]
    dispatch = next(r for r in rows if r["ev"] == "artifact" and r["path"].endswith("_dispatch.json"))
    path = root / dispatch["path"]
    if change == "missing":
        rows.remove(dispatch)
        path.unlink()
    elif change in ("runtime", "invocation"):
        value = json.loads(path.read_bytes())
        if change == "runtime":
            value["runtime"]["python"] = "unadmitted-runtime"
        else:
            value["invocation"] = "manual-spoof"
        dispatch.update(write(dispatch["path"], value))
    attempts.save(root, cfg, rows)
    resolve(root, cfg, model, write)
    if change == "tampered":
        path.write_bytes(path.read_bytes() + b" ")
    selected = cfg
    if change in ("model", "effort"):
        selected = replace(cfg, seats={**cfg.seats, "coder": replace(cfg.seats["coder"],
                               **{change: "unadmitted"})})
    outcomes.adapter(monkeypatch, calls, change=False)
    before = (root / cfg.ledger).read_bytes()
    if change:
        with pytest.raises(ValueError, match="unchanged coder configuration|drift"):
            outcomes.step(attempts.internal_loop(root, selected, model))
        assert (root / cfg.ledger).read_bytes() == before
        assert len(calls) == 1
    else:
        state = outcomes.step(attempts.internal_loop(root, selected, model))
        assert state.slices["M001-S01"].step == "verifying"
        assert len(calls) == 2
        assert state.runs["R1"]["consumed"]["jobs"] == blocked.runs["R1"]["consumed"]["jobs"] + 1


@pytest.mark.parametrize("proof", ["attempt", "started", "dispatch", "marker"])
def test_manual_actor_and_prefix_do_not_override_runner_evidence(project, monkeypatch, proof):
    root, cfg, model, _, write = outcomes.materialize(project)
    rows = [ledger._event_dict(e) for e in ledger.read(root / cfg.ledger)]
    if proof == "attempt":
        rows.extend([
            _attempts.event("attempt_started", invocation="manual-owner", scope="M001-S01", round=1,
                            role="coder", contract="a" * 64, allowance_seconds=10, retry="initial"),
            _attempts.event("attempt_finished", invocation="manual-owner", status="completed",
                            usage=dict(secs=1, completeness="unknown")),
        ])
    elif proof in ("started", "dispatch"):
        ref = write(f"05_governance/reviews/m001/M001-S01_r1_{proof}.json", {})
        rows.append(_attempts.event("artifact", scope="M001-S01", round=1, role="evidence", **ref))
    elif proof == "marker":
        path = root / "local_state/frutlups/coding/M001-S01_r1.json"
        path.parent.mkdir(parents=True)
        path.write_text("{}")
    attempts.save(root, cfg, rows)
    manual_block(root, cfg, model, write)
    resolve(root, cfg, model, write)
    before = (root / cfg.ledger).read_bytes()
    monkeypatch.setattr(loop.Loop, "_call", lambda *a, **k: pytest.fail("contradiction dispatched"))
    with pytest.raises(ValueError, match="unchanged coder configuration"):
        outcomes.step(attempts.internal_loop(root, cfg, model))
    assert (root / cfg.ledger).read_bytes() == before


def test_manual_labels_and_repeated_blocks_do_not_invent_runner_policy(project, monkeypatch):
    root, cfg, model, _, write = outcomes.materialize(project)
    for _ in range(2):
        events = ledger.read(root / cfg.ledger)
        current = ledger.validate_history(root, events, model).slices["M001-S01"]
        rows = [ledger._event_dict(e) for e in events]
        ref = write(f"05_governance/reviews/m001/owner-r{current.round}_started.json", {})
        rows.append(_attempts.event("artifact", scope="M001-S01", round=current.round, role="evidence", **ref))
        attempts.save(root, cfg, rows)
        manual_block(root, cfg, model, write)
        rows = [ledger._event_dict(e) for e in ledger.read(root / cfg.ledger)]
        rows[-1].update(seat="coder", by="frutlups")
        attempts.save(root, cfg, rows)
        resolve(root, cfg, model, write)
    calls = []
    outcomes.adapter(monkeypatch, calls, change=False)
    state = outcomes.step(attempts.internal_loop(root, cfg, model))
    assert state.slices["M001-S01"].step == "verifying"
    assert len(calls) == state.runs["R1"]["consumed"]["jobs"] == 1


@pytest.mark.parametrize("changed", [False, True])
def test_manual_round_cannot_erase_earlier_runner_policy(project, monkeypatch, changed):
    root, cfg, model, _, write = outcomes.materialize(project)
    calls = []
    outcomes.adapter(monkeypatch, calls, "blocked_authority", change=False)
    outcomes.step(attempts.internal_loop(root, cfg, model))
    resolve(root, cfg, model, write)
    manual_block(root, cfg, model, write, result="blocked_environment")
    resolve(root, cfg, model, write)
    selected = replace(cfg, seats={**cfg.seats, "coder": replace(cfg.seats["coder"], model="new-model")}) if changed else cfg
    outcomes.adapter(monkeypatch, calls, change=False)
    if changed:
        with pytest.raises(ValueError, match="unchanged coder configuration"):
            outcomes.step(attempts.internal_loop(root, selected, model))
        assert len(calls) == 1
    else:
        state = outcomes.step(attempts.internal_loop(root, selected, model))
        assert state.slices["M001-S01"].step == "verifying"
        assert len(calls) == state.runs["R1"]["consumed"]["jobs"] == 2


@pytest.mark.parametrize("case", ["unresolved", "authority", "jobs", "seconds"])
def test_manual_continuation_does_not_expand_authority_or_allowance(project, monkeypatch, case):
    root, cfg, model, _, write = outcomes.materialize(project)
    manual_block(root, cfg, model, write)
    resolve(root, cfg, model, write)
    rows = [ledger._event_dict(e) for e in ledger.read(root / cfg.ledger)]
    if case == "authority":
        row = next(r for r in rows if r["ev"] == "resolved")
        (root / row["authority"][0]["path"]).unlink()
    elif case == "unresolved":
        rows.append(_attempts.event("attempt_started", invocation="unresolved", scope="M001-S01", round=2,
                                   role="coder", contract="a" * 64, allowance_seconds=10, retry="initial"))
    else:
        rows[0]["allowance"][case] = 1 if case == "jobs" else 10
        rows.extend([
            _attempts.event("attempt_started", invocation="consumed", scope="M001-S01", round=2,
                            role="coder", contract="a" * 64, allowance_seconds=10, retry="initial"),
            _attempts.event("attempt_finished", invocation="consumed", status="failed",
                            usage=dict(secs=10 if case == "seconds" else 1, completeness="unknown")),
        ])
    attempts.save(root, cfg, rows)
    before = (root / cfg.ledger).read_bytes()
    monkeypatch.setattr(loop.Loop, "_job", lambda *a, **k: pytest.fail("inadmissible job"))
    # The ordinary profile/attempt gate, not manual provenance, owns these refusals.
    from frutlups import _preflight
    with pytest.raises((ValueError, FileNotFoundError), match="unresolved|missing|positive|allowance|budget|exhausted"):
        events = ledger.read(root / cfg.ledger)
        current = ledger.validate_history(root, events, model)
        _preflight._profile_gate(model, current)
    assert (root / cfg.ledger).read_bytes() == before
