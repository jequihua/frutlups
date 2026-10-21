"""Owned checker integration against the immutable accepted development tree."""
import hashlib
import json
import os
import shutil
import sys
import tarfile
from pathlib import Path, PurePosixPath

import pytest

from frutlups import gitws, procs

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def checker_pin(tmp_path_factory):
    raw = (FIXTURES / "template_4632a891.manifest.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "a0904699ea28bcc20c0b1bb3c35060134807d5e4637325691a8e5fea2cef2cd4"
    manifest = json.loads(raw)
    archive = FIXTURES / "template_4632a891.tar"
    raw = archive.read_bytes()
    assert manifest["archive"]["path"] == archive.name
    assert len(raw) == manifest["archive"]["bytes"] == 1187840
    assert hashlib.sha256(raw).hexdigest() == manifest["archive"]["raw_sha256"] == "54ebc87eab66a6b079610ded073c6665b9ef945e49a266f4c41fbb45a135544b"
    expected = {row["path"]: row for row in manifest["members"]}
    assert len(expected) == len(manifest["members"]) == 83
    with tarfile.open(archive) as source:
        members = source.getmembers()
        assert len(members) == len({m.name for m in members}) == 83
        assert {m.name for m in members} == set(expected)
        for member in members:
            path = PurePosixPath(member.name)
            assert member.isfile() and member.mode == 0o644
            assert expected[member.name]["mode"] == "100644"
            assert not path.is_absolute() and not {"..", "."} & set(path.parts)
            assert "\\" not in member.name and ":" not in member.name
        files = {member.name: source.extractfile(member).read() for member in members}
        for name, data in files.items():
            assert len(data) == expected[name]["bytes"]
            assert hashlib.sha256(data).hexdigest() == expected[name]["raw_sha256"]
    root = tmp_path_factory.mktemp("checker-pin")
    for name, data in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return root


@pytest.mark.parametrize("value", ["", "git", "relative/git.exe", '"C:/git.exe"',
    "C:/missing-checker-git.exe", "bad\0name", "~/git.exe", "$GIT", "git.exe --version"])
def test_explicit_selection_refuses_without_launch(value, monkeypatch):
    monkeypatch.setattr(gitws, "run_process", lambda *a, **k: pytest.fail("invalid selection launched"))
    with pytest.raises(ValueError, match="^configured Git must name an existing absolute executable file$"):
        gitws.selected_executable(value)


@pytest.mark.parametrize("suffix", [".cmd", ".bat", ".ps1", ""])
def test_windows_shell_selection_is_not_an_executable(tmp_path, suffix):
    target = tmp_path / ("selected" + suffix)
    target.write_bytes(b"fixture")
    if os.name == "nt":
        with pytest.raises(ValueError, match="configured Git must name"):
            gitws.selected_executable(str(target))
    else:
        target.chmod(0o600)
        with pytest.raises(ValueError, match="configured Git must name"):
            gitws.selected_executable(str(target))


def owned_python(root, project=None):
    """Copy only named runtime binaries; use the installed read-only library paths."""
    base = Path(sys._base_executable).parent
    root.mkdir()
    for name in ("python.exe", "python314.dll", "python3.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        source = base / name
        if source.is_file():
            shutil.copyfile(source, root / name)
    (root / "python314._pth").write_text("\n".join([
        str(base / "Lib"), str(base / "DLLs"), str(Path(sys.prefix) / "Lib/site-packages"),
        *([str(project / "scripts")] if project else []), "import site", ""]), encoding="utf-8")
    return root / "python.exe"


def managed(argv, root, env, output):
    result = procs.run_process(argv, root, env, 30, output / "stdout", output / "stderr")
    assert not result.timed_out
    assert result.exception is None
    assert result.exit is not None
    assert not result.stdout_truncated
    assert not result.stderr_truncated
    return result


def test_owned_application_directory_and_both_real_git_routes(checker_pin, tmp_path):
    assert os.name == "nt", "this owned runtime proof is admitted for Windows CPython 3.14.3"
    executable = owned_python(tmp_path / "owned python")
    # An invalid image in the actual application's directory makes fallback observable.
    (executable.parent / "git.exe").write_bytes(b"not a Windows executable")
    selected = gitws.selected_executable(shutil.which("git"))
    code = r"""
import ctypes,json,sys,platform,os
from pathlib import Path
import yaml
root=Path(sys.argv[1]);sys.path.insert(0,str(root/'scripts'))
import _common,_git
buf=ctypes.create_unicode_buffer(32768)
assert ctypes.windll.kernel32.GetModuleFileNameW(None,buf,len(buf))
print(json.dumps(dict(module=buf.value,python=platform.python_version(),implementation=platform.python_implementation(),yaml=yaml.__version__,cwd=os.getcwd())))
if len(sys.argv)>2:
 helper=_common.git if sys.argv[2]=='common' else _git.run
 helper(root,'--version')
 raise SystemExit(0)
a=_common.git(root,'--version')
b=_git.run(root,'--version')
assert a.returncode==b.returncode==0
print('both selected helpers passed')
"""
    env = gitws.controlled_environment(os.environ)
    env["TEMPLATE_GIT_EXECUTABLE"] = selected
    result = managed([str(executable), "-B", "-c", code, str(checker_pin)], checker_pin, env, tmp_path / "selected")
    assert result.exit == 0, result.stderr_path.read_text()
    lines = result.stdout_path.read_text().splitlines()
    identity = json.loads(lines[0])
    assert Path(identity["module"]) == executable
    assert identity["python"] == "3.14.3"
    assert identity["implementation"] == "CPython"
    assert identity["yaml"] == "6.0.3"
    assert Path(identity["cwd"]) == checker_pin
    assert lines[-1] == "both selected helpers passed"
    del env["TEMPLATE_GIT_EXECUTABLE"]
    for helper in ("common", "bounded"):
        refused = managed([str(executable), "-B", "-c", code, str(checker_pin), helper], checker_pin, env, tmp_path / helper)
        assert refused.exit != 0
        assert 'both selected helpers passed' not in refused.stdout_path.read_text()


@pytest.fixture
def checker_project(checker_pin, tmp_path):
    from dataclasses import replace
    import conftest
    import yaml
    from frutlups import roadmap
    root = tmp_path / "project with spaces"
    shutil.copytree(checker_pin, root)
    cfg = replace(conftest.offline_config(), timeouts=conftest.Timeouts(30, 30, 30),
                  git=gitws.selected_executable(shutil.which("git")))
    data = yaml.safe_load((root / "roadmap.yaml").read_text("utf-8"))
    data["verification"]["full"] = ["python", "-c", "print('verified')"]
    data["milestones"][0]["holistic_review"] = False
    (root / "roadmap.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    with (root / ".gitignore").open("a", encoding="utf-8") as stream:
        stream.write("\nfrutlups.local.toml\nproject.local.toml\n")
    main = []
    for name in ("schema", "until", "max_corrective_rounds", "max_jobs", "max_wall_minutes",
                 "commit_on_accept", "prompt_dir", "review_prompt_dir", "reviews_dir", "ledger"):
        main.append(name + " = " + json.dumps(getattr(cfg, name)))
    main.append("[timeouts]")
    for name in ("coder_seconds", "reviewer_seconds", "verification_seconds"):
        main.append(name + " = " + str(getattr(cfg.timeouts, name)))
    for name, seat in cfg.seats.items():
        main.append("[seats." + name + "]")
        for key in ("adapter", "model", "effort", "provider", "corrective_effort"):
            if getattr(seat, key) is not None:
                main.append(key + " = " + json.dumps(getattr(seat, key)))
    (root / "frutlups.toml").write_text("\n".join(main), encoding="utf-8")
    local = ['schema = "frutlups.local/1"']
    for name in ("pi", "claude", "git", "path_dirs", "env_passthrough"):
        local.append(name + " = " + json.dumps(getattr(cfg, name)))
    (root / "frutlups.local.toml").write_text("\n".join(local), encoding="utf-8")
    for args in (("init", "-q"), ("config", "user.name", "Owned"),
                 ("config", "user.email", "owned@example.invalid"),
                 ("config", "core.autocrlf", "false"), ("add", "."), ("commit", "-qm", "checker fixture")):
        gitws._git(root, *args, executable=cfg.git)
    return root, cfg, roadmap.load(root / "roadmap.yaml")


def admission(project):
    import time
    from frutlups import _preflight
    root, cfg, model = project
    return _preflight._owned_admission(root, cfg, model, deadline=time.monotonic() + 60)


def test_real_checker_unprofiled_read_and_fresh_dispatch_refusal(checker_project, monkeypatch):
    from _native_support import Controller
    from frutlups import _preflight, loop
    root, cfg, model = checker_project
    assert model.autonomy is None
    before = gitws.workspace_witness(root, executable=cfg.git)
    calls = []
    original = _preflight.procs.run_process
    def forwarded(argv, cwd, env, timeout, *args, **kwargs):
        if "scripts/ledger.py" in argv:
            calls.append((list(argv), cwd, dict(env), timeout))
        return original(argv, cwd, env, timeout, *args, **kwargs)
    monkeypatch.setattr(_preflight.procs, "run_process", forwarded)
    with admission(checker_project) as (actual, loaded, events):
        assert actual == cfg
        assert loaded == model
        assert events == ()
    assert len(calls) == 1
    argv, cwd, env, timeout = calls[0]
    assert argv == [sys.executable, "-B", "scripts/ledger.py", "--root", str(root), "check"]
    assert cwd == root
    assert env["TEMPLATE_GIT_EXECUTABLE"] == cfg.git
    assert 0 < timeout <= 30
    assert gitws.workspace_witness(root, executable=cfg.git) == before
    monkeypatch.setattr(loop.Loop, "_job", lambda *a, **k: pytest.fail("profile-free job"))
    notices = []
    assert Controller(root, cfg, model, emit=notices.append).iteration() == loop.StopReason.internal
    assert "fresh dispatch requires an autonomy profile" in notices[-1]["detail"]
    assert not (root / "local_state/frutlups/jobs").exists()
    assert gitws.workspace_witness(root, executable=cfg.git) == before


@pytest.mark.parametrize("key", ["GIT_DIR", "git_work_tree", "GIT_INDEX_FILE", "GIT_CONFIG_COUNT",
    "GIT_OBJECT_DIRECTORY", "PYTHONPATH", "PYTHONHOME", "TEMPLATE_GIT_EXECUTABLE"])
def test_environment_rejects_ambient_redirection(checker_project, monkeypatch, key):
    from frutlups import _preflight
    root, cfg, model = checker_project
    monkeypatch.setenv(key, "owned-bogus-redirection")
    original = _preflight.procs.run_process
    def forwarded(argv, cwd, env, *args, **kwargs):
        if "scripts/ledger.py" in argv:
            assert env.get(key) == (cfg.git if key == "TEMPLATE_GIT_EXECUTABLE" else None)
        return original(argv, cwd, env, *args, **kwargs)
    monkeypatch.setattr(_preflight.procs, "run_process", forwarded)
    with admission(checker_project):
        pass


@pytest.mark.parametrize("failure", ["timeout", "unknown", "exception", "nonzero", "stdout", "stderr"])
def test_checker_terminal_refusals_are_private_and_nonwriting(checker_project, monkeypatch, failure):
    from frutlups import _preflight
    root, cfg, model = checker_project
    before = gitws.workspace_witness(root, executable=cfg.git)
    original = _preflight.procs.run_process
    calls = []
    def result(argv, cwd, env, timeout, stdout, stderr, *args, **kwargs):
        if "scripts/ledger.py" not in argv:
            return original(argv, cwd, env, timeout, stdout, stderr, *args, **kwargs)
        calls.append(argv)
        stdout.write_bytes(b"abc secret")
        stderr.write_bytes(b"xyz C:/private/owned-path")
        return procs.ProcessResult(None if failure == "unknown" else 2 if failure == "nonzero" else 0,
            failure == "timeout", 0.1, stdout, stderr, "xyz private" if failure == "exception" else None,
            failure == "stdout", failure == "stderr")
    monkeypatch.setattr(_preflight.procs, "run_process", result)
    from _native_support import Controller
    from frutlups import loop, commit, receipt
    def forbidden(*a, **k):
        pytest.fail("effect after failed checker")
    monkeypatch.setattr(_preflight, "_probe", forbidden)
    monkeypatch.setattr(loop.Loop, "_job", forbidden)
    monkeypatch.setattr(receipt, "run", forbidden)
    monkeypatch.setattr(commit, "recover", forbidden)
    notices = []
    assert Controller(root, cfg, model, emit=notices.append).iteration() == loop.StopReason.internal
    assert len(calls) == 1
    detail = notices[-1]["detail"]
    expected = {"timeout": "project checker timed out; inspect retained state",
                "unknown": "project checker terminal status unavailable; inspect retained state",
                "exception": "project checker terminal status unavailable; inspect retained state",
                "nonzero": "project checker refused the project",
                "stdout": "project checker output exceeded capture allowance",
                "stderr": "project checker output exceeded capture allowance"}
    assert detail == "M001-S01: LedgerError: " + expected[failure] + "; owner must inspect"
    assert "abc" not in detail
    assert "xyz" not in detail
    assert "private" not in detail
    assert gitws.workspace_witness(root, executable=cfg.git) == before
    assert not (root / "local_state/frutlups/jobs").exists()


@pytest.mark.parametrize("which", ["historical_manifest", "owner_hotfix"])
def test_configured_git_historical_and_head_reads_without_path(checker_project, monkeypatch, which):
    from frutlups import ledger, commit
    import test_commit_v2
    import test_idle_v2
    if which == "historical_manifest":
        root, cfg, model, _, _ = test_commit_v2.materialize(checker_project)
        result = test_commit_v2.approve(root, cfg, model)
        events = ledger.read(root / cfg.ledger)
        intent = ledger._thaw(events[-1].data["commit_intent"])
        (root / intent["manifest"]["path"]).write_bytes(b"later current manifest bytes")
    else:
        root, cfg, model, rows, _ = test_idle_v2.accepted(checker_project)
        (root / "07_app/result.txt").write_bytes(b"owner hotfix\n")
        gitws._git(root, "add", ".", executable=cfg.git)
        gitws._git(root, "commit", "-qm", "owned hotfix", executable=cfg.git)
        events = ledger.read(root / cfg.ledger)
    before = gitws.workspace_witness(root, executable=cfg.git)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("GIT_DIR", str(root / "wrong-repository"))
    state = ledger.validate_history(root, events, model, executable=cfg.git)
    assert state.slices["M001-S01"].step == "accepted"
    if which == "historical_manifest":
        assert commit.status(root, events, model, executable=cfg.git) == result
    else:
        assert ledger.check(root / cfg.ledger, root, executable=cfg.git) == ()
    assert gitws.workspace_witness(root, executable=cfg.git) == before
    with pytest.raises((ValueError, OSError)):
        if which == "historical_manifest":
            ledger.validate_history(root, events, model, executable=str(root / "missing.exe"))
        else:
            ledger.check(root / cfg.ledger, root, executable=str(root / "missing.exe"))


@pytest.mark.parametrize("field", ["frutlups.local.toml", "project.local.toml", "roadmap.yaml", "ledger", "executable", "head", "index"])
def test_real_checker_success_does_not_authorize_drift(checker_project, monkeypatch, tmp_path, field):
    from frutlups import _preflight
    root, cfg, model = checker_project
    original = _preflight.procs.run_process
    calls = []
    if field == "executable":
        executable = owned_python(tmp_path / "replacement python", root)
        (root / "project.local.toml").write_text('schema="template.local/1"\npython=' + json.dumps(str(executable)), encoding="utf-8")
    def drift(argv, cwd, env, *args, **kwargs):
        result = original(argv, cwd, env, *args, **kwargs)
        if "scripts/ledger.py" in argv:
            calls.append(argv)
            assert result.exit == 0
            if field == "executable":
                with executable.open("ab") as stream:
                    stream.write(b"\0")
            elif field == "head":
                gitws._git(root, "commit", "--allow-empty", "-qm", "owned checker HEAD drift", executable=cfg.git)
            elif field == "index":
                gitws._git(root, "update-index", "--assume-unchanged", "--", "07_app/CONTEXT.md", executable=cfg.git)
            else:
                path = root / (cfg.ledger if field == "ledger" else field)
                path.write_bytes(path.read_bytes() + b"\n" if path.exists() else b"new local authority")
        return result
    monkeypatch.setattr(_preflight.procs, "run_process", drift)
    with pytest.raises(ValueError, match="witness"):
        with admission(checker_project):
            pytest.fail("drift admitted")
    assert len(calls) == 1


@pytest.mark.parametrize("module,case,method", [
    ("test_verification_v2", "GitSelectionTests", "test_invalid_selection_refuses_before_either_launch"),
    ("test_verification_v2", "GitSelectionTests", "test_real_selected_failure_and_replacement_never_fall_back"),
    ("test_commit_v2", "GitArgumentTests", "test_actual_frame_boundaries_and_complete_diff_batches"),
])
def test_pinned_helper_selection_replacement_and_synthetic_framing(checker_pin, tmp_path, module, case, method):
    code = r"""
import sys,unittest,importlib
from pathlib import Path
root=Path(sys.argv[1]);sys.path[:0]=[str(root/'tests'),str(root/'scripts')]
cls=getattr(importlib.import_module(sys.argv[2]),sys.argv[3])
result=unittest.TextTestRunner().run(unittest.TestSuite([cls(sys.argv[4])]))
assert result.testsRun==1
assert not result.skipped
assert result.wasSuccessful()
"""
    env = gitws.controlled_environment(os.environ)
    env["TEMPLATE_GIT_EXECUTABLE"] = gitws.selected_executable(shutil.which("git"))
    result = managed([sys.executable, "-B", "-c", code, str(checker_pin), module, case, method],
                     checker_pin, env, tmp_path)
    assert result.exit == 0, result.stdout_path.read_text() + result.stderr_path.read_text()


@pytest.mark.parametrize("linked,kind", [(False, "manual"), (False, "native"), (True, "manual"), (True, "native")])
def test_owned_checker_read_allows_no_second_writer(checker_project, tmp_path, linked, kind):
    from frutlups import _preflight
    root, cfg, model = checker_project
    other = root
    if linked:
        other = tmp_path / "linked project"
        gitws._git(root, "worktree", "add", "--detach", str(other), "HEAD", executable=cfg.git)
    before = gitws.workspace_witness(root, executable=cfg.git)
    authority = _preflight._authority_bytes(root)
    code = r"""
import sys
from pathlib import Path
root=Path(sys.argv[1])
if sys.argv[3]=='manual':
 sys.path.insert(0,str(root/'scripts'))
 from _common import writer_lock
 lock=writer_lock(root)
else:
 from frutlups.ownership import writer_lock
 lock=writer_lock(root,executable=sys.argv[2])
try:
 with lock: pass
except ValueError as error:
 assert 'writer' in str(error)
 print('competing writer refused')
 raise SystemExit(2)
raise SystemExit(3)
"""
    env = gitws.controlled_environment(os.environ)
    env["TEMPLATE_GIT_EXECUTABLE"] = cfg.git
    with admission(checker_project):
        result = managed([sys.executable, "-B", "-c", code, str(other), cfg.git, kind], other, env, tmp_path / "contender")
        assert result.exit == 2, result.stdout_path.read_text() + result.stderr_path.read_text()
        assert result.stdout_path.read_text().strip() == "competing writer refused"
    assert gitws.workspace_witness(root, executable=cfg.git) == before
    assert _preflight._authority_bytes(root) == authority


@pytest.mark.parametrize("control", ["root", "path", "absent", "combined"])
def test_independent_search_controls_use_selected_git(checker_project, tmp_path, control):
    from dataclasses import replace
    root, cfg, model = checker_project
    shadow = tmp_path / "path shadow"
    shadow.mkdir()
    if control in ("root", "combined"):
        (root / "git.exe").write_bytes(b"invalid root shadow")
    if control in ("path", "combined"):
        (shadow / "git.exe").write_bytes(b"invalid PATH shadow")
    directories = (str(shadow),) if control in ("path", "combined") else () if control == "absent" else cfg.path_dirs
    cfg = replace(cfg, path_dirs=directories)
    before = gitws.workspace_witness(root, executable=cfg.git)
    with admission((root, cfg, model)):
        pass
    assert gitws.workspace_witness(root, executable=cfg.git) == before
    if control in ("root", "combined"):
        import stat
        from frutlups import receipt
        assert receipt.native_snapshot(root, cfg)["files"]["git.exe"]["mode"] == stat.S_IMODE((root / "git.exe").stat().st_mode)
    # Negative controls are independent: no project shadow in PATH-only/absent cases.
    if control in ("path", "absent"):
        assert not (root / "git.exe").exists()
    code = r"""
import sys
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root/'scripts'))
import _common,_git
helper=_common.git if sys.argv[2]=='common' else _git.run
helper(root,'--version')
"""
    env = gitws.controlled_environment(os.environ)
    env["PATH"] = os.pathsep.join(directories)
    for helper in ("common", "bounded"):
        result = managed([sys.executable, "-B", "-c", code, str(root), helper], root, env, tmp_path / helper)
        assert result.exit != 0


@pytest.mark.parametrize("cap", [0, -1, float("nan"), float("inf")])
def test_nonfinite_or_exhausted_checker_budget_never_launches(checker_project, monkeypatch, cap):
    from dataclasses import replace
    from frutlups import _preflight
    root, cfg, model = checker_project
    cfg = replace(cfg, timeouts=replace(cfg.timeouts, verification_seconds=cap))
    original = _preflight.procs.run_process
    def no_checker(argv, *a, **k):
        assert "scripts/ledger.py" not in argv
        return original(argv, *a, **k)
    monkeypatch.setattr(_preflight.procs, "run_process", no_checker)
    with pytest.raises(ValueError, match="finite positive deadline"):
        with admission((root, cfg, model)):
            pytest.fail("invalid checker budget admitted")


@pytest.mark.parametrize("scope", ["slice", "milestone"])
@pytest.mark.parametrize("recovery_actor", ["runner", "manual"])
def test_fresh_process_checker_precedes_exact_pending_recovery(checker_project, monkeypatch, tmp_path, scope, recovery_actor):
    from dataclasses import replace
    import yaml
    import test_loop
    from _native_support import Controller, grant_project
    from frutlups import ledger, roadmap, commit, loop
    root, cfg, model = checker_project
    cfg = replace(cfg, commit_on_accept=True)
    main = root / "frutlups.toml"
    main.write_text(main.read_text("utf-8").replace("commit_on_accept = false", "commit_on_accept = true"), encoding="utf-8")
    data = yaml.safe_load((root / "roadmap.yaml").read_text("utf-8"))
    data["milestones"][0]["holistic_review"] = scope == "milestone"
    (root / "roadmap.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    root, cfg, model = grant_project((root, cfg, roadmap.load(root / "roadmap.yaml")))
    monkeypatch.setattr(test_loop.pi, "_argv", lambda job, cfg: (sys.executable, str(test_loop.HERE / "fake_pi.py"), str(job.prompt_path)))
    monkeypatch.setattr(test_loop.claude, "_argv", lambda job, cfg: (sys.executable, str(test_loop.HERE / "fake_claude.py")))
    (root / "local_state").mkdir(exist_ok=True)
    test_loop.plan(root)
    runner = Controller(root, cfg, model)
    for _ in range(4):
        assert runner.iteration() is None
    if scope == "milestone":
        assert runner.iteration() is None
    with monkeypatch.context() as patch:
        recover = commit._recover
        def interrupted(*a, **k):
            if any(row["state"] == "pending" for row in
                   commit.status(a[0], a[1], a[2], executable=k.get("executable", cfg.git))):
                raise RuntimeError("owned interruption before approved commit")
            return recover(*a, **k)
        patch.setattr(commit, "_recover", interrupted)
        assert runner.iteration() == loop.StopReason.internal
    events = ledger.read(root / cfg.ledger)
    state = ledger.validate_history(root, events, model, executable=cfg.git)
    prefix = (root / cfg.ledger).read_bytes()
    head = gitws.head(root, executable=cfg.git)
    assert commit.status(root, events, model, executable=cfg.git)[-1]["state"] == "pending"
    code = r"""
import json,sys
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,sys.argv[2])
from _native_support import Controller
from frutlups import _preflight,commit,ledger,roadmap,loop
from frutlups.config import Config
cfg=Config.load(root);model=roadmap.load(root/'roadmap.yaml')
calls=[]
original=_preflight.procs.run_process
def check(argv,*a,**kw):
 result=original(argv,*a,**kw)
 if 'scripts/ledger.py' in argv:
  assert result.exit==0 and not result.timed_out
  calls.append('checker')
 return result
_preflight.procs.run_process=check
recover=commit.recover
def recovery(*a,**kw):
 assert calls==['checker']
 result=recover(*a,**kw);calls.append('recovery');return result
commit.recover=recovery
load=Config.load
Config.load=classmethod(lambda cls,*a,**kw: (calls.append('reload_config'),load(*a,**kw))[1])
result=Controller(root,cfg,model).iteration()
assert result in (loop.StopReason.done,loop.StopReason.boundary)
print('CHECKER_RECOVERY='+json.dumps(calls))
"""
    env = gitws.controlled_environment(os.environ)
    env["TEMPLATE_GIT_EXECUTABLE"] = cfg.git
    assert events[-1].by == "frutlups"
    if recovery_actor == "manual":
        result = managed([sys.executable, "-B", "scripts/ledger.py", "--root", str(root),
                          "recover", "--execute"], root, env, tmp_path / "manual-restart")
        assert result.exit == 0, result.stdout_path.read_text() + result.stderr_path.read_text()
        assert gitws.head(root, executable=cfg.git) != head
        assert (root / cfg.ledger).read_bytes() == prefix
    for repeat in (False, True):
        output = tmp_path / ("restart" if not repeat else "repeat")
        result = managed([sys.executable, "-B", "-c", code, str(root), str(Path(__file__).parent)], root, env, output)
        assert result.exit == 0, result.stdout_path.read_text() + result.stderr_path.read_text()
        calls = json.loads(next(line.split("=", 1)[1] for line in result.stdout_path.read_text().splitlines() if line.startswith("CHECKER_RECOVERY=")))
        assert calls == (["checker", "recovery", "reload_config"] if not repeat and recovery_actor == "runner" else ["checker"])
        assert (root / cfg.ledger).read_bytes() == prefix
        current = ledger.validate_history(root, ledger.read(root / cfg.ledger), model, executable=cfg.git)
        assert current.attempts == state.attempts
        assert current.runs == state.runs
        assert commit.status(root, events, model, executable=cfg.git)[-1]["state"] == "completed"
        if not repeat:
            recovered = gitws.head(root, executable=cfg.git)
            assert recovered != head
        else:
            assert gitws.head(root, executable=cfg.git) == recovered


@pytest.mark.parametrize("suffix", [".cmd", ".bat", ".ps1"])
def test_python_shell_selection_refuses_before_identity_child(checker_project, tmp_path, monkeypatch, suffix):
    from frutlups import runtime
    root, cfg, model = checker_project
    selected = tmp_path / ("not-python" + suffix)
    selected.write_bytes(b"owned invalid interpreter")
    (root / "project.local.toml").write_text('schema="template.local/1"\npython=' + json.dumps(str(selected)), encoding="utf-8")
    monkeypatch.setattr(runtime.procs, "run_process", lambda *a, **k: pytest.fail("shell interpreter launched"))
    with pytest.raises(ValueError, match="python must name an existing absolute executable file"):
        runtime.resolve_runtime(root, model, git=cfg.git)


@pytest.mark.parametrize("kind", ["reported_overrun", "elapsed_overrun", "timeout"])
def test_checker_deadline_refuses_late_success_and_owned_timeout(checker_project, tmp_path, monkeypatch, kind):
    import time
    from dataclasses import replace
    from frutlups import _preflight
    root, cfg, model = checker_project
    original = _preflight.procs.run_process
    now = [time.monotonic()]
    calls, child_pids = [], []
    if kind == "timeout":
        # Owned timeout fixture only; successful checker proofs use the pinned script.
        (root / "scripts/ledger.py").write_text(
            "import subprocess,sys,time\n"
            "child=subprocess.Popen([sys.executable,'-B','-c','import time;time.sleep(20)'])\n"
            "print(child.pid,flush=True)\ntime.sleep(20)\n", encoding="utf-8")
        cfg = replace(cfg, timeouts=replace(cfg.timeouts, verification_seconds=2))
    def forwarded(argv, cwd, env, timeout, *args, **kwargs):
        result = original(argv, cwd, env, timeout, *args, **kwargs)
        if "scripts/ledger.py" in argv:
            calls.append(result)
            if kind == "timeout":
                child_pids.append(int(result.stdout_path.read_text().strip()))
            assert 0 < timeout <= (2 if kind == "timeout" else 30)
            if kind == "reported_overrun":
                assert result.exit == 0
                result = replace(result, secs=timeout + 1)
            elif kind == "elapsed_overrun":
                assert result.exit == 0
                now[0] += 100
        return result
    monkeypatch.setattr(_preflight.procs, "run_process", forwarded)
    before = gitws.workspace_witness(root, executable=cfg.git)
    with pytest.raises(ValueError, match="timed out" if kind == "timeout" else "exceeded its deadline"):
        with _preflight._owned_admission(root, cfg, model, deadline=now[0] + 60,
                clock=time.monotonic if kind == "timeout" else lambda: now[0]):
            pytest.fail("late checker admitted")
    assert len(calls) == 1
    if kind == "timeout":
        assert calls[0].timed_out
        assert calls[0].exception is None
        assert len(child_pids) == 1
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = api.OpenProcess(0x1000, False, child_pids[0])
        if handle:
            try:
                status = wintypes.DWORD()
                assert api.GetExitCodeProcess(handle, ctypes.byref(status))
                assert status.value != 259  # STILL_ACTIVE
            finally:
                api.CloseHandle(handle)
    assert gitws.workspace_witness(root, executable=cfg.git) == before



@pytest.fixture
def manual_pending(checker_project):
    import test_commit_v2
    import test_attempts_v2
    from frutlups import ledger, receipt, commit, _attempts, cli
    from frutlups.roadmap import slice_by_id
    root, cfg, model, rows, write = test_commit_v2.materialize(checker_project, profile=False)
    prompt, coded, verified, reviewed = rows
    frozen = ledger.envelope(root, prompt["envelope"], "M001-S01", 1)
    frozen["full"] = ["python", "-c", "print('owned manual verification')"]
    prompt["envelope"] = write(prompt["envelope"]["path"], frozen)
    report = (root / reviewed["report"]).read_bytes()
    (root / reviewed["report"]).unlink()
    (root / verified["receipt"]).unlink()
    test_attempts_v2.save(root, cfg, [prompt, coded])
    gitws._git(root, "add", "-A", executable=cfg.git)
    gitws._git(root, "commit", "-qm", "owned pre-verification state", executable=cfg.git)
    result = receipt.run(root, slice_by_id(model, "M001-S01"), 1, model, cfg, False)
    assert result.ok
    verified["sha"] = receipt.write(result, root / verified["receipt"])
    assert write(reviewed["report"], report)["sha"] == reviewed["sha"]
    test_attempts_v2.save(root, cfg, rows)
    # Run the pinned template's actual human accept route in an isolated child.
    # Interrupt its existing recovery boundary after the real intent is appended.
    code = r"""
import sys
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root/'scripts'))
import ledger,_commit
original=_commit.recover
def interrupted(root,events,rm,execute=False):
 if execute:
  raise RuntimeError('owned interruption before Git recovery')
 return original(root,events,rm,execute)
_commit.recover=interrupted
try:
 ledger.main(['--root',str(root),'accept','M001-S01','--commit','--by','human'])
except RuntimeError as exc:
 assert str(exc)=='owned interruption before Git recovery'
 print('manual approval retained')
else:
 raise AssertionError('manual approval did not reach recovery')
"""
    env = gitws.controlled_environment(os.environ)
    env["TEMPLATE_GIT_EXECUTABLE"] = cfg.git
    import tempfile
    with tempfile.TemporaryDirectory(prefix="owned-manual-approval-") as output:
        actual = managed([sys.executable, "-B", "-c", code, str(root)], root, env, Path(output))
        assert actual.exit == 0, actual.stdout_path.read_text() + actual.stderr_path.read_text()
        assert actual.stdout_path.read_text().strip() == "manual approval retained"
    events = ledger.read(root / cfg.ledger)
    assert events[-1].by == "human"
    assert events[-1].ev == ledger.Ev.accepted
    assert commit.status(root, events, model, executable=cfg.git)[0]["state"] == "pending"
    original_head = gitws.head(root, executable=cfg.git)
    prefix = (root / cfg.ledger).read_bytes()
    state = ledger.validate_history(root, ledger.read(root / cfg.ledger), model, executable=cfg.git)
    assert model.autonomy is None
    assert not state.runs
    assert not state.attempts
    return root, cfg, model


def test_manual_pending_recovery_needs_no_profile_or_new_grant(manual_pending):
    from frutlups import ledger, commit, cli
    root, cfg, model = manual_pending
    prefix = (root / cfg.ledger).read_bytes()
    original_head = gitws.head(root, executable=cfg.git)
    with admission((root, cfg, model)) as (_, reloaded, events):
        assert reloaded.autonomy is None
        assert commit.status(root, events, reloaded, executable=cfg.git)[0]["state"] == "completed"
    assert (root / cfg.ledger).read_bytes() == prefix
    recovered_head = gitws.head(root, executable=cfg.git)
    assert recovered_head != original_head
    with admission((root, cfg, model)) as (_, reloaded, events):
        assert reloaded.autonomy is None
        assert commit.status(root, events, reloaded, executable=cfg.git)[0]["state"] == "completed"
    assert gitws.head(root, executable=cfg.git) == recovered_head
    assert (root / cfg.ledger).read_bytes() == prefix
    before = gitws.workspace_witness(root, executable=cfg.git)
    output = []
    cli._status(root, True, True, output.append)
    assert output
    assert gitws.workspace_witness(root, executable=cfg.git) == before
    assert (root / cfg.ledger).read_bytes() == prefix

@pytest.mark.parametrize("control", ["git_identity", "authority", "worktree"])
def test_lock_boundary_change_refuses_before_checker(checker_project, monkeypatch, control):
    from contextlib import contextmanager
    from frutlups import _preflight
    root, cfg, model = checker_project
    original = _preflight.writer_lock
    @contextmanager
    def changed(*args, **kwargs):
        if control == "git_identity":
            # Same textual Git directory, replacement between discovery and lock.
            source, target = root / ".git", root / "owned-old-git"
            assert source.resolve().parent == root.resolve()
            assert target.resolve().parent == root.resolve()
            source.rename(target)
            shutil.copytree(target, source)
        if control == "worktree":
            redirected = root.parent / "owned redirected worktree"
            redirected.mkdir()
            gitws._git(root, "config", "core.worktree", str(redirected), executable=cfg.git)
        with original(*args, **kwargs):
            if control == "authority":
                with (root / "frutlups.local.toml").open("a") as stream:
                    stream.write("\n# owned lock-boundary mutation\n")
            yield
    monkeypatch.setattr(_preflight, "writer_lock", changed)
    monkeypatch.setattr(_preflight, "_checker", lambda *a, **k: pytest.fail("checker after boundary drift"))
    with pytest.raises(ValueError, match="admission (authority or ownership changed|canonical root changed)"):
        with admission(checker_project):
            pytest.fail("changed boundary admitted")


def test_actual_checker_child_identity_at_supported_long_spaced_root(manual_pending, tmp_path):
    root, cfg, model = manual_pending
    with admission(manual_pending):
        pass
    long_root = tmp_path / ("owned long space " * 4).rstrip() / "project"
    shutil.copytree(root, long_root, ignore=shutil.ignore_patterns("local_state", "__pycache__"))
    root = long_root.resolve()
    assert 160 <= len(str(root)) < 190
    executable = owned_python(tmp_path / "identity python", root)
    (executable.parent / "git.exe").write_bytes(b"owned invalid application shadow")
    record = tmp_path / "actual-checker-identity.json"
    # Startup instrumentation forwards the checker unchanged and records real
    # child identity/calls outside the project; it never supplies a result.
    startup = executable.parent / "sitecustomize.py"
    startup.write_text(
        "import atexit,ctypes,json,os,platform,sys,yaml\n"
        "from pathlib import Path\n"
        "calls=[]\n"
        "def observe(frame,event,arg):\n"
        " if event=='call' and frame.f_code.co_name=='run' and frame.f_code.co_filename.endswith('_git.py'):\n"
        "  calls.append('template_git')\n"
        "sys.setprofile(observe)\n"
        "def record():\n"
        " sys.setprofile(None)\n"
        " if len(sys.argv)>1 and sys.argv[-1]=='check':\n"
        "  buf=ctypes.create_unicode_buffer(32768)\n"
        "  assert ctypes.windll.kernel32.GetModuleFileNameW(None,buf,len(buf))\n"
        "  value=dict(module=buf.value,python=platform.python_version(),implementation=platform.python_implementation(),yaml=yaml.__version__,cwd=os.getcwd(),argv=sys.argv,git=os.environ.get('TEMPLATE_GIT_EXECUTABLE'),calls=calls)\n"
        + "  Path(" + repr(str(record)) + ").write_text(json.dumps(value))\n"
        "atexit.register(record)\n", encoding="utf-8")
    pth = executable.parent / "python314._pth"
    pth.write_text(str(executable.parent) + "\n" + pth.read_text(), encoding="utf-8")
    (root / "project.local.toml").write_text(
        'schema="template.local/1"\npython=' + json.dumps(str(executable)), encoding="utf-8")
    before = gitws.workspace_witness(root, executable=cfg.git)
    with admission((root, cfg, model)):
        pass
    identity = json.loads(record.read_text())
    assert Path(identity["module"]) == executable
    assert identity["python"] == "3.14.3"
    assert identity["implementation"] == "CPython"
    assert identity["yaml"] == "6.0.3"
    assert Path(identity["cwd"]) == root
    assert identity["argv"] == ["scripts/ledger.py", "--root", str(root), "check"]
    assert identity["git"] == cfg.git
    assert identity["calls"] and set(identity["calls"]) == {"template_git"}
    assert gitws.workspace_witness(root, executable=cfg.git) == before


@pytest.mark.parametrize("condition", ["approved_file", "unknown", "cancelled"])
def test_checked_manual_pending_guards_preserve_state(manual_pending, monkeypatch, condition):
    from frutlups import ledger, commit, _preflight
    root, cfg, model = manual_pending
    event = ledger.read(root / cfg.ledger)[-1]
    intent = ledger._thaw(event.data["commit_intent"])
    if condition == "approved_file":
        (root / "prompt.md").write_bytes(b"owned unapproved drift")
    elif condition == "unknown":
        commit._marker(root, executable=cfg.git).write_text(json.dumps(
            {"operation": intent["id"], "parent": intent["parent"]}))
    else:
        commit.cancel(root, model, intent["id"], "Owned cancellation", "human",
                      retain_acceptance=True, executable=cfg.git)
    before = gitws.workspace_witness(root, executable=cfg.git)
    saved = (root / cfg.ledger).read_bytes()
    original = _preflight.procs.run_process
    checks = []
    def forwarded(argv, *a, **k):
        result = original(argv, *a, **k)
        if "scripts/ledger.py" in argv:
            checks.append(result.exit)
        return result
    monkeypatch.setattr(_preflight.procs, "run_process", forwarded)
    if condition == "cancelled":
        with admission((root, cfg, model)) as (_, _, events):
            assert commit.status(root, events, model, executable=cfg.git)[0]["state"] == "cancelled"
        assert checks == [0]
    else:
        with pytest.raises(ValueError):
            with admission((root, cfg, model)):
                pytest.fail("unsafe pending state admitted")
        assert len(checks) <= 1
    assert gitws.workspace_witness(root, executable=cfg.git) == before
    assert (root / cfg.ledger).read_bytes() == saved
    if condition == "unknown":
        assert commit._marker(root, executable=cfg.git).is_file()


@pytest.mark.parametrize("case", ["missing", "closed", "jobs", "seconds", "wrong_scope", "retrofit"])
def test_fresh_dispatch_grant_guards_before_effects(checker_project, monkeypatch, case):
    import copy
    import yaml
    from dataclasses import replace
    from _native_support import Controller, grant_project
    from frutlups import _attempts, _preflight, ledger, loop, roadmap, receipt
    root, cfg, model = checker_project
    if case == "wrong_scope":
        data = yaml.safe_load((root / "roadmap.yaml").read_text())
        second = copy.deepcopy(data["milestones"][0]["slices"][0])
        second["id"] = "M001-S02"
        data["milestones"][0]["slices"].append(second)
        (root / "roadmap.yaml").write_text(yaml.safe_dump(data))
        model = roadmap.load(root / "roadmap.yaml")
    root, cfg, model = grant_project((root, cfg, model))
    rows = [ledger._event_dict(e) for e in ledger.read(root / cfg.ledger)]
    if case == "missing":
        rows = []
    elif case == "wrong_scope":
        rows[0]["slices"] = ["M001-S02"]
        rows[0]["stop"] = "M001-S02"
    elif case in ("jobs", "seconds"):
        rows[0]["allowance"]["jobs" if case == "jobs" else "seconds"] = 1
    elif case == "retrofit":
        ordinary = [_attempts.event("attempt_started", invocation="ordinary", scope="M001", round="holistic",
                    role="probe", contract="a" * 64, allowance_seconds=1, retry="initial"),
                    _attempts.event("attempt_finished", invocation="ordinary", status="completed",
                                    usage={"secs": 0.1, "completeness": "unknown"})]
        ledger.fold(tuple(ledger._parse_event(r) for r in ordinary), replace(model, autonomy=None))
        rows = ordinary + rows
    (root / cfg.ledger).write_bytes(b"".join(json.dumps(r).encode() + b"\n" for r in rows))
    if case in ("jobs", "seconds", "closed"):
        ticks = iter([0, 2 if case == "closed" else 1])
        with _attempts.Operation(root, cfg, model, "probe", 1, clock=lambda: next(ticks)) as operation:
            operation.finish(True)
    saved = (root / cfg.ledger).read_bytes()
    before = gitws.workspace_witness(root, executable=cfg.git)
    authority = _preflight._authority_bytes(root)
    def forbidden(*a, **k):
        pytest.fail("provider, job or verifier after inadmissible grant")
    monkeypatch.setattr(_preflight, "_probe", forbidden)
    monkeypatch.setattr(loop.Loop, "_job", forbidden)
    monkeypatch.setattr(receipt, "run", forbidden)
    notices = []
    if case == "retrofit":
        with pytest.raises(ValueError, match="profile attempts require preceding admission; historical cutover unsupported"):
            Controller(root, cfg, model, emit=notices.append).iteration()
    else:
        reason = Controller(root, cfg, model, emit=notices.append).iteration()
        assert reason == (loop.StopReason.boundary if case == "closed" else loop.StopReason.internal)
        expected = {"missing": "profile requires current grant", "closed": "current grant boundary completed",
                    "jobs": "run allowance exhausted", "seconds": "run allowance exhausted",
                    "wrong_scope": "slice outside current grant"}
        assert expected[case] in notices[-1]["detail"]
    assert (root / cfg.ledger).read_bytes() == saved
    assert gitws.workspace_witness(root, executable=cfg.git) == before
    assert _preflight._authority_bytes(root) == authority
    assert not (root / "local_state/frutlups/jobs").exists()
