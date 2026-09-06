import json
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import frutlups.receipt as receipt_module
from frutlups.ledger import evidence_sha
from frutlups.receipt import run, write
from frutlups.roadmap import load

FIXTURE = Path(__file__).parent / "fixtures" / "v4_project"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True,
    )


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "core.autocrlf", "false")
    (root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    _git(root, "add", ".gitignore")
    _git(root, "commit", "-q", "-m", "base")
    return root


def _config(timeout: float = 2) -> SimpleNamespace:
    executable = shutil.which("git")
    assert executable
    passthrough = tuple(
        key for key in ("SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "TMP") if key in os.environ
    )
    return SimpleNamespace(
        git=executable,
        ledger="ledger.jsonl",
        env_passthrough=passthrough,
        path_dirs=(str(Path(sys.executable).parent),),
        timeouts=SimpleNamespace(verification_seconds=timeout),
    )


def _roadmap_with(command: tuple[str, ...], focused=()):
    roadmap = load(FIXTURE / "roadmap.yaml")
    item = replace(roadmap.milestones[0].slices[0], verification=command, focused=focused)
    milestone = replace(roadmap.milestones[0], slices=(item,))
    return replace(roadmap, milestones=(milestone,)), item


def _coded(root: Path, item, changed: list[dict[str, str]]) -> None:
    common = {"schema": "frutlups.ledger/1", "t": "2026-09-03T12:00:00Z"}
    events = [
        {
            **common,
            "ev": "prompt",
            "by": "architect",
            "slice": item.id,
            "round": 1,
            "path": "prompt.md",
            "sha": "a" * 64,
        },
        {
            **common,
            "ev": "coded",
            "by": "frutlups",
            "slice": item.id,
            "round": 1,
            "changed": changed,
        },
    ]
    text = "".join(json.dumps(event, separators=(",", ":")) + "\n" for event in events)
    (root / "ledger.jsonl").write_text(text, encoding="utf-8")


def test_run_is_not_ok_on_nonzero_and_bounds_tails_and_paths(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    command = (
        sys.executable,
        "-c",
        "import sys; print('x' * 5000); print('y' * 5000, file=sys.stderr); sys.exit(4)",
    )
    roadmap, item = _roadmap_with(command)

    receipt = run(root, item, 1, roadmap, _config(), focused=False)

    result = receipt.commands[0]
    assert receipt.ok is False
    assert result.exit == 4
    assert len(result.stdout_tail.encode()) <= 4096
    assert len(result.stderr_tail.encode()) <= 4096
    assert result.argv[0] == "<outside-repo>"


def test_run_is_not_ok_on_timeout(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    roadmap, item = _roadmap_with(
        (sys.executable, "-c", "import time; time.sleep(2)"),
    )

    receipt = run(root, item, 1, roadmap, _config(0.05), focused=False)

    assert receipt.ok is False
    assert receipt.commands[0].timed_out is True
    assert receipt.commands[0].exit is None


def test_run_is_not_ok_when_git_status_changes(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    roadmap, item = _roadmap_with(
        (sys.executable, "-c", "from pathlib import Path; Path('new.txt').write_text('new')"),
    )

    receipt = run(root, item, 1, roadmap, _config(), focused=False)

    assert receipt.ok is False
    assert receipt.commands[0].exit == 0
    assert receipt.changed_files == ()


def test_run_uses_recorded_coded_manifest_with_receipt_schema_keys(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    roadmap, item = _roadmap_with((sys.executable, "-c", "print('ok')"))
    changed = [{"path": "recorded.txt", "sha": "b" * 64, "kind": "modified"}]
    _coded(root, item, changed)

    receipt = run(root, item, 1, roadmap, _config(), focused=False)
    path = tmp_path / "receipt.json"
    write(receipt, path)

    assert json.loads(path.read_text(encoding="utf-8"))["changed_files"] == changed


def test_run_accepts_an_unchanged_dirty_tree_and_runs_focused_first(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "existing-dirty.txt").write_text("dirty", encoding="utf-8")
    command = (sys.executable, "-c", "print('full')")
    focused = ((sys.executable, "-c", "print('focused')"),)
    roadmap, item = _roadmap_with(command, focused)

    receipt = run(root, item, 2, roadmap, _config(), focused=True)

    assert receipt.ok is True
    assert receipt.tree_dirty_before is True
    assert receipt.tree_clean_after is False
    assert [result.label for result in receipt.commands] == ["focused", "full"]


def test_write_returns_hash_of_portable_receipt(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    roadmap, item = _roadmap_with((sys.executable, "-c", "print('ok')"))
    receipt = run(root, item, 1, roadmap, _config(), focused=False)
    path = tmp_path / "receipts" / "receipt.json"

    digest = write(receipt, path)

    assert digest == evidence_sha(path.read_bytes())
    assert path.read_bytes().endswith(b"\n")


def test_receipt_uses_exact_shared_scrubbed_child_environment(tmp_path, monkeypatch):
    root = _repository(tmp_path)
    names = (
        "SAFE_VALUE", "API_KEY", "Api_Key", "OPENAI_API_KEY", "ANTHROPIC_AUTH_TOKEN",
        "Vendor_Token", "VENDOR_SECRET", "mixed_api_key",
    )
    for name in names:
        monkeypatch.setenv(name, "value-" + name)
    config = _config()
    config.env_passthrough = names
    captured = []
    original = receipt_module._execute

    def execute(label, argv, cwd, env, timeout):
        captured.append(env)
        return original(label, argv, cwd, env, timeout)

    monkeypatch.setattr(receipt_module, "_execute", execute)
    roadmap, item = _roadmap_with((sys.executable, "-c", "print('ok')"))
    result = run(root, item, 1, roadmap, config, focused=False)

    assert result.ok is True
    assert captured == [{
        "SAFE_VALUE": "value-SAFE_VALUE",
        "PATH": os.pathsep.join(config.path_dirs),
        "PYTHONDONTWRITEBYTECODE": "1",
        "FRUTLUPS_SEAT": "verification",
    }]


def test_scrub_text_preserves_long_text_and_relative_paths(tmp_path):
    root = tmp_path / "Project Folder"
    text = (
        f"BEGIN {str(root).upper()}/07_app/code.py\n"
        "Inline `C:\\Fixture Folder\\code.py` and "
        "[link](C:/fixture/code.py) and /tmp/fixture.py\n"
        "UNC `\\\\server\\share\\Fixture Folder\\code.py`\n"
        "Relative 07_app/code.py https://example.invalid/docs\n"
        + "z" * 6000 + "\nEND"
    )

    assert receipt_module.scrub_text(text, root, {}) == (
        "BEGIN <repo>/07_app/code.py\n"
        "Inline `<absolute-path>` and [link](<absolute-path>) and <absolute-path>\n"
        "UNC `<absolute-path>`\n"
        "Relative 07_app/code.py https://example.invalid/docs\n"
        + "z" * 6000 + "\nEND"
    )
