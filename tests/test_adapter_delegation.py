"""Offline main-invocation contracts and the actual packaged TypeScript handler."""
import importlib.resources
import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest
from frutlups import loop, render
from frutlups.config import Config
from frutlups.procs import run_process
from frutlups.seats import FailureClass, Job, Role, claude, pi
from test_config import _write_config, EXAMPLE
from test_seats import _config, _job, _process, _stream_process

HERE = Path(__file__).parent
CASES = json.loads((HERE / "fixtures/pi_records/cases.json").read_bytes())
# Complete pinned Pi Usage shape; cost components sum exactly to the total.
USAGE = {"input": 3, "cacheRead": 1, "cacheWrite": 2, "output": 4, "totalTokens": 10,
         "cost": {"input": 0.03125, "output": 0.0625, "cacheRead": 0.0078125,
                  "cacheWrite": 0.0234375, "total": 0.125}}


def record(event):
    return json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"


def final(text="final", usage=USAGE):
    return record({"type": "message_end", "message": {
        "role": "assistant", "stopReason": "stop", "content": [{"type": "text", "text": text}],
        "usage": usage,
    }})


def record_case(name):
    cap = 1024
    settled = record({"type": "agent_settled"})
    terminal = final() + settled
    padding = lambda n: b'{"padding":"' + b"x" * (n - 14) + b'"}\n'
    data = {
        "below": lambda: padding(cap - 1) + terminal,
        "at": lambda: padding(cap) + terminal,
        "above": lambda: padding(cap + 1) + terminal,
        "multibyte": lambda: padding(cap) + final("€🍏") + settled,
        "eof-settled": lambda: terminal.rstrip(b"\n"),
        "malformed": lambda: b"not JSON\n" + terminal,
        "bom": lambda: b'\xef\xbb\xbf{"padding":true}\n' + terminal,
        "nonfinite-json": lambda: b'{"padding":NaN}\n' + terminal,
        "invalid-utf8": lambda: b'"\xff"\n' + terminal,
        "partial-eof": lambda: terminal + b'{"type":',
        "oversized-retry": lambda: final() + record({"type": "auto_retry_end", "success": False,
                                                     "finalError": "x" * cap}) + terminal,
        "oversized-aggregate-retry": lambda: final() + record({"type": "agent_end",
                        "messages": ["x" * cap], "willRetry": True}) + terminal,
        "late-message-missing-markers": lambda: terminal + final("x" * cap) + settled,
        "late-message-with-markers": lambda: terminal + record({"type": "turn_start"})
                                                + final("x" * cap) + settled,
        "oversized-eof": lambda: terminal + padding(cap + 1).rstrip(b"\n"),
        "failed-retry": lambda: final() + record({"type": "auto_retry_end", "success": False,
                                                  "finalError": "transport"}) + settled,
        "recovered-retry": lambda: record({"type": "auto_retry_end", "success": False,
                                             "finalError": "transport"}) + terminal,
        "recovered-helper": lambda: record({"type": "message_end", "message": {
            "role": "toolResult", "toolName": "subagent", "isError": True, "usage": USAGE,
        }}) + terminal,
        "agent-end-only": lambda: final() + record({"type": "agent_end", "willRetry": False}),
        "later-start": lambda: terminal + record({"type": "agent_start"}),
    }
    if name.startswith("aggregate-"):
        cap = (2 if name == "aggregate-default" else 4) * 1024 * 1024
        data = final() + record({"type": "turn_end", "toolResults": ["x" * (2 * 1024 * 1024)]}) + settled
        assert len(data) > 2 * 1024 * 1024
    else:
        data = data[name]()
    return data, cap


@pytest.mark.parametrize(("name", "failure"), CASES)
@pytest.mark.parametrize("chunk", [1, 7, 65536])
def test_shared_root_records(name, failure, chunk, tmp_path):
    data, cap = record_case(name)
    stream = pi._Stream(cap)
    for start in range(0, len(data), chunk):
        stream.feed(data[start:start + chunk])
    stream.feed(b"")
    observed, diagnostic = stream.outcome(_process(tmp_path / "out", tmp_path / "err"))
    assert (observed.value if observed else None) == failure
    assert stream.totals[0] == (12 if name == "recovered-helper" else 12 if name == "oversized-retry"
                               or name == "oversized-aggregate-retry" else 6)
    assert stream.totals[1] == (8 if name in ("recovered-helper", "oversized-retry", "oversized-aggregate-retry") else 4)
    assert stream.totals[2] == (0.25 if name in ("recovered-helper", "oversized-retry", "oversized-aggregate-retry") else 0.125)
    if name in ("above", "oversized-retry", "oversized-aggregate-retry", "late-message-missing-markers",
                "late-message-with-markers", "oversized-eof", "aggregate-default"):
        assert "limits.pi_record_mib" in diagnostic
        assert f"{cap} bytes" in diagnostic
    assert stream.completeness(observed) == ("partial" if failure or name == "recovered-helper" else "complete")
    assert len(stream.pending) <= cap


@pytest.mark.parametrize("mib", [2, 4, 32])
def test_project_limit_reaches_frozen_real_seat_and_file_reader(tmp_path, monkeypatch, mib):
    _write_config(tmp_path, EXAMPLE.read_text(encoding="utf-8") + f"\n[limits]\npi_record_mib = {mib}\n")
    cfg = Config.load(tmp_path)
    obj = object.__new__(loop.Loop)
    obj.root, obj.cfg, obj.roadmap = tmp_path, cfg, type("Model", (), {"memory": None})()
    (tmp_path / "prompt.md").write_bytes(b"Scoped task")
    current = obj._job("coder", Role.coder, "prompt.md")
    assert current.pi_record_bytes == mib * 1024 * 1024
    data, _ = record_case("aggregate-custom")
    capture = {}
    process = _stream_process(data)
    def invoke(argv, cwd, env, timeout, stdout, stderr):
        capture.update(env=env)
        return process(argv, cwd, env, timeout, stdout, stderr)
    monkeypatch.setenv(pi.RECORD_ENV, "1")
    cfg = replace(cfg, env_passthrough=(*cfg.env_passthrough, pi.RECORD_ENV))
    monkeypatch.setattr(pi, "run_process", invoke)
    result = pi.PiSeat().run(current, cfg)
    assert capture["env"][pi.RECORD_ENV] == str(mib * 1024 * 1024)
    assert result.failure_class is (FailureClass.output if mib == 2 else None)
    assert result.status == ("failed" if mib == 2 else "completed")
    parsed = pi._parse(result.stdout_path, _process(result.stdout_path, result.stderr_path), cfg.pi_record_bytes)
    assert parsed[-1] is result.failure_class


@pytest.mark.parametrize("milliseconds", [1000, 1800000, 7200001])
def test_claude_reserved_wait_limits_override_passthrough(tmp_path, monkeypatch, milliseconds):
    names = ("CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS", "BASH_DEFAULT_TIMEOUT_MS",
             "BASH_MAX_TIMEOUT_MS", "CLAUDE_ASYNC_AGENT_STALL_TIMEOUT_MS")
    for name in names:
        monkeypatch.setenv(name, "1")
    cfg = _config(tmp_path, names)
    def process(argv, cwd, env, timeout, stdout, stderr, stdin):
        assert env[names[0]] == "0"
        for name in names[1:]:
            assert env[name] == str(milliseconds)
        assert "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS" not in env
        assert stdin == b"Execute this prompt." + render.dispatch_framing("claude").encode()
        stdout.write_bytes(b'{"is_error":false,"result":"ok"}')
        return _process(stdout, stderr)
    monkeypatch.setattr(claude, "run_process", process)
    assert claude.ClaudeSeat().run(replace(_job(tmp_path, "claude"), timeout=milliseconds / 1000), cfg).status == "completed"


@pytest.mark.parametrize(("models", "failure", "tokens_in", "tokens_out", "completeness"), [
    ({"a": {"inputTokens": 1, "cacheReadInputTokens": 2, "cacheCreationInputTokens": 3, "outputTokens": 4},
      "b": {"inputTokens": 10, "cacheReadInputTokens": 20, "cacheCreationInputTokens": 30, "outputTokens": 40}},
     None, 66, 44, "complete"),
    (None, None, 9, 8, "partial"), ({}, None, 9, 8, "partial"),
    ({"a": {"inputTokens": 1}}, None, 9, 8, "partial"),
    ({"a": {"inputTokens": True, "cacheReadInputTokens": 2, "cacheCreationInputTokens": 3, "outputTokens": 4}},
     None, 9, 8, "partial"),
    ({"a": {"inputTokens": 1, "cacheReadInputTokens": 2, "cacheCreationInputTokens": 3, "outputTokens": 4}},
     FailureClass.transport, 6, 4, "partial"),
])
def test_claude_aggregate_usage_and_honest_fallback(models, failure, tokens_in, tokens_out, completeness):
    data = {"modelUsage": models, "total_cost_usd": 0.5,
            "usage": {"input_tokens": 9, "output_tokens": 8}}
    metrics, coverage = claude._metrics(data, failure)
    assert metrics[0] == tokens_in
    assert metrics[1] == tokens_out
    assert metrics[2] == 0.5
    assert coverage == completeness


def test_packaged_extension_resource_and_metadata():
    resource = importlib.resources.files("frutlups.seats").joinpath("pi_delegate/index.ts")
    assert resource.is_file()
    package = tomllib.loads((HERE.parent / "pyproject.toml").read_text(encoding="utf-8"))
    assert package["tool"]["setuptools"]["package-data"]["frutlups.seats"] == ["pi_delegate/index.ts"]


@pytest.mark.parametrize("adapter", ["pi", "claude"])
@pytest.mark.parametrize("role", [Role.coder, Role.reviewer])
@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_loop_job_counts_exact_sent_framing(tmp_path, adapter, role, delta):
    from conftest import offline_config
    from types import SimpleNamespace
    cfg = offline_config()
    cfg = replace(cfg, seats={**cfg.seats, "selected": replace(cfg.seats["coder"], adapter=adapter)})
    obj = object.__new__(loop.Loop)
    obj.root, obj.cfg, obj.roadmap = tmp_path, cfg, SimpleNamespace(memory=None)
    limit = render.CODING_LIMIT if role == Role.coder else render.REVIEW_LIMIT
    length = limit - len(render.dispatch_framing(adapter).encode()) + delta
    raw = ("界" * (length // 3) + "x" * (length % 3)).encode()
    (tmp_path / "prompt.md").write_bytes(raw)
    if delta > 0:
        with pytest.raises(ValueError, match="^prompt exceeds bounded job input$"):
            obj._job("selected", role, "prompt.md")
    else:
        current = obj._job("selected", role, "prompt.md")
        assert current.tools == (pi if adapter == "pi" else claude).tools_for(role)
        assert render.prompt_size(raw.decode(), adapter) == limit + delta


@pytest.mark.parametrize("adapter", ["pi", "claude"])
def test_short_owned_main_waits_for_exit_and_deadline_cleans_child(tmp_path, monkeypatch, adapter):
    module = pi if adapter == "pi" else claude
    seat = pi.PiSeat() if adapter == "pi" else claude.ClaudeSeat()
    payload = (final() + record({"type": "agent_settled"})) if adapter == "pi" else b'{"is_error":false,"result":"ok"}'
    code = "import sys,time; sys.stdout.buffer.write(" + repr(payload) + ");sys.stdout.flush();time.sleep(1)"
    monkeypatch.setattr(module, "_argv", lambda job, cfg: (sys.executable, "-B", "-c", code))
    current = _job(tmp_path, adapter)
    start = time.monotonic()
    result = seat.run(current, _config(tmp_path, ("SYSTEMROOT",)))
    assert result.status == "completed"
    assert result.secs >= 1
    assert time.monotonic() - start >= 1
    marker = tmp_path / "owned-child-pid"
    child_code = "import os,pathlib,time;pathlib.Path(" + repr(str(marker)) + ").write_text(str(os.getpid()));time.sleep(20)"
    parent_code = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-B','-c'," + repr(child_code) + "]);time.sleep(20)"
    monkeypatch.setattr(module, "_argv", lambda job, cfg: (sys.executable, "-B", "-c", parent_code))
    result = seat.run(replace(current, timeout=2), _config(tmp_path, ("SYSTEMROOT",)))
    assert result.status == "timed_out"
    assert result.failure_class is FailureClass.timeout
    assert marker.is_file()
    pid = int(marker.read_text())
    if os.name == "nt":
        import ctypes
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = api.OpenProcess(0x1000, False, pid)
        if handle:
            code = ctypes.c_ulong()
            assert api.GetExitCodeProcess(handle, ctypes.byref(code))
            api.CloseHandle(handle)
            assert code.value != 259
    else:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def test_actual_typescript_handler_with_owned_children(tmp_path):
    node = shutil.which("node")
    assert node, "The admitted Node runtime must be available"
    cases = []
    for name, failure in CASES:
        data, cap = record_case(name)
        source = tmp_path / f"{name}.jsonl"
        source.write_bytes(data)
        cases.append(dict(name=name, path=str(source), cap=cap, failure=failure))
    spec = tmp_path / "cases.json"
    spec.write_text(json.dumps(cases), encoding="utf-8")
    env = dict(os.environ)
    env[pi.RECORD_ENV] = str(4 * 1024 * 1024)
    observed = run_process((node, str(HERE / "pi_delegate_harness.mjs"), str(spec)),
                           tmp_path, env, 45, tmp_path / "harness.out", tmp_path / "harness.err")
    assert observed.exception is None
    assert not observed.timed_out
    assert observed.exit == 0, observed.stderr_path.read_text(encoding="utf-8")
    evidence = json.loads(observed.stdout_path.read_text(encoding="utf-8"))
    assert evidence["shared_cases"] == len(CASES)
    assert evidence["chunks"] == [1, 7, 65536]
    assert evidence["return_bytes"] <= 512 * 1024
    assert evidence["usage_input"] == 60
    assert evidence["usage_output"] == 240
    assert evidence["wait_ms"] >= 1900
    assert evidence["nested_limit"] == 4 * 1024 * 1024
    assert evidence["abort_disposition"] == "closed"
    # The F reader consumes partial handler results from details.usage exactly once,
    # then adds the recovering parent's own final usage (6 in, 4 out, 0.125).
    expected = {"recovered": (18, 12, 0.375), "missing": (12, 8, 0.25), "mixed": (18, 12, 0.375),
                "nested": (18, 12, 0.375), "rejection": (12, 8, 0.25), "all_failed": (6, 4, 0.125),
                "nested_rejection": (18, 12, 0.375)}
    assert set(evidence["partial"]) == set(expected)
    for name, totals in expected.items():
        output = evidence["partial"][name]
        assert "usage" not in output
        stream = pi._Stream()
        stream.feed(record({"type": "message_end", "message": {
            "role": "toolResult", "toolName": "subagent", **output}}) + final() + record({"type": "agent_settled"}))
        stream.feed(b"")
        assert stream.outcome(_process(tmp_path / "out", tmp_path / "err")) == (None, None), name
        assert tuple(stream.totals) == totals, name
        assert stream.completeness(None) == "partial", name
    print(json.dumps({key: value for key, value in evidence.items() if key != "partial"}, sort_keys=True))
