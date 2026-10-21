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
import test_ledger
import test_loop
from test_loop import events_of, plan, state
from _native_support import grant_project, run_transitions, probe_errors

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
    before = (root / cfg.ledger).read_bytes()
    for once in (["--once"], []):
        # The public runner reaches its owned iteration; without a profile it
        # refuses fresh dispatch there, with one stop row and no effects.
        code, output = invoke(capsys, "run", root, *once, *options)
        assert code == 1
        assert "fresh dispatch requires an autonomy profile" in output
        assert len(output.splitlines()) == 1
        assert (root / cfg.ledger).read_bytes() == before
        if json_mode:
            assert json.loads(output)["action"] == "stop"
    assert not (root / "local_state/frutlups/jobs").exists()
    root, cfg, model = grant_project(configured)
    code, output = invoke(capsys, "run", root, "--once", *options)
    assert code == 0
    assert output == ""
    assert state(root, cfg, model).slices["M001-S01"].step == "coding"
    assert len(events_of(root, cfg, "prompt")) == 1
    code, output = invoke(capsys, "run", root, *options)
    assert code == 0
    assert state(root, cfg, model).slices["M001-S01"].step == "accepted"
    if json_mode:
        rows = [json.loads(line) for line in output.splitlines()]
        assert [row["action"] for row in rows] == ["verified", "accepted", "stop"]
        assert rows[0]["ok"] is True
        assert rows[-1]["reason"] == "done"
    else:
        assert "M001-S01 r1 verify ok" in output
        assert "M001-S01 accepted" in output
        assert "done:" in output
    assert len(events_of(root, cfg, "prompt")) == 1
    coded = events_of(root, cfg, "coded")[0]
    assert coded.data["tokens_in"] == 17
    assert coded.data["tokens_out"] == 3
    assert coded.data["cost_usd"] == 0.01


@pytest.mark.parametrize("until", ["slice", "milestone", "roadmap"])
def test_until_overrides_configuration(configured, capsys, until):
    root, cfg, _ = configured
    code, output = invoke(capsys, "run", root, "--until", until, "--json")
    assert code == 1
    assert json.loads(output)["action"] == "stop"
    assert "fresh dispatch requires an autonomy profile" in json.loads(output)["detail"]
    root, cfg, model = grant_project(configured)
    code, output = invoke(capsys, "run", root, "--until", until, "--json")
    assert code == 0
    assert json.loads(output.splitlines()[-1])["reason"] == ("done" if until == "roadmap" else "boundary")
    assert len(events_of(root, cfg, "accepted")) == 1


@pytest.mark.parametrize("json_mode", [False, True])
def test_status_and_usage_conform_to_template(configured, capsys, json_mode):
    root, cfg, model = grant_project(configured)
    assert run_transitions(root, cfg, model) == loop.StopReason.done
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
        for row in [row for row in rows if row["action"] == "usage"]:
            assert row["tokens_in"] == 30
            assert row["tokens_out"] == 7
            assert row["cost_usd"] == pytest.approx(0.03)
            assert row["secs"] == sum(e.data["usage"].get("secs", 0) for e in ledger.read(root / cfg.ledger) if e.ev == ledger.Ev.attempt_finished)
    else:
        assert output.startswith(expected)
        assert "tokens_in=30 tokens_out=7 cost_usd=0.03" in output
        assert "M001 usage " in output


@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize(("cost", "local_copy"), [(0, True), (0.5, True), (0.5, False)])
def test_usage_combines_coded_event_cost_and_legacy_result_fallback(
    configured, capsys, json_mode, cost, local_copy,
):
    root, cfg, _ = configured
    old_sha, new_sha = "1" * 64, "2" * 64
    test_ledger._write(root / cfg.ledger, [
        {**test_ledger._prompt(), "sha": old_sha},
        test_ledger._coded(),
        test_ledger._verified(False),
        {**test_ledger._prompt(2), "sha": new_sha},
        test_ledger._coded(2, cost_usd=cost),
    ])
    results = {
        "legacy-attempt": {"role": "coder", "prompt_sha": old_sha, "cost_usd": 0.125},
        "legacy-retry": {"role": "coder", "prompt_sha": old_sha, "cost_usd": 0.125},
        "wrong-role": {"role": "reviewer", "prompt_sha": old_sha, "cost_usd": 99},
        "wrong-prompt": {"role": "coder", "prompt_sha": "3" * 64, "cost_usd": 99},
    }
    if local_copy:
        results["already-recorded"] = {"role": "coder", "prompt_sha": new_sha, "cost_usd": 99}
    for name, result in results.items():
        directory = root / "local_state/frutlups/jobs" / name
        directory.mkdir(parents=True)
        (directory / "result.json").write_text(json.dumps(result), encoding="utf-8")

    code, output = invoke(capsys, "status", root, "--usage", *(["--json"] if json_mode else []))

    assert code == 0, output
    if json_mode:
        rows = [json.loads(line) for line in output.splitlines()]
        usage = [row for row in rows if row["action"] == "usage"]
        assert len(usage) == 2
        assert usage[0]["slice"] == "M001-S01"
        assert usage[1]["milestone"] == "M001"
        assert usage[0]["cost_usd"] == cost
        assert usage[1]["cost_usd"] == cost
    else:
        for scope in ("M001-S01", "M001"):
            assert f"{scope} usage secs=? tokens_in=? tokens_out=? cost_usd={cost}" in output


@pytest.mark.parametrize("json_mode", [False, True])
def test_status_usage_includes_only_matching_holistic_jobs(
    holistic_project, monkeypatch, capsys, json_mode,
):
    root, cfg, model = holistic_project
    monkeypatch.setattr(Config, "load", lambda root: cfg)
    root, cfg, model = grant_project((root, cfg, model))
    assert run_transitions(root, cfg, model) == loop.StopReason.done
    capsys.readouterr()
    events = ledger.read(root / cfg.ledger)
    sha = next(e.data["sha"] for e in events if e.data.get("role") == "holistic_prompt")
    report_sha = next(e.data["sha"] for e in events if e.data.get("role") == "holistic_report")
    jobs = root / "local_state/frutlups/jobs"
    attempts = state(root, cfg, model).attempts
    holistic = next(attempt["finish"]["usage"] for attempt in attempts.values()
                    if attempt["start"]["role"] == "holistic")
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
    slice_secs = sum(attempt["finish"]["usage"]["secs"] for attempt in attempts.values()
                     if attempt["start"]["scope"] == "M001-S01")
    milestone_secs = slice_secs + holistic["secs"]
    if json_mode:
        rows = [json.loads(line) for line in output.splitlines()]
        assert [row["action"] for row in rows] == ["status", "next", "run_status", "holistic", "usage", "usage"]
        run = state(root, cfg, model).runs["R1"]
        assert rows[2] == {"action": "run_status", "run": "R1", "revision": 0, "stop": "M001",
                           "state": "boundary", "remaining": dict(run["remaining"])}
        assert rows[3] == {"action": "holistic", "milestone": "M001", "step": "done"}
        rows = rows[4:]
        assert rows[0]["slice"] == "M001-S01"
        assert rows[0]["secs"] == slice_secs
        assert rows[0]["tokens_in"] == 30
        assert rows[0]["tokens_out"] == 7
        assert rows[0]["cost_usd"] == pytest.approx(0.03)
        assert rows[1]["milestone"] == "M001"
        assert rows[1]["secs"] == pytest.approx(milestone_secs)
        assert rows[1]["tokens_in"] == 43
        assert rows[1]["tokens_out"] == 11
        assert rows[1]["cost_usd"] == pytest.approx(0.05)
    else:
        line = next(line for line in output.splitlines() if line.startswith("M001 usage "))
        fields = dict(field.split("=") for field in line.split()[2:])
        assert float(fields["secs"]) == pytest.approx(milestone_secs)
        assert fields["tokens_in"] == "43"
        assert fields["tokens_out"] == "11"
        assert float(fields["cost_usd"]) == pytest.approx(0.05)
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
        assert rows[-1]["milestone"] == "M001"
        assert not any("note" in row for row in rows)
    else:
        assert output.splitlines()[-1].startswith("M001 usage ")
        assert "usage note" not in output


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

    # Neither duplicate artifact hashes nor local job files are usage authority.
    events += tuple(SimpleNamespace(ev=kind, data=data, round=data.get("round")) for kind, data in (
        (ledger.Ev.attempt_started, dict(invocation="a", scope="M001", round="holistic", role="holistic")),
        (ledger.Ev.attempt_finished, dict(invocation="a", usage=dict(secs=2, tokens_in=20, tokens_out=200, cost_usd=.2))),
        (ledger.Ev.attempt_started, dict(invocation="b", scope="M002", round="holistic", role="holistic")),
        (ledger.Ev.attempt_finished, dict(invocation="b", usage=dict(secs=5, tokens_in=50, tokens_out=500, cost_usd=.5))),
    ))
    rows = list(cli._usage(root, model, events))
    assert [row["milestone"] for row in rows] == ["M001", "M002", "M003"]
    for row, amount in zip(rows[:2], (2, 5)):
        assert row["secs"] == amount
        assert row["tokens_in"] == amount * 10
        assert row["tokens_out"] == amount * 100
        assert row["cost_usd"] == amount / 10
    assert rows[2] == dict(action="usage", milestone="M003", secs=None,
                           tokens_in=None, tokens_out=None, cost_usd=None,
                           completeness="unknown", unresolved_attempts=0)


@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize(
    ("verb", "expected"), [("preflight", 2), ("status", 0), pytest.param("run", 1, id="run-0"), ("internal", 1)],
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
        assert state(root, cfg, model).slices["M001-S01"].step == "unstarted"


@pytest.mark.parametrize("json_mode", [False, True])
def test_stopped_run_names_reason_and_human_step(configured, capsys, json_mode):
    root, cfg, model = grant_project(configured)
    plan(root, coder=[{"failure": "auth"}])
    reason = run_transitions(root, cfg, model, emit=lambda row: cli._emit(row, root, json_mode))
    output = capsys.readouterr().out
    assert reason == loop.StopReason.seat_auth
    assert "seat_auth" in output
    assert "resolve seat access and resume" in output
    if json_mode:
        assert json.loads(output.splitlines()[-1])["reason"] == "seat_auth"


def test_internal_error_is_exit_one_json(configured, monkeypatch, capsys):
    root, _, _ = configured

    def broken(*args, **kwargs):
        raise RuntimeError("synthetic internal error")

    monkeypatch.setattr(loop, "run", broken)
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
    if failure in ("config", "roadmap"):
        assert expected in output
    else:
        errors = probe_errors(root, cfg, model)
        if failure == "git":
            assert errors == ("configured Git must name an existing absolute executable file",)
        else:
            assert any(expected in error for error in errors)
        # Public preflight reports exactly the genuine owned refusals.
        assert output == "".join(f"preflight: {error}\n" for error in errors)
    assert all(line.startswith("preflight: ") for line in output.splitlines())
    assert (root / cfg.ledger).read_bytes() == before


@pytest.mark.skipif(os.name != "nt", reason="Windows launcher contract")
@pytest.mark.parametrize("name", ["SYSTEMROOT", "COMSPEC", "PATHEXT"])
def test_preflight_names_each_missing_windows_variable(configured, monkeypatch, capsys, name):
    root, cfg, model = configured
    cfg = replace(cfg, env_passthrough=tuple(k for k in cfg.env_passthrough if k != name))
    monkeypatch.setattr(Config, "load", lambda root: cfg)
    code, output = invoke(capsys, "preflight", root)
    assert code == 2
    assert f"env_passthrough missing required Windows variable: {name}" in probe_errors(root, cfg, model)


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
        seats={**{name: seat for name, seat in cfg.seats.items() if name != "holistic"},
               "planned_only": replace(cfg.seats["coder"], provider="planned-only")},
    )
    calls = []
    monkeypatch.setattr(_preflight, "_probe", lambda root, cfg, name, env: calls.append(name))
    assert probe_errors(root, cfg, model) == ()
    assert calls == ["coder", "reviewer"]  # unused holistic seat is excluded


@pytest.mark.parametrize("same_model", [False, True])
def test_preflight_probes_each_distinct_claude_model(holistic_project, monkeypatch, same_model):
    root, cfg, model = holistic_project
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
        assert job.tools == _preflight.claude.tools_for(Role.reviewer)
        assert job.timeout == 60
        assert job.expected_notes_path is None
        assert job.cwd == root
        assert job.prompt_path.is_relative_to(root / "local_state/frutlups")
        assert job.prompt_path.read_bytes() == b"Reply with OK only.\n"
        assert job.prompt_sha == hashlib.sha256(job.prompt_path.read_bytes()).hexdigest()
        return run(self, job, config)

    monkeypatch.setattr(_preflight.claude.ClaudeSeat, "run", probe)
    assert probe_errors(root, cfg, model) == ()
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
        assert job.tools == module.tools_for(Role.reviewer)
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

    errors = probe_errors(root, cfg, model)

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
        name = "coder" if adapter == "pi" else "reviewer"
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
    root, cfg, model = configured
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
    errors = probe_errors(root, cfg, model)
    for error in errors:
        cli._emit(dict(action="preflight", ok=False, detail=error), root, True)
    output = capsys.readouterr().out
    assert len(errors) == 2
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
    assert code == 1  # Admitted, then the no-profile owned iteration refuses dispatch.
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
    errors = _preflight._static(root, None, None)[2]
    assert any("STOP exists" in error for error in errors)
    assert any("AGENTS.md must exist" in error for error in errors)
    assert "unsupported roadmap schema" in output
    assert len(output.splitlines()) == len(set(output.splitlines()))


@pytest.mark.parametrize("outcome", ["ok", "auth_error", "timeout", "malformed"])
def test_claude_missing_auth_verb_uses_read_only_probe(project, monkeypatch, outcome):
    from frutlups.render import dispatch_framing
    root, cfg, _ = project
    calls = []
    completed = []
    monkeypatch.setattr(_preflight.claude, "_argv", CLAUDE_ARGV)

    def probe(argv, cwd, env, timeout, stdout, stderr, stdin_bytes=None):
        calls.append((argv, stdout, stderr))
        assert "auth" not in argv
        assert argv[argv.index("--tools") + 1] == ",".join(_preflight.claude.tools_for(Role.reviewer))
        assert argv[argv.index("--model") + 1] == cfg.seats["holistic"].model
        assert argv[argv.index("--effort") + 1] == cfg.seats["holistic"].effort
        assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
        assert argv[argv.index("--mcp-config") + 1] == '{"mcpServers":{}}'
        assert stdin_bytes == b"Reply with OK only.\n" + dispatch_framing("claude").encode("utf-8")
        assert timeout == 60
        assert env["FRUTLUPS_SEAT"] == "reviewer"
        stderr.write_text("")
        body = {"is_error": outcome == "auth_error", "result": "OK"}
        stdout.write_text("invalid json" if outcome == "malformed" else json.dumps(body))
        completed.append(outcome)  # Reached only after every stub assertion and write.
        return ProcessResult(0, outcome == "timeout", 0, stdout, stderr, None)

    monkeypatch.setattr(_preflight.claude, "run_process", probe)
    error = _preflight._probe(root, cfg, "holistic")
    assert completed == [outcome]
    assert (error is None) == (outcome == "ok")
    assert len(calls) == 1
    assert all(stdout.exists() == (outcome != "ok") for _, stdout, _ in calls)
    assert all(stderr.exists() == (outcome != "ok") for _, _, stderr in calls)


def test_commit_failure_reports_slice_and_internal_exit(configured, monkeypatch, capsys):
    root, cfg, model = grant_project(configured)
    cfg = replace(cfg, commit_on_accept=True)
    original_git = loop.gitws._git
    def fail(root, *args, **kwargs):
        if args[0] == "commit":
            raise OSError("synthetic commit failure")
        return original_git(root, *args, **kwargs)
    monkeypatch.setattr(loop.gitws, "_git", fail)
    notices = []
    reason = run_transitions(root, cfg, model, emit=notices.append)
    assert reason == loop.StopReason.internal
    # Test CLI serialization/exit mapping with the observed private result.
    def observed(*args, emit, **kwargs):
        for row in notices:
            emit(row)
        return reason
    monkeypatch.setattr(loop, "run", observed)
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
    from _native_support import Controller
    root, cfg, model = grant_project(configured)
    runner = Controller(root, cfg, model)
    assert runner.iteration() is None
    prompt = events_of(root, cfg, "prompt")[0]
    original_guard = loop.Loop._guard
    def stop_after_prompt(self, *, job=False):
        original_guard(self, job=job)
        if not job and events_of(root, cfg, "prompt"):
            raise loop._Stop(loop.StopReason.seat_output, "inspect \u2603 and resume")
    monkeypatch.setattr(loop.Loop, "_guard", stop_after_prompt)
    buffer = io.BytesIO()
    notices = []
    with io.TextIOWrapper(buffer, encoding="cp1252", newline="\n") as stdout:
        with monkeypatch.context() as output_patch:
            output_patch.setattr(sys, "stdout", stdout)
            with pytest.raises(SystemExit) as caught:
                cli.main(["run", str(root)])
            cli._emit(dict(action="prompt", slice=prompt.slice, round=prompt.round,
                           path=prompt.data["path"]), root, False)
            def emit(row):
                notices.append(row)
                cli._emit(row, root, False)
            runner.emit = emit
            assert runner.iteration() == loop.StopReason.seat_output
        stdout.flush()
        assert stdout.encoding == "cp1252"
        assert stdout.errors == "backslashreplace"
        assert buffer.getvalue() == (
            b"seat_output: inspect \\u2603 and resume\n"
            + f"M001-S01 r1 prompt -> {prompt.data['path']}\n".encode()
            + b"seat_output: inspect \\u2603 and resume\n")
    assert caught.value.code == 3
    assert notices == [dict(action="stop", reason="seat_output", detail="inspect \u2603 and resume")]
    assert events_of(root, cfg, "stop") == []


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


@pytest.mark.parametrize("declared", [False, True], ids=["ordinary-v2", "profile-v2"])
def test_v2_public_runner_and_append_refuse_before_effects(
    initialized_project, tmp_path, monkeypatch, capsys, declared,
):
    import shutil
    import yaml
    from conftest import offline_config
    from frutlups import gitws, roadmap

    root = tmp_path / "repo"
    shutil.copytree(initialized_project, root)
    cfg = offline_config()
    path = root / "roadmap.yaml"
    data = yaml.safe_load(path.read_text("utf-8"))
    data["schema"] = "frutlups.roadmap/2"
    if declared:
        data["autonomy"] = {"schema": "frutlups.autonomy/1"}
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    model = roadmap.load(path)
    monkeypatch.setattr(Config, "load", lambda root: cfg)
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("adapter must not run")
    monkeypatch.setattr(_preflight.pi.PiSeat, "run", forbidden)
    monkeypatch.setattr(_preflight.claude.ClaudeSeat, "run", forbidden)
    before = gitws.workspace_witness(root, executable=cfg.git)
    ledger_before = (root / cfg.ledger).read_bytes()
    assert not (root / "local_state/frutlups").exists()
    if declared:
        # A profile's authority edit exists only in the working tree: genuine refusal.
        refusal = "unrecorded change: roadmap.yaml"
        for verb in ("preflight", "run"):
            code, output = invoke(capsys, verb, root)
            assert code == 2
            assert refusal in output
        assert _preflight.prepare(root, cfg, model)[2] == (refusal,)
        assert loop.preflight(root, cfg, model) == (refusal,)
        assert loop.run(root, cfg, model) == loop.StopReason.preflight_failed
    else:
        # Ordinary /2 admits reads, then refuses fresh dispatch in the owned iteration.
        code, output = invoke(capsys, "preflight", root)
        assert (code, output) == (0, "preflight: ok\n")
        code, output = invoke(capsys, "run", root)
        assert code == 1
        assert "fresh dispatch requires an autonomy profile" in output
        assert _preflight.prepare(root, cfg, model)[2] == ()
        assert loop.run(root, cfg, model, emit=lambda _: None) == loop.StopReason.internal
    # Construction alone has no effects; it validates schema and the effective until.
    runner = loop.Loop(root, cfg, model)
    with pytest.raises(ValueError, match="unsupported roadmap schema"):
        loop.Loop(root, cfg, replace(model, schema="frutlups.roadmap/1", autonomy=None))
    with pytest.raises(ValueError, match="^until: must be one of milestone, roadmap, slice$"):
        loop.Loop(root, replace(cfg, until="forever"), model)
    # Direct iteration acquires ownership and reaches the real owned refusal.
    notices = []
    runner.emit = notices.append
    assert runner.iteration() == loop.StopReason.internal
    assert notices[-1]["detail"].endswith("owner must inspect")
    for candidate_schema in ("frutlups.ledger/1", ledger.SCHEMA_V2):
        row = {"schema": candidate_schema, "ev": "note", "by": "human",
               "t": "2026-09-14T00:00:00Z", "text": "must not append"}
        for supplied in (None, model):
            message = "unsupported legacy history" if candidate_schema.endswith("/1") else "/2 ledger append is not enabled"
            with pytest.raises(ledger.LedgerError, match=message):
                ledger.append(root / cfg.ledger, row, supplied)
    capsys.readouterr()
    code, output = invoke(capsys, "status", root)
    assert code == 0
    assert "M001-S01 r1 unstarted" in output
    assert calls == []
    assert (root / cfg.ledger).read_bytes() == ledger_before
    assert gitws.workspace_witness(root, executable=cfg.git) == before
    assert not (root / "local_state/frutlups/jobs").exists()


def test_v2_commit_status_is_read_only_even_with_completed_marker(project, monkeypatch):
    from test_commit_v2 import materialize, approve
    from frutlups import commit, gitws
    root, cfg, model, _, _ = materialize(project)
    result = approve(root, cfg, model)
    events = ledger.read(root / cfg.ledger)
    intent = ledger._thaw(events[-1].data["commit_intent"])
    marker = commit._marker(root)
    marker.write_bytes(json.dumps({"operation": intent["id"], "parent": intent["parent"]}).encode())
    marker_bytes = marker.read_bytes()
    before = gitws.workspace_witness(root)
    monkeypatch.setattr(Config, "load", lambda root: cfg)
    def forbidden(*args, **kwargs): pytest.fail("status attempted a mutation")
    monkeypatch.setattr(commit, "writer_lock", forbidden)
    monkeypatch.setattr(commit, "recover", forbidden)
    output = []
    cli._status(root, False, True, output.append)
    assert json.loads(output[-1]) == {"action": "commit", **result[0]}
    assert marker.read_bytes() == marker_bytes
    assert gitws.workspace_witness(root) == before


def _fake_seat(tmp_path, script, seat, action, stdin=None):
    """Run one shared fake seat script under owned process control with a short deadline."""
    (tmp_path / "local_state").mkdir()
    (tmp_path / "local_state/plan.json").write_text(json.dumps({seat: [action]}), encoding="utf-8")
    (tmp_path / "prompt.md").write_text("Scripted task.\n", encoding="utf-8")
    argv = [sys.executable, "-B", str(test_loop.HERE / script)]
    if script == "fake_pi.py":
        argv.append(str(tmp_path / "prompt.md"))
    return run_process(argv, tmp_path, {**os.environ, "FRUTLUPS_SEAT": seat}, 30,
                       tmp_path / "out", tmp_path / "err", stdin)


@pytest.mark.parametrize(("action", "failure", "settled"), [
    ({}, None, True), ({"failure": "auth"}, "auth", False),
    ({"failure": "capacity"}, "capacity", False), ({"failure": "transport"}, "transport", False),
    ({"failure": "output"}, "output", False),
])
def test_fixture_adapter_contract_fake_pi_settlement(tmp_path, action, failure, settled):
    from frutlups.seats import pi
    process = _fake_seat(tmp_path, "fake_pi.py", "coder", action)
    assert not process.timed_out
    lines = process.stdout_path.read_bytes().splitlines(keepends=True)
    assert any(line.strip() == b'{"type": "agent_settled"}' for line in lines) is settled
    for kept in (lines, [line for line in lines if b"agent_settled" not in line]):
        stream = pi._Stream()
        for line in kept:
            stream.feed(line)
        stream.feed(b"")
        observed, diagnostic = stream.outcome(process)
        if failure is None and kept is not lines:
            # The real reader still refuses completion without settlement.
            assert (observed, diagnostic) == (pi.FailureClass.output, "Pi missing settled final message")
        else:
            assert (observed.value if observed else None) == failure
        if failure in (None, "auth", "capacity"):
            assert stream.totals == [17, 3, 0.01]
    if failure is None:
        assert stream.final_text == "Implemented scripted fixture change."


@pytest.mark.parametrize(("suffix", "probe"), [
    pytest.param("framing", True, id="exact"),
    pytest.param("", False, id="missing"),
    pytest.param("\n\nDifferent instructions.", False, id="wrong"),
    pytest.param("framing+extra", False, id="extra-after"),
    pytest.param("extra+framing", False, id="extra-before"),
])
def test_fixture_adapter_contract_fake_claude_framing(tmp_path, suffix, probe):
    from frutlups.render import dispatch_framing
    task, framing = "Reply with OK only.\n", dispatch_framing("claude")
    observed = {"framing": task + framing, "framing+extra": task + framing + "\nextra",
                "extra+framing": task + "Also do more.\n" + framing}.get(suffix, task + suffix)
    process = _fake_seat(tmp_path, "fake_claude.py", "reviewer", {"text": "not the probe"},
                         observed.encode("utf-8"))
    assert not process.timed_out
    state = tmp_path / "local_state"
    assert (state / "probe-count.txt").exists() is probe
    if probe:
        assert process.exit == 0
        assert json.loads(process.stdout_path.read_text("utf-8"))["result"] == "OK"
        assert (state / "probe-0.md").read_text("utf-8") == task
    elif suffix == "extra+framing":
        # Correctly framed, but an extra instruction makes it an ordinary seat task.
        assert process.exit == 0
        assert json.loads(process.stdout_path.read_text("utf-8"))["result"] == "not the probe"
        assert (state / "reviewer-0.md").read_text("utf-8") == task + "Also do more.\n"
    else:
        assert process.exit == 2
        assert process.stdout_path.read_bytes() == b""
        assert not (state / "reviewer-count.txt").exists()
