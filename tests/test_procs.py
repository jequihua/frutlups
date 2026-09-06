from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

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
