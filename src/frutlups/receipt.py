"""Run verification commands and create portable receipts."""

from __future__ import annotations

import json
import hashlib
import math
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from .config import Config
from .gitws import status, workspace_witness
from .procs import run_process, stdout_observer
from .ledger import evidence_sha, fold, read
from .roadmap import Roadmap, Slice
from .seats import child_environment
from . import runtime


@dataclass(frozen=True)
class NativeReceipt:
    schema: str
    slice: str
    round: int
    t: str
    base_commit: str
    tree_dirty_before: bool
    commands: tuple
    tree_clean_after: bool
    ok: bool
    manifest: dict
    witness: dict
    runtime: dict
    observation: str


def native_runtime(root, roadmap, frozen, config):
    from ._attempts import _digest
    selected = runtime.resolve_runtime(root, replace(roadmap, runtime=frozen["runtime"]), git=config.git)
    identity = {k: v for k, v in selected.identity.items() if k != "sha"}
    identity["sha"] = _digest(dict(runtime=identity, declaration=frozen["runtime"]))
    return runtime.Runtime(selected.executable, identity)


def native_snapshot(root, config):
    """Project the safe raw witness into the existing template hash domain."""
    import stat
    from . import gitws
    witness = workspace_witness(root, executable=config.git)
    files = {}
    for path, identity in witness["files"].items():
        if identity[0] not in ("file", "missing"):
            raise ValueError("native verification requires regular Git-owned paths")
        files[path] = (dict(kind="file", mode=stat.S_IMODE(identity[2][2]), sha=identity[3])
                       if identity[0] == "file" else dict(kind="missing"))
    index = gitws._git(root, "ls-files", "--stage", "-v", "-z", executable=config.git).stdout
    return dict(head=witness["head"], index=hashlib.sha256(index).hexdigest(), files=files)


def native_product(root, config, snapshot, events):
    from ._attempts import _digest
    from ._loop_evidence import evidence_paths
    excluded = evidence_paths(events, root) | {config.ledger, "05_governance/backlog.md", "roadmap.yaml", "docs/roadmap.md"}
    excluded -= {r["path"] for e in events for r in e.data.get("changed", ())}
    return _digest({p: v for p, v in snapshot["files"].items() if p not in excluded})


def validate_native(root, value, current, frozen):
    """Validate the existing wire contract for success and failure receipts."""
    from . import ledger
    from ._attempts import _digest
    ledger._need(isinstance(value, dict) and set(value) == set(NativeReceipt.__dataclass_fields__),
                 "invalid native receipt fields")
    ledger._need(value["schema"] == "frutlups.receipt/2" and value["slice"] == current.id
                 and type(value["round"]) is int and value["round"] == current.round
                 and value["manifest"] == ledger._thaw(current.manifest), "native receipt identity mismatch")
    ledger._need(isinstance(value["base_commit"], str) and re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", value["base_commit"])
                 and isinstance(value["t"], str) and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value["t"])
                 and all(type(value[k]) is bool for k in ("ok", "tree_dirty_before", "tree_clean_after")),
                 "invalid native receipt metadata")
    w, commands, selected = value["witness"], value["commands"], value["runtime"]
    ledger._need(isinstance(w, dict) and set(w) == {"before", "after", "stable", "head", "index", "product"}
                 and all(ledger.digest(w[k]) for k in ("before", "after", "index", "product"))
                 and w["head"] == value["base_commit"] and type(w["stable"]) is bool
                 and w["stable"] == (w["before"] == w["after"]), "native receipt witness contradiction")
    ledger._need(isinstance(commands, list) and len(commands) == 1, "native receipt needs one full command")
    command = commands[0]
    ledger._need(isinstance(command, dict) and set(command) == set(CommandResult.__dataclass_fields__)
                 and command["label"] == "full" and command["argv"] == list(_public_argv(frozen["full"], root))
                 and (command["exit"] is None or type(command["exit"]) is int)
                 and type(command["timed_out"]) is bool
                 and type(command["secs"]) in (int, float) and math.isfinite(command["secs"]) and command["secs"] >= 0
                 and all(isinstance(command[k], str) and len(command[k].encode()) <= 4096
                         for k in ("stdout_tail", "stderr_tail")), "invalid native receipt command")
    ledger._need(value["ok"] == (command["exit"] == 0 and not command["timed_out"] and w["stable"]),
                 "native receipt result contradiction")
    ledger._need(value["observation"] == frozen["observation"] and isinstance(selected, dict)
                 and set(selected) == {"python", "implementation", "source", "sha"}
                 and isinstance(selected["python"], str) and re.fullmatch(r"3\.\d+\.\d+[A-Za-z0-9.+-]*", selected["python"])
                 and isinstance(selected["implementation"], str) and re.fullmatch(r"[A-Za-z0-9_. -]{1,80}", selected["implementation"])
                 and selected["source"] in ("invoking_python", "project.local")
                 and selected["sha"] == _digest(dict(runtime={k: v for k, v in selected.items() if k != "sha"},
                                                      declaration=frozen["runtime"])), "native receipt runtime mismatch")
    version = frozen["runtime"].get("python")
    ledger._need(not version or selected["python"] == version or selected["python"].startswith(version + "."),
                 "native receipt runtime version mismatch")


def _run_native(root, item, round_no, roadmap, config, selected, deadline):
    from . import ledger
    from ._attempts import _digest
    events = read(root / config.ledger)
    current = ledger.validate_history(root, events, roadmap, executable=config.git).slices[item.id]
    frozen = ledger.envelope(root, ledger._thaw(current.envelope), item.id, round_no)
    resolved = native_runtime(root, roadmap, frozen, config)
    if selected is not None and (selected.executable != resolved.executable or dict(selected.identity) != resolved.identity):
        raise ValueError("native verification runtime drift")
    before_status = status(root, executable=config.git)
    before = native_snapshot(root, config)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError("native verification deadline exhausted before launch")
    env = child_environment("verification", config, memory_enabled=roadmap.memory is not None)
    command = _execute("full", tuple(runtime.runtime_argv(frozen["full"], resolved.executable)), root, env, remaining,
                       native=True)
    command = replace(command, argv=_public_argv(frozen["full"], root))
    after_status = status(root, executable=config.git)
    after = native_snapshot(root, config)
    # The native grammar cannot truthfully encode an exit-zero stable overrun as
    # ok=false. Refuse completion, preserving the unresolved reservation.
    if command.exit == 0 and not command.timed_out and time.monotonic() > deadline:
        raise ValueError("native verification exceeded its deadline; completion refused")
    return NativeReceipt("frutlups.receipt/2", item.id, round_no,
        datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), before["head"], bool(before_status), (command,),
        not bool(after_status), command.exit == 0 and not command.timed_out and before == after,
        ledger._thaw(current.manifest), dict(before=_digest(before), after=_digest(after), stable=before == after,
            head=before["head"], index=before["index"], product=native_product(root, config, before, events)),
        dict(resolved.identity), frozen["observation"])

_LAST_COMPONENT = r"(?:[^\\/\r\n|()<>\"'`]*?\.[a-z0-9]{1,12}(?=\s|[):,;]|$)|[^\r\n|();,<>\"'`\\/]+)"
ABSOLUTE_PATH = re.compile(
    r"(?i)(?:"
    r"[a-z]:[\\/](?:[^\\/\r\n|()\[\]<>\"'`]+[\\/])*"
    r"|\\\\(?:[^\\/\r\n|()\[\]<>\"'`]+[\\/])+"
    r"|(?<![:/])//(?:[^/\r\n|()<>\"'`]+/)+"
    r"|(?<![\w:/.>])/(?!/)(?=[\w.~])(?:[^\\/\r\n|()\[\]<>\"'`]+[\\/])*"
    r")" + _LAST_COMPONENT
)
QUOTED_PATH = re.compile(r"([\"'`])((?:[a-zA-Z]:[\\/]|/(?!/)|\\\\|file://)[^\r\n]*?)\1")
FILE_URL = re.compile(r"\bfile://[^;\r\n|()<>\"'`]+", re.IGNORECASE)
PROTECTED = re.compile(r"https?://[^\s|()\[\]<>\"'`]+|(?<!\w)r?([\"'])\^[^\r\n]*?\$\1")

OMITTED = "[Earlier diagnostic output omitted.]\n"


@dataclass(frozen=True)
class CommandResult:
    label: str
    argv: tuple[str, ...]
    exit: int | None
    secs: float
    stdout_tail: str
    stderr_tail: str
    timed_out: bool


def _tail(value: bytes, omitted: bool = False) -> str:
    if len(value) > 4096:
        omitted = True
    if omitted:
        allowance = 4096 - len(OMITTED.encode("utf-8"))
        if len(value) > allowance:
            value = value[-allowance:].partition(b"\n")[2]
        return OMITTED + value.decode("utf-8", "strict")
    return value.decode("utf-8", "strict")


def scrub_text(text: str, root: Path, env: dict[str, str]) -> str:
    """Scrub before limiting text, preserving report and Markdown grammar."""
    resolved = root.resolve()
    for form in sorted({str(resolved), resolved.as_posix()}, key=len, reverse=True):
        text = re.sub(re.escape(form), "<repo>", text, flags=re.IGNORECASE)
    secrets = set()
    for key, secret in env.items():
        if secret and any(word in key.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
            secrets.add(secret)
            secrets.update(part for part in secret.splitlines() if part)
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, "<redacted>")

    def paths(value):
        value = QUOTED_PATH.sub(
            lambda m: m[1] + "<absolute-path>" + m[1] if len(m[2]) > 1 else m[0], value,
        )
        value = FILE_URL.sub(lambda m: "<absolute-path>" + m[0][len(m[0].rstrip()):], value)
        return ABSOLUTE_PATH.sub(lambda m: "<absolute-path>" + m[0][len(m[0].rstrip()):], value)

    # Redact secrets first, including those embedded in otherwise preserved URLs.
    parts, offset = [], 0
    for match in PROTECTED.finditer(text):
        parts.extend((paths(text[offset:match.start()]), match[0]))
        offset = match.end()
    parts.append(paths(text[offset:]))
    return "".join(parts)


def _scrub(value: bytes, root: Path, env: dict[str, str], omitted: bool = False) -> str:
    text = scrub_text(value.decode("utf-8", "replace"), root, env)
    return _tail(text.encode("utf-8"), omitted)


def _public_argv(argv: tuple[str, ...], root: Path, env=None) -> tuple[str, ...]:
    output = []
    env = dict(os.environ) if env is None else env
    for value in argv:
        value = scrub_text(value, root, env)
        # Keep the legacy public spelling for the executable outside the project.
        if value == "<absolute-path>":
            value = "<outside-repo>"
        output.append(value)
    return tuple(output)


def _execute(
    label: str, argv: tuple[str, ...], root: Path, env: dict[str, str], timeout: float,
    *, native=True,
) -> CommandResult:
    started = time.monotonic()
    exit_code, timed_out, stdout, stderr = None, False, b"", b""
    omitted_out = omitted_err = False
    public_env = {**os.environ, **env}
    try:
        with tempfile.TemporaryDirectory(prefix="frutlups-verification-") as directory:
            folder = Path(directory)
            token = stdout_observer.set(None)
            try:
                result = run_process(argv, root, env, timeout, folder / "out", folder / "err",
                                     tail_limit=16384)
            finally:
                stdout_observer.reset(token)
            timed_out = result.timed_out
            exit_code = None if timed_out or result.exception else result.exit
            stdout = result.stdout_path.read_bytes() if result.stdout_path.exists() else b""
            stderr = result.stderr_path.read_bytes() if result.stderr_path.exists() else b""
            omitted_out, omitted_err = result.stdout_truncated, result.stderr_truncated
            if result.exception:
                raise ValueError("native verifier disposition unresolved: " +
                                 _scrub(result.exception.encode(), root, public_env))
            if timed_out:
                stderr += b"\nverification timed out"
    except OSError as exc:
        stderr = f"{type(exc).__name__}: {exc}".encode("utf-8", "replace")
    seconds = round(time.monotonic() - started, 3)
    return CommandResult(
        label, _public_argv(argv, root, public_env), exit_code, seconds,
        _scrub(stdout, root, public_env, omitted_out),
        _scrub(stderr, root, public_env, omitted_err), timed_out,
    )


def run(
    root: Path,
    item: Slice,
    round: int,
    roadmap: Roadmap,
    config: Config,
    focused: bool,
    *, selected_runtime=None, timeout=None,
) -> NativeReceipt:
    if roadmap.schema != "frutlups.roadmap/2":
        raise ValueError("unsupported roadmap schema; use native /2 or pinned v4")
    deadline = time.monotonic() + (config.timeouts.verification_seconds if timeout is None else timeout)
    return _run_native(root, item, round, roadmap, config, selected_runtime, deadline)


def write(receipt: NativeReceipt, path: Path) -> str:
    if not isinstance(receipt, NativeReceipt) or receipt.schema != "frutlups.receipt/2":
        raise ValueError("unsupported receipt schema; expected frutlups.receipt/2")
    data = json.dumps(asdict(receipt), ensure_ascii=False, separators=(",", ":")) + "\n"
    if len(data.encode("utf-8")) > 128 * 1024:
        raise ValueError("native receipt exceeds 128 KiB")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return evidence_sha(data.encode("utf-8"))
