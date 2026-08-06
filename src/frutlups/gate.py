"""Human stop/go gates and final milestone handoff for the local orchestrator (M016-S05).

This module makes the orchestrator's stopping points **visible, typed, resumable,
and safe for human supervision**. It adds no autonomy: it classifies the current
loop state into a human gate and renders a final milestone handoff that a human
can inspect. Live repository artifacts remain authoritative; the run journal is
evidence only.

Nothing here executes anything: no shell/subprocess, no provider/network calls, no
memory mutation, no coder/reviewer agent dispatch, no commits or pull requests. The
final handoff is written **only** through an explicit writer (the read-only
builders/renderers never touch the filesystem).

It depends on ``frutlups.orchestrator`` / ``frutlups.project`` / ``frutlups.journal``
and must not be imported by them (no cycle).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath

from frutlups.journal import (
    RunJournalResumeSummary,
    journal_path_for,
    read_run_journal,
    summarize_run_journal,
)
from frutlups.orchestrator import (
    OrchestratorPlan,
    StepActor,
    _layout_guarded_plan,
    plan_from_resume_status,
)
from frutlups.project import (
    PLANNING_FRONTIER_CONTRACT_ID,
    PLANNING_FRONTIER_SUPPORTED_VERSIONS,
    LoopResumeStatus,
    LoopResumeStep,
    PlanningFrontierOutcome,
    PlanningFrontierStatus,
    ProjectStatus,
    _ARCHITECT_FRONTIER_ACTION,
    _build_status_with_evidence,
    _cap_evidence_diagnostic,
    _compute_planning_frontier,
    _layout_mutation_blockers,
    _layout_mutation_refusal_message,
    _loop_resume_with_verdict_and_evidence,
    build_loop_resume_status,
    build_status,
)

FINAL_HANDOFF_REL_PATH = "05_governance/orchestrator/m016_final_handoff.md"
"""Default repo-relative path of the final M016 milestone handoff artifact."""

_HANDOFF_VALIDATION_COMMANDS: tuple[str, ...] = (
    "python -m unittest discover -s tests",
    "python -m frutlups status .. --json",
    "python -m frutlups orchestrator-plan .. --json",
)

_NON_AUTHORIZATION_NOTE = (
    "This handoff is read-only evidence. It does not commit, open pull requests, "
    "dispatch coder/reviewer agents, mutate memory, run shells, or bypass any human "
    "decision. Live repository artifacts remain authoritative."
)


class HumanGateState(StrEnum):
    """Stable human gate states for the local loop."""

    OPEN = "open"  # exactly one safe local artifact step may be run
    STOP = "stop"  # a human/coder/reviewer must act before continuing
    FINAL_HANDOFF = "final_handoff"  # verdict recorded; human decides the next move
    NO_FRONTIER = "no_frontier"  # no actionable frontier slice
    BLOCKED = "blocked"  # reserved: explicit blocked evidence


@dataclass(frozen=True)
class HumanGate:
    """Typed, JSON-safe description of the current human stop/go gate."""

    gate_state: str
    requires_human_go: bool
    reason: str
    recommended_human_action: str
    loop_step: str
    actor: str
    frontier_slice_id: str
    frontier_slice_title: str
    recommended_command: str
    safe_for_auto_execution: bool
    diagnostics: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, object]:
        return {
            "gate_state": self.gate_state,
            "requires_human_go": self.requires_human_go,
            "reason": self.reason,
            "recommended_human_action": self.recommended_human_action,
            "loop_step": self.loop_step,
            "actor": self.actor,
            "frontier_slice_id": self.frontier_slice_id,
            "frontier_slice_title": self.frontier_slice_title,
            "recommended_command": self.recommended_command,
            "safe_for_auto_execution": self.safe_for_auto_execution,
            "diagnostics": list(self.diagnostics),
        }


def human_gate_from_plan(plan: OrchestratorPlan) -> HumanGate:
    """Derive a :class:`HumanGate` from an :class:`OrchestratorPlan`.

    Pure and conservative. Reuses the plan's existing actor / safe-step
    classification (the orchestrator safety table) rather than re-deriving loop
    state. Never raises.
    """

    step = plan.loop_step
    actor = plan.actor
    actor_value = actor.value if isinstance(actor, StepActor) else str(actor)
    command = plan.recommended_command

    if step == LoopResumeStep.NO_FRONTIER.value:
        state = HumanGateState.NO_FRONTIER
        requires_go = False
        action = (
            "Inspect the roadmap: confirm the milestone is complete, or add/repair "
            "active and development roadmap data so a frontier slice can be inferred."
        )
    elif step == LoopResumeStep.FRONTIER_RECORDED.value:
        state = HumanGateState.FINAL_HANDOFF
        requires_go = True
        action = (
            "Inspect the accepted review report and verdict record, then decide the "
            "next roadmap slice or milestone. Automatic continuation stops here."
        )
    elif plan.safe_for_auto_execution:
        state = HumanGateState.OPEN
        requires_go = False
        runnable = command or "python -m frutlups orchestrator-run .. --once"
        action = (
            "Review the recommended command, then optionally run exactly one safe "
            f"local artifact step: {runnable}"
        )
    elif actor == StepActor.CODER:
        state = HumanGateState.STOP
        requires_go = True
        action = (
            "The coder must act before the loop can continue (implement the slice / "
            "fix the self-report); the orchestrator will not do coder work."
        )
    elif actor == StepActor.REVIEWER:
        state = HumanGateState.STOP
        requires_go = True
        action = (
            "The reviewer must act before the loop can continue (execute the review / "
            "fix the review report); the orchestrator will not do reviewer work."
        )
    else:
        state = HumanGateState.STOP
        requires_go = True
        action = (
            "Resolve the blocking condition (missing inputs or ambiguous evidence) "
            "before continuing; the orchestrator will not proceed automatically."
        )

    return HumanGate(
        gate_state=state.value,
        requires_human_go=requires_go,
        reason=plan.rationale,
        recommended_human_action=action,
        loop_step=step,
        actor=actor_value,
        frontier_slice_id=plan.frontier_slice_id,
        frontier_slice_title=plan.frontier_slice_title,
        recommended_command=command,
        safe_for_auto_execution=plan.safe_for_auto_execution,
        diagnostics=tuple(plan.diagnostics),
    )


def build_human_gate(
    start: Path | str = ".",
    *,
    layout_config: Path | str | None = None,
) -> HumanGate:
    """Build the human gate for the project at ``start`` (read-only). Never raises.

    When the selected layout carries an error-severity diagnostic the
    underlying plan is never auto-safe (M002-S04), so the gate cannot be open.
    Selects layout and acceptance evidence exactly once (Prompt 031).
    """

    status, evidence = _build_status_with_evidence(start, layout_config=layout_config)
    resume, _verdict, _used = _loop_resume_with_verdict_and_evidence(
        status, evidence=evidence
    )
    return human_gate_from_plan(_layout_guarded_plan(plan_from_resume_status(resume), status.layout))


# ---------------------------------------------------------------------------
# M003-S06: planning-frontier composition and the thin consumer boundary
# ---------------------------------------------------------------------------


def _resume_and_planning_frontier_from_status(
    status: ProjectStatus,
    evidence=None,
) -> tuple[LoopResumeStatus, PlanningFrontierStatus]:
    """One selected resume plus its planning frontier (M003-S06).

    Private single-selection helper: the resume, typed verdict, acceptance
    evidence, guarded plan, human gate, and frontier all derive from the one
    already selected ``LoadedLayout`` carried by ``status``, so the status
    surface can never show a resume and a frontier computed from different
    snapshots. ``evidence`` threads the Prompt 031 one-snapshot input (the
    exact acceptance snapshot the selected status itself used); when omitted,
    this begins a fresh read-only resume invocation with its own single scan.
    Read-only; never raises.
    """

    resume, verdict, used_evidence = _loop_resume_with_verdict_and_evidence(
        status, evidence=evidence
    )
    gate = human_gate_from_plan(
        _layout_guarded_plan(plan_from_resume_status(resume), status.layout)
    )
    frontier = _compute_planning_frontier(
        status, resume, verdict, used_evidence, gate_state=gate.gate_state
    )
    return resume, frontier


def _build_status_resume_and_frontier(
    start: Path | str = ".",
    *,
    layout_config: Path | str | None = None,
) -> tuple[ProjectStatus, LoopResumeStatus, PlanningFrontierStatus]:
    """One path-based composition: status, resume, and frontier (Prompt 031).

    Selects the layout once and the acceptance evidence once
    (:func:`_build_status_with_evidence`); the same private snapshot feeds
    the status's accepted IDs and next slice, the loop resume, the typed
    verdict, the human gate, and the planning-frontier outcome, so one
    emitted composite response never combines two evidence snapshots.
    Read-only; never raises for constructible inputs.
    """

    status, evidence = _build_status_with_evidence(start, layout_config=layout_config)
    resume, frontier = _resume_and_planning_frontier_from_status(
        status, evidence=evidence
    )
    return status, resume, frontier


def build_planning_frontier_status(
    start: Path | str = ".",
    *,
    layout_config: Path | str | None = None,
) -> PlanningFrontierStatus:
    """Build the versioned planning-frontier output for the project at ``start``.

    Read-only composition of the existing selected status, loop resume, typed
    review verdict, acceptance evidence, and human gate (M003-S06, Decision
    6). Selects layout and acceptance evidence exactly once per invocation
    (Prompt 031). Emits contract ``frutlups.planning_frontier`` version ``1``
    inside the existing status surface semantics: never writes, never
    journals, never raises for constructible inputs.
    """

    _status, _resume, frontier = _build_status_resume_and_frontier(
        start, layout_config=layout_config
    )
    return frontier


_FRONTIER_BEHAVIOR_CONTINUE = "continue_declared_loop"
_FRONTIER_BEHAVIOR_DISPATCH = "dispatch_architect_and_recompute"
_FRONTIER_BEHAVIOR_STOP_BLOCKED = "stop_blocked"
_FRONTIER_BEHAVIOR_STOP_COMPLETE = "stop_complete"
_FRONTIER_BEHAVIOR_STOP_FAIL_CLOSED = "stop_fail_closed"
_FRONTIER_BEHAVIOR_STOP_RETRY_EXHAUSTED = "stop_retry_exhausted"
_FRONTIER_BEHAVIOR_STOP_NO_PROGRESS = "stop_no_progress"

_FRONTIER_MAX_DIAGNOSTICS = 64
_FRONTIER_MAX_FIELD_LENGTH = 240
_FRONTIER_BLOCK_OWNER_VOCABULARY = ("human",)
"""The currently emitted safe version-1 block-owner vocabulary. Hostile input
never broadens it."""


def _safe_relative_identity(value: object) -> bool:
    """Whether a frontier path field carries a safe bounded repo-relative shape."""

    if not isinstance(value, str) or not value or len(value) > _FRONTIER_MAX_FIELD_LENGTH:
        return False
    if "\\" in value or value.startswith("/"):
        return False
    import re as _re

    if _re.match(r"^[A-Za-z]:", value):
        return False
    return all(part not in ("", ".", "..") for part in value.split("/"))


def _validate_frontier_shape_v1(frontier: PlanningFrontierStatus) -> tuple[str, ...]:
    """Fixed-vocabulary version-1 contract-shape validation (Prompt 031).

    Runs at the consumer boundary after identifier/version support checks and
    before any behavior: retry flags, dispatch, continuation, blocked stop,
    or successful completion. Returns fixed problem strings that never echo
    field content, and never raises for constructible malformed values. Any
    cross-outcome optional-field contamination or incomplete combination is
    invalid typed state.
    """

    problems: list[str] = []
    fields = {
        "action": frontier.action,
        "actor": frontier.actor,
        "block_citation": frontier.block_citation,
        "block_owner": frontier.block_owner,
        "completion_evidence": frontier.completion_evidence,
    }
    for name, value in fields.items():
        if not isinstance(value, str):
            problems.append(f"frontier field {name} is not a string")
        elif len(value) > _FRONTIER_MAX_FIELD_LENGTH:
            problems.append(f"frontier field {name} exceeds the bounded length")
    diagnostics = frontier.diagnostics
    # Prompt 032 (Review 031 finding 2): exactly the accepted runtime shape —
    # a tuple (never a list or other iterable), at most the accepted count,
    # every member a string of at most 240 characters. No coercion, no
    # truncation-to-validity, no content echo.
    if not isinstance(diagnostics, tuple):
        problems.append("frontier diagnostics is not a bounded tuple of strings")
    elif len(diagnostics) > _FRONTIER_MAX_DIAGNOSTICS:
        problems.append("frontier diagnostics exceeds the bounded count")
    else:
        for item in diagnostics:
            if not isinstance(item, str) or len(item) > _FRONTIER_MAX_FIELD_LENGTH:
                problems.append(
                    "frontier diagnostics is not a bounded tuple of strings"
                )
                break
    if problems:
        return tuple(problems)

    outcome = frontier.outcome
    empty_required = {
        PlanningFrontierOutcome.READY.value: (
            "action",
            "actor",
            "block_citation",
            "block_owner",
            "completion_evidence",
        ),
        PlanningFrontierOutcome.NEEDS_SPECIFICATION.value: (
            "block_citation",
            "block_owner",
            "completion_evidence",
        ),
        PlanningFrontierOutcome.BLOCKED.value: (
            "action",
            "actor",
            "completion_evidence",
        ),
        PlanningFrontierOutcome.COMPLETE.value: (
            "action",
            "actor",
            "block_citation",
            "block_owner",
        ),
        PlanningFrontierOutcome.INVALID.value: (
            "action",
            "actor",
            "block_citation",
            "block_owner",
            "completion_evidence",
        ),
    }[outcome]
    for name in empty_required:
        if fields[name]:
            problems.append(
                f"frontier field {name} must be empty for outcome {outcome}"
            )
    if outcome == PlanningFrontierOutcome.NEEDS_SPECIFICATION.value:
        if frontier.action != _ARCHITECT_FRONTIER_ACTION:
            problems.append(
                "needs_specification action is not the one canonical bounded "
                "architect action"
            )
        if frontier.actor != "architect":
            problems.append("needs_specification actor is not architect")
    elif outcome == PlanningFrontierOutcome.BLOCKED.value:
        if not _safe_relative_identity(frontier.block_citation):
            problems.append("blocked citation is missing or not a safe identity")
        if frontier.block_owner not in _FRONTIER_BLOCK_OWNER_VOCABULARY:
            problems.append("blocked owner is not in the emitted v1 vocabulary")
    elif outcome == PlanningFrontierOutcome.COMPLETE.value:
        if not _safe_relative_identity(frontier.completion_evidence):
            problems.append(
                "completion evidence is missing or not a safe identity"
            )
    return tuple(problems)


@dataclass(frozen=True)
class PlanningFrontierDecision:
    """One thin-runner decision for one planning-frontier value (M003-S06).

    ``behavior`` is exactly one of the boundary behaviors; ``success`` is
    ``True`` only for ``stop_complete``. ``dispatched`` records whether the
    injected architect seam was invoked (at most once). ``recomputed`` carries
    the fresh durable frontier rebuilt after a dispatch, else ``None``.
    """

    frontier: PlanningFrontierStatus
    behavior: str
    dispatched: bool
    recomputed: PlanningFrontierStatus | None
    success: bool
    diagnostics: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, object]:
        return {
            "frontier": self.frontier.to_dict(),
            "behavior": self.behavior,
            "dispatched": self.dispatched,
            "recomputed": self.recomputed.to_dict() if self.recomputed else None,
            "success": self.success,
            "diagnostics": list(self.diagnostics),
        }


def decide_planning_frontier_step(
    start: Path | str = ".",
    *,
    frontier: PlanningFrontierStatus | None = None,
    architect_dispatch=None,
    supported_versions: tuple[str, ...] = PLANNING_FRONTIER_SUPPORTED_VERSIONS,
    retry_exhausted: bool = False,
    no_durable_progress: bool = False,
    layout_config: Path | str | None = None,
) -> PlanningFrontierDecision:
    """Bind one planning-frontier value to exactly one thin-runner behavior.

    The existing orchestration boundary consumer (M003-S06, Decision 6):

    - an unsupported contract identifier or version is refused fail-closed
      with a bounded diagnostic naming the observed and supported versions;
      no loop advance, no dispatch, no write, and no journal authority follow
      (resolution 1);
    - ``retry_exhausted`` and ``no_durable_progress`` each stop the run and
      report; neither is success and neither can become ``complete``
      (resolution 6);
    - ``ready`` continues only the already declared loop — execution still
      goes through the existing S04 layout/policy-guarded one-step runner,
      which this decision never bypasses;
    - ``needs_specification`` invokes the injected ``architect_dispatch``
      seam at most once with the frontier value, performs no write itself,
      and rebuilds durable state from repository artifacts before reporting
      the post-dispatch frontier (resolution 4); no network, subprocess, or
      roadmap edit is performed here;
    - ``blocked`` stops and reports the citation and owner (resolution 5);
    - ``complete`` stops successfully only with non-empty accepted
      completion evidence (resolution 3); and
    - ``invalid`` and every unknown outcome stop fail-closed (resolution 6).

    Prompt 031 hardening: after the identifier/version support checks and
    before any behavior, every same-version frontier value must pass the
    fixed-vocabulary version-1 shape validation
    (:func:`_validate_frontier_shape_v1`): cross-outcome field contamination
    or an incomplete combination is invalid typed state and stops fail-closed
    with no dispatch, no recomputation, no loop advance, no write, and no
    journal. A frontier this call built itself from ``start`` is the selected
    snapshot and a legitimate internally built ``complete`` is not recomputed;
    a directly injected ``complete`` value is untrusted and is checked once
    against the current durable frontier (same outcome and the same
    closure-receipt identity) before success is reported.

    Read-only apart from whatever the caller-owned dispatch seam does; this
    function itself never writes and never journals. Never raises for
    constructible inputs.
    """

    internally_built = frontier is None
    if frontier is None:
        frontier = build_planning_frontier_status(start, layout_config=layout_config)

    def _decision(
        behavior: str,
        *,
        dispatched: bool = False,
        recomputed: PlanningFrontierStatus | None = None,
        success: bool = False,
        diagnostics: tuple[str, ...] = (),
    ) -> PlanningFrontierDecision:
        return PlanningFrontierDecision(
            frontier=frontier,
            behavior=behavior,
            dispatched=dispatched,
            recomputed=recomputed,
            success=success,
            diagnostics=tuple(_cap_evidence_diagnostic(diag) for diag in diagnostics),
        )

    supported_names = ", ".join(supported_versions) if supported_versions else "none"
    if frontier.contract_id != PLANNING_FRONTIER_CONTRACT_ID:
        return _decision(
            _FRONTIER_BEHAVIOR_STOP_FAIL_CLOSED,
            diagnostics=(
                "unsupported planning-frontier contract identifier; supported "
                f"identifier: {PLANNING_FRONTIER_CONTRACT_ID}; supported "
                f"versions: {supported_names}",
            ),
        )
    if frontier.contract_version not in supported_versions:
        return _decision(
            _FRONTIER_BEHAVIOR_STOP_FAIL_CLOSED,
            diagnostics=(
                "unsupported planning-frontier contract version: observed "
                f"{frontier.contract_version or '(empty)'}; supported: "
                f"{supported_names}",
            ),
        )

    outcome = frontier.outcome
    if outcome not in tuple(item.value for item in PlanningFrontierOutcome):
        return _decision(
            _FRONTIER_BEHAVIOR_STOP_FAIL_CLOSED,
            diagnostics=(
                "unknown planning-frontier outcome "
                f"{outcome if isinstance(outcome, str) and outcome else '(empty)'}; "
                "never defaulted to ready or complete",
            ),
        )

    shape_problems = _validate_frontier_shape_v1(frontier)
    if shape_problems:
        return _decision(
            _FRONTIER_BEHAVIOR_STOP_FAIL_CLOSED,
            diagnostics=("invalid version-1 planning-frontier shape",)
            + shape_problems,
        )

    if retry_exhausted:
        diagnostics = ["retry budget exhausted; the run stops and reports without completion"]
        if no_durable_progress:
            diagnostics.append("no durable progress this turn")
        return _decision(
            _FRONTIER_BEHAVIOR_STOP_RETRY_EXHAUSTED, diagnostics=tuple(diagnostics)
        )
    if no_durable_progress:
        return _decision(
            _FRONTIER_BEHAVIOR_STOP_NO_PROGRESS,
            diagnostics=(
                "no durable progress this turn; the run stops and reports "
                "without completion",
            ),
        )

    if outcome == PlanningFrontierOutcome.READY.value:
        return _decision(
            _FRONTIER_BEHAVIOR_CONTINUE,
            diagnostics=(
                "continue the declared loop through the existing "
                "layout/policy-guarded one-step runner",
            ),
        )
    if outcome == PlanningFrontierOutcome.NEEDS_SPECIFICATION.value:
        if architect_dispatch is None:
            return _decision(
                _FRONTIER_BEHAVIOR_DISPATCH,
                diagnostics=(
                    "one bounded architect action required "
                    f"(actor {frontier.actor}); no dispatch seam injected",
                ),
            )
        try:
            architect_dispatch(frontier)
        except Exception:
            return _decision(
                _FRONTIER_BEHAVIOR_STOP_FAIL_CLOSED,
                dispatched=True,
                diagnostics=("architect dispatch seam failed; stopping fail-closed",),
            )
        recomputed = build_planning_frontier_status(start, layout_config=layout_config)
        return _decision(
            _FRONTIER_BEHAVIOR_DISPATCH,
            dispatched=True,
            recomputed=recomputed,
            diagnostics=(
                "one bounded architect dispatch completed; durable state "
                "recomputed from repository artifacts",
            ),
        )
    if outcome == PlanningFrontierOutcome.BLOCKED.value:
        return _decision(
            _FRONTIER_BEHAVIOR_STOP_BLOCKED,
            diagnostics=(
                f"blocked: citation {frontier.block_citation}; "
                f"owner {frontier.block_owner}",
            ),
        )
    if outcome == PlanningFrontierOutcome.COMPLETE.value:
        if not internally_built:
            # A directly injected complete value is untrusted: recompute the
            # current durable frontier once and require an exact qualifying
            # complete outcome with the same closure-receipt identity.
            current = build_planning_frontier_status(start, layout_config=layout_config)
            if (
                current.outcome != PlanningFrontierOutcome.COMPLETE.value
                or current.completion_evidence != frontier.completion_evidence
            ):
                return _decision(
                    _FRONTIER_BEHAVIOR_STOP_FAIL_CLOSED,
                    diagnostics=(
                        "injected complete outcome does not match the current "
                        "durable planning frontier; refusing completion",
                    ),
                )
        return _decision(
            _FRONTIER_BEHAVIOR_STOP_COMPLETE,
            success=True,
            diagnostics=(
                f"complete: accepted closure evidence {frontier.completion_evidence}",
            ),
        )
    return _decision(
        _FRONTIER_BEHAVIOR_STOP_FAIL_CLOSED,
        diagnostics=("invalid planning-frontier state; stopping fail-closed",),
    )


@dataclass(frozen=True)
class FinalMilestoneHandoff:
    """Typed, JSON-safe final milestone handoff summary.

    Read-only evidence for a human at the end of a milestone. All fields are plain
    values or nested dicts; ``render()`` produces the deterministic markdown body.
    """

    milestone_id: str
    gate: HumanGate
    live_loop_step: str
    resume_summary: RunJournalResumeSummary
    coding_prompt_path: str
    self_report_path: str
    review_prompt_path: str
    review_report_path: str
    verdict_record_path: str
    validation_commands: tuple[str, ...]
    non_authorization_note: str
    handoff_path: str

    def to_dict(self) -> dict[str, object]:
        return {
            "milestone_id": self.milestone_id,
            "gate": self.gate.to_dict(),
            "live_loop_step": self.live_loop_step,
            "resume_summary": self.resume_summary.to_dict(),
            "coding_prompt_path": self.coding_prompt_path,
            "self_report_path": self.self_report_path,
            "review_prompt_path": self.review_prompt_path,
            "review_report_path": self.review_report_path,
            "verdict_record_path": self.verdict_record_path,
            "validation_commands": list(self.validation_commands),
            "non_authorization_note": self.non_authorization_note,
            "handoff_path": self.handoff_path,
        }

    def render(self) -> str:
        return render_final_handoff(self)


def final_handoff_path_for(root: Path) -> Path:
    """Return the final-handoff artifact path under ``root`` (the project root)."""

    return root / PurePosixPath(FINAL_HANDOFF_REL_PATH)


def build_final_handoff(
    start: Path | str = ".",
    *,
    milestone_id: str = "M016",
    layout_config: Path | str | None = None,
) -> FinalMilestoneHandoff:
    """Build a read-only final milestone handoff for the project at ``start``.

    Composes live status, the loop-resume state, the orchestrator plan / human
    gate, and the run-journal resume summary. Strictly read-only: never writes and
    never raises for constructible inputs. Use :func:`write_final_handoff` to
    persist it explicitly.
    """

    status, evidence = _build_status_with_evidence(start, layout_config=layout_config)
    return _final_handoff_from_status(status, milestone_id, evidence=evidence)


def _final_handoff_from_status(
    status: ProjectStatus, milestone_id: str, evidence=None
) -> FinalMilestoneHandoff:
    """Build the handoff from an already-discovered :class:`ProjectStatus`.

    ``evidence`` threads the Prompt 031 one-snapshot input when the caller
    already selected it with the status.
    """

    resume, _verdict, _used = _loop_resume_with_verdict_and_evidence(
        status, evidence=evidence
    )
    gate = human_gate_from_plan(
        _layout_guarded_plan(plan_from_resume_status(resume), status.layout)
    )
    read = read_run_journal(journal_path_for(status.root))
    resume_summary = summarize_run_journal(read, resume)

    validation_commands = _HANDOFF_VALIDATION_COMMANDS
    layout = status.layout
    if layout is not None and layout.profile.validation_command:
        cmd = layout.profile.validation_command
        if cmd not in validation_commands:
            validation_commands = validation_commands + (cmd,)

    return FinalMilestoneHandoff(
        milestone_id=milestone_id,
        gate=gate,
        live_loop_step=resume.step.value,
        resume_summary=resume_summary,
        coding_prompt_path=resume.coding_prompt_path,
        self_report_path=resume.self_report_path,
        review_prompt_path=resume.review_prompt_path,
        review_report_path=resume.review_report_path,
        verdict_record_path=resume.verdict_record_path,
        validation_commands=validation_commands,
        non_authorization_note=_NON_AUTHORIZATION_NOTE,
        handoff_path=FINAL_HANDOFF_REL_PATH,
    )


def render_final_handoff(handoff: FinalMilestoneHandoff) -> str:
    """Render a deterministic markdown body for ``handoff``. Pure; never raises."""

    gate = handoff.gate
    lines: list[str] = [
        f"# Final Milestone Handoff: {handoff.milestone_id}",
        "",
        "Read-only handoff produced by the local orchestrator for human review.",
        "",
        "## Gate State",
        "",
        f"- Gate: `{gate.gate_state}`",
        f"- Requires human go: {'yes' if gate.requires_human_go else 'no'}",
        f"- Loop step: `{gate.loop_step}`",
        f"- Actor: `{gate.actor}`",
        f"- Safe for one local artifact step: {'yes' if gate.safe_for_auto_execution else 'no'}",
        f"- Reason: {gate.reason}",
        f"- Recommended human action: {gate.recommended_human_action}",
        "",
        "## Frontier",
        "",
    ]
    if gate.frontier_slice_id:
        title = f" - {gate.frontier_slice_title}" if gate.frontier_slice_title else ""
        lines.append(f"- Frontier slice: {gate.frontier_slice_id}{title}")
    else:
        lines.append("- Frontier slice: none")
    lines.append(f"- Live loop step: `{handoff.live_loop_step}`")
    if gate.recommended_command:
        lines.append(f"- Recommended command: {gate.recommended_command}")

    lines += ["", "## Latest Artifacts", ""]
    artifacts = (
        ("Coding prompt", handoff.coding_prompt_path),
        ("Self-report", handoff.self_report_path),
        ("Review prompt", handoff.review_prompt_path),
        ("Review report", handoff.review_report_path),
        ("Verdict record", handoff.verdict_record_path),
    )
    any_artifact = False
    for label, value in artifacts:
        if value:
            any_artifact = True
            lines.append(f"- {label}: `{value}`")
    if not any_artifact:
        lines.append("- (no slice artifacts recorded for the current frontier yet)")

    summary = handoff.resume_summary
    lines += ["", "## Run Journal Summary", ""]
    lines.append(f"- {summary.message}")
    lines.append(f"- Journal entries: {summary.entry_count}")
    if summary.malformed_count:
        lines.append(f"- Malformed journal lines skipped: {summary.malformed_count}")
    lines.append(f"- Stale vs live status: {'yes' if summary.stale else 'no'}")

    lines += ["", "## Validation Commands To Run Or Inspect", ""]
    for cmd in handoff.validation_commands:
        lines.append(f"- `{cmd}`")

    lines += ["", "## Authorization", "", handoff.non_authorization_note, ""]
    return "\n".join(lines)


@dataclass(frozen=True)
class FinalHandoffWriteResult:
    """Result of an explicit final-handoff write."""

    wrote: bool
    target_path: str
    overwrote: bool
    errors: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, object]:
        return {
            "wrote": self.wrote,
            "target_path": self.target_path,
            "overwrote": self.overwrote,
            "errors": list(self.errors),
        }


def write_final_handoff(
    root: Path,
    handoff: FinalMilestoneHandoff,
    *,
    overwrite: bool = False,
) -> FinalHandoffWriteResult:
    """Explicitly write the rendered handoff under ``root``. The only writer here.

    Writes to ``05_governance/orchestrator/m016_final_handoff.md`` under the
    project root. Refuses to overwrite an existing file unless ``overwrite=True``.
    Never raises; returns deterministic errors.
    """

    target = final_handoff_path_for(root)
    target_existed = target.exists()
    if target_existed and not overwrite:
        return FinalHandoffWriteResult(
            wrote=False,
            target_path=str(target),
            overwrote=False,
            errors=(f"{FINAL_HANDOFF_REL_PATH} already exists; pass overwrite=True to replace it",),
        )
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_final_handoff(handoff), encoding="utf-8")
    except OSError as exc:
        return FinalHandoffWriteResult(
            wrote=False,
            target_path=str(target),
            overwrote=False,
            errors=(f"could not write final handoff: {exc}",),
        )
    return FinalHandoffWriteResult(
        wrote=True,
        target_path=str(target),
        overwrote=target_existed,
        errors=(),
    )


def write_final_handoff_artifact(
    start: Path | str = ".",
    *,
    milestone_id: str = "M016",
    overwrite: bool = False,
    layout_config: Path | str | None = None,
) -> tuple[FinalMilestoneHandoff, FinalHandoffWriteResult]:
    """Build and explicitly write the final handoff under the project root.

    Discovers the effective project/template root once, so the handoff artifact is
    written under the project root even when invoked from a child path. Returns the
    built handoff plus the write result. The only entry point that writes a handoff.

    M002-S04 defense in depth: when the selected layout carries an
    error-severity diagnostic, the write is refused with the named layout
    refusal before any directory or file is created.
    """

    status, evidence = _build_status_with_evidence(start, layout_config=layout_config)
    return _write_final_handoff_artifact_from_status(
        status, milestone_id=milestone_id, overwrite=overwrite, evidence=evidence
    )


def _write_final_handoff_artifact_from_status(
    status: ProjectStatus,
    *,
    milestone_id: str = "M016",
    overwrite: bool = False,
    evidence=None,
) -> tuple[FinalMilestoneHandoff, FinalHandoffWriteResult]:
    """Build and write the handoff from an already-built status (M002-S04).

    Private single-selection helper: the handoff, gate, and S04 authority
    decision all derive from the one already selected ``LoadedLayout``
    carried by ``status``; ``evidence`` threads the Prompt 031 one-snapshot
    input. The behavior contract is the public
    :func:`write_final_handoff_artifact` contract.
    """

    handoff = _final_handoff_from_status(status, milestone_id, evidence=evidence)
    blockers = _layout_mutation_blockers(status.layout)
    if blockers:
        return handoff, FinalHandoffWriteResult(
            wrote=False,
            target_path=str(final_handoff_path_for(status.root)),
            overwrote=False,
            errors=(_layout_mutation_refusal_message(blockers),),
        )
    result = write_final_handoff(status.root, handoff, overwrite=overwrite)
    return handoff, result
