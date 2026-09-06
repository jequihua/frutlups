"""Run verification commands and create portable receipts."""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from .config import Config
from .gitws import head, status
from .ledger import evidence_sha, fold, read
from .roadmap import Roadmap, Slice
from .seats import child_environment

ABSOLUTE_PATH = re.compile(
    r"(?i)(?<![\w:/\\>])(?:[a-z]:[\\/]|\\\\|/)[^\s<>\"'`|()\[\]]+"
)
QUOTED_PATH = re.compile(
    r"""(?i)(?P<quote>['"`])(?:[a-z]:[\\/]|\\\\|/)[^\r\n]*?(?P=quote)"""
)


@dataclass(frozen=True)
class CommandResult:
    label: str
    argv: tuple[str, ...]
    exit: int | None
    secs: float
    stdout_tail: str
    stderr_tail: str
    timed_out: bool


@dataclass(frozen=True)
class ReceiptChange:
    path: str
    sha: str
    kind: str


@dataclass(frozen=True)
class Receipt:
    schema: str
    slice: str
    round: int
    t: str
    base_commit: str
    tree_dirty_before: bool
    commands: tuple[CommandResult, ...]
    changed_files: tuple[ReceiptChange, ...]
    tree_clean_after: bool
    ok: bool


def _tail(value: bytes) -> str:
    return value[-4096:].decode("utf-8", "replace")


def scrub_text(text: str, root: Path, env: dict[str, str]) -> str:
    """Remove local paths and known secrets without truncating evidence text."""
    resolved = root.resolve()
    for form in {str(resolved), resolved.as_posix()}:
        text = re.sub(re.escape(form), "<repo>", text, flags=re.IGNORECASE)
    for key, secret in env.items():
        sensitive = any(word in key.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
        if secret and len(secret) >= 4 and sensitive:
            text = text.replace(secret, "<redacted>")
    text = QUOTED_PATH.sub(lambda match: match["quote"] + "<absolute-path>" + match["quote"], text)
    return ABSOLUTE_PATH.sub("<absolute-path>", text)


def _scrub(value: bytes, root: Path, env: dict[str, str]) -> str:
    scrubbed = scrub_text(_tail(value), root, env)
    return scrubbed.encode("utf-8")[-4096:].decode("utf-8", "ignore")


def _public_argv(argv: tuple[str, ...], root: Path) -> tuple[str, ...]:
    output = []
    for value in argv:
        try:
            path = Path(value)
            if path.is_absolute():
                try:
                    value = path.resolve().relative_to(root.resolve()).as_posix()
                except ValueError:
                    value = "<outside-repo>"
        except (OSError, ValueError):
            pass
        output.append(ABSOLUTE_PATH.sub("<outside-repo>", value))
    return tuple(output)


def _recorded_changes(
    root: Path, item: Slice, roadmap: Roadmap, config: Config,
) -> tuple[ReceiptChange, ...]:
    state = fold(read(root / config.ledger), roadmap).slices[item.id]
    return tuple(
        ReceiptChange(str(row["path"]), str(row["sha"]), str(row["kind"]))
        for row in state.changed
    )


def _execute(
    label: str, argv: tuple[str, ...], root: Path, env: dict[str, str], timeout: float,
) -> CommandResult:
    started = time.monotonic()
    exit_code, timed_out, stdout, stderr = None, False, b"", b""
    try:
        result = subprocess.run(
            argv,
            cwd=root,
            env=env,
            capture_output=True,
            timeout=timeout,
            shell=False,
            check=False,
        )
        exit_code, stdout, stderr = result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = exc.stdout or b""
        stderr = (exc.stderr or b"") + b"\nverification timed out"
    except OSError as exc:
        stderr = f"{type(exc).__name__}: {exc}".encode("utf-8", "replace")
    seconds = round(time.monotonic() - started, 3)
    return CommandResult(
        label, _public_argv(argv, root), exit_code, seconds, _scrub(stdout, root, env),
        _scrub(stderr, root, env), timed_out,
    )


def run(
    root: Path,
    item: Slice,
    round: int,
    roadmap: Roadmap,
    config: Config,
    focused: bool,
) -> Receipt:
    before = status(root, executable=config.git)
    commands = []
    if focused:
        selected = item.focused or roadmap.focused_default
        commands.extend(("focused", tuple(argv)) for argv in selected)
    full = item.verification or roadmap.verification_full
    commands.append(("full", tuple(full)))
    env = child_environment("verification", config, memory_enabled=roadmap.memory is not None)
    results = []
    timeout = config.timeouts.verification_seconds
    for label, argv in commands:
        result = _execute(label, argv, root, env, timeout)
        results.append(result)
        if result.exit != 0 or result.timed_out:
            break
    after = status(root, executable=config.git)
    ok = all(result.exit == 0 and not result.timed_out for result in results) and before == after
    now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    return Receipt(
        "frutlups.receipt/1", item.id, round, now, head(root, executable=config.git),
        bool(before), tuple(results), _recorded_changes(root, item, roadmap, config),
        not bool(after), ok,
    )


def write(receipt: Receipt, path: Path) -> str:
    data = json.dumps(asdict(receipt), ensure_ascii=False, separators=(",", ":")) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(data)
    return evidence_sha(data.encode("utf-8"))
