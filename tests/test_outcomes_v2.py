"""Typed outcomes at the real, privately qualified /2 coding boundary."""
import json
import re
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
import test_loop
import test_attempts_v2 as attempts
from frutlups import _attempts, _loop_evidence as evidence, ledger, loop, roadmap, render
from frutlups.seats import FailureClass, JobResult, pi

project = test_loop.project


def materialize(project, profile=True):
    root, cfg, model, rows, write = attempts.materialize(project, seconds=300)
    data = yaml.safe_load((root / "roadmap.yaml").read_text("utf-8"))
    if not profile:
        data.pop("autonomy")
        rows = rows[1:]
    (root / "roadmap.yaml").write_text(yaml.safe_dump(data), encoding="utf-8", newline="\n")
    model = roadmap.load(root / "roadmap.yaml")
    prompt = rows[-1]
    envelope = ledger.read_reference(root, prompt["envelope"])
    envelope.update(allowed_prefixes=["07_app/"], forbidden=["00_brief/", "05_governance/", "roadmap.yaml"])
    prompt["envelope"] = write(prompt["envelope"]["path"], envelope)
    test_loop.git(root, "add", ".")
    test_loop.git(root, "commit", "-qm", "owned outcome qualification")
    attempts.save(root, cfg, rows)
    return root, cfg, model, rows, write


def step(obj):
    events = ledger.read(obj.root / obj.cfg.ledger)
    state = ledger.validate_history(obj.root, events, obj.roadmap)
    item = obj.roadmap.milestones[0].slices[0]
    obj._slice(item, state.slices[item.id], events)
    return ledger.validate_history(obj.root, ledger.read(obj.root / obj.cfg.ledger), obj.roadmap)


def adapter(monkeypatch, calls, outcome="implemented", change=True, transform=None, mutate=None):
    def run(self, job, cfg):
        calls.append(job)
        text = job.prompt_path.read_text("utf-8")
        binding = json.loads(re.findall(r"```json\n(.*?)\n```", text, re.S)[-1])
        assert binding["invocation"] == job.id
        assert binding["slice"] == "M001-S01"
        if change:
            (job.cwd / "07_app/outcome.txt").write_bytes(b"meaningful retained work\n")
        value = dict(binding, outcome=outcome, requirement="Preserve work", reason="Observed result",
                     actor="architect" if outcome != "implemented" else "",
                     action="record authority" if outcome != "implemented" else "")
        if mutate:
            mutate(job.cwd, cfg, value)
        output = json.dumps(value)
        if transform:
            output = transform(output)
        folder = job.cwd / "local_state/frutlups/jobs" / job.id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "original.txt").write_bytes(output.encode("utf-8", "surrogatepass"))
        return JobResult("completed", None, output, 0, .01, 7, 9, None, None,
                         folder / "original.txt", folder / "stderr", None)
    monkeypatch.setattr(pi.PiSeat, "run", run)


@pytest.mark.parametrize("profile", [False, True])
@pytest.mark.parametrize("outcome,change,fenced", [
    ("implemented", True, False), ("implemented", False, False),
    ("blocked_authority", True, True), ("blocked_environment", True, False)])
def test_real_coding_outcomes_and_single_local_format_pass(project, monkeypatch, profile, outcome, change, fenced):
    root, cfg, model, rows, _ = materialize(project, profile)
    obj = attempts.internal_loop(root, cfg, model)
    calls = []
    adapter(monkeypatch, calls, outcome, change,
            (lambda text: " \n```json\n" + text + "\n```\n ") if fenced else None)
    state = step(obj)
    current = state.slices["M001-S01"]
    assert current.step == ("verifying" if outcome == "implemented" else "blocked")
    assert len(calls) == obj.jobs == 1
    events = ledger.read(root / cfg.ledger)
    coded = events[-1]
    assert coded.ev == ledger.Ev.coded
    assert coded.data["result"] == outcome
    saved = ledger.outcome(root, ledger._thaw(current.outcome), current.id, 1)
    assert saved["invocation"] == calls[0].id
    assert (root / current.notes_path).read_text("utf-8") == (root / "local_state/frutlups/jobs" / calls[0].id / "original.txt").read_text("utf-8")
    manifest = ledger.manifest(root, ledger._thaw(current.manifest))
    assert len(manifest["changed"]) == int(change)
    assert coded.data["tokens_in"] == 7
    assert coded.data["tokens_out"] == 9
    if profile:
        assert len(state.attempts) == 1
        assert state.attempts[calls[0].id]["finish"]["status"] == "completed"
        assert state.runs["R1"]["consumed"]["jobs"] == 1
    if outcome != "implemented":
        before = (root / cfg.ledger).read_bytes()
        with pytest.raises(loop._Stop, match="resolved authority"):
            step(attempts.internal_loop(root, cfg, model))
        assert len(calls) == 1
        assert (root / cfg.ledger).read_bytes() == before
        assert (root / "07_app/outcome.txt").read_bytes() == b"meaningful retained work\n"


@pytest.mark.parametrize("case", ["prose", "oversized", "two_fences", "duplicate", "wrong_invocation",
                                  "boolean_round", "extra", "bad_authority", "missing_evidence", "unicode"])
def test_invalid_outcome_preserves_edits_output_and_stops_replay(project, monkeypatch, case):
    root, cfg, model, _, _ = materialize(project)
    calls = []
    transforms = {
        "prose": lambda t: "Done! " + t,
        "oversized": lambda t: t + " " * 16384,
        "two_fences": lambda t: "```json\n" + t + "\n```\n```json\n{}\n```",
        "duplicate": lambda t: t[:-1] + ', "outcome":"implemented"}',
        "unicode": lambda t: t.replace("Observed result", "\ud800"),
    }
    def mutate(root, cfg, value):
        if case == "wrong_invocation": value["invocation"] = "invented"
        if case == "boolean_round": value["round"] = True
        if case == "extra": value["extra"] = "invented"
        if case == "bad_authority": value["authority"] = ["../authority.md"]
        if case == "missing_evidence": value["evidence"] = [{"path": "absent.md", "sha": "0" * 64}]
    adapter(monkeypatch, calls, transform=transforms.get(case), mutate=mutate)
    with pytest.raises((ValueError, loop._Stop, UnicodeError)):
        step(attempts.internal_loop(root, cfg, model))
    assert len(calls) == 1
    assert (root / "07_app/outcome.txt").exists()
    assert not any(e.ev == ledger.Ev.coded for e in ledger.read(root / cfg.ledger))
    with pytest.raises((ValueError, loop._Stop, FileExistsError)):
        step(attempts.internal_loop(root, cfg, model))
    assert len(calls) == 1
    assert (root / "local_state/frutlups/jobs" / calls[0].id / "original.txt").exists()


@pytest.mark.parametrize("outcome", ["implemented", "blocked_authority"])
@pytest.mark.parametrize("drift", ["forbidden", "rename", "index", "head", "ledger", "historical"])
def test_identical_drift_guards_precede_outcome_persistence(project, monkeypatch, outcome, drift):
    root, cfg, model, _, _ = materialize(project)
    def mutate(root, cfg, value):
        if drift == "forbidden": (root / "00_brief/decisions.md").write_bytes(b"forbidden\n")
        if drift == "rename": test_loop.git(root, "mv", "00_brief/decisions.md", "07_app/moved.md")
        if drift == "index": test_loop.git(root, "add", "07_app/outcome.txt")
        if drift == "head": test_loop.git(root, "commit", "--allow-empty", "-qm", "unauthorized child commit")
        if drift == "ledger": (root / cfg.ledger).write_bytes(b"corrupted\n")
        if drift == "historical": (root / "prompt.md").write_bytes(b"mutated evidence\n")
    calls = []
    adapter(monkeypatch, calls, outcome, mutate=mutate)
    with pytest.raises((ValueError, loop._Stop)):
        step(attempts.internal_loop(root, cfg, model))
    assert len(calls) == 1
    assert not (root / cfg.reviews_dir / "m001/M001-S01_r1_outcome.json").exists()


@pytest.mark.parametrize("phase", ["notes", "outcome", "manifest", "coded"])
def test_persistence_interruption_never_replays_writable_coder(project, monkeypatch, phase):
    root, cfg, model, _, _ = materialize(project, False)
    calls = []
    adapter(monkeypatch, calls, change=False)
    original = evidence.immutable_text
    suffix = {"notes": "_coder.md", "outcome": "_outcome.json", "manifest": "_manifest.json"}
    def interrupted(root, path, text, limit):
        result = original(root, path, text, limit)
        if phase in suffix and path.endswith(suffix[phase]):
            raise OSError("owned persistence interruption")
        return result
    monkeypatch.setattr(evidence, "immutable_text", interrupted)
    if phase == "coded":
        append = ledger._append_coding
        def fail_coded(root, path, model, raw, *args, **kwargs):
            if raw["ev"] == "coded":
                raise OSError("owned persistence interruption")
            return append(root, path, model, raw, *args, **kwargs)
        monkeypatch.setattr(ledger, "_append_coding", fail_coded)
    with pytest.raises(OSError, match="owned persistence"):
        step(attempts.internal_loop(root, cfg, model))
    with pytest.raises((ValueError, loop._Stop, FileExistsError)):
        step(attempts.internal_loop(root, cfg, model))
    assert len(calls) == 1
    assert not any(e.ev == ledger.Ev.coded for e in ledger.read(root / cfg.ledger))


def test_immutable_collision_retains_existing_bytes(project, monkeypatch):
    root, cfg, model, _, _ = materialize(project, False)
    calls = []
    adapter(monkeypatch, calls, change=False)
    original = evidence.immutable_text
    def collision(root, path, text, limit):
        if path.endswith("_outcome.json"):
            (root / path).write_bytes(b"retained uncertainty\n")
        return original(root, path, text, limit)
    monkeypatch.setattr(evidence, "immutable_text", collision)
    with pytest.raises(FileExistsError):
        step(attempts.internal_loop(root, cfg, model))
    assert (root / cfg.reviews_dir / "m001/M001-S01_r1_outcome.json").read_bytes() == b"retained uncertainty\n"
    assert len(calls) == 1


def fresh_step(root, compare=False):
    code = """import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import conftest, test_attempts_v2
from frutlups import ledger, loop, roadmap, _loop_evidence
root = Path(sys.argv[1])
cfg = conftest.offline_config()
model = roadmap.load(root / 'roadmap.yaml')
obj = test_attempts_v2.internal_loop(root, cfg, model)
def forbidden(*args, **kwargs):
    raise AssertionError('unexpected writable dispatch')
obj._call = forbidden
events = ledger.read(root / cfg.ledger)
state = ledger.validate_history(root, events, model)
if sys.argv[3] == 'compare':
    print('no_progress=' + str(_loop_evidence.no_progress(root, events, model, 'M001-S01')))
    sys.exit(0)
try:
    obj._slice(model.milestones[0].slices[0], state.slices['M001-S01'], events)
except loop._Stop as exc:
    print(exc.reason.value + ': ' + exc.detail)
"""
    return subprocess.run([sys.executable, "-B", "-c", code, str(root), str(Path(__file__).parent), "compare" if compare else "step"],
                          capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("profile", [False, True])
@pytest.mark.parametrize("remove", [False, True])
def test_blocked_fresh_replay_and_external_resolution_freezes_scope(project, monkeypatch, profile, remove):
    root, cfg, model, rows, write = materialize(project, profile)
    original_envelope = ledger.read_reference(root, rows[-1]["envelope"])
    calls = []
    adapter(monkeypatch, calls, "blocked_authority")
    blocked = step(attempts.internal_loop(root, cfg, model))
    before = (root / cfg.ledger).read_bytes()
    result = fresh_step(root)
    assert result.returncode == 0, result.stderr
    assert "resolved authority" in result.stdout
    assert (root / cfg.ledger).read_bytes() == before
    # The external actor, not the private runner writer, supplies authority.
    ref = write("questions/answered/owned-resolution.md", b"Architect permits the remaining work.\n")
    resolution = dict(_attempts.event("resolved", scope="M001-S01", reason="owned authority", authority=[ref]), by="architect")
    rows = [ledger._event_dict(e) for e in ledger.read(root / cfg.ledger)] + [resolution]
    attempts.save(root, cfg, rows)
    widened_item = replace(model.milestones[0].slices[0], acceptance=("unadmitted replacement",), allowed_prefixes=("",))
    widened = replace(model, milestones=(replace(model.milestones[0], slices=(widened_item,)),))
    obj = attempts.internal_loop(root, cfg, widened)
    issued = step(obj).slices["M001-S01"]
    envelope = ledger.envelope(root, ledger._thaw(issued.envelope), issued.id, 2)
    for key in ("acceptance", "read_first", "allowed_prefixes", "forbidden", "full", "focused", "runtime"):
        assert envelope[key] == original_envelope[key]
    assert ref["path"] in (root / issued.last_prompt).read_text("utf-8")
    adapter(monkeypatch, calls, "implemented", change=False,
            mutate=(lambda root, cfg, value: (root / "07_app/outcome.txt").unlink()) if remove else None)
    completed = step(obj)
    assert completed.slices["M001-S01"].step == "verifying"
    assert len(calls) == 2
    if remove:
        assert not (root / "07_app/outcome.txt").exists()
        manifest = ledger.manifest(root, ledger._thaw(completed.slices["M001-S01"].manifest))
        assert manifest["changed"][0]["kind"] == "deleted"
        assert manifest["changed"][0]["round"] == 2
    else:
        assert (root / "07_app/outcome.txt").read_bytes() == b"meaningful retained work\n"
    if profile:
        assert completed.runs["R1"]["consumed"]["jobs"] == blocked.runs["R1"]["consumed"]["jobs"] + 1


def history_of_failures(project, control):
    root, cfg, model, rows, write = materialize(project, False)
    envelope = ledger.read_reference(root, rows[0]["envelope"])
    # Complete, canonical receipts from a controlled failing command. No domain
    # interpretation; the runner consumes durable proofs at its real boundary.
    command_text = "from pathlib import Path; text=Path('local_state/failure.txt').read_text(); raise SystemExit(0 if text == 'success' else text)"
    envelope["full"] = ["python", "-c", command_text]
    rows[0]["envelope"] = write(rows[0]["envelope"]["path"], envelope)
    from frutlups import gitws
    snapshot = gitws.workspace_witness(root, executable=cfg.git)
    for round_no in (1, 2):
        if round_no == 2:
            prompt = write("owned-round2.md", b"Correct the original requirement.\n")
            ref = write("owned-envelope2.json", dict(envelope, round=2))
            rows.append(_attempts.event("prompt", slice="M001-S01", round=2, **prompt, envelope=ref, baseline=[]))
        changed = []
        if round_no == 2 and control == "product":
            relative = "07_app/changed-product.txt"
            (root / relative).write_bytes(b"actual changed product\n")
            changed = [dict(path=relative, sha=ledger.evidence_sha((root / relative).read_bytes()), kind="added")]
        manifest = write(f"owned-manifest{round_no}.json", dict(schema="frutlups.manifest/2", slice="M001-S01", round=round_no,
                                                              changed=[dict(row, round=round_no) for row in changed]))
        rows.append(_attempts.event("coded", slice="M001-S01", round=round_no, changed=changed, manifest=manifest, result="implemented", seat="coder"))
        snapshot = gitws.workspace_witness(root, executable=cfg.git)
        excluded = {cfg.ledger, "roadmap.yaml", "docs/roadmap.md", "05_governance/backlog.md"}
        excluded |= evidence.evidence_paths(tuple(ledger._parse_event(row) for row in rows), root)
        product = _attempts._digest({p: (value[0], value[2][2], value[-1]) for p, value in snapshot["files"].items()
                                    if p not in excluded})
        success = round_no == 2 and control == "success"
        text = "success" if success else "different failure" if round_no == 2 and control == "failure" else "owned failure"
        (root / "local_state/failure.txt").write_text(text, encoding="utf-8")
        measured = subprocess.run([sys.executable, "-c", command_text], cwd=root,
                                  capture_output=True, text=True, timeout=10)
        command = dict(label="full", argv=envelope["full"], exit=measured.returncode,
                       timed_out=False, secs=round_no, stdout_tail=measured.stdout,
                       stderr_tail=measured.stderr)
        witness = dict(before=_attempts._digest(snapshot), after=_attempts._digest(snapshot), stable=True, head=snapshot["head"],
                       index=_attempts._digest(snapshot["index"]),
                       product=product)
        data = dict(schema="frutlups.receipt/2", slice="M001-S01", round=round_no,
                    t=f"2026-01-01T00:00:0{round_no}Z", base_commit=snapshot["head"], tree_dirty_before=True,
                    tree_clean_after=False, ok=success, manifest=manifest, witness=witness,
                    commands=[command], runtime=dict(attempts.internal_loop(root, cfg, model).runtime.identity),
                    observation="process")
        if round_no == 2 and control == "missing": data.pop("witness")
        if round_no == 2 and control == "truncated": command["stderr_tail"] = "[Earlier diagnostic output omitted.]\n"
        if round_no == 2 and control == "wrong_command": command["argv"] = ["different"]
        if round_no == 2 and control == "contradiction": command["exit"] = 0
        ref = write(f"owned-receipt{round_no}.json", data)
        rows.append(_attempts.event("verified", slice="M001-S01", round=round_no, receipt=ref["path"], sha=ref["sha"], ok=success))
    # Evidence is committed only inside the owned disposable repository.
    test_loop.git(root, "add", ".")
    test_loop.git(root, "commit", "-qm", "owned durable failure proofs")
    attempts.save(root, cfg, rows)
    return root, cfg, model


@pytest.mark.parametrize("control", ["same", "product", "failure", "success", "missing", "truncated", "wrong_command", "contradiction"])
def test_durable_no_progress_and_controls(project, control):
    root, cfg, model = history_of_failures(project, control)
    events = ledger.read(root / cfg.ledger)
    before = (root / cfg.ledger).read_bytes()
    if control in ("wrong_command", "contradiction"):
        with pytest.raises(ValueError, match="frozen|contradicts"):
            evidence.no_progress(root, events, model, "M001-S01")
        return
    assert evidence.no_progress(root, events, model, "M001-S01") is (control == "same")
    comparison = fresh_step(root, compare=True)
    assert comparison.returncode == 0, comparison.stderr
    assert comparison.stdout.strip() == "no_progress=" + str(control == "same")
    assert (root / cfg.ledger).read_bytes() == before
    if control == "same":
        result = fresh_step(root)
        assert result.returncode == 0, result.stderr
        assert "identical product and verification failure" in result.stdout
        assert (root / cfg.ledger).read_bytes() == before
    elif control != "success":
        # A changed or uncomparable proof issues the next prompt, no coder yet.
        result = fresh_step(root)
        assert result.returncode == 0, result.stderr
        assert ledger.fold(ledger.read(root / cfg.ledger), model).slices["M001-S01"].step == "coding"


@pytest.mark.parametrize("ev", ["resolved", "run_admitted", "accepted", "milestone_done", "verified"])
def test_private_coding_writer_cannot_expand_authority(project, ev):
    root, cfg, model, _, _ = materialize(project, False)
    before = (root / cfg.ledger).read_bytes()
    with pytest.raises(ValueError, match="coding transition"):
        ledger._append_coding(root, root / cfg.ledger, model, _attempts.event(ev), before, executable=cfg.git)
    assert (root / cfg.ledger).read_bytes() == before
    # Public admission is not a write path around the refused private writer.
    assert loop.preflight(root, cfg, model) == ("admission configuration differs from canonical project configuration",)
    assert (root / cfg.ledger).read_bytes() == before


def test_outcome_reference_drift_is_rehashed(project, monkeypatch):
    root, cfg, model, _, write = materialize(project, False)
    ref = write("07_app/retained.txt", b"original\n")
    test_loop.git(root, "add", "07_app/retained.txt")
    test_loop.git(root, "commit", "-qm", "owned retained evidence")
    calls = []
    def mutate(root, cfg, value):
        value["evidence"] = [ref]
        (root / ref["path"]).write_bytes(b"modified\n")
    adapter(monkeypatch, calls, "blocked_environment", mutate=mutate)
    with pytest.raises(ValueError, match="drift"):
        step(attempts.internal_loop(root, cfg, model))
    assert len(calls) == 1
    assert (root / ref["path"]).read_bytes() == b"modified\n"


@pytest.mark.parametrize("phase", ["dispatch", "persistence"])
def test_durable_start_and_final_raw_guard(project, monkeypatch, phase):
    root, cfg, model, _, _ = materialize(project, False)
    calls = []
    def remove_local_marker(root, cfg, value):
        (root / "local_state/frutlups/coding/M001-S01_r1.json").unlink()
    adapter(monkeypatch, calls, change=False, mutate=remove_local_marker,
            transform=(lambda t: "not JSON") if phase == "dispatch" else None)
    original = evidence.immutable_text
    def concurrent(root, path, text, limit):
        result = original(root, path, text, limit)
        if phase == "persistence" and path.endswith("_manifest.json"):
            (root / "07_app/CONTEXT.md").write_bytes(b"late unrecorded mutation\n")
        return result
    monkeypatch.setattr(evidence, "immutable_text", concurrent)
    with pytest.raises(ValueError):
        step(attempts.internal_loop(root, cfg, model))
    with pytest.raises((ValueError, loop._Stop)):
        step(attempts.internal_loop(root, cfg, model))
    assert len(calls) == 1
    assert not any(e.ev == ledger.Ev.coded for e in ledger.read(root / cfg.ledger))


def test_initial_prompt_custom_template_keeps_complete_contract(project, monkeypatch):
    root, cfg, model, rows, _ = materialize(project, False)
    (root / "prompts/templates/coding_prompt.md").write_text(
        "# {{title}}\n{{notes}}\n{{outcome_contract}}\n", encoding="utf-8")
    test_loop.git(root, "add", ".")
    test_loop.git(root, "commit", "-qm", "owned minimal custom template")
    attempts.save(root, cfg, [])
    obj = attempts.internal_loop(root, cfg, model)
    issued = step(obj).slices["M001-S01"]
    value = ledger.envelope(root, ledger._thaw(issued.envelope), issued.id, 1)
    assert value["acceptance"] == list(model.milestones[0].slices[0].acceptance)
    assert value["full"] == list(model.verification_full)
    text = (root / issued.last_prompt).read_text("utf-8")
    assert "Complete frozen coding envelope" in text
    calls = []
    adapter(monkeypatch, calls, change=False)
    assert step(obj).slices["M001-S01"].step == "verifying"
    assert len(calls) == 1


def test_resume_refuses_changed_model_without_new_budget(project, monkeypatch):
    root, cfg, model, _, write = materialize(project, True)
    calls = []
    adapter(monkeypatch, calls, "blocked_environment")
    blocked = step(attempts.internal_loop(root, cfg, model))
    ref = write("questions/answered/environment.md", b"Environment restored; same seat.\n")
    rows = [ledger._event_dict(e) for e in ledger.read(root / cfg.ledger)]
    rows.append(dict(_attempts.event("resolved", scope="M001-S01", reason="restored", authority=[ref]), by="human"))
    attempts.save(root, cfg, rows)
    changed_cfg = replace(cfg, seats={**cfg.seats, "coder": replace(cfg.seats["coder"], model="unadmitted")})
    obj = attempts.internal_loop(root, changed_cfg, model)
    step(obj)
    with pytest.raises(ValueError, match="unchanged coder configuration"):
        step(obj)
    assert len(calls) == 1
    current = ledger.validate_history(root, ledger.read(root / cfg.ledger), model)
    assert current.runs["R1"]["consumed"] == blocked.runs["R1"]["consumed"]


@pytest.mark.parametrize("failure", [FailureClass.transport, FailureClass.output])
def test_failed_transport_with_edits_is_not_implemented_or_retried(project, monkeypatch, failure):
    root, cfg, model, _, _ = materialize(project)
    calls = []
    adapter(monkeypatch, calls)
    original = pi.PiSeat.run
    def fail(self, job, cfg):
        return replace(original(self, job, cfg), status="failed", failure_class=failure, diagnostic="owned failure")
    monkeypatch.setattr(pi.PiSeat, "run", fail)
    with pytest.raises(loop._Stop) as caught:
        step(attempts.internal_loop(root, cfg, model))
    assert caught.value.reason == (loop.StopReason.seat_output if failure == FailureClass.output else loop.StopReason.seat_transport)
    assert len(calls) == 1
    events = ledger.read(root / cfg.ledger)
    assert events[-1].ev == ledger.Ev.attempt_finished
    assert events[-1].data["status"] == "failed"
    assert not any(e.ev == ledger.Ev.coded for e in events)
    assert (root / "07_app/outcome.txt").exists()


@pytest.mark.parametrize("placeholder", ["notes", "outcome_contract"])
def test_legacy_renderer_rejects_v2_only_placeholders(tmp_path, placeholder):
    template = tmp_path / "legacy.md"
    template.write_text("{{" + placeholder + "}}", encoding="utf-8")
    # The legacy-only restriction is retired; native placeholders are explicit.
    assert render.render_template(template, {placeholder: "native contract"}) == "native contract\n"
    template.write_text("{{unsupported_placeholder}}", encoding="utf-8")
    with pytest.raises(render.RenderError, match="unknown placeholders"):
        render.render_template(template, {placeholder: "native contract"})
