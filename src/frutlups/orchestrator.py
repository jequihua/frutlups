"""Local orchestrator planning and one-step execution (M016-S02, M016-S03).

A small, pure, read-only **planner** (M016-S02) composes the existing loop-resume
logic (:func:`frutlups.project.build_loop_resume_status`) and recommends the next
local command for the two-agent loop *without executing it*, plus a deliberately
tiny **one-step executor** (M016-S03) that may run at most one safe local artifact
command per invocation.

The planner (:func:`build_orchestrator_plan`) is advisory only: it never writes or
executes anything. It classifies whether the recommended command would be a
candidate for safe automatic local execution.

The executor (:func:`run_one_step`) acts on that classification:

- It runs **at most one** step per call, and only when the planner marked the
  current step ``safe_for_auto_execution``.
- Safe steps are limited to local artifact writes: ``make_coding_prompt``,
  ``make_review_prompt``, and ``record_verdict``. It dispatches by *typed loop
  step* to the existing internal build/write APIs — it never parses or runs the
  planner's ``recommended_command`` string, and never uses ``subprocess`` or a
  shell.
- Every other step (coder/reviewer execution, self-report/review-report fixes,
  human stop/go, no-frontier, ambiguous/unknown, anything implying
  memory/roadmap/provider/network mutation) is **refused** with a reason; nothing
  is written.
- ``dry_run=True`` writes no prompt/review/verdict artifact (it remains advisory
  for execution); the CLI still appends one run-journal entry per invocation.

The persistent run journal lives in :mod:`frutlups.journal` (M016-S04) and the
human stop/go gates and final milestone handoff in :mod:`frutlups.gate`
(M016-S05); :func:`run_one_step` integrates journaling via its ``journal`` flag.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path

from frutlups.journal import (
    append_run_journal_entry,
    build_run_journal_entry,
    journal_path_for,
    now_timestamp,
)
from frutlups.layout import AutomationBoundaryPolicy, LoadedLayout, legacy_profile
from frutlups.project import (
    LoopResumeStatus,
    LoopResumeStep,
    ProjectStatus,
    VerdictRecordWriteCommand,
    _build_coding_prompt_plan_from_status,
    _build_review_prompt_plan_from_status,
    _build_status_with_evidence,
    _build_verdict_record_plan_from_profile,
    _layout_mutation_blockers,
    _layout_mutation_refusal_message,
    _loop_resume_with_verdict,
    build_loop_resume_status,
    build_status,
    write_verdict_record,
)
from frutlups.review_report import ReviewVerdict
from frutlups.prompt_template import CodingPromptWriteCommand, write_coding_prompt
from frutlups.review_prompt_template import (
    _write_review_prompt_content,
    write_review_prompt,
)


class StepActor(StrEnum):
    """Who must perform the next loop step."""

    ORCHESTRATOR = "orchestrator"  # a local artifact command the runner could run later
    CODER = "coder"  # coder agent must implement / write evidence
    REVIEWER = "reviewer"  # reviewer agent must execute the review
    HUMAN = "human"  # human stop/go or manual fix
    NONE = "none"  # no actionable next step (e.g. no frontier)


# Loop step -> (actor, safe-for-future-auto-execution, rationale).
# Only local artifact commands run by the orchestrator itself are auto-safe; the
# orchestrator never executes them in this slice regardless.
_STEP_POLICY: dict[LoopResumeStep, tuple[StepActor, bool, str]] = {
    LoopResumeStep.NO_FRONTIER: (
        StepActor.NONE,
        False,
        "no frontier slice; nothing to recommend until the roadmap advances",
    ),
    LoopResumeStep.MAKE_CODING_PROMPT: (
        StepActor.ORCHESTRATOR,
        True,
        "writes a single coding-prompt artifact for the inferred frontier slice",
    ),
    LoopResumeStep.EXECUTE_CODING_PROMPT: (
        StepActor.CODER,
        False,
        "requires the coder agent to implement the slice and write the self-report",
    ),
    LoopResumeStep.FIX_SELF_REPORT: (
        StepActor.CODER,
        False,
        "requires the coder to correct the self-report; not a runnable command",
    ),
    LoopResumeStep.MAKE_REVIEW_PROMPT: (
        StepActor.ORCHESTRATOR,
        True,
        "writes a single review-prompt artifact once the self-report exists",
    ),
    LoopResumeStep.EXECUTE_REVIEW_PROMPT: (
        StepActor.REVIEWER,
        False,
        "requires the reviewer agent to execute the review and write the report",
    ),
    LoopResumeStep.FIX_REVIEW_REPORT: (
        StepActor.REVIEWER,
        False,
        "requires the reviewer to correct the review report; not a runnable command",
    ),
    LoopResumeStep.RECORD_VERDICT: (
        StepActor.ORCHESTRATOR,
        True,
        "writes a single verdict-record artifact from the existing review report",
    ),
    LoopResumeStep.FRONTIER_RECORDED: (
        StepActor.HUMAN,
        False,
        "verdict recorded; recompute the frontier and decide the next slice (human stop/go)",
    ),
}

_UNKNOWN_POLICY: tuple[StepActor, bool, str] = (
    StepActor.HUMAN,
    False,
    "unrecognized loop step; defer to a human decision",
)


@dataclass(frozen=True)
class OrchestratorPlan:
    """A read-only, advisory plan for the next step of the local loop.

    ``loop_step`` is the current :class:`LoopResumeStep` value. ``actor`` names
    who must act next. ``recommended_command`` is the literal next command from
    the loop-resume state (empty when the next action is manual or unknown).
    ``safe_for_auto_execution`` indicates whether a *future* one-step executor
    (M016-S03) could run this command automatically — it is advisory only and is
    never acted on here. ``executed`` is always ``False`` for this slice.
    """

    loop_step: str
    actor: StepActor
    frontier_slice_id: str
    frontier_slice_title: str
    recommended_command: str
    safe_for_auto_execution: bool
    executed: bool
    rationale: str
    diagnostics: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, object]:
        return {
            "loop_step": self.loop_step,
            "actor": self.actor.value if isinstance(self.actor, StepActor) else str(self.actor),
            "frontier_slice_id": self.frontier_slice_id,
            "frontier_slice_title": self.frontier_slice_title,
            "recommended_command": self.recommended_command,
            "safe_for_auto_execution": self.safe_for_auto_execution,
            "executed": self.executed,
            "rationale": self.rationale,
            "diagnostics": list(self.diagnostics),
        }


def plan_from_resume_status(resume: LoopResumeStatus) -> OrchestratorPlan:
    """Build an :class:`OrchestratorPlan` from a resolved loop-resume state.

    Pure: maps the loop step to an actor and a safety classification using
    :data:`_STEP_POLICY`. A command is only ``safe_for_auto_execution`` when both
    the step policy allows it *and* a concrete ``next_command`` is present
    (required inputs known). Never executes anything; ``executed`` is always
    ``False``. Never raises for constructible inputs.
    """

    step = resume.step
    actor, policy_safe, rationale = _STEP_POLICY.get(step, _UNKNOWN_POLICY)
    command = resume.next_command or ""

    # A recommendation is auto-safe only if the policy allows it and a concrete
    # command (with known inputs) exists to run.
    safe = bool(policy_safe and command)
    if policy_safe and not command:
        rationale = (
            f"{rationale}; not auto-safe yet because the next command is not fully determined"
        )

    return OrchestratorPlan(
        loop_step=step.value if isinstance(step, LoopResumeStep) else str(step),
        actor=actor,
        frontier_slice_id=resume.frontier_slice_id,
        frontier_slice_title=resume.frontier_slice_title,
        recommended_command=command,
        safe_for_auto_execution=safe,
        executed=False,
        rationale=rationale,
        diagnostics=tuple(resume.diagnostics),
    )


# ---------------------------------------------------------------------------
# M003-S04: typed runner-policy evaluation (private)
# ---------------------------------------------------------------------------

_RUNNER_STOP_CONDITIONS: tuple[str, ...] = (
    "blocked",
    "override required",
    "invalid self-report",
    "invalid review report",
    "no frontier",
    "memory gate failure",
    "environment gate failure",
)
"""The accepted canonical `must_stop_on` values (exact strip/lowercase match)."""


@dataclass(frozen=True)
class _RunnerPolicyEvaluation:
    """The M003-S04 runner-policy decision for one invocation.

    ``diagnostics`` carries bounded owned policy declarations and matches for
    plan/run diagnostic channels. ``refusal_reason`` is a stable owned refusal
    naming only the canonical policy fact, non-empty only when a non-dry-run
    invocation must refuse before any write. ``journal_suppressed`` is true
    only for the ``runner_implemented: false`` posture refusal, which precedes
    even the advisory journal append; a matching stop condition still journals
    its single bounded refusal entry.
    """

    diagnostics: tuple[str, ...]
    refusal_reason: str
    journal_suppressed: bool


def _runner_policy_for(status: ProjectStatus) -> AutomationBoundaryPolicy:
    """The selected layout's typed automation boundary policy."""

    if status.layout is not None:
        return status.layout.profile.automation_boundary
    return legacy_profile().automation_boundary


def _condition_matches(
    name: str, resume: LoopResumeStatus, verdict: ReviewVerdict | None
) -> bool:
    """Whether a canonical stop condition matches the current typed state."""

    if name == "blocked":
        return verdict is not None and verdict == ReviewVerdict.BLOCKED
    if name == "override required":
        return verdict is not None and verdict == ReviewVerdict.OVERRIDE
    if name == "invalid self-report":
        return resume.step == LoopResumeStep.FIX_SELF_REPORT
    if name == "invalid review report":
        return resume.step == LoopResumeStep.FIX_REVIEW_REPORT
    if name == "no frontier":
        return resume.step == LoopResumeStep.NO_FRONTIER
    return False  # memory/environment gates: declared, non-applicable


def _evaluate_runner_policy(
    policy: AutomationBoundaryPolicy,
    resume: LoopResumeStatus,
    verdict: ReviewVerdict | None,
    *,
    dry_run: bool,
) -> _RunnerPolicyEvaluation:
    """Evaluate the selected runner policy against the current typed state.

    Typed truth only: the parsed ``AutomationBoundaryPolicy``, the typed
    ``LoopResumeStep``, and the parsed ``ReviewVerdict``. Configured stop
    names are normalized once by exact strip/lowercase match against the
    canonical values with first-occurrence de-duplication in configured
    order; an unsupported value is not a match and is reported by ordinal
    without echoing its bytes. A ``dry_run`` evaluation never refuses.
    """

    diagnostics: list[str] = [
        f"runner policy: runner_implemented={'true' if policy.runner_implemented else 'false'}"
    ]
    seen: set[str] = set()
    matched: str | None = None
    for index, raw in enumerate(policy.must_stop_on, 1):
        name = raw.strip().lower() if isinstance(raw, str) else ""
        if name in seen:
            continue
        seen.add(name)
        if name not in _RUNNER_STOP_CONDITIONS:
            diagnostics.append(
                f"runner policy: must_stop_on value at position {index} "
                "is not a supported canonical condition"
            )
            continue
        if name in ("memory gate failure", "environment gate failure"):
            diagnostics.append(
                f"runner policy: must_stop_on '{name}' declared but non-applicable "
                "(no native typed state)"
            )
            continue
        if _condition_matches(name, resume, verdict):
            diagnostics.append(f"runner policy: must_stop_on '{name}' matches current state")
            if matched is None:
                matched = name
        else:
            diagnostics.append(
                f"runner policy: must_stop_on '{name}' declared (not currently matched)"
            )

    refusal_reason = ""
    journal_suppressed = False
    if not dry_run:
        if not policy.runner_implemented:
            refusal_reason = (
                "runner policy refused: runner_implemented is false; "
                "automated one-step execution is not authorized"
            )
            journal_suppressed = True
        elif matched is not None:
            refusal_reason = (
                f"runner policy refused: must_stop_on condition '{matched}' "
                "matched current state"
            )
    return _RunnerPolicyEvaluation(
        diagnostics=tuple(diagnostics),
        refusal_reason=refusal_reason,
        journal_suppressed=journal_suppressed,
    )


def _layout_guarded_plan(plan: OrchestratorPlan, layout: LoadedLayout | None) -> OrchestratorPlan:
    """Downgrade auto-safety when the selected layout blocks mutation (M002-S04).

    Under an error-severity selected-layout diagnostic the plan must never be
    open or auto-safe, and it must never hide the fallback: the rationale
    names the blocked state with the stable diagnostic codes (owned wording
    only). A layout without error-severity diagnostics passes through
    unchanged.
    """

    blockers = _layout_mutation_blockers(layout)
    if not blockers:
        return plan
    codes = ", ".join(sorted({diag.code for diag in blockers}))
    note = (
        "mutation not authorized: error-severity layout diagnostics "
        f"({codes}); read-only fallback orientation only"
    )
    rationale = f"{plan.rationale}; {note}" if plan.rationale else note
    return replace(plan, safe_for_auto_execution=False, rationale=rationale)


def _plan_from_status(
    status: ProjectStatus,
    evidence=None,
    resume_out: list | None = None,
) -> OrchestratorPlan:
    """The layout-guarded orchestrator plan for an already-built status.

    Private single-selection helper (M002-S04): the resume, plan, and S04
    authority decision all derive from the one already selected
    ``LoadedLayout`` carried by ``status``. M003-S04: bounded runner-policy
    diagnostics are appended to the plan's diagnostics without changing the
    plan's step, actor, command, or safety classification. Prompt 031:
    ``evidence`` threads the invocation's one selected acceptance snapshot;
    ``resume_out`` optionally receives the computed resume so composite
    callers (the CLI) never rebuild it.
    """

    resume, verdict = _loop_resume_with_verdict(status, evidence=evidence)
    if resume_out is not None:
        resume_out.append(resume)
    plan = _layout_guarded_plan(plan_from_resume_status(resume), status.layout)
    policy = _evaluate_runner_policy(
        _runner_policy_for(status), resume, verdict, dry_run=True
    )
    return replace(plan, diagnostics=plan.diagnostics + policy.diagnostics)


def build_orchestrator_plan(
    start: Path | str = ".",
    layout_config: Path | str | None = None,
) -> OrchestratorPlan:
    """Build a read-only dry-run orchestrator plan for the project at ``start``.

    Composes :func:`build_loop_resume_status` (which itself composes status,
    frontier, prompt-health, and resumable-status logic) and classifies the next
    step. When the selected layout carries an error-severity diagnostic the
    plan is never auto-safe and its rationale names the blocked state
    (M002-S04). Selects layout and acceptance evidence exactly once per
    invocation (Prompt 031). Strictly read-only: reads repository artifacts
    only, never writes, never executes the recommended command, and never
    mutates memory. Never raises.
    """

    status, evidence = _build_status_with_evidence(start, layout_config=layout_config)
    return _plan_from_status(status, evidence=evidence)


# ---------------------------------------------------------------------------
# M016-S03: one-step local executor for safe artifact commands only
# ---------------------------------------------------------------------------


# Loop steps the executor may run, mapped to the internal action it dispatches.
# Anything not in this set is never executed (it is refused).
_EXECUTABLE_STEPS: frozenset[LoopResumeStep] = frozenset(
    {
        LoopResumeStep.MAKE_CODING_PROMPT,
        LoopResumeStep.MAKE_REVIEW_PROMPT,
        LoopResumeStep.RECORD_VERDICT,
    }
)


@dataclass(frozen=True)
class OrchestratorRunResult:
    """Result of a single :func:`run_one_step` invocation.

    Embeds the advisory :class:`OrchestratorPlan` (``plan``) plus the outcome of
    at most one execution attempt. ``attempted`` is ``True`` only when the
    executor decided the step was safe and concrete and tried to write an
    artifact (always ``False`` for a dry run or a refusal). ``wrote`` is ``True``
    only after an artifact file was actually written; ``artifact_path`` is its
    repo-relative-or-absolute string when written, else ``""``. ``refused`` is
    ``True`` when the step was not executed for safety/ambiguity reasons, with
    ``refusal_reason`` explaining why. ``dry_run`` echoes the call mode.
    """

    plan: OrchestratorPlan
    dry_run: bool
    attempted: bool
    wrote: bool
    artifact_path: str
    refused: bool
    refusal_reason: str
    diagnostics: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, object]:
        return {
            "plan": self.plan.to_dict(),
            "dry_run": self.dry_run,
            "attempted": self.attempted,
            "wrote": self.wrote,
            "artifact_path": self.artifact_path,
            "refused": self.refused,
            "refusal_reason": self.refusal_reason,
            "diagnostics": list(self.diagnostics),
        }


@dataclass(frozen=True)
class _ExecutionOutcome:
    """Internal outcome of dispatching one executable step."""

    attempted: bool
    wrote: bool
    artifact_path: str
    refused: bool
    refusal_reason: str
    diagnostics: tuple[str, ...] = field(default=())


def _exec_make_coding_prompt(
    status: ProjectStatus, resume: LoopResumeStatus, evidence=None
) -> _ExecutionOutcome:
    """Write one coding prompt for the frontier via the internal build/write API.

    M002-S04: the plan is built from the same already selected status/layout
    snapshot that produced the guarded plan and the authority decision.
    ``evidence`` is accepted for handler-signature uniformity (Prompt 031).
    """

    plan = _build_coding_prompt_plan_from_status(status)
    if not plan.valid:
        return _ExecutionOutcome(
            attempted=False,
            wrote=False,
            artifact_path="",
            refused=True,
            refusal_reason="coding-prompt plan is not valid; nothing safe to write",
            diagnostics=plan.errors,
        )
    if plan.template is None or plan.render is None:
        return _ExecutionOutcome(
            attempted=False,
            wrote=False,
            artifact_path="",
            refused=True,
            refusal_reason="coding-prompt template/render unavailable; refusing to write",
        )
    result = write_coding_prompt(
        CodingPromptWriteCommand(
            project_root=plan.frontier.root,
            template=plan.template,
            content=plan.render.content,
            overwrite=False,
            prompt_dir=plan.coding_prompt_dir,
        )
    )
    return _ExecutionOutcome(
        attempted=True,
        wrote=result.wrote,
        artifact_path=result.target_path if result.wrote else "",
        refused=False,
        refusal_reason="",
        diagnostics=result.errors,
    )


def _exec_make_review_prompt(
    status: ProjectStatus, resume: LoopResumeStatus, evidence=None
) -> _ExecutionOutcome:
    """Write one review prompt for the latest unmatched coding prompt.

    M002-S04: the plan is built from the same already selected status/layout
    snapshot that produced the guarded plan and the authority decision.
    ``evidence`` is accepted for handler-signature uniformity (Prompt 031).
    """

    plan = _build_review_prompt_plan_from_status(status)
    if not plan.valid:
        return _ExecutionOutcome(
            attempted=False,
            wrote=False,
            artifact_path="",
            refused=True,
            refusal_reason="review-prompt plan is not valid; nothing safe to write",
            diagnostics=plan.errors,
        )
    if plan.template is None:
        return _ExecutionOutcome(
            attempted=False,
            wrote=False,
            artifact_path="",
            refused=True,
            refusal_reason="review-prompt template unavailable; refusing to write",
        )
    result = _write_review_prompt_content(
        project_root=plan.frontier.root,
        template=plan.template,
        content=plan.render.content,
        overwrite=False,
        prompt_dir=plan.review_prompt_dir,
    )
    return _ExecutionOutcome(
        attempted=True,
        wrote=result.wrote,
        artifact_path=result.target_path if result.wrote else "",
        refused=False,
        refusal_reason="",
        diagnostics=result.errors,
    )


def _exec_record_verdict(
    status: ProjectStatus, resume: LoopResumeStatus, evidence=None
) -> _ExecutionOutcome:
    """Record one verdict using the review-report path from the resume status.

    The review-report path is taken from the already-computed loop-resume state
    (``resume.review_report_path``), not pasted by a user, so no path parsing or
    shell handling is involved. M002-S04: the plan is built from the same
    already selected root and profile that produced the authority decision.
    Prompt 031: ``evidence`` threads the invocation's one selected acceptance
    snapshot into the verdict-record plan.
    """

    review_report_rel = resume.review_report_path
    if not review_report_rel:
        return _ExecutionOutcome(
            attempted=False,
            wrote=False,
            artifact_path="",
            refused=True,
            refusal_reason="record_verdict step has no known review-report path; refusing",
        )
    review_report_path = status.root / review_report_rel
    profile = status.layout.profile if status.layout is not None else legacy_profile()
    plan = _build_verdict_record_plan_from_profile(
        status.root, profile, review_report_path, overwrite=False, evidence=evidence
    )
    if not plan.valid:
        return _ExecutionOutcome(
            attempted=False,
            wrote=False,
            artifact_path="",
            refused=True,
            refusal_reason="verdict-record plan is not valid; nothing safe to write",
            diagnostics=plan.errors,
        )
    result = write_verdict_record(
        VerdictRecordWriteCommand(project_root=plan.root, plan=plan, overwrite=False)
    )
    return _ExecutionOutcome(
        attempted=True,
        wrote=result.wrote,
        artifact_path=result.target_path if result.wrote else "",
        refused=False,
        refusal_reason="",
        diagnostics=result.errors,
    )


# Typed dispatch table: loop step -> internal handler. The executor selects a
# handler by enum identity only; it never interprets the recommended-command text.
# M002-S04: handlers receive the same already selected status snapshot that
# produced the guarded plan and the mutation-authority decision. Prompt 031:
# handlers also receive the invocation's one selected acceptance snapshot.
_StepHandler = Callable[..., _ExecutionOutcome]
_STEP_DISPATCH: dict[LoopResumeStep, _StepHandler] = {
    LoopResumeStep.MAKE_CODING_PROMPT: _exec_make_coding_prompt,
    LoopResumeStep.MAKE_REVIEW_PROMPT: _exec_make_review_prompt,
    LoopResumeStep.RECORD_VERDICT: _exec_record_verdict,
}


def run_one_step(
    start: Path | str = ".",
    *,
    dry_run: bool = False,
    layout_config: Path | str | None = None,
    journal: bool = False,
) -> OrchestratorRunResult:
    """Run at most one safe local artifact command for the project at ``start``.

    Composes :func:`build_orchestrator_plan` and acts on its safety
    classification. With ``dry_run=True`` it writes no prompt/review/verdict
    artifact and is purely advisory. Otherwise it executes exactly one step, and
    only when the planner marked the step ``safe_for_auto_execution`` and the step
    is one of the allow-listed local artifact writes (``make_coding_prompt``,
    ``make_review_prompt``, ``record_verdict``). Dispatch is by typed loop step to
    internal build/write functions; the planner's ``recommended_command`` text is
    never parsed or executed and no shell/subprocess is used. Every other step is
    refused with a reason and writes nothing.

    When ``journal=True`` (the CLI default), exactly one durable entry is appended
    to the run journal under the discovered project root for *every* invocation
    (execute, dry-run, or refuse) as resume evidence; the journal is never
    authoritative over live artifacts. The single exception is an error-severity
    selected-layout diagnostic (M002-S04): while that holds, mutation authority
    is unavailable, the step is refused with the named layout refusal before any
    write, and no journal entry is appended either. ``journal=False`` (the
    library default) leaves the filesystem untouched beyond any executed safe
    step. Never raises for constructible inputs.
    """

    root = start if isinstance(start, Path) else Path(start)
    status, evidence = _build_status_with_evidence(root, layout_config=layout_config)
    result, _policy = _run_one_step_from_status(
        status, dry_run=dry_run, journal=journal, evidence=evidence
    )
    return result


def _run_one_step_from_status(
    status: ProjectStatus,
    *,
    dry_run: bool = False,
    journal: bool = False,
    evidence=None,
    resume_out: list | None = None,
) -> tuple[OrchestratorRunResult, "_RunnerPolicyEvaluation"]:
    """Run at most one safe step from an already-built status (M002-S04).

    Private single-selection helper: the resume, guarded plan, S04 authority
    decision, runner-policy decision (M003-S04), handler artifact plans, and
    journal decision all derive from the one already selected
    ``LoadedLayout`` carried by ``status``. Prompt 031: ``evidence`` threads
    the invocation's one selected acceptance snapshot into the resume and the
    record-verdict handler plan; ``resume_out`` optionally receives the
    computed resume for composite callers. Returns the run result plus the
    typed policy evaluation so callers never need to re-derive or
    substring-match it. The behavior contract is the public
    :func:`run_one_step` contract.
    """

    resume, verdict = _loop_resume_with_verdict(status, evidence=evidence)
    if resume_out is not None:
        resume_out.append(resume)
    plan = _layout_guarded_plan(plan_from_resume_status(resume), status.layout)
    policy = _evaluate_runner_policy(
        _runner_policy_for(status), resume, verdict, dry_run=dry_run
    )
    plan = replace(plan, diagnostics=plan.diagnostics + policy.diagnostics)

    # M002-S04: an error-severity selected-layout diagnostic removes mutation
    # authority for this invocation. The same typed policy the CLI applies
    # guards this composite entry point as defense in depth: no artifact write
    # and no journal append happens while it holds.
    layout_blockers = _layout_mutation_blockers(status.layout)

    # Repo-relative artifact paths in ``resume`` (e.g. the review-report path used
    # by ``record_verdict``) and the run journal must be joined against the
    # discovered effective project/template root, not the raw ``start`` path which
    # may be a child directory such as ``08_pkg``.
    project_root = status.root

    def _result(
        *,
        attempted: bool,
        wrote: bool,
        artifact_path: str,
        refused: bool,
        refusal_reason: str,
        diagnostics: tuple[str, ...] = (),
    ) -> OrchestratorRunResult:
        return OrchestratorRunResult(
            plan=plan,
            dry_run=dry_run,
            attempted=attempted,
            wrote=wrote,
            artifact_path=artifact_path,
            refused=refused,
            refusal_reason=refusal_reason,
            diagnostics=diagnostics,
        )

    def _compute() -> OrchestratorRunResult:
        if dry_run:
            diagnostics = ("dry-run: advisory only, nothing executed",)
            if layout_blockers:
                diagnostics += (
                    "layout fallback active: mutation not authorized "
                    "(error-severity layout diagnostics); read-only orientation only",
                )
            diagnostics += policy.diagnostics
            return _result(
                attempted=False,
                wrote=False,
                artifact_path="",
                refused=False,
                refusal_reason="",
                diagnostics=diagnostics,
            )

        if layout_blockers:
            return _result(
                attempted=False,
                wrote=False,
                artifact_path="",
                refused=True,
                refusal_reason=_layout_mutation_refusal_message(layout_blockers),
            )

        # M003-S04: the runner posture and any matching stop condition refuse
        # before handler dispatch, every artifact writer, and (for the posture
        # refusal) the journal append.
        if policy.refusal_reason:
            return _result(
                attempted=False,
                wrote=False,
                artifact_path="",
                refused=True,
                refusal_reason=policy.refusal_reason,
                diagnostics=policy.diagnostics,
            )

        if not plan.safe_for_auto_execution:
            return _result(
                attempted=False,
                wrote=False,
                artifact_path="",
                refused=True,
                refusal_reason=(
                    f"step '{plan.loop_step}' is not safe for automatic local execution"
                    f" ({plan.actor.value}); {plan.rationale}"
                ),
            )

        handler = _STEP_DISPATCH.get(resume.step)
        if resume.step not in _EXECUTABLE_STEPS or handler is None:
            # Defense in depth: even if a step were ever marked safe, only the
            # allow-listed local artifact writes are dispatchable.
            return _result(
                attempted=False,
                wrote=False,
                artifact_path="",
                refused=True,
                refusal_reason=(
                    f"step '{plan.loop_step}' is not an allow-listed safe artifact command"
                ),
            )

        outcome = handler(status, resume, evidence)
        return _result(
            attempted=outcome.attempted,
            wrote=outcome.wrote,
            artifact_path=outcome.artifact_path,
            refused=outcome.refused,
            refusal_reason=outcome.refusal_reason,
            diagnostics=outcome.diagnostics,
        )

    result = _compute()

    # M002-S04: while the selected layout blocks mutation, no journal entry is
    # appended either — the refusal must precede every write, including the
    # normally advisory dry-run journal line. M003-S04: the unsupported
    # runner-posture refusal likewise suppresses the journal; a matched stop
    # condition still journals its single bounded refusal entry.
    if journal and not layout_blockers and not policy.journal_suppressed:
        profile_id = status.layout.profile.profile_id if status.layout is not None else ""
        config_path = status.layout.config_path if status.layout is not None else ""
        entry = build_run_journal_entry(
            result,
            timestamp=now_timestamp(),
            layout_profile_id=profile_id,
            layout_config_path=config_path,
        )
        appended = append_run_journal_entry(journal_path_for(project_root), entry)
        if not appended:
            result = replace(
                result,
                diagnostics=result.diagnostics + ("run journal append failed",),
            )

    return result, policy
