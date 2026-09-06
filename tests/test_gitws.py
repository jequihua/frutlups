import ast
import inspect
import subprocess
from pathlib import Path

import pytest
from frutlups import gitws
from frutlups.gitws import Changed, changed_files, commit, fence, is_clean, status
from frutlups.ledger import evidence_sha


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True,
    )


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "core.autocrlf", "false")
    (root / ".gitignore").write_text("ignored.tmp\n", encoding="utf-8")
    (root / "modified.txt").write_bytes(b"before\n")
    (root / "deleted.txt").write_bytes(b"deleted\n")
    (root / "renamed.txt").write_bytes(b"renamed\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "base")
    return root


def test_status_and_changed_files_cover_all_kinds_and_ignore_ignored(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "modified.txt").write_bytes(b"after\r\n")
    (root / "deleted.txt").unlink()
    (root / "added.txt").write_bytes(b"added\n")
    (root / "ignored.tmp").write_text("ignored", encoding="utf-8")
    _git(root, "mv", "renamed.txt", "destination.txt")

    entries = status(root)
    changed = {item.path: item for item in changed_files(root)}

    assert "ignored.tmp" not in {item.path for item in entries}
    assert {path: item.kind for path, item in changed.items()} == {
        "added.txt": "added",
        "deleted.txt": "deleted",
        "destination.txt": "renamed",
        "modified.txt": "modified",
    }
    assert changed["modified.txt"].sha256 == evidence_sha(b"after\r\n")
    assert changed["deleted.txt"].sha256 == evidence_sha(b"deleted\n")


def test_is_clean_ignores_default_local_state_prefix(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    local = root / "local_state"
    local.mkdir()
    (local / "job.txt").write_text("working", encoding="utf-8")

    assert is_clean(root)
    assert not is_clean(root, ignore_prefixes=())


def test_fence_reports_forbidden_and_outside_paths() -> None:
    changed = (
        Changed("08_pkg/ok.py", "modified", "a" * 64),
        Changed("00_brief/secret.md", "modified", "b" * 64),
        Changed("other/file.txt", "added", "c" * 64),
    )

    violations = fence(changed, ("08_pkg/",), ("00_brief/",))

    assert [(item.path, item.reason) for item in violations] == [
        ("00_brief/secret.md", "forbidden"),
        ("other/file.txt", "outside allowed prefixes"),
    ]


def test_module_contains_no_forbidden_git_argv_literals() -> None:
    tree = ast.parse(inspect.getsource(gitws))
    literals = {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }

    assert not {"checkout", "reset", "clean", "stash"} & literals


def test_commit_stages_only_named_paths(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "selected.txt").write_text("selected", encoding="utf-8")
    (root / "unrelated.txt").write_text("unrelated", encoding="utf-8")

    commit_id = commit(root, ("selected.txt",), "selected change")

    assert len(commit_id) == 40
    assert {item.path for item in status(root)} == {"unrelated.txt"}


def test_commit_refuses_unrelated_staged_changes_without_mutation(tmp_path):
    root = _repository(tmp_path)
    (root / "selected.txt").write_text("selected", encoding="utf-8")
    (root / "unrelated.txt").write_text("unrelated", encoding="utf-8")
    _git(root, "add", "unrelated.txt")
    before = status(root)
    head = gitws.head(root)

    with pytest.raises(ValueError, match="unrelated staged paths: unrelated.txt"):
        commit(root, ("selected.txt",), "must not commit")

    assert status(root) == before
    assert gitws.head(root) == head
