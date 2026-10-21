"""Owned holistic lifecycle qualification; public /2 execution stays disabled."""
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
import test_loop
import test_attempts_v2 as attempts
import test_checkpoints_v2 as checkpoints
from _restoration import restore_file, snapshot
from frutlups import _attempts, _preflight, commit, gitws, ledger, loop, roadmap, verdict
from frutlups.seats import Role

project = test_loop.project


def report(result="pass", rows=""):
    return checkpoints.report(rows, result).replace("M001-S01 round 1", "M001 round holistic")


def append(root, cfg, *rows):
    with (root / cfg.ledger).open("ab") as stream:
        for row in rows:
            stream.write(json.dumps(row).encode() + b"\n")


def materialize(project, profile=False):
    root, cfg, model, _, write = checkpoints.materialize(project, profile, seats=1)
    ref = write("05_governance/reviews/m001/accepted-review.md", checkpoints.report().encode())
    append(root, cfg, dict(_attempts.event("reviewed", slice="M001-S01", round=1,
        report=ref["path"], sha=ref["sha"], verdict="pass", open=[]), by="human"))
    commit.approve(root, model, _attempts.event("accepted", slice="M001-S01", round=1), "Accept", False)
    return root, cfg, model, write


def runner(root, cfg, model, until="milestone"):
    obj = attempts.internal_loop(root, cfg, model)
    obj.until, obj.target_milestone, obj.accepted_this_run = until, None, False
    return obj


def step(obj):
    state = ledger.validate_history(obj.root, ledger.read(obj.root / obj.cfg.ledger), obj.roadmap)
    obj._holistic(obj.roadmap.milestones[0], state)
    return ledger.validate_history(obj.root, ledger.read(obj.root / obj.cfg.ledger), obj.roadmap)


def refusal(obj, calls, detail):
    """Assert the real iteration refuses without changing its owned evidence."""
    root, cfg = obj.root, obj.cfg
    before = snapshot(root, cfg, calls, "refusal")
    witness = gitws.workspace_witness(root, executable=cfg.git)
    index = (root / ".git/index").read_bytes()
    authority = _preflight._authority_bytes(root)
    saved = (root / cfg.ledger).read_bytes()
    jobs = obj.jobs
    notices = []
    obj.emit = notices.append
    assert obj._iteration_owned() == loop.StopReason.internal
    assert notices == [dict(action="stop", reason="internal", detail=detail)]
    assert (root / cfg.ledger).read_bytes() == saved
    assert (root / ".git/index").read_bytes() == index
    assert _preflight._authority_bytes(root) == authority
    assert gitws.workspace_witness(root, executable=cfg.git) == witness
    assert snapshot(root, cfg, calls, "refusal") == before
    assert obj.jobs == jobs


def multi_model(model):
    first = replace(model.milestones[0], holistic_review=True)
    second = replace(first, id="M002",
                     slices=(replace(first.slices[0], id="M002-S01", milestone_id="M002"),))
    planned = replace(second, id="M003", status="planned",
                      slices=(replace(second.slices[0], id="M003-S01", milestone_id="M003"),))
    return replace(model, milestones=(first, second, planned))


def multi_materialize(project):
    """Record the complete topology before any grant or task issuance."""
    root, cfg, model = project
    root, cfg, model = test_loop.grant_project((root, cfg, multi_model(model)))
    assert roadmap.load(root / "roadmap.yaml") == model
    obj = test_loop.Controller(root, cfg, model, until="slice")
    for _ in range(5):
        assert obj.iteration() is None
    state = ledger.validate_history(root, ledger.read(root / cfg.ledger), model)
    assert state.slices["M001-S01"].step == "accepted"
    assert state.slices["M002-S01"].step == "unstarted"
    assert state.milestones_done == set()
    return root, cfg, model


@pytest.mark.parametrize("profile,close_commit", [(False, False), (True, False), (False, True), (True, True)])
def test_holistic_actual_iteration_and_exact_replay(project, monkeypatch, profile, close_commit):
    root, cfg, model, _ = materialize(project, profile)
    cfg = replace(cfg, commit_on_accept=close_commit, reviews_dir="05_governance/reviews/custom")
    calls = []
    checkpoints.adapter(monkeypatch, calls, [report()])
    obj = runner(root, cfg, model)
    if not profile:
        refusal(obj, calls, "M001: LedgerError: fresh dispatch requires an autonomy profile; owner must inspect")
        assert calls == []
        assert not (root / "local_state/frutlups/jobs").exists()
        return
    assert obj._iteration_owned() is None
    saved = (root / cfg.ledger).read_bytes()
    before = ledger.validate_history(root, ledger.read(root / cfg.ledger), model)
    assert before.milestones_done == {"M001"}
    assert before.holistic["M001"]["step"] == "done"
    assert len(before.checkpoints) == 1
    assert len(before.attempts) == 1
    assert calls[0].role == Role.holistic
    assert calls[0].seat_name == "holistic"
    assert step(runner(root, cfg, model)) == before
    assert (root / cfg.ledger).read_bytes() == saved
    assert len(calls) == 1
    assert sum(e.ev == ledger.Ev.holistic_reviewed for e in ledger.read(root / cfg.ledger)) == 1
    assert sum(e.ev == ledger.Ev.milestone_done for e in ledger.read(root / cfg.ledger)) == 1
    if profile:
        assert before.runs["R1"]["consumed"]["jobs"] == 1
        assert before.runs["R1"]["closed"] == "boundary"
    if close_commit:
        events = ledger.read(root / cfg.ledger)
        paths = {e.data["path"] for e in events if e.ev == ledger.Ev.artifact and e.data["scope"] == "M001"}
        assert any(p.endswith("_result.json") for p in paths)
        assert any(p.endswith("_source.md") for p in paths)
        assert any(p.endswith("_support.json") for p in paths)
        for path in paths:
            assert test_loop.git(root, "show", "HEAD:" + path).stdout == (root / path).read_bytes()
        assert len(commit.status(root, events, model)) == 1


@pytest.mark.parametrize("profile", [False, True])
def test_blocked_then_external_resolution_starts_new_cycle(project, monkeypatch, profile):
    root, cfg, model, write = materialize(project, profile)
    calls = []
    checkpoints.adapter(monkeypatch, calls, [report("blocked"), report()])
    state = step(runner(root, cfg, model))
    assert state.holistic["M001"]["step"] == "blocked"
    assert state.slices["M001-S01"].step == "accepted"
    saved = (root / cfg.ledger).read_bytes()
    with pytest.raises(loop._Stop):
        step(runner(root, cfg, model))
    assert (root / cfg.ledger).read_bytes() == saved
    assert len(calls) == 1
    authority = write("05_governance/resolved.md", b"Owned external authority\n")
    append(root, cfg, dict(_attempts.event("resolved", scope="M001", reason="Owned authority",
                                           authority=[authority]), by="architect"))
    state = step(runner(root, cfg, model))
    assert len(calls) == 2
    assert len({a["start"]["contract"] for a in state.attempts.values()}) == 2
    assert state.milestones_done == {"M001"}


def test_needs_work_groups_findings_once(project, monkeypatch):
    root, cfg, model, write = materialize(project)
    rows = ("| M001-S01-H1 | P2 | open | First correction. |\n"
            "| M001-S01-H2 | P1 | open | Second correction. |")
    calls = []
    checkpoints.adapter(monkeypatch, calls, [report("needs_work", rows), report()])
    state = step(runner(root, cfg, model))
    current = state.slices["M001-S01"]
    assert current.step == "fix"
    assert current.round == 2
    assert current.open_findings == ("M001-S01-H1", "M001-S01-H2")
    assert current.reopened is True
    assert rows in (root / current.last_report).read_text("utf-8")
    assert not any(e.ev == ledger.Ev.reopened for e in ledger.read(root / cfg.ledger))
    assert len(calls) == 1
    # External corrected handoff with unchanged product still requires a new cycle.
    old = ledger.read(root / cfg.ledger)
    issued = next(e for e in old if e.ev == ledger.Ev.prompt)
    verified = next(e for e in old if e.ev == ledger.Ev.verified)
    frozen = ledger.read_reference(root, ledger._thaw(issued.data["envelope"]))
    envelope = write("05_governance/reviews/m001/corrected-envelope.json", dict(frozen, round=2))
    prompt = write("prompts/for_coding_agent/corrected.md", b"Correct the retained findings.\n")
    manifest = write("05_governance/reviews/m001/corrected-manifest.json",
        dict(schema="frutlups.manifest/2", slice="M001-S01", round=2, changed=[]))
    receipt = ledger.read_reference(root, dict(path=verified.data["receipt"], sha=verified.data["sha"]))
    receipt_ref = write("05_governance/reviews/m001/corrected-receipt.json", dict(receipt, round=2, manifest=manifest))
    source_sha = ledger.evidence_sha((root / current.last_report).read_bytes())
    updates = ("## Finding updates\n| source | sha | id | disposition | related |\n"
               "| --- | --- | --- | --- | --- |\n" + "\n".join(
                   f"| {current.last_report} | {source_sha} | {id} | closed_by_review | - |"
                   for id in current.open_findings) + "\n\n")
    review = write("05_governance/reviews/m001/corrected-review.md", checkpoints.report().replace(
        "round 1", "round 2").replace("## Closure Decision", updates + "## Closure Decision").encode())
    append(root, cfg,
        _attempts.event("prompt", slice="M001-S01", round=2, **prompt, envelope=envelope),
        _attempts.event("coded", slice="M001-S01", round=2, changed=[], manifest=manifest, result="implemented"),
        _attempts.event("verified", slice="M001-S01", round=2, receipt=receipt_ref["path"], sha=receipt_ref["sha"], ok=True),
        dict(_attempts.event("reviewed", slice="M001-S01", round=2, report=review["path"], sha=review["sha"],
                            verdict="pass", open=[]), by="human"))
    commit.approve(root, model, _attempts.event("accepted", slice="M001-S01", round=2), "Accept correction", False)
    state = step(runner(root, cfg, model))
    assert state.milestones_done == {"M001"}
    assert len(calls) == 2
    assert len({a["start"]["contract"] for a in state.attempts.values()}) == 2


@pytest.mark.parametrize("phase", ["finished", "checkpoint", "report", "decision", "intent", "commit"])
def test_fresh_holistic_recovery(project, monkeypatch, phase):
    root, cfg, model, _ = materialize(project)
    cfg = replace(cfg, commit_on_accept=True)
    calls = []
    checkpoints.adapter(monkeypatch, calls, [report()])
    original = ledger._append_review
    def interrupt(root, path, model, raw, *args, **kwargs):
        if phase == "finished" and raw["ev"] == "review_checkpoint":
            raise RuntimeError("owned interruption")
        result = original(root, path, model, raw, *args, **kwargs)
        if ((phase == "checkpoint" and raw["ev"] == "review_checkpoint")
                or (phase == "report" and raw["ev"] == "artifact" and raw["role"] == "holistic_report")
                or (phase == "decision" and raw["ev"] == "holistic_reviewed")):
            raise RuntimeError("owned interruption")
        return result
    with monkeypatch.context() as patch:
        patch.setattr(ledger, "_append_review", interrupt)
        if phase == "intent":
            patch.setattr(commit, "_recover", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("owned interruption")))
        if phase == "commit":
            git = commit.g._git
            def stop_after_commit(root, *args, **kwargs):
                result = git(root, *args, **kwargs)
                if args[0] == "commit":
                    raise RuntimeError("owned interruption")
                return result
            patch.setattr(commit.g, "_git", stop_after_commit)
        with pytest.raises(RuntimeError, match="owned interruption"):
            step(runner(root, cfg, model))
    assert len(calls) == 1
    code = '''
import sys
from pathlib import Path
from dataclasses import replace
sys.path.insert(0,sys.argv[2])
import conftest, test_milestones_v2 as m
from frutlups import ledger, roadmap, commit
from frutlups.seats import claude
r=Path(sys.argv[1]); cfg=replace(conftest.offline_config(),commit_on_accept=True)
model=roadmap.load(r/'roadmap.yaml')
def forbidden(*a,**k): raise AssertionError('duplicate seat')
claude.ClaudeSeat.run=forbidden
state=m.step(m.runner(r,cfg,model))
assert state.milestones_done=={'M001'}
assert len(state.attempts)==1
assert len(state.checkpoints)==1
events=ledger.read(r/cfg.ledger)
assert sum(e.ev==ledger.Ev.holistic_reviewed for e in events)==1
assert sum(e.ev==ledger.Ev.milestone_done for e in events)==1
assert len(commit.status(r,events,model))==1
'''
    result = subprocess.run([sys.executable, "-B", "-c", code, str(root), str(Path(__file__).parent)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("outputs", [[None, "malformed", report()], ["malformed", None, report()]])
def test_holistic_retry_chain(project, monkeypatch, outputs):
    root, cfg, model, _ = materialize(project, True)
    calls = []
    checkpoints.adapter(monkeypatch, calls, outputs)
    state = step(runner(root, cfg, model))
    assert len(calls) == 3
    assert sorted(a["start"]["retry"] for a in state.attempts.values()) == ["format", "initial", "transport"]
    assert len({a["start"]["contract"] for a in state.attempts.values()}) == 1
    assert state.runs["R1"]["consumed"]["jobs"] == 3
    assert "Format repair only" in calls[-1].prompt_path.read_text("utf-8")


@pytest.mark.parametrize("until", ["milestone", "roadmap"])
@pytest.mark.parametrize("profile", [False, True])
def test_two_active_milestones_close_before_later_work(project, monkeypatch, until, profile):
    if profile:
        root, cfg, model = multi_materialize(project)
    else:
        root, cfg, model, _ = materialize(project)
        # Manual plan extension preserves the unprofiled history and its hashes.
        data = yaml.safe_load((root / "roadmap.yaml").read_text("utf-8"))
        first = data["milestones"][0]
        for mid, status in (("M002", "active"), ("M003", "planned")):
            data["milestones"].append(dict(first, id=mid, status=status,
                slices=[dict(first["slices"][0], id=mid + "-S01")]))
        (root / "roadmap.yaml").write_text(yaml.safe_dump(data), encoding="utf-8", newline="\n")
        model = roadmap.load(root / "roadmap.yaml")
    calls = []
    checkpoints.adapter(monkeypatch, calls, [report()])
    obj = runner(root, cfg, model, until)
    dispatched = []
    monkeypatch.setattr(obj, "_slice", lambda item, *a: dispatched.append(item.id))
    if not profile:
        refusal(obj, calls, "M001: LedgerError: fresh dispatch requires an autonomy profile; owner must inspect")
        assert calls == []
        assert dispatched == []
        assert not (root / "local_state/frutlups/jobs").exists()
        return
    assert obj._iteration_owned() is None
    assert dispatched == []
    assert len(calls) == 1
    events = ledger.read(root / cfg.ledger)
    state = ledger.validate_history(root, events, model)
    assert state.milestones_done == {"M001"}
    assert state.runs["R1"]["closed"] == "boundary"
    assert sum(e.ev == ledger.Ev.milestone_done for e in events) == 1
    before = snapshot(root, cfg, calls, "replay")
    assert obj._iteration_owned() == loop.StopReason.boundary
    assert snapshot(root, cfg, calls, "replay") == before
    assert ledger.validate_history(root, ledger.read(root / cfg.ledger), model) == state
    assert dispatched == []
    assert len(calls) == 1
    # Even a candidate grant naming M003 cannot admit a planned milestone.
    closed_run = ledger._thaw(state.runs["R1"])
    planned_run = dict(closed_run, closed=None,
                       admission={**closed_run["admission"], "milestone": "M003", "slices": ["M003-S01"]})
    with pytest.raises(ValueError) as planned:
        _preflight._profile_gate(model, replace(state, runs={"R1": planned_run}), ())
    assert str(planned.value) == "new admission requires active milestone"
    assert snapshot(root, cfg, calls, "replay") == before
    test_loop.next_grant(root, cfg, model, model.milestones[1])
    later = runner(root, cfg, model, "roadmap")
    monkeypatch.setattr(later, "_slice", lambda item, *a: dispatched.append(item.id))
    assert later._iteration_owned() is None
    assert dispatched == ["M002-S01"]
    assert len(calls) == 1
    # An explicit reopen must not be hidden by filtering selection to R2.
    append(root, cfg, dict(_attempts.event("reopened", slice="M001-S01", round=2,
                                         reason="Owner requests exact correction"), by="human"))
    reopened = ledger.validate_history(root, ledger.read(root / cfg.ledger), model)
    assert _preflight._frontier(model, reopened)[0].id == "M001"
    with pytest.raises(ValueError) as caught:
        _preflight._profile_gate(model, reopened, ())
    assert str(caught.value) == "current lifecycle selection is outside admitted run"
    refusal(runner(root, cfg, model, "roadmap"), calls,
            "M001: LedgerError: milestone outside current grant; owner must inspect")
    assert dispatched == ["M002-S01"]
    assert len(calls) == 1


@pytest.mark.parametrize("body", [report("override"), report(rows="| M001-S99-H1 | P2 | open | Unknown. |", result="needs_work"),
    report(rows="| M001-S01-H1 | P2 | carried | Forbidden carry. |"),
    report(rows="| M001-S01-H1 | P3 | waived_by_human | No authority. |")])
def test_holistic_parser_refuses_unauthorized_results(project, body):
    root, cfg, model, _ = materialize(project)
    with pytest.raises(verdict.VerdictError):
        verdict.autonomous_holistic(body, model.milestones[0])


@pytest.mark.parametrize("outputs,count,settled,error,message", [
    ([None, None], 2, 2, ledger.LedgerError, "review transport retry allowance exhausted"),
    (["bad", "bad"], 2, 2, ledger.LedgerError, "review format retry allowance exhausted"),
    (["x" * (1024 * 1024 + 1)], 1, 0, loop._Stop,
     "scrubbed seat report exceeds 1 MiB; inspect retained streams"),
    ([report().replace("## Closure Decision", "## Finding updates\n\n## Closure Decision")] * 2,
     2, 2, ledger.LedgerError, "review format retry allowance exhausted"),
])
def test_holistic_failed_chain_never_redispatches(project, monkeypatch, outputs, count, settled, error, message):
    root, cfg, model, _ = materialize(project)
    calls = []
    checkpoints.adapter(monkeypatch, calls, outputs)
    with pytest.raises(error) as failed:
        step(runner(root, cfg, model))
    assert (failed.value.detail if error is loop._Stop else str(failed.value)) == message
    if error is loop._Stop:
        assert failed.value.reason == loop.StopReason.seat_output
    assert len(calls) == count
    events = ledger.read(root / cfg.ledger)
    if outputs[0] and "## Finding updates" in outputs[0]:
        assert "invalid Finding updates table" in calls[-1].prompt_path.read_text("utf-8")
    assert sum(e.ev == ledger.Ev.attempt_started for e in events) == count
    assert sum(e.ev == ledger.Ev.attempt_finished for e in events) == settled
    assert not any(e.ev == ledger.Ev.review_checkpoint for e in events)
    saved = (root / cfg.ledger).read_bytes()
    replay_error = ValueError if settled == 0 else error
    with pytest.raises(replay_error) as replay:
        step(runner(root, cfg, model))
    if settled == 0:
        assert str(replay.value) == "unresolved invocation prevents new work"
    else:
        assert (replay.value.detail if error is loop._Stop else str(replay.value)) == message
    assert len(calls) == count
    assert (root / cfg.ledger).read_bytes() == saved


def test_holistic_visibility_real_git_configured_destinations(project):
    root, cfg, model = project
    cfg = replace(cfg, reviews_dir="05_governance/reviews/custom [x]",
                  review_prompt_dir="prompts/for_review_agent/custom [x]")
    obj = runner(root, cfg, model)
    for relative in (f"{cfg.reviews_dir}/M001_support.json", f"{cfg.review_prompt_dir}/M001.md",
                     f"{cfg.reviews_dir}/job_result.json", f"{cfg.reviews_dir}/job_source.md"):
        obj._require_holistic_visibility(relative)
        assert not (root / relative).exists()


@pytest.mark.parametrize("suffix", ["_support.json", "_holistic.md", "_source.md", "_result.json"])
@pytest.mark.parametrize("failure", ["ignored", "command"])
def test_holistic_visibility_refuses_before_reservation(project, monkeypatch, suffix, failure):
    root, cfg, model, _ = materialize(project)
    cfg = replace(cfg, reviews_dir="05_governance/reviews/custom",
                  review_prompt_dir="prompts/for_review_agent/custom")
    if failure == "ignored":
        with (root / ".git/info/exclude").open("a", encoding="utf-8", newline="\n") as stream:
            stream.write("\n*" + suffix + "\n")
    original = loop.gitws._git
    queries = []
    def query(root, *args, **kwargs):
        if "check-ignore" in args:
            assert args[:-1] == ("--no-literal-pathspecs", "check-ignore", "--no-index", "-q", "--")
            assert kwargs == dict(executable=cfg.git, check=False)
            queries.append(args[-1])
            if failure == "command" and args[-1].endswith(suffix):
                return subprocess.CompletedProcess(args, 128, b"", b"owned command failure")
        return original(root, *args, **kwargs)
    monkeypatch.setattr(loop.gitws, "_git", query)
    calls = []
    checkpoints.adapter(monkeypatch, calls, [report()])
    message = ("holistic evidence must be Git-visible: ignored destination" if failure == "ignored"
               else "holistic visibility query failed (exit 128)")
    with pytest.raises(ledger.LedgerError) as refused:
        step(runner(root, cfg, model))
    assert str(refused.value) == message
    assert queries[-1].endswith(suffix)
    assert queries[-1].startswith(cfg.review_prompt_dir if suffix == "_holistic.md" else cfg.reviews_dir)
    assert calls == []
    events = ledger.read(root / cfg.ledger)
    assert not any(e.ev == ledger.Ev.attempt_started for e in events)
    assert not any(e.ev == ledger.Ev.attempt_finished for e in events)
    assert not any(e.ev == ledger.Ev.review_checkpoint for e in events)


@pytest.mark.parametrize("drift", ["index", "head"])
def test_completed_holistic_candidate_drift_refuses_reuse(project, monkeypatch, drift):
    root, cfg, model, write = materialize(project)
    calls = []
    checkpoints.adapter(monkeypatch, calls, [report()])
    original = ledger._append_review
    def interrupt(root, path, model, raw, *args, **kwargs):
        if raw["ev"] == "review_checkpoint":
            raise RuntimeError("owned checkpoint interruption")
        return original(root, path, model, raw, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(ledger, "_append_review", interrupt)
        with pytest.raises(RuntimeError):
            step(runner(root, cfg, model))
    if drift == "index":
        test_loop.git(root, "add", cfg.ledger)
    elif drift == "head":
        test_loop.git(root, "commit", "--allow-empty", "-qm", "unexplained HEAD")
    obj = runner(root, cfg, model)
    saved = (root / cfg.ledger).read_bytes()
    with pytest.raises((ValueError, loop._Stop)):
        step(obj)
    assert len(calls) == 1
    assert (root / cfg.ledger).read_bytes() == saved


def test_completed_holistic_reversible_drift_group(project, monkeypatch):
    root, cfg, model, write = materialize(project)
    calls = []
    checkpoints.adapter(monkeypatch, calls, [report()])
    original_append = ledger._append_review
    def interrupt(root, path, model, raw, *args, **kwargs):
        if raw["ev"] == "review_checkpoint":
            raise RuntimeError("owned checkpoint interruption")
        return original_append(root, path, model, raw, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(ledger, "_append_review", interrupt)
        with pytest.raises(RuntimeError):
            step(runner(root, cfg, model))
    assert len(calls) == 1, "preparation: original holistic call"
    baseline = snapshot(root, cfg, calls, "preparation")
    exercised = []
    for drift in ("product", "runtime", "neighbor", "output"):
        assert snapshot(root, cfg, calls, drift) == baseline, drift + ": pre-variant drift"
        changed = None
        if drift == "product":
            changed, payload = root / "07_app/CONTEXT.md", b"unrecorded product\n"
        elif drift == "neighbor":
            changed, payload = root / "05_governance/reviews/m001/unbound-neighbor.md", b"unbound\n"
        elif drift == "output":
            finish = next(e for e in ledger.read(root / cfg.ledger) if e.ev == ledger.Ev.attempt_finished)
            changed, payload = root / finish.data["evidence"][1]["path"], b"tampered\n"
        original = changed.read_bytes() if changed is not None and changed.exists() else None
        if changed is not None:
            write(changed.relative_to(root).as_posix(), payload)
        obj = runner(root, cfg, model)
        if drift == "runtime":
            obj.runtime = replace(obj.runtime, identity={**obj.runtime.identity, "sha": "0" * 64})
        saved = (root / cfg.ledger).read_bytes()
        mutated = snapshot(root, cfg, calls, drift)
        try:
            step(obj)
        except (ValueError, loop._Stop):
            pass
        else:
            pytest.fail(drift + ": real step did not refuse")
        assert len(calls) == 1, drift + ": redispatch"
        assert (root / cfg.ledger).read_bytes() == saved, drift + ": ledger appended"
        assert snapshot(root, cfg, calls, drift) == mutated, drift + ": refusal changed state"
        if changed is not None:
            restore_file(root, changed, original)
        del obj
        assert snapshot(root, cfg, calls, drift) == baseline, drift + ": restoration mismatch"
        exercised.append(drift)
    assert exercised == ["product", "runtime", "neighbor", "output"], "holistic coverage"


def test_explicit_owner_reopen_keeps_priority_and_source(project, monkeypatch):
    owner_reopen(project, monkeypatch, False)


def test_q047_granted_owner_reopen_keeps_priority_and_source(project, monkeypatch):
    owner_reopen(project, monkeypatch, True)


def owner_reopen(project, monkeypatch, profile):
    root, cfg, model, _ = materialize(project, profile)
    calls = []
    checkpoints.adapter(monkeypatch, calls, [report()])
    closed = step(runner(root, cfg, model))
    original_report = closed.holistic["M001"]["event"]["report"]
    prefix = (root / cfg.ledger).read_bytes()
    append(root, cfg, dict(_attempts.event("reopened", slice="M001-S01", round=2,
                                         reason="Owner requests exact correction"), by="human"))
    state = ledger.validate_history(root, ledger.read(root / cfg.ledger), model)
    selected = roadmap.next_slice(model, state)
    assert selected.id == "M001-S01"
    assert roadmap.next_milestone(model, state).id == "M001"
    assert state.slices[selected.id].reopen_reason == "Owner requests exact correction"
    assert state.slices[selected.id].reopen_report == original_report
    if profile:
        assert state.runs["R1"]["closed"] == "boundary"
        test_loop.next_grant(root, cfg, model, model.milestones[0])
        granted = ledger.validate_history(root, ledger.read(root / cfg.ledger), model)
        assert granted.runs["R1"] == state.runs["R1"]
        assert granted.run_head["run"] == "R2"
    assert (root / cfg.ledger).read_bytes().startswith(prefix)
    obj = runner(root, cfg, model, "slice")
    dispatched = []
    monkeypatch.setattr(obj, "_slice", lambda item, current, events: dispatched.append(current))
    if not profile:
        refusal(obj, calls, "M001-S01: LedgerError: fresh dispatch requires an autonomy profile; owner must inspect")
        assert dispatched == []
        assert len(calls) == 1
        return
    assert obj._iteration_owned() is None
    assert dispatched[0].id == "M001-S01"
    assert dispatched[0].reopen_reason == "Owner requests exact correction"
    assert dispatched[0].reopen_report == original_report
    obj.accepted_this_run = True
    assert obj._iteration_owned() == loop.StopReason.boundary


@pytest.mark.parametrize("case,expected", [
    ("holistic", ("M001", None)),
    ("ordinary", ("M001", "M001-S01")),
    ("reopened", ("M002", "M002-S01")),
    ("completed", ("M002", "M002-S01")),
    ("inactive", (None, None)),
])
def test_q048_frontier_table(project, case, expected):
    _, _, model = project
    first = replace(model.milestones[0], holistic_review=True)
    second = replace(first, id="M002",
                     slices=(replace(first.slices[0], id="M002-S01", milestone_id="M002"),))
    if case == "inactive":
        first, second = replace(first, status="planned"), replace(second, status="planned")
    model = replace(model, milestones=(first, second))
    state = SimpleNamespace(slices={
        "M001-S01": SimpleNamespace(step="unstarted" if case == "ordinary" else "accepted", reopened=False),
        "M002-S01": SimpleNamespace(step="fix" if case == "reopened" else "unstarted", reopened=case == "reopened"),
    }, milestones_done={"M001"} if case == "completed" else set())
    milestone, item = _preflight._frontier(model, state)
    assert (milestone.id if milestone else None) == expected[0]
    assert (item.id if item else None) == expected[1]


def test_q047_inconsistent_in_memory_topology_refuses(project, monkeypatch):
    root, cfg, model, _ = materialize(project, True)
    model = multi_model(model)
    calls = []
    checkpoints.adapter(monkeypatch, calls, [report()])
    refusal(runner(root, cfg, model), calls,
            "M001: LedgerError: admission roadmap differs from canonical authority; owner must inspect")
    assert calls == []
    assert not (root / "local_state/frutlups/jobs").exists()


def test_q048_pending_holistic_ignores_later_frozen_runtime(project, monkeypatch):
    root, cfg, model = multi_materialize(project)
    events = ledger.read(root / cfg.ledger)
    state = ledger.validate_history(root, events, model)
    item = model.milestones[1].slices[0]
    # A new manual issuance, not a rewrite of any recorded contract or hash.
    frozen = _preflight.render.coding_envelope(root, model, item, state.slices[item.id], events,
                                              executable=cfg.git)
    frozen["runtime"] = {"python": "3.99"}
    refs = []
    for path, raw in (("05_governance/reviews/m002/later-envelope.json", json.dumps(frozen).encode()),
                      ("prompts/for_coding_agent/later.md", b"Later milestone, separate authority required.\n")):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        refs.append(dict(path=path, sha=ledger.evidence_sha(raw)))
    append(root, cfg, dict(_attempts.event("prompt", slice=item.id, round=1,
                                         **refs[1], envelope=refs[0]), by="architect"))
    state = ledger.validate_history(root, ledger.read(root / cfg.ledger), model)
    assert ledger.envelope(root, dict(state.slices[item.id].envelope), item.id, 1)["runtime"] == {"python": "3.99"}
    with pytest.raises(ValueError, match="3.99") as unavailable:
        _preflight.receipt.native_runtime(root, model, frozen, cfg)
    expected = _preflight.runtime.resolve_runtime(root, model, git=cfg.git)
    before = gitws.workspace_witness(root, executable=cfg.git)
    selected, probes, errors = _preflight._static(root, cfg, model)
    assert errors == ()
    assert probes == ["holistic"]
    assert selected == expected
    assert gitws.workspace_witness(root, executable=cfg.git) == before
    checked, calls = [], []
    original = _preflight._checker

    def observe(root, config, selected, **kwargs):
        assert selected.executable == str(Path(expected.executable).resolve())
        assert dict(selected.identity) == dict(expected.identity)
        original(root, config, selected, **kwargs)
        checked.append(selected)

    monkeypatch.setattr(_preflight, "_checker", observe)
    checkpoints.adapter(monkeypatch, calls, [report()])
    obj = runner(root, cfg, model)
    assert obj._iteration_owned() is None
    assert len(checked) == 1
    assert len(calls) == 1
    after = ledger.validate_history(root, ledger.read(root / cfg.ledger), model)
    assert after.milestones_done == {"M001"}
    assert after.slices[item.id] == state.slices[item.id]
    assert after.runs["R1"]["closed"] == "boundary"
    saved = snapshot(root, cfg, calls, "replay")
    assert obj._iteration_owned() == loop.StopReason.boundary
    assert snapshot(root, cfg, calls, "replay") == saved
    assert len(checked) == 1
    # Once M002 is actually granted, its frozen runtime must be enforced.
    test_loop.next_grant(root, cfg, model, model.milestones[1])
    current = runner(root, cfg, model, "roadmap")
    refusal(current, calls,
            f"M002-S01: {type(unavailable.value).__name__}: {unavailable.value}; owner must inspect")
    assert len(checked) == 1
    assert len(calls) == 1


def test_q048_current_holistic_runtime_drift_refuses(project, monkeypatch):
    root, cfg, model, _ = materialize(project, True)
    # Complete the existing review projection before changing the runtime witness.
    with _preflight.writer_lock(root, model, executable=cfg.git):
        _preflight.findings.reconcile(root, ledger.read(root / cfg.ledger))
    calls = []
    checkpoints.adapter(monkeypatch, calls, [report()])
    obj = runner(root, cfg, model)
    obj.runtime = replace(obj.runtime, identity={**obj.runtime.identity, "sha": "0" * 64})
    refusal(obj, calls, "M001: LedgerError: selected holistic runtime drift; owner must inspect")
    assert calls == []
    assert not (root / "local_state/frutlups/jobs").exists()


def test_q048_profile_gate_retains_other_refusals(project):
    root, cfg, model, _ = materialize(project, True)
    state = ledger.validate_history(root, ledger.read(root / cfg.ledger), model)
    run = ledger._thaw(state.runs["R1"])
    cases = [
        (replace(state, run_head=None), "profile admission requires a current run grant"),
        (replace(state, runs={"R1": dict(run, closed="boundary")}), "run is closed"),
        (replace(state, runs={"R1": dict(run, remaining={**run["remaining"], "jobs": 0})}), "run allowance exhausted"),
        (replace(state, runs={"R1": dict(run, remaining={**run["remaining"], "seconds": 0})}), "run allowance exhausted"),
        (replace(state, attempts={"unsettled": {"finish": None}}), "unresolved invocation prevents admission"),
    ]
    before = snapshot(root, cfg, [], "gate")
    for candidate, message in cases:
        with pytest.raises(ValueError) as caught:
            _preflight._profile_gate(model, candidate, ())
        assert str(caught.value) == message
        assert snapshot(root, cfg, [], "gate") == before
    _preflight._profile_gate(model, state, ())
