"""Live Git topology observations required by the idle guard."""

import os
import json
import shutil
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from frutlups import commit, gitws, ledger
import test_commit_v2 as commits
from test_gitws import _repository

project = commits.project


def test_idle_guard_observes_gitfile_redirect_between_marker_checks(tmp_path, monkeypatch):
    """The writer lock does not prevent a linked worktree's gitfile changing."""
    executable = shutil.which("git")
    assert executable is not None
    roots = [tmp_path / name for name in ("one", "two")]
    worktrees = [tmp_path / name for name in ("left", "rght")]
    for root, worktree in zip(roots, worktrees):
        root.mkdir()
        for args in (
            ("init", "-q"),
            ("config", "user.name", "Fixture"),
            ("config", "user.email", "fixture@example.invalid"),
            ("commit", "--allow-empty", "-qm", "fixture"),
            ("worktree", "add", "--detach", str(worktree)),
        ):
            gitws._git(root, *args, executable=executable)

    old_marker = roots[0] / ".git" / "template-commit-inflight"
    new_marker = roots[1] / ".git" / "template-commit-inflight"
    new_marker.write_text("unresolved dispatch", encoding="utf-8")
    gitfile = worktrees[0] / ".git"
    replacement = (worktrees[1] / ".git").read_bytes()
    before = gitfile.stat()
    assert len(replacement) == before.st_size
    original = commit.status

    def redirect_after_status(root, events, rm, *, executable="git"):
        rows = original(root, events, rm, executable=executable)
        assert not old_marker.exists()
        with gitfile.open("r+b") as stream:
            stream.write(replacement)
        os.utime(gitfile, ns=(before.st_atime_ns, before.st_mtime_ns))
        assert gitfile.stat().st_mtime_ns == before.st_mtime_ns
        return rows

    monkeypatch.setattr(commit, "status", redirect_after_status)
    roadmap = SimpleNamespace(schema="frutlups.roadmap/2", milestones=(), autonomy=None)
    with pytest.raises(ValueError, match="prior Git dispatch is unresolved"):
        commit.require_idle(worktrees[0], [], roadmap, executable=executable)
    assert new_marker.read_text(encoding="utf-8") == "unresolved dispatch"


@pytest.mark.parametrize("states", [(), ("cancelled",), ("completed",), ("cancelled", "completed")])
def test_fresh_eligibility_delegation_and_final_guard(monkeypatch, states):
    root, model, event = object(), object(), object()
    events, converted = [event], {"fresh": True}
    state = SimpleNamespace(attempts={})
    calls = []
    first_rows = [dict(state=s) for s in states]
    second_rows = [] if "completed" in states else [dict(state="completed")]
    rows = [first_rows, second_rows]
    expected_delegation = [r for r in rows if any(v["state"] == "completed" for v in r)]
    @contextmanager
    def locked(r, rm, *, executable):
        assert r is root and rm is model and executable == "configured-git"
        calls.append("lock")
        yield
        calls.append("unlock")
    def fold(e, rm):
        assert e is events and rm is model
        calls.append("fold")
        return state
    def status(r, e, rm, *, executable):
        assert r is root and e is events and rm is model and executable == "configured-git"
        calls.append("status")
        return rows.pop(0)
    def resolve(r, supplied, e, *, executable):
        assert r is root and executable == "configured-git"
        assert supplied is expected_delegation.pop(0)
        assert e == [converted]
        assert any(row["state"] == "completed" for row in supplied)
        calls.append("resolve")
    def idle(r, *, executable):
        assert r is root and executable == "configured-git"
        calls.append("idle")
    monkeypatch.setattr(commit, "writer_lock", locked)
    monkeypatch.setattr(ledger, "fold", fold)
    monkeypatch.setattr(ledger, "_event_dict", lambda e: converted if e is event else pytest.fail("wrong event"))
    monkeypatch.setattr(commit, "status", status)
    monkeypatch.setattr(commit, "_resolve_completed_marker", resolve)
    monkeypatch.setattr(commit, "_idle", idle)
    for _ in range(2):
        assert commit.require_idle(root, events, model, executable="configured-git") is state
    first = ["lock", "fold", "status"] + (["resolve"] if "completed" in states else []) + ["idle", "unlock"]
    second = ["lock", "fold", "status"] + (["resolve"] if second_rows else []) + ["idle", "unlock"]
    assert calls == first + second
    assert rows == []
    assert expected_delegation == []


@pytest.mark.parametrize("failure", ["unfinished", "pending", "status", "resolver", "idle"])
def test_refusals_propagate_before_downstream_work(monkeypatch, failure):
    from contextlib import nullcontext
    calls = []
    error = ValueError("original failure")
    monkeypatch.setattr(commit, "writer_lock", lambda *a, **kw: nullcontext())
    monkeypatch.setattr(ledger, "fold", lambda *a: SimpleNamespace(attempts={"job": {"finish": None}} if failure == "unfinished" else {}))
    def step(name, result=None):
        def call(*a, **kw):
            calls.append(name)
            if name == failure:
                raise error
            return result
        return call
    monkeypatch.setattr(commit, "status", step("status", [dict(state="pending" if failure == "pending" else "completed")]))
    monkeypatch.setattr(commit, "_resolve_completed_marker", step("resolver"))
    monkeypatch.setattr(commit, "_idle", step("idle"))
    with pytest.raises(ValueError) as caught:
        commit.require_idle(None, [], None)
    if failure in ("status", "resolver", "idle"):
        assert caught.value is error
    else:
        assert str(caught.value) == ("unresolved invocation prevents new work" if failure == "unfinished" else "commit pending; explicit recovery or cancellation required")
    assert calls == {"unfinished": [], "pending": ["status"], "status": ["status"],
                     "resolver": ["status", "resolver"], "idle": ["status", "resolver", "idle"]}[failure]


@pytest.mark.parametrize("linked", [False, True])
def test_live_marker_and_worktree_index_refusals(tmp_path, monkeypatch, linked):
    root = _repository(tmp_path)
    common = root / ".git"
    executable = shutil.which("git")
    if linked:
        worktree = tmp_path / "linked"
        gitws._git(root, "worktree", "add", "--detach", str(worktree), executable=executable)
        root = worktree
    directory = Path(gitws._git(root, "rev-parse", "--absolute-git-dir", executable=executable).stdout.decode().strip())
    marker, index = common / "template-commit-inflight", directory / "index"
    saved_index = index.read_bytes()
    model = SimpleNamespace(schema="frutlups.roadmap/2", milestones=(), autonomy=None)
    original, calls = gitws._git, []
    def git(r, *args, **kwargs):
        assert kwargs["executable"] == executable
        calls.append(args)
        return original(r, *args, **kwargs)
    monkeypatch.setattr(gitws, "_git", git)
    for _ in range(2):
        calls.clear()
        commit.require_idle(root, [], model, executable=executable)
        assert calls == [("rev-parse", "--git-common-dir"), ("rev-parse", "--git-common-dir"), ("rev-parse", "--absolute-git-dir")]
    unknown = json.dumps(dict(operation="f" * 32, parent="0" * 40)).encode()
    for body in (unknown, b"malformed"):
        marker.write_bytes(body)
        with pytest.raises(ValueError, match="^prior Git dispatch is unresolved; architect must attribute its exit$"):
            commit.require_idle(root, [], model, executable=executable)
        assert marker.read_bytes() == body
        assert index.read_bytes() == saved_index
        marker.unlink()
    marker.mkdir()
    with pytest.raises(ValueError, match="prior Git dispatch is unresolved"):
        commit.require_idle(root, [], model, executable=executable)
    assert marker.is_dir() and list(marker.iterdir()) == []
    marker.rmdir()
    if linked:
        other_lock = common / "index.lock"
        other_lock.write_bytes(b"other worktree")
        commit.require_idle(root, [], model, executable=executable)
        assert other_lock.read_bytes() == b"other worktree"
        other_lock.unlink()
    lock = directory / "index.lock"
    lock.write_bytes(b"owned lock")
    with pytest.raises(ValueError, match="Git index.lock exists"):
        commit.require_idle(root, [], model, executable=executable)
    assert lock.read_bytes() == b"owned lock"
    assert index.read_bytes() == saved_index
    lock.unlink()
    commit.require_idle(root, [], model, executable=executable)


def test_real_completed_marker_parent_proof_archive_and_replay(project, monkeypatch):
    root, cfg, model, _, _ = commits.materialize(project, profile=False)
    def interrupted(*a, **kw):
        raise RuntimeError("leftover completed marker")
    with monkeypatch.context() as patch:
        patch.setattr(commit, "_archive_marker", interrupted)
        with pytest.raises(RuntimeError, match="leftover completed marker"):
            commits.approve(root, cfg, model)
    events = ledger.read(root / cfg.ledger)
    rows = commit.status(root, events, model, executable=cfg.git)
    assert len(rows) == 1
    assert rows[0]["state"] == "completed"
    marker = commit._marker(root, executable=cfg.git)
    original = marker.read_bytes()
    data = commit._marker_data(marker)
    saved = (root / cfg.ledger).read_bytes()
    index = root / ".git/index"
    saved_index, head = index.read_bytes(), gitws.head(root)
    archives = lambda: {p.name: p.read_bytes() for p in marker.parent.glob("template-commit-resolved-*")}
    assert archives() == {}
    # A valid read must not cache marker bytes across equal-size/time replacement.
    stamp = marker.stat()
    changed = original.replace(data["parent"].encode(), b"0" * len(data["parent"]))
    assert changed != original and len(changed) == len(original)
    marker.write_bytes(changed)
    os.utime(marker, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    with pytest.raises(ValueError, match="in-flight Git marker parent differs from the witnessed operation"):
        commit.require_idle(root, events, model, executable=cfg.git)
    assert marker.read_bytes() == changed
    assert marker.stat().st_mtime_ns == stamp.st_mtime_ns
    assert archives() == {}
    assert (root / cfg.ledger).read_bytes() == saved
    assert index.read_bytes() == saved_index
    marker.write_bytes(original)
    lock = root / ".git/index.lock"
    lock.write_bytes(b"in flight")
    with pytest.raises(ValueError, match="Git index.lock exists"):
        commit.require_idle(root, events, model, executable=cfg.git)
    assert marker.read_bytes() == original
    assert archives() == {}
    lock.unlink()
    commit.require_idle(root, events, model, executable=cfg.git)
    assert not marker.exists()
    archived = archives()
    assert len(archived) == 1
    assert json.loads(next(iter(archived.values()))) == dict(data, completion={"commit": rows[0]["commit"]})
    commit.require_idle(root, ledger.read(root / cfg.ledger), model, executable=cfg.git)
    assert archives() == archived
    assert (root / cfg.ledger).read_bytes() == saved
    assert index.read_bytes() == saved_index
    assert gitws.head(root) == head
