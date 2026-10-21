"""Representative private requalification on the final same-commit T export.

The accepted checker/history tests are reused unchanged through module-qualified
calls. A local ``checker_pin`` replaces the older 4632a891 tree only for the
fixtures imported here; there is no fallback to that or the development pin.
"""
import hashlib
import json
import os
import shutil
import sys
import tarfile
import time
from pathlib import Path

import pytest

import test_checker_v2 as checker_tests
import test_historical_deadlines_v2 as history_tests
from frutlups import _preflight, commit, gitws, ledger, loop, roadmap
# Fixtures only; importing no test function keeps the original nodes single.
from test_checker_v2 import checker_project, managed  # noqa: F401
from test_historical_deadlines_v2 import owned_history  # noqa: F401

FIXTURES = Path(__file__).parent / "fixtures"
FINAL = FIXTURES / "v5_final"
FINAL_COMMIT = "711bce067eb0b3d05abf8cdec54f3cc812f62a5d"
MANIFEST_SHA = "c533c82f3e3d66f35ed6140d76554adbee0a9c2eb1cad55e2e5c2c292acf9c0c"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture(scope="session")
def checker_pin(tmp_path_factory):
    raw = (FIXTURES / "v5_final.manifest.json").read_bytes()
    assert sha(raw) == MANIFEST_SHA
    manifest = json.loads(raw)
    assert manifest["commit"] == FINAL_COMMIT
    archive = FINAL / "template-5.0.0.tar"
    raw = archive.read_bytes()
    assert len(raw) == manifest["archive"]["bytes"] == 460800
    assert sha(raw) == manifest["archive"]["raw_sha256"] == "a8d3764d87804e0ae9b880f02a4f53592e553dc53c2d24d51b22d6a7bf4ec04a"
    expected = {row["path"]: row for row in manifest["members"]}
    assert len(expected) == 69
    files = {}
    with tarfile.open(archive) as source:
        assert source.pax_headers == {"comment": FINAL_COMMIT}
        for member in source.getmembers():
            assert not member.issym() and not member.islnk(), member.name
            if member.isdir():
                continue
            assert member.isfile() and member.mode == 0o664, member.name
            files[member.name] = source.extractfile(member).read()
    assert files.keys() == expected.keys()
    root = tmp_path_factory.mktemp("final-pin")
    for name, data in files.items():
        assert (len(data), sha(data)) == (expected[name]["bytes"], expected[name]["raw_sha256"]), name
        # The tracked materialization is the same verified export.
        assert (FINAL / "project" / name).read_bytes() == data, name
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    assert not (root / "tests").exists()
    return root


def assert_final_project(root):
    """Bind an owned project copy to the final export, not an older pin."""
    manifest = json.loads((FIXTURES / "v5_final.manifest.json").read_bytes())
    for row in manifest["members"]:
        if row["path"].startswith("scripts/"):
            assert sha((root / row["path"]).read_bytes()) == row["raw_sha256"], row["path"]
    assert b'version = "5.0.0"' in (root / "pyproject.toml").read_bytes()
    assert not (root / "tests").exists()


def test_final_checker_read_and_profile_free_dispatch_refusal(checker_project, monkeypatch):
    # Story 1: selected Python/configured Git checker under ownership with a
    # finite deadline on the canonical root/ledger; no-profile dispatch refuses
    # with no job, provider or workspace effect.
    root, cfg, model = checker_project
    assert_final_project(root)
    checker_tests.test_real_checker_unprofiled_read_and_fresh_dispatch_refusal(checker_project, monkeypatch)
    assert not (root / "local_state/frutlups").exists()
    assert ledger.read(root / cfg.ledger) == ()


def test_final_configured_git_shadow_and_both_template_routes(checker_pin, tmp_path):
    # Story 2a: actual configured Git succeeds through both T helpers despite an
    # invalid application-directory shadow; absent selection reaches the shadow.
    assert_final_project(checker_pin)
    checker_tests.test_owned_application_directory_and_both_real_git_routes(checker_pin, tmp_path)


@pytest.mark.parametrize("supplied", ["relative", "missing", "directory", "shell"])
def test_final_invalid_supplied_git_refuses_without_path_fallback(checker_pin, tmp_path, supplied):
    # Story 2b: a usable PATH Git exists, so any fallback would succeed visibly.
    git = Path(gitws.selected_executable(shutil.which("git")))
    shell = tmp_path / "git.cmd"
    shell.write_bytes(b"@echo off\r\nexit 0\r\n")
    value = {"relative": "git", "missing": str(tmp_path / "missing" / "git.exe"),
             "directory": str(tmp_path), "shell": str(shell)}[supplied]
    code = r"""
import sys
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root/'scripts'))
import _common,_git
helper=_common.git if sys.argv[2]=='common' else _git.run
helper(root,'--version')
print('helper used Git')
"""
    env = gitws.controlled_environment(os.environ)
    env["PATH"] = str(git.parent)
    for helper in ("common", "bounded"):
        control = managed([sys.executable, "-B", "-c", code, str(checker_pin), helper],
                          checker_pin, env, tmp_path / ("control-" + helper))
        assert control.exit == 0, control.stderr_path.read_text()
        assert control.stdout_path.read_text().strip() == "helper used Git"
        refused = managed([sys.executable, "-B", "-c", code, str(checker_pin), helper],
                          checker_pin, {**env, "TEMPLATE_GIT_EXECUTABLE": value}, tmp_path / helper)
        assert refused.exit != 0
        assert refused.stdout_path.read_text() == ""
        assert "TEMPLATE_GIT_EXECUTABLE must name an existing absolute executable file" in refused.stderr_path.read_text()
    with pytest.raises(ValueError, match="^configured Git must name an existing absolute executable file$"):
        gitws.selected_executable(value)


def test_final_historical_selection_absent_valid_and_invalid(owned_history, monkeypatch):
    # Story 3: invalid selection has no effects; absent stays strict; the valid
    # selection is consumed once and the final T checker accepts the unchanged
    # emitted artifacts afterwards.
    root, cfg, model, pair, _ = owned_history
    assert_final_project(root)
    saved = (root / cfg.ledger).read_bytes()
    before = gitws.workspace_witness(root, executable=cfg.git)
    invalid = (pair[0], "0" * 64)
    with monkeypatch.context() as patch:
        history_tests.no_effects(patch)
        notices = []
        obj = history_tests.controller(root, cfg, model, emit=notices.append)
        assert obj._iteration_owned(historical_assessment=invalid) == loop.StopReason.internal
        assert notices[-1]["detail"] == "ValueError: historical assessment: raw hash mismatch; owner must inspect"
        with pytest.raises(ValueError, match="^historical assessment: raw hash mismatch$"):
            with _preflight._owned_admission(root, cfg, model, deadline=time.monotonic() + 60,
                                            historical_assessment=invalid):
                pytest.fail("invalid selection admitted")
    assert gitws.workspace_witness(root, executable=cfg.git) == before
    assert (root / cfg.ledger).read_bytes() == saved
    history_tests.test_real_owned_holistic_strict_selected_and_t_consumer(owned_history, monkeypatch)


def test_final_pending_recovery_and_exact_replay(checker_project, monkeypatch):
    # Story 4: genuine pending approved commit; invalid selection refuses first,
    # the valid one checks before one recovery, and replay changes nothing.
    root, cfg, model = checker_project
    assert_final_project(root)
    history_tests.test_pending_recovery_invalid_selected_then_valid_exact_recovery(checker_project, monkeypatch)
    # The reused materialization rewrites the owned roadmap; read it back.
    model = roadmap.load(root / "roadmap.yaml")
    events = ledger.read(root / cfg.ledger)
    status = commit.status(root, events, model, executable=cfg.git)
    assert status[-1]["state"] == "completed"
    assert all(row["state"] != "pending" for row in status)
    state = ledger.validate_history(root, events, model, executable=cfg.git)
    assert not state.attempts and not state.runs
    head = gitws.head(root, executable=cfg.git)
    subjects = gitws._git(root, "log", "--format=%s", executable=cfg.git).stdout.decode().splitlines()
    # Exactly one recovered commit sits on the adopted history; none duplicated.
    assert subjects[:2] == ["Owned close", "owned adopted history"]
    assert subjects.count("Owned close") == 1
    prefix = (root / cfg.ledger).read_bytes()
    with _preflight._owned_admission(root, cfg, model, deadline=time.monotonic() + 60,
                                    historical_assessment=None):
        pass
    assert gitws.head(root, executable=cfg.git) == head
    assert (root / cfg.ledger).read_bytes() == prefix
    assert not (root / "local_state/frutlups/jobs").exists()
