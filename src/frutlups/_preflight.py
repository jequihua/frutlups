"""Read-only project admission, with local authentication probe evidence."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

from . import _loop_evidence as evidence
from . import gitws, ledger, procs, receipt
from . import roadmap as roadmap_module
from .config import Config
from .seats import child_environment


def _probe(root, cfg, name, env):
    seat = cfg.seats[name]
    executable = getattr(cfg, seat.adapter)
    argv = (
        (executable, "auth", "check", "--provider", seat.provider)
        if seat.adapter == "pi"
        else (executable, "auth", "status")
    )
    directory = root / "local_state" / "frutlups" / "jobs" / f"auth-{uuid4().hex}"
    directory.mkdir(parents=True)
    stdout, stderr = directory / "stdout.txt", directory / "stderr.txt"
    stdout.touch()
    stderr.touch()
    result = procs.run_process(argv, root, env, 30, stdout, stderr)
    if result.exit == 0 and not result.timed_out and not result.exception:
        return None
    output = stdout.read_text(encoding="utf-8", errors="replace")
    error_text = stderr.read_text(encoding="utf-8", errors="replace")
    unavailable = any(
        term in (output + error_text).lower()
        for term in ("unknown command", "unrecognized command", "unknown subcommand")
    )
    if seat.adapter == "claude" and unavailable and not result.timed_out and not result.exception:
        stdout, stderr = directory / "probe.json", directory / "probe-stderr.txt"
        stdout.touch()
        stderr.touch()
        argv = (
            executable,
            "-p",
            "--model",
            seat.model,
            "--effort",
            seat.effort,
            "--output-format",
            "json",
            "--permission-mode",
            "dontAsk",
            "--tools",
            "",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--no-session-persistence",
        )
        result = procs.run_process(
            argv, root, env, 30, stdout, stderr, stdin_bytes=b"Reply with OK only."
        )
        try:
            body = json.loads(stdout.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            body = None
        if (
            result.exit == 0
            and not result.timed_out
            and not result.exception
            and isinstance(body, dict)
            and body.get("is_error") is False
            and body.get("result")
        ):
            return None
    tail = stderr.read_bytes() if stderr.is_file() else b""
    detail = receipt._scrub(tail, root, dict(os.environ))
    cause = "timed out" if result.timed_out else result.exception or f"exit {result.exit}"
    return (
        f"authentication check failed: {name}: {cause}; stderr: {detail or '(empty)'}; "
        f"streams: {directory.relative_to(root).as_posix()}"
    )


def prepare(root, cfg=None, roadmap=None):
    """Load each authority once and collect every independently detectable refusal."""
    root = Path(root).resolve()
    errors = []
    try:
        cfg = cfg or Config.load(root)
    except (ValueError, OSError) as exc:
        errors.extend(str(exc).splitlines())
    try:
        roadmap = roadmap or roadmap_module.load(root / "roadmap.yaml")
    except (ValueError, OSError) as exc:
        errors.extend(getattr(exc, "errors", (str(exc),)))
    if (root / "STOP").exists():
        errors.append("STOP exists; the owner must remove it to run")
    try:
        agents = root / "AGENTS.md"
        if not agents.is_file() or agents.stat().st_size > 8192:
            errors.append("AGENTS.md must exist and be at most 8 KB")
    except OSError as exc:
        errors.append(f"AGENTS.md: {exc}")
    events = None
    if cfg is not None:
        try:
            events = ledger.read(root / cfg.ledger)
            if roadmap is not None:
                ledger.fold(events, roadmap)
        except (ValueError, OSError) as exc:
            errors.append(str(exc))
        try:
            if not Path(cfg.git).is_file():
                errors.append("git executable missing")
            elif not gitws.is_repo(root, executable=cfg.git):
                errors.append("root is not a Git repository")
            else:
                gitws.head(root, executable=cfg.git)
                errors.extend(evidence.workspace_errors(root, cfg, events or ()))
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            if str(exc) not in errors:
                errors.append(str(exc))
        if os.name == "nt":
            for name in ("SYSTEMROOT", "COMSPEC", "PATHEXT"):
                if name not in {key.upper() for key in cfg.env_passthrough}:
                    errors.append(f"env_passthrough missing required Windows variable: {name}")
    if cfg is not None and roadmap is not None:
        env = child_environment("verification", cfg, memory_enabled=roadmap.memory is not None)
        active = [m for m in roadmap.milestones if m.status == "active"]
        names = {"coder", "holistic"}
        commands = {roadmap.verification_full[0]}
        for milestone in active:
            for item in milestone.slices:
                names.update(roadmap.review_routing[item.risk])
                if item.verification:
                    commands.add(item.verification[0])
        for command in sorted(commands):
            candidate = str(root / command) if Path(command).parent != Path(".") else command
            if not shutil.which(candidate, path=env["PATH"]):
                errors.append(f"verification executable not resolvable: {Path(command).name}")
        checked = set()
        for name in sorted(names):
            if name not in cfg.seats:
                errors.append(f"missing seat: {name}")
                continue
            seat = cfg.seats[name]
            key = (seat.adapter, seat.provider)
            if key in checked:
                continue
            checked.add(key)
            if not Path(getattr(cfg, seat.adapter)).is_file():
                errors.append(f"seat executable missing: {seat.adapter}")
                continue
            try:
                if error := _probe(root, cfg, name, env):
                    errors.append(error)
            except (OSError, subprocess.SubprocessError) as exc:
                errors.append(f"authentication check failed: {name}: {exc}")
    return (
        cfg,
        roadmap,
        tuple(
            " ".join(receipt.scrub_text(error, root, dict(os.environ)).splitlines())
            for error in errors
        ),
    )


def preflight(root, cfg=None, roadmap=None):
    return prepare(root, cfg, roadmap)[2]
