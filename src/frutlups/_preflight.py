"""Read-only project admission, with local model probe evidence."""

import hashlib
import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

from . import _loop_evidence as evidence
from . import gitws, ledger, receipt
from . import roadmap as roadmap_module
from .config import Config
from .seats import Job, Role, child_environment
from .seats import claude, pi


def _probe(root, cfg, name, memory_enabled=False):
    seat = cfg.seats[name]
    adapter = pi.PiSeat() if seat.adapter == "pi" else claude.ClaudeSeat()
    tools = pi.READ_TOOLS if seat.adapter == "pi" else claude.READ_TOOLS
    job_id = f"probe-{uuid4().hex}"
    directory = root / "local_state" / "frutlups" / "jobs" / job_id
    directory.mkdir(parents=True)
    prompt = b"Reply with OK only.\n"
    prompt_path = directory / "prompt.md"
    prompt_path.write_bytes(prompt)
    job = Job(
        id=job_id, role=Role.reviewer, seat_name=name, adapter=seat.adapter,
        provider=seat.provider, model=seat.model, effort=seat.effort,
        prompt_path=prompt_path, prompt_sha=hashlib.sha256(prompt).hexdigest(),
        cwd=root, tools=tools, timeout=60, expected_notes_path=None,
        memory_enabled=memory_enabled,
    )
    result = adapter.run(job, cfg)
    if result.status == "completed":
        shutil.rmtree(directory)
        return None
    tail = result.stderr_path.read_bytes() if result.stderr_path.is_file() else b""
    detail = receipt._scrub(tail, root, dict(os.environ))
    return (
        f"model probe failed: {name}: adapter={seat.adapter} provider={seat.provider} "
        f"model={seat.model}; failure={result.failure_class}; "
        f"diagnostic: {result.diagnostic or '(empty)'}; stderr: {detail or '(empty)'}; "
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
            key = (seat.adapter, seat.provider, seat.model)
            if key in checked:
                continue
            checked.add(key)
            if not Path(getattr(cfg, seat.adapter)).is_file():
                errors.append(f"seat executable missing: {seat.adapter}")
                continue
            try:
                if error := _probe(root, cfg, name, roadmap.memory is not None):
                    errors.append(error)
            except (OSError, subprocess.SubprocessError) as exc:
                errors.append(f"model probe failed: {name}: {exc}")
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
