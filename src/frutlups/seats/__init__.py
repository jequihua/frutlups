"""Seat job contracts and shared child-process boundaries."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from frutlups.config import Config


class Role(StrEnum):
    coder = "coder"
    reviewer = "reviewer"
    holistic = "holistic"


class FailureClass(StrEnum):
    transport = "transport"
    auth = "auth"
    capacity = "capacity"
    output = "output"
    timeout = "timeout"


@dataclass(frozen=True)
class Job:
    id: str
    role: Role
    seat_name: str
    adapter: str
    provider: str | None
    model: str
    effort: str
    prompt_path: Path
    prompt_sha: str
    cwd: Path
    tools: tuple[str, ...]
    timeout: float
    expected_notes_path: str | None
    memory_enabled: bool = False


@dataclass(frozen=True)
class JobResult:
    status: Literal["completed", "failed", "timed_out"]
    failure_class: FailureClass | None
    final_text: str | None
    exit: int | None
    secs: float
    tokens_in: int | None
    tokens_out: int | None
    cost_usd: float | None
    events_path: Path | None
    stdout_path: Path
    stderr_path: Path
    diagnostic: str | None


class Seat(Protocol):
    def run(self, job: Job, config: Config) -> JobResult: ...


def child_environment(
    job: Job | str, config: Config, *, memory_enabled: bool = False,
) -> dict[str, str]:
    """Build every child environment from the same credential-free allow list."""
    env = {
        key: os.environ[key]
        for key in config.env_passthrough
        if key in os.environ and key.upper() not in (
            "API_KEY", "PATH", "PYTHONDONTWRITEBYTECODE", "FRUTLUPS_SEAT",
        )
        and not key.upper().endswith(("_API_KEY", "_AUTH_TOKEN", "_TOKEN", "_SECRET"))
    }
    directories = list(config.path_dirs)
    memory_enabled = job.memory_enabled if isinstance(job, Job) else memory_enabled
    if memory_enabled and config.llloom:
        directories.append(str(Path(config.llloom).parent))
    env["PATH"] = os.pathsep.join(directories)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["FRUTLUPS_SEAT"] = job.role.value if isinstance(job, Job) else str(job)
    return env


def result_boundary(run):
    """Keep preparation and parsing errors inside the Seat.run result contract."""
    @wraps(run)
    def guarded(self, job, config):
        try:
            return run(self, job, config)
        except Exception as exc:  # noqa: BLE001 - Seat.run always returns a diagnostic result.
            directory = job.cwd / "local_state" / "frutlups" / "jobs"
            return JobResult(
                "failed", FailureClass.output, None, None, 0, None, None, None,
                None, directory / "stdout", directory / "stderr",
                f"{type(exc).__name__}: {exc}",
            )
    return guarded


def job_output_paths(job: Job, stdout_name: str) -> tuple[Path, Path]:
    """Create the bounded per-job evidence directory and preserve its prompt."""
    if not job.id or job.id in (".", "..") or any(c in job.id for c in "/\\:"):
        raise ValueError("job id must be a single path component")
    directory = job.cwd / "local_state" / "frutlups" / "jobs" / job.id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "prompt.md").write_bytes(Path(job.prompt_path).read_bytes())
    return directory / stdout_name, directory / "stderr.txt"
