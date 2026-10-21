from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from frutlups.procs import STREAM_CAP, run_process


def _run(tmp_path: Path, code: str, timeout: float = 10, stdin: bytes | None = None):
    return run_process(
        (sys.executable, "-c", code),
        tmp_path,
        os.environ,
        timeout,
        tmp_path / "stdout.bin",
        tmp_path / "stderr.bin",
        stdin,
    )


def _running(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    import ctypes

    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    exit_code = ctypes.c_ulong()
    ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
    ctypes.windll.kernel32.CloseHandle(handle)
    return exit_code.value == 259


def test_run_process_writes_streams_and_stdin(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        "import sys; data=sys.stdin.buffer.read(); "
        "sys.stdout.buffer.write(data); sys.stderr.write('err')",
        stdin=b"input",
    )

    assert result.exit == 0
    assert not result.timed_out
    assert result.exception is None
    assert not result.stdout_truncated
    assert not result.stderr_truncated
    assert result.stdout_path.read_bytes() == b"input"
    assert result.stderr_path.read_bytes() == b"err"


def test_streams_are_capped_and_flagged(tmp_path: Path) -> None:
    code = (
        "import os; data=b'x'*(8*1024*1024+17); "
        "os.write(1, data); os.write(2, data)"
    )
    result = _run(tmp_path, code, timeout=30)

    assert result.exit == 0
    assert result.exception is None
    assert result.stdout_truncated
    assert result.stderr_truncated
    assert result.stdout_path.stat().st_size == STREAM_CAP
    assert result.stderr_path.stat().st_size == STREAM_CAP


def test_timeout_kills_child_process_tree(tmp_path: Path) -> None:
    pid_path = tmp_path / "grandchild.pid"
    grandchild = "import time; time.sleep(60)"
    child = (
        "import pathlib, subprocess, sys, time; "
        f"p=subprocess.Popen([sys.executable, '-c', {grandchild!r}]); "
        f"pathlib.Path({str(pid_path)!r}).write_text(str(p.pid)); time.sleep(60)"
    )

    result = _run(tmp_path, child, timeout=1)

    assert result.timed_out
    assert result.exit is not None
    pid = int(pid_path.read_text())
    deadline = time.monotonic() + 5
    while _running(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _running(pid)


def test_process_boundary_captures_exception(monkeypatch, tmp_path: Path) -> None:
    class BoundaryError(Exception):
        pass

    def fail(*args, **kwargs):
        raise BoundaryError("broken launch")

    monkeypatch.setattr(subprocess, "Popen", fail)
    result = _run(tmp_path, "pass")

    assert result.exit is None
    assert not result.timed_out
    assert result.exception == "BoundaryError: broken launch"



def test_stdout_consumer_observes_full_stream_and_eof_with_both_channels_flooding(tmp_path):
    import tracemalloc

    counts = [0, 0]
    def consume(chunk):
        counts[0] += len(chunk)
        counts[1] += not chunk
    code = ("import os, threading; data=b'x'*(9*1024*1024); "
            "t=threading.Thread(target=lambda: os.write(2,data)); t.start(); "
            "os.write(1,data); t.join()")
    tracemalloc.start()
    try:
        result = run_process((sys.executable, "-c", code), tmp_path, os.environ, 30,
                             tmp_path / "out", tmp_path / "err", stdout_consumer=consume)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result.exit == 0
    assert result.exception is None
    assert result.timed_out is False
    assert counts == [9 * 1024 * 1024, 1]
    assert result.stdout_truncated is True
    assert result.stderr_truncated is True
    assert result.stdout_path.stat().st_size == STREAM_CAP
    assert result.stderr_path.stat().st_size == STREAM_CAP
    assert peak < 2 * 1024 * 1024
    print(f"dual 9 MiB streams: wall={result.secs}s parent_allocation_peak={peak} "
          f"raw_retained={2 * STREAM_CAP}")


def test_consumer_error_still_drains_both_streams(tmp_path):
    def fail(chunk):
        raise ValueError("synthetic observer failure")
    result = run_process(
        (sys.executable, "-c", "import os; data=b'x'*(9*1024*1024); "
         "os.write(1,data); os.write(2,data)"),
        tmp_path, os.environ, 30, tmp_path / "out", tmp_path / "err", stdout_consumer=fail,
    )
    assert result.exit == 0
    assert result.timed_out is False
    assert result.exception == "ValueError: synthetic observer failure"
    assert result.stdout_truncated is True
    assert result.stderr_truncated is True


@pytest.mark.parametrize("launcher_exits", [False, True])
def test_owned_cleanup_survives_launcher_exit_without_touching_other_processes(tmp_path, launcher_exits):
    pid_file = tmp_path / "descendant.pid"
    # This sentinel is independently owned by the test, never by run_process's Job.
    sentinel = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], cwd=tmp_path)
    child = "import time; time.sleep(30)"
    code = ("import subprocess,sys,pathlib,time; "
            f"p=subprocess.Popen([sys.executable,'-c',{child!r}]); "
            f"pathlib.Path({str(pid_file)!r}).write_text(str(p.pid)); "
            + ("print('launcher done', flush=True)" if launcher_exits else "time.sleep(30)"))
    started = time.monotonic()
    try:
        result = _run(tmp_path, code, timeout=5 if launcher_exits else 1)
        assert result.exception is None
        assert result.timed_out is (not launcher_exits)
        if launcher_exits:
            assert result.exit == 0
            assert result.stdout_path.read_bytes().strip() == b"launcher done"
        assert time.monotonic() - started < 8
        pid = int(pid_file.read_text())
        deadline = time.monotonic() + 5
        while _running(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _running(pid)
        assert sentinel.poll() is None
    finally:
        sentinel.terminate()
        sentinel.wait(timeout=5)


def test_ownership_assignment_failure_never_dispatches_command(tmp_path, monkeypatch):
    import frutlups.procs as procs

    if os.name != "nt":
        # Capability-independent refusal test for the same setup boundary.
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(OSError("ownership unavailable")))
    else:
        monkeypatch.setattr(procs._WindowsJob, "assign", lambda *a: (_ for _ in ()).throw(OSError("ownership unavailable")))
    result = _run(tmp_path, "from pathlib import Path; Path('dispatched').touch()")
    assert "ownership unavailable" in result.exception
    assert not (tmp_path / "dispatched").exists()


@pytest.mark.parametrize("stdin", [None, b"", bytes(range(256))], ids=["none", "empty", "binary"])
def test_bootstrap_preserves_literal_argv_and_binary_stdin(tmp_path, monkeypatch, stdin):
    import json

    launches = []
    popen = subprocess.Popen
    def observe(argv, **kwargs):
        launches.append(tuple(argv))
        return popen(argv, **kwargs)
    monkeypatch.setattr(subprocess, "Popen", observe)
    arguments = ["", "two words", 'a"b', "trailing" + chr(92), chr(0x96ea), "&|<>^%!"]
    code = ("import json,sys; "
            "sys.stdout.write(json.dumps([sys.executable, sys.argv[1:]])); "
            "sys.stdout.flush(); sys.stderr.buffer.write(sys.stdin.buffer.read())")
    argv = (sys.executable, "-c", code, *arguments)
    result = run_process(argv, tmp_path, os.environ, 10, tmp_path / "out", tmp_path / "err", stdin)
    assert result.exit == 0
    assert result.timed_out is False
    assert result.exception is None
    assert json.loads(result.stdout_path.read_bytes()) == [sys.executable, arguments]
    assert result.stderr_path.read_bytes() == (stdin or b"")
    if os.name == "nt":
        assert launches[0][0] == (getattr(sys, "_base_executable", None) or sys.executable)
        assert launches[0][1:3] == ("-I", "-S")
    else:
        assert launches == [argv]


@pytest.mark.parametrize("base", [None, "missing"])
def test_bootstrap_runtime_fallback_and_launch_refusal(tmp_path, monkeypatch, base):
    if base == "missing":
        monkeypatch.setattr(sys, "_base_executable", str(tmp_path / "missing-python.exe"), raising=False)
    else:
        monkeypatch.setattr(sys, "_base_executable", None, raising=False)
    result = _run(tmp_path, "from pathlib import Path; Path('dispatched').touch()")
    if base == "missing" and os.name == "nt":
        assert result.exit is None
        assert result.timed_out is False
        assert result.exception.startswith("FileNotFoundError:")
        assert not (tmp_path / "dispatched").exists()
    else:
        assert result.exit == 0
        assert result.timed_out is False
        assert result.exception is None
        assert (tmp_path / "dispatched").exists()
