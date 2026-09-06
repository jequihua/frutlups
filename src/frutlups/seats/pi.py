"""Pi seat adapter and JSONL result classification."""

from __future__ import annotations

import json
import os
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

READ_TOOLS = ("read", "grep", "find", "ls")


def tools_for(role: Role) -> tuple[str, ...]:
    if role is not Role.coder:
        return READ_TOOLS
    shell = "powershell" if os.name == "nt" else "bash"
    return ("read", shell, "edit", "write", "grep", "find", "ls")


def _argv(job: Job, config: Config) -> tuple[str, ...]:
    if not job.provider:
        raise ValueError("Pi jobs require a provider")
    if job.tools != tools_for(job.role):
        raise ValueError("Pi job tools do not match its role")
    return (
        config.pi,
        "-p",
        "--mode",
        "json",
        "--no-session",
        "--provider",
        job.provider,
        "--model",
        job.model,
        "--thinking",
        job.effort,
        "--tools",
        ",".join(job.tools),
        "--no-extensions",
        "--no-skills",
        "--no-prompt-templates",
        f"@{job.prompt_path}",
        "Execute the attached prompt.",
    )


def _integer(value: object) -> int | None:
    return value if not isinstance(value, bool) and isinstance(value, int) else None


def _cost(value: object) -> float | None:
    return float(value) if not isinstance(value, bool) and isinstance(value, (int, float)) else None


def _failure(message: dict) -> FailureClass | None:
    if message.get("stopReason") != "error":
        return None
    text = str(message.get("errorMessage", "")).lower()
    if "401" in text or "authentication_error" in text:
        return FailureClass.auth
    if any(term in text for term in ("429", "rate_limit", "quota", "overloaded")):
        return FailureClass.capacity
    return FailureClass.transport


def _assistant_text(message: dict) -> str:
    content = message.get("content")
    if not isinstance(content, list):
        return ""
    return "".join(
        item["text"]
        for item in content
        if isinstance(item, dict) and isinstance(item.get("text"), str)
    )


def _parse(path: Path, process: ProcessResult) -> tuple[list[dict], bool, FailureClass | None]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        events = [json.loads(line) for line in lines]
    except (OSError, UnicodeError, json.JSONDecodeError):
        return [], False, FailureClass.output
    if not events:
        return [], False, FailureClass.transport
    if any(not isinstance(event, dict) for event in events):
        return [], False, FailureClass.output
    ended = any(event.get("type") == "agent_end" for event in events)
    messages = [
        event.get("message")
        for event in events
        if event.get("type") == "message_end"
        and isinstance(event.get("message"), dict)
        and event["message"].get("role") == "assistant"
    ]
    message = messages[-1] if messages else None
    if not ended:
        return messages, False, FailureClass.output
    if message is not None and (failure := _failure(message)) is not None:
        return messages, True, failure
    if process.exit != 0 or process.exception:
        return messages, True, FailureClass.transport
    return messages, True, None if message is not None else FailureClass.output


def _usage(messages: list[dict]) -> tuple[int | None, int | None, float | None]:
    tokens_in = tokens_out = cost_usd = None
    for message in messages:
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        for key in ("input", "cacheRead", "cacheWrite"):
            if (value := _integer(usage.get(key))) is not None:
                tokens_in = (tokens_in or 0) + value
        if (value := _integer(usage.get("output"))) is not None:
            tokens_out = (tokens_out or 0) + value
        cost = usage.get("cost")
        if isinstance(cost, dict) and (value := _cost(cost.get("total"))) is not None:
            cost_usd = (cost_usd or 0) + value
    return tokens_in, tokens_out, cost_usd


class PiSeat:
    @result_boundary
    def run(self, job: Job, config: Config) -> JobResult:
        stdout, stderr = job_output_paths(job, "stdout.jsonl")
        process = run_process(
            _argv(job, config), job.cwd, child_environment(job, config), job.timeout,
            stdout, stderr,
        )
        if process.timed_out:
            failure = FailureClass.timeout
            messages = []
        elif process.exception:
            failure = FailureClass.transport
            messages = []
        else:
            messages, _, failure = _parse(stdout, process)
        message = messages[-1] if messages else None
        tokens_in, tokens_out, cost_usd = _usage(messages)
        completed = failure is None
        return JobResult(
            "completed" if completed else ("timed_out" if process.timed_out else "failed"),
            failure,
            _assistant_text(message) if isinstance(message, dict) else None,
            process.exit,
            process.secs,
            tokens_in,
            tokens_out,
            cost_usd,
            stdout,
            stdout,
            stderr,
            None if completed else process.exception or f"Pi {failure.value} failure",
        )
