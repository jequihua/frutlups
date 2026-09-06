"""Read and mutate Git state through a small argv-only boundary."""

import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ._paths import repo_path, value
from .ledger import evidence_sha


@dataclass(frozen=True)
class StatusEntry:
    path: str
    code: str
    original_path: str | None = None


@dataclass(frozen=True)
class Changed:
    path: str
    kind: str
    sha256: str


@dataclass(frozen=True)
class Violation:
    path: str
    reason: str


def _git(
    root: Path, *args: str, executable: str = "git", check: bool = True,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [executable, "-C", str(root), *args], check=check, capture_output=True,
    )


def is_repo(root: Path, *, executable: str = "git") -> bool:
    result = _git(root, "rev-parse", "--is-inside-work-tree", executable=executable, check=False)
    return result.returncode == 0 and result.stdout.strip() == b"true"


def head(root: Path, *, executable: str = "git") -> str:
    return _git(root, "rev-parse", "HEAD", executable=executable).stdout.decode().strip()


def status(root: Path, *, executable: str = "git") -> tuple[StatusEntry, ...]:
    raw = _git(
        root, "status", "--porcelain=v1", "-z", "--untracked-files=all",
        executable=executable,
    ).stdout
    records, output, index = raw.split(b"\0"), [], 0
    while index < len(records) and records[index]:
        record = records[index].decode("utf-8", "surrogateescape")
        code, relative = record[:2], record[3:].replace("\\", "/")
        index += 1
        original = None
        if "R" in code or "C" in code:
            original = records[index].decode("utf-8", "surrogateescape").replace("\\", "/")
            index += 1
        output.append(StatusEntry(relative, code, original))
    return tuple(output)


def is_clean(
    root: Path, ignore_prefixes: Sequence[str] = ("local_state/",), *, executable: str = "git",
) -> bool:
    return not any(
        not any(item.path.startswith(prefix) for prefix in ignore_prefixes)
        for item in status(root, executable=executable)
    )


def changed_files(root: Path, *, executable: str = "git") -> tuple[Changed, ...]:
    output = []
    for item in status(root, executable=executable):
        if "R" in item.code:
            kind = "renamed"
        elif "D" in item.code:
            kind = "deleted"
        elif item.code == "??" or "A" in item.code:
            kind = "added"
        else:
            kind = "modified"
        path = repo_path(root, item.path)
        if path.is_symlink():
            raise ValueError(f"changed path is a symlink: {item.path}")
        if kind == "deleted":
            data = _git(root, "show", f"HEAD:{item.path}", executable=executable).stdout
        elif path.is_file():
            data = path.read_bytes()
        else:
            raise ValueError(f"changed path is not a regular file: {item.path}")
        output.append(Changed(item.path, kind, evidence_sha(data)))
    return tuple(output)


def fence(
    changed: Iterable[Changed | Mapping[str, object]],
    allowed_prefixes: Sequence[str],
    forbidden: Sequence[str],
) -> tuple[Violation, ...]:
    def matches(path: str, rule: str) -> bool:
        return path.startswith(rule) if rule.endswith("/") else path == rule

    output = []
    for item in changed:
        path = str(value(item, "path"))
        if any(matches(path, rule) for rule in forbidden):
            output.append(Violation(path, "forbidden"))
        elif not any(matches(path, rule) for rule in allowed_prefixes):
            output.append(Violation(path, "outside allowed prefixes"))
    return tuple(output)


def commit(
    root: Path, paths: Sequence[str], message: str, *, executable: str = "git",
) -> str:
    staged = {
        item.path for item in status(root, executable=executable)
        if item.code[0] not in (" ", "?")
    }
    if staged - set(paths):
        raise ValueError("unrelated staged paths: " + ", ".join(sorted(staged - set(paths))))
    _git(root, "add", "-A", "--", *paths, executable=executable)
    staged = {
        item.path for item in status(root, executable=executable)
        if item.code[0] not in (" ", "?")
    }
    if staged - set(paths):
        raise ValueError("unexpected staged paths; inspect the index before committing")
    _git(root, "commit", "-m", message, executable=executable)
    return head(root, executable=executable)
