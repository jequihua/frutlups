from __future__ import annotations

import json
import os
import shutil
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from frutlups.procs import ProcessResult
from frutlups.seats import FailureClass, Job, Role, claude, pi

SAMPLES = Path(__file__).parent / "fixtures" / "samples"


def _job(tmp_path: Path, adapter: str, role: Role = Role.coder) -> Job:
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
    def run(argv, cwd, env, timeout, stdout, stderr, stdin_bytes=None):
        shutil.copyfile(sample, stdout)
        stderr.write_bytes(b"")
        if capture is not None:
            capture.update(argv=argv, cwd=cwd, env=env, timeout=timeout, stdin=stdin_bytes)
        return _process(stdout, stderr, exit=exit)

    return run


def test_job_contract_is_frozen(tmp_path: Path) -> None:
    job = _job(tmp_path, "pi")

    with pytest.raises(FrozenInstanceError):
        job.model = "changed"  # type: ignore[misc]


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
        ",".join(pi.tools_for(Role.coder)), "--no-extensions", "--no-skills",
        "--no-prompt-templates", f"@{job.prompt_path}", "Execute the attached prompt.",
    )
    assert "--bare" not in argv and "--api-key" not in argv
    assert capture["env"] == {
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
        events.append({"type": event_type, "message": last})
    for role in ("user", "toolResult"):
        events.append({"type": "message_end", "message": {**last, "role": role}})
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
        "Read,Edit,Write,Bash,Grep,Glob", "--strict-mcp-config", "--mcp-config",
        '{"mcpServers":{}}', "--no-session-persistence",
    )
    assert "--bare" not in argv and "--api-key" not in argv
    assert capture["stdin"] == b"Execute this prompt."
    assert result.status == "completed"
    assert result.final_text == "OK"
    assert (result.tokens_in, result.tokens_out, result.cost_usd) == (
        tokens_in, tokens_out, cost,
    )
    assert result.events_path is None
    assert result.stdout_path.name == "result.json"


@pytest.mark.parametrize("role", [Role.reviewer, Role.holistic])
def test_reviewer_roles_receive_only_read_tools(tmp_path: Path, role: Role) -> None:
    pi_job = _job(tmp_path, "pi", role)
    claude_job = _job(tmp_path, "claude", role)
    pi_argv = pi._argv(pi_job, _config(tmp_path))
    claude_argv = claude._argv(claude_job, _config(tmp_path))

    assert pi_argv[pi_argv.index("--tools") + 1] == "read,grep,find,ls"
    assert claude_argv[claude_argv.index("--tools") + 1] == "Read,Grep,Glob"
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
    def timed_out(argv, cwd, env, timeout, stdout, stderr, stdin_bytes=None):
        stdout.write_bytes(b"")
        stderr.write_bytes(b"")
        return _process(stdout, stderr, exit=1, timed_out=True)

    monkeypatch.setattr(module, "run_process", timed_out)

    result = seat().run(_job(tmp_path, module.__name__.rsplit(".", 1)[-1]), _config(tmp_path))

    assert result.status == "timed_out"
    assert result.failure_class is FailureClass.timeout


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


@pytest.mark.unverified_until_m004
@pytest.mark.parametrize("text", ["authentication failed", "unauthorized", "HTTP 401"])
def test_claude_auth_text_rules_are_unverified_until_m004(tmp_path: Path, text: str) -> None:
    process = _process(tmp_path / "stdout", tmp_path / "stderr", exit=1)

    assert claude._classify({"is_error": True, "result": text}, process) is FailureClass.auth


@pytest.mark.unverified_until_m004
@pytest.mark.parametrize("text", ["429", "rate_limit", "quota", "overloaded"])
def test_pi_capacity_text_rules_are_unverified_until_m004(text: str) -> None:
    message = {"stopReason": "error", "errorMessage": text}

    assert pi._failure(message) is FailureClass.capacity


@pytest.mark.unverified_until_m004
@pytest.mark.parametrize("text", ["rate limit", "429", "billing", "overloaded", "quota"])
def test_claude_capacity_text_rules_are_unverified_until_m004(
    tmp_path: Path, text: str,
) -> None:
    process = _process(tmp_path / "stdout", tmp_path / "stderr", exit=1)

    assert claude._classify({"is_error": True, "result": text}, process) is FailureClass.capacity
