"""Claude Code seat adapter and JSON result classification."""

from __future__ import annotations

import json
import math
from pathlib import Path

from frutlups.config import Config
from frutlups.procs import ProcessResult, run_process
from frutlups.render import dispatch_framing

from . import (
    FINAL_TEXT_CAP,
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
    return (WRITE_TOOLS if role is Role.coder else READ_TOOLS) + ("Agent",)


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
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


def _cost(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except OverflowError:
        return None
    return value if math.isfinite(value) and 0 <= value <= 2**63 - 1 else None


def _tokens_in(usage: object) -> int | None:
    if not isinstance(usage, dict):
        return None
    counts = [_integer(usage.get(key)) for key in (
        "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
    )]
    known = [count for count in counts if count is not None]
    return sum(known) if known else None


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
    except (OSError, UnicodeError, ValueError, RecursionError):
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
        stdout, stderr = job_output_paths(job, "stdout.json")
        env = child_environment(job, config)
        env["CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS"] = "0"
        for name in ("BASH_DEFAULT_TIMEOUT_MS", "BASH_MAX_TIMEOUT_MS",
                     "CLAUDE_ASYNC_AGENT_STALL_TIMEOUT_MS"):
            env[name] = str(math.ceil(job.timeout * 1000))
        process = run_process(
            _argv(job, config), job.cwd, env, job.timeout,
            stdout, stderr, Path(job.prompt_path).read_bytes() + dispatch_framing("claude").encode(),
        )
        data, failure = _parse(stdout, process)
        if process.timed_out:
            failure = FailureClass.timeout
        elif process.exception:
            failure = FailureClass.transport
        text = data.get("result") if isinstance(data, dict) else None
        diagnostic = None
        if text is not None:
            try:
                if len(text.encode("utf-8")) > FINAL_TEXT_CAP:
                    text = None
                    diagnostic = "Claude final text exceeds 1 MiB UTF-8 limit"
            except UnicodeError:
                text = None
                diagnostic = "Claude final text contains invalid UTF-8"
        if process.stdout_truncated:
            diagnostic = "Claude raw stdout exceeds 8 MiB capture limit"
        if diagnostic and failure is None:
            failure = FailureClass.output
        metrics, completeness = _metrics(data, failure)
        completed = failure is None
        return JobResult(
            "completed" if completed else ("timed_out" if process.timed_out else "failed"),
            failure,
            text,
            process.exit,
            process.secs,
            *metrics,
            None,
            stdout,
            stderr,
            None if completed else (process.exception or diagnostic or f"Claude {failure.value} failure")[:2048],
            process.stdout_truncated, process.stderr_truncated, completeness,
        )


def _metrics(data, failure):
    data = data if isinstance(data, dict) else {}
    usage = data.get("usage", {})
    fallback = (_tokens_in(usage),
                _integer(usage.get("output_tokens")) if isinstance(usage, dict) else None)
    cost = _cost(data.get("total_cost_usd"))
    models = data.get("modelUsage")
    totals = None
    if isinstance(models, dict) and models:
        counts = [[_integer(entry.get(key)) for key in (
            "inputTokens", "cacheReadInputTokens", "cacheCreationInputTokens", "outputTokens",
        )] if isinstance(entry, dict) else [None] for entry in models.values()]
        if all(all(value is not None for value in row) for row in counts):
            summed = (sum(sum(row[:3]) for row in counts), sum(row[3] for row in counts))
            if all(value <= 2**63 - 1 for value in summed):
                totals = summed
    metrics = (*(totals if totals is not None else fallback), cost)
    completeness = ("unknown" if all(value is None for value in metrics) else
                    "complete" if failure is None and totals is not None and cost is not None
                    else "partial")
    return metrics, completeness
