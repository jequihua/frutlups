"""Blocked-state resume guidance.

When a review verdict is ``blocked``, the same slice cannot advance until a
human or external dependency supplies a missing decision. This module turns that
situation into deterministic, artifact-first guidance so a human, architect,
reviewer, or coder can resume safely without relying on chat history.

The builder is pure and read-only: it derives guidance from explicit inputs
and/or an existing :class:`~frutlups.state.NextActionDecision`. It never writes
files, automatically unblocks work, edits roadmap markdown, records verdicts,
dispatches agents, mutates memory, or guesses a missing human answer. When the
blocker is an ambiguous missing decision it can surface a *suggested*
:class:`~frutlups.question.QuestionArtifactTemplate`, but it never writes the
question artifact — the caller must invoke
:func:`~frutlups.question.write_question_artifact` explicitly.

Roles (``human``, ``architect``, ``reviewer``, ``coder``) are logical and
provider-neutral; no provider or model family is required to block, unblock, or
answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from frutlups.question import (
    QuestionArtifactTemplate,
    validate_question_artifact_template,
)
from frutlups.review_report import ReviewVerdict
from frutlups.state import NextActionDecision, NextActionKind

_BOUNDARIES: tuple[str, ...] = (
    "Do not record a `pass` to move past the block.",
    "Do not advance or edit roadmap markdown.",
    "Do not skip the blocked slice or advance to a later slice.",
    "Do not mutate memory.",
    "Do not invent or guess the missing human/external answer.",
    "Do not hard-code a provider; humans, architects, reviewers, and coders are "
    "logical roles, not provider families.",
)

_MEMORY_POSTURE: tuple[str, ...] = (
    "`llloom` remains optional and read-only unless a prompt explicitly assigns "
    "memory-update work.",
    "Do not edit claim YAML, rendered pages, sidecars, journals, indexes, or "
    "locks while resolving a block.",
)


@dataclass(frozen=True)
class BlockedResumeGuidance:
    """Deterministic guidance for resuming a blocked loop slice.

    All path/string fields are repo-relative or human-readable; an empty
    string marks an explicit gap (information not supplied). ``suggested_question``
    is an optional :class:`QuestionArtifactTemplate` the caller may write
    explicitly via the M011-S03 question API; it is never written here.
    ``content`` is the rendered markdown (``""`` when ``valid`` is ``False``
    because no slice could be identified). ``valid`` is ``True`` iff ``errors``
    is empty. ``to_dict()`` returns only plain Python values.
    """

    slice_id: str
    slice_title: str
    review_report_path: str
    verdict_record_path: str
    verdict: str
    next_action_kind: str
    why_blocked: str
    human_input_needed: str
    suggested_question: QuestionArtifactTemplate | None
    question_artifact_path: str
    resume_checklist: tuple[str, ...]
    next_command: str
    boundaries: tuple[str, ...]
    memory_posture: tuple[str, ...]
    content: str
    valid: bool
    errors: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, object]:
        return {
            "slice_id": self.slice_id,
            "slice_title": self.slice_title,
            "review_report_path": self.review_report_path,
            "verdict_record_path": self.verdict_record_path,
            "verdict": self.verdict,
            "next_action_kind": self.next_action_kind,
            "why_blocked": self.why_blocked,
            "human_input_needed": self.human_input_needed,
            "suggested_question": (
                self.suggested_question.to_dict() if self.suggested_question is not None else None
            ),
            "question_artifact_path": self.question_artifact_path,
            "resume_checklist": list(self.resume_checklist),
            "next_command": self.next_command,
            "boundaries": list(self.boundaries),
            "memory_posture": list(self.memory_posture),
            "content": self.content,
            "valid": self.valid,
            "errors": list(self.errors),
        }


def build_blocked_resume_guidance(
    *,
    slice_id: str = "",
    slice_title: str = "",
    review_report_path: str = "",
    verdict_record_path: str = "",
    next_action: NextActionDecision | None = None,
    verdict: ReviewVerdict | None = None,
    human_input_needed: str = "",
    suggested_question: QuestionArtifactTemplate | None = None,
    question_artifact_path: str = "",
    next_command: str = "",
) -> BlockedResumeGuidance:
    """Build deterministic blocked-state resume guidance.

    Inputs are explicit. ``slice_id`` may be omitted when ``next_action`` is
    supplied (the reviewed slice id is taken from it). The verdict and
    next-action kind are resolved from ``next_action`` when present, otherwise
    from ``verdict`` (a ``blocked`` verdict implies ``unblock_same_slice``).

    Pure and read-only: never raises for constructible inputs, never writes
    files, and never writes the suggested question artifact. Malformed optional
    inputs produce deterministic ``errors`` and explicit gaps rather than
    exceptions.
    """

    errors: list[str] = []

    if next_action is not None and not isinstance(next_action, NextActionDecision):
        errors.append("next_action must be a NextActionDecision or None")
        next_action = None
    if verdict is not None and not isinstance(verdict, ReviewVerdict):
        errors.append("verdict must be a ReviewVerdict or None")
        verdict = None

    resolved_slice_id = slice_id.strip() if isinstance(slice_id, str) else ""
    if not resolved_slice_id and next_action is not None:
        resolved_slice_id = next_action.current_slice_id or ""
    if not resolved_slice_id:
        errors.append("slice_id is required (or supply a next_action with a current_slice_id)")

    resolved_verdict: ReviewVerdict | None = None
    if next_action is not None and next_action.verdict is not None:
        resolved_verdict = next_action.verdict
    elif verdict is not None:
        resolved_verdict = verdict
    verdict_str = resolved_verdict.value if resolved_verdict is not None else ""

    if next_action is not None:
        next_action_kind = next_action.kind.value
    elif resolved_verdict == ReviewVerdict.BLOCKED:
        next_action_kind = NextActionKind.UNBLOCK_SAME_SLICE.value
    else:
        next_action_kind = ""

    if resolved_verdict is not None and resolved_verdict != ReviewVerdict.BLOCKED:
        errors.append(
            f"verdict {resolved_verdict.value!r} is not 'blocked'; this guidance "
            "applies to blocked verdicts"
        )

    if suggested_question is not None:
        if not isinstance(suggested_question, QuestionArtifactTemplate):
            errors.append("suggested_question must be a QuestionArtifactTemplate or None")
            suggested_question = None
        else:
            q_errors = validate_question_artifact_template(suggested_question)
            for q_err in q_errors:
                errors.append(f"suggested_question invalid: {q_err}")

    why_blocked = (
        "A `blocked` verdict means the same slice cannot proceed until a human "
        "decision or an external dependency is supplied. Blocked verdicts route "
        "back to the same slice for human/external unblock; they never advance "
        "to a later slice."
    )

    human_input_needed_text = (
        human_input_needed.strip()
        if isinstance(human_input_needed, str) and human_input_needed.strip()
        else (
            "A human or external decision is required and has not been recorded "
            "yet. Obtain it from the responsible human or architect before "
            "resuming the slice."
        )
    )

    resume_checklist = _build_resume_checklist(
        review_report_path=review_report_path,
        question_artifact_path=question_artifact_path,
        has_suggested_question=suggested_question is not None,
    )

    content = (
        _render(
            slice_id=resolved_slice_id,
            slice_title=slice_title,
            review_report_path=review_report_path,
            verdict_record_path=verdict_record_path,
            verdict_str=verdict_str,
            next_action_kind=next_action_kind,
            why_blocked=why_blocked,
            human_input_needed=human_input_needed_text,
            suggested_question=suggested_question,
            question_artifact_path=question_artifact_path,
            resume_checklist=resume_checklist,
            next_command=next_command,
        )
        if resolved_slice_id
        else ""
    )

    return BlockedResumeGuidance(
        slice_id=resolved_slice_id,
        slice_title=slice_title if isinstance(slice_title, str) else "",
        review_report_path=review_report_path if isinstance(review_report_path, str) else "",
        verdict_record_path=(verdict_record_path if isinstance(verdict_record_path, str) else ""),
        verdict=verdict_str,
        next_action_kind=next_action_kind,
        why_blocked=why_blocked,
        human_input_needed=human_input_needed_text,
        suggested_question=suggested_question,
        question_artifact_path=(
            question_artifact_path if isinstance(question_artifact_path, str) else ""
        ),
        resume_checklist=resume_checklist,
        next_command=next_command if isinstance(next_command, str) else "",
        boundaries=_BOUNDARIES,
        memory_posture=_MEMORY_POSTURE,
        content=content,
        valid=not errors,
        errors=tuple(errors),
    )


def _build_resume_checklist(
    *,
    review_report_path: str,
    question_artifact_path: str,
    has_suggested_question: bool,
) -> tuple[str, ...]:
    report_ref = (
        f" (`{review_report_path}`)"
        if isinstance(review_report_path, str) and review_report_path
        else ""
    )
    if isinstance(question_artifact_path, str) and question_artifact_path:
        question_step = (
            "If the missing decision is ambiguous, create or update the question "
            f"artifact at `{question_artifact_path}` (write it explicitly with the "
            "question API; it is not written automatically)."
        )
    elif has_suggested_question:
        question_step = (
            "If the missing decision is ambiguous, write the suggested question "
            "artifact explicitly with the question API (it is not written "
            "automatically)."
        )
    else:
        question_step = (
            "If the missing decision is ambiguous, create or update a question "
            "artifact (write it explicitly with the question API; it is not "
            "written automatically)."
        )
    return (
        f"Inspect the blocking review report{report_ref}.",
        question_step,
        "Obtain the human/external answer that the block depends on.",
        "Resume the same slice through a new corrective coding prompt or an "
        "explicit human override, per the recorded verdict workflow.",
    )


def _render(
    *,
    slice_id: str,
    slice_title: str,
    review_report_path: str,
    verdict_record_path: str,
    verdict_str: str,
    next_action_kind: str,
    why_blocked: str,
    human_input_needed: str,
    suggested_question: QuestionArtifactTemplate | None,
    question_artifact_path: str,
    resume_checklist: tuple[str, ...],
    next_command: str,
) -> str:
    lines: list[str] = []

    lines.append("# Blocked-State Resume Guidance")
    lines.append("")

    lines.append("## Blocked Slice")
    lines.append("")
    title_part = f" — {slice_title}" if slice_title else ""
    lines.append(f"- Slice: `{slice_id}`{title_part}")
    lines.append("")

    lines.append("## Source Review Report")
    lines.append("")
    lines.append(
        "- Review report: "
        + (f"`{review_report_path}`" if review_report_path else "(not provided)")
    )
    lines.append(
        "- Verdict record: "
        + (f"`{verdict_record_path}`" if verdict_record_path else "(not provided)")
    )
    lines.append("")

    lines.append("## Parsed Verdict and Next Action")
    lines.append("")
    lines.append("- Verdict: " + (f"`{verdict_str}`" if verdict_str else "(unknown)"))
    lines.append("- Next action: " + (f"`{next_action_kind}`" if next_action_kind else "(unknown)"))
    lines.append("")

    lines.append("## Why Work Is Blocked")
    lines.append("")
    lines.append(why_blocked)
    lines.append("")

    lines.append("## Required Human / External Input")
    lines.append("")
    lines.append(human_input_needed)
    lines.append("")

    if suggested_question is not None:
        lines.append("## Suggested Question Artifact")
        lines.append("")
        lines.append(
            "The blocker may be an ambiguous missing decision. The following "
            "question metadata is *suggested*; write it explicitly with the "
            "question API. It is not written automatically."
        )
        lines.append("")
        lines.append(f"- Question ID: `{suggested_question.question_id}`")
        lines.append(f"- Title: {suggested_question.title}")
        lines.append(f"- Status: `{suggested_question.status}`")
        lines.append(f"- Asker role: `{suggested_question.asker_role}`")
        lines.append(f"- Answerer role: `{suggested_question.answerer_role}`")
        lines.append("")

    lines.append("## Resume Checklist")
    lines.append("")
    for index, step in enumerate(resume_checklist, start=1):
        lines.append(f"{index}. {step}")
    lines.append("")

    lines.append("## Next Command After Resolution")
    lines.append("")
    if next_command.strip():
        lines.append("```powershell")
        lines.append(next_command.strip())
        lines.append("```")
    else:
        lines.append(
            "*(none provided; determine the corrective coding prompt or human "
            "override after the answer is recorded)*"
        )
    lines.append("")

    lines.append("## Boundaries")
    lines.append("")
    lines.append("Do not:")
    lines.append("")
    for entry in _BOUNDARIES:
        # Strip the leading "Do not " for the bulleted list while keeping the
        # source tuple self-describing for to_dict() consumers.
        text = entry[len("Do not ") :] if entry.startswith("Do not ") else entry
        lines.append(f"- {text}")
    lines.append("")

    lines.append("## Memory Posture")
    lines.append("")
    for entry in _MEMORY_POSTURE:
        lines.append(f"- {entry}")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"
