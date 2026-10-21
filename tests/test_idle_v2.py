"""Idle component contract on owned native histories; public execution stays shut."""
import copy
import json
from dataclasses import replace

import pytest
import yaml

import test_autonomy
import test_loop
import test_milestones_v2 as milestones
import test_commit_v2 as commits
from _native_support import Controller
from frutlups import _attempts, _preflight, cli, commit, gitws, ledger, loop, receipt, render, roadmap
from frutlups.config import Config
from frutlups.ownership import writer_lock

project = test_loop.project


def save(root, cfg, rows):
    (root / cfg.ledger).write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows))


def plan(root, *, statuses=None, holistic=None, second=False):
    path = root / "roadmap.yaml"
    data = yaml.safe_load(path.read_text("utf-8"))
    if second:
        other = copy.deepcopy(data["milestones"][0])
        other["id"] = "M002"
        other["slices"][0]["id"] = "M002-S01"
        data["milestones"].append(other)
    if statuses:
        for milestone, status in zip(data["milestones"], statuses, strict=True):
            milestone["status"] = status
    if holistic is not None:
        for milestone in data["milestones"]:
            milestone["holistic_review"] = holistic
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return roadmap.load(path)


def accepted(project, profile=False):
    root, cfg, model, write = milestones.materialize(project, profile)
    model = plan(root, holistic=False)
    rows = [ledger._event_dict(e) for e in ledger.read(root / cfg.ledger)]
    if profile:
        rows[0]["stop"] = "M001-S01"
        # A settled earlier probe retains its exact lifetime charge at manual completion.
        rows[1:1] = [
            dict(_attempts.event("attempt_started", invocation="idle-probe", scope="M001-S01",
                 round=1, role="probe", contract="a" * 64, allowance_seconds=10, retry="initial"), by="human"),
            dict(_attempts.event("attempt_finished", invocation="idle-probe", status="completed",
                 usage={"completeness": "partial", "secs": 2.2}), by="human"),
        ]
    product = write("07_app/result.txt", b"accepted product\n")
    coded = next(row for row in rows if row["ev"] == "coded")
    coded["changed"] = [dict(product, kind="added")]
    value = ledger.read_reference(root, coded["manifest"])
    value["changed"] = [dict(product, kind="added", round=1)]
    coded["manifest"] = write(coded["manifest"]["path"], value)
    verified = next(row for row in rows if row["ev"] == "verified")
    value = json.loads((root / verified["receipt"]).read_bytes())
    value["manifest"] = coded["manifest"]
    verified["sha"] = write(verified["receipt"], value)["sha"]
    save(root, cfg, rows)
    ledger.validate_history(root, ledger.read(root / cfg.ledger), model)
    return root, cfg, model, rows, write


def blocker(write, sid):
    return write("blocked-outcome.json", dict(schema="frutlups.outcome/2", slice=sid, round=1,
                 invocation="owned-manual", outcome="blocked_authority", requirement="Owner evidence",
                 reason="Evidence pending", actor="architect", action="Bind evidence", remaining=[],
                 evidence=[], authority=[]))


def snapshot(root, cfg):
    # Semantic staged entries, not physical index bytes; no local_state traversal.
    names = test_loop.git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").stdout
    files = {name: (root / name.decode()).read_bytes() if (root / name.decode()).is_file() else None
             for name in names.split(b"\0") if name}
    return (files, test_loop.git(root, "rev-parse", "HEAD").stdout,
            test_loop.git(root, "ls-files", "--stage", "-v", "-z").stdout,
            (root / cfg.ledger).read_bytes())


@pytest.fixture
def no_dispatch(monkeypatch):
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(True)
        pytest.fail("idle/refusal attempted a probe, job, seat or verifier")
    for owner, name in ((_preflight, "_probe"), (loop.Loop, "_job"),
                        (loop.Loop, "_call"), (loop.Loop, "_verifying_slice"),
                        (receipt, "run"), (_preflight.pi.PiSeat, "run"),
                        (_preflight.claude.ClaudeSeat, "run")):
        monkeypatch.setattr(owner, name, forbidden)
    yield calls
    assert calls == []


def observe(root, cfg, model, *, target=None):
    notices = []
    runner = Controller(root, cfg, model, until="milestone" if target else "roadmap", emit=notices.append)
    runner.target_milestone = target
    before = snapshot(root, cfg)
    result = runner.iteration()
    assert snapshot(root, cfg) == before
    assert runner.jobs == 0
    assert not (root / "local_state/frutlups/jobs").exists()
    return result, notices[-1]["detail"]


@pytest.mark.parametrize("statuses", [("planned",), ("done",), ("done", "planned")])
def test_empty_frontier_is_idle_not_completion(project, monkeypatch, no_dispatch, statuses):
    root, cfg, _ = project
    model = plan(root, statuses=statuses, second=len(statuses) == 2)
    state = ledger.validate_history(root, ledger.read(root / cfg.ledger), model)
    assert roadmap.next_slice(model, state) is None
    assert roadmap.next_milestone(model, state) is None
    assert all(not loop.Loop._complete(None, m, state) for m in model.milestones)
    assert all(m.status in roadmap.render_markdown(model) for m in model.milestones)
    monkeypatch.setattr(Config, "load", lambda root: cfg)
    before = snapshot(root, cfg)
    output = []
    cli._status(root, False, False, output.append)
    expected = ["\n".join([*(f"{m.slices[0].id} r1 unstarted" for m in model.milestones),
                             "no active milestone", "next: none"])]
    expected += [f"warning: {m.id} declared done without ledger closure" for m in model.milestones if m.status == "done"]
    assert output == expected
    assert snapshot(root, cfg) == before
    assert _preflight._static(root, cfg, model)[1] == []
    assert observe(root, cfg, model) == (loop.StopReason.needs_specification,
                                       "architect must admit the next milestone")


@pytest.mark.parametrize("status", ["active", "planned", "done"])
def test_repeat_completion_is_label_independent_and_nonwriting(project, no_dispatch, status):
    root, cfg, model, _, _ = accepted(project)
    model = plan(root, statuses=(status,))
    before = ledger.validate_history(root, ledger.read(root / cfg.ledger), model)
    for _ in range(2):
        assert observe(root, cfg, model, target="M001") == (
            loop.StopReason.boundary, "milestone completed; " +
            ("roadmap already done" if status == "done" else "mark roadmap milestone done separately"))
        assert ledger.validate_history(root, ledger.read(root / cfg.ledger), model) == before
    assert not before.attempts
    assert not before.runs
    assert not before.milestones_done


def test_last_active_completion_preserves_planned_block_and_separate_edit(project, no_dispatch):
    root, cfg, _, rows, write = accepted(project)
    model = plan(root, statuses=("active", "planned"), second=True)
    # Earlier planned blocked history has no autonomous admission.
    data = yaml.safe_load((root / "roadmap.yaml").read_text("utf-8"))
    data["milestones"].reverse()
    (root / "roadmap.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    model = roadmap.load(root / "roadmap.yaml")
    prompt = next(row for row in rows if row["ev"] == "prompt")
    frozen = ledger.read_reference(root, prompt["envelope"])
    env = write("blocked-envelope.json", dict(frozen, slice="M002-S01"))
    pref = write("blocked-prompt.md", b"Owned blocked work\n")
    manifest = write("blocked-manifest.json", dict(schema="frutlups.manifest/2", slice="M002-S01", round=1, changed=[]))
    rows += [dict(_attempts.event("prompt", slice="M002-S01", round=1, **pref, envelope=env), by="human"),
             dict(_attempts.event("coded", slice="M002-S01", round=1, changed=[], manifest=manifest,
                  result="blocked_authority", outcome=blocker(write, "M002-S01")), by="human")]
    save(root, cfg, rows)
    state = ledger.validate_history(root, ledger.read(root / cfg.ledger), model)
    assert state.slices["M002-S01"].step == "blocked"
    assert observe(root, cfg, model, target="M001")[0] == loop.StopReason.boundary
    before = snapshot(root, cfg)
    model = plan(root, statuses=("planned", "done"))
    (root / "docs/roadmap.md").write_text(roadmap.render_markdown(model), encoding="utf-8")
    after = snapshot(root, cfg)
    assert {p for p in before[0].keys() | after[0].keys() if before[0].get(p) != after[0].get(p)} == {
        b"roadmap.yaml", b"docs/roadmap.md"}
    assert before[1:] == after[1:]
    assert ledger.validate_history(root, ledger.read(root / cfg.ledger), model) == state
    assert observe(root, cfg, model, target="M001")[0] == loop.StopReason.boundary


def test_reopen_priority_and_explicit_selection_do_not_activate_plan(project, no_dispatch):
    root, cfg, _, rows, _ = accepted(project)
    rows.append(dict(_attempts.event("reopened", slice="M001-S01", round=2, reason="Owner correction"), by="human"))
    save(root, cfg, rows)
    model = plan(root, statuses=("planned", "planned"), second=True)
    before = snapshot(root, cfg)
    state = ledger.validate_history(root, ledger.read(root / cfg.ledger), model)
    assert roadmap.next_slice(model, state).id == "M001-S01"
    assert roadmap.next_milestone(model, state).id == "M001"
    item = roadmap.slice_by_id(model, "M001-S01")
    frozen = render.coding_envelope(root, model, item, state.slices[item.id])
    assert frozen["slice"] == "M001-S01"
    assert frozen["round"] == 2
    assert ledger.status_text(model, state) == "M001-S01 r2 fix\nM002-S01 r1 unstarted\nno active milestone\nnext: M001-S01"
    assert state.run_head is None
    assert not state.attempts
    assert snapshot(root, cfg) == before


@pytest.mark.parametrize("step", ["unstarted", "coding", "verifying", "reviewing", "accept_pending", "blocked", "fix"])
def test_done_label_does_not_complete_nonaccepted_state(project, monkeypatch, no_dispatch, step):
    root, cfg, _, rows, write = accepted(project)
    count = {"unstarted": 0, "coding": 1, "verifying": 2, "reviewing": 3,
             "accept_pending": 4, "blocked": 2, "fix": 3}[step]
    rows = rows[:count]
    if step == "blocked":
        rows[-1].update(result="blocked_authority", outcome=blocker(write, "M001-S01"))
    if step == "fix":
        rows[-1]["ok"] = False
        value = json.loads((root / rows[-1]["receipt"]).read_bytes())
        value["ok"] = False
        rows[-1]["sha"] = write(rows[-1]["receipt"], value)["sha"]
    save(root, cfg, rows)
    model = plan(root, statuses=("done",))
    state = ledger.validate_history(root, ledger.read(root / cfg.ledger), model)
    assert state.slices["M001-S01"].step == step
    assert not loop.Loop._complete(None, model.milestones[0], state)
    monkeypatch.setattr(Config, "load", lambda root: cfg)
    output = []
    cli._status(root, False, False, output.append)
    assert output[-1] == "warning: M001 declared done without ledger closure"
    assert observe(root, cfg, model)[0] == loop.StopReason.needs_specification


def test_bad_bound_evidence_and_product_drift_refuse_without_effects(project, no_dispatch):
    root, cfg, model, rows, _ = accepted(project)
    report = root / next(row["report"] for row in rows if row["ev"] == "reviewed")
    saved = report.read_bytes()
    report.write_bytes(saved + b"unrecorded edit\n")
    result, detail = observe(root, cfg, model, target="M001")
    assert result == loop.StopReason.internal
    assert detail == ("LedgerError: reviewed: immutable evidence drift: " +
                      report.relative_to(root).as_posix() + "; owner must inspect")
    report.write_bytes(saved)
    (root / "07_app/result.txt").write_bytes(b"unrecorded product drift\n")
    assert observe(root, cfg, model, target="M001") == (
        loop.StopReason.internal, "LedgerError: drift: 07_app/result.txt; owner must inspect")


def test_housekeeping_later_accepted_owner_and_committed_hotfix(project, no_dispatch):
    root, cfg, model, rows, write = accepted(project)
    assert observe(root, cfg, model, target="M001")[0] == loop.StopReason.boundary
    test_loop.git(root, "add", ".")
    test_loop.git(root, "commit", "-qm", "owned housekeeping")
    assert observe(root, cfg, model, target="M001")[0] == loop.StopReason.boundary
    # A later accepted scope owns the same product identity in ledger order.
    model = plan(root, statuses=("done", "active"), second=True)
    later = copy.deepcopy(rows)
    manifest = None
    for row in later:
        row["slice"] = "M002-S01"
        if row["ev"] == "prompt":
            value = ledger.read_reference(root, row["envelope"])
            row["envelope"] = write("later-envelope.json", dict(value, slice="M002-S01"))
            row.update(write("later-prompt.md", b"Later accepted scope\n"))
        elif row["ev"] == "coded":
            product = write("07_app/result.txt", b"later accepted product\n")
            row["changed"] = [dict(product, kind="added")]
            manifest = write("later-manifest.json", dict(schema="frutlups.manifest/2", slice="M002-S01", round=1,
                             changed=[dict(product, kind="added", round=1)]))
            row["manifest"] = manifest
        elif row["ev"] == "verified":
            value = json.loads((root / row["receipt"]).read_bytes())
            ref = write("later-receipt.json", dict(value, slice="M002-S01", manifest=manifest))
            row.update(receipt=ref["path"], sha=ref["sha"])
        elif row["ev"] == "reviewed":
            text = (root / row["report"]).read_text("utf-8").replace("M001-S01", "M002-S01")
            ref = write("later-review.md", text.encode())
            row.update(report=ref["path"], sha=ref["sha"])
    save(root, cfg, rows + later)
    assert observe(root, cfg, model, target="M001")[0] == loop.StopReason.boundary
    (root / "07_app/result.txt").write_bytes(b"committed owner hotfix\n")
    test_loop.git(root, "add", ".")
    test_loop.git(root, "commit", "-qm", "owned hotfix")
    assert observe(root, cfg, model, target="M001")[0] == loop.StopReason.boundary


def test_done_profile_replay_and_manual_completion_preserve_accounting(project, monkeypatch, no_dispatch):
    root, cfg, _, rows, _ = accepted(project, profile=True)
    model = plan(root, statuses=("done", "planned"), second=True)
    events = ledger.read(root / cfg.ledger)
    state = ledger.validate_history(root, events, model)
    assert state.runs["R1"]["closed"] == "boundary"
    assert dict(state.runs["R1"]["consumed"]) == dict(jobs=1, seconds=3, transport_retries=0, format_retries=0)
    assert dict(state.runs["R1"]["remaining"]) == dict(jobs=19, seconds=297, transport_retries=1, format_retries=1)
    assert roadmap.next_slice(model, state) is None
    assert roadmap.next_milestone(model, state) is None
    for _ in range(2):
        assert observe(root, cfg, model, target="M001")[0] == loop.StopReason.boundary
        assert ledger.validate_history(root, ledger.read(root / cfg.ledger), model) == state
    with pytest.raises(ValueError, match="^new admission requires active milestone$"):
        _preflight._profile_gate(model, state, [])
    monkeypatch.setattr(Config, "load", lambda root: cfg)
    before = snapshot(root, cfg)
    output = []
    cli._status(root, False, False, output.append)
    assert output == ["M001-S01 r1 accepted\nM002-S01 r1 unstarted\nno active milestone\nnext: none",
                      "run R1 revision=0 stop=M001-S01 state=boundary remaining jobs=19 transport_retries=1 format_retries=1 seconds=297"]
    assert snapshot(root, cfg) == before


@pytest.mark.parametrize("name", test_autonomy.IDLE_NAMES[:-1])
def test_pinned_invalid_idle_histories_refuse_real_readers_before_effects(project, monkeypatch, no_dispatch, name):
    root, cfg, _ = project
    case = next(c for c in test_autonomy.IDLE_CASES if c["name"] == name)
    model = test_autonomy.case_roadmap(case, root)
    save(root, cfg, case["events"])
    before = snapshot(root, cfg)
    monkeypatch.setattr(Config, "load", lambda root: cfg)
    for read in (lambda: ledger.validate_history(root, ledger.read(root / cfg.ledger), model),
                 lambda: cli._status(root, False, False),
                 lambda: commit.require_idle(root, ledger.read(root / cfg.ledger), model)):
        with pytest.raises(ledger.LedgerError) as caught:
            read()
        assert str(caught.value) == case["error"]
        assert snapshot(root, cfg) == before
    assert case["error"] in _preflight._static(root, cfg, model)[2]
    assert not (root / "local_state/frutlups/jobs").exists()


def test_unfinished_invocation_precedes_target_and_approval_flags(project, monkeypatch, no_dispatch):
    root, cfg, _, rows, _ = accepted(project)
    model = plan(root, statuses=("done",))
    assert observe(root, cfg, model, target="M999") == (
        loop.StopReason.internal, "LedgerError: unknown milestone: M999; choose a roadmap milestone; owner must inspect")
    rows.append(dict(_attempts.event("attempt_started", invocation="unknown-child", scope="M001-S01", round=1,
                 role="probe", contract="b" * 64, allowance_seconds=10, retry="initial"), by="human"))
    save(root, cfg, rows)
    assert observe(root, cfg, model, target="M999") == (
        loop.StopReason.internal, "ValueError: unresolved invocation prevents new work; owner must inspect")
    before = snapshot(root, cfg)
    for flag in (False, True):
        with pytest.raises(ValueError, match="^unresolved invocation prevents approval or recovery$"):
            commit.approve(root, model, _attempts.event("accepted", slice="M999-S01", round=1), "Unknown", flag)
        assert snapshot(root, cfg) == before
    monkeypatch.setattr(Config, "load", lambda root: cfg)
    output = []
    cli._status(root, False, False, output.append)
    assert output == ["M001-S01 r1 accepted\nno active milestone\nnext: none", "unresolved invocation: unknown-child"]
    assert snapshot(root, cfg) == before


def test_pending_intent_precedes_unknown_approval_and_commit_flags(project, monkeypatch, no_dispatch):
    root, cfg, model, _, _ = commits.materialize(project, profile=False)
    event, _ = commits.pending(root, cfg, model, monkeypatch)
    model = plan(root, statuses=("planned",))
    before = snapshot(root, cfg)
    state = ledger.validate_history(root, ledger.read(root / cfg.ledger), model)
    assert state.slices["M001-S01"].step == "accepted"
    for flag in (False, True):
        with pytest.raises(ValueError, match="^commit pending; explicit recovery or cancellation required$"):
            commit.approve(root, model, _attempts.event("accepted", slice="M999-S01", round=1), "Unknown", flag)
        assert snapshot(root, cfg) == before
    monkeypatch.setattr(Config, "load", lambda root: cfg)
    output = []
    cli._status(root, False, False, output.append)
    assert output == ["M001-S01 r1 accepted\nno active milestone\nnext: none",
                      f"commit {event['commit_intent']['id']}: pending"]
    assert snapshot(root, cfg) == before


def test_no_active_holistic_close_pending_stays_visible(project, monkeypatch, no_dispatch):
    root, cfg, model, write = milestones.materialize(project)
    model = plan(root, statuses=("done",))
    ref = write("05_governance/reviews/m001/pending-holistic.md", milestones.report().encode())
    milestones.append(root, cfg, dict(_attempts.event("holistic_reviewed", milestone="M001", report=ref["path"],
                                                    sha=ref["sha"], verdict="pass", open=[]), by="human"))
    state = ledger.validate_history(root, ledger.read(root / cfg.ledger), model)
    assert state.holistic["M001"]["step"] == "close_pending"
    assert not loop.Loop._complete(None, model.milestones[0], state)
    monkeypatch.setattr(Config, "load", lambda root: cfg)
    before = snapshot(root, cfg)
    output = []
    cli._status(root, False, False, output.append)
    assert output == ["M001-S01 r1 accepted\nno active milestone\nnext: none",
                      "M001 holistic: close_pending", "warning: M001 declared done without ledger closure"]
    assert observe(root, cfg, model)[0] == loop.StopReason.needs_specification
    assert snapshot(root, cfg) == before


@pytest.mark.parametrize("failure", ["incomplete", "holistic", "unsettled"])
def test_bound_done_history_requires_complete_settled_milestone(project, monkeypatch, no_dispatch, failure):
    root, cfg, _, rows, _ = accepted(project, profile=True)
    if failure == "incomplete":
        rows.pop()
    elif failure == "unsettled":
        rows = [r for r in rows if r["ev"] != "attempt_finished"]
    save(root, cfg, rows)
    model = plan(root, statuses=("done",), holistic=failure == "holistic")
    before = snapshot(root, cfg)
    monkeypatch.setattr(Config, "load", lambda root: cfg)
    message = "inactive run history requires completed milestone and settled attempts"
    for read in (lambda: ledger.validate_history(root, ledger.read(root / cfg.ledger), model),
                 lambda: cli._status(root, False, False)):
        with pytest.raises(ledger.LedgerError) as caught:
            read()
        assert str(caught.value) == message
        assert snapshot(root, cfg) == before
    # Relabelling back to active does not erase prior charges or reservations.
    model = plan(root, statuses=("active",))
    state = ledger.validate_history(root, ledger.read(root / cfg.ledger), model)
    assert state.runs["R1"]["consumed"]["jobs"] == 1
    assert state.runs["R1"]["consumed"]["seconds"] == (10 if failure == "unsettled" else 3)
    assert (state.attempts["idle-probe"]["finish"] is None) == (failure == "unsettled")


# Public idle outcomes: (status, profile) -> (stop, detail); none probes or dispatches.
IDLE_PUBLIC = {
    ("planned", False): (loop.StopReason.needs_specification, "architect must admit the next milestone"),
    ("done", False): (loop.StopReason.needs_specification, "architect must admit the next milestone"),
    ("planned", True): (loop.StopReason.internal, "LedgerError: profile requires current grant; owner must inspect"),
    ("done", True): (loop.StopReason.internal, "LedgerError: profile requires current grant; owner must inspect"),
}


@pytest.mark.parametrize("status", ["planned", "done"])
@pytest.mark.parametrize("profile", [False, True])
def test_idle_public_entry_points_remain_disabled(project, monkeypatch, no_dispatch, status, profile):
    root, cfg, _ = project
    model = plan(root, statuses=(status,))
    if profile:
        data = yaml.safe_load((root / "roadmap.yaml").read_text("utf-8"))
        data["autonomy"] = {"schema": "frutlups.autonomy/1"}
        (root / "roadmap.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
        model = roadmap.load(root / "roadmap.yaml")
    # Idle authority belongs on disk before the public entry points read it.
    test_loop.git(root, "add", "roadmap.yaml")
    test_loop.git(root, "commit", "-qm", "owned idle authority")
    monkeypatch.setattr(Config, "load", lambda root: cfg)
    before = snapshot(root, cfg)
    # Idle public execution has nothing to probe or dispatch.
    assert _preflight.prepare(root, cfg, model)[2] == ()
    notices = []
    expected = IDLE_PUBLIC[(status, profile)]
    assert loop.run(root, cfg, model, emit=notices.append) == expected[0]
    assert notices[-1]["detail"] == expected[1]
    runner = loop.Loop(root, cfg, model, emit=notices.append)
    assert runner.iteration() == expected[0]
    assert notices[-1]["detail"] == expected[1]
    with pytest.raises(ValueError, match="/2 ledger append is not enabled"):
        runner.append("note", text="No idle mutation")
    with pytest.raises(ValueError, match="/2 ledger append is not enabled"):
        ledger.append(root / cfg.ledger, _attempts.event("note", text="No idle mutation"), model)
    assert snapshot(root, cfg) == before
    assert not (root / "local_state/frutlups/jobs").exists()
