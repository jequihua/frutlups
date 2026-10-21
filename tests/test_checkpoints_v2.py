"""Durable routed slice reviews through the real private /2 loop boundaries."""
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
import test_loop
import test_attempts_v2 as attempts
import test_commit_v2
from _restoration import restore_file, snapshot
from frutlups import commit, gitws
from frutlups import _attempts, _loop_evidence as evidence, ledger, loop, roadmap, verdict, receipt, runtime
from frutlups.seats import FailureClass, JobResult, claude

project = test_loop.project


def materialize(project, profile=True, seats=2):
    root, cfg, model, rows, write = attempts.materialize(project, "reviewer", seconds=300)
    data = yaml.safe_load((root / "roadmap.yaml").read_text("utf-8"))
    if not profile:
        data.pop("autonomy")
        rows = rows[1:]
    for risk in data["review"]:
        data["review"][risk] = ["reviewer", "claude_reviewer"][:seats]
    (root / "roadmap.yaml").write_text(yaml.safe_dump(data), encoding="utf-8", newline="\n")
    model = roadmap.load(root / "roadmap.yaml")
    test_loop.git(root, "add", ".")
    test_loop.git(root, "commit", "-qm", "owned checkpoint qualification")
    attempts.save(root, cfg, rows)
    # Materialize a canonical successful receipt using the pinned producer's
    # snapshot semantics, without enabling the runner's /2 verification path.
    code = ("import sys,json;from pathlib import Path;sys.path.insert(0,sys.argv[2]);"
            "import _workspace,_integrity,ledger; r=Path(sys.argv[1]);s=_workspace.snapshot(r);"
            "events=ledger.read(r/'05_governance/ledger.jsonl');"
            "print(json.dumps(dict(head=s['head'],index=s['index'],stable=True,"
            "before=_workspace.snapshot_digest(s),after=_workspace.snapshot_digest(s),"
            "product=_integrity.product_digest(r,s,events))))")
    pinned = Path(__file__).parent / "fixtures/v2_project/scripts"
    result = subprocess.run([sys.executable, "-B", "-c", code, str(root), str(pinned)],
                            capture_output=True, text=True, timeout=30, check=True)
    witness = json.loads(result.stdout)
    verified = rows[-1]
    coded = next(r for r in rows if r["ev"] == "coded")
    frozen = ledger.envelope(root, next(r for r in rows if r["ev"] == "prompt")["envelope"], "M001-S01", 1)
    value = dict(schema="frutlups.receipt/2", slice="M001-S01", round=1, t=verified["t"],
                 base_commit=witness["head"], tree_dirty_before=True, tree_clean_after=False, ok=True,
                 manifest=coded["manifest"], witness=witness, runtime=dict(runtime.resolve_runtime(root, model).identity),
                 observation=frozen["observation"], commands=[dict(label="full",
                 argv=list(receipt._public_argv(frozen["full"], root)), exit=0, secs=.01,
                 stdout_tail="verified", stderr_tail="", timed_out=False)])
    verified["sha"] = write(verified["receipt"], value)["sha"]
    attempts.save(root, cfg, rows)
    return root, cfg, model, rows, write


def report(rows="", result="pass"):
    return ("# Review: M001-S01 round 1\n\n## Findings\n\n"
            "| id | severity | disposition | summary |\n| --- | --- | --- | --- |\n"
            + rows + "\n\n## Closure Decision\nObjective status: achieved\n"
            "Objective evidence: Owned evidence.\n\n## Verdict\n"
            f"Verdict: {result} - next: Inspect evidence.\n")


def adapter(monkeypatch, calls, outputs=None, mutate=None):
    def run(self, job, cfg):
        calls.append(job)
        if mutate:
            mutate(job, cfg)
        output = outputs[len(calls) - 1] if outputs else report()
        folder = job.cwd / "local_state/frutlups/jobs" / job.id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "original.txt").write_bytes((output or "").encode("utf-8", "surrogatepass"))
        return JobResult("completed" if output is not None else "failed",
                         None if output is not None else FailureClass.transport,
                         output, 0, .01, 7, 9, None, None, folder / "original.txt", folder / "stderr", None)
    monkeypatch.setattr(claude.ClaudeSeat, "run", run)


def step(obj):
    events = ledger.read(obj.root / obj.cfg.ledger)
    state = ledger.validate_history(obj.root, events, obj.roadmap)
    item = obj.roadmap.milestones[0].slices[0]
    obj._slice(item, state.slices[item.id], events)
    return ledger.validate_history(obj.root, ledger.read(obj.root / obj.cfg.ledger), obj.roadmap)


@pytest.mark.parametrize("profile,seats", [(False, 1), (False, 2), (True, 1), (True, 2)])
def test_sequential_sources_and_exact_usage(project, monkeypatch, profile, seats):
    root, cfg, model, _, _ = materialize(project, profile, seats)
    calls = []
    adapter(monkeypatch, calls)
    state = step(attempts.internal_loop(root, cfg, model))
    assert len(calls) == seats
    assert len(state.attempts) == seats
    assert len(state.checkpoints) == seats
    assert state.slices["M001-S01"].step == "accept_pending"
    assert sum(a["finish"]["usage"]["tokens_in"] for a in state.attempts.values()) == 7 * seats
    assert sum(e.ev == ledger.Ev.reviewed for e in ledger.read(root / cfg.ledger)) == 1
    if profile:
        assert state.runs["R1"]["consumed"]["jobs"] == seats
    else:
        assert not state.runs
        code = ("import sys,yaml;from pathlib import Path;sys.path.insert(0,sys.argv[2]);import ledger;"
                "r=Path(sys.argv[1]);s=ledger.fold(ledger.read(r/'05_governance/ledger.jsonl'),"
                "yaml.safe_load((r/'roadmap.yaml').read_text()));"
                "assert s['slices']['M001-S01']['step']=='accept_pending';"
                "assert len(s['checkpoints'])==int(sys.argv[3])")
        pinned = Path(__file__).parent / "fixtures/v2_project/scripts"
        result = subprocess.run([sys.executable, "-B", "-c", code, str(root), str(pinned), str(seats)],
                                capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr


def test_format_and_transport_chain_is_durable(project, monkeypatch):
    root, cfg, model, _, _ = materialize(project, seats=1)
    calls = []
    adapter(monkeypatch, calls, [None, "malformed original", report()])
    state = step(attempts.internal_loop(root, cfg, model))
    assert [a["start"]["retry"] for a in state.attempts.values()] == ["initial", "transport", "format"]
    assert len({a["start"]["contract"] for a in state.attempts.values()}) == 1
    prompt = calls[-1].prompt_path.read_text("utf-8")
    assert "Format repair only" in prompt and "Do not repeat the review" in prompt
    assert calls[1].id + "_source.md" in prompt
    assert len(state.checkpoints) == 1


@pytest.mark.parametrize("phase", ["checkpoint", "finish", "merged", "report_saved", "proof_saved", "repair"])
def test_fresh_interpreter_restart_without_replay(project, monkeypatch, phase):
    root, cfg, model, _, _ = materialize(project)
    calls = []
    adapter(monkeypatch, calls, ["malformed", report()] if phase == "repair" else None)
    append = ledger._append_review
    def interrupt(root, path, model, raw, *args, **kwargs):
        if (raw["ev"] == "artifact" and
                ((phase == "report_saved" and raw["path"].endswith("/review.md"))
                 or (phase == "proof_saved" and raw["path"].endswith("/merge.json")))):
            raise OSError("owned interruption")
        if phase == "merged" and raw["ev"] == "reviewed":
            raise OSError("owned interruption")
        if phase in ("finish", "repair") and raw["ev"] == "review_checkpoint":
            raise OSError("owned interruption")
        result = append(root, path, model, raw, *args, **kwargs)
        if phase == "checkpoint" and raw["ev"] == "review_checkpoint":
            raise OSError("owned interruption")
        return result
    monkeypatch.setattr(ledger, "_append_review", interrupt)
    with pytest.raises(OSError, match="owned interruption"):
        step(attempts.internal_loop(root, cfg, model))
    before = len(calls)
    code = '''
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import conftest, test_attempts_v2 as a, test_checkpoints_v2 as c
from frutlups import roadmap, ledger
from frutlups.seats import claude, JobResult
r=Path(sys.argv[1]); cfg=conftest.offline_config(); m=roadmap.load(r/'roadmap.yaml'); calls=[]
def run(self,j,cfg):
    calls.append(j.id)
    return JobResult('completed',None,c.report(),0,.01,7,9,None,None,r/'local_state/out',r/'local_state/err',None)
claude.ClaudeSeat.run=run
s=c.step(a.internal_loop(r,cfg,m))
assert len(s.checkpoints)==2
assert sum(x['finish']['usage']['tokens_in'] for x in s.attempts.values())==7*len(s.attempts)
assert sum(e.ev==ledger.Ev.reviewed for e in ledger.read(r/cfg.ledger))==1
print(len(calls))
'''
    result = subprocess.run([sys.executable, "-B", "-c", code, str(root), str(Path(__file__).parent)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert int(result.stdout.strip()) == (0 if phase in ("merged", "report_saved", "proof_saved") else 1)
    assert before == (2 if phase in ("repair", "merged", "report_saved", "proof_saved") else 1)


@pytest.mark.parametrize("profile", [False, True])
def test_routed_review_commit_and_replay(project, monkeypatch, profile):
    root, cfg, model, _, _ = materialize(project, profile)
    cfg = replace(cfg, commit_on_accept=True, reviews_dir="05_governance/reviews/custom")
    calls = []
    adapter(monkeypatch, calls)
    before = step(attempts.internal_loop(root, cfg, model))
    events = ledger.read(root / cfg.ledger)
    reviewed = events[-1]
    assert reviewed.ev == ledger.Ev.reviewed
    proof = events[-2].data["path"]
    paths = [reviewed.data["report"], proof, *(c["report"] for c in before.checkpoints.values())]
    assert len(paths) == 4
    assert all(p.startswith(cfg.reviews_dir + "/m001/") for p in paths)
    original = {p: (root / p).read_bytes() for p in paths}
    for path in paths:
        ignored = subprocess.run([cfg.git, "check-ignore", "--no-index", path], cwd=root,
                                 capture_output=True, timeout=30)
        assert ignored.returncode == 1, ignored.stderr
    parent = gitws.head(root)
    result = test_commit_v2.approve(root, cfg, model)
    assert len(result) == 1
    assert result[0]["state"] == "completed"
    assert result[0]["scope"] == "M001-S01"
    head = gitws.head(root)
    assert head != parent
    assert test_loop.git(root, "rev-parse", "HEAD^").stdout.decode().strip() == parent
    for path, body in original.items():
        assert test_loop.git(root, "show", "HEAD:" + path).stdout == body
    saved = (root / cfg.ledger).read_bytes()
    assert test_commit_v2.approve(root, cfg, model) == result
    assert commit.recover(root, model) == result
    assert (root / cfg.ledger).read_bytes() == saved
    assert gitws.head(root) == head
    events = ledger.read(root / cfg.ledger)
    assert sum(e.ev == ledger.Ev.accepted for e in events) == 1
    assert sum(e.ev == ledger.Ev.reviewed for e in events) == 1
    after = ledger.validate_history(root, events, model)
    assert after.attempts == before.attempts
    assert after.checkpoints == before.checkpoints
    assert len(calls) == 2
    if profile:
        assert after.runs["R1"]["consumed"] == before.runs["R1"]["consumed"]


@pytest.mark.parametrize("drift", ["index", "head"])
def test_interrupted_merge_refuses_drift_without_adoption(project, monkeypatch, drift):
    root, cfg, model, _, _ = materialize(project)
    calls = []
    adapter(monkeypatch, calls)
    append = ledger._append_review
    saved_report = []
    def interrupt(root, path, model, raw, *args, **kwargs):
        if raw["ev"] == "artifact" and raw["path"].endswith("/review.md"):
            saved_report.append(root / raw["path"])
            raise OSError("owned interruption")
        return append(root, path, model, raw, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(ledger, "_append_review", interrupt)
        with pytest.raises(OSError, match="owned interruption"):
            step(attempts.internal_loop(root, cfg, model))
    assert len(calls) == 2
    target = saved_report[0]
    if drift == "index": test_loop.git(root, "add", cfg.ledger)
    if drift == "head": test_loop.git(root, "commit", "--allow-empty", "-qm", "owned drift")
    obj = attempts.internal_loop(root, cfg, model)
    prefix = (root / cfg.ledger).read_bytes()
    with pytest.raises((ValueError, loop._Stop)):
        step(obj)
    assert (root / cfg.ledger).read_bytes() == prefix
    assert target.exists()
    assert len(calls) == 2


def test_interrupted_merge_reversible_drift_group(project, monkeypatch):
    root, cfg, model, _, _ = materialize(project)
    calls = []
    adapter(monkeypatch, calls)
    append = ledger._append_review
    saved_report = []
    def interrupt(root, path, model, raw, *args, **kwargs):
        if raw["ev"] == "artifact" and raw["path"].endswith("/review.md"):
            saved_report.append(root / raw["path"])
            raise OSError("owned interruption")
        return append(root, path, model, raw, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(ledger, "_append_review", interrupt)
        with pytest.raises(OSError, match="owned interruption"):
            step(attempts.internal_loop(root, cfg, model))
    assert len(calls) == 2, "preparation: original two routed calls"
    target = saved_report[0]
    baseline = snapshot(root, cfg, calls, "preparation")
    exercised = []
    for drift in ("report", "neighbor", "product", "runtime"):
        assert snapshot(root, cfg, calls, drift) == baseline, drift + ": pre-variant drift"
        changed = {"report": target, "neighbor": target.parent / "unbound.txt",
                   "product": root / "07_app/drift.txt"}.get(drift)
        original = changed.read_bytes() if changed is not None and changed.exists() else None
        if changed is not None:
            changed.write_bytes({"report": b"tampered", "neighbor": b"unbound", "product": b"drift"}[drift])
        obj = attempts.internal_loop(root, cfg, model)
        if drift == "runtime":
            obj.runtime = type("ChangedRuntime", (), {"identity": {"changed": True}})()
        prefix = (root / cfg.ledger).read_bytes()
        mutated = snapshot(root, cfg, calls, drift)
        try:
            step(obj)
        except (ValueError, loop._Stop):
            pass
        else:
            pytest.fail(drift + ": real step did not refuse")
        assert (root / cfg.ledger).read_bytes() == prefix, drift + ": ledger appended"
        assert target.exists(), drift + ": lost retained report"
        assert len(calls) == 2, drift + ": redispatch"
        assert snapshot(root, cfg, calls, drift) == mutated, drift + ": refusal changed state"
        if changed is not None:
            restore_file(root, changed, original)
        del obj
        assert snapshot(root, cfg, calls, drift) == baseline, drift + ": restoration mismatch"
        exercised.append(drift)
    assert exercised == ["report", "neighbor", "product", "runtime"], "routed coverage"


@pytest.mark.parametrize("drift", ["product", "index", "head", "ledger", "evidence"])
def test_readonly_drift_keeps_unfinished_reservation(project, monkeypatch, drift):
    root, cfg, model, _, _ = materialize(project)
    def mutate(job, cfg):
        if drift == "product": (root / "07_app/drift.txt").write_bytes(b"drift")
        if drift == "index": test_loop.git(root, "add", cfg.ledger)
        if drift == "head": test_loop.git(root, "commit", "--allow-empty", "-qm", "owned bad seat")
        if drift == "ledger": (root / cfg.ledger).write_bytes(b"broken\n")
        if drift == "evidence": (root / "prompt.md").write_bytes(b"drift")
    calls = []
    adapter(monkeypatch, calls, mutate=mutate)
    with pytest.raises((ValueError, loop._Stop)):
        step(attempts.internal_loop(root, cfg, model))
    assert len(calls) == 1
    with pytest.raises((ValueError, loop._Stop)):
        step(attempts.internal_loop(root, cfg, model))
    assert len(calls) == 1


@pytest.mark.parametrize("outputs", [[None, None], ["broken", "still broken"],
    [report().replace("round 1", "round 2")] * 2,
    [report("| X | P3 | waived_by_human | no |")]*2,
    [report().replace("## Closure Decision", "## Finding updates\n\n## Closure Decision")] * 2,
    ["x" * (1024 * 1024 + 1)]])
def test_invalid_output_and_exhaustion_never_restart(project, monkeypatch, outputs):
    root, cfg, model, _, _ = materialize(project, seats=1)
    calls = []
    adapter(monkeypatch, calls, outputs)
    for _ in range(2):
        with pytest.raises((ValueError, loop._Stop)):
            step(attempts.internal_loop(root, cfg, model))
    assert len(calls) == len(outputs)
    assert not attempts.state(root, cfg, model).checkpoints
    if outputs[0] and "## Finding updates" in outputs[0]:
        assert "invalid Finding updates table" in calls[-1].prompt_path.read_text("utf-8")


def test_each_contract_component_is_independent(project, monkeypatch):
    from frutlups.seats import Role, pi
    from frutlups import gitws
    root, cfg, model, _, _ = materialize(project)
    obj = attempts.internal_loop(root, cfg, model)
    events = ledger.read(root / cfg.ledger)
    state = ledger.validate_history(root, events, model)
    job = obj._job("reviewer", Role.reviewer, "prompt.md")
    raw = (root / "prompt.md").read_bytes()
    runtime = dict(obj.runtime.identity)
    def binding(config=cfg, selected=model, current_job=job, prompt=raw, identity=runtime, history=events):
        return _attempts._binding(root, config, selected, history, state, "M001-S01", 1,
                                 "reviewer", current_job, prompt, identity)
    original = binding()
    assert binding(prompt=raw+b"changed") != original
    assert binding(identity={**runtime, "sha": "1"*64}) != original
    assert binding(selected=replace(model, review_routing={k: tuple(reversed(v))
                    for k, v in model.review_routing.items()})) != original
    for field, value in (("model", "other"), ("effort", "low"), ("provider", "other")):
        config = replace(cfg, seats={**cfg.seats, "reviewer": replace(cfg.seats["reviewer"], **{field:value})})
        assert binding(config=config, current_job=replace(job, **{field:value})) != original, field
    config = replace(cfg, seats={**cfg.seats, "reviewer": replace(cfg.seats["reviewer"], adapter="pi")})
    assert binding(config=config, current_job=replace(job, adapter="pi", tools=pi.tools_for(Role.reviewer))) != original
    assert binding(current_job=replace(job, seat_name="claude_reviewer")) != original
    with pytest.raises(ValueError, match="tools"):
        binding(current_job=replace(job, tools=()))
    parser = root / "local_state/parser.py"
    parser.write_bytes(b"owned distinct parser")
    with monkeypatch.context() as patch:
        patch.setattr(claude, "__file__", str(parser))
        assert binding() != original
    witness = gitws.workspace_witness(root, executable=cfg.git)
    for key, value in (("head", "1"*40), ("index", ()), ("files", {})):
        with monkeypatch.context() as patch:
            patch.setattr(gitws, "workspace_witness", lambda *a, **k: {**witness, key:value})
            assert binding() != original, key
    for field, value in (("envelope", {"path":"changed.json", "sha":"1"*64}),
                         ("baseline", [{"path":"owned.txt", "kind":"added", "sha":"1"*64}])):
        rows = [ledger._event_dict(e) for e in events]
        next(r for r in rows if r["ev"] == "prompt")[field] = value
        assert binding(history=tuple(ledger._parse_event(r) for r in rows)) != original, field


def test_completed_source_references_and_pending_finish_refuse(project, monkeypatch):
    root, cfg, model, _, _ = materialize(project)
    calls = []
    adapter(monkeypatch, calls)
    append = ledger._append_review
    def interrupt(root, path, model, raw, *args, **kwargs):
        if raw["ev"] == "review_checkpoint":
            raise OSError("owned checkpoint interruption")
        return append(root, path, model, raw, *args, **kwargs)
    monkeypatch.setattr(ledger, "_append_review", interrupt)
    with pytest.raises(OSError):
        step(attempts.internal_loop(root, cfg, model))
    monkeypatch.setattr(ledger, "_append_review", append)
    state = attempts.state(root, cfg, model)
    finish = next(iter(state.attempts.values()))["finish"]
    for ref in finish["evidence"]:
        path = root / ref["path"]
        original = path.read_bytes()
        for missing in (False, True):
            if missing: path.unlink()
            else: path.write_bytes(b"tampered")
            with pytest.raises(ValueError):
                step(attempts.internal_loop(root, cfg, model))
            path.write_bytes(original)
    assert len(calls) == 1
    # A valid completed call is required: remove only the fixture's finish to
    # materialize the actual crash window after result persistence, before settlement.
    rows = [ledger._event_dict(e) for e in ledger.read(root / cfg.ledger)]
    assert rows[-1]["ev"] == "attempt_finished"
    attempts.save(root, cfg, rows[:-1])
    with pytest.raises(ValueError, match="unresolved"):
        step(attempts.internal_loop(root, cfg, model))
    assert len(calls) == 1


def test_changed_model_requires_new_review_preserving_prior_checkpoint(project, monkeypatch):
    root, cfg, model, _, _ = materialize(project)
    calls = []
    adapter(monkeypatch, calls)
    append = ledger._append_review
    def interrupt(*args, **kwargs):
        result = append(*args, **kwargs)
        if args[3]["ev"] == "review_checkpoint":
            raise OSError("owned checkpoint interruption")
        return result
    monkeypatch.setattr(ledger, "_append_review", interrupt)
    with pytest.raises(OSError):
        step(attempts.internal_loop(root, cfg, model))
    old = next(iter(attempts.state(root, cfg, model).checkpoints))
    monkeypatch.setattr(ledger, "_append_review", append)
    cfg = replace(cfg, seats={**cfg.seats, "reviewer": replace(cfg.seats["reviewer"], model="changed")})
    state = step(attempts.internal_loop(root, cfg, model))
    assert len(calls) == len(state.checkpoints) == 3
    assert old in state.checkpoints
    assert state.runs["R1"]["consumed"]["jobs"] == 3


def test_frozen_custom_prompt_and_whole_bound_before_writes(project, monkeypatch):
    from frutlups import render
    root, cfg, model, rows, write = materialize(project)
    issued = next(r for r in rows if r["ev"] == "prompt")
    frozen = ledger.envelope(root, issued["envelope"], "M001-S01", 1)
    template = root / "prompts/templates/review_prompt.md"
    template.write_text("Custom {{acceptance}} {{allowed_prefixes}} {{notes}} {{finding_updates}}", encoding="utf-8")
    text = render.review_v2(root, frozen, {}, "report.md")
    assert text.startswith("Custom") and json.dumps(frozen, ensure_ascii=False, sort_keys=True) in text
    assert render.FINDING_UPDATES in text
    with pytest.raises(render.RenderError, match="unknown placeholders"):
        render.render_template(template, frozen)
    for field in ("acceptance", "read_first", "allowed_prefixes"):
        changed = {**frozen, field:["a" * (48*1024)]}
        with pytest.raises(render.RenderError, match="48 KiB"):
            render.review_v2(root, changed, {}, "report.md")


def test_colliding_findings_preserve_both_sources_and_strict_verdict(project, monkeypatch):
    root, cfg, model, _, _ = materialize(project)
    calls = []
    adapter(monkeypatch, calls, [report("| M001-S01-F1 | P3 | open | first |"),
                               report("| M001-S01-F1 | P2 | open | second |", "needs_work")])
    state = step(attempts.internal_loop(root, cfg, model))
    text = (root / state.slices["M001-S01"].last_report).read_text("utf-8")
    parsed = verdict.parse(text)
    assert parsed.verdict == "needs_work"
    assert [f.id for f in parsed.findings] == ["M001-S01-F1-reviewer", "M001-S01-F1-claude_reviewer"]
    assert len(state.checkpoints) == 2


def test_receipt_contradictions_and_product_drift_precede_first_dispatch(project, monkeypatch):
    root, cfg, model, rows, write = materialize(project)
    calls = []
    adapter(monkeypatch, calls)
    verified = rows[-1]
    original = json.loads((root / verified["receipt"]).read_bytes())
    for field, value in (("base_commit", "1"*40), ("ok", False), ("runtime", {}), ("commands", []),
                         ("witness", {**original["witness"], "product":"1"*64}),
                         ("witness", {**original["witness"], "index":"1"*64})):
        verified["sha"] = write(verified["receipt"], {**original, field:value})["sha"]
        attempts.save(root, cfg, rows)
        with pytest.raises(ValueError):
            step(attempts.internal_loop(root, cfg, model))
    verified["sha"] = write(verified["receipt"], original)["sha"]
    attempts.save(root, cfg, rows)
    target = root / "README.md"
    before = target.read_bytes()
    target.write_bytes(before.replace(b"\n", b"\r\n"))
    with pytest.raises((ValueError, loop._Stop)):
        step(attempts.internal_loop(root, cfg, model))
    assert not calls


def test_artifact_collision_and_writer_authority_refuse(project, monkeypatch):
    root, cfg, model, _, _ = materialize(project)
    path = root / cfg.ledger
    prefix = path.read_bytes()
    for ev in ("run_granted", "resolved", "accepted", "milestone_done", "coded"):
        with pytest.raises(ValueError):
            ledger._append_review(root, path, model, _attempts.event(ev), prefix, executable=cfg.git)
    with pytest.raises(ValueError, match="prefix"):
        ledger._append_review(root, path, model, _attempts.event("artifact", scope="M001-S01", round=1,
                              role="evidence", path="x", sha="1"*64), b"stale", executable=cfg.git)
    assert path.read_bytes() == prefix
    calls = []
    adapter(monkeypatch, calls)
    original = evidence.immutable_text
    def collision(root, path, text, limit):
        if path.endswith("_source.md"):
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"collision")
        return original(root, path, text, limit)
    monkeypatch.setattr(evidence, "immutable_text", collision)
    with pytest.raises(FileExistsError):
        step(attempts.internal_loop(root, cfg, model))
    with pytest.raises(ValueError, match="unresolved"):
        step(attempts.internal_loop(root, cfg, model))
    assert len(calls) == 1


def test_format_transport_retry_preserves_repair_scope(project, monkeypatch):
    root, cfg, model, _, _ = materialize(project, seats=1)
    calls = []
    adapter(monkeypatch, calls, ["malformed", None, report()])
    state = step(attempts.internal_loop(root, cfg, model))
    assert [a["start"]["retry"] for a in state.attempts.values()] == ["initial", "format", "transport"]
    assert all("Format repair only" in job.prompt_path.read_text("utf-8") for job in calls[1:])
    assert len(state.checkpoints) == 1
