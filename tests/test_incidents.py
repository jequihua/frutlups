"""Offline M001-S01 witnesses: repaired staging/Pi success and no-change failure.

Only owned external scratch receives Git commits, installs, or generated evidence.
Run this module as a script only for its fresh-process loop driver.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tracemalloc
import zipfile
from dataclasses import replace
from pathlib import Path

import conftest
import pytest
import yaml

from frutlups import gitws, ledger, loop
from frutlups.procs import STREAM_CAP, run_process
from frutlups.roadmap import load
from frutlups.seats import FailureClass, pi
from test_loop import canonical_project, events_of, plan, project, state
from _native_support import grant_project, run_transitions


PACKAGE = Path(__file__).resolve().parents[1]
WORKSPACE = PACKAGE.parents[1]
SID = "M001-S01"


def command(root, *argv, ok=True):
    result = subprocess.run(
        argv, cwd=root, capture_output=True, timeout=90,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if ok:
        assert result.returncode == 0, (result.stdout + result.stderr).decode("utf-8", "replace")
    return result


def git(root, *args):
    return command(root, "git", "-C", str(root), *args).stdout


def initialize(root):
    for args in (
        ("init", "-q"), ("config", "user.name", "Incident fixture"),
        ("config", "user.email", "fixture@example.invalid"),
        ("config", "core.autocrlf", "false"),
        ("add", "."), ("commit", "-qm", "Owned offline baseline"),
    ):
        git(root, *args)


@pytest.fixture
def scratch():
    with tempfile.TemporaryDirectory(prefix="frutlups-incidents-") as name:
        root = Path(name).resolve()
        assert not root.is_relative_to(WORKSPACE)
        yield root


@pytest.fixture(autouse=True)
def measurement(request):
    started = time.perf_counter()
    tracemalloc.start()
    yield
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    # Wall time includes fixture setup/teardown after this fixture starts.
    print(f"incident measurement: {request.node.name}: "
          f"{time.perf_counter() - started:.3f}s; parent Python allocation peak={peak} bytes")


def test_windows_large_staging_exact_success(scratch, monkeypatch):
    (scratch / "baseline.txt").write_text("baseline\n", encoding="utf-8")
    initialize(scratch)
    paths = [f"p{i:03d}_" + "x" * 51 + ".txt" for i in range(700)]
    for path in paths:
        (scratch / path).write_bytes(b"owned\n")
    (scratch / "unrelated.txt").write_bytes(b"retain unrelated work\n")
    before = git(scratch, "rev-parse", "HEAD")
    captured = []
    original = gitws._git

    def observe(root, *args, **kwargs):
        if args[:2] == ("add", "-A"):
            captured.append((args, kwargs["input"]))
        return original(root, *args, **kwargs)

    monkeypatch.setattr(gitws, "_git", observe)
    committed = gitws.commit(scratch, paths, "Owned large-path witness")
    assert git(scratch, "rev-parse", "HEAD").decode().strip() == committed
    assert git(scratch, "rev-parse", "HEAD^") == before
    assert git(scratch, "diff", "--cached", "--name-only") == b""
    delta = git(scratch, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "HEAD")
    assert set(delta.decode().strip("\0").split("\0")) == set(paths)
    assert gitws.status(scratch) == (gitws.StatusEntry("unrelated.txt", "??"),)
    assert (scratch / "unrelated.txt").read_bytes() == b"retain unrelated work\n"
    assert len(captured) == 1
    assert captured[0][0] == ("add", "-A", "--pathspec-from-file=-", "--pathspec-file-nul")
    assert captured[0][1] == b"".join(path.encode() + b"\0" for path in paths)
    chars = len(subprocess.list2cmdline(["git", "-C", str(scratch), "add", "-A", "--", *paths]))
    assert chars >= 40000
    assert len(paths) == 700
    assert all((scratch / path).read_bytes() == b"owned\n" for path in paths)
    print(f"expanded staging: paths={len(paths)} command_characters={chars}")


def test_pi_terminal_after_cap_preserves_final_text_and_usage(scratch, monkeypatch):
    from test_seats import _config, _job

    terminal = (
        json.dumps({"type": "message_end", "message": {
            "role": "assistant", "content": [{"type": "text", "text": "terminal witness"}],
            "stopReason": "stop", "usage": {"input": 11, "cacheRead": 2,
            "cacheWrite": 3, "output": 7, "cost": {"total": 0.25}},
        }}).encode() + b'\n{"type":"agent_end"}\n{"type":"agent_settled"}\n'
    )
    prefix_line = b'{"padding":"' + b'x' * 1009 + b'"}\n'
    assert len(prefix_line) == 1024
    source = scratch / "synthetic.jsonl"
    with source.open("wb") as output:
        for _ in range(8192):
            output.write(prefix_line)
        output.write(terminal)
    assert source.stat().st_size <= 16 * 1024 * 1024
    monkeypatch.setattr(pi, "_argv", lambda job, cfg: (
        sys.executable, "-B", "-c",
        "import shutil,sys; shutil.copyfileobj(open(sys.argv[1],'rb'),sys.stdout.buffer)",
        str(source),
    ))
    result = pi.PiSeat().run(_job(scratch, "pi"), _config(scratch, ("SYSTEMROOT",)))
    assert result.exit == 0
    assert result.status == "completed"
    assert result.failure_class is None
    assert result.final_text == "terminal witness"
    assert result.tokens_in == 16
    assert result.tokens_out == 7
    assert result.cost_usd == 0.25
    assert result.usage_completeness == "complete"
    assert result.stdout_truncated is True
    assert result.stdout_path.stat().st_size == STREAM_CAP
    assert result.stderr_path.read_bytes() == b""
    print(f"Pi stream: emitted={source.stat().st_size} raw_retained={STREAM_CAP}")


def test_identical_no_change_verification_failures_characterization(project):
    root, cfg, roadmap = grant_project(canonical_project(project, verification_full=(
        sys.executable, "-c", "import sys; print('identical offline failure'); sys.exit(1)",
    )))
    cfg = replace(cfg, max_corrective_rounds=1)
    plan(root, coder=[{}, {}])
    assert run_transitions(root, cfg, roadmap) == loop.StopReason.verification_error
    coded = events_of(root, cfg, "coded")
    assert len(coded) == 2
    assert [event.round for event in coded] == [1, 2]
    assert [event.data["changed"] for event in coded] == [(), ()]
    verified = events_of(root, cfg, "verified")
    assert len(verified) == 2
    receipts = [json.loads((root / event.data["receipt"]).read_text()) for event in verified]
    for receipt in receipts:
        assert receipt["ok"] is False
        assert receipt["commands"][0]["exit"] == 1
        assert receipt["commands"][0]["stdout_tail"] == "identical offline failure" + os.linesep
    assert (root / "local_state/coder-count.txt").read_text() == "2"
    assert state(root, cfg, roadmap).slices[SID].corrective_rounds_used == 1
    assert events_of(root, cfg, "accepted") == []


def test_crlf_raw_git_evidence(scratch):
    payload = b"owned raw evidence\r\nsecond line\r\n"
    (scratch / ".gitattributes").write_text("*.raw -text\n", encoding="utf-8")
    (scratch / "evidence.raw").write_bytes(payload)
    initialize(scratch)
    stored = git(scratch, "show", "HEAD:evidence.raw")
    assert stored == payload
    assert hashlib.sha256(stored).hexdigest() == hashlib.sha256(payload).hexdigest()
    assert ledger.evidence_sha(payload) == hashlib.sha256(payload.replace(b"\r\n", b"\n")).hexdigest()
    assert ledger.evidence_sha(payload) != hashlib.sha256(payload).hexdigest()
    git(scratch, "archive", "--format=zip", "--output=raw.zip", "HEAD")
    with zipfile.ZipFile(scratch / "raw.zip") as archive:
        assert archive.read("evidence.raw") == payload


def test_fresh_process_loop_resume(project):
    root, cfg, roadmap = grant_project(project)
    plan(root, coder=[{"writes": {"07_app/result.txt": "fresh process"}}])
    for expected in ("coding", "verifying", "reviewing", "accept_pending", "accepted"):
        result = command(root, sys.executable, "-B", str(Path(__file__).resolve()), str(root))
        assert result.stdout.decode().strip().endswith(expected)
        assert state(root, cfg, roadmap).slices[SID].step == expected
    assert len(events_of(root, cfg, "prompt")) == 1
    assert len(events_of(root, cfg, "coded")) == 1
    assert len(events_of(root, cfg, "verified")) == 1
    assert len(events_of(root, cfg, "reviewed")) == 1
    assert len(events_of(root, cfg, "accepted")) == 1
    assert (root / "local_state/coder-count.txt").read_text() == "1"
    assert (root / "local_state/reviewer-count.txt").read_text() == "1"
    assert (root / "07_app/result.txt").read_text() == "fresh process"


def test_candidate_git_archive_install(scratch):
    inventory = json.loads((WORKSPACE / "02_analysis/source_import.json").read_text())
    paths = {row["path"] for row in inventory["files"]}
    assert len(paths) == 115
    paths |= {"tests/_native_support.py", "tests/test_incidents.py", "docs/baseline.md", "src/frutlups/_autonomy.py",
              "src/frutlups/runtime.py", "src/frutlups/ownership.py", "src/frutlups/_attempts.py",
              "src/frutlups/commit.py", "src/frutlups/findings.py", "src/frutlups/_historical.py",
              "src/frutlups/seats/pi_delegate/index.ts"}
    candidate = scratch / "candidate"
    candidate.mkdir()
    for relative in sorted(paths):
        destination = candidate / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PACKAGE / relative, destination)
    initialize(candidate)
    tracked = set(git(candidate, "ls-files", "-z").decode().strip("\0").split("\0"))
    assert tracked == paths
    restored = "tests/fixtures/v4_project/local_state/README.md"
    assert restored in tracked
    assert git(candidate, "status", "--porcelain") == b""
    archive = scratch / "candidate.zip"
    git(candidate, "archive", "--format=zip", f"--output={archive}", "HEAD")
    exported = scratch / "exported"
    with zipfile.ZipFile(archive) as zipped:
        assert {name for name in zipped.namelist() if not name.endswith("/")} == paths
        zipped.extractall(exported)
    installed = scratch / "installed"
    command(scratch, sys.executable, "-B", "-m", "pip", "install", "--no-index", "--no-deps",
            "--no-cache-dir", "--disable-pip-version-check", "--no-build-isolation",
            "--target", str(installed), str(exported))
    check = """
import hashlib, json, pathlib, sys
sys.path.insert(0, sys.argv.pop(1))
root = pathlib.Path(sys.argv.pop(1))
import frutlups
assert pathlib.Path(frutlups.__file__).is_relative_to(pathlib.Path(sys.path[0]))
manifest = json.loads((root / 'tests/fixtures/v4_project.manifest.json').read_text())
for name, expected in manifest['files'].items():
    data = (root / 'tests/fixtures/v4_project' / name).read_bytes()
    if b'\\0' not in data:
        data = data.replace(b'\\r\\n', b'\\n')
    assert hashlib.sha256(data).hexdigest() == expected, name
print('manifest files:', len(manifest['files']))
from importlib.metadata import version
assert version('frutlups') == '0.4.2'
from frutlups.cli import main
sys.argv = ['frutlups', '--help']
main()
"""
    result = command(scratch, sys.executable, "-I", "-B", "-c", check,
                     str(installed), str(exported), ok=False)
    # 0.4.2 treats top-level --help as usage error; preserve this CLI behavior.
    assert result.returncode == 2
    assert result.stdout.decode().splitlines() == [
        "manifest files: 54", "usage: frutlups {preflight,run,status}",
    ]
    assert result.stderr == b""
    # Exercise a subcommand parser in another isolated process. Configured live
    # preflight/run/status/resume qualification belongs to the integration gate.
    status_check = """
import pathlib, sys
sys.path.insert(0, sys.argv.pop(1))
import frutlups
assert pathlib.Path(frutlups.__file__).is_relative_to(pathlib.Path(sys.path[0]))
from importlib.metadata import entry_points
entries = list(entry_points(group='console_scripts', name='frutlups'))
assert len(entries) == 1
assert entries[0].value == 'frutlups.cli:main'
sys.argv = ['frutlups', 'status', '--help']
entries[0].load()()
"""
    status = command(scratch, sys.executable, "-I", "-B", "-c", status_check, str(installed))
    assert b"usage: frutlups status" in status.stdout
    assert b"--json" in status.stdout
    print(f"candidate tracked/archive files={len(paths)}; " + result.stdout.decode().splitlines()[0])


def test_current_manual_v2_fresh_processes(scratch, initialized_project):
    root = scratch / "manual"
    shutil.copytree(initialized_project, root)
    # Copy only the Git-listed runtime and prompt templates; do not inspect source.
    paths = git(WORKSPACE, "ls-files", "-z", "scripts", "prompts/templates").decode().strip("\0").split("\0")
    for relative in paths:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(WORKSPACE / relative, destination)
    data = yaml.safe_load((root / "roadmap.yaml").read_text())
    data["schema"] = "frutlups.roadmap/2"
    (root / "roadmap.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "Owned current manual runtime")

    def cli(script, *args):
        return command(root, sys.executable, "-B", f"scripts/{script}.py", *args)

    cli("roadmap", "check")
    cli("prompt", SID)
    (root / "07_app/result.txt").write_bytes(b"manual fresh process\r\n")
    cli("ledger", "coded", SID)
    cli("verify", SID)
    cli("prompt", SID, "--review")
    report = "05_governance/reviews/001_M001-S01_r1_review.md"
    (root / report).write_text(
        "# Review: M001-S01 round 1\n\n## Findings\n\n"
        "| id | severity | disposition | summary |\n| --- | --- | --- | --- |\n\n"
        "## Closure Decision\n\nObjective status: achieved\n"
        "Objective evidence: Owned offline fixture.\n\n"
        "## Verdict\n\nVerdict: pass - next: Accept M001-S01.\n", encoding="utf-8",
    )
    cli("ledger", "record", report)
    cli("ledger", "accept", SID)
    cli("ledger", "check")
    status = cli("ledger", "status")
    assert b"accepted" in status.stdout
    rows = [json.loads(line) for line in (root / "05_governance/ledger.jsonl").read_text().splitlines()]
    assert all(row["schema"] == "frutlups.ledger/2" for row in rows)
    for event in ("prompt", "coded", "verified", "reviewed", "accepted"):
        assert sum(row["ev"] == event for row in rows) == 1
    verified = next(row for row in rows if row["ev"] == "verified")
    receipt = json.loads((root / verified["receipt"]).read_text())
    assert receipt["schema"] == "frutlups.receipt/2"
    assert receipt["ok"] is True
    assert receipt["witness"]["stable"] is True
    print(f"current manual: runtime files copied={len(paths)}; lifecycle events=5")


if __name__ == "__main__":
    # Construct the shared offline config anew in every fresh process;
    # no Python object or in-memory checkpoint survives between processes.
    from frutlups.seats import claude

    root = Path(sys.argv[1]).resolve()
    assert not root.is_relative_to(WORKSPACE)
    cfg = conftest.offline_config()
    roadmap = load(root / "roadmap.yaml")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(pi, "_argv", lambda job, config: (
            sys.executable, str(PACKAGE / "tests/fake_pi.py"), str(job.prompt_path),
        ))
        patch.setattr(claude, "_argv", lambda job, config: (
            sys.executable, str(PACKAGE / "tests/fake_claude.py"),
        ))
        assert run_transitions(root, cfg, roadmap, once=True) is None
        print(state(root, cfg, roadmap).slices[SID].step)
