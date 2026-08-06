"""Question artifact generator.

Makes uncertainty durable and reviewable. When a coder, reviewer, or
architect/human handoff lacks enough evidence to proceed safely, a question
artifact records what is being asked, why it blocks or redirects work, which
repository artifacts provide context, who should answer, and what decision or
next command should follow.

This module provides:

- :class:`QuestionArtifactTemplate` — typed, frozen input for one question
- :func:`validate_question_artifact_template` — pure deterministic validation
- :func:`render_question_artifact` — pure deterministic markdown renderer
- :func:`question_artifact_filename` / :func:`preview_question_artifact` —
  deterministic filename and dry-run preview helpers
- :class:`QuestionArtifactWriteCommand` / :func:`write_question_artifact` — the
  only sanctioned surface that touches the filesystem

Rendering is pure and read-only. Writing happens only when
:func:`write_question_artifact` is invoked explicitly, never as a side effect of
rendering. No agent dispatch, memory mutation, roadmap mutation, or verdict
recording occurs anywhere in this module. Roles are logical and
provider-neutral; no provider or model family is required to ask or answer a
question.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

QUESTION_DIR = "05_governance/questions"
"""Repo-relative directory where question artifacts are written."""


QUESTION_STATUSES: tuple[str, ...] = ("open", "answered", "closed", "superseded")
"""Allowed lifecycle statuses for a question artifact."""


QUESTION_ROLES: tuple[str, ...] = ("architect", "reviewer", "coder", "human")
"""Allowed logical roles for askers and answerers (provider-neutral)."""


QUESTION_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
"""Safe question-id pattern: lowercase alnum start, then alnum/``-``/``_``.

Deliberately forbids ``/``, ``.``, ``..``, whitespace, and uppercase so a
caller-supplied id cannot produce path traversal, an absolute path, or a
surprising filename.
"""


@dataclass(frozen=True)
class QuestionArtifactTemplate:
    """Typed inputs for a single question artifact.

    ``status`` must be one of :data:`QUESTION_STATUSES`; ``asker_role`` and
    ``answerer_role`` must be one of :data:`QUESTION_ROLES`. ``milestone_id``,
    ``slice_id``, and ``next_action`` are optional strings (empty means
    unknown). ``context_paths``, ``options``, and ``notes`` are ordered tuples
    whose order is load-bearing and preserved verbatim.
    """

    question_id: str
    title: str
    question: str
    rationale: str
    asker_role: str
    answerer_role: str
    status: str = "open"
    milestone_id: str = ""
    slice_id: str = ""
    context_paths: tuple[str, ...] = field(default=())
    options: tuple[str, ...] = field(default=())
    next_action: str = ""
    notes: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, object]:
        return {
            "question_id": self.question_id,
            "title": self.title,
            "question": self.question,
            "rationale": self.rationale,
            "asker_role": self.asker_role,
            "answerer_role": self.answerer_role,
            "status": self.status,
            "milestone_id": self.milestone_id,
            "slice_id": self.slice_id,
            "context_paths": list(self.context_paths),
            "options": list(self.options),
            "next_action": self.next_action,
            "notes": list(self.notes),
        }


def validate_question_artifact_template(
    template: QuestionArtifactTemplate,
) -> tuple[str, ...]:
    """Return a tuple of validation error messages (empty when valid).

    Pure and deterministic: no filesystem access, no clock reads, and never
    raises for any constructible ``QuestionArtifactTemplate`` (malformed field
    types produce deterministic messages instead of exceptions).
    """

    errors: list[str] = []

    qid = template.question_id
    if not isinstance(qid, str) or not qid.strip():
        errors.append("question_id must be a non-empty string")
    elif not QUESTION_ID_RE.match(qid):
        errors.append(
            "question_id must be lowercase letters, digits, '-' or '_' and "
            "start with a letter or digit"
        )

    for field_name in ("title", "question", "rationale"):
        value = getattr(template, field_name)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{field_name} must be a non-empty string")

    if not isinstance(template.status, str) or template.status not in QUESTION_STATUSES:
        errors.append(f"status must be one of {', '.join(QUESTION_STATUSES)}")

    if not isinstance(template.asker_role, str) or template.asker_role not in QUESTION_ROLES:
        errors.append(f"asker_role must be one of {', '.join(QUESTION_ROLES)}")
    if not isinstance(template.answerer_role, str) or template.answerer_role not in QUESTION_ROLES:
        errors.append(f"answerer_role must be one of {', '.join(QUESTION_ROLES)}")

    for field_name in ("milestone_id", "slice_id", "next_action"):
        value = getattr(template, field_name)
        if not isinstance(value, str):
            errors.append(f"{field_name} must be a string")

    for field_name in ("context_paths", "options", "notes"):
        errors.extend(_validate_string_collection(field_name, getattr(template, field_name)))

    return tuple(errors)


def _validate_string_collection(field_name: str, value: object) -> list[str]:
    """Return deterministic errors for an optional string-collection field."""

    errors: list[str] = []
    if not isinstance(value, (tuple, list)):
        errors.append(f"{field_name} must be a tuple or list of non-empty strings")
        return errors
    for index, entry in enumerate(value):
        if not isinstance(entry, str) or not entry.strip():
            errors.append(f"{field_name}[{index}] must be a non-empty string")
    return errors


def question_artifact_filename(template: QuestionArtifactTemplate) -> str:
    """Return the deterministic ``<question_id>.md`` filename, or ``""``.

    Returns ``""`` when ``question_id`` does not satisfy
    :data:`QUESTION_ID_RE`. Because the pattern forbids ``/``, ``.``, and
    ``..``, the returned name cannot encode a path or escape its directory.
    Never raises and never inspects the filesystem.
    """

    qid = template.question_id
    if not isinstance(qid, str) or not QUESTION_ID_RE.match(qid):
        return ""
    return f"{qid}.md"


@dataclass(frozen=True)
class QuestionArtifact:
    """Rendered markdown content for a question artifact.

    ``content`` is the full markdown body. ``valid`` is ``True`` iff
    ``errors`` is empty; when invalid, ``content`` is ``""`` so callers
    cannot accidentally write unusable content to disk.
    """

    content: str
    valid: bool
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "content": self.content,
            "valid": self.valid,
            "errors": list(self.errors),
        }


def render_question_artifact(
    template: QuestionArtifactTemplate,
) -> QuestionArtifact:
    """Render a deterministic question-artifact markdown body for ``template``.

    Composes :func:`validate_question_artifact_template`. Returns
    ``valid=False`` with ``content=""`` and the deterministic error tuple
    when validation fails. Otherwise emits markdown with stable section
    order and caller-supplied list order preserved. Never writes files,
    never inspects the filesystem, and never raises for constructible
    templates.
    """

    errors = validate_question_artifact_template(template)
    if errors:
        return QuestionArtifact(content="", valid=False, errors=errors)
    return QuestionArtifact(content=_render_markdown(template), valid=True, errors=())


def _render_markdown(template: QuestionArtifactTemplate) -> str:
    lines: list[str] = []

    lines.append(f"# Question: {template.title.strip()}")
    lines.append("")

    lines.append(f"- Question ID: `{template.question_id}`")
    lines.append(f"- Status: `{template.status}`")
    lines.append(f"- Asker role: `{template.asker_role}`")
    lines.append(f"- Answerer role: `{template.answerer_role}`")
    lines.append(
        "- Related milestone: "
        + (f"`{template.milestone_id}`" if template.milestone_id else "none")
    )
    lines.append("- Related slice: " + (f"`{template.slice_id}`" if template.slice_id else "none"))
    lines.append("")

    lines.append("## Question")
    lines.append("")
    lines.append(template.question.strip())
    lines.append("")

    lines.append("## Why It Matters")
    lines.append("")
    lines.append(template.rationale.strip())
    lines.append("")

    lines.append("## Context Artifacts")
    lines.append("")
    if template.context_paths:
        for entry in template.context_paths:
            lines.append(f"- `{entry}`")
    else:
        lines.append("- *(none provided)*")
    lines.append("")

    lines.append("## Options / Candidate Decisions")
    lines.append("")
    if template.options:
        for entry in template.options:
            lines.append(f"- {entry}")
    else:
        lines.append("- *(none provided)*")
    lines.append("")

    lines.append("## Recommended Next Action")
    lines.append("")
    if template.next_action.strip():
        lines.append(template.next_action.strip())
    else:
        lines.append("*(none provided; await an answer before proceeding)*")
    lines.append("")

    lines.append("## Answer")
    lines.append("")
    lines.append(
        f"*(to be completed by the `{template.answerer_role}`; leave blank until answered)*"
    )
    lines.append("")

    lines.append("## Resolution Notes")
    lines.append("")
    lines.append("*(to be completed when the question is answered, closed, or superseded)*")
    lines.append("")

    lines.append("## Stop / Route Guidance")
    lines.append("")
    lines.append(
        "This question records blocking or redirecting ambiguity. If it blocks "
        "safe implementation or review, stop and route the question to a human "
        "or architect for an answer rather than speculative coding or review. "
        "Repository artifacts remain the source of truth; do not advance "
        "roadmap state or record a verdict to work around an open question."
    )
    lines.append("")

    if template.notes:
        lines.append("## Notes")
        lines.append("")
        for entry in template.notes:
            lines.append(f"- {entry}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


@dataclass(frozen=True)
class QuestionArtifactPreview:
    """Dry-run preview of where a question artifact would be written.

    Never touches the filesystem, never writes, and ``wrote`` is always
    ``False``. ``would_write`` is ``True`` only when the template validates.
    """

    kind: str
    question_id: str
    filename: str
    target_path: str
    valid: bool
    errors: tuple[str, ...]
    would_write: bool
    wrote: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "question_id": self.question_id,
            "filename": self.filename,
            "target_path": self.target_path,
            "valid": self.valid,
            "errors": list(self.errors),
            "would_write": self.would_write,
            "wrote": self.wrote,
        }


def preview_question_artifact(
    template: QuestionArtifactTemplate,
) -> QuestionArtifactPreview:
    """Return a dry-run preview for the question artifact ``template``.

    Composes :func:`validate_question_artifact_template` and
    :func:`question_artifact_filename`. Never inspects the filesystem,
    never writes, and never raises for constructible templates.
    """

    errors = validate_question_artifact_template(template)
    valid = not errors
    filename = question_artifact_filename(template)
    target_path = f"{QUESTION_DIR}/{filename}" if filename else ""
    question_id = template.question_id if isinstance(template.question_id, str) else ""
    return QuestionArtifactPreview(
        kind="question",
        question_id=question_id,
        filename=filename,
        target_path=target_path,
        valid=valid,
        errors=errors,
        would_write=valid,
        wrote=False,
    )


@dataclass(frozen=True)
class QuestionArtifactWriteCommand:
    """Explicit request to write a question artifact file.

    The command is the only sanctioned entry point for package code to write
    a question artifact. Content is rendered internally via
    :func:`render_question_artifact` so callers cannot supply stale content.
    Construction never touches the filesystem; only
    :func:`write_question_artifact` does.
    """

    project_root: Path
    template: QuestionArtifactTemplate
    overwrite: bool = False


@dataclass(frozen=True)
class QuestionArtifactWriteResult:
    """Result of an explicit question-artifact write attempt.

    ``wrote`` is ``True`` only after a file has actually been written.
    ``overwrote`` is ``True`` only when an existing file at the target path
    was replaced (``overwrite=True`` had to be set on the command). On
    failure, ``target_path`` is the resolved target string where possible, or
    ``""`` when failure happened before a path could be safely computed.
    """

    preview: QuestionArtifactPreview
    target_path: str
    wrote: bool
    errors: tuple[str, ...]
    overwrote: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "preview": self.preview.to_dict(),
            "target_path": self.target_path,
            "wrote": self.wrote,
            "errors": list(self.errors),
            "overwrote": self.overwrote,
        }


def write_question_artifact(
    command: QuestionArtifactWriteCommand,
) -> QuestionArtifactWriteResult:
    """Write a question artifact file when ``command`` is valid.

    Composes :func:`preview_question_artifact` and
    :func:`render_question_artifact`, refusing to write when validation or
    rendering fails. Refuses to write outside :data:`QUESTION_DIR`, refuses to
    overwrite an existing file unless ``command.overwrite`` is ``True``, and
    creates the directory only on a successful write. Returns deterministic
    errors for all failure paths. This is the only function in this module
    that touches the filesystem.
    """

    preview = preview_question_artifact(command.template)
    if preview.errors:
        return QuestionArtifactWriteResult(
            preview=preview,
            target_path="",
            wrote=False,
            errors=preview.errors,
            overwrote=False,
        )

    render_result = render_question_artifact(command.template)
    if not render_result.valid or not render_result.content:
        render_errors = (
            render_result.errors
            if render_result.errors
            else ("render_question_artifact returned invalid or empty content",)
        )
        return QuestionArtifactWriteResult(
            preview=preview,
            target_path="",
            wrote=False,
            errors=tuple(render_errors),
            overwrote=False,
        )

    project_root = Path(command.project_root)
    questions_dir = project_root / "05_governance" / "questions"
    target = questions_dir / preview.filename

    try:
        resolved_target_parent = target.parent.resolve(strict=False)
        resolved_questions_dir = questions_dir.resolve(strict=False)
    except OSError as exc:
        return QuestionArtifactWriteResult(
            preview=preview,
            target_path="",
            wrote=False,
            errors=(f"target path could not be resolved: {exc}",),
            overwrote=False,
        )

    if resolved_target_parent != resolved_questions_dir:
        return QuestionArtifactWriteResult(
            preview=preview,
            target_path="",
            wrote=False,
            errors=("target path must resolve inside 05_governance/questions/",),
            overwrote=False,
        )

    target_existed = target.exists()
    if target_existed and not command.overwrite:
        return QuestionArtifactWriteResult(
            preview=preview,
            target_path=str(target),
            wrote=False,
            errors=(f"{preview.filename} already exists; pass overwrite=True to replace it",),
            overwrote=False,
        )

    questions_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(render_result.content, encoding="utf-8")

    return QuestionArtifactWriteResult(
        preview=preview,
        target_path=str(target),
        wrote=True,
        errors=(),
        overwrote=target_existed,
    )
