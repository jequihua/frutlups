"""Persistent run journal and resume behavior for the local orchestrator (M016-S04).

Each ``orchestrator-run`` invocation appends one durable, reviewable entry to a
local repository artifact (JSON Lines) so a later invocation, another agent, or a
human can see what happened and resume safely. The journal is *evidence for
resume*, never the source of truth: live repository artifacts remain
authoritative for advancing the loop.

Journal contract (also documented in the self-report):

- ``orchestrator-run`` (execute / dry-run / refuse) appends exactly one entry per
  invocation. The ``dry_run`` and ``refuse`` kinds write a journal entry but no
  prompt/review/verdict artifact.
- ``orchestrator-plan`` stays fully read-only: it never writes a journal entry; it
  only *reads* the journal to produce a resume summary.

Storage format: one JSON object per line at
``05_governance/orchestrator/run_journal.jsonl`` under the project (template)
root. Append-only in normal operation. Malformed lines are skipped (counted, not
fatal) so a corrupted journal never crashes status/resume.

This module depends only on the standard library and ``frutlups.project`` /
``frutlups.layout``; it must not import ``frutlups.orchestrator`` (which imports
this module), so the ``OrchestratorRunResult`` annotation is deferred.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from frutlups.project import (
    LoopResumeStatus,
    _build_status_with_evidence,
    _loop_resume_with_verdict_and_evidence,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle
    from frutlups.orchestrator import OrchestratorRunResult

JOURNAL_REL_PATH = "05_governance/orchestrator/run_journal.jsonl"
"""Default repo-relative path of the orchestrator run journal."""


class OrchestratorEventKind(StrEnum):
    """Kind of orchestrator-run event recorded in the journal."""

    DRY_RUN = "dry_run"  # advisory run; no prompt/review/verdict artifact written
    EXECUTE = "execute"  # a safe artifact command was executed
    REFUSE = "refuse"  # the step was refused (unsafe/ambiguous/no-frontier)


def now_timestamp() -> str:
    """Return a stable ISO-like UTC timestamp (seconds precision, ``Z`` suffix)."""

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class RunJournalEntry:
    """A single durable orchestrator-run journal record.

    All fields are plain JSON-safe scalars (or a string tuple) so the entry
    serializes deterministically to one JSON Lines row.
    """

    timestamp: str
    event_kind: str
    loop_step: str
    actor: str
    frontier_slice_id: str
    frontier_slice_title: str
    recommended_command: str
    safe_for_auto_execution: bool
    attempted: bool
    wrote: bool
    artifact_path: str
    refused: bool
    refusal_reason: str
    diagnostics: tuple[str, ...] = field(default=())
    layout_profile_id: str = ""
    layout_config_path: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "timestamp": self.timestamp,
            "event_kind": self.event_kind,
            "loop_step": self.loop_step,
            "actor": self.actor,
            "frontier_slice_id": self.frontier_slice_id,
            "frontier_slice_title": self.frontier_slice_title,
            "recommended_command": self.recommended_command,
            "safe_for_auto_execution": self.safe_for_auto_execution,
            "attempted": self.attempted,
            "wrote": self.wrote,
            "artifact_path": self.artifact_path,
            "refused": self.refused,
            "refusal_reason": self.refusal_reason,
            "diagnostics": list(self.diagnostics),
            "layout_profile_id": self.layout_profile_id,
            "layout_config_path": self.layout_config_path,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> RunJournalEntry:
        """Build an entry from a parsed JSON object, coercing field types.

        Missing keys fall back to empty/false defaults so older or partial
        entries still parse. Raises :class:`ValueError` only when ``data`` is not
        a mapping (callers treat that as a malformed line).
        """

        if not isinstance(data, dict):
            raise ValueError("journal entry must be a JSON object")

        def _str(key: str) -> str:
            value = data.get(key, "")
            return value if isinstance(value, str) else str(value) if value is not None else ""

        def _bool(key: str) -> bool:
            return bool(data.get(key, False))

        diags_raw = data.get("diagnostics", [])
        diagnostics = (
            tuple(str(d) for d in diags_raw) if isinstance(diags_raw, (list, tuple)) else ()
        )
        return cls(
            timestamp=_str("timestamp"),
            event_kind=_str("event_kind"),
            loop_step=_str("loop_step"),
            actor=_str("actor"),
            frontier_slice_id=_str("frontier_slice_id"),
            frontier_slice_title=_str("frontier_slice_title"),
            recommended_command=_str("recommended_command"),
            safe_for_auto_execution=_bool("safe_for_auto_execution"),
            attempted=_bool("attempted"),
            wrote=_bool("wrote"),
            artifact_path=_str("artifact_path"),
            refused=_bool("refused"),
            refusal_reason=_str("refusal_reason"),
            diagnostics=diagnostics,
            layout_profile_id=_str("layout_profile_id"),
            layout_config_path=_str("layout_config_path"),
        )


@dataclass(frozen=True)
class RunJournalReadResult:
    """Result of reading the run journal file.

    ``entries`` are the successfully parsed entries in file (chronological)
    order. ``malformed_count`` counts non-JSON or non-object lines that were
    skipped. ``exists`` is ``False`` when no journal file is present yet.
    """

    path: str
    exists: bool
    entries: tuple[RunJournalEntry, ...]
    malformed_count: int
    errors: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "exists": self.exists,
            "entries": [e.to_dict() for e in self.entries],
            "malformed_count": self.malformed_count,
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class RunJournalResumeSummary:
    """Summary of the latest journal entry against current live resume status.

    Live repository artifacts are authoritative; ``recommended_next_command`` and
    ``live_loop_step`` come from the live :class:`LoopResumeStatus`, while
    ``latest_*`` come from the journal. ``stale`` is ``True`` when the most recent
    journal entry observed a different loop step than the live status, signalling
    the journal no longer matches reality.
    """

    has_journal: bool
    entry_count: int
    malformed_count: int
    latest: RunJournalEntry | None
    latest_event_kind: str
    latest_observed_step: str
    latest_wrote: bool
    live_loop_step: str
    recommended_next_command: str
    stale: bool
    message: str

    def to_dict(self) -> dict[str, object]:
        return {
            "has_journal": self.has_journal,
            "entry_count": self.entry_count,
            "malformed_count": self.malformed_count,
            "latest": self.latest.to_dict() if self.latest is not None else None,
            "latest_event_kind": self.latest_event_kind,
            "latest_observed_step": self.latest_observed_step,
            "latest_wrote": self.latest_wrote,
            "live_loop_step": self.live_loop_step,
            "recommended_next_command": self.recommended_next_command,
            "stale": self.stale,
            "message": self.message,
        }


def journal_path_for(root: Path) -> Path:
    """Return the journal file path under ``root`` (the project/template root)."""

    return root / PurePosixPath(JOURNAL_REL_PATH)


def build_run_journal_entry(
    result: OrchestratorRunResult,
    *,
    timestamp: str,
    layout_profile_id: str = "",
    layout_config_path: str = "",
) -> RunJournalEntry:
    """Build a :class:`RunJournalEntry` from an orchestrator run result.

    Pure: derives the event kind from the result (``dry_run`` > ``refuse`` >
    ``execute``) and copies the plan/outcome fields. Never raises.
    """

    plan = result.plan
    if result.dry_run:
        kind = OrchestratorEventKind.DRY_RUN
    elif result.refused:
        kind = OrchestratorEventKind.REFUSE
    elif result.attempted:
        kind = OrchestratorEventKind.EXECUTE
    else:
        kind = OrchestratorEventKind.REFUSE

    actor = plan.actor.value if hasattr(plan.actor, "value") else str(plan.actor)
    return RunJournalEntry(
        timestamp=timestamp,
        event_kind=kind.value,
        loop_step=plan.loop_step,
        actor=actor,
        frontier_slice_id=plan.frontier_slice_id,
        frontier_slice_title=plan.frontier_slice_title,
        recommended_command=plan.recommended_command,
        safe_for_auto_execution=plan.safe_for_auto_execution,
        attempted=result.attempted,
        wrote=result.wrote,
        artifact_path=result.artifact_path,
        refused=result.refused,
        refusal_reason=result.refusal_reason,
        diagnostics=tuple(result.diagnostics),
        layout_profile_id=layout_profile_id,
        layout_config_path=layout_config_path,
    )


def append_run_journal_entry(path: Path, entry: RunJournalEntry) -> bool:
    """Append one entry to the journal at ``path`` (append-only). Never raises.

    Creates the parent directory if needed and writes one deterministic JSON Lines
    row. Returns ``True`` on success, ``False`` if the write failed (the caller may
    surface the failure as a diagnostic; journaling must never crash a run).
    """

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry.to_dict(), sort_keys=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return True
    except OSError:
        return False


def read_run_journal(path: Path) -> RunJournalReadResult:
    """Read and parse the journal at ``path``. Never raises.

    Skips blank lines and malformed (non-JSON or non-object) lines, counting them
    in ``malformed_count`` so a corrupted journal degrades gracefully.
    """

    path_str = str(path)
    if not path.is_file():
        return RunJournalReadResult(path=path_str, exists=False, entries=(), malformed_count=0)

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return RunJournalReadResult(
            path=path_str,
            exists=True,
            entries=(),
            malformed_count=0,
            errors=(f"could not read journal: {exc}",),
        )

    entries: list[RunJournalEntry] = []
    malformed = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            data = json.loads(stripped)
            entries.append(RunJournalEntry.from_dict(data))
        except (json.JSONDecodeError, ValueError):
            malformed += 1
    return RunJournalReadResult(
        path=path_str,
        exists=True,
        entries=tuple(entries),
        malformed_count=malformed,
    )


def summarize_run_journal(
    read: RunJournalReadResult,
    live: LoopResumeStatus,
) -> RunJournalResumeSummary:
    """Summarize the latest journal entry against live resume status. Never raises."""

    latest = read.entries[-1] if read.entries else None
    live_step = live.step.value if hasattr(live.step, "value") else str(live.step)
    recommended = live.next_command or ""

    if latest is None:
        message = f"no orchestrator run journaled yet; live loop step is {live_step!r}" + (
            f"; next: {recommended}" if recommended else ""
        )
        return RunJournalResumeSummary(
            has_journal=read.exists,
            entry_count=0,
            malformed_count=read.malformed_count,
            latest=None,
            latest_event_kind="",
            latest_observed_step="",
            latest_wrote=False,
            live_loop_step=live_step,
            recommended_next_command=recommended,
            stale=False,
            message=message,
        )

    stale = latest.loop_step != live_step
    state = (
        "wrote an artifact"
        if latest.wrote
        else (
            "dry-run only"
            if latest.event_kind == OrchestratorEventKind.DRY_RUN.value
            else "refused"
        )
    )
    drift = " (stale: live loop step has since changed)" if stale else ""
    message = (
        f"last run at {latest.timestamp} {state}; it observed loop step "
        f"{latest.loop_step!r}; live loop step is {live_step!r}{drift}"
        + (f"; next: {recommended}" if recommended else "")
    )
    return RunJournalResumeSummary(
        has_journal=read.exists,
        entry_count=len(read.entries),
        malformed_count=read.malformed_count,
        latest=latest,
        latest_event_kind=latest.event_kind,
        latest_observed_step=latest.loop_step,
        latest_wrote=latest.wrote,
        live_loop_step=live_step,
        recommended_next_command=recommended,
        stale=stale,
        message=message,
    )


def build_resume_summary(
    start: Path | str = ".",
    *,
    layout_config: Path | str | None = None,
    resume: LoopResumeStatus | None = None,
    root: Path | None = None,
) -> RunJournalResumeSummary:
    """Read the journal under the discovered project root and summarize resume.

    Read-only: composes :func:`frutlups.project.build_status` (to discover the
    effective project/template root and live loop state) with the journal reader.
    Never writes and never raises for constructible inputs.

    Prompt 031: when a composite caller already selected the invocation's
    resume and project root, it passes both so no second status/acceptance
    scan occurs; with either omitted, this begins its own single-scan
    read-only invocation.
    """

    if resume is None or root is None:
        status, evidence = _build_status_with_evidence(start, layout_config=layout_config)
        live, _verdict, _used = _loop_resume_with_verdict_and_evidence(
            status, evidence=evidence
        )
        status_root = status.root
    else:
        live = resume
        status_root = root
    read = read_run_journal(journal_path_for(status_root))
    return summarize_run_journal(read, live)
