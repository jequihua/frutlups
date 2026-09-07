import hashlib
import io
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_loop
from test_loop import events_of, plan, state

from frutlups import _preflight, cli, ledger, loop
from frutlups.config import Config, ConfigError
from frutlups.procs import ProcessResult, run_process
from frutlups.seats import Role

project = test_loop.project
holistic_project = test_loop.holistic_project
CLAUDE_ARGV = _preflight.claude._argv


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
def test_status_usage_includes_only_matching_holistic_jobs(
    holistic_project, monkeypatch, capsys, json_mode,
):
    root, cfg, model = holistic_project
    monkeypatch.setattr(Config, "load", lambda root: cfg)
    assert loop.run(root, cfg, model) == loop.StopReason.done
    capsys.readouterr()
    events = ledger.read(root / cfg.ledger)
    sha = next(e.data["sha"] for e in events if e.data.get("role") == "holistic_prompt")
    report_sha = next(e.data["sha"] for e in events if e.data.get("role") == "holistic_report")
    jobs = root / "local_state/frutlups/jobs"
    results = [json.loads((d / "result.json").read_text()) for d in jobs.iterdir()]
    holistic = next(result for result in results if result["role"] == "holistic")
    extra = {
        "role": "holistic", "prompt_sha": sha,
        "secs": 5, "tokens_in": 11, "tokens_out": 13, "cost_usd": 0.5,
    }
    for name, result in {
        "retry": extra,
        "unknown": {**extra, "secs": None, "tokens_in": None, "tokens_out": None, "cost_usd": None},
        "wrong-hash": {**extra, "prompt_sha": "unrelated"},
        "report-hash": {**extra, "prompt_sha": report_sha},
        "wrong-role": {**extra, "role": "reviewer"},
        "coder-role": {**extra, "role": "coder"},
        "nested/child": extra,
    }.items():
        directory = jobs / name
        directory.mkdir(parents=True)
        (directory / "result.json").write_text(json.dumps(result), encoding="utf-8")

    code, output = invoke(capsys, "status", root, "--usage", *(["--json"] if json_mode else []))

    assert code == 0, output
    slice_secs = sum(e.data["secs"] for e in events if e.ev in ("coded", "reviewed"))
    milestone_secs = slice_secs + holistic["secs"] + 5
    if json_mode:
        rows = [json.loads(line) for line in output.splitlines()]
        assert [row["action"] for row in rows] == ["status", "next", "usage", "usage"]
        assert rows[2]["slice"] == "M001-S01"
        assert rows[2]["secs"] == slice_secs
        assert rows[2]["tokens_in"] == 30
        assert rows[2]["tokens_out"] == 7
        assert rows[2]["cost_usd"] == pytest.approx(0.03)
        assert rows[3]["milestone"] == "M001"
        assert rows[3]["secs"] == pytest.approx(milestone_secs)
        assert rows[3]["tokens_in"] == 54
        assert rows[3]["tokens_out"] == 24
        assert rows[3]["cost_usd"] == pytest.approx(0.55)
    else:
        line = next(line for line in output.splitlines() if line.startswith("M001 usage "))
        fields = dict(field.split("=") for field in line.split()[2:])
        assert float(fields["secs"]) == pytest.approx(milestone_secs)
        assert fields["tokens_in"] == "54"
        assert fields["tokens_out"] == "24"
        assert float(fields["cost_usd"]) == pytest.approx(0.55)
        assert "usage note" not in output


@pytest.mark.parametrize("json_mode", [False, True])
def test_status_usage_counts_unreadable_results_after_milestones(
    configured, monkeypatch, capsys, json_mode,
):
    root, _, _ = configured
    jobs = root / "local_state/frutlups/jobs"
    for name, data in {
        "malformed": b"{", "empty": b"", "list": b"[]", "scalar": b"7",
        "null": b"null", "encoding": b"\xff", "denied": b"{}",
        "object": b"{}", "nested/child": b"{",
    }.items():
        directory = jobs / name
        directory.mkdir(parents=True)
        (directory / "result.json").write_bytes(data)
    read_text = Path.read_text

    def read(path, *args, **kwargs):
        if path == jobs / "denied/result.json":
            raise PermissionError("synthetic unreadable result")
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    code, output = invoke(capsys, "status", root, "--usage", *(["--json"] if json_mode else []))

    assert code == 0
    if json_mode:
        rows = [json.loads(line) for line in output.splitlines()]
        assert rows[-2]["milestone"] == "M001"
        assert rows[-1] == {"action": "usage", "note": "unreadable", "count": 7}
        assert sum("note" in row for row in rows) == 1
    else:
        lines = output.splitlines()
        assert lines[-2].startswith("M001 usage ")
        assert lines[-1] == "usage note unreadable=7"
        assert output.count("usage note") == 1


def test_usage_matches_holistic_artifacts_to_each_milestone(project):
    root, _, model = project
    first = model.milestones[0]
    model = replace(model, milestones=(
        replace(first, slices=()), replace(first, id="M002", slices=()),
        replace(first, id="M003", slices=()),
    ))
    events = tuple(
        SimpleNamespace(ev=ledger.Ev.artifact, data={
            "role": "holistic_prompt", "scope": mid, "sha": sha,
        })
        for mid, sha in (
            ("M001", "shared"), ("M001", "shared"), ("M002", "shared"), ("M002", "extra"),
        )
    )
    jobs = root / "local_state/frutlups/jobs"
    for sha, amount in (("shared", 2), ("extra", 3)):
        directory = jobs / sha
        directory.mkdir(parents=True)
        (directory / "result.json").write_text(json.dumps({
            "role": "holistic", "prompt_sha": sha, "secs": amount,
            "tokens_in": amount * 10, "tokens_out": amount * 100, "cost_usd": amount / 10,
        }), encoding="utf-8")
    unreadable = jobs / "unreadable"
    unreadable.mkdir()
    (unreadable / "result.json").write_text("invalid json", encoding="utf-8")

    rows = list(cli._usage(root, model, events))

    assert [row["milestone"] for row in rows[:-1]] == ["M001", "M002", "M003"]
    for row, amount in zip(rows[:2], (2, 5)):
        assert row["secs"] == amount
        assert row["tokens_in"] == amount * 10
        assert row["tokens_out"] == amount * 100
        assert row["cost_usd"] == amount / 10
    assert rows[2]["secs"] is None
    assert rows[2]["tokens_in"] is None
    assert rows[2]["tokens_out"] is None
    assert rows[2]["cost_usd"] is None
    assert rows[3] == {"action": "usage", "note": "unreadable", "count": 1}


@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize(
    ("verb", "expected"), [("preflight", 2), ("status", 0), ("run", 0), ("internal", 1)],
)
def test_main_drops_rows_on_broken_stdout(
    configured, monkeypatch, capsys, verb, expected, json_mode,
):
    root, cfg, model = configured
    attempts = []

    class ClosedOutput:
        def write(self, text):
            attempts.append(text)
            raise BrokenPipeError("synthetic closed stdout")

    if verb == "preflight":
        (root / "STOP").touch()
    if verb == "internal":
        def broken(*args, **kwargs):
            raise RuntimeError("synthetic internal failure")

        monkeypatch.setattr(loop, "run", broken)
    args = ["run" if verb == "internal" else verb, str(root)]
    if verb == "run":
        args.append("--once")
    if verb == "status":
        args.append("--usage")
    if json_mode and verb != "preflight":
        args.append("--json")
    with monkeypatch.context() as output_patch:
        output_patch.setattr(sys, "stdout", ClosedOutput())
        with pytest.raises(SystemExit) as caught:
            cli.main(args)

    assert caught.value.code == expected
    assert attempts
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == ""
    if verb == "run":
        assert state(root, cfg, model).slices["M001-S01"].step == "coding"


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
    assert calls == ["coder", "holistic"]  # reviewer shares the holistic model


@pytest.mark.parametrize("same_model", [False, True])
def test_preflight_probes_each_distinct_claude_model(project, monkeypatch, same_model):
    root, cfg, model = project
    holistic = cfg.seats["holistic"]
    reviewer = replace(
        holistic, model=holistic.model if same_model else "other-model", effort="low",
    )
    cfg = replace(cfg, seats={**cfg.seats, "reviewer": reviewer})
    calls = []
    run = _preflight.claude.ClaudeSeat.run

    def probe(self, job, config):
        calls.append(job)
        seat = config.seats[job.seat_name]
        assert job.role is Role.reviewer
        assert job.adapter == seat.adapter
        assert job.provider == seat.provider
        assert job.model == seat.model
        assert job.effort == seat.effort
        assert job.tools == _preflight.claude.READ_TOOLS
        assert job.timeout == 60
        assert job.expected_notes_path is None
        assert job.cwd == root
        assert job.prompt_path.is_relative_to(root / "local_state/frutlups")
        assert job.prompt_path.read_bytes() == b"Reply with OK only.\n"
        assert job.prompt_sha == hashlib.sha256(job.prompt_path.read_bytes()).hexdigest()
        return run(self, job, config)

    monkeypatch.setattr(_preflight.claude.ClaudeSeat, "run", probe)
    assert loop.preflight(root, cfg, model) == ()
    assert [job.seat_name for job in calls] == (
        ["holistic"] if same_model else ["holistic", "reviewer"]
    )
    assert list((root / "local_state/frutlups/jobs").iterdir()) == []
    assert (root / "local_state/probe-count.txt").read_text() == ("2" if same_model else "3")


@pytest.mark.parametrize("adapter", ["pi", "claude"])
@pytest.mark.parametrize("failure", [None, "auth", "capacity", "transport", "output", "timeout"])
def test_preflight_probe_plan_preserves_roles_and_keeps_only_failed_job(
    project, monkeypatch, adapter, failure,
):
    root, cfg, model = project
    cfg = replace(cfg, seats={
        name: replace(seat, model="probe-model") if seat.adapter == adapter else seat
        for name, seat in cfg.seats.items()
    })
    selected = {"failure": failure} if failure else {}
    plan(
        root,
        probe=[selected, {}] if adapter == "pi" else [{}, selected],
        coder=[{"failure": "auth"}],
        reviewer=[{"failure": "auth"}],
    )
    module = _preflight.pi if adapter == "pi" else _preflight.claude
    seat_class = module.PiSeat if adapter == "pi" else module.ClaudeSeat
    run = seat_class.run
    captured = []

    def probe(self, job, config):
        assert job.role is Role.reviewer
        assert job.tools == module.READ_TOOLS
        assert job.timeout == 60
        result = run(self, job, config)
        captured.append(result)
        return result

    if failure == "timeout":
        def short_timeout(argv, cwd, env, timeout, stdout, stderr, stdin_bytes=None):
            assert timeout == 60
            return run_process(argv, cwd, env, 1, stdout, stderr, stdin_bytes)

        monkeypatch.setattr(module, "run_process", short_timeout)
    monkeypatch.setattr(seat_class, "run", probe)
    before = (root / cfg.ledger).read_bytes()
    plan_before = (root / "local_state/plan.json").read_bytes()

    errors = loop.preflight(root, cfg, model)

    assert len(captured) == 1
    result = captured[0]
    directories = list((root / "local_state/frutlups/jobs").iterdir())
    if failure:
        assert result.status == ("timed_out" if failure == "timeout" else "failed")
        assert result.failure_class == failure
        assert directories == [result.stdout_path.parent]
        assert result.stdout_path.is_file()
        assert result.stderr_path.is_file()
        assert (directories[0] / "prompt.md").read_bytes() == b"Reply with OK only.\n"
        name = "coder" if adapter == "pi" else "holistic"
        seat = cfg.seats[name]
        assert errors == (
            f"model probe failed: {name}: adapter={adapter} provider={seat.provider} "
            f"model=probe-model; failure={failure}; diagnostic: {result.diagnostic}; "
            f"stderr: (empty); streams: {directories[0].relative_to(root).as_posix()}",
        )
    else:
        assert errors == ()
        assert result.status == "completed"
        assert result.final_text == "OK"
        assert directories == []
    assert (root / "local_state/probe-count.txt").read_text() == "2"
    assert not (root / "local_state/coder-count.txt").exists()
    assert not (root / "local_state/reviewer-count.txt").exists()
    assert (root / "local_state/plan.json").read_bytes() == plan_before
    assert (root / cfg.ledger).read_bytes() == before


@pytest.mark.parametrize("timed_out", [False, True])
def test_auth_refusal_retains_streams_and_scrubs_tail(configured, monkeypatch, capsys, timed_out):
    root, cfg, _ = configured
    monkeypatch.setenv("FIXTURE_SECRET", "synthetic-auth-secret")
    captured = []

    def probe(argv, cwd, env, timeout, stdout, stderr, stdin_bytes=None):
        stdout.write_text("raw output")
        stderr.write_text("x" * 5000 + f"\n{root}/private synthetic-auth-secret\nauth denied")
        captured.append((stdout, stderr))
        return ProcessResult(1, timed_out, 30, stdout, stderr, None)

    monkeypatch.setattr(_preflight.pi, "run_process", probe)
    monkeypatch.setattr(_preflight.claude, "run_process", probe)
    before = (root / cfg.ledger).read_bytes()
    code, output = invoke(capsys, "run", root, "--json")
    assert code == 2
    assert "synthetic-auth-secret" not in output
    assert str(root) not in output
    assert "<redacted>" in output
    assert "auth denied" in output
    assert len(output) < 10000
    if timed_out:
        assert "failure=timeout" in output
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
    monkeypatch.setattr(_preflight.claude, "_argv", CLAUDE_ARGV)

    def probe(argv, cwd, env, timeout, stdout, stderr, stdin_bytes=None):
        calls.append((argv, stdout, stderr))
        assert "auth" not in argv
        assert argv[argv.index("--tools") + 1] == "Read,Grep,Glob"
        assert argv[argv.index("--model") + 1] == cfg.seats["holistic"].model
        assert argv[argv.index("--effort") + 1] == cfg.seats["holistic"].effort
        assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
        assert argv[argv.index("--mcp-config") + 1] == '{"mcpServers":{}}'
        assert stdin_bytes == b"Reply with OK only.\n"
        assert timeout == 60
        assert env["FRUTLUPS_SEAT"] == "reviewer"
        stderr.write_text("")
        body = {"is_error": outcome == "auth_error", "result": "OK"}
        stdout.write_text("invalid json" if outcome == "malformed" else json.dumps(body))
        return ProcessResult(0, outcome == "timeout", 0, stdout, stderr, None)

    monkeypatch.setattr(_preflight.claude, "run_process", probe)
    error = _preflight._probe(root, cfg, "holistic")
    assert (error is None) == (outcome == "ok")
    assert len(calls) == 1
    assert all(stdout.exists() == (outcome != "ok") for _, stdout, _ in calls)
    assert all(stderr.exists() == (outcome != "ok") for _, _, stderr in calls)


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
