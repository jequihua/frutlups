"""Typed data model for coding prompt templates.

This module defines the typed inputs that M004 uses to render, preview,
and write coding prompt markdown deterministically. It provides:

- the prompt template data model (M004-S01)
- a pure validation helper (M004-S01)
- deterministic filename and dry-run preview helpers (M004-S02)
- an explicit write surface that writes only when invoked through a
  typed command (M004-S03)
- a deterministic markdown renderer that turns typed template data
  into a complete coding-prompt body (M004-S04)

The renderer is the only function in this module that produces prompt
markdown, and it produces that markdown from typed template data only.
The module does not synthesise prompt content from roadmap or
governance artifacts; callers still construct the
``CodingPromptTemplate`` themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from frutlups.layout import is_safe_relative
from frutlups.memory import MemoryPromptSnippet


def _is_within(child: Path, parent: Path) -> bool:
    """Return ``True`` when ``child`` is ``parent`` or nested under it."""

    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


@dataclass(frozen=True)
class CodingPromptTemplate:
    """Typed inputs for a single coding prompt.

    The template captures everything a future generator needs to render a
    deterministic coding prompt markdown file without depending on chat
    history or hidden state. Tuple-valued fields preserve insertion
    order; this is load-bearing for required reading and verification
    commands.
    """

    sequence: int
    milestone_id: str
    slice_id: str
    slug: str
    title: str
    role_instructions: str
    required_reading: tuple[str, ...]
    scope_paths: tuple[str, ...]
    non_goals: tuple[str, ...]
    definition_of_done: tuple[str, ...]
    verification_commands: tuple[str, ...]
    self_report_path: str
    notes: tuple[str, ...] = field(default=())
    memory_update: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "milestone_id": self.milestone_id,
            "slice_id": self.slice_id,
            "slug": self.slug,
            "title": self.title,
            "role_instructions": self.role_instructions,
            "required_reading": list(self.required_reading),
            "scope_paths": list(self.scope_paths),
            "non_goals": list(self.non_goals),
            "definition_of_done": list(self.definition_of_done),
            "verification_commands": list(self.verification_commands),
            "self_report_path": self.self_report_path,
            "notes": list(self.notes),
            "memory_update": self.memory_update,
        }


def validate_coding_prompt_template(
    template: CodingPromptTemplate,
) -> tuple[str, ...]:
    """Return a tuple of validation error messages (empty when valid).

    Validation is intentionally lightweight and pure: it does not touch
    the filesystem, does not look up roadmap context, and never raises
    for any constructible ``CodingPromptTemplate``. Malformed field
    values (for example, an ``int`` where a tuple of strings is
    expected, or ``None`` where a tuple is expected) produce
    deterministic human-readable error messages instead of exceptions.
    """

    errors: list[str] = []
    if not isinstance(template.sequence, int) or template.sequence <= 0:
        errors.append("sequence must be a positive integer")
    elif template.sequence > MAX_PROMPT_SEQUENCE:
        errors.append(f"sequence must be at most {MAX_PROMPT_SEQUENCE}")
    for field_name in (
        "milestone_id",
        "slice_id",
        "slug",
        "title",
        "role_instructions",
        "self_report_path",
    ):
        value = getattr(template, field_name)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{field_name} must be a non-empty string")
    for field_name in (
        "required_reading",
        "scope_paths",
        "definition_of_done",
        "verification_commands",
    ):
        errors.extend(
            _validate_string_collection(
                field_name, getattr(template, field_name), allow_empty=False
            )
        )
    # non_goals and notes may be empty, but if present, individual entries
    # must be non-empty strings.
    for field_name in ("non_goals", "notes"):
        errors.extend(
            _validate_string_collection(field_name, getattr(template, field_name), allow_empty=True)
        )
    return tuple(errors)


def _validate_string_collection(
    field_name: str,
    value: object,
    *,
    allow_empty: bool,
) -> list[str]:
    """Return deterministic errors for a string-collection field.

    The value is rejected if it is not a ``tuple`` or ``list`` (this
    catches malformed inputs such as ``None`` or a bare ``int`` without
    raising). Otherwise emptiness is checked according to ``allow_empty``
    and each entry must be a non-empty string. Never raises.
    """

    errors: list[str] = []
    if not isinstance(value, (tuple, list)):
        errors.append(f"{field_name} must be a tuple or list of non-empty strings")
        return errors
    if not value:
        if not allow_empty:
            errors.append(f"{field_name} must be non-empty")
        return errors
    for index, entry in enumerate(value):
        if not isinstance(entry, str) or not entry.strip():
            errors.append(f"{field_name}[{index}] must be a non-empty string")
    return errors


CODING_PROMPT_DIR = "prompts/for_coding_agent"
"""Repo-relative directory where coding prompts are written."""


REVIEW_PROMPT_DIR = "prompts/for_review_agent"
"""Repo-relative directory where matching review prompts live.

Used only by the renderer to display the matching review prompt path
inside a rendered coding prompt. No M004 surface writes into this
directory; review prompts are coder-authored handoff artifacts.
"""


MAX_PROMPT_SEQUENCE = 999
"""Inclusive upper bound on the project's three-digit prompt sequence convention.

Sequences above this require an explicit governance decision (a four-digit
convention is not currently part of the project). The constant is intentionally
exposed so tests and future tooling can reference the same boundary.
"""


def format_prompt_sequence(sequence: object) -> str:
    """Format a prompt sequence number using the project convention.

    Returns the sequence as a three-digit zero-padded string (``"001"``,
    ``"012"``, ``"999"``) for positive integers in the inclusive range
    ``1..MAX_PROMPT_SEQUENCE``. Returns ``""`` for anything else (zero,
    negative, ``None``, non-int, ``bool``, or any integer greater than
    ``MAX_PROMPT_SEQUENCE``). Never raises.
    """

    if isinstance(sequence, bool) or not isinstance(sequence, int):
        return ""
    if sequence <= 0:
        return ""
    if sequence > MAX_PROMPT_SEQUENCE:
        return ""
    return f"{sequence:03d}"


def coding_prompt_filename(template: CodingPromptTemplate) -> str:
    """Construct the deterministic coding-prompt filename for ``template``.

    Returns ``""`` when ``sequence`` cannot be formatted or when ``slug``
    is not a non-empty string. The slug is used verbatim (after stripping
    surrounding whitespace) so human-authored slugs are not silently
    normalised. Never raises and never inspects the filesystem.
    """

    sequence = format_prompt_sequence(template.sequence)
    if not sequence:
        return ""
    slug = template.slug if isinstance(template.slug, str) else ""
    slug = slug.strip()
    if not slug:
        return ""
    return f"{sequence}_{slug}.md"


@dataclass(frozen=True)
class CodingPromptPreview:
    """Dry-run preview of where a coding prompt would be written.

    The preview is intentionally non-mutating: it never touches the
    filesystem, never inspects the existing prompt directory for
    collisions, and never writes. ``wrote`` is always ``False`` for any
    preview returned by this slice. ``would_write`` is ``True`` only
    when the template validates; an invalid template would not produce
    a write even if a future explicit-write helper is invoked.
    """

    kind: str
    sequence: int | None
    sequence_formatted: str
    filename: str
    target_path: str
    valid: bool
    errors: tuple[str, ...]
    would_write: bool
    wrote: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "sequence": self.sequence,
            "sequence_formatted": self.sequence_formatted,
            "filename": self.filename,
            "target_path": self.target_path,
            "valid": self.valid,
            "errors": list(self.errors),
            "would_write": self.would_write,
            "wrote": self.wrote,
        }


def preview_coding_prompt(
    template: CodingPromptTemplate,
    prompt_dir: str = CODING_PROMPT_DIR,
) -> CodingPromptPreview:
    """Return a dry-run preview for the coding prompt described by ``template``.

    The preview composes the existing M004-S01 helpers:
    ``validate_coding_prompt_template`` produces the deterministic
    error tuple, and ``coding_prompt_filename`` produces the
    deterministic filename. ``prompt_dir`` is the repo-relative directory the
    prompt would be written to (defaults to the legacy
    ``prompts/for_coding_agent``); layout profiles pass their configured coding
    prompt directory so the preview target reflects where the write will land.
    The result is pure: no filesystem access, no global state, no writes, and
    never raises.
    """

    errors = validate_coding_prompt_template(template)
    valid = not errors
    filename = coding_prompt_filename(template)
    sequence_value: int | None
    if isinstance(template.sequence, bool) or not isinstance(template.sequence, int):
        sequence_value = None
    else:
        sequence_value = template.sequence
    sequence_formatted = format_prompt_sequence(template.sequence)
    directory = prompt_dir if isinstance(prompt_dir, str) and prompt_dir else CODING_PROMPT_DIR
    target_path = f"{directory}/{filename}" if filename else ""
    return CodingPromptPreview(
        kind="coding",
        sequence=sequence_value,
        sequence_formatted=sequence_formatted,
        filename=filename,
        target_path=target_path,
        valid=valid,
        errors=errors,
        would_write=valid,
        wrote=False,
    )


@dataclass(frozen=True)
class CodingPromptWriteCommand:
    """Explicit request to write a coding prompt file.

    The command is the only sanctioned entry point for package code to
    write a coding prompt. Construction never touches the filesystem;
    only ``write_coding_prompt`` does. ``content`` is the markdown text
    to write verbatim — this module does not render markdown from the
    template.
    """

    project_root: Path
    template: CodingPromptTemplate
    content: str
    overwrite: bool = False
    prompt_dir: str = CODING_PROMPT_DIR


@dataclass(frozen=True)
class CodingPromptWriteResult:
    """Result of an explicit coding-prompt write attempt.

    ``wrote`` is ``True`` only after a file has actually been written.
    ``overwrote`` is ``True`` only when an existing file at the target
    path was replaced (``overwrite=True`` had to be set on the
    command). On failure, ``target_path`` is the string form of the
    resolved target where possible, or ``""`` when the failure happened
    before a path could be safely computed.
    """

    preview: CodingPromptPreview
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


def write_coding_prompt(
    command: CodingPromptWriteCommand,
) -> CodingPromptWriteResult:
    """Write a coding prompt file when ``command`` is valid.

    The writer composes :func:`preview_coding_prompt`, validates
    ``content`` and overwrite policy, refuses to write outside the
    configured coding-prompt directory (``command.prompt_dir``, defaulting to
    the legacy ``prompts/for_coding_agent``), and writes the supplied markdown
    only when every check passes. The configured directory must be a safe
    repo-relative path that does not escape ``project_root``. Errors are
    deterministic human-readable strings. The writer is the only function in
    this module that touches the filesystem.
    """

    prompt_dir = command.prompt_dir if isinstance(command.prompt_dir, str) else ""
    if not prompt_dir:
        prompt_dir = CODING_PROMPT_DIR

    preview = preview_coding_prompt(command.template, prompt_dir=prompt_dir)
    errors: list[str] = list(preview.errors)

    if not isinstance(command.content, str) or not command.content:
        errors.append("content must be a non-empty string")

    if not is_safe_relative(prompt_dir):
        errors.append("prompt_dir must be a safe repo-relative path inside the template root")

    if errors:
        return CodingPromptWriteResult(
            preview=preview,
            target_path="",
            wrote=False,
            errors=tuple(errors),
            overwrote=False,
        )

    project_root = Path(command.project_root)
    coding_dir = project_root / PurePosixPath(prompt_dir)
    target = coding_dir / preview.filename

    try:
        resolved_root = project_root.resolve(strict=False)
        resolved_target_parent = target.parent.resolve(strict=False)
        resolved_coding_dir = coding_dir.resolve(strict=False)
    except OSError as exc:
        return CodingPromptWriteResult(
            preview=preview,
            target_path="",
            wrote=False,
            errors=(f"target path could not be resolved: {exc}",),
            overwrote=False,
        )
    if resolved_target_parent != resolved_coding_dir:
        return CodingPromptWriteResult(
            preview=preview,
            target_path="",
            wrote=False,
            errors=(f"target path must resolve inside {prompt_dir}/",),
            overwrote=False,
        )
    if not _is_within(resolved_coding_dir, resolved_root):
        return CodingPromptWriteResult(
            preview=preview,
            target_path="",
            wrote=False,
            errors=("target directory must resolve inside the project/template root",),
            overwrote=False,
        )

    target_existed = target.exists()
    if target_existed and not command.overwrite:
        return CodingPromptWriteResult(
            preview=preview,
            target_path=str(target),
            wrote=False,
            errors=(f"{preview.filename} already exists; pass overwrite=True to replace it",),
            overwrote=False,
        )

    coding_dir.mkdir(parents=True, exist_ok=True)
    # Exact bytes: the supplied content is persisted verbatim (LF), never
    # platform-translated.
    target.write_bytes(command.content.encode("utf-8"))

    return CodingPromptWriteResult(
        preview=preview,
        target_path=str(target),
        wrote=True,
        errors=(),
        overwrote=target_existed,
    )


REQUIRED_READING_BASELINE: tuple[str, ...] = ("CLAUDE.md", "README.md")
"""Files every rendered coding prompt must visibly require.

If the template's ``required_reading`` omits either of these, the
renderer surfaces a deterministic error rather than silently inserting
them. This matches the M004-S02 corrective decision to keep
human-authored mistakes visible instead of normalising them.
"""


@dataclass(frozen=True)
class CodingPromptRenderResult:
    """Rendered markdown content for a coding prompt.

    ``content`` is the full markdown body that a coder should treat as
    the prompt. ``valid`` is ``True`` iff ``errors`` is empty; when
    invalid, ``content`` is ``""`` so callers cannot accidentally
    write unusable content to disk.
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


def render_coding_prompt(
    template: CodingPromptTemplate,
    snippet: MemoryPromptSnippet | None = None,
) -> CodingPromptRenderResult:
    """Render a deterministic coding-prompt markdown body for ``template``.

    The renderer composes :func:`validate_coding_prompt_template`,
    enforces the :data:`REQUIRED_READING_BASELINE` rule, and produces
    a markdown body whose section ordering is stable across runs. It
    never writes files, never inspects the filesystem, never mutates
    its input, and never raises for malformed template fields covered
    by existing validation.

    Returns a :class:`CodingPromptRenderResult`. When the template
    cannot produce usable content, ``content`` is ``""`` and
    ``errors`` contains the deterministic error messages.
    """

    errors: list[str] = list(validate_coding_prompt_template(template))

    if isinstance(template.required_reading, (tuple, list)):
        seen = {entry for entry in template.required_reading if isinstance(entry, str)}
        for baseline in REQUIRED_READING_BASELINE:
            if baseline not in seen:
                errors.append(f"required_reading must include {baseline}")

    if errors:
        return CodingPromptRenderResult(
            content="",
            valid=False,
            errors=tuple(errors),
        )

    content = _render_markdown(template, snippet=snippet)
    return CodingPromptRenderResult(
        content=content,
        valid=True,
        errors=(),
    )


def _render_markdown(
    template: CodingPromptTemplate,
    snippet: MemoryPromptSnippet | None = None,
) -> str:
    sequence_text = format_prompt_sequence(template.sequence)
    review_filename = (
        f"{sequence_text}_review_{template.slug}.md" if sequence_text and template.slug else ""
    )
    review_path = f"{REVIEW_PROMPT_DIR}/{review_filename}" if review_filename else ""

    lines: list[str] = []

    lines.append(f"# Coding Prompt {sequence_text}: frutlups {template.slice_id} {template.title}")
    lines.append("")

    lines.append("## Role")
    lines.append("")
    lines.append(template.role_instructions.strip())
    lines.append("")

    lines.append("## Active Roadmap Item")
    lines.append("")
    lines.append(f"Active roadmap milestone: `{template.milestone_id}`")
    lines.append("")
    lines.append(f"Detailed roadmap slice: `{template.slice_id}: {template.title}`")
    lines.append("")

    lines.append("## Required Reading")
    lines.append("")
    for entry in template.required_reading:
        lines.append(f"- `{entry}`")
    lines.append("")

    lines.append("## Scope")
    lines.append("")
    lines.append("Work under the following paths:")
    lines.append("")
    for entry in template.scope_paths:
        lines.append(f"- `{entry}`")
    lines.append("")

    lines.append("## Non-Goals")
    lines.append("")
    if template.non_goals:
        for entry in template.non_goals:
            lines.append(f"- {entry}")
    else:
        lines.append("- *(none specified)*")
    lines.append("")

    lines.append("## Definition of Done")
    lines.append("")
    for entry in template.definition_of_done:
        lines.append(f"- {entry}")
    lines.append("")

    lines.append("## Verification Commands")
    lines.append("")
    lines.append("Run from the package workspace:")
    lines.append("")
    lines.append("```powershell")
    for entry in template.verification_commands:
        lines.append(entry)
    lines.append("```")
    lines.append("")

    lines.append("## Required Self-Report")
    lines.append("")
    lines.append("Write a self-report at:")
    lines.append("")
    lines.append(f"`{template.self_report_path}`")
    lines.append("")
    lines.append("The self-report must include, at minimum:")
    lines.append("")
    lines.append("- files changed")
    lines.append("- behavior implemented")
    lines.append("- tests added or updated")
    lines.append("- verification commands and results")
    lines.append("- live status summary")
    lines.append("- known limits and intentional deferrals")
    lines.append("- memory usage statement")
    lines.append("- matching review prompt path created by the coder")
    lines.append("- blockers or open questions")
    lines.append("")

    lines.append("## Matching Review Prompt")
    lines.append("")
    lines.append("Loop convention:")
    lines.append("")
    lines.append("1. the architect/reviewer creates this coding prompt")
    lines.append("2. the coder executes the coding prompt")
    lines.append("3. the coder writes the self-report")
    lines.append("4. the coder then creates the matching review prompt")
    lines.append("5. the reviewer executes the review prompt")
    lines.append("")
    lines.append("Create the matching review prompt at:")
    lines.append("")
    lines.append(f"`{review_path}`")
    lines.append("")

    if snippet is not None and snippet.has_content:
        lines.append("## Optional Memory Context")
        lines.append("")
        lines.append(
            "The following is summarized read-only evidence from the "
            "project memory backend. Repository artifacts remain "
            "authoritative; use this only as optional context."
        )
        lines.append("")
        for evidence_line in snippet.lines:
            lines.append(f"- {evidence_line}")
        lines.append("")

    lines.append("## llloom Integration Posture")
    lines.append("")
    if template.memory_update:
        lines.append(
            "This is an explicit memory-update slice. Memory mutation is "
            "permitted only within this slice and only with review evidence. "
            "Repository artifacts remain authoritative over memory; memory "
            "updates must not contradict or replace primary source artifacts. "
            "Read the current instructions in "
            "`05_governance/llloom_operating_model.md` before planning any "
            "mutating command. Isolate all command construction behind "
            "patchable interfaces and require a passing review before "
            "applying seed manifests or other mutating operations."
        )
    else:
        lines.append(
            "Follow the current project memory instructions defined in "
            "`05_governance/llloom_operating_model.md`. Do not mutate "
            "`llloom` workspaces during normal coding or review slices. "
            "If memory integration is required, isolate command "
            "construction behind small patchable interfaces and check "
            "current upstream instructions first."
        )
    lines.append("")

    if template.notes:
        lines.append("## Notes")
        lines.append("")
        for entry in template.notes:
            lines.append(f"- {entry}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
