"""Bounded subprocess execution with whole-tree timeout termination."""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Mapping, Sequence

STREAM_CAP = 8 * 1024 * 1024
_CHUNK_SIZE = 64 * 1024


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
) -> None:
    try:
        written = 0
        with source, path.open("wb") as destination:
            while chunk := source.read(_CHUNK_SIZE):
                remaining = max(0, STREAM_CAP - written)
                if remaining:
                    kept = chunk[:remaining]
                    destination.write(kept)
                    written += len(kept)
                if len(chunk) > remaining:
                    truncated[stream] = True
    except Exception as exc:
        errors.append(exc)


def _terminate_tree(process: subprocess.Popen[bytes], errors: list[Exception]) -> None:
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except Exception as exc:
        errors.append(exc)
        try:
            process.kill()
        except Exception as fallback_exc:
            errors.append(fallback_exc)


def run_process(
    argv: Sequence[str],
    cwd: Path,
    env: Mapping[str, str],
    timeout: float,
    stdout_path: Path,
    stderr_path: Path,
    stdin_bytes: bytes | None = None,
) -> ProcessResult:
    """Run one process while draining bounded output directly to files."""
    started = time.monotonic()
    stdout_path, stderr_path = Path(stdout_path), Path(stderr_path)
    process: subprocess.Popen[bytes] | None = None
    input_file: BinaryIO | None = None
    threads: list[threading.Thread] = []
    errors: list[Exception] = []
    truncated = {"stdout": False, "stderr": False}
    timed_out = False

    try:
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        platform_options = (
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            if os.name == "nt"
            else {"start_new_session": True}
        )
        if stdin_bytes is not None:
            input_file = tempfile.TemporaryFile()
            input_file.write(stdin_bytes)
            input_file.seek(0)
        try:
            process = subprocess.Popen(
                tuple(argv), cwd=cwd, env=dict(env), stdin=input_file or subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, **platform_options,
            )
        finally:
            if input_file is not None:
                input_file.close()
        assert process.stdout is not None and process.stderr is not None
        threads = [
            threading.Thread(
                target=_drain,
                args=(process.stdout, stdout_path, "stdout", truncated, errors),
                daemon=True,
            ),
            threading.Thread(
                target=_drain,
                args=(process.stderr, stderr_path, "stderr", truncated, errors),
                daemon=True,
            ),
        ]
        for thread in threads:
            thread.start()

        deadline = started + timeout
        try:
            process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            timed_out = True
        if not timed_out:
            for thread in threads:
                thread.join(max(0, deadline - time.monotonic()))
            timed_out = any(thread.is_alive() for thread in threads)
        if timed_out:
            _terminate_tree(process, errors)
            if process.poll() is None:
                process.wait(timeout=10)
        drain_deadline = time.monotonic() + 10
        for thread in threads:
            thread.join(max(0, drain_deadline - time.monotonic()))
        if any(thread.is_alive() for thread in threads):
            errors.append(TimeoutError("process pipes did not close after tree termination"))
    except Exception as exc:
        errors.append(exc)
        if process is not None and process.poll() is None:
            _terminate_tree(process, errors)
    exception = None if not errors else f"{type(errors[0]).__name__}: {errors[0]}"
    return ProcessResult(
        None if process is None else process.returncode,
        timed_out,
        round(time.monotonic() - started, 3),
        stdout_path,
        stderr_path,
        exception,
        truncated["stdout"],
        truncated["stderr"],
    )
