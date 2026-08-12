"""Coder and reviewer handoff generators.

Produces deterministic onboarding artifacts for the next logical coder
(:func:`build_coder_handoff`) or reviewer (:func:`build_reviewer_handoff`).
Both generators are read-only: they derive state from existing project APIs
and return a rendered string; they never write files, dispatch agents, mutate
memory, advance roadmap state, or record verdicts.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from frutlups.layout import ProfileSource
from frutlups.project import (
    LoopResumeStatus,
    LoopResumeStep,
    ProjectStatus,
    _build_status_with_evidence,
    _loop_resume_with_verdict_and_evidence,
    build_loop_resume_status,
)
from frutlups.prompt_template import CodingPromptTemplate
from frutlups.review_prompt_template import (
    ReviewPromptEvidenceCommand,
    derive_review_prompt_evidence,
)
from frutlups.self_report import (
    SelfReportLocationCommand,
    SelfReportSchema,
    SelfReportValidationCommand,
    self_report_schema_for_profile,
    validate_expected_self_report,
)


def _handoff_status_and_resume(
    start: "Path | str | ProjectStatus",
) -> tuple[ProjectStatus, LoopResumeStatus]:
    """One selected status/resume pair for a handoff composition (Prompt 032).

    For path or string input, the layout is selected once and
    ``_collect_acceptance_evidence`` runs exactly once; the resume is built
    from that exact private evidence object by identity
    (:func:`_loop_resume_with_verdict_and_evidence`), so one rendered handoff
    can never combine two acceptance snapshots. For a deliberately supplied
    public ``ProjectStatus`` the accepted Prompt 031 rule holds: the handoff
    begins one fresh read-only resume invocation with exactly one new
    acceptance scan. Read-only; never raises for constructible inputs.
    """

    if isinstance(start, ProjectStatus):
        status = start
        resume = build_loop_resume_status(status)
    else:
        status, evidence = _build_status_with_evidence(start)
        resume, _verdict, _used = _loop_resume_with_verdict_and_evidence(
            status, evidence=evidence
        )
    return status, resume


@dataclass(frozen=True)
class CoderHandoff:
    """Rendered coder handoff artifact.

    ``content`` is the complete markdown text.  The handoff is a
    deterministic onboarding document, not an authorization to code: the
    coder must still read and follow the current coding prompt.
    """

    content: str

    def to_dict(self) -> dict[str, object]:
        return {"content": self.content}


def build_coder_handoff(
    start: Path | str | ProjectStatus = ".",
    date_str: str = "",
) -> CoderHandoff:
    """Build a deterministic coder handoff from current repository state.

    ``start`` may be a path, a path-like string, or an already-built
    ``ProjectStatus`` (to avoid re-reading artifacts in a caller that has
    one in scope).

    ``date_str`` is an optional date label for the handoff title; when
    omitted the title does not include a date so the output remains
    deterministic across calls.

    This function is **read-only**: it composes the selected status and loop
    resume (one layout selection and one acceptance scan per path/string
    invocation, Prompt 032) but never writes files, dispatches agents, or
    mutates memory.
    """
    status, resume = _handoff_status_and_resume(start)

    date_label = f" - {date_str}" if date_str else ""
    content = _render(status, resume, date_label)
    return CoderHandoff(content=content)


# ---------------------------------------------------------------------------
# Renderer (private)
# ---------------------------------------------------------------------------

# M011-S01 (D3): the memory operating-model/posture reading entry is no longer a
# fixed literal. The base list omits it; _handoff_required_reading inserts the
# selected entry (or nothing) at _MEMORY_READING_INDEX so genuine legacy
# fallbacks keep the historical `05_governance/llloom_operating_model.md` line
# byte-for-byte, a selected template-v3 mode `none` cites nothing (it must not
# imply memory is active or point at a nonexistent operating-model file), and
# selected lightweight/llloom modes cite the one selected posture path.
_REQUIRED_READING_BASE = (
    "CLAUDE.md",
    "README.md",
    "prompts/README.md",
    "03_experiments/active_roadmap_frutlups.md",
    "03_experiments/development_roadmap_frutlups.md",
    "05_governance/prompt_loop_operating_model.md",
    "06_infra/architecture.md",
    "08_pkg/README.md",
    "08_pkg/CONTEXT.md",
)
_MEMORY_READING_INDEX = 6
_LEGACY_MEMORY_READING = "05_governance/llloom_operating_model.md"


def _handoff_memory_reading(status: ProjectStatus) -> str | None:
    """The selected memory operating-model/posture reading entry, or ``None``.

    Uses one selected state/layout snapshot (M011-S01): genuine legacy fallback
    keeps the historical operating-model path; a selected non-legacy profile
    cites its posture file only when the selected memory mode is
    lightweight/llloom (signalled by the mode-aware memory backend), and cites
    nothing for mode none/missing/invalid.
    """

    layout = status.layout
    if layout is None or layout.source == ProfileSource.LEGACY_FALLBACK:
        return _LEGACY_MEMORY_READING
    if status.memory.backend in ("lightweight", "llloom"):
        return layout.profile.llloom_posture_file or None
    return None


def _handoff_required_reading(status: ProjectStatus) -> tuple[str, ...]:
    """The deterministic handoff Required-Reading list for the selected profile."""

    entries = list(_REQUIRED_READING_BASE)
    memory_reading = _handoff_memory_reading(status)
    if memory_reading:
        entries.insert(_MEMORY_READING_INDEX, memory_reading)
    return tuple(entries)

_VERIFICATION_COMMANDS = (
    "$env:PYTHONPATH='src'",
    "python -m unittest discover -s tests",
    "python -m frutlups status ..",
    "python -m frutlups status .. --json",
    "python -m frutlups next ..",
    "python -m frutlups next .. --json",
    "python -m frutlups --help",
    "python -m compileall -q src",
)


def _render(
    status: ProjectStatus,
    resume: LoopResumeStatus,
    date_label: str,
) -> str:
    lines: list[str] = []

    # Title
    lines.append(f"# Handoff: frutlups Next Coder{date_label}")
    lines.append("")
    lines.append(
        "Use this handoff to onboard the next frutlups coding agent. It is an "
        "onboarding artifact, not permission to start coding without a current "
        "coding prompt."
    )
    lines.append("")

    # Role
    lines.append("## Role")
    lines.append("")
    lines.append(
        "You are the frutlups coding agent. Implement only the slice assigned "
        "by the current coding prompt. Keep changes narrow, testable, "
        "deterministic, artifact-first, provider-neutral, and standard-library "
        "only unless the coding prompt explicitly says otherwise."
    )
    lines.append("")
    lines.append(
        "The usual project setup may use GPT as architect/reviewer and "
        "Anthropic/Claude as coder, but your role is logical: `coder`. Do not "
        "assume the package is provider-specific. The package must support "
        "swapped roles, same-family agents, manual handoff, and future adapters."
    )
    lines.append("")

    # Purpose
    lines.append("## Current Project Purpose")
    lines.append("")
    lines.append(
        "`frutlups` is a lightweight Python package for orchestrating coding "
        "loops between coder agents, reviewer agents, and a human owner."
    )
    lines.append("")
    lines.append("The package moves work through:")
    lines.append("")
    lines.append("```text")
    lines.append("roadmap slice")
    lines.append("  -> coding prompt")
    lines.append("  -> coder implementation and self-report")
    lines.append("  -> coder-created matching review prompt")
    lines.append("  -> reviewer verdict")
    lines.append("  -> verdict record / next action")
    lines.append("```")
    lines.append("")
    lines.append(
        "Repository artifacts are the source of truth. Do not rely on chat "
        "history as the only record of a decision."
    )
    lines.append("")

    # Current State
    lines.append("## Current State")
    lines.append("")

    # Prompt health
    health = status.prompt_health
    if health.ok:
        lines.append(
            f"Prompts: {status.prompts.coding_count} coding, "
            f"{status.prompts.review_count} review — Prompt health: ok"
        )
    else:
        lines.append(
            f"Prompts: {status.prompts.coding_count} coding, "
            f"{status.prompts.review_count} review — "
            f"Prompt health: warnings ({len(health.findings)})"
        )
        for finding in health.findings:
            lines.append(f"  [{finding.severity.value}] {finding.code}: {finding.message}")
    lines.append("")

    # Loop step
    lines.append(f"Loop step: `{resume.step.value}`")
    if resume.message:
        lines.append(f"Message: {resume.message}")
    lines.append("")

    # Frontier
    if resume.frontier_slice_id:
        title_part = f" — {resume.frontier_slice_title}" if resume.frontier_slice_title else ""
        lines.append(f"Frontier: `{resume.frontier_slice_id}`{title_part}")
    elif status.next_slice is not None:
        lines.append(f"Frontier: `{status.next_slice.slice_id}` — {status.next_slice.title}")
    else:
        lines.append("Frontier: no unaccepted slice found")
    lines.append("")

    # Current Coding Assignment
    lines.append("## Current Coding Assignment")
    lines.append("")
    if resume.coding_prompt_path:
        lines.append(f"Coding prompt: `{resume.coding_prompt_path}`")
    else:
        lines.append("Coding prompt: not yet created for this frontier slice")
    lines.append("")
    if resume.self_report_path:
        lines.append(f"Expected self-report: `{resume.self_report_path}`")
    lines.append("")
    if resume.next_command:
        lines.append("Next command:")
        lines.append("")
        lines.append("```powershell")
        lines.append(resume.next_command)
        lines.append("```")
        lines.append("")

    # Required Reading
    lines.append("## Required Reading Before Any Slice")
    lines.append("")
    lines.append("```text")
    for entry in _handoff_required_reading(status):
        lines.append(entry)
    lines.append("```")
    lines.append("")
    lines.append("Then read the current coding prompt assigned by the architect/reviewer.")
    lines.append("")

    # Package Workspace
    lines.append("## Package Workspace")
    lines.append("")
    lines.append("The package lives under `08_pkg/`.")
    lines.append("")
    lines.append("Use the existing style:")
    lines.append("")
    lines.append("- standard library first")
    lines.append("- small frozen dataclasses and `StrEnum` values")
    lines.append("- pure/read-only status helpers")
    lines.append("- explicit filesystem write commands only")
    lines.append("- deterministic `to_dict()` payloads")
    lines.append("- tests for every loop-state behavior")
    lines.append("")

    # Verification Commands
    lines.append("## Verification Baseline")
    lines.append("")
    lines.append("From `08_pkg/`:")
    lines.append("")
    lines.append("```powershell")
    for cmd in _VERIFICATION_COMMANDS:
        lines.append(cmd)
    lines.append("```")
    lines.append("")

    # Memory Rules
    lines.append("## Memory Rules")
    lines.append("")
    lines.append(
        "`llloom` is optional and still in development. Normal coding slices "
        "must not mutate memory. If a future prompt asks you to use memory, "
        "use read-only commands unless the prompt is explicitly a memory-update "
        "slice. Read current upstream/project instructions before relying on "
        "exact `llloom` command details, and keep command assumptions isolated "
        "and easy to patch."
    )
    lines.append("")
    lines.append(
        "Do not edit claim YAML, rendered pages, sidecars, journals, indexes, or locks manually."
    )
    lines.append("")

    # Boundaries
    lines.append("## Boundaries")
    lines.append("")
    lines.append("Do not:")
    lines.append("")
    lines.append("- start coding without a current coding prompt")
    lines.append("- broaden a slice beyond the prompt")
    lines.append("- implement automatic agent dispatch")
    lines.append("- hard-code GPT, Claude, Anthropic, or any provider as required")
    lines.append("- make `llloom` mandatory")
    lines.append("- mutate memory in a normal coding slice")
    lines.append("- hide state outside repository files")
    lines.append("- delete, move, rename, or renumber existing prompts unless assigned")
    lines.append("- create review prompts before self-report evidence exists")
    lines.append("- record verdicts or advance roadmap markdown by hand")
    lines.append("")
    lines.append(
        "If the prompt is ambiguous, stop and ask the architect/human for "
        "clarification instead of guessing."
    )
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Reviewer handoff
# ---------------------------------------------------------------------------

_SEVERITY_GUIDANCE = (
    "blocker: correctness failures, missing required behavior, broken "
    "interfaces, invalid self-report, or test regressions",
    "major: incomplete behavior, incorrect error handling, or significant scope violations",
    "minor: documentation gaps, redundant code, or style issues that do not affect correctness",
    "nit: cosmetic observations a reviewer may note but should not block acceptance",
)

_VERDICT_LABELS = ("pass", "needs_work", "blocked", "override")


@dataclass(frozen=True)
class ReviewerHandoff:
    """Rendered reviewer handoff artifact.

    ``content`` is the complete markdown text.  The handoff is a
    deterministic onboarding document, not authorization for a verdict: the
    reviewer must still inspect the review prompt, the coder self-report, the
    implementation, and the verification evidence before deciding.
    """

    content: str

    def to_dict(self) -> dict[str, object]:
        return {"content": self.content}


def build_reviewer_handoff(
    start: Path | str | ProjectStatus = ".",
    date_str: str = "",
) -> ReviewerHandoff:
    """Build a deterministic reviewer handoff from current repository state.

    ``start`` may be a path, a path-like string, or an already-built
    ``ProjectStatus`` (to avoid re-reading artifacts in a caller that has one
    in scope), mirroring :func:`build_coder_handoff`.

    ``date_str`` is an optional date label for the handoff title; when omitted
    the title carries no date so the output remains deterministic across calls.

    This function is **read-only**: it composes :func:`build_status`,
    :func:`build_loop_resume_status`, and the existing self-report / evidence
    helpers, but never writes files, dispatches agents, records verdicts, or
    mutates memory.  Incomplete loop states are represented as artifact gaps
    rather than invented review evidence. Path/string input selects one
    layout and one acceptance snapshot for the whole handoff (Prompt 032).
    """
    status, resume = _handoff_status_and_resume(start)
    milestone_id, milestone_title = _frontier_milestone(status, resume)
    schema = self_report_schema_for_profile(status.layout.profile if status.layout else None)
    evidence = _derive_self_report_evidence(status.root, resume, schema)
    review_report_present = _review_report_present(status.root, resume)

    date_label = f" - {date_str}" if date_str else ""
    content = _render_reviewer(
        status,
        resume,
        date_label,
        milestone_id=milestone_id,
        milestone_title=milestone_title,
        evidence=evidence,
        review_report_present=review_report_present,
    )
    return ReviewerHandoff(content=content)


def _frontier_milestone(
    status: ProjectStatus,
    resume: LoopResumeStatus,
) -> tuple[str, str]:
    """Return the frontier milestone id and title, best-effort.

    The milestone id is derived from the frontier slice id prefix (for
    example ``M011-S02`` -> ``M011``); the title is looked up in the parsed
    active-roadmap milestones.  Either value may be ``""`` when not derivable.
    """
    slice_id = resume.frontier_slice_id
    if not slice_id or "-" not in slice_id:
        return "", ""
    milestone_id = slice_id.split("-", 1)[0]
    title = ""
    for milestone in status.milestones:
        if milestone.milestone_id.upper() == milestone_id.upper():
            title = milestone.title
            break
    return milestone_id, title


def _derive_self_report_evidence(
    root: Path,
    resume: LoopResumeStatus,
    schema: SelfReportSchema,
) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    """Derive expected changed files and verification commands from the
    coder self-report, reusing the existing M005/M006 helpers.

    ``schema`` is the effective self-report schema for the resolved layout
    profile, so reviewer-handoff evidence honors the same configured headings as
    loop-resume / make-review-prompt. Returns ``None`` when the self-report is
    missing or invalid (so the handoff can represent the slice as not-yet-ready
    rather than inventing evidence). Read-only; never writes.
    """
    if not resume.self_report_path or not resume.frontier_slice_id:
        return None

    stub = CodingPromptTemplate(
        sequence=1,
        milestone_id=resume.frontier_slice_id.split("-", 1)[0] or "UNKNOWN",
        slice_id=resume.frontier_slice_id,
        slug="reviewer_handoff_evidence_probe",
        title=resume.frontier_slice_title or "unknown",
        role_instructions="reviewer evidence probe",
        required_reading=("CLAUDE.md", "README.md"),
        scope_paths=("08_pkg/",),
        non_goals=(),
        definition_of_done=("not applicable",),
        verification_commands=("not applicable",),
        self_report_path=resume.self_report_path,
    )
    validation = validate_expected_self_report(
        SelfReportValidationCommand(
            location=SelfReportLocationCommand(project_root=root, template=stub),
            schema=schema,
        )
    )
    if not validation.valid:
        return None
    evidence = derive_review_prompt_evidence(ReviewPromptEvidenceCommand(validation=validation))
    if evidence.errors:
        return None
    return evidence.expected_changed_files, evidence.verification_commands


def _review_report_present(root: Path, resume: LoopResumeStatus) -> bool:
    """Return ``True`` when the expected review report file already exists."""
    if not resume.review_report_path:
        return False
    return (root / resume.review_report_path).is_file()


_REVIEW_READINESS: dict[LoopResumeStep, str] = {
    LoopResumeStep.NO_FRONTIER: ("No frontier slice is available; there is nothing to review."),
    LoopResumeStep.MAKE_CODING_PROMPT: (
        "Not ready: no coding prompt exists for the frontier slice yet."
    ),
    LoopResumeStep.EXECUTE_CODING_PROMPT: (
        "Not ready: the coder self-report is missing. The slice is not ready for review."
    ),
    LoopResumeStep.FIX_SELF_REPORT: (
        "Not ready: the coder self-report exists but is invalid and must be fixed before review."
    ),
    LoopResumeStep.MAKE_REVIEW_PROMPT: (
        "Artifact gap: the self-report is valid but no matching review prompt "
        "exists yet; the coder must create it before review."
    ),
    LoopResumeStep.EXECUTE_REVIEW_PROMPT: (
        "Ready for review: the review prompt exists and no review report has been written yet."
    ),
    LoopResumeStep.FIX_REVIEW_REPORT: (
        "A review report exists but its verdict could not be parsed; the "
        "review report must be fixed."
    ),
    LoopResumeStep.RECORD_VERDICT: (
        "A review report with a parseable verdict already exists; review "
        "evidence is present and the verdict is pending recording. Do not "
        "duplicate the review."
    ),
    LoopResumeStep.FRONTIER_RECORDED: (
        "This slice's verdict is already recorded; recompute the frontier."
    ),
}


def _render_reviewer(
    status: ProjectStatus,
    resume: LoopResumeStatus,
    date_label: str,
    *,
    milestone_id: str,
    milestone_title: str,
    evidence: tuple[tuple[str, ...], tuple[str, ...]] | None,
    review_report_present: bool,
) -> str:
    lines: list[str] = []

    # Title
    lines.append(f"# Handoff: frutlups Next Reviewer{date_label}")
    lines.append("")
    lines.append(
        "Use this handoff to onboard the next frutlups reviewer. It is an "
        "onboarding artifact, not authorization for a verdict: the reviewer "
        "must inspect the review prompt, the coder self-report, the "
        "implementation, and the verification evidence before deciding."
    )
    lines.append("")

    # Role
    lines.append("## Role")
    lines.append("")
    lines.append(
        "You are the frutlups reviewer. Review the slice against its coding "
        "prompt, the coder self-report, and the project framework. Your role "
        "is logical: `reviewer`. Do not assume the package is "
        "provider-specific."
    )
    lines.append("")
    lines.append(
        "The usual project setup may use GPT as architect/reviewer and "
        "Anthropic/Claude as coder, but that is only a preset. No provider or "
        "model family is required to act as the reviewer. The package must "
        "support swapped roles, same-family agents, manual handoff, and future "
        "adapters."
    )
    lines.append("")

    # Current State
    lines.append("## Current State")
    lines.append("")

    health = status.prompt_health
    if health.ok:
        lines.append(
            f"Prompts: {status.prompts.coding_count} coding, "
            f"{status.prompts.review_count} review — Prompt health: ok"
        )
    else:
        lines.append(
            f"Prompts: {status.prompts.coding_count} coding, "
            f"{status.prompts.review_count} review — "
            f"Prompt health: warnings ({len(health.findings)})"
        )
        for finding in health.findings:
            lines.append(f"  [{finding.severity.value}] {finding.code}: {finding.message}")
    lines.append("")

    lines.append(f"Loop step: `{resume.step.value}`")
    if resume.message:
        lines.append(f"Message: {resume.message}")
    lines.append("")

    readiness = _REVIEW_READINESS.get(
        resume.step, "Review readiness is unknown for the current loop step."
    )
    lines.append(f"Review readiness: {readiness}")
    lines.append("")

    # Frontier
    if resume.frontier_slice_id:
        title_part = f" — {resume.frontier_slice_title}" if resume.frontier_slice_title else ""
        lines.append(f"Frontier slice: `{resume.frontier_slice_id}`{title_part}")
    else:
        lines.append("Frontier slice: no unaccepted slice found")
    if milestone_id:
        milestone_part = f" — {milestone_title}" if milestone_title else ""
        lines.append(f"Frontier milestone: `{milestone_id}`{milestone_part}")
    lines.append("")

    # Review Pairing
    lines.append("## Review Pairing")
    lines.append("")
    lines.append(f"Coding prompt: {_path_or_gap(resume.coding_prompt_path, 'not yet created')}")
    lines.append(f"Coder self-report: {_path_or_gap(resume.self_report_path, 'not yet derivable')}")
    lines.append(
        f"Matching review prompt: {_path_or_gap(resume.review_prompt_path, 'not yet created')}"
    )
    lines.append(
        f"Expected review report: {_path_or_gap(resume.review_report_path, 'not yet derivable')}"
    )
    lines.append(
        f"Expected verdict record: {_path_or_gap(resume.verdict_record_path, 'not yet derivable')}"
    )
    lines.append("")
    if review_report_present:
        lines.append(
            "An existing review report is already present at the expected path. "
            "Do not duplicate or overwrite review evidence; inspect it and, if "
            "appropriate, route to record-verdict instead of re-reviewing."
        )
    else:
        lines.append("No review report exists yet at the expected path.")
    lines.append("")

    # Coder Evidence
    lines.append("## Coder Evidence to Inspect")
    lines.append("")
    if evidence is None:
        lines.append(
            "Self-report evidence is not available yet (the self-report is "
            "missing or invalid). The slice is not ready for review; treat this "
            "as an artifact gap rather than missing diligence."
        )
        lines.append("")
    else:
        changed_files, verification = evidence
        lines.append("Expected changed files (from the coder self-report):")
        lines.append("")
        if changed_files:
            for entry in changed_files:
                lines.append(f"- `{entry}`")
        else:
            lines.append("- *(none reported)*")
        lines.append("")
        lines.append("Verification commands reported by the coder (re-run and confirm):")
        lines.append("")
        if verification:
            lines.append("```powershell")
            for cmd in verification:
                lines.append(cmd)
            lines.append("```")
        else:
            lines.append("*(none reported)*")
        lines.append("")

    # Required Reading
    lines.append("## Required Reading Before Reviewing")
    lines.append("")
    lines.append("```text")
    for entry in _handoff_required_reading(status):
        lines.append(entry)
    lines.append("```")
    lines.append("")
    lines.append(
        "Then read the matching review prompt, the coder self-report, and the "
        "changed files listed above."
    )
    lines.append("")

    # Package Workspace
    lines.append("## Package Workspace")
    lines.append("")
    lines.append("The package lives under `08_pkg/`.")
    lines.append("")
    lines.append("Review against the existing style:")
    lines.append("")
    lines.append("- standard library first")
    lines.append("- small frozen dataclasses and `StrEnum` values")
    lines.append("- pure/read-only status helpers")
    lines.append("- explicit filesystem write commands only")
    lines.append("- deterministic `to_dict()` payloads")
    lines.append("- tests for every loop-state behavior")
    lines.append("")

    # Verification Baseline
    lines.append("## Verification Baseline")
    lines.append("")
    lines.append("From `08_pkg/`:")
    lines.append("")
    lines.append("```powershell")
    for cmd in _VERIFICATION_COMMANDS:
        lines.append(cmd)
    lines.append("```")
    lines.append("")
    lines.append(
        "Also run the focused tests named by the coder self-report. Prefer the "
        "coder-reported verification commands above when they are present."
    )
    lines.append("")

    # Severity Guidance and Verdicts
    lines.append("## Severity Guidance and Verdicts")
    lines.append("")
    lines.append("Order findings by severity before stating the verdict:")
    lines.append("")
    for entry in _SEVERITY_GUIDANCE:
        lines.append(f"- {entry}")
    lines.append("")
    lines.append("Choose exactly one verdict:")
    lines.append("")
    for label in _VERDICT_LABELS:
        lines.append(f"- `{label}`")
    lines.append("")
    lines.append(
        "Blockers and majors justify `needs_work`. Minors and nits alone "
        "should not. A human `override` must include rationale. Return "
        "`blocked` or ask the architect/human when evidence is missing or "
        "ambiguous instead of guessing."
    )
    lines.append("")

    # Memory Rules
    lines.append("## Memory Rules")
    lines.append("")
    memory_reading = _handoff_memory_reading(status)
    posture_sentence = (
        f" Read `{memory_reading}` before relying on exact `llloom` command details."
        if memory_reading
        else ""
    )
    lines.append(
        "`llloom` is optional and still in development. Normal review slices "
        "must not mutate memory; memory access, if any, is read-only. Do not "
        "edit claim YAML, rendered pages, sidecars, journals, indexes, or "
        "locks. Memory mutation belongs only in an explicit memory-update "
        "slice." + posture_sentence
    )
    lines.append("")

    # Boundaries
    lines.append("## Boundaries")
    lines.append("")
    lines.append("Do not:")
    lines.append("")
    lines.append("- issue a verdict without inspecting the actual evidence")
    lines.append("- create or overwrite the verdict record during review")
    lines.append("- create or overwrite the review report from this handoff")
    lines.append("- implement automatic agent dispatch")
    lines.append("- hard-code GPT, Claude, Anthropic, or any provider as required")
    lines.append("- make `llloom` mandatory")
    lines.append("- mutate memory in a normal review slice")
    lines.append("- hide state outside repository files")
    lines.append("- advance roadmap markdown by hand")
    lines.append("- delete, move, rename, or renumber existing prompts unless assigned")
    lines.append("")
    lines.append(
        "This handoff does not itself authorize a verdict. If evidence is "
        "missing or ambiguous, return `blocked` or ask the architect/human "
        "instead of guessing."
    )
    lines.append("")

    return "\n".join(lines)


def _path_or_gap(value: str, gap_message: str) -> str:
    """Render a repo-relative path as backtick code, or an explicit gap note."""
    return f"`{value}`" if value else f"({gap_message})"
