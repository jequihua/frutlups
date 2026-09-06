"""Claude Code seat adapter and JSON result classification."""

from __future__ import annotations

import json
from pathlib import Path

from frutlups.config import Config
from frutlups.procs import ProcessResult, run_process

from . import (
    FailureClass,
    Job,
    JobResult,
    Role,
    child_environment,
    job_output_paths,
    result_boundary,
)

READ_TOOLS = ("Read", "Grep", "Glob")
WRITE_TOOLS = ("Read", "Edit", "Write", "Bash", "Grep", "Glob")


def tools_for(role: Role) -> tuple[str, ...]:
    return WRITE_TOOLS if role is Role.coder else READ_TOOLS


def _argv(job: Job, config: Config) -> tuple[str, ...]:
    if job.tools != tools_for(job.role):
        raise ValueError("Claude job tools do not match its role")
    mode = "acceptEdits" if job.role is Role.coder else "dontAsk"
    return (
        config.claude,
        "-p",
        "--model",
        job.model,
        "--effort",
        job.effort,
        "--output-format",
        "json",
        "--permission-mode",
        mode,
        "--tools",
        ",".join(job.tools),
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--no-session-persistence",
    )


def _integer(value: object) -> int | None:
    return value if not isinstance(value, bool) and isinstance(value, int) else None


def _cost(value: object) -> float | None:
    numeric = not isinstance(value, bool) and isinstance(value, (int, float))
    return float(value) if numeric else None


def _tokens_in(usage: object) -> int | None:
    if not isinstance(usage, dict):
        return None
    counts = [_integer(usage.get("input_tokens"))]
    counts += [_integer(usage.get(key, 0)) for key in (
        "cache_read_input_tokens", "cache_creation_input_tokens",
    )]
    return sum(counts) if all(count is not None for count in counts) else None


def _classify(data: dict, process: ProcessResult) -> FailureClass | None:
    if data.get("is_error") is True:
        text = str(data.get("result", "")).lower()
        if any(term in text for term in ("authentication", "unauthorized", "401")):
            return FailureClass.auth
        capacity = ("rate limit", "rate_limit", "429", "billing", "overloaded", "quota")
        if any(term in text for term in capacity):
            return FailureClass.capacity
        return FailureClass.transport
    if process.exit != 0 or process.exception:
        return FailureClass.transport
    return None


def _parse(path, process: ProcessResult) -> tuple[dict | None, FailureClass | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, FailureClass.output
    valid = (
        isinstance(data, dict)
        and isinstance(data.get("is_error"), bool)
        and isinstance(data.get("result"), str)
    )
    if not valid:
        return None, FailureClass.output
    return data, _classify(data, process)


class ClaudeSeat:
    @result_boundary
    def run(self, job: Job, config: Config) -> JobResult:
        stdout, stderr = job_output_paths(job, "result.json")
        process = run_process(
            _argv(job, config), job.cwd, child_environment(job, config), job.timeout,
            stdout, stderr, Path(job.prompt_path).read_bytes(),
        )
        if process.timed_out:
            failure = FailureClass.timeout
            data = None
        elif process.exception:
            failure = FailureClass.transport
            data = None
        else:
            data, failure = _parse(stdout, process)
        usage = data.get("usage", {}) if isinstance(data, dict) else {}
        completed = failure is None
        return JobResult(
            "completed" if completed else ("timed_out" if process.timed_out else "failed"),
            failure,
            data.get("result") if isinstance(data, dict) else None,
            process.exit,
            process.secs,
            _tokens_in(usage),
            _integer(usage.get("output_tokens")) if isinstance(usage, dict) else None,
            _cost(data.get("total_cost_usd")) if isinstance(data, dict) else None,
            None,
            stdout,
            stderr,
            None if completed else process.exception or f"Claude {failure.value} failure",
        )
