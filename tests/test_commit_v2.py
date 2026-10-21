"""Exact approval recovery in owned materialized ordinary/profile projects."""
import copy
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
import test_loop
from test_runtime_v2 import bound_case
from test_attempts_v2 import save
from frutlups import commit, gitws, ledger, loop, roadmap, _attempts

project = test_loop.project


def materialize(project, scope="slice", profile=True):
    root, cfg, _ = project
    model, rows, write = bound_case(root, "completed-holistic-history-survives-done-roadmap")
    data = yaml.safe_load((root / "roadmap.yaml").read_text("utf-8"))
    data["milestones"][0]["status"] = "active"
    if not profile:
        data.pop("autonomy")
    (root / "roadmap.yaml").write_text(yaml.safe_dump(data), encoding="utf-8", newline="\n")
    model = roadmap.load(root / "roadmap.yaml")
    rows = rows[:5 if scope == "slice" else 7]
    if not profile:
        rows = rows[1:]
    test_loop.git(root, "add", ".")
    test_loop.git(root, "commit", "-qm", "owned commit qualification")
    save(root, cfg, rows)
    return root, replace(cfg, commit_on_accept=True), model, rows, write


def runner(root, cfg, model):
    # Public construction is deliberately refused. Exercise the actual methods
    # on a materialized model, without a schema downgrade or alternate executor.
    result = object.__new__(loop.Loop)
    result.root, result.cfg, result.roadmap = root, cfg, model
    result.emit, result.jobs, result.accepted_this_run = None, 0, False
    return result


def approve(root, cfg, model, scope="slice"):
    seat = runner(root, cfg, model)
    events = ledger.read(root / cfg.ledger)
    state = ledger.fold(events, model)
    milestone = model.milestones[0]
    if scope == "slice":
        seat._slice(milestone.slices[0], state.slices["M001-S01"], events)
    else:
        seat._holistic(milestone, state)
    assert seat.jobs == 0
    return commit.status(root, ledger.read(root / cfg.ledger), model)


def pending(root, cfg, model, monkeypatch, scope="slice"):
    original = commit._recover
    with monkeypatch.context() as patch:
        patch.setattr(commit, "_recover", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("interrupted")))
        with pytest.raises(RuntimeError, match="interrupted"):
            approve(root, cfg, model, scope)
    assert commit._recover is original
    events = ledger.read(root / cfg.ledger)
    event = ledger._event_dict(events[-1])
    return event, json.loads((root / event["commit_intent"]["manifest"]["path"]).read_bytes())


def test_unchanged_protocol_commit_manifest_sample():
    sample = json.loads((Path(__file__).parent / "fixtures/protocol_v2.json").read_bytes())["commit_manifest"]
    assert commit._manifest(sample["text"].encode(), sample["intent"], sample["scope"]) == sample["expected"]
    assert ledger.evidence_sha(sample["text"].encode()) == sample["intent"]["manifest"]["sha"]


@pytest.mark.parametrize("scope,profile", [("slice", True), ("milestone", True), ("slice", False), ("milestone", False)])
def test_actual_approval_replay_has_one_event_and_witness(project, monkeypatch, scope, profile):
    root, cfg, model, rows, _ = materialize(project, scope, profile)
    before = ledger.fold(ledger.read(root / cfg.ledger), model)
    result = approve(root, cfg, model, scope)
    assert len(result) == 1
    assert result[0]["state"] == "completed"
    assert result[0]["scope"] == ("M001-S01" if scope == "slice" else "M001")
    saved = (root / cfg.ledger).read_bytes()
    head = gitws.head(root)
    assert len(ledger.read(root / cfg.ledger)) == len(rows) + 1
    assert not commit._marker(root).exists()
    assert approve(root, cfg, model, scope) == result
    assert (root / cfg.ledger).read_bytes() == saved
    assert gitws.head(root) == head
    after = ledger.fold(ledger.read(root / cfg.ledger), model)
    assert after.attempts == before.attempts
    if profile:
        assert after.runs["R1"]["consumed"] == before.runs["R1"]["consumed"]
    event = ledger._event_dict(ledger.read(root / cfg.ledger)[-1])
    manifest = json.loads((root / event["commit_intent"]["manifest"]["path"]).read_bytes())
    if profile:
        assert rows[0]["authority"][0]["path"] in {e["path"] for e in manifest["entries"]}
    assert test_loop.git(root, "show", "HEAD:" + cfg.ledger).stdout == saved
    if scope == "slice" and profile:
        marker = commit._marker(root)
        marker.write_bytes(json.dumps({"operation": event["commit_intent"]["id"],
                                      "parent": event["commit_intent"]["parent"]}).encode())
        original_unlink = Path.unlink
        def interrupted_unlink(path, *args, **kwargs):
            if path == marker: raise RuntimeError("archive persisted before interruption")
            return original_unlink(path, *args, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(Path, "unlink", interrupted_unlink)
            with pytest.raises(RuntimeError, match="archive persisted"):
                commit.recover(root, model)
        assert marker.exists()
        assert commit.recover(root, model) == result
        assert not marker.exists()
        assert (root / cfg.ledger).read_bytes() == saved


@pytest.mark.parametrize("scope", ["slice", "milestone"])
@pytest.mark.parametrize("phase", ["before_stage", "after_stage", "after_commit"])
def test_fresh_interpreter_interruptions_resume_only_git(project, scope, phase):
    root, cfg, model, rows, _ = materialize(project, scope)
    code = r'''
import os, sys
from pathlib import Path
import conftest
from dataclasses import replace
from frutlups import commit, gitws, roadmap
from test_commit_v2 import approve
root=Path(sys.argv[1]); phase=sys.argv[3]
cfg=replace(conftest.offline_config(),commit_on_accept=True)
model=roadmap.load(root/"roadmap.yaml")
original_stage=gitws.stage_exact
original_git=gitws._git
def stage(*args,**kwargs):
 if phase=="before_stage": os._exit(23)
 result=original_stage(*args,**kwargs)
 if phase=="after_stage": os._exit(23)
 return result
def git(*args,**kwargs):
 result=original_git(*args,**kwargs)
 if len(args)>1 and args[1]=="commit" and phase=="after_commit": os._exit(23)
 return result
gitws.stage_exact=stage;gitws._git=git
approve(root,cfg,model,sys.argv[2])
'''
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parent)}
    child = subprocess.run([sys.executable, "-B", "-c", code, str(root), scope, phase],
                           capture_output=True, env=env, timeout=35)
    assert child.returncode == 23, child.stderr.decode()
    before = (root / cfg.ledger).read_bytes()
    assert len(ledger.read(root / cfg.ledger)) == len(rows) + 1
    assert commit._marker(root).exists()
    identity = ledger.read(root / cfg.ledger)[-1].data["commit_intent"]["id"]
    if phase != "after_commit":
        with pytest.raises(ValueError, match="unresolved"):
            commit.recover(root, model)
        commit.resolve_inflight(root, model, identity, "Owned child exited 23; no child remains", "architect")
    resume = r'''
import sys
from pathlib import Path
import conftest
from dataclasses import replace
from frutlups import roadmap, gitws
from test_commit_v2 import approve
root=Path(sys.argv[1]);model=roadmap.load(root/"roadmap.yaml")
def forbidden(*args,**kwargs): raise AssertionError("repeated completed Git work")
if sys.argv[3] in ("after_stage","after_commit"): gitws.stage_exact=forbidden
assert approve(root,replace(conftest.offline_config(),commit_on_accept=True),model,sys.argv[2])[0]["state"]=="completed"
'''
    child = subprocess.run([sys.executable, "-B", "-c", resume, str(root), scope, phase],
                           capture_output=True, env=env, timeout=35)
    assert child.returncode == 0, child.stderr.decode()
    assert (root / cfg.ledger).read_bytes() == before
    assert not commit._marker(root).exists()
    events = ledger.read(root / cfg.ledger)
    assert len(commit._candidates(root, ledger._thaw(events[-1].data["commit_intent"]))) == 1
    assert ledger.fold(events, model).runs["R1"]["consumed"]["jobs"] == 0


@pytest.mark.parametrize("scope", ["slice", "milestone"])
def test_ledger_only_approval_never_mints_intent(project, scope):
    root, cfg, model, rows, _ = materialize(project, scope)
    head = gitws.head(root)
    assert approve(root, replace(cfg, commit_on_accept=False), model, scope) == []
    before = (root / cfg.ledger).read_bytes()
    assert approve(root, cfg, model, scope) == []
    assert (root / cfg.ledger).read_bytes() == before
    assert len(ledger.read(root / cfg.ledger)) == len(rows) + 1
    assert gitws.head(root) == head


def test_historical_witness_uses_blobs_and_refreshes_refs(project):
    root, cfg, model, _, _ = materialize(project)
    result = approve(root, cfg, model)
    events = ledger.read(root / cfg.ledger)
    original = result[0]["commit"]
    intent = ledger._thaw(events[-1].data["commit_intent"])
    # Legitimate descendants and dirty current payload/index do not redefine history.
    (root / "later.txt").write_bytes(b"later\n")
    test_loop.git(root, "add", "later.txt")
    test_loop.git(root, "commit", "-qm", "legitimate descendant")
    (root / intent["manifest"]["path"]).write_bytes(b"current manifest differs\n")
    (root / "later.txt").write_bytes(b"dirty index\n")
    test_loop.git(root, "add", "later.txt")
    assert commit.status(root, events, model) == result
    # A second immutable commit with the exact tree/message/parent is a duplicate.
    tree = test_loop.git(root, "rev-parse", original + "^{tree}").stdout.decode().strip()
    duplicate = test_loop.git(root, "-c", "user.name=Second Witness", "commit-tree", tree,
        "-p", intent["parent"], "-m", intent["message"] + "\n\nTemplate-Operation: " + intent["id"]).stdout.decode().strip()
    assert duplicate != original
    test_loop.git(root, "update-ref", "refs/heads/duplicate", duplicate)
    with pytest.raises(ValueError, match="duplicate commit witnesses"):
        commit.status(root, events, model)
    test_loop.git(root, "update-ref", "-d", "refs/heads/duplicate")
    assert commit.status(root, events, model) == result
    # Replacement objects must neither hide nor change the immutable witness.
    test_loop.git(root, "replace", original, intent["parent"])
    assert commit.status(root, events, model) == result
    assert test_loop.git(root, "replace", "-l").stdout.decode().strip() == original


@pytest.mark.parametrize("by,retain,allowed", [("human", True, True), ("architect", False, True),
                                             ("architect", True, False), ("frutlups", False, False)])
def test_cancellation_actor_and_index_ownership(project, monkeypatch, by, retain, allowed):
    root, cfg, model, rows, _ = materialize(project)
    event, manifest = pending(root, cfg, model, monkeypatch)
    identity = event["commit_intent"]["id"]
    before = gitws.workspace_witness(root)
    if allowed:
        commit.cancel(root, model, identity, "Owned review must be renewed", by, retain)
        state = ledger.fold(ledger.read(root / cfg.ledger), model)
        assert state.slices["M001-S01"].step == ("accepted" if retain else "fix")
        assert state.slices["M001-S01"].round == (1 if retain else 2)
        assert identity in state.cancelled
        assert commit.status(root, ledger.read(root / cfg.ledger), model)[0]["state"] == "cancelled"
    else:
        with pytest.raises(ValueError):
            commit.cancel(root, model, identity, "Owned review must be renewed", by, retain)
        assert gitws.workspace_witness(root) == before
    assert gitws.workspace_witness(root)["index"] == before["index"]


def test_pending_freezes_operations_approval_and_stop_and_preserves_marker(project, monkeypatch):
    root, cfg, model, _, _ = materialize(project)
    event, _ = pending(root, cfg, model, monkeypatch)
    saved = (root / cfg.ledger).read_bytes()
    for action in (
        lambda: ledger._append_operation(root, root / cfg.ledger, model,
            _attempts.event("attempt_started", invocation="blocked", scope="M001", round="holistic",
                            role="probe", contract="a" * 64, allowance_seconds=1, retry="initial")),
        lambda: runner(root, cfg, model)._guard(),
        lambda: ledger._append_approval(root, model, _attempts.event("accepted", slice="M001-S01", round=1)),
        lambda: ledger._append_approval(root, model, _attempts.event("stop", reason="internal", detail="blocked")),
    ):
        with pytest.raises(ValueError):
            action()
        assert (root / cfg.ledger).read_bytes() == saved
    marker = commit._marker(root)
    marker.write_bytes(b'{"operation":"foreign"}\n')
    with pytest.raises(ValueError):
        commit.resolve_inflight(root, model, event["commit_intent"]["id"], "no child", "architect")
    assert marker.read_bytes() == b'{"operation":"foreign"}\n'
    marker.write_bytes(json.dumps({"operation": event["commit_intent"]["id"],
                                 "parent": event["commit_intent"]["parent"]}).encode())
    with pytest.raises(ValueError, match="unresolved"):
        commit.recover(root, model)
    lock = root / ".git/index.lock"
    lock.write_bytes(b"owned uncertain index")
    with pytest.raises(ValueError, match="index.lock"):
        commit.resolve_inflight(root, model, event["commit_intent"]["id"], "no child", "architect")
    assert lock.read_bytes() == b"owned uncertain index"
    assert marker.exists()


@pytest.mark.parametrize("drift", ["product", "index", "policy", "ledger", "head"])
def test_pending_drift_refuses_without_repair(project, monkeypatch, drift):
    root, cfg, model, _, write = materialize(project)
    # Add a real product handoff and its exact immutable manifest/receipt reference.
    rows = [ledger._event_dict(e) for e in ledger.read(root / cfg.ledger)]
    target = root / "07_app/CONTEXT.md"
    target.write_bytes(b"approved product\n")
    change = {"path": "07_app/CONTEXT.md", "kind": "modified", "sha": ledger.evidence_sha(target.read_bytes())}
    coded = rows[2]
    coded["changed"] = [change]
    coded["manifest"] = write(coded["manifest"]["path"], dict(schema="frutlups.manifest/2",
        slice="M001-S01", round=1, changed=[dict(change, round=1)]))
    rows[3]["sha"] = write(rows[3]["receipt"], dict(schema="frutlups.receipt/2", slice="M001-S01",
        round=1, manifest=coded["manifest"], ok=True))["sha"]
    save(root, cfg, rows)
    event, _ = pending(root, cfg, model, monkeypatch)
    if drift == "product": target.write_bytes(b"unapproved product\n")
    if drift == "index":
        (root / "unrelated.txt").write_bytes(b"keep index")
        test_loop.git(root, "add", "unrelated.txt")
    if drift == "policy": model = replace(model, verification_git_boundary=("python", "-c", "pass"))
    if drift == "ledger":
        with (root / cfg.ledger).open("ab") as stream: stream.write(b"\n")
    if drift == "head": test_loop.git(root, "commit", "--allow-empty", "-qm", "unexpected")
    before = gitws.workspace_witness(root)
    with pytest.raises(ValueError): commit.recover(root, model)
    assert gitws.workspace_witness(root) == before


def test_raw_text_modes_rename_delete_and_tombstone(project, monkeypatch):
    root, cfg, model, rows, write = materialize(project)
    # Qualify the adopted manifest boundary with real Git types and literal paths.
    (root / "old.txt").write_bytes(b"rename content\n")
    (root / "delete.txt").write_bytes(b"deleted content\n")
    (root / "mode.txt").write_bytes(b"executable\n")
    (root / ".gitattributes").write_bytes(b"* text=auto eol=lf\n*.bin -text\n")
    test_loop.git(root, "add", ".")
    test_loop.git(root, "update-index", "--chmod=+x", "mode.txt")
    test_loop.git(root, "commit", "-qm", "owned types")
    test_loop.git(root, "mv", "old.txt", "new.txt")
    (root / "delete.txt").unlink()
    (root / "[literal].txt").write_bytes(b"first\r\nsecond\r\n")
    (root / "raw.bin").write_bytes(b"raw\r\nbytes\0")
    paths = ["new.txt", "delete.txt", "mode.txt", "[literal].txt", "raw.bin", "tombstone.txt"]
    from frutlups.ownership import writer_lock
    with writer_lock(root, model):
        intent = commit._request_intent(root, paths, "M001-S01", "Accept owned types", model,
                                        "05_governance/reviews/m001")
        raw = _attempts.event("accepted", slice="M001-S01", round=1, commit_intent=intent)
        ledger._append_approval(root, model, raw)
    result = commit.recover(root, model)
    assert result[0]["state"] == "completed"
    manifest = json.loads((root / intent["manifest"]["path"]).read_bytes())
    entries = {e["path"]: e for e in manifest["entries"]}
    assert entries["raw.bin"]["storage"] == "raw"
    assert entries["[literal].txt"]["storage"] == "normalized"
    assert entries["mode.txt"]["mode"] == "100755"
    assert entries["old.txt"]["state"] == "deleted"
    assert entries["delete.txt"]["state"] == "deleted"
    assert "tombstone.txt" not in entries
    assert test_loop.git(root, "show", "HEAD:raw.bin").stdout == b"raw\r\nbytes\0"
    assert test_loop.git(root, "show", "HEAD:[literal].txt").stdout == b"first\nsecond\n"


def test_hook_policy_candidate_and_raw_drift(project, monkeypatch):
    root, cfg, model, _, _ = materialize(project)
    hook = ("python", "-c", "import os; assert len(os.environ['TEMPLATE_CANDIDATE_TREE'])==40")
    model = replace(model, verification_git_boundary=hook, verification_timeout_seconds=10)
    event, _ = pending(root, cfg, model, monkeypatch)
    for altered in (replace(model, verification_git_boundary=None),
                    replace(model, verification_timeout_seconds=11),
                    replace(model, runtime={"python": "3.99"})):
        with pytest.raises(ValueError, match="policy changed"):
            commit.recover(root, altered)
    assert commit.recover(root, model)[0]["state"] == "completed"
    # Historical policy remains original, even after configuration changes.
    assert commit.status(root, ledger.read(root / cfg.ledger), replace(model, verification_git_boundary=None))[0]["state"] == "completed"
    with pytest.raises(ValueError, match="mutated"):
        commit._boundary(root, {"argv": ["python", "-c", "from pathlib import Path; Path('hook-edit').write_bytes(b'raw')"],
                               "timeout_seconds": 10, "runtime": {}}, gitws.head(root))
    assert (root / "hook-edit").read_bytes() == b"raw"


def test_spoofed_witness_and_missing_parent_are_not_completion(project, monkeypatch):
    root, cfg, model, _, _ = materialize(project)
    event, _ = pending(root, cfg, model, monkeypatch)
    intent = event["commit_intent"]
    message = intent["message"] + "\n\nTemplate-Operation: " + intent["id"]
    tree = test_loop.git(root, "rev-parse", "HEAD^{tree}").stdout.decode().strip()
    spoof = test_loop.git(root, "commit-tree", tree, "-p", intent["parent"], "-m", message).stdout.decode().strip()
    test_loop.git(root, "update-ref", "refs/heads/spoof", spoof)
    with pytest.raises(ValueError, match="missing committed evidence"):
        commit.status(root, ledger.read(root / cfg.ledger), model)
    test_loop.git(root, "update-ref", "-d", "refs/heads/spoof")
    assert commit.status(root, ledger.read(root / cfg.ledger), model)[0]["state"] == "pending"
    with pytest.raises(subprocess.CalledProcessError):
        commit._candidates(root, dict(intent, parent="f" * 40))


def test_filter_drift_refuses_staged_candidate_and_retains_uncertainty(project, monkeypatch):
    root, cfg, model, _, _ = materialize(project)
    report = next(e.data["report"] for e in ledger.read(root / cfg.ledger) if e.ev == ledger.Ev.reviewed)
    target = root / report
    original = target.read_bytes()
    # Establish a non-racy cached entry before configuring the real clean filter.
    # Changing attributes/config alone need not make Git rehash an unchanged file.
    os.utime(target, ns=(1_000_000_000, 1_000_000_000))
    (root / ".gitattributes").write_bytes((report + " filter=owned\n").encode())
    test_loop.git(root, "add", ".gitattributes", report)
    test_loop.git(root, "commit", "-qm", "owned filter policy")
    event, manifest = pending(root, cfg, model, monkeypatch)
    approved = next(e for e in manifest["entries"] if e["path"] == report)
    assert ledger.evidence_sha(original) == approved["sha"]
    assert test_loop.git(root, "show", ":" + report).stdout == original

    script = root / "local_state/filter.py"
    script.write_text(
        "import sys\nfrom pathlib import Path\n"
        "Path('local_state/filter-input').write_bytes(sys.stdin.buffer.read())\n"
        "output = b'123\\n'\n"
        "Path('local_state/filter-output').write_bytes(output)\n"
        "sys.stdout.buffer.write(output)\n", encoding="utf-8", newline="\n")
    test_loop.git(root, "config", "filter.owned.clean",
                  f'"{Path(sys.executable).as_posix()}" "{script.as_posix()}"')
    test_loop.git(root, "config", "filter.owned.required", "true")
    # --path executes the configured filter without changing the index. Bind the
    # actual selected interpreter's input/output and the expected transformed OID.
    filtered_oid = gitws._git(root, "hash-object", "--path=" + report, report,
                              executable=cfg.git).stdout.strip()
    filter_input, filter_output = root / "local_state/filter-input", root / "local_state/filter-output"
    assert filter_input.read_bytes() == original
    assert filter_output.read_bytes() == b"123\n"
    assert ledger.evidence_sha(filter_output.read_bytes()) != approved["sha"]
    assert test_loop.git(root, "rev-parse", ":" + report).stdout.strip() != filtered_oid
    filter_input.unlink()
    filter_output.unlink()
    # Force a stat mismatch deterministically, without sleeps or changing raw
    # evidence. This makes the real recovery staging step execute the filter.
    os.utime(target, ns=(2_000_000_000, 2_000_000_000))
    before = gitws.workspace_witness(root, executable=cfg.git)
    ledger_before = (root / cfg.ledger).read_bytes()
    with pytest.raises(ValueError, match="blob content differs"):
        commit.recover(root, model)
    after = gitws.workspace_witness(root, executable=cfg.git)
    assert after["head"] == before["head"]
    assert after["files"] == before["files"]
    assert after["index"] != before["index"]
    assert (root / cfg.ledger).read_bytes() == ledger_before
    assert filter_input.read_bytes() == original
    assert filter_output.read_bytes() == b"123\n"
    assert test_loop.git(root, "rev-parse", ":" + report).stdout.strip() == filtered_oid
    staged = test_loop.git(root, "show", ":" + report).stdout
    assert staged == b"123\n"
    assert ledger.evidence_sha(staged) != approved["sha"]
    marker = commit._marker_data(commit._marker(root))
    assert marker == {"operation": event["commit_intent"]["id"], "parent": before["head"]}
    assert commit.status(root, ledger.read(root / cfg.ledger), model) == [
        {"id": event["commit_intent"]["id"], "scope": "M001-S01", "state": "pending"}]


def test_unfinished_invocation_freezes_recovery_without_run_grant(project, monkeypatch):
    root, cfg, model, _, _ = materialize(project, profile=False)
    event, _ = pending(root, cfg, model, monkeypatch)
    # An externally materialized unfinished invocation is never cleared by recovery.
    rows = [ledger._event_dict(e) for e in ledger.read(root / cfg.ledger)]
    rows.append(_attempts.event("attempt_started", invocation="unfinished", scope="M001",
                              round="holistic", role="probe", contract="a" * 64,
                              allowance_seconds=1, retry="initial"))
    save(root, cfg, rows)
    before = gitws.workspace_witness(root)
    with pytest.raises(ValueError, match="unresolved invocation"):
        commit.recover(root, model)
    with pytest.raises(ValueError, match="unresolved invocation"):
        commit.cancel(root, model, event["commit_intent"]["id"], "no Git child", "human", True)
    assert gitws.workspace_witness(root) == before


def test_witness_search_has_a_hard_descendant_bound(tmp_path, monkeypatch):
    intent = {"id": "a" * 32, "parent": "b" * 40, "message": "Accept"}
    raw = (("c" * 40 + "\0" + "b" * 40 + "\0message\0\n").encode()) * 10001
    def git(root, *args, **kwargs):
        assert "--max-count=10001" in args
        return SimpleNamespace(stdout=raw)
    monkeypatch.setattr(gitws, "_git", git)
    with pytest.raises(ValueError, match="10000 descendant"):
        commit._candidates(tmp_path, intent)


def test_milestone_cancellation_requires_new_review_then_new_approval(project, monkeypatch):
    root, cfg, model, _, write = materialize(project, "milestone", False)
    first, _ = pending(root, cfg, model, monkeypatch, "milestone")
    commit.cancel(root, model, first["commit_intent"]["id"], "review again", "architect")
    with pytest.raises(ValueError):
        commit.approve(root, model, _attempts.event("milestone_done", milestone="M001"), "Close M001", True)
    ref = write("new-holistic.md", (
        "# Review: M001 round holistic\n\n## Findings\n"
        "| id | severity | disposition | summary |\n| --- | --- | --- | --- |\n"
        "\n## Closure Decision\nObjective status: achieved\nObjective evidence: Renewed human review.\n"
        "\n## Verdict\nVerdict: pass - next: Close.\n").encode())
    row = _attempts.event("holistic_reviewed", milestone="M001", report=ref["path"],
                          sha=ref["sha"], verdict="pass", open=[])
    row["by"] = "human"
    with (root / cfg.ledger).open("ab") as stream:
        stream.write(json.dumps(row).encode() + b"\n")
    result = approve(root, cfg, model, "milestone")
    assert [r["state"] for r in result] == ["cancelled", "completed"]
    events = ledger.read(root / cfg.ledger)
    approvals = [e for e in events if e.ev == ledger.Ev.milestone_done]
    assert len(approvals) == 2
    assert approvals[0].data["commit_intent"]["id"] != approvals[1].data["commit_intent"]["id"]


def append_chronological_rows(root, cfg, rows):
    """Preserve byte-bound historical approvals when extending owned history."""
    path = root / cfg.ledger
    prefix = path.read_bytes()
    suffix = b"".join(json.dumps(row).encode() + b"\n" for row in rows)
    with path.open("ab") as stream:
        stream.write(suffix)
    assert path.read_bytes() == prefix + suffix


def chronological_closure(project, profile=False, *, final="edit", historical_commits=False):
    """Owned manual handoffs with real approval of each changed slice candidate."""
    root, cfg, model, seed, write = materialize(project, profile=profile)
    data = yaml.safe_load((root / "roadmap.yaml").read_text("utf-8"))
    second = copy.deepcopy(data["milestones"][0]["slices"][0])
    second["id"] = "M001-S02"
    data["milestones"][0]["slices"].append(second)
    (root / "roadmap.yaml").write_text(yaml.safe_dump(data), encoding="utf-8", newline="\n")
    model = roadmap.load(root / "roadmap.yaml")
    rows = seed[:1] if profile else []
    if profile:
        rows[0]["slices"].append("M001-S02")
    shared = "07_app/shared.txt"
    if final != "tombstone":
        write(shared, b"initial\n")
    write("07_app/rename-origin.txt", b"rename retained bytes\n")
    test_loop.git(root, "add", ".")
    test_loop.git(root, "commit", "-qm", "owned chronological baseline")
    save(root, cfg, rows)
    frozen = ledger.read_reference(root, next(r["envelope"] for r in seed if r["ev"] == "prompt"))

    def handoff(sid, round_no, edits, *, accept=True):
        history = ledger.read(root / cfg.ledger)
        prefix = (root / cfg.ledger).read_bytes()
        rows = []
        if round_no > 1:
            rows.append(dict(_attempts.event("reopened", slice=sid, round=round_no,
                                             reason="Explicit owned correction"), by="human"))
        stem = f"05_governance/reviews/m001/{sid}_r{round_no}_chronology"
        envelope = write(stem + "_envelope.json", dict(frozen, slice=sid, round=round_no))
        prompt = write(stem + "_prompt.md", b"Owned correction contract\n")
        rows.append(_attempts.event("prompt", slice=sid, round=round_no, **prompt, envelope=envelope))
        changed = []
        for path, body in edits.items():
            target = root / path
            old = target.read_bytes() if target.exists() else b"prior tombstone\n"
            kind = "modified" if target.exists() else "added"
            if body is None:
                target.unlink(missing_ok=True)
                kind = "deleted"
            else:
                write(path, body)
            changed.append(dict(path=path, kind=kind, sha=ledger.evidence_sha(old if body is None else body)))
        cumulative = {r["path"]: dict(r, round=e.round) for e in history
                      if e.ev == ledger.Ev.coded and e.slice == sid for r in e.data["changed"]}
        cumulative.update({r["path"]: dict(r, round=round_no) for r in changed})
        manifest = write(stem + "_manifest.json", dict(schema="frutlups.manifest/2", slice=sid,
                         round=round_no, changed=[cumulative[p] for p in sorted(cumulative)]))
        rows.append(_attempts.event("coded", slice=sid, round=round_no, changed=changed,
                                    manifest=manifest, result="implemented"))
        receipt = write(stem + "_receipt.json", dict(schema="frutlups.receipt/2", slice=sid,
                        round=round_no, manifest=manifest, ok=True))
        rows.append(_attempts.event("verified", slice=sid, round=round_no,
                                    receipt=receipt["path"], sha=receipt["sha"], ok=True))
        report = write(stem + "_review.md", (
            f"# Review: {sid} round {round_no}\n\n## Findings\n"
            "| id | severity | disposition | summary |\n| --- | --- | --- | --- |\n"
            "\n## Closure Decision\nObjective status: achieved\nObjective evidence: Owned fixture.\n"
            "\n## Verdict\nVerdict: pass - next: Accept.\n").encode())
        rows.append(_attempts.event("reviewed", slice=sid, round=round_no,
                                    report=report["path"], sha=report["sha"], verdict="pass", open=[]))
        append_chronological_rows(root, cfg, rows)
        ledger.validate_history(root, ledger.read(root / cfg.ledger), model)
        if accept:
            commit.approve(root, model, _attempts.event("accepted", slice=sid, round=round_no),
                           "Accept owned chronological candidate", historical_commits, executable=cfg.git)
        assert (root / cfg.ledger).read_bytes().startswith(prefix)
        return manifest

    handoff("M001-S01", 1, {shared: b"first\n"})
    handoff("M001-S02", 1, {shared: None if final == "recreate" else b"second\n"})
    latest = None if final in ("delete", "tombstone") else b"latest correction\n"
    handoff("M001-S01", 2, {shared: latest})
    return root, cfg, model, write, handoff, shared, latest


def record_chronological_close(root, cfg, model, write):
    ref = write("05_governance/reviews/m001/chronology_holistic.md", (
        "# Review: M001 round holistic\n\n## Findings\n"
        "| id | severity | disposition | summary |\n| --- | --- | --- | --- |\n"
        "\n## Closure Decision\nObjective status: achieved\nObjective evidence: Latest recorded identities.\n"
        "\n## Verdict\nVerdict: pass - next: Close.\n").encode())
    append_chronological_rows(root, cfg, [dict(_attempts.event(
        "holistic_reviewed", milestone="M001", report=ref["path"],
        sha=ref["sha"], verdict="pass", open=[]), by="human")])
    return _attempts.event("milestone_done", milestone="M001", holistic_report=ref["path"])


@pytest.mark.parametrize("profile,close_commit", [(False, False), (False, True), (True, False), (True, True)])
def test_chronological_milestone_approval_and_replay(project, profile, close_commit):
    root, cfg, model, write, _, shared, latest = chronological_closure(
        project, profile, historical_commits=close_commit)
    raw = record_chronological_close(root, cfg, model, write)
    events = ledger.read(root / cfg.ledger)
    before = ledger.validate_history(root, events, model)
    assert list(before.slices) == ["M001-S01", "M001-S02"]
    assert before.slices["M001-S01"].round == 2
    assert before.slices["M001-S02"].round == 1
    originals = {e.data["manifest"]["path"]: (root / e.data["manifest"]["path"]).read_bytes()
                 for e in events if e.ev == ledger.Ev.coded}
    historical = commit.status(root, events, model)
    head = gitws.head(root)
    result = commit.approve(root, model, raw, "Close M001", close_commit, executable=cfg.git)
    saved = (root / cfg.ledger).read_bytes()
    closed_head = gitws.head(root)
    closed = ledger.validate_history(root, ledger.read(root / cfg.ledger), model)
    assert closed.attempts == before.attempts == {}
    if profile:
        assert before.runs["R1"]["closed"] is None
        assert closed.runs["R1"]["closed"] == "boundary"
        assert dict(closed.runs["R1"]) == dict(before.runs["R1"], closed="boundary")
        assert closed.runs["R1"]["consumed"] == before.runs["R1"]["consumed"]
    else:
        assert closed.runs == before.runs == {}
    closed_witnesses = commit.status(root, ledger.read(root / cfg.ledger), model)
    assert sum(e.ev == ledger.Ev.milestone_done for e in ledger.read(root / cfg.ledger)) == 1
    assert (root / shared).read_bytes() == latest
    if close_commit:
        assert result == closed_head
        assert closed_head != head
        assert test_loop.git(root, "show", "HEAD:" + shared).stdout == latest
    else:
        assert result is None
        assert closed_head == head
    assert commit.approve(root, model, raw, "Close M001", close_commit, executable=cfg.git) == result
    recovered = commit.recover(root, model, executable=cfg.git)
    assert recovered == closed_witnesses
    assert recovered[:len(historical)] == historical
    assert len(recovered) == len(historical) + int(close_commit)
    assert (root / cfg.ledger).read_bytes() == saved
    assert gitws.head(root) == closed_head
    after = ledger.validate_history(root, ledger.read(root / cfg.ledger), model)
    assert after == closed
    for path, original in originals.items():
        assert (root / path).read_bytes() == original
        if close_commit:
            assert test_loop.git(root, "show", "HEAD:" + path).stdout == original


def test_closure_ignores_unchanged_carried_rows_but_slice_approval_does_not(project):
    root, cfg, model, write, handoff, shared, latest = chronological_closure(project)
    # A later cumulative handoff retains S02's older shared-path row. Materialize
    # historical manual acceptance only after proving the active runner refuses it.
    ref = handoff("M001-S02", 2, {"07_app/other.txt": b"later independent edit\n"}, accept=False)
    carried = ledger.manifest(root, ref)
    old = next(row for row in carried["changed"] if row["path"] == shared)
    assert old["round"] == 1
    assert old["sha"] == ledger.evidence_sha(b"second\n")
    before = gitws.workspace_witness(root)
    with pytest.raises(ValueError, match="active product drift: 07_app/shared.txt"):
        commit.approve(root, model, _attempts.event("accepted", slice="M001-S02", round=2),
                       "Reject stale active candidate", False)
    assert gitws.workspace_witness(root) == before
    append_chronological_rows(root, cfg, [dict(
        _attempts.event("accepted", slice="M001-S02", round=2), by="human")])
    raw = record_chronological_close(root, cfg, model, write)
    original = (root / ref["path"]).read_bytes()
    commit.approve(root, model, raw, "Close per-path chronology", True)
    assert test_loop.git(root, "show", "HEAD:" + shared).stdout == latest
    assert test_loop.git(root, "show", "HEAD:07_app/other.txt").stdout == b"later independent edit\n"
    assert (root / ref["path"]).read_bytes() == original


@pytest.mark.parametrize("final", ["delete", "recreate", "tombstone"])
def test_chronological_delete_recreation_and_exact_rename_payload(project, final):
    root, cfg, model, write, handoff, shared, latest = chronological_closure(project, final=final)
    # A recorded rename destination still selects its origin through the existing
    # exact Git payload machinery; no unrelated untracked file may be swept in.
    test_loop.git(root, "mv", "07_app/rename-origin.txt", "07_app/rename-destination.txt")
    handoff("M001-S01", 3, {"07_app/rename-origin.txt": None,
                            "07_app/rename-destination.txt": b"rename retained bytes\n"})
    write("unrelated-untracked.txt", b"not approved\n")
    raw = record_chronological_close(root, cfg, model, write)
    commit.approve(root, model, raw, "Close exact latest paths", True)
    events = ledger.read(root / cfg.ledger)
    intent = ledger._thaw(events[-1].data["commit_intent"])
    entries = {row["path"]: row for row in ledger.read_reference(root, intent["manifest"])["entries"]}
    assert entries["07_app/rename-origin.txt"]["state"] == "deleted"
    assert entries["07_app/rename-destination.txt"]["state"] == "file"
    assert "unrelated-untracked.txt" not in entries
    assert test_loop.git(root, "show", "HEAD:07_app/rename-destination.txt").stdout == b"rename retained bytes\n"
    tracked = test_loop.git(root, "ls-tree", "-r", "--name-only", "HEAD").stdout.decode().splitlines()
    assert "07_app/rename-origin.txt" not in tracked
    assert "unrelated-untracked.txt" not in tracked
    if final == "recreate":
        assert entries[shared]["state"] == "file"
        assert test_loop.git(root, "show", "HEAD:" + shared).stdout == latest
    else:
        assert shared not in tracked
        assert not (root / shared).exists()
        if final == "delete":
            assert entries[shared]["state"] == "deleted"
        else:
            assert shared not in entries


@pytest.mark.parametrize("drift", ["product", "missing_manifest", "tampered_manifest", "staged"])
def test_chronological_close_refusal_preserves_git_and_ledger(project, drift):
    root, cfg, model, write, _, shared, _ = chronological_closure(project)
    raw = record_chronological_close(root, cfg, model, write)
    if drift == "product":
        write(shared, b"second\n")  # Matches the stale slice, not the latest change.
    elif drift in ("missing_manifest", "tampered_manifest"):
        event = next(e for e in ledger.read(root / cfg.ledger) if e.ev == ledger.Ev.coded)
        path = root / event.data["manifest"]["path"]
        if drift == "missing_manifest":
            path.unlink()
        else:
            path.write_bytes(b"tampered immutable manifest\n")
    else:
        write("unrelated-staged.txt", b"retain staged ownership\n")
        test_loop.git(root, "add", "unrelated-staged.txt")
    before = gitws.workspace_witness(root)
    with pytest.raises((ValueError, OSError)):
        commit.approve(root, model, raw, "Refuse changed closure", True)
    assert gitws.workspace_witness(root) == before
