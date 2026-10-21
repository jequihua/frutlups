"""Exact admitted S02 task selection and real private consumers."""
import hashlib
import json
import subprocess
import sys
import tarfile
from dataclasses import replace
from pathlib import Path, PurePosixPath

import pytest
import yaml
import test_loop
from frutlups import ledger, render, roadmap, receipt, _preflight, gitws, loop, _attempts
from _native_support import Controller, grant_project

FIXTURES = Path(__file__).parent / "fixtures"
CASES = json.loads((FIXTURES / "selector_issuance_cases.json").read_bytes())
FIELDS = CASES["fields"]
project = test_loop.project


@pytest.fixture(scope="session")
def selector_pin(tmp_path_factory):
    manifest_bytes = (FIXTURES / "v5_selector_9fff0cd.manifest.json").read_bytes()
    assert hashlib.sha256(manifest_bytes).hexdigest() == "d97477fd13f9d564e2811f8f07487f41c531402bbff195aefd2fe1dfcdcada36"
    manifest = json.loads(manifest_bytes)
    archive = FIXTURES / "v5_selector_9fff0cd.tar"
    raw = archive.read_bytes()
    assert len(raw) == manifest["archive_bytes"] == 1167360
    assert hashlib.sha256(raw).hexdigest() == manifest["archive_raw_sha256"] == "f0e34715c2be243f0dddfbe211065c0ce60ba207779ede8e2401e055a89e5a4c"
    expected = {row["path"]: row for row in manifest["members"]}
    with tarfile.open(archive) as source:
        members = source.getmembers()
        assert len(members) == len({m.name for m in members}) == len(expected) == 83
        assert {m.name for m in members} == set(expected)
        for member in members:
            path = PurePosixPath(member.name)
            assert member.isfile()
            assert not path.is_absolute() and ".." not in path.parts
            assert "\\" not in member.name and ":" not in member.name
            assert member.mode == 0o644 and expected[member.name]["mode"] == "100644"
        files = {member.name: source.extractfile(member).read() for member in members}
        for name, data in files.items():
            assert len(data) == expected[name]["bytes"]
            assert hashlib.sha256(data).hexdigest() == expected[name]["raw_sha256"]
    root = tmp_path_factory.mktemp("selector-pin")
    for name, data in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return root


def test_exact_selector_fixtures(selector_pin):
    assert hashlib.sha256((FIXTURES / "selector_issuance_cases.json").read_bytes()).hexdigest() == "193a8230cfbaef2a5082c706e3027ffcb2e42ea25abb6189ee926e2323826f8c"
    assert hashlib.sha256((FIXTURES / "selector_field_consumers.json").read_bytes()).hexdigest() == "91e211c66a3523b5cbf761c8fcb4d3a2e576568868a18b63d46c77d2bdb9be37"
    mapping = json.loads((FIXTURES / "selector_field_consumers.json").read_bytes())
    assert [row["field"] for row in mapping["fields"]] == FIELDS
    assert len(FIELDS) == 14
    assert len(CASES["cases"]) == 11


ORACLE = r"""
import json,sys,unittest
from pathlib import Path
root=Path(sys.argv[1]); fixtures=Path(sys.argv[2])
sys.path[:0]=[str(root/'tests'),str(root/'scripts')]
import test_prompt_dependability as t
from frutlups import render as f_render, ledger as f_ledger, roadmap as f_roadmap, receipt as f_receipt
from types import SimpleNamespace
cases=json.loads((fixtures/'selector_issuance_cases.json').read_bytes())
original=t.ContractSelectionTests.issue
observations=[]
def issue(self,root,expected,**kwargs):
    model=f_roadmap.load(root/'roadmap.yaml')
    events=f_ledger.read(root/'05_governance/ledger.jsonl')
    state=f_ledger.validate_history(root,events,model).slices[t.SID]
    item=model.milestones[0].slices[0]
    before=t._workspace.snapshot(root)
    frozen=f_render.coding_envelope(root,model,item,state,events)
    again=f_render.coding_envelope(root,model,item,state,events)
    assert frozen==again
    assert t._workspace.snapshot(root)==before
    task=next(name for name,value in cases['contracts'].items() if expected==value)
    for field in cases['fields']:
        assert frozen[field]==cases['contracts'][task][field],(self._testMethodName,field,frozen[field],expected[field])
    text=f_render.coding_v2(root,frozen,state)
    assert len(text.encode())<=f_render.CODING_LIMIT
    # The real pinned consumer computes the identity for this same declaration.
    executable,identity=t._workspace.resolve_runtime(root,{'runtime':frozen['runtime']})
    selected=f_receipt.native_runtime(root,model,frozen,SimpleNamespace(git='git'))
    assert Path(selected.executable).resolve()==Path(executable).resolve()
    assert dict(selected.identity)==identity
    result=original(self,root,expected,**kwargs)
    for field in cases['fields']:
        assert result[field]==frozen[field],field
    observations.append(dict(test=self._testMethodName,task=task,round=state.round,backend=kwargs.get('backend','manual'),case=dict(self._subtest.params) if self._subtest else {}))
    return result
t.ContractSelectionTests.issue=issue
suite=unittest.defaultTestLoader.loadTestsFromTestCase(t.ContractSelectionTests)
result=unittest.TextTestRunner(verbosity=1).run(suite)
print('SELECTOR_RESULT='+json.dumps(dict(run=result.testsRun,failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),observations=observations)))
raise SystemExit(not result.wasSuccessful())
"""


def test_pinned_fresh_process_issuance_and_all_field_consumers(selector_pin):
    result = subprocess.run([sys.executable, "-B", "-c", ORACLE, str(selector_pin), str(FIXTURES)],
                            capture_output=True, text=True, timeout=900)
    assert result.returncode == 0, result.stdout + result.stderr
    row = json.loads(next(line.partition("=")[2] for line in result.stdout.splitlines()
                          if line.startswith("SELECTOR_RESULT=")))
    assert row["run"] == 5
    assert row["failures"] == 0
    assert row["errors"] == 0
    assert row["skipped"] == 0
    assert {v["task"] for v in row["observations"]} == set(CASES["contracts"])
    assert {v["backend"] for v in row["observations"]} == {"manual", "autonomous"}
    for case in CASES["cases"]:
        keys = ("route", "transition") if "route" in case else ("custom", "backend")
        observed = [v for v in row["observations"] if v["test"] == case["test"]
                    and all(v["case"].get(key) == case[key] for key in keys)]
        expected = case.get("rounds") or [case[key] for key in ("initial", "continuation", "correction") if case[key]]
        assert [(v["round"], v["task"]) for v in observed] == [(v["round"], v["task"]) for v in expected]



def configure(root, key, *, invalid_later=False):
    task = CASES["contracts"][key]
    data = yaml.safe_load((root / "roadmap.yaml").read_text("utf-8"))
    item = data["milestones"][0]["slices"][0]
    for field in ("title", "objective", "acceptance", "non_goals", "read_first", "allowed_prefixes", "focused", "notes"):
        item[field] = task[field]
    item["verification"] = task["full"]
    item["memory_pages"] = ["page" + key[-1] + ".md"]
    data["memory"] = dict(kind="llloom", root="memory/llloom", manual="docs/memory.md", read_verbs=["search"], read_first_pages=[])
    data["forbidden"] = task["forbidden"]
    data["runtime"] = task["runtime"]
    data["verification"].update(timeout_seconds=task["timeout_seconds"], observation=task["observation"])
    if invalid_later:
        data["runtime"] = {"python": "3.99"}
        item["verification"] = ["not-the-issued-verifier"]
        item["allowed_prefixes"] = ["07_app/a.py", "07_app/b.py"]
        data["verification"]["timeout_seconds"] = 99
    (root / "roadmap.yaml").write_text(yaml.safe_dump(data), encoding="utf-8", newline="\n")
    test_loop.git(root, "add", "roadmap.yaml")
    test_loop.git(root, "commit", "-qm", "owned task contract")
    return roadmap.load(root / "roadmap.yaml")


def transition(root, cfg, model):
    obj = Controller(root, cfg, model)
    events = ledger.read(root / cfg.ledger)
    current = ledger.validate_history(root, events, model).slices["M001-S01"]
    item = model.milestones[0].slices[0]
    obj._slice(item, current, events)
    return ledger.validate_history(root, ledger.read(root / cfg.ledger), model)


def resolved_review(project):
    root, cfg, _ = project
    model = configure(root, "v1")
    root, cfg, model = grant_project((root, cfg, model))
    cfg = replace(cfg, timeouts=replace(cfg.timeouts, verification_seconds=60))
    test_loop.plan(root, coder=[{"writes": {"07_app/a.py": "original a", "07_app/b.py": "original b"}}],
                   reviewer=[{"verdict": "blocked", "rows": [test_loop.ROW]},
                             {"verdict": "needs_work", "rows": [test_loop.ROW2], "close_ids": ["M001-S01-H1"]},
                             {"verdict": "pass", "close_ids": ["M001-S01-H2"]}])
    for expected in ("coding", "verifying", "reviewing", "blocked"):
        assert transition(root, cfg, model).slices["M001-S01"].step == expected
    before = (root / cfg.ledger).read_bytes()
    ref = dict(path="00_brief/decisions.md", sha=ledger.evidence_sha((root / "00_brief/decisions.md").read_bytes()))
    row = dict(_attempts.event("resolved", scope="M001-S01", reason="owned resolution", authority=[ref]), by="architect")
    with (root / cfg.ledger).open("ab") as stream:
        stream.write(json.dumps(row).encode() + b"\n")
    assert (root / cfg.ledger).read_bytes().startswith(before)
    ledger.validate_history(root, ledger.read(root / cfg.ledger), model)
    model = configure(root, "v2")
    state = transition(root, cfg, model)
    envelope = ledger.envelope(root, ledger._thaw(state.slices["M001-S01"].envelope), "M001-S01", 2)
    for field in FIELDS:
        assert envelope[field] == CASES["contracts"]["v1"][field], field
    # The second coder has no new writes, but retains both original identities.
    test_loop.plan(root, coder=[{}, {}, {"writes": {"07_app/a.py": "corrected a"}}],
                   reviewer=[{"verdict": "blocked", "rows": [test_loop.ROW]},
                             {"verdict": "needs_work", "rows": [test_loop.ROW2], "close_ids": ["M001-S01-H1"]},
                             {"verdict": "pass", "close_ids": ["M001-S01-H2"]}])
    for expected in ("verifying", "reviewing", "fix"):
        assert transition(root, cfg, model).slices["M001-S01"].step == expected
    return root, cfg, model


@pytest.mark.parametrize("forbidden_write", [False, True])
def test_real_narrowed_dispatch_frozen_verifier_and_review(project, monkeypatch, forbidden_write):
    root, cfg, model = resolved_review(project)
    before = (root / cfg.ledger).read_bytes()
    old_b = ledger.evidence_sha((root / "07_app/b.py").read_bytes())
    state = transition(root, cfg, model)
    current = state.slices["M001-S01"]
    assert current.round == 3 and current.step == "coding"
    issued = ledger.envelope(root, ledger._thaw(current.envelope), current.id, 3)
    for field in FIELDS:
        assert issued[field] == CASES["contracts"]["v2"][field], field
    assert "Historical blocker" in issued["findings"]
    assert "Historical resolution authority" in issued["findings"]
    assert "| M001-S01-H2 | P1 | open |" in issued["findings"]
    assert (root / cfg.ledger).read_bytes().startswith(before)
    model = configure(root, "v3", invalid_later=True)
    selected, _, errors = _preflight._static(root, cfg, model)
    assert errors == ()
    assert dict(selected.identity) == dict(receipt.native_runtime(root, model, issued, cfg).identity)
    if forbidden_write:
        test_loop.plan(root, coder=[{}, {}, {"writes": {"07_app/b.py": "attempted excluded edit"}}])
        prefix = (root / cfg.ledger).read_bytes()
        witness = gitws.workspace_witness(root, executable=cfg.git)
        with pytest.raises(loop._Stop, match="07_app/b.py"):
            transition(root, cfg, model)
        assert (root / "07_app/b.py").read_bytes() == b"attempted excluded edit"
        assert (root / cfg.ledger).read_bytes().startswith(prefix)
        after = gitws.workspace_witness(root, executable=cfg.git)
        assert after["head"] == witness["head"]
        assert after["index"] == witness["index"]
        assert not any(e.ev == ledger.Ev.coded and e.round == 3 for e in ledger.read(root / cfg.ledger))
        with pytest.raises((ValueError, loop._Stop)):
            transition(root, cfg, model)
        assert (root / "local_state/coder-count.txt").read_text() == "3"
        return
    state = transition(root, cfg, model)
    current = state.slices["M001-S01"]
    assert current.step == "verifying"
    manifest = ledger.manifest(root, ledger._thaw(current.manifest))
    rows = {row["path"]: row for row in manifest["changed"]}
    assert rows["07_app/a.py"]["round"] == 3
    assert rows["07_app/b.py"]["round"] == 1
    assert rows["07_app/b.py"]["sha"] == old_b
    last = next(e for e in reversed(ledger.read(root / cfg.ledger)) if e.ev == ledger.Ev.coded)
    assert [row["path"] for row in last.data["changed"]] == ["07_app/a.py"]
    launches = []
    original = receipt.run_process
    def launch(argv, cwd, env, timeout, *args, **kwargs):
        launches.append((argv, timeout))
        return original(argv, cwd, env, timeout, *args, **kwargs)
    monkeypatch.setattr(receipt, "run_process", launch)
    state = transition(root, cfg, model)
    assert state.slices["M001-S01"].step == "reviewing"
    value = json.loads((root / state.slices["M001-S01"].last_receipt).read_bytes())
    assert value["commands"][0]["argv"] == issued["full"]
    assert value["commands"][0]["stdout_tail"].strip() == "full-2"
    assert value["observation"] == issued["observation"]
    assert value["runtime"] == dict(selected.identity)
    assert len(launches) == 1
    assert Path(launches[0][0][0]).resolve() == Path(selected.executable).resolve()
    assert 0 < launches[0][1] <= issued["timeout_seconds"]
    captured = []
    review = render.review_v2
    def capture(root, frozen, *args, **kwargs):
        captured.append(frozen)
        return review(root, frozen, *args, **kwargs)
    monkeypatch.setattr(render, "review_v2", capture)
    assert transition(root, cfg, model).slices["M001-S01"].step == "accept_pending"
    assert len(captured) == 1
    for field in FIELDS:
        assert captured[0][field] == issued[field], field
    assert (root / "07_app/b.py").read_bytes() == b"original b"


@pytest.mark.parametrize("events,expected", [
    ([], False),
    ([("prompt", "M001-S01")], False),
    ([("prompt", "M001-S01"), ("resolved", "M001-S01")], True),
    ([("resolved", "M001-S01"), ("prompt", "M001-S01")], False),
    ([("prompt", "M001-S01"), ("resolved", "M001-S02")], False),
    ([("resolved", "M001-S01"), ("prompt", "M001-S02"), ("resolved", "M001")], True),
    ([("resolved", "M001-S01"), ("note", None), ("artifact", "M001-S01")], True),
    ([("resolved", "M001-S01"), ("prompt", "M001-S01"), ("resolved", "M001-S01")], True),
])
def test_order_selector_neutrality(events, expected):
    from types import SimpleNamespace
    rows = [SimpleNamespace(ev=ledger.Ev(kind), slice=scope if kind == "prompt" else None,
                            data={"scope": scope}) for kind, scope in events]
    assert render.resumes_contract(rows, "M001-S01") is expected


@pytest.mark.parametrize("which", ["envelope", "authority"])
@pytest.mark.parametrize("missing", [False, True])
def test_invalid_selected_input_refuses_before_every_effect(project, monkeypatch, which, missing):
    import test_outcomes_v2 as outcomes
    import test_attempts_v2 as attempts
    from _native_support import probe_errors
    root, cfg, model, rows, write = outcomes.materialize(project)
    calls = []
    outcomes.adapter(monkeypatch, calls, "blocked_authority")
    state = outcomes.step(attempts.internal_loop(root, cfg, model))
    ref = write("questions/answered/selector-authority.md", b"Owned resolution.\n")
    rows = [ledger._event_dict(e) for e in ledger.read(root / cfg.ledger)]
    rows.append(dict(_attempts.event("resolved", scope="M001-S01", reason="input supplied", authority=[ref]), by="architect"))
    attempts.save(root, cfg, rows)
    events = ledger.read(root / cfg.ledger)
    state = ledger.validate_history(root, events, model)
    current = state.slices["M001-S01"]
    target = root / (current.envelope["path"] if which == "envelope" else ref["path"])
    if missing:
        target.unlink()
    else:
        target.write_bytes(b"tampered selected input")
    before = gitws.workspace_witness(root, executable=cfg.git)
    prefix = (root / cfg.ledger).read_bytes()
    monkeypatch.setattr(_preflight.runtime, "resolve_runtime", lambda *a, **k: pytest.fail("invalid history runtime fallback"))
    monkeypatch.setattr(_preflight, "_probe", lambda *a, **k: pytest.fail("probe effect"))
    monkeypatch.setattr(receipt, "run", lambda *a, **k: pytest.fail("verifier effect"))
    monkeypatch.setattr(test_loop.pi.PiSeat, "run", lambda *a, **k: pytest.fail("seat effect"))
    assert probe_errors(root, cfg, model)
    with pytest.raises(ValueError, match="evidence|drift"):
        render.task_contract(root, model, model.milestones[0].slices[0], current, events)
    assert gitws.workspace_witness(root, executable=cfg.git) == before
    assert (root / cfg.ledger).read_bytes() == prefix
    assert len(calls) == 1


def test_public_and_legacy_refusals_remain_without_effects(project, monkeypatch):
    root, cfg, model = project
    before = gitws.workspace_witness(root, executable=cfg.git)
    monkeypatch.setattr(_preflight, "_probe", lambda *a, **k: pytest.fail("public probe"))
    monkeypatch.setattr(receipt, "run", lambda *a, **k: pytest.fail("public verification"))
    refusal = "admission configuration differs from canonical project configuration"
    assert _preflight.prepare(root, cfg, model)[2] == (refusal,)
    assert loop.run(root, cfg, model) == loop.StopReason.preflight_failed
    obj = loop.Loop(root, cfg, model, emit=lambda _: None)  # construction alone has no effects
    assert obj.iteration() == loop.StopReason.preflight_failed
    with pytest.raises(ValueError, match="append is not enabled"):
        ledger.append(root / cfg.ledger, _attempts.event("note", text="refuse"), model)
    with pytest.raises(ValueError, match="unsupported"):
        render.coding_envelope(root, replace(model, schema="frutlups.roadmap/1"),
                               model.milestones[0].slices[0], ledger.fold((), model).slices["M001-S01"])
    with pytest.raises(ValueError, match="unsupported legacy"):
        ledger._parse_event(dict(_attempts.event("note", text="refuse"), schema="frutlups.ledger/1"))
    assert gitws.workspace_witness(root, executable=cfg.git) == before
    assert not (root / "local_state/frutlups/jobs").exists()


@pytest.mark.parametrize("profile", [False, True])
@pytest.mark.parametrize("changed", [None, "roadmap", "envelope"])
def test_real_checkpoint_restart_preserves_binding_and_usage(project, monkeypatch, profile, changed):
    import test_checkpoints_v2 as checkpoints
    root, cfg, model, _, write = checkpoints.materialize(project, profile=profile, seats=1)
    test_loop.plan(root, reviewer=[{"verdict": "pass"}])
    append = ledger._append_review
    def interrupt(root, path, model, raw, *args, **kwargs):
        result = append(root, path, model, raw, *args, **kwargs)
        if raw["ev"] == "review_checkpoint":
            raise OSError("owned completed checkpoint interruption")
        return result
    monkeypatch.setattr(ledger, "_append_review", interrupt)
    with pytest.raises(OSError, match="completed checkpoint"):
        transition(root, cfg, model)
    monkeypatch.setattr(ledger, "_append_review", append)
    state = ledger.validate_history(root, ledger.read(root / cfg.ledger), model)
    assert len(state.checkpoints) == 1
    assert len(state.attempts) == 1
    assert all(a["finish"] is not None for a in state.attempts.values())
    usage = state.runs
    attempts_before = state.attempts
    if changed == "roadmap":
        model = configure(root, "v2")
    elif changed == "envelope":
        import test_attempts_v2 as attempts
        rows = [ledger._event_dict(e) for e in ledger.read(root / cfg.ledger)]
        issued = next(row for row in rows if row["ev"] == "prompt")
        frozen = ledger.envelope(root, issued["envelope"], "M001-S01", 1)
        frozen["objective"] = "Changed task cannot reuse the completed checkpoint."
        issued["envelope"] = write(issued["envelope"]["path"], frozen)
        attempts.save(root, cfg, rows)
    prefix = (root / cfg.ledger).read_bytes()
    witness = gitws.workspace_witness(root, executable=cfg.git)
    monkeypatch.setattr(test_loop.pi.PiSeat, "run", lambda *a, **k: pytest.fail("checkpoint redispatch"))
    monkeypatch.setattr(test_loop.claude.ClaudeSeat, "run", lambda *a, **k: pytest.fail("checkpoint redispatch"))
    if changed:
        with pytest.raises((ValueError, loop._Stop), match="HEAD/index" if changed == "roadmap" else "bounded text evidence drift"):
            transition(root, cfg, model)
        assert (root / cfg.ledger).read_bytes() == prefix
        assert gitws.workspace_witness(root, executable=cfg.git) == witness
        state = ledger.validate_history(root, ledger.read(root / cfg.ledger), model)
    else:
        state = transition(root, cfg, model)
        assert state.slices["M001-S01"].step == "accept_pending"
        assert (root / cfg.ledger).read_bytes().startswith(prefix)
    assert state.attempts == attempts_before
    assert state.runs == usage


@pytest.mark.parametrize("retry", ["transport", "format"])
def test_changed_envelope_cannot_reuse_retry_or_refund(project, retry):
    import test_attempts_v2 as attempts
    root, cfg, model, _, write = attempts.materialize(project)
    with attempts.operation(root, cfg, model) as first:
        first.finish(False)
    rows = [ledger._event_dict(e) for e in ledger.read(root / cfg.ledger)]
    issued = next(row for row in rows if row["ev"] == "prompt")
    frozen = ledger.envelope(root, issued["envelope"], "M001-S01", 1)
    frozen["objective"] = "Different frozen task cannot inherit a retry."
    issued["envelope"] = write(issued["envelope"]["path"], frozen)
    attempts.save(root, cfg, rows)
    state = ledger.validate_history(root, ledger.read(root / cfg.ledger), model)
    prefix = (root / cfg.ledger).read_bytes()
    witness = gitws.workspace_witness(root, executable=cfg.git)
    with pytest.raises(ValueError, match="stale retry contract"):
        with attempts.operation(root, cfg, model, retry=retry, parent=first.invocation):
            pytest.fail("changed envelope dispatched")
    after = ledger.validate_history(root, ledger.read(root / cfg.ledger), model)
    assert after.runs == state.runs
    assert after.attempts == state.attempts
    assert (root / cfg.ledger).read_bytes() == prefix
    assert gitws.workspace_witness(root, executable=cfg.git) == witness


@pytest.mark.parametrize("declaration", [{}, {"python": "3.14.3"}])
def test_runtime_context_transition_and_exact_drift_guards(project, monkeypatch, declaration):
    from frutlups import runtime
    root, cfg, model = project
    model = replace(model, runtime=declaration)
    runner = Controller(root, cfg, model)
    expected = runtime.resolve_runtime(root, model, git=cfg.git)
    prior = receipt.native_runtime(root, model, {"runtime": declaration}, cfg)
    before = gitws.workspace_witness(root, executable=cfg.git)
    for mutation in (None, "python", "implementation", "source", "sha", "executable", "declaration"):
        runner.runtime = prior
        runner._slice_runtime_declaration = dict(declaration)
        if mutation == "executable":
            runner.runtime = replace(prior, executable="different-interpreter")
        elif mutation == "declaration":
            runner._slice_runtime_declaration = {"python": "3.14"}
        elif mutation:
            runner.runtime = replace(prior, identity={**prior.identity, mutation: "changed"})
        if mutation:
            retained = runner.runtime
            with pytest.raises(ValueError, match="selected holistic runtime drift"):
                runner._holistic_runtime()
            assert runner.runtime is retained
        else:
            selected = runner._holistic_runtime()
            assert selected.executable == expected.executable
            assert dict(selected.identity) == dict(expected.identity)
            assert runner._slice_runtime_declaration is None
            assert runner._holistic_runtime() == selected
        assert gitws.workspace_witness(root, executable=cfg.git) == before
    # A freshly resolved physical source change must not adopt the cached identity.
    runner.runtime = prior
    runner._slice_runtime_declaration = dict(declaration)
    resolve = runtime.resolve_runtime
    def changed(*args, **kwargs):
        value = resolve(*args, **kwargs)
        return replace(value, identity={**value.identity, "source": "project.local"})
    monkeypatch.setattr(runtime, "resolve_runtime", changed)
    with pytest.raises(ValueError, match="selected holistic runtime drift"):
        runner._holistic_runtime()
    assert runner.runtime is prior
    assert gitws.workspace_witness(root, executable=cfg.git) == before


@pytest.mark.parametrize("declaration,fresh_acceptance", [({}, False), ({"python": "3.14.3"}, False), ({}, True)])
def test_same_controller_holistic_context_and_fresh_checkpoint_recovery(project, monkeypatch, declaration, fresh_acceptance):
    from frutlups import runtime
    root, cfg, model = project
    data = yaml.safe_load((root / "roadmap.yaml").read_text("utf-8"))
    if declaration:
        data["runtime"] = declaration
    else:
        data.pop("runtime", None)
    data["milestones"][0]["holistic_review"] = True
    (root / "roadmap.yaml").write_text(yaml.safe_dump(data), encoding="utf-8", newline="\n")
    test_loop.git(root, "add", "roadmap.yaml")
    test_loop.git(root, "commit", "-qm", "owned holistic declaration")
    root, cfg, model = grant_project((root, cfg, roadmap.load(root / "roadmap.yaml")))
    cfg = replace(cfg, commit_on_accept=True)
    runner = Controller(root, cfg, model)
    for _ in range(4):
        assert runner.iteration() is None
    if fresh_acceptance:
        runner = Controller(root, cfg, model)
    assert runner.iteration() is None
    assert runner._slice_runtime_declaration == declaration
    prefix = (root / cfg.ledger).read_bytes()
    witness = gitws.workspace_witness(root, executable=cfg.git)
    prior = runner.runtime
    runner.runtime = replace(prior, identity={**prior.identity, "sha": "0" * 64})
    with monkeypatch.context() as patch:
        patch.setattr(test_loop.claude.ClaudeSeat, "run", lambda *a, **k: pytest.fail("drift dispatched"))
        assert runner.iteration() == loop.StopReason.internal
    assert (root / cfg.ledger).read_bytes() == prefix
    assert gitws.workspace_witness(root, executable=cfg.git) == witness
    runner.runtime = prior
    append = ledger._append_review
    def interrupt(root, path, model, raw, *args, **kwargs):
        value = append(root, path, model, raw, *args, **kwargs)
        if raw["ev"] == "review_checkpoint" and raw["scope"] == "M001":
            raise OSError("owned holistic checkpoint interruption")
        return value
    with monkeypatch.context() as patch:
        patch.setattr(ledger, "_append_review", interrupt)
        assert runner.iteration() == loop.StopReason.internal
    selected = runtime.resolve_runtime(root, model, git=cfg.git)
    assert runner.runtime == selected
    assert runner._slice_runtime_declaration is None
    before = ledger.validate_history(root, ledger.read(root / cfg.ledger), model)
    assert any(c["scope"] == "M001" for c in before.checkpoints.values())
    assert (root / "local_state/holistic-count.txt").read_text() == "1"
    fresh = Controller(root, cfg, model)
    original = fresh.runtime
    prefix = (root / cfg.ledger).read_bytes()
    witness = gitws.workspace_witness(root, executable=cfg.git)
    fresh.runtime = replace(original, identity={**original.identity, "sha": "0" * 64})
    monkeypatch.setattr(test_loop.claude.ClaudeSeat, "run", lambda *a, **k: pytest.fail("checkpoint redispatch"))
    assert fresh.iteration() == loop.StopReason.internal
    assert (root / cfg.ledger).read_bytes() == prefix
    assert gitws.workspace_witness(root, executable=cfg.git) == witness
    fresh.runtime = original
    assert fresh.iteration() is None
    after = ledger.validate_history(root, ledger.read(root / cfg.ledger), model)
    assert after.milestones_done == frozenset({"M001"})
    assert after.attempts == before.attempts
    assert len(after.checkpoints) == len(before.checkpoints)
    assert after.runs["R1"]["consumed"] == before.runs["R1"]["consumed"]
    assert fresh.iteration() in (loop.StopReason.done, loop.StopReason.boundary)
    assert (root / "local_state/holistic-count.txt").read_text() == "1"
