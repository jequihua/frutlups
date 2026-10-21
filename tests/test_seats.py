from __future__ import annotations

import json
import os
import shutil
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_loop import project as project

from frutlups import loop, render
from frutlups.procs import ProcessResult, stdout_observer
from frutlups.seats import FailureClass, Job, Role, claude, pi

SAMPLES = Path(__file__).parent / "fixtures" / "samples"


def _job(tmp_path: Path, adapter: str, role: Role = Role.coder, *, memory=False) -> Job:
    prompt = tmp_path / f"{adapter}-prompt.md"
    prompt.write_text("Execute this prompt.", encoding="utf-8")
    tools = pi.tools_for(role) if adapter == "pi" else claude.tools_for(role)
    return Job(
        "job-001",
        role,
        role.value,
        adapter,
        "openai-codex" if adapter == "pi" else None,
        "model-name",
        "high",
        prompt,
        "a" * 64,
        tmp_path,
        tools,
        12,
        "notes.md" if role is Role.coder else None,
        memory,
    )


def _config(tmp_path: Path, passthrough: tuple[str, ...] = ()) -> SimpleNamespace:
    return SimpleNamespace(
        pi="C:/tools/pi.cmd",
        claude="C:/tools/claude.exe",
        path_dirs=(str(tmp_path / "bin"), str(tmp_path / "more-bin")),
        env_passthrough=passthrough,
    )


def _process(stdout: Path, stderr: Path, *, exit: int | None = 0, timed_out=False):
    return ProcessResult(exit, timed_out, 1.25, stdout, stderr, None)


def _fake_process(sample: Path, exit: int = 0, capture: dict | None = None):
    def run(argv, cwd, env, timeout, stdout, stderr, stdin_bytes=None, *, stdout_consumer=None):
        stdout_consumer = stdout_consumer or stdout_observer.get()
        payload = sample.read_bytes()
        if sample.suffix == ".jsonl":
            payload = payload.rstrip(b"\n") + b'\n{"type":"agent_settled"}\n'
        stdout.write_bytes(payload)
        if stdout_consumer is not None:
            stdout_consumer(payload)
            stdout_consumer(b"")
        stderr.write_bytes(b"")
        if capture is not None:
            capture.update(argv=argv, cwd=cwd, env=env, timeout=timeout, stdin=stdin_bytes)
        return _process(stdout, stderr, exit=exit)

    return run


def test_job_contract_is_frozen(tmp_path: Path) -> None:
    job = _job(tmp_path, "pi")

    with pytest.raises(FrozenInstanceError):
        job.model = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("module", "seat", "sample"),
    [(pi, pi.PiSeat, "pi_ok.jsonl"), (claude, claude.ClaudeSeat, "claude_opus_ok.json")],
)
@pytest.mark.parametrize(
    ("memory", "configured", "extra_dirs"),
    [
        pytest.param(False, False, (), id="neither"),
        pytest.param(False, True, (), id="executable-only"),
        pytest.param(True, False, (), id="memory-only"),
        pytest.param(True, True, ("memory-bin",), id="memory-and-executable"),
    ],
)
def test_child_environment_adds_llloom_only_for_memory_jobs_with_local_executable(
    monkeypatch, tmp_path, module, seat, sample, memory, configured, extra_dirs,
):
    monkeypatch.setenv("SAFE_VALUE", "fixture-value")
    monkeypatch.setenv("PATH", str(tmp_path / "ambient-bin"))
    cfg = _config(tmp_path, ("SAFE_VALUE",))
    cfg.llloom = str(tmp_path / "memory-bin" / "llloom.exe") if configured else None
    capture = {}
    monkeypatch.setattr(module, "run_process", _fake_process(SAMPLES / sample, capture=capture))
    adapter = module.__name__.rsplit(".", 1)[-1]
    job = _job(tmp_path, adapter, memory=memory)

    result = seat().run(job, cfg)

    assert result.status == "completed"
    assert capture["env"] == {
        "SAFE_VALUE": "fixture-value",
        "PATH": os.pathsep.join((*cfg.path_dirs, *(str(tmp_path / d) for d in extra_dirs))),
        "PYTHONDONTWRITEBYTECODE": "1",
        "FRUTLUPS_SEAT": "coder",
        **({pi.RECORD_ENV: str(pi.RECORD_CAP)} if module is pi else {
            "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": "0",
            "BASH_DEFAULT_TIMEOUT_MS": "12000", "BASH_MAX_TIMEOUT_MS": "12000",
            "CLAUDE_ASYNC_AGENT_STALL_TIMEOUT_MS": "12000",
        }),
    }


def test_pi_argv_environment_and_completed_sample(monkeypatch, tmp_path: Path) -> None:
    names = (
        "SAFE_VALUE", "OPENAI_API_KEY", "VENDOR_API_KEY", "ANTHROPIC_API_KEY",
        "API_KEY", "Vendor_Auth_Token", "Mixed_Token", "Vendor_Secret",
    )
    for name in names:
        monkeypatch.setenv(name, f"value-for-{name}")
    capture: dict = {}
    monkeypatch.setattr(pi, "run_process", _fake_process(SAMPLES / "pi_ok.jsonl", capture=capture))
    job = _job(tmp_path, "pi")

    result = pi.PiSeat().run(job, _config(tmp_path, names))

    argv = capture["argv"]
    assert argv == (
        "C:/tools/pi.cmd", "-p", "--mode", "json", "--no-session", "--provider",
        "openai-codex", "--model", "model-name", "--thinking", "high", "--tools",
        ",".join(pi.tools_for(Role.coder)), "--no-extensions", "-e", str(pi.extension_path()), "--no-skills",
        "--no-prompt-templates", f"@{job.prompt_path}", render.dispatch_framing("pi"),
    )
    assert "--bare" not in argv and "--api-key" not in argv
    assert capture["env"] == {
        pi.RECORD_ENV: str(pi.RECORD_CAP),
        "SAFE_VALUE": "value-for-SAFE_VALUE",
        "PATH": os.pathsep.join(_config(tmp_path).path_dirs),
        "PYTHONDONTWRITEBYTECODE": "1",
        "FRUTLUPS_SEAT": "coder",
    }
    assert capture["stdin"] is None
    assert result.status == "completed"
    assert result.failure_class is None
    assert result.final_text == "OK"
    assert (result.tokens_in, result.tokens_out) == (575, 5)
    assert result.cost_usd == 0.0030250000000000003
    assert result.events_path == result.stdout_path
    assert result.stdout_path.name == "stdout.jsonl"
    assert result.stdout_path.parent.joinpath("prompt.md").read_text() == "Execute this prompt."


def test_pi_sums_per_message_usage_from_m004_canary(monkeypatch, tmp_path: Path) -> None:
    # Six assistant messages from the 2026-09-05 canary; usage objects are unmodified.
    monkeypatch.setattr(pi, "run_process", _fake_process(SAMPLES / "pi_canary_usage.jsonl"))

    result = pi.PiSeat().run(_job(tmp_path, "pi"), _config(tmp_path))

    assert result.status == "completed"
    assert result.failure_class is None
    assert result.final_text == "<elided>"
    assert result.tokens_in == 36175
    assert result.tokens_out == 1148
    assert result.cost_usd == sum([
        0.020935000000000002, 0.024038999999999998, 0.04471000000000001,
        0.035195000000000004, 0.005433, 0.010123,
    ])


@pytest.fixture
def pi_partial_stream():
    # Session/start followed by the first four recorded assistant message_end events.
    lines = (SAMPLES / "pi_canary_usage.jsonl").read_bytes().splitlines()
    return b"\n".join(lines[:6]) + b"\n"


@pytest.mark.parametrize("failure", [FailureClass.timeout, FailureClass.transport])
@pytest.mark.parametrize("tail", [b"", b'{"type": "\xe2'])
def test_pi_preserves_drained_usage_after_process_failure(
    monkeypatch, tmp_path, pi_partial_stream, failure, tail,
):
    exception = "OSError: synthetic pipe failure" if failure is FailureClass.transport else None

    def failed(argv, cwd, env, timeout, stdout, stderr, *, stdout_consumer=None):
        stdout_consumer = stdout_consumer or stdout_observer.get()
        stdout.write_bytes(pi_partial_stream + tail)
        stdout_consumer(pi_partial_stream + tail)
        stdout_consumer(b"")
        return ProcessResult(1, failure is FailureClass.timeout, 1.25, stdout, stderr, exception)

    monkeypatch.setattr(pi, "run_process", failed)
    result = pi.PiSeat().run(_job(tmp_path, "pi"), _config(tmp_path))

    assert result.status == ("timed_out" if failure is FailureClass.timeout else "failed")
    assert result.failure_class is failure
    assert result.exit == 1
    assert result.secs == 1.25
    assert result.tokens_in == 22445
    assert result.tokens_out == 921
    assert result.cost_usd == pytest.approx(0.124879)
    assert result.diagnostic == (exception or "Pi timeout failure")
    assert result.stdout_path.read_bytes() == pi_partial_stream + tail


@pytest.mark.parametrize("failure", [FailureClass.timeout, FailureClass.transport])
def test_failed_pi_attempts_save_drained_usage_in_result_json(
    project, monkeypatch, pi_partial_stream, failure,
):
    from test_attempts_v2 import materialize, internal_loop
    root, cfg, model, _, _ = materialize(project)
    original_process = pi.run_process

    def failed(argv, cwd, env, timeout, stdout, stderr, *, stdout_consumer=None):
        if env["FRUTLUPS_SEAT"] != "coder":
            return original_process(argv, cwd, env, timeout, stdout, stderr,
                                    stdout_consumer=stdout_consumer)
        stdout_consumer = stdout_consumer or stdout_observer.get()
        stdout.write_bytes(pi_partial_stream)
        stdout_consumer(pi_partial_stream)
        stdout_consumer(b"")
        return ProcessResult(
            1, failure is FailureClass.timeout, 1.25, stdout, stderr,
            "OSError: synthetic pipe failure" if failure is FailureClass.transport else None,
        )

    monkeypatch.setattr(pi, "run_process", failed)
    with pytest.raises(loop._Stop) as caught:
        internal_loop(root, cfg, model)._call("coder", Role.coder, "prompt.md")
    assert caught.value.reason == loop.StopReason.seat_transport
    directories = list((root / "local_state/frutlups/jobs").iterdir())
    assert len(directories) == 2  # The original attempt and its same-round retry.
    for directory in directories:
        result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
        assert result["status"] == ("timed_out" if failure is FailureClass.timeout else "failed")
        assert result["failure_class"] == failure.value
        assert result["tokens_in"] == 22445
        assert result["tokens_out"] == 921
        assert result["cost_usd"] == pytest.approx(0.124879)


@pytest.mark.parametrize("cache_key", ["cacheRead", "cacheWrite"])
def test_pi_counts_cache_and_only_assistant_message_ends(
    monkeypatch, tmp_path: Path, cache_key: str,
) -> None:
    events = [
        json.loads(line)
        for line in (SAMPLES / "pi_canary_usage.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    # Reuse recorded counts to exercise cache writes and duplicate/non-assistant events.
    for event in events[2:-1]:
        usage = event["message"]["usage"]
        usage[cache_key] = usage.pop("cacheRead")
    last = events[-2]["message"]
    last["content"] = [{"type": "text", "text": "Final answer"}]
    events[-1]["messages"] = [last]  # agent_end also repeats the final message.
    for event_type in ("message_start", "message_update", "turn_end"):
        events.insert(2, {"type": event_type, "message": last})
    for role in ("user", "toolResult"):
        # A non-model tool result does not repeat assistant model-call usage.
        other = {key: value for key, value in last.items() if key != "usage"}
        events.append({"type": "message_end", "message": {**other, "role": role}})
    events.append({"type": "agent_settled"})
    sample = tmp_path / "usage.jsonl"
    sample.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
    monkeypatch.setattr(pi, "run_process", _fake_process(sample))

    result = pi.PiSeat().run(_job(tmp_path, "pi"), _config(tmp_path))

    assert result.status == "completed"
    assert result.final_text == "Final answer"
    assert result.tokens_in == 36175
    assert result.tokens_out == 1148
    assert result.cost_usd == sum([
        0.020935000000000002, 0.024038999999999998, 0.04471000000000001,
        0.035195000000000004, 0.005433, 0.010123,
    ])


@pytest.mark.parametrize(
    ("sample", "tokens_in", "tokens_out", "cost"),
    [
        ("claude_opus_ok.json", 4585, 4, 0.0024015),
        ("claude_fable_ok.json", 4693, 4, 0.005488),
    ],
)
def test_claude_argv_stdin_and_completed_samples(
    monkeypatch, tmp_path: Path, sample: str, tokens_in: int, tokens_out: int, cost: float,
) -> None:
    capture: dict = {}
    monkeypatch.setattr(
        claude, "run_process", _fake_process(SAMPLES / sample, capture=capture),
    )
    job = _job(tmp_path, "claude")

    result = claude.ClaudeSeat().run(job, _config(tmp_path))

    argv = capture["argv"]
    assert argv == (
        "C:/tools/claude.exe", "-p", "--model", "model-name", "--effort", "high",
        "--output-format", "json", "--permission-mode", "acceptEdits", "--tools",
        "Read,Edit,Write,Bash,Grep,Glob,Agent", "--strict-mcp-config", "--mcp-config",
        '{"mcpServers":{}}', "--no-session-persistence",
    )
    assert "--bare" not in argv and "--api-key" not in argv
    assert capture["stdin"] == b"Execute this prompt." + render.dispatch_framing("claude").encode()
    assert result.status == "completed"
    assert result.final_text == "OK"
    # Keep the collected legacy parameter identities. The Fable sample's
    # modelUsage covers additional work excluded by top-level usage.
    aggregate_in, aggregate_out = (5219, 16) if sample == "claude_fable_ok.json" else (tokens_in, tokens_out)
    assert result.tokens_in == aggregate_in
    assert result.tokens_out == aggregate_out
    assert result.cost_usd == cost
    assert result.events_path is None
    assert result.stdout_path.name == "stdout.json"


@pytest.mark.parametrize("role", [Role.reviewer, Role.holistic])
def test_reviewer_roles_receive_only_read_tools(tmp_path: Path, role: Role) -> None:
    pi_job = _job(tmp_path, "pi", role)
    claude_job = _job(tmp_path, "claude", role)
    pi_argv = pi._argv(pi_job, _config(tmp_path))
    claude_argv = claude._argv(claude_job, _config(tmp_path))

    assert pi_argv[pi_argv.index("--tools") + 1] == "read,grep,find,ls,subagent"
    assert claude_argv[claude_argv.index("--tools") + 1] == "Read,Grep,Glob,Agent"
    assert claude_argv[claude_argv.index("--permission-mode") + 1] == "dontAsk"


def test_pi_auth_sample_is_auth_despite_exit_zero(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        pi, "run_process", _fake_process(SAMPLES / "pi_auth_fail.jsonl", exit=0),
    )

    result = pi.PiSeat().run(_job(tmp_path, "pi"), _config(tmp_path))

    assert result.status == "failed"
    assert result.exit == 0
    assert result.failure_class is FailureClass.auth


def test_claude_recorded_api_error_is_transport(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        claude,
        "run_process",
        _fake_process(SAMPLES / "claude_transport_fail.json", exit=1),
    )

    result = claude.ClaudeSeat().run(_job(tmp_path, "claude"), _config(tmp_path))

    assert result.status == "failed"
    assert result.failure_class is FailureClass.transport
    assert result.final_text.startswith("API Error:")


@pytest.mark.parametrize("adapter", [pi, claude])
def test_non_json_stdout_is_output(adapter, tmp_path: Path) -> None:
    stdout = tmp_path / "stdout"
    stderr = tmp_path / "stderr"
    stdout.write_text("not json", encoding="utf-8")
    process = _process(stdout, stderr)

    parsed = adapter._parse(stdout, process)

    assert parsed[-1] is FailureClass.output


def test_pi_stream_missing_agent_end_is_output(tmp_path: Path) -> None:
    stdout = tmp_path / "stdout.jsonl"
    message = {"type": "message_end", "message": {"role": "assistant", "content": []}}
    stdout.write_text(json.dumps(message) + "\n", encoding="utf-8")

    _, ended, failure = pi._parse(stdout, _process(stdout, tmp_path / "stderr"))

    assert ended is False
    assert failure is FailureClass.output


@pytest.mark.parametrize(("module", "seat"), [(pi, pi.PiSeat), (claude, claude.ClaudeSeat)])
def test_timed_out_process_is_timeout(monkeypatch, tmp_path: Path, module, seat) -> None:
    def timed_out(argv, cwd, env, timeout, stdout, stderr, stdin_bytes=None, *, stdout_consumer=None):
        stdout.write_bytes(b"")
        stderr.write_bytes(b"")
        return _process(stdout, stderr, exit=1, timed_out=True)

    monkeypatch.setattr(module, "run_process", timed_out)

    result = seat().run(_job(tmp_path, module.__name__.rsplit(".", 1)[-1]), _config(tmp_path))

    assert result.status == "timed_out"
    assert result.failure_class is FailureClass.timeout
    assert result.tokens_in is None
    assert result.tokens_out is None
    assert result.cost_usd is None


@pytest.mark.parametrize(("module", "seat"), [(pi, pi.PiSeat), (claude, claude.ClaudeSeat)])
@pytest.mark.parametrize("failure", ["argv", "job-id", "missing-prompt", "unreadable-prompt"])
def test_prelaunch_failures_return_output_result(monkeypatch, tmp_path, module, seat, failure):
    job = _job(tmp_path, module.__name__.rsplit(".", 1)[-1])
    if failure == "argv":
        job = replace(job, tools=("write",))
    elif failure == "job-id":
        job = replace(job, id="../escape")
    elif failure == "missing-prompt":
        job = replace(job, prompt_path=tmp_path / "missing")
    else:
        job = replace(job, prompt_path=tmp_path)
    monkeypatch.setattr(module, "run_process", lambda *a: pytest.fail("child must not start"))

    result = seat().run(job, _config(tmp_path))

    assert result.status == "failed"
    assert result.failure_class is FailureClass.output
    assert result.exit is None
    assert result.secs == 0
    assert result.diagnostic
    assert "Error:" in result.diagnostic


@pytest.mark.text_defined
@pytest.mark.parametrize("text", ["authentication failed", "unauthorized", "HTTP 401"])
def test_claude_auth_text_rules_are_unverified(tmp_path: Path, text: str) -> None:
    process = _process(tmp_path / "stdout", tmp_path / "stderr", exit=1)

    assert claude._classify({"is_error": True, "result": text}, process) is FailureClass.auth


@pytest.mark.text_defined
@pytest.mark.parametrize("text", ["429", "rate_limit", "quota", "overloaded"])
def test_pi_capacity_text_rules_are_unverified(text: str) -> None:
    message = {"stopReason": "error", "errorMessage": text}

    assert pi._failure(message) is FailureClass.capacity


@pytest.mark.text_defined
@pytest.mark.parametrize("text", ["rate limit", "429", "billing", "overloaded", "quota"])
def test_claude_capacity_text_rules_are_unverified(
    tmp_path: Path, text: str,
) -> None:
    process = _process(tmp_path / "stdout", tmp_path / "stderr", exit=1)

    assert claude._classify({"is_error": True, "result": text}, process) is FailureClass.capacity



def _pi_terminal(text="Final answer", usage=None):
    message = {"role": "assistant", "stopReason": "stop",
               "content": [{"type": "text", "text": text}]}
    if usage is not None:
        message["usage"] = usage
    return (json.dumps({"type": "message_end", "message": message}, ensure_ascii=False).encode()
            + b'\n{"type":"agent_end"}\n{"type":"agent_settled"}\n')


def _stream_process(data, chunk_size=65536):
    def run(argv, cwd, env, timeout, stdout, stderr, *, stdout_consumer=None):
        stdout_consumer = stdout_consumer or stdout_observer.get()
        stdout.write_bytes(data[:8 * 1024 * 1024])
        for offset in range(0, len(data), chunk_size):
            stdout_consumer(data[offset:offset + chunk_size])
        stdout_consumer(b"")
        return _process(stdout, stderr)
    return run


@pytest.mark.parametrize("ending", [b"\n", b"\r\n", b""])
def test_pi_split_utf8_records_and_eof(monkeypatch, tmp_path, ending):
    data = _pi_terminal("Answer: \u20ac\U0001f34e").rstrip(b"\n") + ending
    monkeypatch.setattr(pi, "run_process", _stream_process(data, 1))
    result = pi.PiSeat().run(_job(tmp_path, "pi"), _config(tmp_path))
    assert result.status == "completed"
    assert result.final_text == "Answer: \u20ac\U0001f34e"
    assert result.usage_completeness == "unknown"
    assert result.tokens_in is None
    assert result.tokens_out is None
    assert result.cost_usd is None


@pytest.mark.parametrize(("case", "diagnostic"), [
    ("utf8", "invalid UTF-8"), ("malformed", "malformed"),
    ("oversized", "record exceeds 2 MiB"), ("unterminated-limit", "record exceeds 2 MiB"),
    ("text", "final text exceeds 1 MiB"), ("escaped-surrogate", "invalid UTF-8"),
    pytest.param("missing-end", "missing settled final message", id="missing-end-missing terminal agent_end"),
    ("missing-text", "missing final assistant text"),
    pytest.param("empty", "missing settled final message", id="empty-missing terminal agent_end"),
    ("partial-eof", "malformed"),
])
def test_pi_output_failures_are_specific_and_bounded(monkeypatch, tmp_path, case, diagnostic):
    terminal = _pi_terminal()
    data = {
        "utf8": b'"\xff"\n' + terminal,
        "malformed": b'not JSON\n' + terminal,
        "oversized": b'x' * (pi.RECORD_CAP + 1) + b'\n' + terminal,
        "unterminated-limit": terminal + b'x' * (pi.RECORD_CAP + 1),
        "text": _pi_terminal("\u20ac" * (1024 * 1024 // 3 + 1)),
        "escaped-surrogate": terminal.replace(b"Final answer", b"\\ud800"),
        "missing-end": terminal.split(b"\n")[0],
        "missing-text": b'{"type":"agent_settled"}',
        "empty": b"",
        "partial-eof": terminal + b'{"type":',
    }[case]
    monkeypatch.setattr(pi, "run_process", _stream_process(data, 65535))
    result = pi.PiSeat().run(_job(tmp_path, "pi"), _config(tmp_path))
    assert result.status == "failed"
    assert result.failure_class is FailureClass.output
    assert diagnostic in result.diagnostic
    assert len(result.diagnostic) < 100
    if case in ("text", "escaped-surrogate"):
        assert result.final_text is None


def test_pi_exact_record_and_final_text_limits(monkeypatch, tmp_path):
    padding = b'{"padding":"' + b'x' * (pi.RECORD_CAP - 14) + b'"}'
    assert len(padding) == pi.RECORD_CAP
    data = padding + b"\n" + _pi_terminal("x" * (1024 * 1024))
    monkeypatch.setattr(pi, "run_process", _stream_process(data))
    result = pi.PiSeat().run(_job(tmp_path, "pi"), _config(tmp_path))
    assert result.status == "completed"
    assert len(result.final_text.encode()) == 1024 * 1024


def test_pi_many_completed_messages_keep_bounded_state_and_exact_usage(tmp_path):
    import tracemalloc
    import time

    usage = {"input": 1, "cacheRead": 2, "cacheWrite": 3, "output": 4,
             "cost": {"total": 0.125}}
    record = _pi_terminal("ok", usage).split(b"\n")[0] + b"\n"
    repeats = 30000
    assert len(record) * repeats < 16 * 1024 * 1024
    stream = pi._Stream()
    started = time.perf_counter()
    tracemalloc.start()
    try:
        for _ in range(repeats):
            stream.feed(record)
            stream.feed(b'{"type":"message_update"}\n')
        stream.feed(b'{"type":"agent_end"}\n{"type":"agent_settled"}')
        stream.feed(b"")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 2 * 1024 * 1024
    assert stream.totals[0] == 180000
    assert stream.totals[1] == 120000
    assert stream.totals[2] == 3750
    assert stream.outcome(_process(tmp_path / "out", tmp_path / "err")) == (None, None)
    print(f"30000 assistant messages: wall={time.perf_counter()-started:.3f}s "
          f"parent_allocation_peak={peak} retained_disk=0")


@pytest.mark.parametrize("failure", [FailureClass.timeout, FailureClass.transport])
def test_claude_preserves_usage_on_process_failure(monkeypatch, tmp_path, failure):
    sample = SAMPLES / "claude_opus_ok.json"
    original = _fake_process(sample, exit=1)
    def failed(*args, **kwargs):
        return replace(original(*args, **kwargs), timed_out=failure is FailureClass.timeout)
    monkeypatch.setattr(claude, "run_process", failed)
    result = claude.ClaudeSeat().run(_job(tmp_path, "claude"), _config(tmp_path))
    assert result.failure_class is failure
    assert result.tokens_in == 4585
    assert result.tokens_out == 4
    assert result.cost_usd == 0.0024015
    assert result.usage_completeness == "partial"
    assert result.stdout_path.read_bytes() == sample.read_bytes()



@pytest.mark.parametrize("adapter", ["pi", "claude"])
def test_partial_metrics_remain_known_with_explicit_completeness(monkeypatch, tmp_path, adapter):
    if adapter == "pi":
        data = _pi_terminal(usage={"cacheRead": 8}) + _pi_terminal()
        monkeypatch.setattr(pi, "run_process", _stream_process(data))
        seat = pi.PiSeat()
    else:
        sample = tmp_path / "partial.json"
        sample.write_text(json.dumps({"is_error": False, "result": "ok",
                                     "usage": {"cache_read_input_tokens": 8}}))
        monkeypatch.setattr(claude, "run_process", _fake_process(sample))
        seat = claude.ClaudeSeat()
    result = seat.run(_job(tmp_path, adapter), _config(tmp_path))
    assert result.status == "completed"
    assert result.tokens_in == 8
    assert result.tokens_out is None
    assert result.cost_usd is None
    assert result.usage_completeness == "partial"


def test_pi_auth_marker_after_diagnostic_limit_is_still_auth(monkeypatch, tmp_path):
    message = {"role": "assistant", "stopReason": "error",
               "errorMessage": "x" * 3000 + "401", "content": []}
    data = json.dumps({"type": "message_end", "message": message}).encode() + b"\n"
    monkeypatch.setattr(pi, "run_process", _stream_process(data))
    result = pi.PiSeat().run(_job(tmp_path, "pi"), _config(tmp_path))
    assert result.failure_class is FailureClass.auth
    assert result.diagnostic == "Pi auth failure"



@pytest.mark.parametrize("reason", ["pending", "toolUse", "length"])
def test_pi_nonfinal_assistant_stop_is_output(monkeypatch, tmp_path, reason):
    data = _pi_terminal().replace(b'"stopReason": "stop"',
                                  json.dumps({"stopReason": reason}).encode()[1:-1])
    monkeypatch.setattr(pi, "run_process", _stream_process(data))
    result = pi.PiSeat().run(_job(tmp_path, "pi"), _config(tmp_path))
    assert result.failure_class is FailureClass.output
    assert result.diagnostic == "Pi missing final assistant text"



def test_pi_observer_scope_restored_even_when_wrapper_raises(monkeypatch, tmp_path):
    def previous(chunk):
        pytest.fail("outer observer must not consume Pi output")
    def failed(argv, cwd, env, timeout, stdout, stderr):
        assert stdout_observer.get() is not previous
        raise OSError("synthetic wrapper failure")
    token = stdout_observer.set(previous)
    try:
        monkeypatch.setattr(pi, "run_process", failed)
        result = pi.PiSeat().run(_job(tmp_path, "pi"), _config(tmp_path))
        assert result.status == "failed"
        assert stdout_observer.get() is previous
    finally:
        stdout_observer.reset(token)



def test_claude_missing_cache_metrics_make_usage_partial(monkeypatch, tmp_path):
    sample = tmp_path / "usage.json"
    sample.write_text(json.dumps({"is_error": False, "result": "ok", "total_cost_usd": 0.25,
                                 "usage": {"input_tokens": 8, "output_tokens": 3}}))
    monkeypatch.setattr(claude, "run_process", _fake_process(sample))
    result = claude.ClaudeSeat().run(_job(tmp_path, "claude"), _config(tmp_path))
    assert result.status == "completed"
    assert result.tokens_in == 8
    assert result.tokens_out == 3
    assert result.cost_usd == 0.25
    assert result.usage_completeness == "partial"
