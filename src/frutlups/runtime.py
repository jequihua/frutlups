"""Project-local Python selection, with managed isolated identity queries."""

import hashlib
import json
import os
import platform
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from . import gitws, procs


@dataclass(frozen=True)
class Runtime:
    executable: str
    identity: object


def runtime_argv(argv, executable):
    """Only a leading literal python names the selected interpreter."""
    return [executable if i == 0 and value == "python" else value
            for i, value in enumerate(argv)]


def resolve_runtime(root, roadmap=None, *, git="git"):
    root = Path(root).resolve()
    local = root / "project.local.toml"
    executable, source = sys.executable, "invoking_python"
    if local.exists():
        ignored = gitws._git(root, "--no-literal-pathspecs", "check-ignore", "-q",
                             "project.local.toml", executable=git, check=False)
        tracked = gitws._git(root, "ls-files", "--error-unmatch", "project.local.toml",
                             executable=git, check=False)
        if ignored.returncode or tracked.returncode == 0:
            raise ValueError("project.local.toml must be ignored and untracked")
        data = tomllib.loads(local.read_text(encoding="utf-8"))
        if set(data) != {"schema", "python"} or data.get("schema") != "template.local/1":
            raise ValueError("project.local.toml requires schema template.local/1 and python")
        value = data["python"]
        if not isinstance(value, str) or not Path(value).is_absolute() or not Path(value).is_file():
            raise ValueError("project.local.toml python must name an existing absolute executable")
        executable, source = gitws.selected_executable(value, label="project.local.toml python"), "project.local"
    executable = gitws.selected_executable(executable, label="selected Python")
    if Path(executable).resolve() == Path(sys.executable).resolve():
        identity = {"python": platform.python_version(),
                    "implementation": platform.python_implementation()}
    else:
        with tempfile.TemporaryDirectory(prefix="frutlups-runtime-") as directory:
            capture = Path(directory)
            argv = [executable, "-I", "-S", "-c",
                    "import json,platform;print(json.dumps({'python':platform.python_version(),"
                    "'implementation':platform.python_implementation()}))"]
            token = procs.stdout_observer.set(None)
            try:
                result = procs.run_process(argv, root, gitws.controlled_environment(os.environ), 10,
                                           capture / "stdout", capture / "stderr")
            finally:
                procs.stdout_observer.reset(token)
            if (result.exit != 0 or result.timed_out or result.exception
                    or result.stdout_truncated or result.stderr_truncated):
                raise ValueError("selected project Python could not report its runtime identity")
            try:
                identity = json.loads(result.stdout_path.read_bytes())
            except (ValueError, UnicodeError) as exc:
                raise ValueError("selected project Python returned an invalid runtime identity") from exc
        if (not isinstance(identity, dict) or set(identity) != {"python", "implementation"}
                or not all(isinstance(v, str) and v for v in identity.values())):
            raise ValueError("selected project Python returned an invalid runtime identity")
    declaration = dict(roadmap.runtime) if roadmap is not None else {}
    expected = declaration.get("python")
    if expected and not (identity["python"] == expected or identity["python"].startswith(expected + ".")):
        raise ValueError(f"project Python {expected} required; selected runtime is {identity['python']}")
    identity["source"] = source
    identity["sha"] = hashlib.sha256(json.dumps(
        {"runtime": identity, "declaration": declaration or None},
        ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    return Runtime(executable, MappingProxyType(identity))
