import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import frutlups.receipt as receipt_module
from frutlups.ledger import evidence_sha
from frutlups.receipt import run as native_run, write
from frutlups.roadmap import load
from frutlups.runtime import Runtime

FIXTURE = Path(__file__).parent / "fixtures" / "v5_development"


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
    (root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    _git(root, "add", ".gitignore")
    _git(root, "commit", "-q", "-m", "base")
    return root


def _config(timeout: float = 2) -> SimpleNamespace:
    executable = shutil.which("git")
    assert executable
    passthrough = tuple(
        key for key in ("SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "TMP") if key in os.environ
    )
    return SimpleNamespace(
        git=executable,
        ledger="ledger.jsonl",
        env_passthrough=passthrough,
        path_dirs=(str(Path(sys.executable).parent),),
        timeouts=SimpleNamespace(verification_seconds=timeout),
    )


def _roadmap_with(command: tuple[str, ...], focused=()):
    roadmap = load(FIXTURE / "roadmap.yaml")
    item = replace(roadmap.milestones[0].slices[0], verification=command, focused=focused)
    milestone = replace(roadmap.milestones[0], slices=(item,))
    return replace(roadmap, milestones=(milestone,)), item


def run(root, item, round_no, model, config, focused, **kwargs):
    from _native_support import verification_history
    if not (root / "ledger.jsonl").exists():
        verification_history(root, model, item)
    return native_run(root, item, round_no, model, config, focused, **kwargs)


def _coded(root, item, changed):
    from _native_support import verification_history
    model, item = _roadmap_with(item.verification, item.focused)
    verification_history(root, model, item, changed=changed)


def test_run_is_not_ok_on_nonzero_and_bounds_tails_and_paths(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    command = (
        sys.executable,
        "-c",
        "import sys; print('x' * 5000); print('y' * 5000, file=sys.stderr); sys.exit(4)",
    )
    roadmap, item = _roadmap_with(command)

    receipt = run(root, item, 1, roadmap, _config(), focused=False)

    result = receipt.commands[0]
    assert receipt.ok is False
    assert result.exit == 4
    assert len(result.stdout_tail.encode()) <= 4096
    assert len(result.stderr_tail.encode()) <= 4096
    assert result.argv[0] == "<outside-repo>"


def test_run_is_not_ok_on_timeout(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    roadmap, item = _roadmap_with(
        (sys.executable, "-c", "import time; time.sleep(2)"),
    )

    command = receipt_module._execute("full", item.verification, root,
                                      receipt_module.child_environment("verification", _config()), 0.05,
                                      native=True)
    assert command.timed_out is True
    assert command.exit is None


def test_multiple_real_commands_share_one_deadline(tmp_path):
    root = _repository(tmp_path)
    first = (sys.executable, "-c", "import time; time.sleep(.4); print('first')")
    second = (sys.executable, "-c", "import time; time.sleep(4); print('second')")
    third = (sys.executable, "-c", "from pathlib import Path; Path('never').touch()")
    # Native receipts freeze one full command. Its serial children still share
    # that command's original deadline and owned process lifetime.
    command = (sys.executable, "-c", "import subprocess; " + "; ".join(
        f"subprocess.run({list(argv)!r}, check=True)" for argv in (first, second, third)))
    model, item = _roadmap_with(command)
    started = time.monotonic()
    result = run(root, item, 1, model, _config(1.5), focused=True)
    elapsed = time.monotonic() - started
    assert [c.label for c in result.commands] == ["full"]
    assert result.commands[0].timed_out is True
    assert result.commands[0].exit is None
    assert result.ok is False
    assert not (root / "never").exists()
    assert elapsed < 3.5


def test_selected_runtime_is_used_without_second_resolution(tmp_path, monkeypatch):
    root = _repository(tmp_path)
    model, item = _roadmap_with(("python", "-c", "print('selected')"))
    from _native_support import verification_history
    frozen, _ = verification_history(root, model, item)
    selected = receipt_module.native_runtime(root, model, frozen, _config())
    original, calls = receipt_module._execute, []
    def execute(label, argv, *args, **kwargs):
        calls.append(argv)
        return original(label, argv, *args, **kwargs)
    monkeypatch.setattr(receipt_module, "_execute", execute)
    result = run(root, item, 1, model, _config(), False, selected_runtime=selected)
    assert result.ok
    assert calls == [(sys.executable, "-c", "print('selected')")]
    assert result.runtime == dict(selected.identity)
    with pytest.raises(ValueError, match="native verification runtime drift"):
        run(root, item, 1, model, _config(), False, selected_runtime=Runtime(sys.executable, {}))
    assert len(calls) == 1


def test_no_command_launch_after_preparation_exhausts_deadline(tmp_path, monkeypatch):
    root = _repository(tmp_path)
    model, item = _roadmap_with((sys.executable, "-c", "pass"))
    monkeypatch.setattr(receipt_module, "_execute", lambda *a: pytest.fail("late launch"))
    with pytest.raises(ValueError, match="native verification deadline exhausted before launch"):
        run(root, item, 1, model, _config(), False, timeout=0)


def test_receipt_hash_is_returned_only_after_fsync(tmp_path, monkeypatch):
    root = _repository(tmp_path)
    model, item = _roadmap_with((sys.executable, "-c", "pass"))
    value = run(root, item, 1, model, _config(), False)
    path = tmp_path / "durable.json"
    calls = []
    original = receipt_module.os.fsync
    def sync(fd):
        assert path.read_bytes().endswith(b"\n")
        calls.append(fd)
        original(fd)
    monkeypatch.setattr(receipt_module.os, "fsync", sync)
    assert write(value, path) == evidence_sha(path.read_bytes())
    assert len(calls) == 1


def test_run_is_not_ok_when_git_status_changes(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    roadmap, item = _roadmap_with(
        (sys.executable, "-c", "from pathlib import Path; Path('new.txt').write_text('new')"),
    )

    receipt = run(root, item, 1, roadmap, _config(), focused=False)

    assert receipt.ok is False
    assert receipt.commands[0].exit == 0
    assert receipt.witness["stable"] is False


def test_run_uses_recorded_coded_manifest_with_receipt_schema_keys(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    roadmap, item = _roadmap_with((sys.executable, "-c", "print('ok')"))
    (root / "recorded.txt").write_bytes(b"recorded")
    changed = [{"path": "recorded.txt", "sha": evidence_sha(b"recorded"), "kind": "added"}]
    _coded(root, item, changed)

    receipt = run(root, item, 1, roadmap, _config(), focused=False)
    path = tmp_path / "receipt.json"
    write(receipt, path)

    value = json.loads(path.read_text(encoding="utf-8"))
    assert value["schema"] == "frutlups.receipt/2"
    assert json.loads((root / value["manifest"]["path"]).read_bytes())["changed"] == [dict(row, round=1) for row in changed]


def test_run_accepts_an_unchanged_dirty_tree_and_runs_focused_first(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "existing-dirty.txt").write_text("dirty", encoding="utf-8")
    command = (sys.executable, "-c", "print('full')")
    focused = ((sys.executable, "-c", "print('focused')"),)
    roadmap, item = _roadmap_with(command, focused)

    receipt = run(root, item, 1, roadmap, _config(10), focused=True)

    assert receipt.ok is True
    assert receipt.tree_dirty_before is True
    assert receipt.tree_clean_after is False
    assert [result.label for result in receipt.commands] == ["full"]
    assert receipt.commands[0].stdout_tail == "full" + os.linesep


def test_write_returns_hash_of_portable_receipt(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    roadmap, item = _roadmap_with((sys.executable, "-c", "print('ok')"))
    receipt = run(root, item, 1, roadmap, _config(), focused=False)
    path = tmp_path / "receipts" / "receipt.json"

    digest = write(receipt, path)

    assert digest == evidence_sha(path.read_bytes())
    assert path.read_bytes().endswith(b"\n")


def test_receipt_uses_exact_shared_scrubbed_child_environment(tmp_path, monkeypatch):
    root = _repository(tmp_path)
    names = (
        "SAFE_VALUE", "API_KEY", "Api_Key", "OPENAI_API_KEY", "ANTHROPIC_AUTH_TOKEN",
        "Vendor_Token", "VENDOR_SECRET", "mixed_api_key",
    )
    for name in names:
        monkeypatch.setenv(name, "value-" + name)
    config = _config()
    config.env_passthrough = names
    captured = []
    original = receipt_module._execute

    def execute(label, argv, cwd, env, timeout, **kwargs):
        captured.append(env)
        return original(label, argv, cwd, env, timeout, **kwargs)

    monkeypatch.setattr(receipt_module, "_execute", execute)
    roadmap, item = _roadmap_with((sys.executable, "-c", "print('ok')"))
    result = run(root, item, 1, roadmap, config, focused=False)

    assert result.ok is True
    assert captured == [{
        "SAFE_VALUE": "value-SAFE_VALUE",
        "PATH": os.pathsep.join(config.path_dirs),
        "PYTHONDONTWRITEBYTECODE": "1",
        "FRUTLUPS_SEAT": "verification",
    }]


def test_scrub_text_preserves_long_text_and_relative_paths(tmp_path):
    root = tmp_path / "Project Folder"
    text = (
        f"BEGIN {str(root).upper()}/07_app/code.py\n"
        "Inline `C:\\Fixture Folder\\code.py` and "
        "[link](C:/fixture/code.py) and /tmp/fixture.py\n"
        "UNC `\\\\server\\share\\Fixture Folder\\code.py`\n"
        "Relative 07_app/code.py https://example.invalid/docs\n"
        + "z" * 6000 + "\nEND"
    )

    assert receipt_module.scrub_text(text, root, {}) == (
        "BEGIN <repo>/07_app/code.py\n"
        "Inline `<absolute-path>` and [link](<absolute-path>) and <absolute-path>\n"
        "UNC `<absolute-path>`\n"
        "Relative 07_app/code.py https://example.invalid/docs\n"
        + "z" * 6000 + "\nEND"
    )


@pytest.mark.parametrize(("text", "expected"), [
    ("`/` / 1/2 24/7 MB/s 07_app/a.py ./a.py ../a.py", "`/` / 1/2 24/7 MB/s 07_app/a.py ./a.py ../a.py"),
    ("| x | `/` | `a` / `b` |", "| x | `/` | `a` / `b` |"),
    ("```\n/\n07_app/file.py\n```", "```\n/\n07_app/file.py\n```"),
    ("https://example.invalid/a/b?q=token-value", "https://example.invalid/a/b?q=<redacted>"),
    ("http://example.invalid/docs", "http://example.invalid/docs"),
    ("`C:\\Private Folder\\file.py`", "`<absolute-path>`"),
    ("C:/Private Folder/file name.py", "<absolute-path>"),
    ("/home/Private Folder/file name.py", "<absolute-path>"),
    ("`/private file`", "`<absolute-path>`"),
    ("/private", "<absolute-path>"),
    ("//server/share/Private Folder/x.py", "<absolute-path>"),
    ("/\u03a9/private file", "<absolute-path>"),
    ("C:/Private Folder", "<absolute-path>"),
    ("/home/Private Folder", "<absolute-path>"),
    ("https://example.invalid/token-value/docs", "https://example.invalid/<redacted>/docs"),
    ("\\\\server\\share\\Private Folder\\file name.py", "<absolute-path>"),
    ("[file](file:///C:/Private Folder/file.py)", "[file](<absolute-path>)"),
])
def test_scrub_preserves_meaning_and_removes_local_paths(tmp_path, text, expected):
    assert receipt_module.scrub_text(text, tmp_path, {"API_TOKEN": "token-value"}) == expected


@pytest.mark.parametrize("case", ["dirty", "crlf", "recreate", "type", "new", "delete", "commit", "index", "flags", "mode"])
def test_receipt_detects_raw_head_and_index_drift(tmp_path, case):
    root = _repository(tmp_path)
    path = root / "tracked.txt"
    path.write_bytes(b"before\n")
    _git(root, "add", "tracked.txt")
    _git(root, "commit", "-qm", "tracked")
    if case == "dirty":
        path.write_bytes(b"dirty before\n")
    if case == "recreate":
        path.unlink()
    if case == "delete":
        (root / "untracked.txt").write_bytes(b"retain deletion evidence")
    git = f"subprocess.run([{shutil.which('git')!r}, '-C', '.', "
    actions = {
        "dirty": "p.write_bytes(b'dirty after\\n')",
        "crlf": "p.write_bytes(b'before\\r\\n')",
        "recreate": "p.write_bytes(b'recreated differently\\n')",
        "type": "p.unlink(); p.mkdir()",
        "new": "Path('new.txt').write_bytes(b'new')",
        "delete": "Path('untracked.txt').unlink()",
        "commit": git + "'commit', '--allow-empty', '-qm', 'owned drift'], check=True)",
        "index": "p.write_bytes(b'staged\\n'); " + git + "'add', 'tracked.txt'], check=True); p.write_bytes(b'before\\n')",
        "flags": git + "'update-index', '--assume-unchanged', 'tracked.txt'], check=True)",
        "mode": "p.chmod(0o444)",
    }
    before = receipt_module.workspace_witness(root)
    command = (sys.executable, "-c", "from pathlib import Path; import subprocess; p=Path('tracked.txt'); " + actions[case])
    roadmap, item = _roadmap_with(command)
    try:
        if case == "type":
            with pytest.raises(ValueError, match="native verification requires regular Git-owned paths"):
                run(root, item, 1, roadmap, _config(10), focused=False)
            assert path.is_dir()
            assert before != receipt_module.workspace_witness(root)
            return
        result = run(root, item, 1, roadmap, _config(10), focused=False)
        after = receipt_module.workspace_witness(root)
        assert result.commands[0].exit == 0
        assert result.commands[0].timed_out is False
        assert result.ok is False
        assert result.base_commit == before["head"]
        assert before != after
        if case == "dirty":
            assert path.read_bytes() == b"dirty after\n"
        elif case == "crlf":
            assert path.read_bytes() == b"before\r\n"
        elif case == "index":
            assert _git(root, "show", ":tracked.txt").stdout == b"staged\n"
            assert path.read_bytes() == b"before\n"
        elif case == "commit":
            assert result.base_commit != after["head"]
    finally:
        if case == "mode":
            path.chmod(0o666)  # Restore only the owned scratch permission for teardown.


@pytest.mark.parametrize("boundary", ["chunk", "retained-chunk", "retention", "publication", "utf8"])
def test_real_receipt_scrubs_split_diagnostics_and_labels_omission(tmp_path, monkeypatch, boundary):
    import tracemalloc
    from dataclasses import asdict

    root = _repository(tmp_path)
    secret = "synthetic-start-SECRET-END"
    monkeypatch.setenv("FIXTURE_SECRET", secret)
    sensitive = b"C:/Private Folder/PRIVATE-END.txt " + secret.encode()
    if boundary == "chunk":
        data = b"x" * (65536 - 5) + sensitive + b"\nFINAL diagnostic\n"
    elif boundary == "retained-chunk":
        data = b"padding\n" * 8191 + b"abc" + sensitive + b"\nFINAL diagnostic\n"
    elif boundary == "retention":
        data = b"x" * (9 * 1024 * 1024) + sensitive + b"z" * 16370 + b"\nFINAL diagnostic\n"
    elif boundary == "publication":
        data = sensitive + b"z" * 4080 + b"\nFINAL diagnostic\n"
    else:
        data = b"x" * 65535 + ("\u03a9" * 9000).encode() + b"\n" + sensitive + b"\nFINAL diagnostic\n"
    assert len(data) <= 16 * 1024 * 1024
    source = tmp_path / "synthetic.bin"
    source.write_bytes(data)
    command = (sys.executable, "-c", "import pathlib,sys,threading; d=pathlib.Path(sys.argv[1]).read_bytes(); "
               "t=threading.Thread(target=lambda: sys.stderr.buffer.write(d)); t.start(); "
               "sys.stdout.buffer.write(d); t.join()", str(source))
    roadmap, item = _roadmap_with(command)
    original = receipt_module.run_process
    retained = []
    def measured(*args, **kwargs):
        result = original(*args, **kwargs)
        retained.append((result.stdout_path.stat().st_size, result.stderr_path.stat().st_size))
        return result
    monkeypatch.setattr(receipt_module, "run_process", measured)
    tracemalloc.start()
    try:
        result = run(root, item, 1, roadmap, _config(30), focused=False)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result.ok is True
    published = json.dumps(asdict(result))
    assert secret not in published
    assert "SECRET-END" not in published
    assert "PRIVATE-END" not in published
    assert "Private Folder" not in published
    for tail in (result.commands[0].stdout_tail, result.commands[0].stderr_tail):
        assert tail.endswith("FINAL diagnostic\n")
        assert len(tail.encode("utf-8")) <= 4096
        assert tail.startswith(receipt_module.OMITTED)
    assert len(retained) == 1
    assert all(size <= 16384 for size in retained[0])
    assert peak < 4 * 1024 * 1024
    print(f"verification {boundary}: wall={result.commands[0].secs}s retained_disk={sum(retained[0])} parent_allocation_peak={peak}")


def test_public_argv_scrubs_parent_secrets_even_when_child_does_not_receive_them(tmp_path, monkeypatch):
    root = _repository(tmp_path)
    monkeypatch.setenv("PRIVATE_TOKEN", "synthetic-argv-token")
    command = (sys.executable, "-c", "import sys; print(sys.argv[1])", "https://example.invalid/?token=synthetic-argv-token")
    roadmap, item = _roadmap_with(command)
    result = run(root, item, 1, roadmap, _config(), False)
    assert result.ok is True
    assert result.commands[0].argv[-1] == "https://example.invalid/?token=<redacted>"
    assert result.commands[0].stdout_tail.strip() == "https://example.invalid/?token=<redacted>"


@pytest.mark.parametrize("launcher_exits", [False, True])
def test_receipt_owns_live_and_exited_launcher_descendants(tmp_path, launcher_exits):
    from test_procs import _running
    import time

    root = _repository(tmp_path)
    pid_file = tmp_path / "owned.pid"
    child = "import time; time.sleep(30)"
    command = (sys.executable, "-c", "import subprocess,sys,pathlib,time; "
               f"p=subprocess.Popen([sys.executable,'-c',{child!r}]); "
               f"pathlib.Path({str(pid_file)!r}).write_text(str(p.pid)); "
               + ("print('complete')" if launcher_exits else "time.sleep(30)"))
    roadmap, item = _roadmap_with(command)
    result = run(root, item, 1, roadmap, _config(10), False)
    assert result.ok is launcher_exits
    assert result.commands[0].exit == (0 if launcher_exits else None)
    assert result.commands[0].timed_out is (not launcher_exits)
    pid = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while _running(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _running(pid)
