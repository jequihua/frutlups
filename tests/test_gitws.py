import ast
import inspect
import os
import subprocess
import sys
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


def test_commit_literal_names_preserves_matching_unrelated_files(tmp_path):
    root = _repository(tmp_path)
    names = ["- leading [ab] Î©.txt", "space [1].txt", "unicodÃ©.txt"]
    if os.name != "nt":
        names += ["wild*.txt", "question?.txt", "line\nbreak.txt"]
    unrelated = ["- leading a Î©.txt", "space 1.txt", "wildcard.txt", "question1.txt"]
    for name in names + unrelated:
        (root / name).write_bytes(name.encode("utf-8"))
    parent = gitws.head(root)

    result = commit(root, names, "literal files")

    assert result == gitws.head(root)
    assert _git(root, "rev-parse", "HEAD^").stdout.decode().strip() == parent
    delta = _git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "HEAD")
    assert set(delta.stdout.decode().strip("\0").split("\0")) == set(names)
    assert _git(root, "diff", "--cached", "--name-only").stdout == b""
    assert {item.path for item in status(root)} == set(unrelated)
    for name in names + unrelated:
        assert (root / name).read_bytes() == name.encode("utf-8")


@pytest.mark.parametrize("staged", [False, True])
def test_commit_empty_selection_preserves_head_index_and_work(tmp_path, staged):
    root = _repository(tmp_path)
    (root / "unrelated.txt").write_bytes(b"retained")
    if staged:
        _git(root, "add", "unrelated.txt")
    before = (gitws.head(root), _git(root, "write-tree").stdout, status(root))

    if staged:
        with pytest.raises(ValueError, match="unrelated staged paths"):
            commit(root, (), "empty selection")
    else:
        assert commit(root, (), "empty selection") == before[0]

    assert (gitws.head(root), _git(root, "write-tree").stdout, status(root)) == before
    assert (root / "unrelated.txt").read_bytes() == b"retained"


@pytest.mark.parametrize("staged", [False, True])
def test_commit_deletion_including_already_staged_absence(tmp_path, staged):
    root = _repository(tmp_path)
    (root / "deleted.txt").unlink()
    (root / "modified.txt").write_bytes(b"unrelated edit\n")
    if staged:
        _git(root, "add", "-A", "--", "deleted.txt")
    parent = gitws.head(root)

    commit(root, ("deleted.txt",), "selected deletion")

    assert _git(root, "rev-parse", "HEAD^").stdout.decode().strip() == parent
    assert _git(root, "diff-tree", "--no-commit-id", "--name-status", "-r", "HEAD").stdout == (
        b"D\tdeleted.txt\n"
    )
    assert _git(root, "diff", "--cached", "--name-only").stdout == b""
    assert status(root) == (gitws.StatusEntry("modified.txt", " M"),)
    assert (root / "modified.txt").read_bytes() == b"unrelated edit\n"


def test_commit_rename_includes_origin_but_preserves_unrelated_work(tmp_path):
    root = _repository(tmp_path)
    _git(root, "mv", "renamed.txt", "destination [1].txt")
    (root / "modified.txt").write_bytes(b"retain this edit\n")
    parent = gitws.head(root)

    commit(root, ("destination [1].txt",), "selected rename")

    assert _git(root, "rev-parse", "HEAD^").stdout.decode().strip() == parent
    delta = _git(root, "diff-tree", "--no-commit-id", "--name-status", "--no-renames", "-r", "HEAD")
    assert delta.stdout == b"A\tdestination [1].txt\nD\trenamed.txt\n"
    assert not (root / "renamed.txt").exists()
    assert (root / "destination [1].txt").read_bytes() == b"renamed\n"
    assert _git(root, "diff", "--cached", "--name-only").stdout == b""
    assert status(root) == (gitws.StatusEntry("modified.txt", " M"),)
    assert (root / "modified.txt").read_bytes() == b"retain this edit\n"


def test_post_stage_guard_retains_both_selected_and_injected_index_work(tmp_path, monkeypatch):
    root = _repository(tmp_path)
    (root / "selected.txt").write_bytes(b"selected")
    (root / "unrelated.txt").write_bytes(b"unrelated")
    parent = gitws.head(root)
    original = gitws._git

    def inject(root, *args, **kwargs):
        result = original(root, *args, **kwargs)
        if args[:2] == ("add", "-A"):
            _git(root, "add", "unrelated.txt")
        return result

    monkeypatch.setattr(gitws, "_git", inject)
    with pytest.raises(ValueError, match="unexpected staged paths"):
        commit(root, ("selected.txt",), "refuse")

    assert gitws.head(root) == parent
    assert _git(root, "show", ":selected.txt").stdout == b"selected"
    assert _git(root, "show", ":unrelated.txt").stdout == b"unrelated"
    assert (root / "unrelated.txt").read_bytes() == b"unrelated"


@pytest.mark.parametrize("selection", [(".",), ("../escape",), ("",), ("folder",), ("missing",)])
def test_invalid_exact_selection_refuses_without_staging(tmp_path, selection):
    root = _repository(tmp_path)
    (root / "folder").mkdir()
    (root / "folder/unrelated.txt").write_bytes(b"retain")
    before = (gitws.head(root), _git(root, "write-tree").stdout, status(root))
    with pytest.raises(ValueError):
        commit(root, selection, "refuse invalid selection")
    assert (gitws.head(root), _git(root, "write-tree").stdout, status(root)) == before
    assert (root / "folder/unrelated.txt").read_bytes() == b"retain"


def test_oversized_argv_refuses_before_process_launch(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("oversized command reached subprocess.run")

    monkeypatch.setattr(gitws.subprocess, "run", forbidden)
    with pytest.raises(ValueError, match="bounded argument allowance"):
        gitws._git(tmp_path, "ls-files", "--", "x" * 40000)



@pytest.mark.parametrize("operation", ["status", "tracked", "staged", "renames"])
@pytest.mark.parametrize("channel", ["stdout", "stderr"])
def test_metadata_overflow_refuses_before_dependent_work(tmp_path, monkeypatch, operation, channel):
    from frutlups.procs import STREAM_CAP, stdout_observer

    root = _repository(tmp_path)
    (root / "modified.txt").write_bytes(b"selected edit\n")
    (root / "unrelated.txt").write_bytes(b"unrelated working bytes\n")
    before_head = _git(root, "rev-parse", "HEAD").stdout
    before_index = (root / ".git/index").read_bytes()
    names = (".gitignore", "modified.txt", "deleted.txt", "renamed.txt", "unrelated.txt")
    before_work = {name: (root / name).read_bytes() for name in names}
    original = gitws.run_process
    launched = []
    captured = []

    def observe(argv, cwd, env, timeout, stdout, stderr, stdin_bytes=None):
        assert timeout == 120
        assert stdout_observer.get() is None
        args = argv[argv.index("-C") + 2:]
        launched.append(args)
        target = {"status": args[0] == "status", "tracked": args[0] == "ls-files",
                  "staged": "--cached" in args, "renames": "--name-status" in args}[operation]
        if not target:
            return original(argv, cwd, env, timeout, stdout, stderr, stdin_bytes)
        fd = 1 if channel == "stdout" else 2
        result = original((sys.executable, "-c", f"import os; os.write({fd}, b'x'*(9*1024*1024))"),
                          cwd, env, timeout, stdout, stderr)
        assert result.exit == 0
        assert result.exception is None
        assert getattr(result, channel + "_truncated") is True
        assert getattr(result, channel + "_path").stat().st_size == STREAM_CAP
        captured.append(result)
        return result

    def outer(chunk):
        pytest.fail("Git bytes reached the seat observer")
    token = stdout_observer.set(outer)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(gitws, "run_process", observe)
            with pytest.raises(ValueError, match="8 MiB capture allowance"):
                if operation == "status":
                    gitws.status(root)
                elif operation == "renames":
                    gitws.bounded_diff(root, "HEAD", ["modified.txt"])
                else:
                    gitws.commit(root, ["modified.txt"], "must not stage")
            assert stdout_observer.get() is outer
    finally:
        stdout_observer.reset(token)
    assert len(captured) == 1
    assert all(args[0] not in ("add", "commit") for args in launched)
    assert all("--" not in args for args in launched)  # No dependent patch/stat.
    assert _git(root, "rev-parse", "HEAD").stdout == before_head
    assert (root / ".git/index").read_bytes() == before_index
    assert {name: (root / name).read_bytes() for name in names} == before_work
    assert not captured[0].stdout_path.exists()
    assert not captured[0].stderr_path.exists()


@pytest.mark.parametrize("channel", ["stdout", "stderr"])
@pytest.mark.parametrize("check", [False, True])
def test_metadata_overflow_is_not_authorized_by_check_false(tmp_path, monkeypatch, channel, check):
    root = _repository(tmp_path)
    original = gitws.run_process
    def overflow(argv, cwd, env, timeout, stdout, stderr, stdin_bytes=None):
        fd = 1 if channel == "stdout" else 2
        return original((sys.executable, "-c",
                         f"import os,sys; os.write({fd}, b'x'*(9*1024*1024)); sys.exit(7)"),
                        cwd, env, timeout, stdout, stderr)
    monkeypatch.setattr(gitws, "run_process", overflow)
    with pytest.raises(ValueError, match="8 MiB capture allowance"):
        gitws._git(root, "ls-files", "-z", check=check)


@pytest.mark.parametrize("check", [False, True])
def test_metadata_timeout_refuses_and_preserves_repository(tmp_path, monkeypatch, check):
    from frutlups.procs import stdout_observer

    root = _repository(tmp_path)
    (root / "modified.txt").write_bytes(b"staged edit\n")
    _git(root, "add", "modified.txt")
    before = (_git(root, "rev-parse", "HEAD").stdout, (root / ".git/index").read_bytes(),
              (root / "modified.txt").read_bytes())
    original = gitws.run_process
    def timeout(argv, cwd, env, seconds, stdout, stderr, stdin_bytes=None):
        assert seconds == 120
        assert stdout_observer.get() is None
        result = original((sys.executable, "-c", "import time; print('partial', flush=True); time.sleep(30)"),
                          cwd, env, 0.5, stdout, stderr)
        assert result.timed_out is True
        return result
    token = stdout_observer.set(lambda chunk: pytest.fail("observer must stay isolated"))
    outer = stdout_observer.get()
    try:
        monkeypatch.setattr(gitws, "run_process", timeout)
        with pytest.raises(ValueError, match="Git operation timed out"):
            gitws._git(root, "status", "--porcelain=v1", "-z", check=check)
        assert stdout_observer.get() is outer
    finally:
        stdout_observer.reset(token)
    assert (_git(root, "rev-parse", "HEAD").stdout, (root / ".git/index").read_bytes(),
            (root / "modified.txt").read_bytes()) == before


def test_git_completed_process_and_checked_error_preserve_exact_bytes(tmp_path, monkeypatch):
    root = _repository(tmp_path)
    original = gitws.run_process
    def echo(argv, cwd, env, timeout, stdout, stderr, stdin_bytes=None):
        return original((sys.executable, "-c", "import sys; "
                         "sys.stdout.buffer.write(sys.stdin.buffer.read()); "
                         "sys.stderr.buffer.write(b'error\\x00'); sys.exit(7)"),
                        cwd, env, timeout, stdout, stderr, stdin_bytes)
    monkeypatch.setattr(gitws, "run_process", echo)
    result = gitws._git(root, "version", input=b"input\0", check=False)
    assert isinstance(result, subprocess.CompletedProcess)
    assert result.args == gitws._argv(root, ("version",), "git")
    assert result.returncode == 7
    assert result.stdout == b"input\0"
    assert result.stderr == b"error\0"
    with pytest.raises(subprocess.CalledProcessError) as error:
        gitws._git(root, "version", input=b"input\0")
    assert error.value.cmd == result.args
    assert error.value.returncode == 7
    assert error.value.stdout == b"input\0"
    assert error.value.stderr == b"error\0"


def test_metadata_and_patch_reads_preserve_index_and_observer(tmp_path):
    from frutlups.procs import stdout_observer

    root = _repository(tmp_path)
    target = root / "modified.txt"
    info = target.stat()
    before = (root / ".git/index").read_bytes()
    os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns + 2_000_000_000))
    token = stdout_observer.set(lambda chunk: pytest.fail("Git output entered seat semantics"))
    outer = stdout_observer.get()
    try:
        assert gitws.status(root) == ()
        assert gitws.bounded_diff(root, "HEAD", ["modified.txt"]) == ""
        assert stdout_observer.get() is outer
    finally:
        stdout_observer.reset(token)
    assert (root / ".git/index").read_bytes() == before
    assert target.read_bytes() == b"before\n"


@pytest.mark.parametrize("extra", [0, 1])
def test_deleted_blob_evidence_is_complete_or_refused(tmp_path, extra):
    from frutlups.procs import STREAM_CAP

    root = _repository(tmp_path)
    payload = b"x" * (STREAM_CAP + extra - 1) + b"Z"
    (root / "large.bin").write_bytes(payload)
    _git(root, "add", "large.bin")
    _git(root, "commit", "-qm", "owned bounded blob")
    (root / "large.bin").unlink()
    before = (_git(root, "rev-parse", "HEAD").stdout, (root / ".git/index").read_bytes())
    if extra:
        with pytest.raises(ValueError, match="incomplete evidence refused"):
            gitws.changed_files(root)
    else:
        assert gitws.changed_files(root) == (
            Changed("large.bin", "deleted", evidence_sha(payload)),
        )
    assert not (root / "large.bin").exists()
    assert (_git(root, "rev-parse", "HEAD").stdout, (root / ".git/index").read_bytes()) == before


@pytest.mark.parametrize(("operation", "bad"), [
    ("status", b" M modified.txt"),
    ("status", b"R  renamed.txt\0"),
    ("status", b"xx modified.txt\0"),
    ("tracked", b"modified.txt"),
    ("tracked", b"../outside\0"),
    ("staged", b"modified.txt"),
    ("renames", b"R100\0renamed.txt\0"),
    ("renames", b"INVALID\0modified.txt\0"),
    ("index", b"H 100644 invalid 0\tmodified.txt\0"),
    ("flags", b"modified.txt\0partial flags"),
])
def test_malformed_required_metadata_refuses_before_dependent_work(tmp_path, monkeypatch, operation, bad):
    root = _repository(tmp_path)
    before = (_git(root, "rev-parse", "HEAD").stdout, (root / ".git/index").read_bytes())
    original = gitws._git
    dispatched = []
    def corrupt(root, *args, **kwargs):
        dispatched.append(args)
        selected = {"status": args[0] == "status",
                    "tracked": args[0] == "ls-files",
                    "staged": "--cached" in args,
                    "renames": "--name-status" in args,
                    "index": "--stage" in args,
                    "flags": "--debug" in args}[operation]
        if selected:
            return subprocess.CompletedProcess(args, 0, bad, b"")
        return original(root, *args, **kwargs)
    monkeypatch.setattr(gitws, "_git", corrupt)
    with pytest.raises(ValueError):
        if operation in ("index", "flags"):
            gitws.workspace_witness(root)
        elif operation == "status":
            gitws.status(root)
        elif operation == "renames":
            gitws.bounded_diff(root, "HEAD", ["modified.txt"])
        else:
            gitws.commit(root, ["modified.txt"], "must not dispatch")
    assert all(args[0] not in ("add", "commit") for args in dispatched)
    assert all("--" not in args for args in dispatched)
    assert (_git(root, "rev-parse", "HEAD").stdout, (root / ".git/index").read_bytes()) == before


def test_witness_includes_tracked_ignored_files_and_ignores_untracked_stores(tmp_path, monkeypatch):
    root = _repository(tmp_path)
    (root / "ignored.tmp").write_bytes(b"tracked despite ignore\r\n")
    _git(root, "add", "-f", "ignored.tmp")
    _git(root, "commit", "-qm", "tracked ignored")
    (root / ".gitignore").write_bytes(b"ignored.tmp\nstore/\n")
    (root / "store").mkdir()
    (root / "store/private").write_bytes(b"never read")
    original = gitws._file_identity
    seen = []
    def observed(root, name):
        seen.append(name)
        return original(root, name)
    monkeypatch.setattr(gitws, "_file_identity", observed)
    before = gitws.workspace_witness(root)
    assert "ignored.tmp" in before["files"]
    assert not any(p.startswith("store/") for p in seen)
    (root / "ignored.tmp").write_bytes(b"tracked despite ignore\n")
    assert gitws.workspace_witness(root) != before


def test_witness_ignores_stat_cache_but_detects_intent_to_add(tmp_path):
    root = _repository(tmp_path)
    before = gitws.workspace_witness(root)
    path = root / "modified.txt"
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
    _git(root, "update-index", "--refresh")
    assert gitws.workspace_witness(root) == before
    (root / "empty.txt").write_bytes(b"")
    _git(root, "add", "--intent-to-add", "empty.txt")
    intent = gitws.workspace_witness(root)
    _git(root, "add", "empty.txt")
    staged = gitws.workspace_witness(root)
    assert intent["files"] == staged["files"]
    assert intent["index"] != staged["index"]


def test_raw_witness_reads_at_most_64_kib_per_read(tmp_path, monkeypatch):
    root = _repository(tmp_path)
    (root / "large.raw").write_bytes(b"x" * (256 * 1024))
    original = gitws._open_regular
    reads = []
    class Bounded:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): return self
        def __exit__(self, *args): self.stream.close()
        def fileno(self): return self.stream.fileno()
        def read(self, size):
            assert 0 < size <= 65536
            reads.append(size)
            return self.stream.read(size)
    monkeypatch.setattr(gitws, "_open_regular", lambda p: Bounded(original(p)))
    gitws.workspace_witness(root)
    assert len(reads) >= 5


@pytest.mark.parametrize("kind", ["symlink", "reparse", "replaced-ancestor", "mode"])
def test_identity_capability_independent_no_target_reads(tmp_path, monkeypatch, kind):
    import stat
    from types import SimpleNamespace

    root = tmp_path
    (root / "parent").mkdir()
    path = root / "parent/file"
    path.write_bytes(b"owned")
    before = gitws._file_identity(root, "parent/file")
    original = Path.lstat
    target = root / "parent" if kind == "replaced-ancestor" else path
    def lstat(p):
        actual = original(p)
        if p != target:
            return actual
        return SimpleNamespace(st_dev=actual.st_dev, st_ino=actual.st_ino + 1,
            st_mode=(stat.S_IFLNK | 0o777) if kind == "symlink" else actual.st_mode ^ (0o200 if kind == "mode" else 0),
            st_reparse_tag=0xA0000003, st_file_attributes=0x400 if kind in ("reparse", "replaced-ancestor") else 0)
    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr(os, "readlink", lambda p: "external-target")
    if kind != "mode":
        monkeypatch.setattr(gitws, "_open_regular", lambda p: pytest.fail("followed a link/reparse target"))
        assert gitws._file_identity(root, "parent/file") != before
    else:
        with pytest.raises(ValueError, match="changed while opening"):
            gitws._file_identity(root, "parent/file")


def test_actual_symlink_capability_is_disclosed_without_a_skip(tmp_path):
    target = tmp_path / "outside"
    target.write_bytes(b"owned external target")
    root = tmp_path / "root"
    root.mkdir()
    link = root / "link"
    try:
        link.symlink_to(target)
    except OSError as exc:
        print(f"symlink capability unavailable: {type(exc).__name__}; synthetic identity cases cover refusal")
        assert not link.exists()
    else:
        identity = gitws._file_identity(root, "link")
        assert identity[0] == "link"
        assert identity[-1] == str(target)
        print("symlink capability available: real non-following identity observed")
    assert target.read_bytes() == b"owned external target"


def test_complete_blob_batches_exceed_diagnostic_and_aggregate_caps(tmp_path, monkeypatch):
    import hashlib
    from frutlups import procs
    import subprocess
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True, capture_output=True)
    objects, expected = [], {}
    # 73 MiB total, including an exact-limit blob, within the 128 MiB allowance.
    for name, byte, size in (("one", b"a", 64), ("two", b"b", 9)):
        data = byte * (size * 1024 * 1024)
        path = tmp_path / name
        path.write_bytes(data)
        oid = subprocess.run(["git", "-C", str(tmp_path), "hash-object", "-w", name],
                             check=True, capture_output=True).stdout.decode().strip()
        objects.append(oid)
        expected[oid] = hashlib.sha256(data).hexdigest()
        del data
    calls = []
    original = gitws.run_process
    def managed(*args, **kwargs):
        result = original(*args, **kwargs)
        if "stdout_consumer" in kwargs:
            calls.append(result.stdout_path.stat().st_size)
            assert result.stdout_truncated is True
            assert result.stdout_path.stat().st_size == procs.STREAM_CAP
        return result
    monkeypatch.setattr(gitws, "run_process", managed)
    result = gitws.blob_hashes(tmp_path, objects)
    assert calls == [procs.STREAM_CAP, procs.STREAM_CAP]
    for oid in objects:
        assert result[oid]["raw"] == expected[oid]
        assert result[oid]["normalized"] == expected[oid]


@pytest.mark.parametrize("bad", ["size", "header", "truncated", "extra", "no_eof", "stderr"])
def test_blob_parser_refuses_incomplete_or_unbounded_evidence(tmp_path, monkeypatch, bad):
    from types import SimpleNamespace
    oid = "a" * 40
    size = gitws.BLOB_LIMIT + 1 if bad == "size" else 3
    header = f"{oid} blob {size}".encode()
    monkeypatch.setattr(gitws, "_git", lambda *a, **k: SimpleNamespace(stdout=header + b"\n"))
    calls = []
    def managed(argv, root, env, timeout, stdout, stderr, input, *, stdout_consumer):
        calls.append(argv)
        data = header + b"\nabc\n"
        if bad == "header": data = b"b" + data[1:]
        if bad == "truncated": data = data[:-1]
        if bad == "extra": data += b"x"
        exception = None
        try:
            stdout_consumer(data)
            if bad != "no_eof": stdout_consumer(b"")
        except ValueError as exc: exception = str(exc)
        return SimpleNamespace(exit=0, timed_out=False, exception=exception,
                               stdout_truncated=True, stderr_truncated=bad == "stderr")
    monkeypatch.setattr(gitws, "run_process", managed)
    with pytest.raises(ValueError): list(gitws.read_blobs(tmp_path, [oid]))
    assert len(calls) == (0 if bad == "size" else 1)
