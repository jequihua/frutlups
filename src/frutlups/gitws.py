"""Read and mutate Git state through a small argv-only boundary."""

import hashlib
import os
import re
import stat
import subprocess
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ._paths import repo_path, safe_rel, value
from .ledger import evidence_sha
from .procs import STREAM_CAP, run_process, stdout_observer


ARGV_LIMIT = 12 * 1024
DIFF_NOTICE = "\n[Diff truncated; inspect the complete selected paths in the manifest.]\n"


def selected_executable(value, *, label="configured Git"):
    """Resolve an explicit executable without shell interpretation or fallback."""
    message = f"{label} must name an existing absolute executable file"
    try:
        if not isinstance(value, str) or not value or "\0" in value:
            raise ValueError(message)
        supplied = Path(value)
        if not supplied.is_absolute():
            raise ValueError(message)
        resolved = supplied.resolve(strict=True)
        if not stat.S_ISREG(resolved.stat().st_mode):
            raise ValueError(message)
        if os.name == "nt":
            if supplied.suffix.lower() != ".exe" or resolved.suffix.lower() != ".exe":
                raise ValueError(message)
        elif not os.access(resolved, os.X_OK):
            raise ValueError(message)
        return str(resolved)
    except (OSError, ValueError, RuntimeError):
        raise ValueError(message) from None


def controlled_environment(source):
    """Remove ambient project/interpreter redirection, leaving provider policy alone."""
    env = {key: value for key, value in source.items()
           if not key.upper().startswith(("GIT_", "PYTHON"))
           and key.upper() != "TEMPLATE_GIT_EXECUTABLE"}
    env.update(PYTHONDONTWRITEBYTECODE="1", GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL=os.devnull)
    return env


def _argv(root, args, executable):
    # Status and porcelain diff must not refresh the index during evidence reads.
    argv = [executable, "--no-replace-objects", "--literal-pathspecs", "--no-optional-locks",
            "-c", "diff.autoRefreshIndex=false", "-C", str(root), *args]
    encoded = subprocess.list2cmdline(argv).encode("utf-16-le" if os.name == "nt" else "utf-8")
    if len(encoded) > ARGV_LIMIT:
        raise ValueError("Git command exceeds the bounded argument allowance")
    return argv


def _selected_paths(root, paths):
    paths = list(dict.fromkeys(safe_rel(path) for path in paths))
    if len(paths) > 100_000 or sum(len(p.encode("utf-8")) + 1 for p in paths) > STREAM_CAP:
        raise ValueError("Git path manifest exceeds the bounded selection allowance")
    for path in paths:
        if repo_path(root, path).is_dir():
            raise ValueError("exact Git selection cannot include a directory: " + path)
    return paths


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
    input: bytes | None = None,
) -> subprocess.CompletedProcess:
    argv = _argv(root, args, executable)
    with tempfile.TemporaryDirectory(prefix="frutlups-git-") as directory:
        output = Path(directory)
        result = _run_git(argv, Path.cwd(), output / "stdout", output / "stderr", input)
        # Even check=False cannot authorize parsing incomplete metadata/blob bytes.
        if result.timed_out:
            raise ValueError("Git operation timed out; inspect retained state")
        if result.exit is None:
            raise ValueError("Git operation failed: no exit status; inspect retained state")
        if result.exception:
            raise OSError("Git process failed: " + result.exception)
        if result.stdout_truncated or result.stderr_truncated:
            raise ValueError("Git output exceeds the 8 MiB capture allowance; incomplete evidence refused")
        completed = subprocess.CompletedProcess(
            argv, result.exit, result.stdout_path.read_bytes(), result.stderr_path.read_bytes(),
        )
        if check:
            completed.check_returncode()
        if args and args[0] == "ls-files" and "-z" in args and not {"--stage", "--debug"} & set(args):
            _listed_paths(completed.stdout)
        return completed


def _run_git(argv, cwd, stdout, stderr, input=None):
    # Git output is never part of an enclosing seat's semantic stream.
    token = stdout_observer.set(None)
    try:
        return run_process(argv, cwd, controlled_environment(os.environ), 120, stdout, stderr, input)
    finally:
        stdout_observer.reset(token)


def is_repo(root: Path, *, executable: str = "git") -> bool:
    result = _git(root, "rev-parse", "--is-inside-work-tree", executable=executable, check=False)
    return result.returncode == 0 and result.stdout.strip() == b"true"


def _nul_records(raw):
    if raw and (not raw.endswith(b"\0") or b"\0\0" in raw):
        raise ValueError("malformed required Git metadata")
    return raw[:-1].split(b"\0") if raw else []


def _metadata_path(raw):
    return safe_rel(raw.decode("utf-8", "surrogateescape"))


def head(root: Path, *, executable: str = "git") -> str:
    value = _git(root, "rev-parse", "HEAD", executable=executable).stdout.decode("ascii").strip()
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value):
        raise ValueError("malformed required Git HEAD")
    return value


def status(root: Path, *, executable: str = "git") -> tuple[StatusEntry, ...]:
    raw = _git(
        root, "status", "--porcelain=v1", "-z", "--untracked-files=all",
        executable=executable,
    ).stdout
    records, output, index = _nul_records(raw), [], 0
    while index < len(records):
        record = records[index].decode("utf-8", "surrogateescape")
        if len(record) < 4 or record[2] != " " or not re.fullmatch(r"[ MADRCUT?!]{2}", record[:2]):
            raise ValueError("malformed required Git status")
        code, relative = record[:2], safe_rel(record[3:])
        if code not in ("??", "!!") and (code == "  " or "?" in code or "!" in code):
            raise ValueError("malformed required Git status")
        index += 1
        original = None
        if "R" in code or "C" in code:
            if index >= len(records):
                raise ValueError("incomplete required Git rename")
            original = _metadata_path(records[index])
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
    paths = _selected_paths(root, paths)
    for item in status(root, executable=executable):
        if "R" in item.code and item.path in paths and item.original_path not in paths:
            paths.append(item.original_path)
    paths = _selected_paths(root, paths)
    staged = _staged_paths(root, executable)
    if staged - set(paths):
        raise ValueError("unrelated staged paths: " + ", ".join(sorted(staged - set(paths))))
    if not paths:
        return head(root, executable=executable)
    indexed = {p.encode("utf-8", "surrogateescape") for p in
               _listed_paths(_git(root, "ls-files", "-z", executable=executable).stdout)}
    selected = []
    for path in paths:
        if path.encode("utf-8") in indexed or repo_path(root, path).exists():
            selected.append(path)
        elif path not in staged:
            raise ValueError("selected path is neither present nor staged: " + path)
    # Already staged deletions need no pathspec; add would reject their absence.
    if selected:
        selection = b"".join(path.encode("utf-8") + b"\0" for path in selected)
        _git(root, "add", "-A", "--pathspec-from-file=-", "--pathspec-file-nul",
             input=selection, executable=executable)
    staged = _staged_paths(root, executable)
    if staged - set(paths):
        raise ValueError("unexpected staged paths; inspect the index before committing")
    _git(root, "commit", "-m", message, executable=executable)
    return head(root, executable=executable)


def _staged_paths(root, executable):
    # --no-renames exposes both sides, so an origin cannot evade the index guard.
    raw = _git(root, "diff", "--cached", "--name-only", "--no-renames", "-z",
               executable=executable).stdout
    return set(_listed_paths(raw))


def approved_paths(root, paths, *, executable="git"):
    paths = _selected_paths(root, paths)
    for row in status(root, executable=executable):
        if "R" in row.code and row.path in paths and row.original_path not in paths:
            paths.append(row.original_path)
    return sorted(_selected_paths(root, paths))


def guard_index(root, paths, *, executable="git"):
    extra = _staged_paths(root, executable) - set(paths)
    if extra:
        raise ValueError("unrelated staged paths; index preserved: " + ", ".join(sorted(extra)[:8]))


def stage_exact(root, paths, *, executable="git"):
    paths = _selected_paths(root, paths)
    guard_index(root, paths, executable=executable)
    indexed = set(_listed_paths(_git(root, "ls-files", "-z", executable=executable).stdout))
    selected = [p for p in paths if p in indexed or repo_path(root, p).exists()]
    if selected:
        _git(root, "add", "-A", "--pathspec-from-file=-", "--pathspec-file-nul",
             input=b"".join(p.encode("utf-8") + b"\0" for p in selected), executable=executable)
    guard_index(root, paths, executable=executable)


BLOB_LIMIT = 64 * 1024 * 1024
OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})$")


def read_blobs(root, objects, *, executable="git"):
    """Yield size-checked complete blobs; diagnostic retention is not transport."""
    objects = list(dict.fromkeys(objects))
    if not objects:
        return
    if any(not isinstance(oid, str) or not OID.fullmatch(oid) for oid in objects):
        raise ValueError("invalid Git blob identity")
    request = "".join(oid + "\n" for oid in objects).encode("ascii")
    if len(request) > STREAM_CAP:
        raise ValueError("Git blob selection exceeds the metadata allowance")
    metadata = _git(root, "cat-file", "--batch-check", input=request,
                    executable=executable).stdout.splitlines()
    if len(metadata) != len(objects):
        raise ValueError("incomplete Git blob metadata")
    batches, batch, used = [], [], 0
    for oid, header in zip(objects, metadata):
        parts = header.split(b" ")
        if (len(parts) != 3 or parts[:2] != [oid.encode(), b"blob"]
                or not re.fullmatch(rb"0|[1-9][0-9]*", parts[2])):
            raise ValueError("commit content is not an available blob")
        size = int(parts[2])
        if size > BLOB_LIMIT:
            raise ValueError("Git blob exceeds the 64 MiB per-file allowance")
        cost = len(header) + size + 2
        if batch and used + cost > BLOB_LIMIT + 1024:
            batches.append(batch)
            batch, used = [], 0
        batch.append((oid, size, header))
        used += cost
    batches.append(batch)
    for batch in batches:
        expected = sum(len(h) + size + 2 for _, size, h in batch)
        data, eof = bytearray(), False
        def consume(chunk):
            nonlocal eof
            if eof or len(data) + len(chunk) > expected:
                raise ValueError("unexpected Git blob evidence")
            if not chunk:
                eof = True
                if len(data) != expected:
                    raise ValueError("incomplete Git blob evidence")
            data.extend(chunk)
        argv = _argv(root, ("cat-file", "--batch"), executable)
        with tempfile.TemporaryDirectory(prefix="frutlups-blobs-") as directory:
            output = Path(directory)
            token = stdout_observer.set(None)
            try:
                result = run_process(argv, root, controlled_environment(os.environ), 120, output / "stdout",
                                     output / "stderr",
                                     "".join(oid + "\n" for oid, _, _ in batch).encode(),
                                     stdout_consumer=consume)
            finally:
                stdout_observer.reset(token)
            if (result.timed_out or result.exception or result.exit != 0
                    or result.stderr_truncated or not eof or len(data) != expected):
                raise ValueError("incomplete Git blob evidence; managed read failed")
        # Check all framing before yielding any blob from this batch.
        offset, spans = 0, []
        for oid, size, header in batch:
            start = offset + len(header) + 1
            end = start + size
            if data[offset:start] != header + b"\n" or data[end:end + 1] != b"\n":
                raise ValueError("incomplete Git blob evidence")
            spans.append((oid, start, end))
            offset = end + 1
        for oid, start, end in spans:
            yield oid, bytes(memoryview(data)[start:end])
        del data


def blob_hashes(root, objects, *, executable="git"):
    return {oid: {"raw": hashlib.sha256(data).hexdigest(), "normalized": evidence_sha(data)}
            for oid, data in read_blobs(root, objects, executable=executable)}


def bounded_diff(root, base, paths, limit=32 * 1024, *, stat=False, executable="git"):
    """Literal, byte-budgeted batches; diff does not support pathspec-from-file."""
    paths = _selected_paths(root, paths)
    if not paths:
        return ""
    if not isinstance(base, str) or not base or base.startswith("-"):
        raise ValueError("invalid bounded diff revision")
    if not len(DIFF_NOTICE.encode()) <= limit < STREAM_CAP:
        raise ValueError("invalid bounded diff allowance")
    revision = _git(root, "rev-parse", "--verify", base + "^{commit}",
                    executable=executable).stdout.decode("ascii").strip()
    # Recover committed as well as staged rename origins without changing manifests.
    metadata = _git(root, "diff", "--no-ext-diff", "--no-textconv", "--name-status",
                    "--find-renames", "-z", revision, executable=executable).stdout
    fields = iter(_nul_records(metadata))
    for code in fields:
        if not re.fullmatch(rb"(?:[ACDMRTUXB]|[RC][0-9]{1,3})", code):
            raise ValueError("malformed required Git rename metadata")
        if code.startswith((b"R", b"C")) and (len(code) == 1 or int(code[1:]) > 100):
            raise ValueError("malformed required Git rename score")
        try:
            origin = _metadata_path(next(fields))
            if code.startswith((b"R", b"C")):
                destination = _metadata_path(next(fields))
                if code.startswith(b"R") and destination in paths and origin not in paths:
                    paths.append(origin)
        except StopIteration as exc:
            raise ValueError("incomplete required Git rename metadata") from exc
    paths = _selected_paths(root, paths)
    flags = ("--stat",) if stat else ()
    framing = ("diff", "--no-ext-diff", "--no-textconv", *flags, revision, "--")
    batches, batch = [], []
    # Validate every batch before launching any patch/stat, even omitted ones.
    for path in paths:
        _argv(root, (*framing, path), executable)
        try:
            _argv(root, (*framing, *batch, path), executable)
        except ValueError:
            batches.append(batch)
            batch = []
        batch.append(path)
    batches.append(batch)
    remaining = limit - len(DIFF_NOTICE.encode())
    parts = []
    for batch in batches:
        if remaining <= 0:
            return "".join(parts) + DIFF_NOTICE
        argv = _argv(root, (*framing, *batch), executable)
        # Reuse the existing bounded stream drainer; never capture a whole diff.
        with tempfile.TemporaryDirectory(prefix="frutlups-diff-") as directory:
            output = Path(directory)
            result = _run_git(argv, root, output / "stdout", output / "stderr")
            if result.timed_out or result.exception:
                raise ValueError("Git diff did not complete; inspect retained work")
            if result.exit != 0:
                with result.stderr_path.open("rb") as stream:
                    detail = stream.read(2048)
                raise subprocess.CalledProcessError(result.exit, argv, stderr=detail)
            with result.stdout_path.open("rb") as stream:
                data = stream.read(remaining + 1)
            # Replacement characters can expand non-UTF-8 patches on encoding.
            text = data[:remaining].decode("utf-8", "replace").encode("utf-8")
            parts.append(text[:remaining].decode("utf-8", "ignore"))
            if len(data) > remaining or len(text) > remaining or result.stdout_truncated:
                return "".join(parts) + DIFF_NOTICE
            remaining -= len(parts[-1].encode("utf-8"))
    return "".join(parts)


def _listed_paths(raw):
    return tuple(_metadata_path(p) for p in _nul_records(raw))


def _reparse(info):
    return bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _node_identity(info, *, path_mode=None):
    # Windows handle stat omits suffix-inferred execute bits. Restore only those
    # for the open-object comparison; retain the path mode in the raw witness and
    # native receipt hash domain. Type/read-only/reparse checks remain intact.
    mode = info.st_mode
    if os.name == "nt" and path_mode is not None:
        mode = (mode & ~0o111) | (path_mode & 0o111)
    return (info.st_dev, info.st_ino, mode,
            getattr(info, "st_reparse_tag", 0), getattr(info, "st_file_attributes", 0) & 0x401)


def _open_regular(path):
    if os.name != "nt":
        return os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb")
    import ctypes
    import msvcrt
    from ctypes import wintypes

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    # OPEN_REPARSE_POINT opens the link itself, never its final target.
    handle = api.CreateFileW(str(path), 0x80000000, 7, None, 3, 0x00200000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except Exception:
        api.CloseHandle(handle)
        raise
    return os.fdopen(descriptor, "rb")


def _file_identity(root, relative):
    """Read raw bytes in 64 KiB chunks, refusing traversal of replaced ancestors."""
    path = root
    ancestors = []
    for part in safe_rel(relative).split("/"):
        path = path / part
        try:
            info = path.lstat()
        except (FileNotFoundError, NotADirectoryError):
            return ("missing", tuple(ancestors))
        identity = _node_identity(info)
        if stat.S_ISLNK(info.st_mode) or _reparse(info):
            # readlink reads the link itself; unsupported reparse tags fail closed.
            return ("link", tuple(ancestors), identity, os.readlink(path))
        if path != root / relative:
            ancestors.append(identity)
            if not stat.S_ISDIR(info.st_mode):
                return ("non-directory-parent", tuple(ancestors))
    if not stat.S_ISREG(info.st_mode):
        return ("non-file", tuple(ancestors), identity)
    digest = hashlib.sha256()
    # Validate the opened object before reading: a replaced link/file is refused.
    with _open_regular(path) as stream:
        if _node_identity(os.fstat(stream.fileno()), path_mode=info.st_mode) != identity:
            raise ValueError("workspace path changed while opening: " + relative)
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
        if _node_identity(os.fstat(stream.fileno()), path_mode=info.st_mode) != identity:
            raise ValueError("workspace path changed while reading: " + relative)
    return ("file", tuple(ancestors), identity, digest.hexdigest())


def workspace_witness(root, *, executable="git"):
    """Snapshot Git-listed raw files, HEAD and semantic index entries/flags.

    Tracked ignored files are included. Nonignored untracked names come from Git;
    ignored stores and submodule internals are never walked. This is a before/after
    observation, not surveillance of transient edits restored to identical state.
    """
    root = Path(root).resolve()
    names = _listed_paths(_git(root, "ls-files", "--cached", "--others",
                              "--exclude-standard", "-z", executable=executable).stdout)
    # One listing binds entries to flags, including intent-to-add (absent from -v).
    # Ignore volatile stat-cache fields; preserve semantic index ownership.
    raw = _git(root, "ls-files", "--stage", "-v", "--debug", "-z", executable=executable).stdout
    index, flags, offset = [], [], 0
    debug_record = re.compile(
        rb"  ctime: [0-9]+:[0-9]+\n  mtime: [0-9]+:[0-9]+\n"
        rb"  dev: [0-9]+\tino: [0-9]+\n  uid: [0-9]+\tgid: [0-9]+\n"
        rb"  size: [0-9]+\tflags: ([0-9a-fA-F]+)\n")
    while offset < len(raw):
        end = raw.find(b"\0", offset)
        if end < 0:
            raise ValueError("malformed required Git index metadata")
        entry = raw[offset:end]
        header, separator, name = entry.partition(b"\t")
        if not separator or not re.fullmatch(
            rb"[HSMRCK?hsmrck] (?:100644|100755|120000|160000) (?:[0-9a-f]{40}|[0-9a-f]{64}) [0-3]", header
        ):
            raise ValueError("malformed required Git index metadata")
        name = _metadata_path(name)
        match = debug_record.match(raw, end + 1)
        if not match:
            raise ValueError("malformed required Git index flags")
        index.append(entry)
        flags.append((name, int(match[1], 16) & 0x60008000))
        offset = match.end()
    return {
        "head": head(root, executable=executable),
        "index": (tuple(sorted(index)), tuple(sorted(flags))),
        "files": {p: _file_identity(root, p) for p in sorted(set(names))},
    }
