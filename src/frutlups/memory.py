"""Memory backend interfaces.

The package starts with a disabled backend. `llloom` support belongs in a later
milestone and should remain optional.
"""

from __future__ import annotations

import subprocess as _subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class MemoryStatus:
    """Small memory summary safe to include in status output."""

    enabled: bool
    backend: str
    root: Path | None = None
    message: str = ""
    diagnostics: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "backend": self.backend,
            "root": str(self.root) if self.root is not None else None,
            "message": self.message,
            "diagnostics": list(self.diagnostics),
        }


@runtime_checkable
class MemoryBackend(Protocol):
    """Protocol for optional project memory backends."""

    def status(self) -> MemoryStatus:
        """Return read-only memory health information."""


@dataclass(frozen=True)
class DisabledMemoryBackend:
    """Default backend for projects without configured memory."""

    reason: str = "memory backend disabled"

    def status(self) -> MemoryStatus:
        return MemoryStatus(enabled=False, backend="disabled", message=self.reason)


# ---------------------------------------------------------------------------
# llloom CLI backend (read-only, optional)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryCommandResult:
    """Result of a single llloom CLI command attempt.

    ``returncode`` is None when the executable could not be launched (e.g.
    FileNotFoundError or TimeoutExpired). ``ok`` is False in that case.
    ``error`` carries a human-readable explanation for launcher failures;
    it is empty string when the process ran normally (even if returncode != 0).
    """

    command: tuple[str, ...]
    returncode: int | None
    stdout: str
    stderr: str
    ok: bool
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "command": list(self.command),
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "ok": self.ok,
            "error": self.error,
        }


@runtime_checkable
class MemoryCommandRunner(Protocol):
    """Boundary for executing llloom CLI commands.

    Implementations must not raise; they must return a ``MemoryCommandResult``
    even when the executable is missing or the command times out.
    """

    def run(self, args: tuple[str, ...]) -> MemoryCommandResult: ...


@dataclass(frozen=True)
class SubprocessMemoryCommandRunner:
    """Runs llloom CLI commands as a subprocess.

    All failures (missing executable, timeout, unexpected exception) are
    captured as ``MemoryCommandResult`` values rather than raised exceptions.
    """

    timeout_seconds: float = 10.0

    def run(self, args: tuple[str, ...]) -> MemoryCommandResult:
        try:
            proc = _subprocess.run(
                list(args),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
            return MemoryCommandResult(
                command=args,
                returncode=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                ok=proc.returncode == 0,
            )
        except FileNotFoundError:
            executable = args[0] if args else "?"
            return MemoryCommandResult(
                command=args,
                returncode=None,
                stdout="",
                stderr="",
                ok=False,
                error=f"executable not found: {executable}",
            )
        except _subprocess.TimeoutExpired:
            return MemoryCommandResult(
                command=args,
                returncode=None,
                stdout="",
                stderr="",
                ok=False,
                error=f"command timed out after {self.timeout_seconds}s",
            )
        except Exception as exc:  # noqa: BLE001
            return MemoryCommandResult(
                command=args,
                returncode=None,
                stdout="",
                stderr="",
                ok=False,
                error=str(exc),
            )


@dataclass(frozen=True)
class LlloomCliBackend:
    """Read-only llloom CLI backend.

    All four methods run non-mutating llloom commands through a patchable
    ``runner`` so tests can substitute a fake without installing llloom.
    Mutating commands (seed apply, ingest, render, supersede, unlock,
    reconcile, rebuild) are intentionally absent.
    """

    root: Path
    executable: str = "llloom"
    runner: MemoryCommandRunner = field(default_factory=SubprocessMemoryCommandRunner)

    def _base(self) -> tuple[str, ...]:
        return (self.executable, "--root", str(self.root))

    def status(self) -> MemoryStatus:
        """Run ``llloom status`` and return a MemoryStatus.

        Returns enabled=True always (the root was detected). If the command
        fails or the executable is missing, the message carries the error.
        """
        result = self.runner.run(self._base() + ("status",))
        if result.ok:
            message = result.stdout.strip() or "llloom status ok"
        else:
            message = result.error or result.stderr.strip() or "llloom status failed"
        return MemoryStatus(
            enabled=True,
            backend="llloom",
            root=self.root,
            message=message,
        )

    def doctor(self) -> MemoryCommandResult:
        """Run ``llloom doctor`` (read-only health check)."""
        return self.runner.run(self._base() + ("doctor",))

    def query(self, question: str) -> MemoryCommandResult:
        """Run ``llloom query <question> --status reviewed --verification-status verified``."""
        return self.runner.run(
            self._base()
            + (
                "query",
                question,
                "--status",
                "reviewed",
                "--verification-status",
                "verified",
            )
        )

    def verify(self) -> MemoryCommandResult:
        """Run ``llloom verify`` (read-only verification pass)."""
        return self.runner.run(self._base() + ("verify",))

    def doctor_last_op(self) -> DoctorLastOpEvidence:
        """Run ``llloom doctor --last-op`` and return bounded evidence."""
        return capture_doctor_last_op_evidence(self.root, self.runner, self.executable)


# ---------------------------------------------------------------------------
# doctor --last-op evidence capture (M010-S03)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DoctorLastOpEvidence:
    """Bounded evidence from a ``llloom doctor --last-op`` call.

    All text fields are capped to :data:`_DIAG_MAX_LEN` characters via
    :func:`_summarize` before storage so that large operation reports do
    not bloat status or review artifacts.
    """

    command: tuple[str, ...]
    returncode: int | None
    ok: bool
    stdout_summary: str
    stderr_summary: str

    def to_dict(self) -> dict[str, object]:
        return {
            "command": list(self.command),
            "returncode": self.returncode,
            "ok": self.ok,
            "stdout_summary": self.stdout_summary,
            "stderr_summary": self.stderr_summary,
        }


def capture_doctor_last_op_evidence(
    memory_root: Path,
    runner: MemoryCommandRunner,
    executable: str = "llloom",
) -> DoctorLastOpEvidence:
    """Run ``llloom doctor --last-op`` through a supplied runner and return bounded evidence.

    Gracefully handles missing executable, timeout, nonzero exit, and oversized
    output.  Never raises.
    """
    command: tuple[str, ...] = (executable, "--root", str(memory_root), "doctor", "--last-op")
    result = runner.run(command)
    return DoctorLastOpEvidence(
        command=command,
        returncode=result.returncode,
        ok=result.ok,
        stdout_summary=_summarize(result.stdout.strip()),
        stderr_summary=_summarize(result.error or result.stderr.strip()),
    )


# ---------------------------------------------------------------------------
# Project-level memory detection
# ---------------------------------------------------------------------------

_DIAG_MAX_LEN = 120


def _summarize(text: str, max_len: int = _DIAG_MAX_LEN) -> str:
    """Return the first line of text capped at max_len characters.

    Used to bound llloom command output before it reaches MemoryStatus fields,
    so that large stdout payloads do not appear verbatim in status reports.
    """
    first = text.splitlines()[0] if text else text
    return first[:max_len]


def detect_memory(
    root: Path,
    runner: MemoryCommandRunner | None = None,
) -> MemoryStatus:
    """Detect the default memory root and gather concise read-only diagnostics.

    When no ``07_app/llloom_memory`` directory exists, returns the disabled
    backend status and does not invoke any command runner.

    When the memory root is present, uses :class:`LlloomCliBackend` with the
    supplied ``runner`` (or a fresh :class:`SubprocessMemoryCommandRunner` when
    ``runner`` is ``None``) to call ``llloom status`` and ``llloom doctor``.
    Results are summarised into short diagnostic strings; raw command output is
    not stored verbatim.
    """
    memory_root = root / "07_app" / "llloom_memory"
    if not memory_root.exists():
        return DisabledMemoryBackend().status()

    _runner = runner if runner is not None else SubprocessMemoryCommandRunner()
    backend = LlloomCliBackend(root=memory_root, runner=_runner)

    llloom_status = backend.status()
    doctor_result = backend.doctor()

    diagnostics: list[str] = []
    if doctor_result.ok:
        output = doctor_result.stdout.strip()
        diagnostics.append(f"doctor: {_summarize(output) if output else 'ok'}")
    else:
        msg = doctor_result.error or doctor_result.stderr.strip() or "check failed"
        diagnostics.append(f"doctor: {_summarize(msg)}")

    return MemoryStatus(
        enabled=True,
        backend="llloom",
        root=memory_root,
        message=_summarize(llloom_status.message),
        diagnostics=tuple(diagnostics),
    )


# ---------------------------------------------------------------------------
# Prompt snippet from summarized memory evidence (M009-S04)
# ---------------------------------------------------------------------------

_SNIPPET_MAX_LINES = 5


@dataclass(frozen=True)
class MemoryPromptSnippet:
    """Bounded optional memory context for generated prompts.

    ``lines`` contains up to :data:`_SNIPPET_MAX_LINES` summarized evidence
    lines, each capped at :data:`_DIAG_MAX_LEN` characters.  An empty
    ``lines`` tuple means no useful evidence was found or memory is disabled;
    callers should omit the snippet section from prompt text in that case.
    """

    lines: tuple[str, ...]
    query: str
    source: str = "llloom"

    @property
    def has_content(self) -> bool:
        return bool(self.lines)

    def to_dict(self) -> dict[str, object]:
        return {
            "lines": list(self.lines),
            "query": self.query,
            "source": self.source,
        }


def build_memory_prompt_snippet(
    root: Path,
    query: str,
    runner: MemoryCommandRunner | None = None,
) -> MemoryPromptSnippet:
    """Query memory for reviewed/verified evidence and return a bounded snippet.

    When ``07_app/llloom_memory`` is absent, returns an empty snippet without
    invoking the runner.  When the root exists, calls
    :meth:`LlloomCliBackend.query` through the supplied ``runner`` (or a fresh
    :class:`SubprocessMemoryCommandRunner` when ``None``).  Any failure
    (missing executable, nonzero return, empty output) returns an empty snippet
    without raising.
    """
    memory_root = root / "07_app" / "llloom_memory"
    if not memory_root.exists():
        return MemoryPromptSnippet(lines=(), query=query)

    _runner = runner if runner is not None else SubprocessMemoryCommandRunner()
    backend = LlloomCliBackend(root=memory_root, runner=_runner)
    result = backend.query(query)

    if not result.ok or not result.stdout.strip():
        return MemoryPromptSnippet(lines=(), query=query)

    raw_lines = result.stdout.strip().splitlines()
    summary = tuple(line[:_DIAG_MAX_LEN] for line in raw_lines[:_SNIPPET_MAX_LINES] if line.strip())
    return MemoryPromptSnippet(lines=summary, query=query)


# ---------------------------------------------------------------------------
# Seed-manifest update command planning (M010-S02)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeedManifestUpdatePlan:
    """Typed representation of a llloom seed-manifest update command pair.

    Holds the two command vectors for a seed-manifest update cycle:
    a dry-run pass for inspection and a real apply pass for supervised
    execution.  Both are deterministic tuples of plain strings.

    This is **planning only**.  No command is executed here.  Callers that
    wish to run the commands must pass them to an explicit runner under
    human supervision.
    """

    memory_root: Path
    manifest_path: Path
    executable: str = "llloom"

    @property
    def dry_run_command(self) -> tuple[str, ...]:
        """``llloom --root <root> seed apply <manifest> --dry-run``."""
        return (
            self.executable,
            "--root",
            str(self.memory_root),
            "seed",
            "apply",
            str(self.manifest_path),
            "--dry-run",
        )

    @property
    def apply_command(self) -> tuple[str, ...]:
        """``llloom --root <root> seed apply <manifest>``."""
        return (
            self.executable,
            "--root",
            str(self.memory_root),
            "seed",
            "apply",
            str(self.manifest_path),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "memory_root": str(self.memory_root),
            "manifest_path": str(self.manifest_path),
            "executable": self.executable,
            "dry_run_command": list(self.dry_run_command),
            "apply_command": list(self.apply_command),
        }


def plan_seed_manifest_update(
    memory_root: Path,
    manifest_path: Path,
    executable: str = "llloom",
) -> SeedManifestUpdatePlan:
    """Return a typed plan for a llloom seed-manifest update.

    Pure planning only — no files are created, read, or executed.
    The returned plan carries both the dry-run and apply command vectors
    as deterministic tuples; the caller is responsible for running them
    under human supervision.
    """
    return SeedManifestUpdatePlan(
        memory_root=memory_root,
        manifest_path=manifest_path,
        executable=executable,
    )


# ---------------------------------------------------------------------------
# Accepted-warning visibility (M010-S04)
# ---------------------------------------------------------------------------

_ACCEPTED_WARNINGS_REL = "state/reports/health/accepted_warnings.yaml"
_VAGUE_IDS: frozenset[str] = frozenset({"*", "all", ""})


@dataclass(frozen=True)
class AcceptedWarning:
    """A single entry from ``accepted_warnings.yaml``.

    ``warning_id`` must be an exact ID, not a wildcard.  ``reason`` and
    ``evidence`` must be non-empty.  ``findings`` holds validation messages
    for this entry; callers should surface these rather than silently
    accepting vague or incomplete warnings.
    """

    warning_id: str
    reason: str
    evidence: str
    findings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "warning_id": self.warning_id,
            "reason": self.reason,
            "evidence": self.evidence,
            "findings": list(self.findings),
        }


@dataclass(frozen=True)
class AcceptedWarningsVisibility:
    """Visibility surface for ``accepted_warnings.yaml`` under a memory root.

    ``present`` is ``False`` when the file does not exist; ``warnings`` is
    empty in that case and ``findings`` may contain an informational note.
    When ``present`` is ``True``, ``warnings`` preserves file order and each
    entry carries its own validation findings.  ``findings`` at this level
    captures file-level issues such as parse errors.
    """

    file_path: Path | None
    present: bool
    warnings: tuple[AcceptedWarning, ...]
    findings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "file_path": str(self.file_path) if self.file_path is not None else None,
            "present": self.present,
            "warnings": [w.to_dict() for w in self.warnings],
            "findings": list(self.findings),
        }


def _parse_accepted_warnings_yaml(
    content: str,
) -> tuple[list[dict[str, str]], list[str]]:
    """Parse a minimal ``accepted_warnings.yaml`` subset.

    Returns ``(entries, file_findings)`` where ``entries`` is a list of
    raw string-keyed dicts and ``file_findings`` captures parse-level
    issues.  Never raises; malformed lines are skipped.

    Visible findings are produced for unsupported shapes:
    - ``warnings: some-scalar`` (non-list value on the same line)
    - ``warnings:`` block whose indented lines are mapping keys, not list items
    """
    entries: list[dict[str, str]] = []
    file_findings: list[str] = []
    current: dict[str, str] | None = None
    in_warnings_block = False
    found_warnings_key = False
    warnings_inline_value = ""  # text after "warnings:" on its line, stripped
    found_list_item = False
    found_mapping_line = False  # indented non-list-item line under warnings block

    for line in content.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue

        # Top-level key
        if not line[0].isspace():
            stripped = line.strip()
            if stripped.startswith("warnings:"):
                found_warnings_key = True
                warnings_inline_value = stripped[len("warnings:") :].strip()
                in_warnings_block = True
            else:
                in_warnings_block = False
            continue

        if not in_warnings_block:
            continue

        # List-item line (starts with exactly two spaces then "- ")
        if line.startswith("  - "):
            if current is not None:
                entries.append(current)
            current = {}
            found_list_item = True
            rest = line[4:].strip()
            if rest and ":" in rest:
                key, _, value = rest.partition(":")
                current[key.strip()] = value.strip()
            continue

        # Key-value under current item (four or more leading spaces)
        if current is not None and len(line) > 4 and line[:4] == "    ":
            stripped = line.strip()
            if ":" in stripped:
                key, _, value = stripped.partition(":")
                current[key.strip()] = value.strip()
            continue

        # Indented line that is NOT a list item and NOT a sub-key of a current item
        # — indicates a mapping directly under warnings: instead of a list
        if line.startswith("  "):
            found_mapping_line = True

    if current is not None:
        entries.append(current)

    # Produce findings for unsupported shapes
    if not found_warnings_key:
        file_findings.append("no 'warnings' key found in accepted_warnings.yaml")
    elif warnings_inline_value and warnings_inline_value != "[]":
        file_findings.append(
            f"unsupported 'warnings' value {warnings_inline_value!r}; "
            "expected a YAML list or empty list '[]'"
        )
    elif (
        found_warnings_key
        and not warnings_inline_value
        and not found_list_item
        and found_mapping_line
    ):
        file_findings.append(
            "unsupported 'warnings' shape: expected list items starting with "
            "'  - ', found mapping keys; check accepted_warnings.yaml format"
        )

    return entries, file_findings


def _validate_warning_entry(raw: dict[str, str]) -> AcceptedWarning:
    """Convert a raw parsed dict to ``AcceptedWarning`` with validation findings."""
    findings: list[str] = []

    warning_id = raw.get("warning_id", "")
    reason = raw.get("reason", "")
    evidence = raw.get("evidence", "")

    # Strip surrounding YAML quotes before vague-ID check
    bare_id = warning_id.strip().strip("\"'").strip().lower()
    if not warning_id or not bare_id:
        findings.append("accepted warning is missing warning_id")
    elif bare_id in _VAGUE_IDS:
        findings.append(f"accepted warning has vague warning_id {warning_id!r}; use an exact ID")

    if not reason:
        findings.append("accepted warning is missing reason")

    if not evidence:
        findings.append("accepted warning is missing evidence")

    return AcceptedWarning(
        warning_id=warning_id,
        reason=reason,
        evidence=evidence,
        findings=tuple(findings),
    )


def read_accepted_warnings(memory_root: Path) -> AcceptedWarningsVisibility:
    """Read and validate ``accepted_warnings.yaml`` under a memory root.

    Returns an ``AcceptedWarningsVisibility`` in all cases.  When the file
    is absent the result has ``present=False`` and an empty ``warnings``
    tuple.  Never raises.
    """
    yaml_path = memory_root / _ACCEPTED_WARNINGS_REL

    if not yaml_path.is_file():
        return AcceptedWarningsVisibility(
            file_path=yaml_path,
            present=False,
            warnings=(),
            findings=("accepted_warnings.yaml not present",),
        )

    try:
        content = yaml_path.read_text(encoding="utf-8")
    except OSError as exc:
        return AcceptedWarningsVisibility(
            file_path=yaml_path,
            present=False,
            warnings=(),
            findings=(f"could not read accepted_warnings.yaml: {exc}",),
        )

    raw_entries, file_findings = _parse_accepted_warnings_yaml(content)
    warnings = tuple(_validate_warning_entry(e) for e in raw_entries)

    return AcceptedWarningsVisibility(
        file_path=yaml_path,
        present=True,
        warnings=warnings,
        findings=tuple(file_findings),
    )
