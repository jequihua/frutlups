import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import conftest
import pytest
import yaml

from frutlups import gitws, ledger, loop, verdict
from frutlups._loop_evidence import workspace_errors
from frutlups.procs import STREAM_CAP, run_process
from frutlups.roadmap import load
from frutlups.seats import claude, pi

from _native_support import Controller, run_transitions, grant_project, probe_errors, next_grant

HERE = Path(__file__).parent
ROW = "| M001-S01-H1 | P2 | open | Preserve this verbatim holistic finding. |"
ROW2 = "| M001-S01-H2 | P1 | open | Preserve the second finding, too. |"


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True)


@pytest.fixture
def project(tmp_path, monkeypatch, initialized_project):
    root = tmp_path / "repo"
    shutil.copytree(initialized_project, root)
    cfg = conftest.offline_config()
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

    return root, cfg, load(root / "roadmap.yaml")


@pytest.fixture
def native_project(project):
    return grant_project(project)


def canonical_project(project, *, verification_full=None, ordinary_reviewers=None):
    """Commit intended scratch authority before any grant or task issuance."""
    root, cfg, _ = project
    prefix = (root / cfg.ledger).read_bytes()
    assert not ledger.read(root / cfg.ledger)
    path = root / "roadmap.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    changed = False
    if verification_full is not None and data["verification"]["full"] != list(verification_full):
        data["verification"]["full"] = list(verification_full)
        changed = True
    if ordinary_reviewers is not None and data["review"]["ordinary"] != list(ordinary_reviewers):
        data["review"]["ordinary"] = list(ordinary_reviewers)
        changed = True
    if changed:
        path.write_text(yaml.safe_dump(data), encoding="utf-8", newline="\n")
        git(root, "add", "roadmap.yaml")
        git(root, "commit", "-qm", "owned scratch command and review authority")
    assert (root / cfg.ledger).read_bytes() == prefix
    return root, cfg, load(path)


@pytest.fixture
def holistic_project(project):
    root, cfg, roadmap = project
    roadmap = replace(
        roadmap, milestones=(replace(roadmap.milestones[0], holistic_review=True),)
    )
    # Keep the fixture script's roadmap in sync for the conformance check.
    data = yaml.safe_load((root / "roadmap.yaml").read_text(encoding="utf-8"))
    data["milestones"][0]["holistic_review"] = True
    (root / "roadmap.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    git(root, "add", "roadmap.yaml")
    git(root, "commit", "-qm", "enable holistic fixture")
    return root, cfg, roadmap


@pytest.fixture
def native_holistic_project(holistic_project):
    return grant_project(holistic_project)


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
            current = state(root, cfg, roadmap)
            lines = [ledger.status_text(roadmap, current)]
            for name, run in current.runs.items():
                balances = " ".join(f"{key}={run['remaining'][key]}" for key in
                                    ("jobs", "transport_retries", "format_retries", "seconds"))
                lines.append(f"run {name} revision={run['revision']} stop={run['admission']['stop']} "
                             f"state={run['closed'] or 'open'} remaining {balances}")
            from frutlups import commit
            lines.extend(f"commit {row['id']}: {row['state']}" for row in
                         commit.status(root, ledger.read(root / cfg.ledger), roadmap, executable=cfg.git))
            lines.extend(f"{mid} holistic: {value['step']}" for mid, value in current.holistic.items())
            lines.extend(f"unresolved invocation: {key}" for key, value in current.attempts.items() if not value["finish"])
            for milestone in roadmap.milestones:
                complete = all(current.slices[item.id].step == "accepted" for item in milestone.slices)
                complete = complete and (not milestone.holistic_review or milestone.id in current.milestones_done)
                if milestone.status == "done" and not complete:
                    lines.append(f"warning: {milestone.id} declared done without ledger closure")
            assert result.stdout == "\n".join(lines) + "\n"
            for diagnostic in lines[1:]:
                assert result.stdout.splitlines().count(diagnostic) == 1


@pytest.mark.parametrize("phase", ["prompt", "stop"])
def test_failing_notify_preserves_effectful_stop_or_static_refusal(native_project, phase):
    root, cfg, roadmap = native_project
    calls = []

    def fail(row):
        calls.append(row["action"])
        raise RuntimeError("synthetic notify failure")

    if phase == "stop":
        (root / "STOP").touch()
        before = (root / cfg.ledger).read_bytes()
        with pytest.raises(RuntimeError, match="synthetic notify failure"):
            run_transitions(root, cfg, roadmap, emit=fail)
        assert calls == ["preflight"]
        assert (root / cfg.ledger).read_bytes() == before
    else:
        # Native prompt issuance has no callback. Its first effectful callback
        # follows durable verification; notification failure cannot erase it.
        runner = Controller(root, cfg, roadmap, emit=fail)
        assert runner.iteration() is None
        assert runner.iteration() is None
        with pytest.raises(RuntimeError, match="synthetic notify failure"):
            runner.iteration()
        assert calls == ["verified", "stop"]
        assert len(events_of(root, cfg, "verified")) == 1
        assert state(root, cfg, roadmap).slices["M001-S01"].step == "reviewing"
    assert events_of(root, cfg, "stop") == []  # No retired general writer.



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


def test_initial_pass_with_fake_seats_and_conformant_ledger(native_project):
    root, cfg, roadmap = native_project
    plan(root, coder=[{"writes": {"07_app/result.txt": "first"}}])

    assert run_transitions(root, cfg, roadmap) == loop.StopReason.done

    current = state(root, cfg, roadmap).slices["M001-S01"]
    assert current.step == "accepted"
    assert current.round == 1
    assert events_of(root, cfg, "coded")[0].data["tokens_in"] == 17
    assert events_of(root, cfg, "coded")[0].data["cost_usd"] == 0.01
    attempts = state(root, cfg, roadmap).attempts
    review = next(a for a in attempts.values() if a["start"]["role"] == "reviewer")
    assert review["finish"]["usage"]["tokens_in"] == 13
    assert len([e for e in events_of(root, cfg, "artifact") if e.data["role"] == "review_prompt"]) == 1
    report_path = current.last_report
    review_prompt = (root / "local_state" / "reviewer-0.md").read_text(encoding="utf-8")
    assert "Return the report" in review_prompt
    assert report_path == events_of(root, cfg, "reviewed")[0].data["report"]
    assert (root / report_path).is_file()
    pages = [root / event.data["path"] for event in events_of(root, cfg, "artifact")
             if "page" in event.data["path"]]
    assert any("07_app/result.txt" in page.read_text(encoding="utf-8") and "first" in page.read_text(encoding="utf-8")
               for page in pages)
    conform(root, cfg, roadmap)



@pytest.mark.parametrize("failure", ["needs_work", "verification"])
def test_correction_then_pass(project, failure):
    if failure == "verification":
        command = (
            sys.executable,
            "-c",
            "from pathlib import Path; assert Path('07_app/result.txt').read_text() == 'good'",
        )
        project = canonical_project(project, verification_full=command)
    root, cfg, roadmap = grant_project(project)
    reviewers = [{"verdict": "needs_work", "rows": [ROW]}, {"close_ids": ["M001-S01-H1"]}] if failure == "needs_work" else [{}]
    plan(
        root,
        coder=[{"writes": {"07_app/result.txt": "bad"}}, {"writes": {"07_app/result.txt": "good"}}],
        reviewer=reviewers,
    )
    assert run_transitions(root, cfg, roadmap) == loop.StopReason.done

    current = state(root, cfg, roadmap).slices["M001-S01"]
    assert current.round == 2
    assert current.corrective_rounds_used == 1
    prompt = (root / "local_state" / "coder-1.md").read_text(encoding="utf-8")
    if failure == "needs_work":
        assert ROW in prompt
    else:
        failed = events_of(root, cfg, "verified")[0]
        receipt = json.loads((root / failed.data["receipt"]).read_text("utf-8"))
        assert receipt["ok"] is False
        assert "AssertionError" in receipt["commands"][0]["stderr_tail"]
        assert failed.data["receipt"] in prompt
    conform(root, cfg, roadmap)



@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        ("transport", loop.StopReason.done),
        ("output", loop.StopReason.seat_output),
        ("timeout", loop.StopReason.done),
        ("auth", loop.StopReason.seat_auth),
        ("capacity", loop.StopReason.seat_capacity),
    ],
)
def test_seat_failure_classification_and_same_round_retry(project, failure, expected, monkeypatch):
    root, cfg, roadmap = project
    if failure == "timeout":
        cfg = replace(cfg, timeouts=replace(cfg.timeouts, coder_seconds=1))
    if failure != "timeout":
        root, cfg, roadmap = grant_project(project)
    plan(root, coder=[{"failure": failure}, {"writes": {"07_app/result.txt": "ok"}}])

    if failure == "timeout":
        # D080 preserves this node as the precise no-profile refusal oracle.
        from frutlups import _preflight
        def forbidden(*a, **k):
            pytest.fail("probe or seat after profile-free refusal")
        monkeypatch.setattr(_preflight, "_probe", forbidden)
        monkeypatch.setattr(pi.PiSeat, "run", forbidden)
        monkeypatch.setattr(claude.ClaudeSeat, "run", forbidden)
        monkeypatch.setattr(loop.Loop, "_job", forbidden)
        before = gitws.workspace_witness(root, executable=cfg.git)
        authority = _preflight._authority_bytes(root)
        saved = (root / cfg.ledger).read_bytes()
        notices = []
        runner = Controller(root, cfg, roadmap, emit=notices.append)
        assert runner.iteration() == loop.StopReason.internal
        assert notices[-1]["detail"] == (
            "M001-S01: LedgerError: fresh dispatch requires an autonomy profile; owner must inspect")
        assert gitws.workspace_witness(root, executable=cfg.git) == before
        assert _preflight._authority_bytes(root) == authority
        assert (root / cfg.ledger).read_bytes() == saved
        assert events_of(root, cfg, "prompt") == []
        assert not state(root, cfg, roadmap).attempts
        assert runner.jobs == 0
        for path in ("local_state/frutlups/jobs", "local_state/coder-count.txt", "07_app/result.txt"):
            assert not (root / path).exists()
        conform(root, cfg, roadmap)
        return
    else:
        assert run_transitions(root, cfg, roadmap) == expected

    assert len(events_of(root, cfg, "prompt")) == 1
    assert state(root, cfg, roadmap).slices["M001-S01"].corrective_rounds_used == 0
    count = (root / "local_state" / "coder-count.txt").read_text()
    assert count == ("2" if expected == loop.StopReason.done else "1")
    conform(root, cfg, roadmap)



def test_granted_real_timeout_settles_lifetime_without_second_dispatch(native_project):
    root, cfg, roadmap = native_project
    cfg = replace(cfg, timeouts=replace(cfg.timeouts, coder_seconds=1))
    plan(root, coder=[{"failure": "timeout"}, {"writes": {"07_app/result.txt": "must not run"}}])
    notices = []
    runner = Controller(root, cfg, roadmap, emit=notices.append)
    assert runner.iteration() is None  # issue the prompt before the seat operation
    assert runner.iteration() == loop.StopReason.internal
    current = state(root, cfg, roadmap)
    assert len(current.attempts) == 1
    attempt = next(iter(current.attempts.values()))
    assert attempt["start"]["allowance_seconds"] == 1
    assert attempt["finish"]["status"] == "failed"
    assert attempt["finish"]["usage"]["secs"] > 1
    run = current.runs[current.run_head["run"]]
    assert run["closed"] == "overrun"
    assert run["consumed"]["jobs"] == 1
    import math
    assert run["consumed"]["seconds"] == math.ceil(attempt["finish"]["usage"]["secs"])
    assert run["remaining"]["seconds"] == run["admission"]["allowance"]["seconds"] - run["consumed"]["seconds"]
    saved = (root / cfg.ledger).read_bytes()
    assert runner.iteration() == loop.StopReason.boundary
    assert (root / cfg.ledger).read_bytes() == saved
    assert (root / "local_state/coder-count.txt").read_text() == "1"
    assert not (root / "07_app/result.txt").exists()
    assert len(events_of(root, cfg, "prompt")) == 1
    assert current.slices["M001-S01"].corrective_rounds_used == 0
    conform(root, cfg, roadmap)


def test_second_transport_failure_stops(native_project):
    root, cfg, roadmap = native_project
    plan(root, coder=[{"failure": "transport"}])
    assert run_transitions(root, cfg, roadmap) == loop.StopReason.seat_transport
    assert (root / "local_state" / "coder-count.txt").read_text() == "2"



def test_path_violation_preserves_tree_and_refuses_resume(native_project):
    root, cfg, roadmap = native_project
    plan(root, coder=[{"writes": {"forbidden.txt": "evidence", "07_app/valid.txt": "valid"}}])
    assert run_transitions(root, cfg, roadmap) == loop.StopReason.path_violation
    assert (root / "forbidden.txt").read_text() == "evidence"
    assert (root / "07_app/valid.txt").read_text() == "valid"
    assert events_of(root, cfg, "coded") == []
    before = (root / cfg.ledger).read_bytes()
    assert run_transitions(root, cfg, roadmap) == loop.StopReason.preflight_failed
    assert (root / cfg.ledger).read_bytes() == before



def test_rename_from_forbidden_origin_stops_with_rename_intact(native_project):
    root, cfg, roadmap = native_project
    original = (root / "AGENTS.md").read_bytes()
    plan(root, coder=[{"renames": {"AGENTS.md": "07_app/renamed.md"}}])

    assert run_transitions(root, cfg, roadmap) == loop.StopReason.path_violation
    assert (root / "07_app/renamed.md").read_bytes() == original
    assert not (root / "AGENTS.md").exists()
    assert events_of(root, cfg, "coded") == []



def test_read_only_reviewer_mutation_stops_before_reviewed(native_project):
    root, cfg, roadmap = native_project
    plan(
        root,
        coder=[{"writes": {"07_app/result.txt": "coder"}}],
        reviewer=[{"writes": {"07_app/result.txt": "reviewer mutation"}}],
    )

    assert run_transitions(root, cfg, roadmap) == loop.StopReason.path_violation
    assert (root / "07_app/result.txt").read_text() == "reviewer mutation"
    assert events_of(root, cfg, "reviewed") == []



def test_all_routed_reviewers_contribute_before_one_review_transition(project):
    root, cfg, roadmap = grant_project(canonical_project(
        project, ordinary_reviewers=("reviewer", "claude_reviewer")))
    plan(root, reviewer=[{}, {"verdict": "needs_work", "rows": [ROW]}, {"close_ids": ["M001-S01-H1"]}])

    assert run_transitions(root, cfg, roadmap) == loop.StopReason.done

    reviews = events_of(root, cfg, "reviewed")
    assert len(reviews) == 2
    assert reviews[0].data["verdict"] == "needs_work"
    assert reviews[0].data["open"] == ("M001-S01-H1",)
    assert reviews[0].data["seat"] == "reviewer,claude_reviewer"
    first_reviews = [a for a in state(root, cfg, roadmap).attempts.values()
                     if a["start"]["role"] == "reviewer" and a["start"]["round"] == 1]
    assert sum(a["finish"]["usage"]["tokens_in"] for a in first_reviews) == 26
    assert reviews[1].data["verdict"] == "pass"
    assert (root / "local_state" / "reviewer-count.txt").read_text() == "4"
    conform(root, cfg, roadmap)



def test_two_routed_seats_readdress_shared_and_distinct_ids_through_acceptance(project):
    root, cfg, roadmap = grant_project(canonical_project(
        project, ordinary_reviewers=("reviewer", "claude_reviewer")))
    first_shared = "| M001-S01-F1 | P1 | open | First seat's shared id. |"
    second_shared = "| M001-S01-F1 | P2 | open | Second seat's shared id. |"
    first_unique = "| M001-S01-F2 | P2 | open | First seat's distinct id. |"
    second_unique = "| M001-S01-F3 | P1 | open | Second seat's distinct id. |"
    prior_ids = (
        "M001-S01-F1-reviewer", "M001-S01-F2",
        "M001-S01-F1-claude_reviewer", "M001-S01-F3",
    )
    open_rows = (
        "| M001-S01-F1-reviewer | P1 | open | First seat's shared id. |",
        first_unique,
        "| M001-S01-F1-claude_reviewer | P2 | open | Second seat's shared id. |",
        second_unique,
    )
    closed_rows = [row.replace("| open |", "| closed_by_review |") for row in open_rows]
    plan(
        root,
        coder=[
            {"writes": {"07_app/result.txt": "first"}},
            {"writes": {"07_app/result.txt": "corrected"}},
        ],
        reviewer=[
            {"verdict": "needs_work", "rows": [first_shared, first_unique]},
            {"verdict": "needs_work", "rows": [second_shared, second_unique]},
            {"close_ids": list(prior_ids)},
            {"close_ids": list(prior_ids)},
        ],
    )

    assert run_transitions(root, cfg, roadmap) == loop.StopReason.done

    reviews = events_of(root, cfg, "reviewed")
    assert len(reviews) == 2
    for event, round_no, expected_verdict, expected_open, rows in (
        (reviews[0], 1, "needs_work", prior_ids, open_rows),
        (reviews[1], 2, "pass", (), tuple(closed_rows)),
    ):
        assert event.round == round_no
        assert event.data["seat"] == "reviewer,claude_reviewer"
        assert event.data["verdict"] == expected_verdict
        assert event.data["open"] == expected_open
        text = (root / event.data["report"]).read_text(encoding="utf-8")
        parsed = verdict.parse(text)
        if round_no == 1:
            assert tuple(f.id for f in parsed.findings) == prior_ids
            assert verdict.finding_rows(text, prior_ids) == rows
        else:
            assert parsed.findings == ()
            assert tuple(u.id for u in parsed.updates) == prior_ids
            for update in parsed.updates:
                assert update.source == reviews[0].data["report"]
                assert update.sha == reviews[0].data["sha"]
                assert update.disposition == "closed_by_review"
    prompts = events_of(root, cfg, "prompt")
    assert [event.round for event in prompts] == [1, 2]
    corrective = (root / prompts[1].data["path"]).read_text(encoding="utf-8")
    for row in open_rows:
        assert row in corrective
    from frutlups import render
    attempt = next(a for a in state(root, cfg, roadmap).attempts.values()
                   if a["start"]["role"] == "coder" and a["start"]["round"] == 2)
    assert (root / "local_state" / "coder-1.md").read_text(encoding="utf-8") == (
        corrective + render.outcome_contract("M001-S01", 2, attempt["start"]["invocation"]))
    assert (root / "local_state" / "reviewer-count.txt").read_text() == "4"
    assert (root / "local_state" / "coder-count.txt").read_text() == "2"
    current = state(root, cfg, roadmap).slices["M001-S01"]
    assert current.step == "accepted"
    assert current.round == 2
    assert current.corrective_rounds_used == 1
    assert current.open_findings == ()
    accepted = events_of(root, cfg, "accepted")
    assert len(accepted) == 1
    assert accepted[0].round == 2
    conform(root, cfg, roadmap)



def test_recorded_architect_dirty_baseline_is_admitted_and_excluded_from_accept(native_project):
    root, cfg, roadmap = native_project
    cfg = replace(cfg, commit_on_accept=True)
    git(root, "add", cfg.ledger)
    git(root, "commit", "-qm", "record native grant")
    baseline_path = "architect-notes.txt"
    baseline_bytes = b"Architect-owned work in progress.\n"
    (root / baseline_path).write_bytes(baseline_bytes)
    assert workspace_errors(root, cfg, ledger.read(root / cfg.ledger)) == [
        f"unrecorded change: {baseline_path}",
    ]
    result = subprocess.run(
        [sys.executable, "-B", str(root / "scripts" / "prompt.py"),
         "M001-S01", "--root", str(root), "--allow-dirty"],
        cwd=root, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    prompts = events_of(root, cfg, "prompt")
    assert len(prompts) == 1
    prompt = prompts[0]
    assert prompt.by == "architect"
    assert prompt.round == 1
    assert result.stdout == prompt.data["path"] + "\n"
    assert tuple(dict(row) for row in prompt.data["baseline"]) == ({
        "path": baseline_path, "sha": ledger.evidence_sha(baseline_bytes), "kind": "added",
    },)
    assert state(root, cfg, roadmap).slices["M001-S01"].step == "coding"
    assert workspace_errors(root, cfg, ledger.read(root / cfg.ledger)) == []
    original_head = gitws.head(root)
    plan(root, coder=[{"writes": {"07_app/result.txt": "implemented"}}])

    assert run_transitions(root, cfg, roadmap) == loop.StopReason.done

    assert state(root, cfg, roadmap).slices["M001-S01"].step == "accepted"
    assert events_of(root, cfg, "prompt") == prompts
    coded = events_of(root, cfg, "coded")
    assert len(coded) == 1
    assert tuple(dict(row) for row in coded[0].data["changed"]) == ({
        "path": "07_app/result.txt", "sha": ledger.evidence_sha(b"implemented"), "kind": "added",
    },)
    assert gitws.head(root) != original_head
    assert git(root, "log", "-1", "--format=%s").stdout.strip() == b"Accept M001-S01 round 1"
    committed_paths = git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD")
    paths = committed_paths.stdout.decode().splitlines()
    assert baseline_path not in paths
    assert "07_app/result.txt" in paths
    assert cfg.ledger in paths
    assert git(root, "ls-tree", "--name-only", "HEAD", "--", baseline_path).stdout == b""
    assert git(root, "show", "HEAD:07_app/result.txt").stdout == b"implemented"
    assert (root / baseline_path).read_bytes() == baseline_bytes
    assert git(root, "status", "--porcelain", "--", baseline_path).stdout == (
        b"?? architect-notes.txt\n"
    )
    assert workspace_errors(root, cfg, ledger.read(root / cfg.ledger)) == []
    conform(root, cfg, roadmap)



def test_crash_before_coded_refuses_preflight_with_tree_intact(native_project, monkeypatch):
    root, cfg, roadmap = native_project
    plan(root, coder=[{"writes": {"07_app/result.txt": "survives crash"}}])
    runner = Controller(root, cfg, roadmap)
    assert runner.iteration() is None
    original = ledger._append_coding

    def crash(root, path, model, row, prefix, **kwargs):
        if row["ev"] == "coded":
            raise KeyboardInterrupt("simulated crash after coder")
        return original(root, path, model, row, prefix, **kwargs)

    monkeypatch.setattr(ledger, "_append_coding", crash)
    with pytest.raises(KeyboardInterrupt):
        runner.iteration()
    assert (root / "07_app/result.txt").read_text() == "survives crash"
    assert any("07_app/result.txt" in e for e in probe_errors(root, cfg, roadmap))
    assert events_of(root, cfg, "coded") == []



@pytest.mark.parametrize("iterations", [1, 2, 3, 4])
def test_resume_each_mid_slice_step(native_project, iterations):
    root, cfg, roadmap = native_project
    plan(root, coder=[{"writes": {"07_app/result.txt": "ok"}}])
    for _ in range(iterations):
        assert run_transitions(root, cfg, roadmap, once=True) is None
    assert run_transitions(root, cfg, roadmap) == loop.StopReason.done
    assert len(events_of(root, cfg, "prompt")) == 1
    conform(root, cfg, roadmap)



@pytest.mark.parametrize("commit_on_accept", [True, False])
def test_holistic_reopen_carries_verbatim_rows_then_closes(native_holistic_project, commit_on_accept):
    root, cfg, roadmap = native_holistic_project
    cfg = replace(cfg, commit_on_accept=commit_on_accept, max_wall_minutes=10)
    plan(
        root,
        coder=[
            {"writes": {"07_app/result.txt": "first"}},
            {"writes": {"07_app/result.txt": "fixed"}},
        ],
        reviewer=[{}, {"close_ids": ["M001-S01-H1", "M001-S01-H2"]}],
        holistic=[{"verdict": "needs_work", "rows": [ROW, ROW2]}, {}],
    )
    runner = Controller(root, cfg, roadmap)
    for _ in range(5):
        assert runner.iteration() is None
    accepted_head = gitws.head(root)
    assert runner.iteration() is None
    assert gitws.head(root) == accepted_head
    artifacts = [
        e.data["path"] for e in events_of(root, cfg, "artifact") if e.data["scope"] == "M001"
    ]
    assert len(artifacts) == 7
    assert events_of(root, cfg, "milestone_done") == []
    reopened = state(root, cfg, roadmap).slices["M001-S01"]
    assert reopened.reopened is True
    assert reopened.open_findings == ("M001-S01-H1", "M001-S01-H2")
    assert reopened.last_report == events_of(root, cfg, "holistic_reviewed")[0].data["report"]
    assert reopened.round == 2
    for _ in range(5):
        assert runner.iteration() is None
    if commit_on_accept:
        assert git(root, "log", "-1", "--format=%s").stdout.strip() == b"Accept M001-S01 round 2"
        # Native milestone evidence remains bound and retained until its own
        # close approval; it is not reclassified as slice-owned product.
        for event in events_of(root, cfg, "artifact"):
            if event.data["path"] in artifacts:
                assert ledger.evidence_sha((root / event.data["path"]).read_bytes()) == event.data["sha"]
        assert workspace_errors(root, cfg, ledger.read(root / cfg.ledger)) == []
    else:
        assert gitws.head(root) == accepted_head
    assert runner.iteration() is None
    if commit_on_accept:
        for path in artifacts:
            assert git(root, "show", f"HEAD:{path}").stdout == (root / path).read_bytes()
    assert runner.iteration() == loop.StopReason.done
    prompt = (root / "local_state" / "coder-1.md").read_text(encoding="utf-8")
    assert ROW in prompt
    assert ROW2 in prompt
    assert events_of(root, cfg, "reopened") == []
    assert len(events_of(root, cfg, "holistic_reviewed")) == 2
    assert state(root, cfg, roadmap).milestones_done == frozenset({"M001"})
    conform(root, cfg, roadmap)



@pytest.mark.parametrize("commit_on_accept", [True, False])
def test_holistic_pass_commits_exact_close_evidence(native_holistic_project, commit_on_accept):
    root, cfg, roadmap = native_holistic_project
    cfg = replace(cfg, commit_on_accept=commit_on_accept)
    runner = Controller(root, cfg, roadmap)
    for _ in range(5):
        assert runner.iteration() is None
    accepted_head = gitws.head(root)

    assert runner.iteration() is None

    assert state(root, cfg, roadmap).milestones_done == frozenset({"M001"})
    artifacts = {
        e.data["role"]: e.data["path"]
        for e in events_of(root, cfg, "artifact") if e.data["scope"] == "M001" and e.data["role"] != "evidence"
    }
    assert set(artifacts) == {"holistic_prompt", "holistic_report"}
    if commit_on_accept:
        assert git(root, "log", "-1", "--format=%s").stdout.strip() == b"Close M001"
        assert git(root, "rev-parse", "HEAD^").stdout.decode().strip() == accepted_head
        paths = git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD")
        done_event = events_of(root, cfg, "milestone_done")[0]
        all_paths = {e.data["path"] for e in events_of(root, cfg, "artifact") if e.data["scope"] == "M001"}
        assert set(paths.stdout.decode().splitlines()) == all_paths | {
            cfg.ledger, done_event.data["commit_intent"]["manifest"]["path"]}
        committed = git(root, "show", f"HEAD:{cfg.ledger}").stdout.decode()
        done = [json.loads(line) for line in committed.splitlines()][-1]
        assert done["ev"] == "milestone_done"
        assert done["milestone"] == "M001"
        assert done["holistic_report"] == artifacts["holistic_report"]
        assert [row.path for row in gitws.status(root)] == ["05_governance/backlog.md"]
        from frutlups import findings
        assert findings.matches_backlog(root, ledger.read(root / cfg.ledger))
    else:
        assert gitws.head(root) == accepted_head
    # Completion reports the stop without a retired general append.
    assert runner.iteration() == loop.StopReason.done
    conform(root, cfg, roadmap)



def test_holistic_format_retry_pass_commits_every_milestone_artifact(native_holistic_project):
    root, cfg, roadmap = native_holistic_project
    cfg = replace(cfg, commit_on_accept=True)
    plan(root, holistic=[{"text": "broken holistic report"}, {}])
    runner = Controller(root, cfg, roadmap)
    for _ in range(5):
        assert runner.iteration() is None
    accepted_head = gitws.head(root)

    assert runner.iteration() is None

    artifacts = [e for e in events_of(root, cfg, "artifact") if e.data["scope"] == "M001"]
    prompts = [e for e in artifacts if e.data["role"] == "holistic_prompt"]
    reports = [e for e in artifacts if e.data["role"] == "holistic_report"]
    assert len(prompts) == 1
    assert len(reports) == 1
    original_text = (root / prompts[0].data["path"]).read_text("utf-8")
    retry_text = (root / "local_state/holistic-1.md").read_text("utf-8")
    assert retry_text.startswith("Format repair only.")
    assert prompts[0].data["path"] in retry_text
    assert prompts[0].data["sha"] in retry_text
    assert (root / "local_state/holistic-0.md").read_text("utf-8") == original_text
    assert (root / "local_state/holistic-count.txt").read_text() == "2"
    attempts = [a for a in state(root, cfg, roadmap).attempts.values() if a["start"]["role"] == "holistic"]
    assert [a["start"]["retry"] for a in attempts] == ["initial", "format"]
    assert attempts[1]["start"]["parent"] == attempts[0]["start"]["invocation"]
    assert attempts[1]["start"]["contract"] == attempts[0]["start"]["contract"]
    assert state(root, cfg, roadmap).milestones_done == frozenset({"M001"})
    assert git(root, "log", "-1", "--format=%s").stdout.strip() == b"Close M001"
    assert git(root, "rev-parse", "HEAD^").stdout.decode().strip() == accepted_head
    done_event = events_of(root, cfg, "milestone_done")[0]
    paths = git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD")
    assert set(paths.stdout.decode().splitlines()) == {
        *(e.data["path"] for e in artifacts), cfg.ledger, done_event.data["commit_intent"]["manifest"]["path"]}
    for event in artifacts:
        committed = git(root, "show", f"HEAD:{event.data['path']}").stdout
        assert committed == (root / event.data["path"]).read_bytes()
        assert ledger.evidence_sha(committed) == event.data["sha"]
    committed_ledger = git(root, "show", f"HEAD:{cfg.ledger}").stdout
    assert committed_ledger == (root / cfg.ledger).read_bytes()
    done = json.loads(committed_ledger.splitlines()[-1])
    assert done["ev"] == "milestone_done"
    assert done["milestone"] == "M001"
    assert done["holistic_report"] == reports[0].data["path"]
    assert [row.path for row in gitws.status(root)] == ["05_governance/backlog.md"]
    from frutlups import findings
    assert findings.matches_backlog(root, ledger.read(root / cfg.ledger))
    conform(root, cfg, roadmap)



def test_holistic_format_retry_exhaustion_names_last_job_result(native_holistic_project):
    root, cfg, roadmap = native_holistic_project
    cfg = replace(cfg, commit_on_accept=True)
    plan(root, holistic=[{"text": "broken holistic report"}, {"text": "still broken holistic"}])
    runner = Controller(root, cfg, roadmap)
    for _ in range(5):
        assert runner.iteration() is None
    accepted_head = gitws.head(root)

    notices = []
    runner.emit = notices.append
    assert runner.iteration() == loop.StopReason.internal
    assert (root / "local_state/holistic-count.txt").read_text() == "2"
    retry_text = (root / "local_state/holistic-1.md").read_text("utf-8")
    assert retry_text.startswith("Format repair only.")
    assert notices[-1]["reason"] == "internal"
    assert "review format retry allowance exhausted" in notices[-1]["detail"]
    attempts = [a for a in state(root, cfg, roadmap).attempts.values() if a["start"]["role"] == "holistic"]
    assert len(attempts) == 2
    assert [a["start"]["retry"] for a in attempts] == ["initial", "format"]
    result_ref, output_ref = attempts[-1]["finish"]["evidence"]
    result = ledger.read_reference(root, ledger._thaw(result_ref))
    assert result["role"] == "holistic"
    assert result["output"] == dict(output_ref)
    assert (root / output_ref["path"]).read_text("utf-8") == "still broken holistic"
    assert result["invocation"] == attempts[-1]["start"]["invocation"]
    assert events_of(root, cfg, "milestone_done") == []
    assert events_of(root, cfg, "reopened") == []
    assert state(root, cfg, roadmap).milestones_done == frozenset()
    assert gitws.head(root) == accepted_head
    conform(root, cfg, roadmap)



def test_holistic_close_refuses_unrelated_staged_paths_before_done(native_holistic_project, monkeypatch):
    root, cfg, roadmap = native_holistic_project
    cfg = replace(cfg, commit_on_accept=True)
    runner = Controller(root, cfg, roadmap)
    for _ in range(5):
        assert runner.iteration() is None
    accepted_head = gitws.head(root)
    append = runner._review_append

    def stage_unrelated(ev, **data):
        append(ev, **data)
        if ev == "artifact" and data["role"] == "holistic_report":
            (root / "unrelated.txt").write_text("preserve staged work", encoding="utf-8")
            git(root, "add", "unrelated.txt")

    monkeypatch.setattr(runner, "_review_append", stage_unrelated)

    notices = []
    runner.emit = notices.append
    assert runner.iteration() == loop.StopReason.internal

    assert events_of(root, cfg, "milestone_done") == []
    assert gitws.head(root) == accepted_head
    assert "unrelated" in notices[-1]["detail"]
    assert "unrelated.txt" in notices[-1]["detail"]
    assert git(root, "show", ":unrelated.txt").stdout == b"preserve staged work"
    conform(root, cfg, roadmap)



def test_holistic_commit_failure_preserves_done_and_names_milestone(native_holistic_project, monkeypatch):
    root, cfg, roadmap = native_holistic_project
    cfg = replace(cfg, commit_on_accept=True)
    runner = Controller(root, cfg, roadmap)
    for _ in range(5):
        assert runner.iteration() is None
    accepted_head = gitws.head(root)

    original_git = gitws._git
    calls = []
    def fail_commit(root, *args, **kwargs):
        if args[0] == "commit":
            calls.append(args)
            assert args[args.index("-m") + 1].startswith("Close M001\n\nTemplate-Operation: ")
            assert kwargs["executable"] == gitws.selected_executable(cfg.git)
            assert len(events_of(root, cfg, "milestone_done")) == 1
            raise subprocess.CalledProcessError(1, ["git", "commit"])
        return original_git(root, *args, **kwargs)
    monkeypatch.setattr(gitws, "_git", fail_commit)
    notices = []
    runner.emit = notices.append
    assert runner.iteration() == loop.StopReason.internal
    assert len(calls) == 1
    assert gitws.head(root) == accepted_head
    assert state(root, cfg, roadmap).milestones_done == frozenset({"M001"})
    assert notices[-1]["detail"].startswith("M001: CalledProcessError:")
    staged = git(root, "diff", "--cached", "--name-only").stdout.decode().splitlines()
    artifacts = {e.data["path"] for e in events_of(root, cfg, "artifact") if e.data["scope"] == "M001"}
    done = events_of(root, cfg, "milestone_done")[0]
    assert set(staged) == artifacts | {cfg.ledger, done.data["commit_intent"]["manifest"]["path"]}
    assert '"ev":"milestone_done"' in git(root, "show", f":{cfg.ledger}").stdout.decode()
    assert all((root / path).is_file() for path in artifacts)
    from frutlups import commit
    assert commit.status(root, ledger.read(root / cfg.ledger), roadmap)[-1]["state"] == "pending"
    ledger.validate_history(root, ledger.read(root / cfg.ledger), roadmap)



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

    root, cfg, roadmap = grant_project((root, cfg, roadmap))
    result = run_transitions(root, cfg, roadmap, until)
    if until == "roadmap":
        assert result == loop.StopReason.boundary
        assert len(events_of(root, cfg, "accepted")) == 2
        next_grant(root, cfg, roadmap, second)
        result = run_transitions(root, cfg, roadmap, until)
    assert result == expected
    assert len(events_of(root, cfg, "accepted")) == accepted



def test_commit_on_accept_includes_event_and_exact_product_evidence(native_project):
    root, cfg, roadmap = native_project
    plan(root, coder=[{"writes": {"07_app/result.txt": "committed"}}])
    original = gitws.head(root)

    assert run_transitions(root, replace(cfg, commit_on_accept=True), roadmap) == loop.StopReason.done

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
        pytest.param("STOP", loop.StopReason.kill_switch, id="STOP-preflight_failed"),
        ("rounds", loop.StopReason.rounds_exhausted),
        ("blocked", loop.StopReason.blocked_verdict),
    ],
)
def test_budget_and_owner_stops(project, kind, expected, monkeypatch):
    root, cfg, roadmap = project
    if kind == "jobs":
        cfg = replace(cfg, max_jobs=1)
    if kind in ("rounds", "blocked"):
        cfg = replace(cfg, max_corrective_rounds=0)
        plan(
            root,
            reviewer=[{"verdict": "blocked" if kind == "blocked" else "needs_work", "rows": [ROW]}],
        )
    if kind not in ("jobs", "wall"):
        root, cfg, roadmap = grant_project((root, cfg, roadmap))
    notices = []
    runner = Controller(root, cfg, roadmap, emit=notices.append)
    if kind in ("jobs", "wall"):
        # D081 retains the historical node IDs as exact no-profile refusals.
        from frutlups import _preflight
        def forbidden(*a, **k):
            pytest.fail("probe, seat or job after profile-free refusal")
        monkeypatch.setattr(_preflight, "_probe", forbidden)
        monkeypatch.setattr(pi.PiSeat, "run", forbidden)
        monkeypatch.setattr(claude.ClaudeSeat, "run", forbidden)
        monkeypatch.setattr(loop.Loop, "_job", forbidden)
        before = gitws.workspace_witness(root, executable=cfg.git)
        authority = _preflight._authority_bytes(root)
        saved = (root / cfg.ledger).read_bytes()
        assert runner.iteration() == loop.StopReason.internal
        assert notices[-1]["reason"] == "internal"
        assert notices[-1]["detail"] == (
            "M001-S01: LedgerError: fresh dispatch requires an autonomy profile; owner must inspect")
        assert gitws.workspace_witness(root, executable=cfg.git) == before
        assert _preflight._authority_bytes(root) == authority
        assert (root / cfg.ledger).read_bytes() == saved
        assert events_of(root, cfg, "prompt") == []
        assert not state(root, cfg, roadmap).attempts
        assert runner.jobs == 0
        for path in ("local_state/frutlups/jobs", "local_state/coder-count.txt", "07_app/result.txt"):
            assert not (root / path).exists()
        conform(root, cfg, roadmap)
        return
    assert runner.iteration() is None
    if kind == "STOP":
        (root / "STOP").touch()
    result = None
    for _ in range(8):
        result = runner.iteration()
        if result:
            break
    assert result == expected
    assert notices[-1]["reason"] == expected.value
    assert events_of(root, cfg, "stop") == []
    assert events_of(root, cfg, "accepted") == []



@pytest.mark.parametrize("kind", ["jobs", "wall"])
def test_ordinary_local_guard_threshold_without_dispatch(project, monkeypatch, kind):
    from frutlups import _preflight
    root, cfg, roadmap = project
    assert roadmap.autonomy is None
    runner = Controller(root, cfg, roadmap)
    def forbidden(*a, **k):
        pytest.fail("local guard component test dispatched")
    monkeypatch.setattr(loop.Loop, "_iteration_owned", forbidden)
    monkeypatch.setattr(loop.Loop, "_job", forbidden)
    before = gitws.workspace_witness(root, executable=cfg.git)
    authority = _preflight._authority_bytes(root)
    saved = (root / cfg.ledger).read_bytes()
    if kind == "jobs":
        runner.jobs = cfg.max_jobs - 1
    else:
        runner.clock = lambda: runner.started + cfg.max_wall_minutes * 60 - 0.001
    assert runner._guard(job=kind == "jobs") is None
    if kind == "jobs":
        runner.jobs = cfg.max_jobs
    else:
        runner.clock = lambda: runner.started + cfg.max_wall_minutes * 60
    with pytest.raises(loop._Stop) as caught:
        runner._guard(job=kind == "jobs")
    assert caught.value.reason == loop.StopReason.budget_exhausted
    assert caught.value.detail == ("job budget reached; inspect and resume" if kind == "jobs"
                                   else "wall budget reached; inspect and resume")
    assert (root / cfg.ledger).read_bytes() == saved
    assert gitws.workspace_witness(root, executable=cfg.git) == before
    assert _preflight._authority_bytes(root) == authority
    assert not (root / "local_state/frutlups/jobs").exists()
    assert not state(root, cfg, roadmap).attempts
    assert events_of(root, cfg, "prompt") == []


@pytest.mark.parametrize("valid_second", [True, False])
def test_format_only_retry_is_bounded(native_project, valid_second):
    root, cfg, roadmap = native_project
    plan(root, reviewer=[{"text": "broken"}, {} if valid_second else {"text": "broken again"}])
    notices = []
    expected = loop.StopReason.done if valid_second else loop.StopReason.internal
    assert run_transitions(root, cfg, roadmap, emit=notices.append) == expected
    assert (root / "local_state/reviewer-count.txt").read_text() == "2"
    repair = (root / "local_state/reviewer-1.md").read_text("utf-8")
    assert repair.startswith("Format repair only.")
    assert "Do not repeat the review" in repair
    assert len(events_of(root, cfg, "prompt")) == 1
    attempts = [a for a in state(root, cfg, roadmap).attempts.values() if a["start"]["role"] == "reviewer"]
    assert [a["start"]["retry"] for a in attempts] == ["initial", "format"]
    assert attempts[1]["start"]["contract"] == attempts[0]["start"]["contract"]
    if not valid_second:
        assert "review format retry allowance exhausted" in notices[-1]["detail"]
        result_ref, output_ref = attempts[-1]["finish"]["evidence"]
        result = ledger.read_reference(root, ledger._thaw(result_ref))
        assert result["role"] == "reviewer"
        assert result["output"] == dict(output_ref)
        assert (root / output_ref["path"]).read_text("utf-8") == "broken again"
        assert events_of(root, cfg, "accepted") == []
    conform(root, cfg, roadmap)



def test_preflight_reports_multiple_failures_without_writes(project):
    root, cfg, roadmap = project
    (root / "STOP").touch()
    (root / "AGENTS.md").write_bytes(b"x" * 8193)
    cfg = replace(cfg, pi=str(root / "missing-pi"))
    before = (root / cfg.ledger).read_bytes()
    errors = probe_errors(root, cfg, roadmap)
    assert any("STOP" in e for e in errors)
    assert any("AGENTS.md" in e for e in errors)
    assert any("executable missing" in e for e in errors)
    assert (root / cfg.ledger).read_bytes() == before



@pytest.mark.parametrize("failure", ["exit", "launch", "streams"])
def test_preflight_authentication_failure_is_read_only(project, monkeypatch, failure):
    root, cfg, roadmap = project
    captured = []

    def probe(argv, cwd, env, timeout, stdout, stderr, stdin_bytes=None, *, stdout_consumer=None):
        assert "auth" not in argv
        assert timeout == 60
        code = "import sys; print('not authenticated'); sys.exit(1)"
        if failure == "streams":
            code = (
                "import os, sys; data=b'x'*(8*1024*1024+17); "
                "os.write(1,data); os.write(2,data); sys.exit(1)"
            )
        command = (str(root / "missing"),) if failure == "launch" else (sys.executable, "-c", code)
        result = run_process(command, cwd, env, timeout, stdout, stderr,
                             stdout_consumer=stdout_consumer)
        if failure == "streams":
            assert result.stdout_truncated is True
            assert result.stderr_truncated is True
            assert stdout.stat().st_size == STREAM_CAP
            assert stderr.stat().st_size == STREAM_CAP
        captured.append((argv, env, stdout, stderr))
        return result

    monkeypatch.setattr(pi, "run_process", probe)
    monkeypatch.setattr(claude, "run_process", probe)
    before = gitws.status(root)
    ledger_before = (root / cfg.ledger).read_bytes()

    assert probe_errors(root, cfg, roadmap)
    assert len(captured) == 2
    assert all(env["FRUTLUPS_SEAT"] == "reviewer" for _, env, _, _ in captured)
    assert all(stdout.exists() and stderr.exists() for _, _, stdout, stderr in captured)
    assert gitws.status(root) == before
    assert (root / cfg.ledger).read_bytes() == ledger_before



def test_empty_active_frontier_requires_specification(project):
    root, cfg, roadmap = project
    roadmap = replace(roadmap, milestones=(replace(roadmap.milestones[0], status="planned"),))
    assert run_transitions(root, cfg, roadmap) == loop.StopReason.needs_specification
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

    def probe(argv, cwd, env, timeout, stdout, stderr, stdin_bytes=None, *, stdout_consumer=None):
        assert timeout == 60
        pid_file = tmp_path / f"grandchild-{len(results)}.pid"
        result = run_process((str(launcher), str(pid_file)), cwd, env, 1, stdout, stderr,
                             stdout_consumer=stdout_consumer)
        assert result.timed_out is True
        assert result.exit is not None
        assert result.exception is None
        results.append((int(pid_file.read_text()), stdout, stderr))
        return result

    monkeypatch.setattr(pi, "run_process", probe)
    monkeypatch.setattr(claude, "run_process", probe)
    before = gitws.status(root)
    ledger_before = (root / cfg.ledger).read_bytes()
    started = time.monotonic()

    assert probe_errors(root, cfg, roadmap)
    assert time.monotonic() - started < 15
    assert len(results) == 2
    assert all(not _running(pid) for pid, _, _ in results)
    assert all(stdout.exists() and stderr.exists() for _, stdout, stderr in results)
    assert gitws.status(root) == before
    assert (root / cfg.ledger).read_bytes() == ledger_before



@pytest.mark.parametrize("failure", ["transport", "output", "timeout"])
@pytest.mark.parametrize("allowed", [True, False])
def test_failed_coder_edits_are_fenced_and_preserved_without_retry(native_project, failure, allowed):
    root, cfg, roadmap = native_project
    cfg = replace(cfg, timeouts=replace(cfg.timeouts, coder_seconds=1))
    path = "07_app/partial.txt" if allowed else "outside.txt"
    plan(root, coder=[{"failure": failure, "writes": {path: "partial evidence"}}, {}])
    expected = (loop.StopReason.seat_output if failure == "output" else loop.StopReason.seat_transport)
    if not allowed:
        expected = loop.StopReason.path_violation

    notices = []
    assert run_transitions(root, cfg, roadmap, emit=notices.append) == expected
    assert (root / path).read_text() == "partial evidence"
    assert (root / "local_state" / "coder-count.txt").read_text() == "1"
    assert events_of(root, cfg, "coded") == []
    assert len(events_of(root, cfg, "prompt")) == 1
    assert state(root, cfg, roadmap).slices["M001-S01"].round == 1
    assert state(root, cfg, roadmap).slices["M001-S01"].corrective_rounds_used == 0
    stop = notices[-1]
    assert stop["reason"] == expected
    assert path in stop["detail"]
    before = (root / cfg.ledger).read_bytes()
    assert run_transitions(root, cfg, roadmap) == loop.StopReason.preflight_failed
    assert (root / cfg.ledger).read_bytes() == before



@pytest.mark.parametrize("route", [("reviewer",), ("reviewer", "claude_reviewer")])
def test_saved_seat_text_scrubs_paths_without_truncating_reports(
    holistic_project, monkeypatch, route,
):
    root, cfg, roadmap = grant_project(canonical_project(holistic_project, ordinary_reviewers=route))
    cfg = replace(cfg, commit_on_accept=True)
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
        coder=[{"outcome_reason": notes}],
        reviewer=review_actions,
        holistic=[{"rows": [f"| M001-S01-H3 | P3 | carried | {paths} {tail} |"]}],
    )

    portable_root = root.with_name("portable-repo")
    shutil.copytree(root, portable_root)
    assert run_transitions(root, cfg, roadmap) == loop.StopReason.internal
    assert events_of(root, cfg, "coded") == []
    assert events_of(root, cfg, "accepted") == []
    result_files = list((root / "local_state/frutlups/jobs").glob("*/result.json"))
    assert len(result_files) == 1
    raw = json.loads(result_files[0].read_text("utf-8"))
    assert json.loads(raw["final_text"])["reason"] == notes
    # Native output preserves source identity: private text refuses publication.
    # Qualify long portable notes/reports independently with already safe inputs.
    root = portable_root
    plan(root, coder=[{"outcome_reason": "BEGIN-NOTES\n" + portable + "\n" + tail}],
         reviewer=[{"rows": [f"| M001-S01-F{index} | P3 | carried | {portable} {tail} |"]}
                   for index in range(len(route))],
         holistic=[{"rows": [f"| M001-S01-H3 | P3 | carried | {portable} {tail} |"]}])
    assert run_transitions(root, cfg, roadmap) == loop.StopReason.done
    current = state(root, cfg, roadmap).slices["M001-S01"]
    saved_notes = (root / current.notes_path).read_text(encoding="utf-8")
    assert json.loads(saved_notes)["reason"] == "BEGIN-NOTES\n" + portable + "\n" + tail
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




def test_loop_keeps_late_pi_semantics_and_distinct_claude_raw_evidence(native_project, monkeypatch):
    import hashlib
    from test_seats import _pi_terminal

    root, cfg, roadmap = native_project
    source = root / "local_state" / "late.jsonl"
    usage = {"input": 11, "cacheRead": 2, "cacheWrite": 3, "output": 7,
             "cost": {"total": 0.25}}
    with source.open("wb") as output:
        for _ in range(8193):
            output.write(b'{"padding":"' + b'x' * 1009 + b'"}\n')

    assert source.stat().st_size < 16 * 1024 * 1024
    original_argv = pi._argv
    calls = []
    def argv(job, config):
        if job.role.value != "coder":
            return original_argv(job, config)
        calls.append(job.id)
        contract = json.loads(re.findall(r"```json\n(.*?)\n```", job.prompt_path.read_text("utf-8"), re.S)[-1])
        contract.update(requirement="late streaming", reason="Late final notes.")
        with source.open("ab") as output:
            output.write(_pi_terminal(json.dumps(contract), usage))
        return (sys.executable, "-B", "-c",
                "import pathlib,shutil,sys; "
                "pathlib.Path('07_app/late.txt').write_text('implemented'); "
                "shutil.copyfileobj(open(sys.argv[1],'rb'),sys.stdout.buffer)", str(source))
    monkeypatch.setattr(pi, "_argv", argv)
    original_process = claude.run_process
    raw_hashes = {}
    def capture(*args, **kwargs):
        result = original_process(*args, **kwargs)
        raw_hashes[result.stdout_path] = hashlib.sha256(result.stdout_path.read_bytes()).hexdigest()
        return result
    monkeypatch.setattr(claude, "run_process", capture)

    assert run_transitions(root, cfg, roadmap) == loop.StopReason.done
    assert len(calls) == 1
    coded = events_of(root, cfg, "coded")
    assert len(coded) == 1
    assert "Late final notes." in (root / coded[0].data["notes_path"]).read_text()
    directories = list((root / "local_state/frutlups/jobs").iterdir())
    assert len(directories) == 2
    for directory in directories:
        normalized = directory / "result.json"
        result = json.loads(normalized.read_text())
        raw = Path(result["stdout_path"]) if result["role"] == "coder" else directory / "stdout.json"
        assert raw != normalized
        assert result["status"] == "completed"
        if result["role"] == "coder":
            assert json.loads(result["final_text"])["reason"] == "Late final notes."
            assert result["tokens_in"] == 16
            assert result["tokens_out"] == 7
            assert result["cost_usd"] == 0.25
            assert result["usage_completeness"] == "complete"
            assert result["stdout_truncated"] is True
            assert result["stderr_truncated"] is False
            assert Path(result["events_path"]) == raw
            assert raw.stat().st_size == STREAM_CAP
        else:
            assert raw.name == "stdout.json"
            assert hashlib.sha256(raw.read_bytes()).hexdigest() == raw_hashes[raw]
            assert json.loads(raw.read_bytes())["result"] == (root / result["output"]["path"]).read_text("utf-8")
            assert "events_path" not in result["result"]
    conform(root, cfg, roadmap)



@pytest.mark.parametrize("case", ["invalid-utf8", "oversized-record", "final-overflow", "missing-end"])
def test_loop_semantic_output_failures_never_replay_coder(native_project, monkeypatch, case):
    from test_seats import _pi_terminal

    root, cfg, roadmap = native_project
    data = {
        "invalid-utf8": b'\xff\n' + _pi_terminal(),
        "oversized-record": b'x' * (pi.RECORD_CAP + 1) + b'\n' + _pi_terminal(),
        "final-overflow": _pi_terminal("x" * (1024 * 1024 + 1)),
        "missing-end": _pi_terminal().split(b"\n")[0],
    }[case]
    source = root / "local_state" / "invalid.jsonl"
    source.write_bytes(data)
    original_argv = pi._argv
    calls = []
    def argv(job, config):
        if job.role.value != "coder":
            return original_argv(job, config)
        calls.append(job.id)
        return (sys.executable, "-B", "-c",
                "import pathlib,shutil,sys; "
                "pathlib.Path('07_app/partial.txt').write_text('preserved'); "
                "shutil.copyfileobj(open(sys.argv[1],'rb'),sys.stdout.buffer)", str(source))
    monkeypatch.setattr(pi, "_argv", argv)
    assert run_transitions(root, cfg, roadmap) == loop.StopReason.seat_output
    assert len(calls) == 1
    assert (root / "07_app/partial.txt").read_text() == "preserved"
    assert events_of(root, cfg, "coded") == []
    result = json.loads((root / "local_state/frutlups/jobs" / calls[0] / "result.json").read_text())
    assert result["status"] == "failed"
    assert result["failure_class"] == "output"
    assert result["diagnostic"]



@pytest.mark.parametrize(("role", "mutation"), [
    ("coder", "commit"), ("coder", "index"), ("coder", "flags"),
    ("reviewer", "commit"), ("reviewer", "index"), ("holistic", "commit"), ("holistic", "index"),
    ("reviewer", "dirty"), ("reviewer", "crlf"), ("holistic", "dirty"),
    ("coder", "forbidden-delete"), ("coder", "forbidden-type"),
    ("reviewer", "ledger-delete"),
])
def test_seat_witness_stops_drift_before_transition(native_holistic_project, monkeypatch, role, mutation):
    root, cfg, roadmap = native_holistic_project
    plan(root, coder=[{"writes": {"07_app/result.txt": "before\n"}}])
    runner = Controller(root, cfg, roadmap)
    for _ in range({"coder": 1, "reviewer": 3, "holistic": 5}[role]):
        assert runner.iteration() is None
    original_head = gitws.head(root)
    seat = pi.PiSeat if role == "coder" else claude.ClaudeSeat
    original = seat.run
    calls = []
    observed = []
    def tamper(self, job, config):
        result = original(self, job, config)
        if job.role.value != role:
            return result
        calls.append(job.id)
        target = root / "07_app/result.txt"
        if mutation == "commit":
            git(root, "add", "07_app/result.txt")
            git(root, "commit", "--allow-empty", "-qm", "owned seat-side drift")
        elif mutation == "index":
            git(root, "add", "07_app/result.txt")
        elif mutation == "flags":
            git(root, "update-index", "--assume-unchanged", "07_app/CONTEXT.md")
        elif mutation == "dirty":
            target.write_bytes(b"changed with same porcelain\n")
        elif mutation == "crlf":
            assert b"\r\n" in target.read_bytes()
            target.write_bytes(target.read_bytes().replace(b"\r\n", b"\n"))
        elif mutation == "forbidden-delete":
            (root / "AGENTS.md").unlink()
        elif mutation == "forbidden-type":
            (root / "AGENTS.md").unlink()
            (root / "AGENTS.md").mkdir()
        elif mutation == "ledger-delete":
            (root / cfg.ledger).unlink()
        observed.append(gitws.workspace_witness(root))
        return result
    monkeypatch.setattr(seat, "run", tamper)
    transition = {"coder": "coded", "reviewer": "reviewed", "holistic": "milestone_done"}[role]
    count = len(events_of(root, cfg, transition))
    assert runner.iteration() == loop.StopReason.path_violation
    assert len(calls) == 1
    after = gitws.workspace_witness(root)
    assert after["head"] == observed[0]["head"]
    assert after["index"] == observed[0]["index"]
    for path, identity in observed[0]["files"].items():
        if path != cfg.ledger:
            assert after["files"][path] == identity
    if mutation != "ledger-delete":
        assert len(events_of(root, cfg, transition)) == count
    if mutation == "commit":
        assert after["head"] != original_head



@pytest.mark.parametrize("mutation", ["content", "delete", "type"])
def test_coder_cannot_erase_or_modify_allowed_prior_baseline(native_project, monkeypatch, mutation):
    root, cfg, roadmap = native_project
    path = root / "07_app/owner.txt"
    path.write_bytes(b"owner baseline\n")
    result = subprocess.run([sys.executable, "-B", str(root / "scripts/prompt.py"),
                             "M001-S01", "--root", str(root), "--allow-dirty"],
                            cwd=root, capture_output=True, check=False)
    assert result.returncode == 0
    original = pi.PiSeat.run
    calls = []
    def tamper(self, job, config):
        result = original(self, job, config)
        calls.append(job.id)
        if mutation == "content":
            path.write_bytes(b"changed baseline\n")
        else:
            path.unlink()
            if mutation == "type":
                path.mkdir()
        return result
    monkeypatch.setattr(pi.PiSeat, "run", tamper)
    notices = []
    runner = Controller(root, cfg, roadmap, emit=notices.append)
    assert runner.iteration() == loop.StopReason.path_violation
    assert len(calls) == 1
    assert events_of(root, cfg, "coded") == []
    assert "prior-baseline" in notices[-1]["detail"]
    if mutation == "content":
        assert path.read_bytes() == b"changed baseline\n"
    elif mutation == "delete":
        assert not path.exists()
    else:
        assert path.is_dir()



def test_failed_coder_crlf_only_change_is_not_retried(native_project, monkeypatch):
    root, cfg, roadmap = native_project
    target = root / "07_app/CONTEXT.md"
    before = target.read_bytes()
    changed = before.replace(b"\r\n", b"\n") if b"\r\n" in before else before.replace(b"\n", b"\r\n")
    assert changed != before
    plan(root, coder=[{"failure": "transport"}, {}])
    original = pi.PiSeat.run
    calls = []
    def tamper(self, job, config):
        result = original(self, job, config)
        calls.append(job.id)
        target.write_bytes(changed)
        return result
    monkeypatch.setattr(pi.PiSeat, "run", tamper)
    runner = Controller(root, cfg, roadmap)
    assert runner.iteration() is None
    assert runner.iteration() == loop.StopReason.seat_transport
    assert len(calls) == 1
    assert target.read_bytes() == changed
    assert events_of(root, cfg, "coded") == []



def test_scrubbed_report_grammar_and_saved_summary_preserve_separators(native_project, monkeypatch):
    root, cfg, roadmap = native_project
    monkeypatch.setenv("FIXTURE_SECRET", "synthetic-report-secret")
    summary = "`/` / 1/2 MB/s `a` / `b`; file:///C:/Private Folder/x.py; https://example.invalid/?key=synthetic-report-secret"
    portable = "`/` / 1/2 MB/s `a` / `b`; <absolute-path>; https://example.invalid/?key=<redacted>"
    portable_root = root.with_name("portable-repo")
    shutil.copytree(root, portable_root)
    plan(root, reviewer=[{"rows": [f"| M001-S01-F1 | P3 | carried | {summary} |"]}])
    assert run_transitions(root, cfg, roadmap) == loop.StopReason.internal
    assert events_of(root, cfg, "reviewed") == []
    assert events_of(root, cfg, "accepted") == []
    assert "synthetic-report-secret" not in (root / cfg.ledger).read_text("utf-8")
    from frutlups import receipt
    assert receipt.scrub_text(summary, root, dict(os.environ)) == portable
    root = portable_root
    plan(root, reviewer=[{"rows": [f"| M001-S01-F1 | P3 | carried | {portable} |"]}])
    runner = Controller(root, cfg, roadmap)
    for _ in range(4):
        assert runner.iteration() is None
    current = state(root, cfg, roadmap).slices["M001-S01"]
    report = (root / current.last_report).read_text(encoding="utf-8")
    assert verdict.parse(report).findings[0].summary == portable
    attempt = next(a for a in state(root, cfg, roadmap).attempts.values()
                   if a["start"]["role"] == "reviewer")
    result, source = attempt["finish"]["evidence"]
    assert "synthetic-report-secret" not in (root / result["path"]).read_text("utf-8")
    assert portable in (root / source["path"]).read_text("utf-8")





def _preflight_result(job):
    from frutlups.seats import JobResult, Role

    assert job.role is Role.reviewer
    assert job.tools == (pi if job.adapter == "pi" else claude).tools_for(Role.reviewer)
    assert job.timeout == 60
    assert job.prompt_path.read_bytes() == b"Reply with OK only.\n"
    assert job.prompt_sha == ledger.evidence_sha(job.prompt_path.read_bytes())
    stdout = job.prompt_path.parent / "synthetic.stdout"
    stderr = job.prompt_path.parent / "synthetic.stderr"
    stdout.write_bytes(b"retained probe output\n")
    stderr.write_bytes(b"retained probe error\n")
    return JobResult("completed", None, "OK", 0, 0, None, None, None,
                     None, stdout, stderr, None)


@pytest.mark.parametrize(("entry", "mutation", "outcome"), [
    ("probe-pi", "tracked", "completed"),
    ("probe-claude", "tracked", "completed"),
    ("prepare", "dirty", "completed"),
    ("preflight", "crlf", "completed"),
    ("run", "create", "completed"),
    ("prepare", "delete", "completed"),
    ("run", "head", "completed"),
    ("run", "index", "completed"),
    ("prepare", "type", "completed"),
    ("prepare", "mode", "completed"),
    ("run", "ledger", "completed"),
    ("run", "tracked", "failed"),
    ("run", "tracked", "timed_out"),
    ("run", "tracked", "exception"),
])
def test_preflight_probe_integrity_preserves_edits_and_stops_later_calls(
    project, monkeypatch, entry, mutation, outcome,
):
    from frutlups import _preflight
    from frutlups.seats import FailureClass

    root, cfg, roadmap = project
    branches = []  # Checked outside the adapter's catch boundary.
    target = root / "07_app/CONTEXT.md"
    if mutation == "dirty":
        target.write_bytes(b"already dirty\n")
    if mutation == "delete":
        target = root / "nonignored.txt"
        target.write_bytes(b"nonignored before\n")
    original = target.read_bytes()
    if mutation in ("dirty", "delete"):
        # Preserve the raw-witness regression with an explicitly recorded dirty
        # baseline; an unrecorded edit now correctly refuses before any probe.
        issued = subprocess.run(
            [sys.executable, "-B", str(root / "scripts/prompt.py"),
             "M001-S01", "--root", str(root), "--allow-dirty"],
            cwd=root, capture_output=True, text=True, check=False)
        assert issued.returncode == 0, issued.stdout + issued.stderr
    ledger_before = (root / cfg.ledger).read_bytes()
    calls, retained = [], []
    before = gitws.workspace_witness(root)

    def probe(self, job, config):
        calls.append(job)
        result = _preflight_result(job)
        if len(calls) > 1:
            # A whole-prepare guard could allow this second probe to conceal edits.
            target.write_bytes(original)
            pytest.fail("later probe dispatched after integrity refusal")
        if mutation in ("tracked", "dirty"):
            target.write_bytes(b"changed probe bytes\n")
        elif mutation == "crlf":
            changed = original.replace(b"\r\n", b"\n") if b"\r\n" in original else original.replace(b"\n", b"\r\n")
            assert changed != original
            target.write_bytes(changed)
        elif mutation == "create":
            (root / "created.txt").write_bytes(b"created by probe\n")
        elif mutation == "delete":
            target.unlink()
        elif mutation == "head":
            git(root, "commit", "--allow-empty", "-qm", "owned probe drift")
        elif mutation == "index":
            git(root, "update-index", "--assume-unchanged", "07_app/CONTEXT.md")
        elif mutation == "type":
            target.unlink()
            target.mkdir()
        elif mutation == "mode":
            target.chmod(0o444)
            assert target.stat().st_mode != original_mode
        elif mutation == "ledger":
            (root / cfg.ledger).write_bytes(b"preserved invalid ledger edit\n")
        retained.append((gitws.workspace_witness(root), (root / ".git/index").read_bytes(), result))
        branches.append(outcome)
        if outcome == "exception":
            raise RuntimeError("synthetic adapter exception")
        if outcome != "completed":
            return replace(result, status=outcome, exit=1,
                           failure_class=FailureClass.timeout if outcome == "timed_out" else FailureClass.transport)
        return result

    original_mode = target.stat().st_mode
    monkeypatch.setattr(pi.PiSeat, "run", probe)
    monkeypatch.setattr(claude.ClaudeSeat, "run", probe)
    notices = []
    try:
        if entry.startswith("probe-"):
            name = "coder" if entry == "probe-pi" else "holistic"
            with pytest.raises(_preflight._ProbeIntegrityError) as raised:
                _preflight._probe(root, cfg, name)
            errors = (str(raised.value),)
        else:
            # Former public wrappers now refuse; exercise their native private
            # guard/probe sequence separately from the public refusal tests.
            errors = probe_errors(root, cfg, roadmap)
        assert len(calls) == 1
        assert branches == [outcome]
        category = {"head": "head", "index": "index"}.get(mutation, "files")
        integrity = [e for e in errors if "model probe integrity failed:" in e]
        assert len(integrity) == 1
        assert f"{calls[0].seat_name}: changed {category}; streams:" in integrity[0]
        assert gitws.workspace_witness(root) == retained[0][0]
        assert (root / ".git/index").read_bytes() == retained[0][1]
        assert gitws.workspace_witness(root) != before
        if mutation != "ledger":
            assert (root / cfg.ledger).read_bytes() == ledger_before
        else:
            assert (root / cfg.ledger).read_bytes() == b"preserved invalid ledger edit\n"
        result = retained[0][2]
        assert result.stdout_path.read_bytes() == b"retained probe output\n"
        assert result.stderr_path.read_bytes() == b"retained probe error\n"
        assert calls[0].prompt_path.read_bytes() == b"Reply with OK only.\n"
    finally:
        if mutation == "mode":
            target.chmod(original_mode)  # Only restore the owned scratch permission for teardown.



@pytest.mark.parametrize("phase", ["before", "after"])
@pytest.mark.parametrize("adapter", ["pi", "claude"])
def test_preflight_probe_unavailable_witness_refuses_and_retains_artifacts(project, monkeypatch, phase, adapter):
    from frutlups import _preflight

    root, cfg, roadmap = project
    if adapter == "claude":
        cfg = replace(cfg, seats={**cfg.seats, "coder": cfg.seats["holistic"]})
    calls, observations, completed = [], [], []
    original = gitws.workspace_witness
    ledger_before = (root / cfg.ledger).read_bytes()
    monkeypatch.setenv("FIXTURE_SECRET", "synthetic-witness-secret")
    # Fix only the owned probe ID so the before-refusal prompt has an exact known path.
    monkeypatch.setattr(_preflight, "uuid4", lambda: type("ProbeId", (), {"hex": "owned-witness-refusal"})())
    directory = root / "local_state/frutlups/jobs/probe-owned-witness-refusal"
    def witness(root, *, executable):
        observations.append(executable)
        if len(observations) == (1 if phase == "before" else 2):
            raise OSError(f"{root}/private synthetic-witness-secret " + "x" * 9000)
        return original(root, executable=executable)
    def probe(self, job, config):
        calls.append(job)
        result = _preflight_result(job)
        completed.append(job.id)
        return result
    monkeypatch.setattr(gitws, "workspace_witness", witness)
    monkeypatch.setattr(pi.PiSeat, "run", probe)
    monkeypatch.setattr(claude.ClaudeSeat, "run", probe)
    errors = probe_errors(root, cfg, roadmap)
    assert errors == (f"model probe integrity failed: coder: {phase} witness unavailable (OSError); "
                      "streams: local_state/frutlups/jobs/probe-owned-witness-refusal",)
    assert len(calls) == (0 if phase == "before" else 1)
    assert completed == [job.id for job in calls]  # No dispatch before; a completed stub after.
    assert observations == [cfg.git] * (1 if phase == "before" else 2)
    assert (directory / "prompt.md").read_bytes() == b"Reply with OK only.\n"
    if phase == "after":
        assert (directory / "synthetic.stdout").read_bytes() == b"retained probe output\n"
        assert (directory / "synthetic.stderr").read_bytes() == b"retained probe error\n"
    assert (root / cfg.ledger).read_bytes() == ledger_before
    assert "synthetic-witness-secret" not in str(errors)
    assert str(root) not in str(errors)
    assert len(str(errors).encode()) < 4096



@pytest.mark.parametrize("kind", ["file-type", "file-mode", "reparse-parent"])
def test_preflight_probe_identity_refusal_without_host_capability(project, monkeypatch, kind):
    from frutlups import _preflight

    root, cfg, roadmap = project
    original = gitws.workspace_witness
    before = original(root)
    observations, calls, completed = [], [], []
    def witness(root, *, executable):
        snapshot = original(root, executable=executable)
        observations.append(snapshot)
        if len(observations) == 2:
            snapshot = {**snapshot, "files": {**snapshot["files"], "07_app/CONTEXT.md": (kind, "synthetic changed identity")}}
        return snapshot
    def probe(self, job, config):
        calls.append(job)
        result = _preflight_result(job)
        completed.append(job.id)
        return result
    monkeypatch.setattr(gitws, "workspace_witness", witness)
    monkeypatch.setattr(pi.PiSeat, "run", probe)
    monkeypatch.setattr(claude.ClaudeSeat, "run", probe)
    assert probe_errors(root, cfg, roadmap)
    assert len(calls) == 1
    assert completed == [calls[0].id]
    assert len(observations) == 2
    assert original(root) == before
    assert calls[0].prompt_path.is_file()
    assert (calls[0].prompt_path.parent / "synthetic.stdout").is_file()



@pytest.mark.parametrize("outcome", ["completed", "auth", "capacity", "transport", "timeout", "exception"])
def test_preflight_probe_unchanged_results_keep_meaning_and_deduplication(project, monkeypatch, outcome):
    from frutlups import _preflight
    from frutlups.seats import FailureClass

    root, cfg, roadmap = project
    before = gitws.workspace_witness(root)
    calls, results, observations, branches = [], [], [], []
    original = gitws.workspace_witness
    def witness(root, *, executable):
        observations.append(len(calls))
        return original(root, executable=executable)
    def probe(self, job, config):
        calls.append(job)
        result = _preflight_result(job)
        results.append(result)
        branches.append(outcome)
        if outcome == "exception":
            raise RuntimeError("synthetic unchanged adapter exception")
        if outcome != "completed":
            result = replace(result, status="timed_out" if outcome == "timeout" else "failed",
                             failure_class=FailureClass(outcome), diagnostic="synthetic failure", exit=1)
            results[-1] = result
        return result
    monkeypatch.setattr(gitws, "workspace_witness", witness)
    monkeypatch.setattr(pi.PiSeat, "run", probe)
    monkeypatch.setattr(claude.ClaudeSeat, "run", probe)
    errors = probe_errors(root, cfg, roadmap)
    assert [job.seat_name for job in calls] == ["coder", "reviewer"]
    assert branches == [outcome, outcome]  # Checked outside the adapter's catch boundary.
    assert observations == [0, 1, 1, 2]  # A fresh pair around each individual call.
    assert original(root) == before
    if outcome == "completed":
        assert errors == ()
        assert all(not job.prompt_path.parent.exists() for job in calls)
    else:
        assert len(errors) == 2
        for job, result, error in zip(calls, results, errors):
            if outcome == "exception":
                assert error == f"model probe failed: {job.seat_name}: synthetic unchanged adapter exception"
            else:
                seat = cfg.seats[job.seat_name]
                assert error == (f"model probe failed: {job.seat_name}: adapter={seat.adapter} provider={seat.provider} "
                                 f"model={seat.model}; failure={outcome}; diagnostic: synthetic failure; "
                                 f"stderr: retained probe error ; streams: {job.prompt_path.parent.relative_to(root).as_posix()}")
            assert job.prompt_path.is_file()
            assert result.stdout_path.read_bytes() == b"retained probe output\n"
            assert result.stderr_path.read_bytes() == b"retained probe error\n"
