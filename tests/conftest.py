"""Session-initialised repository shared by the loop and CLI fixture copies."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


@pytest.fixture(scope="session")
def initialized_project(tmp_path_factory):
    root = tmp_path_factory.mktemp("loop-baseline") / "repo"
    shutil.copytree(Path(__file__).parent / "fixtures" / "v4_project", root)
    data = yaml.safe_load((root / "roadmap.yaml").read_text(encoding="utf-8"))
    data["verification"]["full"] = [sys.executable, "-c", "print('verified')"]
    data["milestones"][0]["slices"][0]["allowed_prefixes"] = ["07_app/"]
    data["milestones"][0]["holistic_review"] = False
    (root / "roadmap.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    with (root / ".gitignore").open("a", encoding="utf-8") as stream:
        stream.write("\nlocal_state/\n__pycache__/\n")
    (root / "local_state").mkdir(exist_ok=True)
    (root / "local_state" / "plan.json").write_text("{}", encoding="utf-8")
    for args in (
        ("init", "-q"),
        ("config", "user.name", "Fixture"),
        ("config", "user.email", "fixture@example.invalid"),
        ("config", "core.autocrlf", "false"),
        ("add", "."),
        ("commit", "-qm", "fixture"),
    ):
        subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True)
    return root
