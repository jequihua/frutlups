import io
import json
import os
import subprocess
import sys
from dataclasses import replace

import pytest
import test_loop
from test_loop import events_of, plan, state

from frutlups import _preflight, cli, ledger, loop
from frutlups.config import Config, ConfigError
from frutlups.procs import ProcessResult

project = test_loop.project


def invoke(capsys, *args):
    with pytest.raises(SystemExit) as caught:
        cli.main(list(map(str, args)))
    output = capsys.readouterr()
    return caught.value.code, output.out


@pytest.fixture
def configured(project, monkeypatch):
    root, cfg, model = project
    monkeypatch.setattr(Config, "load", lambda root: cfg)
    return root, cfg, model


@pytest.mark.parametrize("json_mode", [False, True])
def test_once_outputs_one_transition_and_resumes(configured, capsys, json_mode):
    root, cfg, model = configured
    options = ["--json"] if json_mode else []
    code, output = invoke(capsys, "run", root, "--once", *options)
    assert code == 0
    assert state(root, cfg, model).slices["M001-S01"].step == "coding"
    assert len(output.splitlines()) == 1
    if json_mode:
        row = json.loads(output)
        assert row["action"] == "prompt"
        assert row["slice"] == "M001-S01"
        assert row["round"] == 1
        assert row["path"] == events_of(root, cfg, "prompt")[0].data["path"]
    else:
        assert output.startswith("M001-S01 r1 prompt -> prompts/for_coding_agent/")
    code, output = invoke(capsys, "run", root, *options)
    assert code == 0
    assert state(root, cfg, model).slices["M001-S01"].step == "accepted"
    if json_mode:
        rows = [json.loads(line) for line in output.splitlines()]
        assert [r["action"] for r in rows] == [
            "coded",
            "verified",
            "artifact",
            "reviewed",
            "accepted",
            "stop",
        ]
        assert rows[0]["tokens_in"] == 17
        assert rows[0]["tokens_out"] == 3
        assert rows[0]["seats"] == [
            {"name": "coder", "adapter": "pi", "model": "fake", "effort": "medium"}
        ]
        assert rows[1]["ok"] is True
        assert rows[-1]["reason"] == "done"
    else:
        assert "M001-S01 r1 coder pi/fake/medium" in output
        assert "M001-S01 r1 verify ok" in output
        assert "M001-S01 r1 review claude/fake/high pass open=0" in output
        assert "M001-S01 accepted" in output
        assert "done:" in output


@pytest.mark.parametrize("until", ["slice", "milestone", "roadmap"])
def test_until_overrides_configuration(configured, capsys, until):
    root, cfg, _ = configured
    code, output = invoke(capsys, "run", root, "--until", until, "--json")
    assert code == 0
    rows = [json.loads(line) for line in output.splitlines()]
    assert rows[-1]["reason"] == ("done" if until == "roadmap" else "boundary")
    assert len(events_of(root, cfg, "accepted")) == 1


@pytest.mark.parametrize("json_mode", [False, True])
def test_status_and_usage_conform_to_template(configured, capsys, json_mode):
    root, cfg, model = configured
    assert loop.run(root, cfg, model) == loop.StopReason.done
    capsys.readouterr()
    expected = subprocess.run(
        [sys.executable, str(root / "scripts/ledger.py"), "--root", str(root), "status"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    code, output = invoke(capsys, "status", root)
    assert code == 0
    assert output == expected
    options = ["--json"] if json_mode else []
    code, output = invoke(capsys, "status", root, "--usage", *options)
    assert code == 0
    if json_mode:
        rows = [json.loads(line) for line in output.splitlines()]
        assert rows[0] == {"action": "status", "slice": "M001-S01", "round": 1, "step": "accepted"}
        assert rows[1] == {"action": "next", "slice": None}
        for row in rows[2:]:
            assert row["tokens_in"] == 30
            assert row["tokens_out"] == 7
            assert row["cost_usd"] == pytest.approx(0.03)
            assert row["secs"] == sum(e.data.get("secs", 0) for e in ledger.read(root / cfg.ledger))
    else:
        assert output.startswith(expected)
        assert "tokens_in=30 tokens_out=7 cost_usd=0.03" in output
        assert "M001 usage " in output


@pytest.mark.parametrize("json_mode", [False, True])
def test_stopped_run_names_reason_and_human_step(configured, capsys, json_mode):
    root, _, _ = configured
    plan(root, coder=[{"failure": "auth"}])
    code, output = invoke(capsys, "run", root, *(["--json"] if json_mode else []))
    assert code == 3
    assert "seat_auth" in output
    assert "resolve seat access and resume" in output
    if json_mode:
        assert json.loads(output.splitlines()[-1])["reason"] == "seat_auth"


def test_internal_error_is_exit_one_json(configured, monkeypatch, capsys):
    root, _, _ = configured

    def broken(self):
        raise RuntimeError("synthetic internal error")

    monkeypatch.setattr(loop.Loop, "iteration", broken)
    code, output = invoke(capsys, "run", root, "--json")
    assert code == 1
    assert json.loads(output)["action"] == "internal"
    assert "owner must inspect" in output


@pytest.mark.parametrize(
    "failure",
    ["STOP", "agents", "dirty", "ledger", "roadmap", "config", "git", "pi", "verification", "seat"],
)
def test_preflight_failure_classes_exit_two(configured, monkeypatch, capsys, failure):
    root, cfg, model = configured
    expected = failure
    if failure == "STOP":
        (root / "STOP").touch()
    elif failure == "agents":
        (root / "AGENTS.md").unlink()
        expected = "AGENTS.md"
    elif failure == "dirty":
        (root / "unrecorded.txt").write_text("change")
        expected = "unrecorded.txt"
    elif failure == "ledger":
        (root / cfg.ledger).write_text("broken\n")
        expected = "line 1"
    elif failure == "roadmap":
        (root / "roadmap.yaml").write_text("schema: wrong\n")
        expected = "schema"
    elif failure == "config":

        def bad_config(root):
            raise ConfigError("config failure")

        monkeypatch.setattr(Config, "load", bad_config)
    elif failure in ("git", "pi"):
        cfg = replace(cfg, **{failure: str(root / "missing")})
        monkeypatch.setattr(Config, "load", lambda root: cfg)
    elif failure == "verification":
        model = replace(model, verification_full=("nonexistent-verifier-for-test",))
        monkeypatch.setattr(_preflight.roadmap_module, "load", lambda path: model)
    elif failure == "seat":
        cfg = replace(cfg, seats={"coder": cfg.seats["coder"]})
        monkeypatch.setattr(Config, "load", lambda root: cfg)
    before = (root / cfg.ledger).read_bytes()
    code, output = invoke(capsys, "preflight", root)
    assert code == 2
    assert expected in output
    assert all(line.startswith("preflight: ") for line in output.splitlines())
    assert (root / cfg.ledger).read_bytes() == before


@pytest.mark.skipif(os.name != "nt", reason="Windows launcher contract")
@pytest.mark.parametrize("name", ["SYSTEMROOT", "COMSPEC", "PATHEXT"])
def test_preflight_names_each_missing_windows_variable(configured, monkeypatch, capsys, name):
    root, cfg, _ = configured
    cfg = replace(cfg, env_passthrough=tuple(k for k in cfg.env_passthrough if k != name))
    monkeypatch.setattr(Config, "load", lambda root: cfg)
    code, output = invoke(capsys, "preflight", root)
    assert code == 2
    assert f"env_passthrough missing required Windows variable: {name}" in output


def test_preflight_only_probes_active_routes(project, monkeypatch):
    root, cfg, model = project
    first = model.milestones[0]
    planned = replace(
        first,
        id="M002",
        status="planned",
        slices=(replace(first.slices[0], id="M002-S01", milestone_id="M002", risk="release"),),
    )
    model = replace(
        model,
        milestones=(first, planned),
        review_routing={
            **model.review_routing,
            "release": ("planned_only",),
        },
    )
    cfg = replace(
        cfg,
        seats={**cfg.seats, "planned_only": replace(cfg.seats["coder"], provider="planned-only")},
    )
    calls = []
    monkeypatch.setattr(_preflight, "_probe", lambda root, cfg, name, env: calls.append(name))
    assert loop.preflight(root, cfg, model) == ()
    assert calls == ["coder", "holistic"]  # reviewer shares the holistic auth command


@pytest.mark.parametrize("timed_out", [False, True])
def test_auth_refusal_retains_streams_and_scrubs_tail(configured, monkeypatch, capsys, timed_out):
    root, cfg, _ = configured
    monkeypatch.setenv("FIXTURE_SECRET", "synthetic-auth-secret")
    captured = []

    def probe(argv, cwd, env, timeout, stdout, stderr):
        stdout.write_text("raw output")
        stderr.write_text("x" * 5000 + f"\n{root}/private synthetic-auth-secret\nauth denied")
        captured.append((stdout, stderr))
        return ProcessResult(1, timed_out, 30, stdout, stderr, None)

    monkeypatch.setattr(_preflight.procs, "run_process", probe)
    before = (root / cfg.ledger).read_bytes()
    code, output = invoke(capsys, "run", root, "--json")
    assert code == 2
    assert "synthetic-auth-secret" not in output
    assert str(root) not in output
    assert "<redacted>" in output
    assert "auth denied" in output
    assert len(output) < 10000
    if timed_out:
        assert "timed out" in output
    rows = [json.loads(line) for line in output.splitlines()]
    assert len(rows) == 2
    for stdout, stderr in captured:
        assert stdout.is_relative_to(root / "local_state/frutlups/jobs")
        assert stdout.read_text() == "raw output"
        assert "synthetic-auth-secret" in stderr.read_text()
    assert (root / cfg.ledger).read_bytes() == before


@pytest.mark.parametrize("verb", ["preflight", "status"])
def test_unsupported_options_rejected(capsys, verb):
    code, _ = invoke(capsys, verb, "--once")
    assert code == 2


def test_preflight_success_and_configuration_loaded_once(configured, monkeypatch, capsys):
    root, cfg, _ = configured
    loads = []

    def load(root):
        loads.append(root)
        return cfg

    monkeypatch.setattr(Config, "load", load)
    code, output = invoke(capsys, "preflight", root)
    assert code == 0
    assert output == "preflight: ok\n"
    assert loads == [root]
    loads.clear()
    code, _ = invoke(capsys, "run", root, "--once")
    assert code == 0
    assert loads == [root]


def test_preflight_collects_independent_refusals(configured, monkeypatch, capsys):
    root, _, _ = configured
    (root / "STOP").touch()
    (root / "AGENTS.md").write_bytes(b"x" * 8193)
    (root / "roadmap.yaml").write_text("schema: wrong\n")

    def invalid(root):
        raise ConfigError("config: synthetic refusal")

    monkeypatch.setattr(Config, "load", invalid)
    code, output = invoke(capsys, "preflight", root)
    assert code == 2
    assert "config: synthetic refusal" in output
    assert "STOP exists" in output
    assert "AGENTS.md must exist" in output
    assert "schema must be" in output
    assert len(output.splitlines()) == len(set(output.splitlines()))


@pytest.mark.parametrize("outcome", ["ok", "auth_error", "timeout", "malformed"])
def test_claude_missing_auth_verb_uses_read_only_probe(project, monkeypatch, outcome):
    root, cfg, _ = project
    calls = []

    def probe(argv, cwd, env, timeout, stdout, stderr, stdin_bytes=None):
        calls.append((argv, stdout, stderr))
        if len(calls) == 1:
            stderr.write_text("error: unknown command 'auth'")
            return ProcessResult(1, False, 0, stdout, stderr, None)
        assert argv[argv.index("--tools") + 1] == ""
        assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
        assert argv[argv.index("--mcp-config") + 1] == '{"mcpServers":{}}'
        assert stdin_bytes == b"Reply with OK only."
        body = {"is_error": outcome == "auth_error", "result": "OK"}
        stdout.write_text("invalid json" if outcome == "malformed" else json.dumps(body))
        return ProcessResult(0, outcome == "timeout", 0, stdout, stderr, None)

    monkeypatch.setattr(_preflight.procs, "run_process", probe)
    error = _preflight._probe(root, cfg, "holistic", {})
    assert (error is None) == (outcome == "ok")
    assert len(calls) == 2
    assert calls[0][2].read_text() == "error: unknown command 'auth'"
    assert all(stdout.exists() and stderr.exists() for _, stdout, stderr in calls)


def test_commit_failure_reports_slice_and_internal_exit(configured, monkeypatch, capsys):
    root, cfg, _ = configured
    cfg = replace(cfg, commit_on_accept=True)
    monkeypatch.setattr(Config, "load", lambda root: cfg)

    def fail(*args, **kwargs):
        raise OSError("synthetic commit failure")

    monkeypatch.setattr(loop.gitws, "commit", fail)
    code, output = invoke(capsys, "run", root, "--json")
    assert code == 1
    rows = [json.loads(line) for line in output.splitlines()]
    assert rows[-1]["reason"] == "internal"
    assert "M001-S01" in rows[-1]["detail"]
    assert "synthetic commit failure" in rows[-1]["detail"]
    assert "owner must inspect" in rows[-1]["detail"]
    assert len(events_of(root, cfg, "accepted")) == 1
    assert not any(row["action"] == "accepted" for row in rows)


def test_default_root_and_unknown_usage(configured, monkeypatch, capsys):
    root, _, _ = configured
    monkeypatch.chdir(root)
    code, output = invoke(capsys, "status", "--usage", "--json")
    assert code == 0
    rows = [json.loads(line) for line in output.splitlines()]
    assert rows[0]["step"] == "unstarted"
    assert rows[1]["slice"] == "M001-S01"
    for row in rows[2:]:
        assert row["secs"] is None
        assert row["tokens_in"] is None
        assert row["tokens_out"] is None
        assert row["cost_usd"] is None


def test_prompt_and_stop_rows_survive_cp1252_stdout(configured, monkeypatch):
    root, cfg, _ = configured
    original_guard = loop.Loop._guard

    def stop_after_prompt(self, *, job=False):
        original_guard(self, job=job)
        if not job and events_of(root, cfg, "prompt"):
            raise loop._Stop(loop.StopReason.seat_output, "inspect \u2603 and resume")

    monkeypatch.setattr(loop.Loop, "_guard", stop_after_prompt)
    buffer = io.BytesIO()
    with io.TextIOWrapper(buffer, encoding="cp1252", newline="\n") as stdout:
        with monkeypatch.context() as output_patch:
            output_patch.setattr(sys, "stdout", stdout)
            with pytest.raises(SystemExit) as caught:
                cli.main(["run", str(root)])
        stdout.flush()
        assert stdout.encoding == "cp1252"
        assert stdout.errors == "backslashreplace"
        assert buffer.getvalue() == (
            b"M001-S01 r1 prompt -> prompts/for_coding_agent/001_M001-S01_r1.md\n"
            b"seat_output: inspect \\u2603 and resume\n"
        )
    assert caught.value.code == 3
    stop = events_of(root, cfg, "stop")[-1]
    assert stop.data["reason"] == "seat_output"
    assert stop.data["detail"] == "inspect \u2603 and resume"


@pytest.mark.parametrize("role", ["holistic_prompt", "holistic_report"])
def test_holistic_artifact_rows_label_the_round(tmp_path, capsys, role):
    cli._emit(
        {
            "action": "artifact",
            "scope": "M001",
            "round": "holistic",
            "role": role,
            "path": "prompts/001_holistic.md",
        },
        tmp_path,
        False,
    )
    assert capsys.readouterr().out == f"M001 holistic {role} -> prompts/001_holistic.md\n"
