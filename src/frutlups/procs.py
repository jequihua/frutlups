"""Bounded subprocess execution with whole-tree timeout termination."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys

# Keep the gated child lightweight: Git calls use this same ownership boundary.
if __name__ == "__main__":
    # The parent sends argv only after assigning this process to its Windows Job.
    import msvcrt

    control = msvcrt.open_osfhandle(int(sys.argv[2]), os.O_WRONLY | os.O_BINARY)
    os.set_inheritable(control, False)
    command = json.loads(sys.stdin.buffer.read())
    try:
        supplied = command["input"]
        result = subprocess.run(
            command["argv"],
            shell=False,
            check=False,
            input=base64.b64decode(supplied) if supplied is not None else None,
            **({"stdin": subprocess.DEVNULL} if supplied is None else {}),
        )
        os.write(control, b"1")
        os.close(control)
        raise SystemExit(result.returncode)
    except OSError as exc:
        os.write(control, b"0")
        os.close(control)
        print(f"verification launch failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(254)


import signal
import tempfile
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Mapping, Sequence

STREAM_CAP = 8 * 1024 * 1024
_CHUNK_SIZE = 64 * 1024
# Scope observation without changing arguments forwarded by existing process wrappers.
stdout_observer: ContextVar[Callable[[bytes], None] | None] = ContextVar(
    "stdout_observer", default=None,
)


@dataclass(frozen=True)
class ProcessResult:
    exit: int | None
    timed_out: bool
    secs: float
    stdout_path: Path
    stderr_path: Path
    exception: str | None
    stdout_truncated: bool = False
    stderr_truncated: bool = False


def _drain(
    source: BinaryIO, path: Path, stream: str, truncated: dict[str, bool], errors: list[Exception],
    consumer: Callable[[bytes], None] | None = None,
    tail_limit: int | None = None,
) -> None:
    try:
        written = 0
        tail = bytearray()
        total = 0
        with source, path.open("wb") as destination:
            while chunk := source.read(_CHUNK_SIZE):
                if consumer is not None:
                    try:
                        consumer(chunk)
                    except Exception as exc:
                        errors.append(exc)
                        consumer = None  # Keep draining even if semantic observation fails.
                if tail_limit is not None:
                    total += len(chunk)
                    tail.extend(chunk)
                    if len(tail) > tail_limit:
                        del tail[:-tail_limit]
                        truncated[stream] = True
                    continue
                remaining = max(0, STREAM_CAP - written)
                if remaining:
                    kept = chunk[:remaining]
                    destination.write(kept)
                    written += len(kept)
                if len(chunk) > remaining:
                    truncated[stream] = True
            if tail_limit is not None:
                # A retained suffix may start inside a path, secret or UTF-8 codepoint.
                # Drop that incomplete line before anyone can decode or publish it.
                data = bytes(tail)
                if total > tail_limit:
                    data = data.partition(b"\n")[2]
                destination.write(data)
            if consumer is not None:
                consumer(b"")  # EOF, including an empty stream.
    except Exception as exc:
        errors.append(exc)


class _WindowsJob:
    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", Basic),
                ("IoInfo", ctypes.c_uint64 * 6),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(
            self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)
        ):
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)

    def assign(self, process):
        import ctypes

        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            import ctypes

            if not self.api.CloseHandle(self.handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = None


def run_process(
    argv: Sequence[str],
    cwd: Path,
    env: Mapping[str, str],
    timeout: float,
    stdout_path: Path,
    stderr_path: Path,
    stdin_bytes: bytes | None = None,
    *,
    stdout_consumer: Callable[[bytes], None] | None = None,
    tail_limit: int | None = None,
) -> ProcessResult:
    """Drain owned children; retain the legacy prefix or a bounded diagnostic tail.

    Windows gates command launch on assignment to a kill-on-close Job. POSIX
    owns a new session. Cleanup also runs when a launcher has already exited.
    Deliberately escaped descendants are outside this ownership guarantee.
    """
    if timeout <= 0 or tail_limit is not None and not 1 <= tail_limit <= 16384:
        raise ValueError("invalid process timeout or diagnostic tail allowance")
    started = time.monotonic()
    if stdout_consumer is None:
        stdout_consumer = stdout_observer.get()
    stdout_path, stderr_path = Path(stdout_path), Path(stderr_path)
    process = job = input_file = None
    control_read = control_write = None
    threads = []
    errors = []
    truncated = {"stdout": False, "stderr": False}
    timed_out = False
    launched = b""
    try:
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        job = _WindowsJob() if os.name == "nt" else None
        command = list(argv)
        startup = None
        if job:
            import msvcrt

            control_read, control_write = os.pipe()
            os.set_inheritable(control_write, True)
            handle = msvcrt.get_osfhandle(control_write)
            startup = subprocess.STARTUPINFO()
            startup.lpAttributeList = {"handle_list": [handle]}
            # The isolated stdlib-only bootstrap needs this interpreter's runtime,
            # not the extra Windows venv redirector. The user's argv is unchanged.
            executable = getattr(sys, "_base_executable", None) or sys.executable
            command = [executable, "-I", "-S", str(Path(__file__).resolve()),
                       "--child", str(handle)]
        elif stdin_bytes is not None:
            input_file = tempfile.TemporaryFile()
            input_file.write(stdin_bytes)
            input_file.seek(0)
        process = subprocess.Popen(
            command, cwd=cwd, env=dict(env),
            stdin=subprocess.PIPE if job else input_file or subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
            start_new_session=not bool(job), startupinfo=startup,
        )
        if job:
            os.close(control_write)
            control_write = None
            job.assign(process)  # No user command has been sent yet.
        threads = [
            threading.Thread(target=_drain,
                args=(process.stdout, stdout_path, "stdout", truncated, errors,
                      stdout_consumer, tail_limit), daemon=True),
            threading.Thread(target=_drain,
                args=(process.stderr, stderr_path, "stderr", truncated, errors,
                      None, tail_limit), daemon=True),
        ]
        for thread in threads:
            thread.start()
        if job:
            payload = json.dumps({"argv": list(argv), "input":
                base64.b64encode(stdin_bytes).decode("ascii")
                if stdin_bytes is not None else None}).encode("utf-8")

            def feed():
                try:
                    process.stdin.write(payload)
                except BrokenPipeError:
                    pass
                except Exception as exc:
                    errors.append(exc)
                finally:
                    process.stdin.close()

            writer = threading.Thread(target=feed, daemon=True)
            writer.start()
            threads.append(writer)
        try:
            process.wait(timeout=max(0.001, started + timeout - time.monotonic()))
        except subprocess.TimeoutExpired:
            timed_out = True
    except Exception as exc:
        errors.append(exc)
    finally:
        try:
            if job:
                job.close()
            elif process:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process:
                if process.poll() is None:
                    process.kill()  # Only this owned bootstrap, even if assignment failed.
                process.wait(timeout=10)
        except Exception as exc:
            errors.append(exc)
        for thread in threads:
            thread.join(timeout=5)
        if any(thread.is_alive() for thread in threads):
            errors.append(OSError("owned process pipes did not close after cleanup"))
        if input_file is not None:
            input_file.close()
        if control_write is not None:
            os.close(control_write)
        if control_read is not None:
            # The non-inherited acknowledgement pipe belongs only to the bootstrap.
            launched = os.read(control_read, 1) if process is not None and process.poll() is not None else b""
            os.close(control_read)
    if job and launched != b"1" and not timed_out and not errors:
        errors.append(OSError("owned command launch could not be established"))
    exception = None if not errors else f"{type(errors[0]).__name__}: {errors[0]}"
    return ProcessResult(
        None if process is None else process.returncode, timed_out,
        round(time.monotonic() - started, 3), stdout_path, stderr_path, exception,
        truncated["stdout"], truncated["stderr"],
    )
