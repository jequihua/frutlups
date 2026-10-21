"""Pi seat adapter and JSONL result classification."""

from __future__ import annotations

import json
import math
import os
from importlib.resources import files
from pathlib import Path

from frutlups.config import Config, DEFAULT_PI_RECORD_BYTES, MAX_PI_RECORD_BYTES
from frutlups.procs import ProcessResult, run_process, stdout_observer
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

READ_TOOLS = ("read", "grep", "find", "ls")
RECORD_ENV = "FRUTLUPS_PI_RECORD_BYTES"


def extension_path():
    return files("frutlups.seats").joinpath("pi_delegate", "index.ts")


def record_limit(job: Job, config: Config) -> int:
    cap = job.pi_record_bytes
    if type(cap) is not int or not 0 < cap <= MAX_PI_RECORD_BYTES:
        raise ValueError("limits.pi_record_mib: invalid encoded-record byte limit")
    if cap != getattr(config, "pi_record_bytes", DEFAULT_PI_RECORD_BYTES):
        raise ValueError("Pi job record limit differs from limits.pi_record_mib")
    return cap


def tools_for(role: Role) -> tuple[str, ...]:
    if role is not Role.coder:
        return READ_TOOLS + ("subagent",)
    shell = "powershell" if os.name == "nt" else "bash"
    return ("read", shell, "edit", "write", "grep", "find", "ls", "subagent")


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
        "-e",
        str(extension_path()),
        "--no-skills",
        "--no-prompt-templates",
        f"@{job.prompt_path}",
        dispatch_framing("pi"),
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


RECORD_CAP = DEFAULT_PI_RECORD_BYTES


def _reject_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


class _Stream:
    """One bounded record, latest assistant, and running usage; never event history."""

    def __init__(self, record_cap=RECORD_CAP):
        if type(record_cap) is not int or not 0 < record_cap <= MAX_PI_RECORD_BYTES:
            raise ValueError("limits.pi_record_mib: invalid encoded-record byte limit")
        self.record_cap = record_cap
        self.pending = bytearray()
        self.discarding = False
        self.finished = False
        self.ended = False
        self.assistant_pending = False
        self.message = None
        self.provider_failure = None
        self.final_text = None
        self.diagnostic = None
        self.totals = [None, None, None]
        self.usage_complete = True

    def error(self, diagnostic):
        self.diagnostic = self.diagnostic or diagnostic
        self.usage_complete = False

    def feed(self, chunk):
        if self.finished:
            return
        if not chunk:
            self.finished = True
            if self.pending and not self.discarding:
                self.record(bytes(self.pending))
            self.pending.clear()
            return
        start = 0
        while start < len(chunk):
            end = chunk.find(b"\n", start)
            stop = len(chunk) if end < 0 else end
            if not self.discarding:
                if len(self.pending) + stop - start > self.record_cap:
                    self.error(f"Pi JSONL record exceeds {self.record_cap / (1024 * 1024):g} MiB "
                               f"limit ({self.record_cap} bytes; limits.pi_record_mib)")
                    self.pending.clear()
                    self.discarding = True
                else:
                    self.pending.extend(chunk[start:stop])
            if end < 0:
                break
            if not self.discarding:
                self.record(bytes(self.pending))
            self.pending.clear()
            self.discarding = False
            start = end + 1

    def record(self, raw):
        try:
            event = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
            if not isinstance(event, dict):
                raise ValueError("record is not an object")
        except UnicodeError:
            self.error("Pi JSONL record contains invalid UTF-8")
            return
        except (ValueError, RecursionError):
            self.error("Pi JSONL record is malformed")
            return
        kind = event.get("type")
        if kind == "agent_start":
            self.ended = False
        if kind == "agent_settled":
            self.ended = True
        if kind == "turn_start":
            self.assistant_pending = True
        if kind == "auto_retry_end" and event.get("success") is False:
            self.provider_failure = FailureClass.transport
        message = event.get("message")
        if not isinstance(message, dict):
            return
        assistant = message.get("role") == "assistant"
        if kind == "message_start" and assistant:
            self.assistant_pending = True
        if kind != "message_end" or message.get("role") not in ("assistant", "toolResult"):
            return
        if assistant:
            self.ended = False
            self.assistant_pending = False
            # Keep only classification fields, not tool calls or event history.
            self.provider_failure = _failure(message)
            self.message = {"stopReason": str(message.get("stopReason", ""))[:64]}
            text = _assistant_text(message)
            try:
                oversized = len(text.encode("utf-8")) > FINAL_TEXT_CAP
            except UnicodeError:
                self.error("Pi final text contains invalid UTF-8")
                oversized = True
            self.final_text = None if oversized else text
            if oversized:
                self.error("Pi final text exceeds 1 MiB UTF-8 limit")
        usage = message.get("usage")
        details = message.get("details")
        if not assistant and usage is None:
            if message.get("toolName") != "subagent":
                return
            # Partial delegation omits native Usage; its known values travel in details.usage.
            self.usage_complete = False
            usage = details.get("usage") if isinstance(details, dict) else None
        if message.get("role") == "toolResult" and message.get("isError") is True:
            self.usage_complete = False
        if (message.get("role") == "toolResult" and message.get("toolName") == "subagent"
                and isinstance(details, dict) and details.get("usage_completeness") != "complete"):
            self.usage_complete = False
        values = _usage([{"usage": usage}])
        if not isinstance(usage, dict) or any(
            _integer(usage.get(key)) is None
            for key in ("input", "cacheRead", "cacheWrite", "output")
        ) or values[2] is None:
            self.usage_complete = False
        for index, value in enumerate(values):
            if value is not None:
                total = (self.totals[index] or 0) + value
                if not math.isfinite(total) or abs(total) > 2**63 - 1:
                    self.error("Pi usage exceeds numeric limit")
                else:
                    self.totals[index] = total

    def outcome(self, process):
        if process.timed_out:
            return FailureClass.timeout, "Pi timeout failure"
        if process.exception:
            return FailureClass.transport, process.exception[:2048]
        if self.provider_failure is not None:
            return self.provider_failure, f"Pi {self.provider_failure.value} failure"
        if process.exit != 0:
            return FailureClass.transport, "Pi transport failure"
        if self.diagnostic:
            return FailureClass.output, self.diagnostic
        if not self.finished or not self.ended or self.assistant_pending:
            return FailureClass.output, "Pi missing settled final message"
        if (self.message is None or not self.final_text
                or self.message["stopReason"] not in ("", "stop")):
            return FailureClass.output, "Pi missing final assistant text"
        return None, None

    def completeness(self, failure):
        if all(value is None for value in self.totals):
            return "unknown"
        return "complete" if self.usage_complete and failure is None else "partial"


def _parse(path: Path, process: ProcessResult, record_cap=RECORD_CAP) -> tuple[list[dict], bool, FailureClass | None]:
    """Bounded file inspection helper; live runs observe the full pipe instead."""
    stream = _Stream(record_cap)
    try:
        with path.open("rb") as source:
            while chunk := source.read(64 * 1024):
                stream.feed(chunk)
        stream.feed(b"")
    except OSError:
        stream.error("Pi stdout could not be read")
    failure, _ = stream.outcome(process)
    messages = [] if stream.message is None else [
        {**stream.message, "content": [{"text": stream.final_text}]}]
    return messages, stream.ended, failure


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
        cap = record_limit(job, config)
        stdout, stderr = job_output_paths(job, "stdout.jsonl")
        stream = _Stream(cap)
        env = child_environment(job, config)
        env[RECORD_ENV] = str(cap)
        token = stdout_observer.set(stream.feed)
        try:
            process = run_process(
                _argv(job, config), job.cwd, env, job.timeout,
                stdout, stderr,
            )
        finally:
            stdout_observer.reset(token)
        failure, diagnostic = stream.outcome(process)
        completed = failure is None
        return JobResult(
            "completed" if completed else ("timed_out" if process.timed_out else "failed"),
            failure, stream.final_text, process.exit, process.secs, *stream.totals,
            stdout, stdout, stderr, diagnostic,
            process.stdout_truncated, process.stderr_truncated,
            stream.completeness(failure),
        )
