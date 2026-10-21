"""Exact projected-backlog admission must retain the fresh workspace guard."""
import json
import platform
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import test_loop
from test_attempts_v2 import internal_loop
from test_runtime_v2 import bound_case
from frutlups import _preflight, findings, gitws, ledger, loop
from frutlups.ownership import writer_lock
from frutlups.seats import claude, pi

project = test_loop.project
BACKLOG = "05_governance/backlog.md"


@pytest.fixture
def profile(project, monkeypatch):
    root, cfg, _ = project
    model, rows, write = bound_case(root, "admission-is-the-only-initial-allowance")
    write(cfg.ledger, b"\n".join(json.dumps(row).encode() for row in rows) + b"\n")
    test_loop.git(root, "add", ".")
    test_loop.git(root, "commit", "-qm", "owned profile admission")
    model = replace(model, runtime={"python": platform.python_version()},
                    verification_full=(sys.executable, "-c", "pass"))
    events = ledger.read(root / cfg.ledger)
    with writer_lock(root, model, executable=cfg.git):
        findings.reconcile(root, events)
    assert [row["path"] for row in _preflight.evidence.changes(root, cfg)] == [BACKLOG]
    calls = []

    def forbidden(*args, **kwargs):
        calls.append("dispatch")
        pytest.fail("admission must not dispatch a probe or provider")

    monkeypatch.setattr(_preflight, "_probe", forbidden)
    monkeypatch.setattr(pi.PiSeat, "run", forbidden)
    monkeypatch.setattr(claude.ClaudeSeat, "run", forbidden)
    return root, cfg, model, events, calls


def test_clean_exact_backlog_runs_final_real_git_guard(profile, monkeypatch):
    root, cfg, model, events, calls = profile
    checked = []
    original = gitws.is_clean

    def observe(root, ignore_prefixes=("local_state/",), *, executable="git"):
        result = original(root, ignore_prefixes, executable=executable)
        checked.append((ignore_prefixes, executable, result))
        return result

    monkeypatch.setattr(gitws, "is_clean", observe)
    with writer_lock(root, model, executable=cfg.git):
        internal_loop(root, cfg, model)._clean()
    assert checked == [(("local_state/", BACKLOG), cfg.git, True)], "final real Git guard was skipped"
    assert calls == []


def saved_state(root, cfg, events):
    names = [cfg.ledger, ".git/index", BACKLOG, "local_state/plan.json"]
    names.extend(ref["path"] for event in events for ref in event.data.get("authority", ()))
    return {name: (root / name).read_bytes() if (root / name).exists() else None for name in names}


@pytest.mark.parametrize("entry", ["shared", "clean"])
def test_new_real_git_path_between_observations_refuses(profile, monkeypatch, entry):
    root, cfg, model, events, calls = profile
    obj = internal_loop(root, cfg, model)
    before = saved_state(root, cfg, events)
    unrelated = root / "07_app/arrived-after-observation.txt"
    original_changes, original_clean = _preflight.evidence.changes, gitws.is_clean
    observations, checked = [], []

    def changes(root, cfg):
        current = original_changes(root, cfg)
        observations.append(current)
        assert [row["path"] for row in current] == [BACKLOG]
        assert not unrelated.exists()
        unrelated.write_bytes(b"preserve newly arrived unrelated bytes\n")
        return current

    def clean(root, ignore_prefixes=("local_state/",), *, executable="git"):
        result = original_clean(root, ignore_prefixes, executable=executable)
        checked.append((ignore_prefixes, executable, result))
        return result

    monkeypatch.setattr(_preflight.evidence, "changes", changes)
    monkeypatch.setattr(gitws, "is_clean", clean)
    with writer_lock(root, model, executable=cfg.git):
        if entry == "shared":
            assert _preflight._profile_workspace_errors(root, cfg, events) == ["workspace changed during admission"]
        else:
            with pytest.raises(loop._Stop) as caught:
                obj._clean()
            assert caught.value.reason == loop.StopReason.path_violation
            assert caught.value.detail == "workspace changed during admission; owner must inspect"
    assert len(observations) == 1
    assert checked == [(("local_state/", BACKLOG), cfg.git, False)]
    assert unrelated.read_bytes() == b"preserve newly arrived unrelated bytes\n"
    assert saved_state(root, cfg, events) == before
    assert calls == []
    assert not (root / "local_state/frutlups/jobs").exists()


@pytest.mark.parametrize("case", ["mismatched", "malformed", "missing", "unsafe", "unrelated", "neighbor"])
def test_only_exact_safe_projection_is_admitted(profile, monkeypatch, case):
    root, cfg, model, events, calls = profile
    target = root / BACKLOG
    if case == "mismatched":
        target.write_text(target.read_text("utf-8").replace(findings.END, "extra derived row\n" + findings.END),
                          encoding="utf-8")
    elif case == "malformed":
        target.write_text(findings.BEGIN, encoding="utf-8")
    elif case == "missing":
        target.unlink()
    elif case == "unsafe":
        original = Path.is_symlink
        monkeypatch.setattr(Path, "is_symlink", lambda path: path == target or original(path))
    elif case in ("unrelated", "neighbor"):
        target = root / ("07_app/unrelated.txt" if case == "unrelated" else BACKLOG + ".extra")
        target.write_bytes(b"unrecorded neighbor or product\n")
    before = saved_state(root, cfg, events)
    target_bytes = target.read_bytes() if target.exists() else None
    checked = []
    original_clean = gitws.is_clean

    def observe(*args, **kwargs):
        checked.append(True)
        return original_clean(*args, **kwargs)

    monkeypatch.setattr(gitws, "is_clean", observe)
    with writer_lock(root, model, executable=cfg.git):
        obj = internal_loop(root, cfg, model)
        if case in ("malformed", "missing", "unsafe"):
            for check in (lambda: _preflight._profile_workspace_errors(root, cfg, events), obj._clean):
                with pytest.raises(ValueError):
                    check()
        else:
            error = "unrecorded change: " + target.relative_to(root).as_posix()
            assert _preflight._profile_workspace_errors(root, cfg, events) == [error]
            with pytest.raises(loop._Stop) as caught:
                obj._clean()
            assert caught.value.reason == loop.StopReason.path_violation
            assert caught.value.detail == error + "; owner must inspect"
    assert checked == [], "existing errors must not enter the final guard"
    assert saved_state(root, cfg, events) == before
    assert (target.read_bytes() if target.exists() else None) == target_bytes
    assert calls == []


def test_static_and_clean_share_projection_policy_without_public_enablement(profile, monkeypatch):
    root, cfg, model, events, calls = profile
    backlog = root / BACKLOG
    # Existing projection semantics intentionally preserve prose outside markers.
    backlog.write_text("Owner prose\n" + backlog.read_text("utf-8") + "Tail\n", encoding="utf-8")
    before = saved_state(root, cfg, events)
    checked = []
    original_clean = gitws.is_clean

    def observe(root, ignore_prefixes=("local_state/",), *, executable="git"):
        result = original_clean(root, ignore_prefixes, executable=executable)
        checked.append((ignore_prefixes, executable, result))
        return result

    monkeypatch.setattr(gitws, "is_clean", observe)
    with writer_lock(root, model, executable=cfg.git):
        selected, probes, errors = _preflight._static(root, cfg, model)
        assert errors == ()
        assert selected.identity["python"] == platform.python_version()
        assert probes == ["coder", "reviewer"]
        internal_loop(root, cfg, model)._clean()
    assert len(checked) == 2
    assert checked[0] == (("local_state/", BACKLOG), gitws.selected_executable(cfg.git), True)
    assert checked[1] == (("local_state/", BACKLOG), cfg.git, True)
    assert Path(checked[0][1]).samefile(checked[1][1])
    # Public admission refuses dataclass-only authority before any probe.
    refusal = "admission roadmap differs from canonical authority"
    assert _preflight.prepare(root, cfg, model)[2] == (refusal,)
    assert loop.Loop(root, cfg, model, emit=lambda _: None).iteration() == loop.StopReason.preflight_failed
    assert saved_state(root, cfg, events) == before
    assert calls == []
    assert not (root / "local_state/frutlups/jobs").exists()
