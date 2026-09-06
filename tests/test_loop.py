import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from frutlups import _preflight, gitws, ledger, loop
from frutlups.config import Config, SeatConfig, Timeouts
from frutlups.procs import STREAM_CAP, run_process
from frutlups.roadmap import load
from frutlups.seats import claude, pi

HERE = Path(__file__).parent
ROW = "| M001-S01-H1 | P2 | open | Preserve this verbatim holistic finding. |"
ROW2 = "| M001-S01-H2 | P1 | open | Preserve the second finding, too. |"


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True)


@pytest.fixture
def project(tmp_path, monkeypatch, initialized_project):
    root = tmp_path / "repo"
    shutil.copytree(initialized_project, root)
    cfg = Config(
        "frutlups/1",
        "roadmap",
        2,
        30,
        5,
        False,
        "prompts/for_coding_agent",
        "prompts/for_review_agent",
        "05_governance/reviews",
        "05_governance/ledger.jsonl",
        {
            "coder": SeatConfig("pi", "fake", "medium", "fake", "high"),
            "reviewer": SeatConfig("claude", "fake", "high"),
            "claude_reviewer": SeatConfig("claude", "fake", "high"),
            "holistic": SeatConfig("claude", "fake", "high"),
        },
        Timeouts(10, 10, 10),
        sys.executable,
        sys.executable,
        shutil.which("git"),
        None,
        (str(Path(sys.executable).parent), str(Path(shutil.which("git")).parent)),
        tuple(
            k
            for k in ("SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "TEMP", "TMP")
            if k in os.environ
        ),
    )
    # Keep real subprocess execution and adapter parsing; only replace the CLI argv.
    monkeypatch.setattr(
        pi,
        "_argv",
        lambda job, cfg: (
            sys.executable,
            str(HERE / "fake_pi.py"),
            str(job.prompt_path),
        ),
    )
    monkeypatch.setattr(
        claude,
        "_argv",
        lambda job, cfg: (
            sys.executable,
            str(HERE / "fake_claude.py"),
        ),
    )

    def auth(argv, cwd, env, timeout, stdout, stderr):
        assert argv[1] == "auth"
        return run_process(
            (sys.executable, "-c", "print('logged in')"),
            cwd,
            env,
            timeout,
            stdout,
            stderr,
        )

    monkeypatch.setattr(_preflight.procs, "run_process", auth)
    return root, cfg, load(root / "roadmap.yaml")


def plan(root, **roles):
    (root / "local_state" / "plan.json").write_text(json.dumps(roles), encoding="utf-8")


def state(root, cfg, roadmap):
    return ledger.fold(ledger.read(root / cfg.ledger), roadmap)


def events_of(root, cfg, kind):
    return [e for e in ledger.read(root / cfg.ledger) if e.ev == kind]


def conform(root, cfg, roadmap):
    assert ledger.check(root / cfg.ledger, root) == ()
    for command in ("check", "status"):
        result = subprocess.run(
            [sys.executable, str(root / "scripts" / "ledger.py"), "--root", str(root), command],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        if command == "status":
            assert result.stdout == ledger.status_text(roadmap, state(root, cfg, roadmap)) + "\n"


@pytest.mark.parametrize("phase", ["prompt", "stop"])
def test_failing_notify_cannot_prevent_stop_event(project, phase):
    root, cfg, roadmap = project
    calls = []

    def fail(row):
        calls.append(row["action"])
        raise RuntimeError("synthetic notify failure")

    if phase == "stop":
        (root / "STOP").touch()
    runner = loop.Loop(root, cfg, roadmap, emit=fail)
    with pytest.raises(RuntimeError, match="synthetic notify failure"):
        runner.iteration()
    assert calls == (["prompt", "stop"] if phase == "prompt" else ["stop"])
    stops = events_of(root, cfg, "stop")
    assert len(stops) == 1
    assert stops[0].data["reason"] == ("internal" if phase == "prompt" else "kill_switch")
    assert stops[0].data["detail"] == (
        "M001-S01: RuntimeError: synthetic notify failure; owner must inspect"
        if phase == "prompt"
        else "STOP exists; owner must remove it to resume"
    )


@pytest.mark.parametrize("copy_number", [1, 2])
def test_initialized_fixture_copies_are_isolated(project, initialized_project, copy_number):
    root, cfg, roadmap = project
    assert root != initialized_project
    assert gitws.head(root) == gitws.head(initialized_project)
    assert gitws.is_clean(root)
    assert state(root, cfg, roadmap).slices["M001-S01"].step == "unstarted"
    marker = root / "07_app" / "copy-marker.txt"
    assert not marker.exists()
    marker.write_text(str(copy_number), encoding="utf-8")
    git(root, "add", "07_app/copy-marker.txt")
    assert not (initialized_project / "07_app" / "copy-marker.txt").exists()
    assert gitws.is_clean(initialized_project)


def test_initial_pass_with_fake_seats_and_conformant_ledger(project):
    root, cfg, roadmap = project
    plan(root, coder=[{"writes": {"07_app/result.txt": "first"}}])

    assert loop.run(root, cfg, roadmap) == loop.StopReason.done

    current = state(root, cfg, roadmap).slices["M001-S01"]
    assert current.step == "accepted"
    assert current.round == 1
    assert events_of(root, cfg, "coded")[0].data["tokens_in"] == 17
    assert events_of(root, cfg, "reviewed")[0].data["tokens_in"] == 13
    assert events_of(root, cfg, "artifact")[0].data["role"] == "review_prompt"
    report_path = current.last_report
    review_prompt = (root / "local_state" / "reviewer-0.md").read_text(encoding="utf-8")
    assert report_path in review_prompt
    assert "Added file: 07_app/result.txt\nfirst" in review_prompt
    conform(root, cfg, roadmap)


@pytest.mark.parametrize("failure", ["needs_work", "verification"])
def test_correction_then_pass(project, failure):
    root, cfg, roadmap = project
    reviewers = [{"verdict": "needs_work", "rows": [ROW]}, {}] if failure == "needs_work" else [{}]
    plan(
        root,
        coder=[{"writes": {"07_app/result.txt": "bad"}}, {"writes": {"07_app/result.txt": "good"}}],
        reviewer=reviewers,
    )
    if failure == "verification":
        command = (
            sys.executable,
            "-c",
            "from pathlib import Path; assert Path('07_app/result.txt').read_text() == 'good'",
        )
        roadmap = replace(roadmap, verification_full=command)

    assert loop.run(root, cfg, roadmap) == loop.StopReason.done

    current = state(root, cfg, roadmap).slices["M001-S01"]
    assert current.round == 2
    assert current.corrective_rounds_used == 1
    prompt = (root / "local_state" / "coder-1.md").read_text(encoding="utf-8")
    assert (ROW if failure == "needs_work" else "AssertionError") in prompt
    conform(root, cfg, roadmap)


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        ("transport", loop.StopReason.done),
        ("output", loop.StopReason.done),
        ("timeout", loop.StopReason.done),
        ("auth", loop.StopReason.seat_auth),
        ("capacity", loop.StopReason.seat_capacity),
    ],
)
def test_seat_failure_classification_and_same_round_retry(project, failure, expected):
    root, cfg, roadmap = project
    if failure == "timeout":
        cfg = replace(cfg, timeouts=replace(cfg.timeouts, coder_seconds=1))
    plan(root, coder=[{"failure": failure}, {"writes": {"07_app/result.txt": "ok"}}])

    assert loop.run(root, cfg, roadmap) == expected

    assert len(events_of(root, cfg, "prompt")) == 1
    assert state(root, cfg, roadmap).slices["M001-S01"].corrective_rounds_used == 0
    count = (root / "local_state" / "coder-count.txt").read_text()
    assert count == ("2" if expected == loop.StopReason.done else "1")
    conform(root, cfg, roadmap)


def test_second_transport_failure_stops(project):
    root, cfg, roadmap = project
    plan(root, coder=[{"failure": "transport"}])
    assert loop.run(root, cfg, roadmap) == loop.StopReason.seat_transport
    assert (root / "local_state" / "coder-count.txt").read_text() == "2"


def test_path_violation_preserves_tree_and_refuses_resume(project):
    root, cfg, roadmap = project
    plan(root, coder=[{"writes": {"forbidden.txt": "evidence", "07_app/valid.txt": "valid"}}])
    assert loop.run(root, cfg, roadmap) == loop.StopReason.path_violation
    assert (root / "forbidden.txt").read_text() == "evidence"
    assert (root / "07_app/valid.txt").read_text() == "valid"
    assert events_of(root, cfg, "coded") == []
    before = (root / cfg.ledger).read_bytes()
    assert loop.run(root, cfg, roadmap) == loop.StopReason.preflight_failed
    assert (root / cfg.ledger).read_bytes() == before


def test_rename_from_forbidden_origin_stops_with_rename_intact(project):
    root, cfg, roadmap = project
    original = (root / "AGENTS.md").read_bytes()
    plan(root, coder=[{"renames": {"AGENTS.md": "07_app/renamed.md"}}])

    assert loop.run(root, cfg, roadmap) == loop.StopReason.path_violation
    assert (root / "07_app/renamed.md").read_bytes() == original
    assert not (root / "AGENTS.md").exists()
    assert events_of(root, cfg, "coded") == []


def test_read_only_reviewer_mutation_stops_before_reviewed(project):
    root, cfg, roadmap = project
    plan(
        root,
        coder=[{"writes": {"07_app/result.txt": "coder"}}],
        reviewer=[{"writes": {"07_app/result.txt": "reviewer mutation"}}],
    )

    assert loop.run(root, cfg, roadmap) == loop.StopReason.path_violation
    assert (root / "07_app/result.txt").read_text() == "reviewer mutation"
    assert events_of(root, cfg, "reviewed") == []


def test_all_routed_reviewers_contribute_before_one_review_transition(project):
    root, cfg, roadmap = project
    roadmap = replace(
        roadmap,
        review_routing={
            **roadmap.review_routing,
            "ordinary": ("reviewer", "claude_reviewer"),
        },
    )
    plan(root, reviewer=[{}, {"verdict": "needs_work", "rows": [ROW]}, {}])

    assert loop.run(root, cfg, roadmap) == loop.StopReason.done

    reviews = events_of(root, cfg, "reviewed")
    assert len(reviews) == 2
    assert reviews[0].data["verdict"] == "needs_work"
    assert reviews[0].data["open"] == ("M001-S01-H1",)
    assert reviews[0].data["seat"] == "reviewer,claude_reviewer"
    assert reviews[0].data["tokens_in"] == 26
    assert reviews[1].data["verdict"] == "pass"
    assert (root / "local_state" / "reviewer-count.txt").read_text() == "4"
    conform(root, cfg, roadmap)


def test_crash_before_coded_refuses_preflight_with_tree_intact(project, monkeypatch):
    root, cfg, roadmap = project
    plan(root, coder=[{"writes": {"07_app/result.txt": "survives crash"}}])
    runner = loop.Loop(root, cfg, roadmap)
    assert runner.iteration() is None
    original = runner.append

    def crash(ev, **data):
        if ev == "coded":
            raise KeyboardInterrupt("simulated crash after coder")
        original(ev, **data)

    monkeypatch.setattr(runner, "append", crash)
    with pytest.raises(KeyboardInterrupt):
        runner.iteration()
    assert (root / "07_app/result.txt").read_text() == "survives crash"
    assert any("07_app/result.txt" in e for e in loop.preflight(root, cfg, roadmap))
    assert events_of(root, cfg, "coded") == []


@pytest.mark.parametrize("iterations", [1, 2, 3, 4])
def test_resume_each_mid_slice_step(project, iterations):
    root, cfg, roadmap = project
    plan(root, coder=[{"writes": {"07_app/result.txt": "ok"}}])
    for _ in range(iterations):
        assert loop.run(root, cfg, roadmap, once=True) is None
    assert loop.run(root, cfg, roadmap) == loop.StopReason.done
    assert len(events_of(root, cfg, "prompt")) == 1
    conform(root, cfg, roadmap)


def test_holistic_reopen_carries_verbatim_rows_then_closes(project):
    root, cfg, roadmap = project
    milestone = replace(roadmap.milestones[0], holistic_review=True)
    roadmap = replace(roadmap, milestones=(milestone,))
    # Keep the fixture script's roadmap in sync for the conformance check.
    data = yaml.safe_load((root / "roadmap.yaml").read_text(encoding="utf-8"))
    data["milestones"][0]["holistic_review"] = True
    (root / "roadmap.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    git(root, "add", "roadmap.yaml")
    git(root, "commit", "-qm", "enable holistic fixture")
    plan(
        root,
        coder=[
            {"writes": {"07_app/result.txt": "first"}},
            {"writes": {"07_app/result.txt": "fixed"}},
        ],
        holistic=[{"verdict": "needs_work", "rows": [ROW, ROW2]}, {}],
    )
    runner = loop.Loop(root, cfg, roadmap)
    for _ in range(6):
        assert runner.iteration() is None
    reopened = state(root, cfg, roadmap).slices["M001-S01"]
    assert reopened.reopen_reason == "holistic findings M001-S01-H1, M001-S01-H2"
    assert reopened.reopen_report
    assert reopened.round == 2
    assert loop.run(root, cfg, roadmap) == loop.StopReason.done
    prompt = (root / "local_state" / "coder-1.md").read_text(encoding="utf-8")
    assert ROW in prompt
    assert ROW2 in prompt
    assert len(events_of(root, cfg, "reopened")) == 1
    assert state(root, cfg, roadmap).milestones_done == frozenset({"M001"})
    conform(root, cfg, roadmap)


@pytest.mark.parametrize(
    ("until", "accepted", "expected"),
    [
        ("slice", 1, loop.StopReason.boundary),
        ("milestone", 2, loop.StopReason.boundary),
        ("roadmap", 3, loop.StopReason.done),
    ],
)
def test_until_boundaries(project, until, accepted, expected):
    root, cfg, roadmap = project
    first = roadmap.milestones[0]
    first = replace(first, slices=(first.slices[0], replace(first.slices[0], id="M001-S02")))
    second = replace(
        first, id="M002", slices=(replace(first.slices[0], id="M002-S01", milestone_id="M002"),)
    )
    roadmap = replace(roadmap, milestones=(first, second))

    assert loop.run(root, cfg, roadmap, until) == expected
    assert len(events_of(root, cfg, "accepted")) == accepted


def test_commit_on_accept_includes_event_and_exact_product_evidence(project):
    root, cfg, roadmap = project
    plan(root, coder=[{"writes": {"07_app/result.txt": "committed"}}])
    original = gitws.head(root)

    assert loop.run(root, replace(cfg, commit_on_accept=True), roadmap) == loop.StopReason.done

    assert gitws.head(root) != original
    assert git(root, "log", "-1", "--format=%s").stdout.strip() == b"Accept M001-S01 round 1"
    committed = git(root, "show", f"HEAD:{cfg.ledger}").stdout.decode()
    assert '"ev":"accepted"' in committed
    assert git(root, "show", "HEAD:07_app/result.txt").stdout == b"committed"
    paths = git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").stdout.decode()
    assert "local_state" not in paths
    conform(root, cfg, roadmap)


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("jobs", loop.StopReason.budget_exhausted),
        ("wall", loop.StopReason.budget_exhausted),
        ("STOP", loop.StopReason.kill_switch),
        ("rounds", loop.StopReason.rounds_exhausted),
        ("blocked", loop.StopReason.blocked_verdict),
    ],
)
def test_budget_and_owner_stops(project, kind, expected):
    root, cfg, roadmap = project
    if kind == "jobs":
        cfg = replace(cfg, max_jobs=1)
    if kind in ("rounds", "blocked"):
        cfg = replace(cfg, max_corrective_rounds=0)
        plan(
            root,
            reviewer=[{"verdict": "blocked" if kind == "blocked" else "needs_work", "rows": [ROW]}],
        )
    runner = loop.Loop(root, cfg, roadmap)
    assert runner.iteration() is None
    if kind == "wall":
        runner.clock = lambda: runner.started + 301
    if kind == "STOP":
        (root / "STOP").touch()
    result = None
    for _ in range(8):
        result = runner.iteration()
        if result:
            break
    assert result == expected
    assert events_of(root, cfg, "stop")[-1].data["reason"] == expected.value


@pytest.mark.parametrize("valid_second", [True, False])
def test_format_only_retry_is_bounded(project, valid_second):
    root, cfg, roadmap = project
    plan(root, reviewer=[{"text": "broken"}, {} if valid_second else {"text": "broken again"}])
    expected = loop.StopReason.done if valid_second else loop.StopReason.seat_output
    assert loop.run(root, cfg, roadmap) == expected
    assert (root / "local_state" / "reviewer-count.txt").read_text() == "2"
    assert "Format-only retry:" in (root / "local_state" / "reviewer-1.md").read_text(
        encoding="utf-8",
    )
    assert len(events_of(root, cfg, "prompt")) == 1
    conform(root, cfg, roadmap)


def test_preflight_reports_multiple_failures_without_writes(project):
    root, cfg, roadmap = project
    (root / "STOP").touch()
    (root / "AGENTS.md").write_bytes(b"x" * 8193)
    cfg = replace(cfg, pi=str(root / "missing-pi"))
    before = (root / cfg.ledger).read_bytes()
    errors = loop.preflight(root, cfg, roadmap)
    assert any("STOP" in e for e in errors)
    assert any("AGENTS.md" in e for e in errors)
    assert any("executable missing" in e for e in errors)
    assert (root / cfg.ledger).read_bytes() == before


@pytest.mark.parametrize("failure", ["exit", "launch", "streams"])
def test_preflight_authentication_failure_is_read_only(project, monkeypatch, failure):
    root, cfg, roadmap = project
    captured = []

    def auth(argv, cwd, env, timeout, stdout, stderr):
        assert argv[1] == "auth"
        assert timeout == 30
        code = "import sys; print('not authenticated'); sys.exit(1)"
        if failure == "streams":
            code = (
                "import os, sys; data=b'x'*(8*1024*1024+17); "
                "os.write(1,data); os.write(2,data); sys.exit(1)"
            )
        command = (str(root / "missing"),) if failure == "launch" else (sys.executable, "-c", code)
        result = run_process(command, cwd, env, timeout, stdout, stderr)
        if failure == "streams":
            assert result.stdout_truncated is True
            assert result.stderr_truncated is True
            assert stdout.stat().st_size == STREAM_CAP
            assert stderr.stat().st_size == STREAM_CAP
        captured.append((argv, env, stdout, stderr))
        return result

    monkeypatch.setattr(_preflight.procs, "run_process", auth)
    before = gitws.status(root)
    ledger_before = (root / cfg.ledger).read_bytes()

    assert loop.run(root, cfg, roadmap) == loop.StopReason.preflight_failed
    assert len(captured) == 2
    assert all(env["FRUTLUPS_SEAT"] == "verification" for _, env, _, _ in captured)
    assert all(stdout.exists() and stderr.exists() for _, _, stdout, stderr in captured)
    assert gitws.status(root) == before
    assert (root / cfg.ledger).read_bytes() == ledger_before


def test_empty_active_frontier_requires_specification(project):
    root, cfg, roadmap = project
    roadmap = replace(roadmap, milestones=(replace(roadmap.milestones[0], status="planned"),))
    assert loop.run(root, cfg, roadmap) == loop.StopReason.needs_specification
    assert events_of(root, cfg, "prompt") == []


@pytest.mark.skipif(os.name != "nt", reason="Windows .cmd launcher regression")
def test_preflight_probe_timeout_kills_launcher_and_grandchild(project, tmp_path, monkeypatch):
    from test_procs import _running

    root, cfg, roadmap = project
    script = tmp_path / "probe.py"
    script.write_text(
        "import pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid))\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    launcher = tmp_path / "probe.cmd"
    launcher.write_text(f'@echo off\n"{sys.executable}" "{script}" %*\n', encoding="utf-8")
    results = []

    def auth(argv, cwd, env, timeout, stdout, stderr):
        assert timeout == 30
        pid_file = tmp_path / f"grandchild-{len(results)}.pid"
        result = run_process((str(launcher), str(pid_file)), cwd, env, 1, stdout, stderr)
        assert result.timed_out is True
        assert result.exit is not None
        assert result.exception is None
        results.append((int(pid_file.read_text()), stdout, stderr))
        return result

    monkeypatch.setattr(_preflight.procs, "run_process", auth)
    before = gitws.status(root)
    ledger_before = (root / cfg.ledger).read_bytes()
    started = time.monotonic()

    assert loop.run(root, cfg, roadmap) == loop.StopReason.preflight_failed
    assert time.monotonic() - started < 15
    assert len(results) == 2
    assert all(not _running(pid) for pid, _, _ in results)
    assert all(stdout.exists() and stderr.exists() for _, stdout, stderr in results)
    assert gitws.status(root) == before
    assert (root / cfg.ledger).read_bytes() == ledger_before


@pytest.mark.parametrize("failure", ["transport", "output", "timeout"])
@pytest.mark.parametrize("allowed", [True, False])
def test_failed_coder_edits_are_fenced_and_preserved_without_retry(project, failure, allowed):
    root, cfg, roadmap = project
    cfg = replace(cfg, timeouts=replace(cfg.timeouts, coder_seconds=1))
    path = "07_app/partial.txt" if allowed else "outside.txt"
    plan(root, coder=[{"failure": failure, "writes": {path: "partial evidence"}}, {}])
    expected = loop.StopReason.seat_transport if allowed else loop.StopReason.path_violation

    assert loop.run(root, cfg, roadmap) == expected
    assert (root / path).read_text() == "partial evidence"
    assert (root / "local_state" / "coder-count.txt").read_text() == "1"
    assert events_of(root, cfg, "coded") == []
    assert len(events_of(root, cfg, "prompt")) == 1
    assert state(root, cfg, roadmap).slices["M001-S01"].round == 1
    assert state(root, cfg, roadmap).slices["M001-S01"].corrective_rounds_used == 0
    stop = events_of(root, cfg, "stop")[-1]
    assert stop.data["reason"] == expected
    assert path in stop.data["detail"]
    before = (root / cfg.ledger).read_bytes()
    assert loop.run(root, cfg, roadmap) == loop.StopReason.preflight_failed
    assert (root / cfg.ledger).read_bytes() == before


@pytest.mark.parametrize("route", [("reviewer",), ("reviewer", "claude_reviewer")])
def test_saved_seat_text_scrubs_paths_without_truncating_reports(project, monkeypatch, route):
    root, cfg, roadmap = project
    cfg = replace(cfg, commit_on_accept=True)
    roadmap = replace(
        roadmap,
        milestones=(replace(roadmap.milestones[0], holistic_review=True),),
        review_routing={**roadmap.review_routing, "ordinary": route},
    )
    data = yaml.safe_load((root / "roadmap.yaml").read_text(encoding="utf-8"))
    data["milestones"][0]["holistic_review"] = True
    (root / "roadmap.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    git(root, "add", "roadmap.yaml")
    git(root, "commit", "-qm", "enable holistic fixture")
    monkeypatch.setenv("FIXTURE_SECRET", "synthetic-seat-secret")
    tail = "x" * 6000 + " TAIL-MARKER"
    paths = (
        f"Root `{root}/07_app/file.py`; "
        "Windows `Q:\\fixture folder\\file.py`; "
        "UNC `\\\\fixture-server\\share\\file.py`; "
        "POSIX `/home/fixture/file.py`; "
        "relative 07_app/file.py; URL https://example.invalid/file; synthetic-seat-secret"
    )
    portable = (
        "Root `<repo>/07_app/file.py`; Windows `<absolute-path>`; "
        "UNC `<absolute-path>`; POSIX `<absolute-path>`; "
        "relative 07_app/file.py; URL https://example.invalid/file; <redacted>"
    )
    notes = "BEGIN-NOTES\n" + paths + "\n" + tail
    review_actions = [
        {"rows": [f"| M001-S01-F{index} | P3 | carried | {paths} {tail} |"]}
        for index in range(len(route))
    ]
    plan(
        root,
        coder=[{"text": notes}],
        reviewer=review_actions,
        holistic=[{"rows": [f"| M001-S01-H3 | P3 | carried | {paths} {tail} |"]}],
    )

    assert loop.run(root, cfg, roadmap) == loop.StopReason.done
    current = state(root, cfg, roadmap).slices["M001-S01"]
    saved_notes = (root / current.notes_path).read_text(encoding="utf-8")
    assert saved_notes == "BEGIN-NOTES\n" + portable + "\n" + tail
    assert git(root, "show", f"HEAD:{current.notes_path}").stdout.decode() == saved_notes
    report_paths = [current.last_report] + [
        event.data["path"]
        for event in events_of(root, cfg, "artifact")
        if event.data["role"] == "holistic_report"
    ]
    for path in report_paths:
        report = (root / path).read_text(encoding="utf-8")
        assert report.startswith("# Review:")
        assert portable in report
        assert tail in report
        assert "## Closure Decision" in report
        assert "Verdict: pass - next: Continue the fixture." in report
        assert str(root) not in report
        assert "synthetic-seat-secret" not in report
    assert git(root, "show", f"HEAD:{current.last_report}").stdout.decode() == (
        root / current.last_report
    ).read_text(encoding="utf-8")
    conform(root, cfg, roadmap)
