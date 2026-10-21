"""Explicit native evidence setup for disposable unit-test repositories."""
import json

from frutlups import ledger, render


def verification_history(root, model, item, *, changed=()):
    """Bind a round-one envelope and manifest, without bypassing their readers."""
    def reference(path, value):
        raw = value if isinstance(value, bytes) else json.dumps(value).encode()
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        return dict(path=path, sha=ledger.evidence_sha(raw))

    state = ledger.fold((), model).slices[item.id]
    frozen = render.coding_envelope(root, model, item, state)
    envelope = reference("envelope.json", frozen)
    prompt = reference("prompt.md", b"Native verification fixture.\n")
    manifest = reference("manifest.json", dict(schema="frutlups.manifest/2", slice=item.id,
                                               round=1, changed=[dict(row, round=1) for row in changed]))
    common = dict(schema=ledger.SCHEMA_V2, t="2026-09-21T00:00:00Z", by="architect", slice=item.id, round=1)
    rows = [dict(common, ev="prompt", **prompt, envelope=envelope),
            dict(common, ev="coded", changed=list(changed), manifest=manifest, result="implemented")]
    (root / "ledger.jsonl").write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows))
    ledger.validate_history(root, ledger.read(root / "ledger.jsonl"), model)
    return frozen, manifest


def grant_project(project):
    """Use the existing owned-test grant; no production admission route is enabled."""
    import copy
    import subprocess
    import yaml
    from dataclasses import replace
    from test_autonomy import NATIVE_CASES
    root, cfg, model = project
    model = replace(model, autonomy={"schema": "frutlups.autonomy/1"})
    data = yaml.safe_load((root / "roadmap.yaml").read_text("utf-8"))
    data["autonomy"] = dict(model.autonomy)
    template = copy.deepcopy(data["milestones"][0])
    data["milestones"] = []
    for milestone in model.milestones:
        value = copy.deepcopy(template)
        value.update(id=milestone.id, status=milestone.status, holistic_review=milestone.holistic_review)
        value["slices"] = [dict(copy.deepcopy(template["slices"][0]), id=item.id) for item in milestone.slices]
        data["milestones"].append(value)
    (root / "roadmap.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    row = copy.deepcopy(NATIVE_CASES["completed-holistic-history-survives-done-roadmap"]["events"][0])
    row["allowance"].update(jobs=20, seconds=300)
    row["slices"] = [item.id for item in model.milestones[0].slices]
    if not model.milestones[0].holistic_review:
        row["stop"] = model.milestones[0].slices[-1].id
    for ref in row["authority"]:
        path = root / ref["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"Owned native lifecycle qualification.\n")
        ref["sha"] = ledger.evidence_sha(path.read_bytes())
    for args in (("add", "."), ("commit", "-qm", "native test authority")):
        subprocess.run([cfg.git, "-C", str(root), *args], check=True, capture_output=True)
    (root / cfg.ledger).write_bytes(json.dumps(row).encode() + b"\n")
    ledger.validate_history(root, ledger.read(root / cfg.ledger), model, executable=cfg.git)
    return root, cfg, model


from frutlups import loop, runtime, _preflight
from frutlups.ownership import writer_lock
import time


class Controller(loop.Loop):
    """Test-only access to the existing private native transition method."""
    def __init__(self, root, cfg, model, until=None, clock=time.monotonic, *, emit=None):
        from dataclasses import replace
        from frutlups import gitws
        cfg = replace(cfg, git=gitws.selected_executable(cfg.git))
        self.root, self.cfg, self.roadmap = root, cfg, model
        self.until, self.clock, self.emit = until or cfg.until, clock, emit
        from frutlups import roadmap as roadmap_module, receipt
        events = ledger.read(root / cfg.ledger)
        state = ledger.fold(events, model)
        item = roadmap_module.next_slice(model, state)
        current = state.slices[item.id] if item else None
        self.runtime = (receipt.native_runtime(root, model,
            render.task_contract(root, model, item, current, events, executable=cfg.git), cfg)
            if current and current.envelope else runtime.resolve_runtime(root, model, git=cfg.git))
        self.started, self.jobs = clock(), 0
        self.accepted_this_run, self.target_milestone = False, None

    def iteration(self):
        with writer_lock(self.root, self.roadmap, executable=self.cfg.git):
            return self._iteration_owned()


def run_transitions(root, cfg, model, until=None, clock=time.monotonic, *, once=False, emit=None):
    runner = Controller(root, cfg, model, until, clock, emit=emit)
    with writer_lock(root, model, executable=cfg.git):
        _, _, errors = _preflight._static(root, cfg, model)
        if errors:
            if emit:
                for error in errors:
                    emit(dict(action="preflight", ok=False, detail=error))
            return loop.StopReason.preflight_failed
        for _ in range(40):
            result = runner.iteration()
            if result is not None or once:
                return result
    raise AssertionError("native test transition bound exhausted")


def probe_errors(root, cfg, model):
    """Exercise native private static/probe guards in one owned interval."""
    _, _, errors = _preflight._static(root, cfg, model)
    if errors:
        return errors
    with writer_lock(root, model, executable=cfg.git):
        _, names, errors = _preflight._static(root, cfg, model)
        if errors:
            return errors
        errors = []
        for name in names:
            try:
                error = _preflight._probe(root, cfg, name, model.memory is not None)
                if error:
                    errors.append(error)
            except _preflight._ProbeIntegrityError as exc:
                errors.append(str(exc))
                break
            except Exception as exc:
                errors.append(f"model probe failed: {name}: {exc}")
        return _preflight._errors(root, errors)


def next_grant(root, cfg, model, milestone):
    """Explicit test architect handoff after the previous native grant closes."""
    events = ledger.read(root / cfg.ledger)
    state = ledger.validate_history(root, events, model, executable=cfg.git)
    assert state.runs[state.run_head["run"]]["closed"] == "boundary"
    row = ledger._event_dict(next(e for e in events if e.ev == ledger.Ev.run_admitted))
    row.update(run="R2", previous=ledger._thaw(state.run_head), milestone=milestone.id, slices=[item.id for item in milestone.slices],
               stop=milestone.id if milestone.holistic_review else milestone.slices[-1].id)
    authority = "00_brief/next-test-grant.md"
    (root / authority).write_bytes(b"Owned explicit second milestone grant.\n")
    row["authority"] = [dict(path=authority, sha=ledger.evidence_sha((root / authority).read_bytes()))]
    with (root / cfg.ledger).open("ab") as stream:
        stream.write(json.dumps(row).encode() + b"\n")
    ledger.validate_history(root, ledger.read(root / cfg.ledger), model, executable=cfg.git)
