"""Typed data model, self-report evidence derivation, rendering, and
explicit write surface for review prompts.

This module provides:

- the M006-S01 typed review-prompt template + validator
- the M006-S02 self-report evidence bridge: derives ordered
  ``expected_changed_files`` and ``verification_commands`` from a
  validated self-report so a future renderer / writer can compose
  them into a review prompt
- the M006-S03 deterministic review-prompt renderer that turns a
  valid ``ReviewPromptTemplate`` into reviewer-executable markdown
  content, with severity-guidance and verdict-requirement
  governance enforced by the validator
- the M006-S04 explicit write surface: deterministic filename
  helper, dry-run preview, and the only package-code entry point
  that writes review prompt files under ``prompts/for_review_agent/``

The module remains filesystem-isolated except for
:func:`write_review_prompt`, which is the sole sanctioned writer.
Verdict parsing is M007; the M007 verdict enum is intentionally not
introduced here. ``verdict_choices`` remains plain strings in the
M006 template model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from frutlups.layout import is_safe_relative
from frutlups.prompt_template import MAX_PROMPT_SEQUENCE, format_prompt_sequence
from frutlups.self_report import (
    SelfReportValidationResult,
    find_self_report_section,
)


def _is_within(child: Path, parent: Path) -> bool:
    """Return ``True`` when ``child`` is ``parent`` or nested under it."""

    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


REVIEW_REQUIRED_READING_BASELINE: tuple[str, ...] = ("CLAUDE.md", "README.md")
"""Files every review-prompt template must visibly require.

Matches the M004-S04 ``REQUIRED_READING_BASELINE`` for coding
prompts. Missing entries are surfaced as deterministic validation
errors rather than silently inserted.
"""


REVIEW_SEVERITY_CATEGORIES: tuple[str, ...] = (
    "blocker",
    "major",
    "minor",
    "nit",
)
"""Canonical severity categories every review prompt must address.

Each entry in ``ReviewPromptTemplate.severity_guidance`` is
matched case-insensitively against a leading ``<category>:``
prefix. The validator requires at least one non-empty guidance
entry per category so generated review prompts cannot omit the
governance gate.
"""


REVIEW_VERDICT_CHOICES: tuple[str, ...] = (
    "pass",
    "needs_work",
    "blocked",
    "override",
)
"""Canonical verdict choices every review prompt must offer.

The verdict vocabulary is intentionally a tuple of plain strings
in M006; the M007 verdict enum has not been introduced yet.
Missing choices are surfaced as deterministic validation errors
rather than silently inserted.
"""


@dataclass(frozen=True)
class ReviewPromptTemplate:
    """Typed inputs for a single review prompt.

    The template captures the structured data a future M006
    review-prompt renderer needs without itself rendering or
    writing markdown. Tuple-valued fields preserve insertion
    order; that ordering is load-bearing for required reading,
    expected changed files, verification commands, severity
    guidance, verdict choices, prior review paths, non-goals,
    and notes.

    ``verdict_choices`` is intentionally a tuple of plain
    strings in this slice; the M007 verdict enum has not been
    introduced yet, so review prompts treat verdict labels as
    prompt-template content for now.
    """

    sequence: int
    milestone_id: str
    slice_id: str
    slug: str
    title: str
    role_instructions: str
    required_reading: tuple[str, ...]
    coding_prompt_path: str
    self_report_path: str
    review_output_path: str
    expected_changed_files: tuple[str, ...]
    verification_commands: tuple[str, ...]
    severity_guidance: tuple[str, ...]
    verdict_choices: tuple[str, ...]
    prior_review_paths: tuple[str, ...] = field(default=())
    non_goals: tuple[str, ...] = field(default=())
    notes: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "milestone_id": self.milestone_id,
            "slice_id": self.slice_id,
            "slug": self.slug,
            "title": self.title,
            "role_instructions": self.role_instructions,
            "required_reading": list(self.required_reading),
            "coding_prompt_path": self.coding_prompt_path,
            "self_report_path": self.self_report_path,
            "review_output_path": self.review_output_path,
            "expected_changed_files": list(self.expected_changed_files),
            "verification_commands": list(self.verification_commands),
            "severity_guidance": list(self.severity_guidance),
            "verdict_choices": list(self.verdict_choices),
            "prior_review_paths": list(self.prior_review_paths),
            "non_goals": list(self.non_goals),
            "notes": list(self.notes),
        }


def validate_review_prompt_template(
    template: ReviewPromptTemplate,
) -> tuple[str, ...]:
    """Return a tuple of validation error messages (empty when valid).

    Validation is pure: it does not touch the filesystem, does not
    inspect live repository state, and never raises for any
    constructible ``ReviewPromptTemplate``. Malformed collection
    fields (for example, an ``int`` where a tuple of strings is
    expected, or ``None`` where a tuple is expected) produce
    deterministic human-readable error messages instead of
    exceptions. Error order is stable.
    """

    errors: list[str] = []
    if (
        not isinstance(template.sequence, int)
        or isinstance(template.sequence, bool)
        or template.sequence <= 0
    ):
        errors.append("sequence must be a positive integer")
    elif template.sequence > MAX_PROMPT_SEQUENCE:
        errors.append(f"sequence must be at most {MAX_PROMPT_SEQUENCE}")
    for field_name in (
        "milestone_id",
        "slice_id",
        "slug",
        "title",
        "role_instructions",
        "coding_prompt_path",
        "self_report_path",
        "review_output_path",
    ):
        value = getattr(template, field_name)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{field_name} must be a non-empty string")
    for field_name in (
        "required_reading",
        "expected_changed_files",
        "verification_commands",
        "severity_guidance",
        "verdict_choices",
    ):
        errors.extend(
            _validate_string_collection(
                field_name, getattr(template, field_name), allow_empty=False
            )
        )
    for field_name in ("prior_review_paths", "non_goals", "notes"):
        errors.extend(
            _validate_string_collection(field_name, getattr(template, field_name), allow_empty=True)
        )
    if isinstance(template.required_reading, (tuple, list)):
        seen = {entry for entry in template.required_reading if isinstance(entry, str)}
        for baseline in REVIEW_REQUIRED_READING_BASELINE:
            if baseline not in seen:
                errors.append(f"required_reading must include {baseline}")
    if isinstance(template.severity_guidance, (tuple, list)):
        present_categories = _present_severity_categories(template.severity_guidance)
        for category in REVIEW_SEVERITY_CATEGORIES:
            if category not in present_categories:
                errors.append(f"severity_guidance must include a {category} entry")
    if isinstance(template.verdict_choices, (tuple, list)):
        present_verdicts = {
            entry.strip().lower() for entry in template.verdict_choices if isinstance(entry, str)
        }
        for verdict in REVIEW_VERDICT_CHOICES:
            if verdict not in present_verdicts:
                errors.append(f"verdict_choices must include {verdict}")
    return tuple(errors)


def _present_severity_categories(
    severity_guidance: object,
) -> set[str]:
    """Return the set of severity categories present in ``severity_guidance``.

    Each entry is matched case-insensitively against a leading
    ``<category>:`` prefix from :data:`REVIEW_SEVERITY_CATEGORIES`.
    Non-string entries and entries with no ``:`` delimiter are
    skipped.
    """

    present: set[str] = set()
    if not isinstance(severity_guidance, (tuple, list)):
        return present
    for entry in severity_guidance:
        if not isinstance(entry, str):
            continue
        head, separator, _ = entry.strip().partition(":")
        if not separator:
            continue
        candidate = head.strip().lower()
        if candidate in REVIEW_SEVERITY_CATEGORIES:
            present.add(candidate)
    return present


def _validate_string_collection(
    field_name: str,
    value: object,
    *,
    allow_empty: bool,
) -> list[str]:
    """Return deterministic errors for a string-collection field.

    Mirrors the M004-S01 ``prompt_template._validate_string_collection``
    helper: rejects non-tuple / non-list inputs first (catching
    ``None`` and bare ``int`` without raising), then enforces
    non-empty string entries. Never raises.
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


@dataclass(frozen=True)
class ReviewPromptEvidenceCommand:
    """Request to derive review-prompt evidence from a validated self-report.

    ``validation`` is a ``SelfReportValidationResult`` produced by
    :func:`frutlups.self_report.validate_expected_self_report`.
    Accepting a pre-validated result keeps the derivation pure (no
    filesystem access here) and lets callers validate once and
    derive evidence multiple times if needed.
    """

    validation: SelfReportValidationResult


@dataclass(frozen=True)
class ReviewPromptEvidenceResult:
    """Ordered evidence extracted from a self-report for a review prompt.

    ``expected_changed_files`` and ``verification_commands`` are
    ordered tuples that preserve the first appearance of each entry
    (duplicates dropped). ``errors`` is a deterministic tuple of
    human-readable messages; when non-empty, the evidence tuples
    are empty (the helper fails closed rather than producing
    partial evidence).
    """

    expected_changed_files: tuple[str, ...]
    verification_commands: tuple[str, ...]
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "expected_changed_files": list(self.expected_changed_files),
            "verification_commands": list(self.verification_commands),
            "errors": list(self.errors),
        }


def derive_review_prompt_evidence(
    command: ReviewPromptEvidenceCommand,
) -> ReviewPromptEvidenceResult:
    """Derive review-prompt evidence from a validated self-report.

    Composes the M005-S03 parsed self-report and aliases. Looks up
    the canonical ``files changed`` and ``verification commands
    and results`` sections (via the same alias map M005-S03
    accepts), extracts ordered entries with duplicate removal, and
    fails closed with deterministic errors when the self-report
    is invalid, unparsed, missing required sections, or yields
    empty evidence collections. Never raises, never writes files,
    never reads memory.
    """

    validation = command.validation
    if not validation.valid:
        return ReviewPromptEvidenceResult(
            expected_changed_files=(),
            verification_commands=(),
            errors=tuple(validation.errors),
        )

    parsed = validation.parsed
    if parsed is None:
        return ReviewPromptEvidenceResult(
            expected_changed_files=(),
            verification_commands=(),
            errors=("self-report could not be parsed; no evidence available",),
        )

    errors: list[str] = []
    files_section = find_self_report_section(parsed, "files changed")
    files_changed: tuple[str, ...] = ()
    if files_section is None:
        errors.append("self-report has no files changed section to derive evidence from")
    elif not isinstance(files_section.body, str):
        errors.append("self-report files changed section body must be a string")
    else:
        files_changed = _extract_changed_files(files_section.body)
        if not files_changed:
            errors.append("self-report files changed section is empty after extraction")

    verification_section = find_self_report_section(parsed, "verification commands and results")
    verification: tuple[str, ...] = ()
    if verification_section is None:
        errors.append("self-report has no verification commands section to derive evidence from")
    elif not isinstance(verification_section.body, str):
        errors.append("self-report verification commands section body must be a string")
    else:
        verification = _extract_verification_commands(verification_section.body)
        if not verification:
            errors.append("self-report verification commands section is empty after extraction")

    if errors:
        return ReviewPromptEvidenceResult(
            expected_changed_files=(),
            verification_commands=(),
            errors=tuple(errors),
        )
    return ReviewPromptEvidenceResult(
        expected_changed_files=files_changed,
        verification_commands=verification,
        errors=(),
    )


_BULLET_RE = re.compile(r"^\s*[-*]\s+(?P<rest>.+?)\s*$")
_NUMBERED_RE = re.compile(r"^\s*\d+[.)]\s+(?P<rest>.+?)\s*$")
_FENCE_RE = re.compile(r"^\s*```")
_FENCE_LANG_RE = re.compile(r"^\s*```(\w*)\s*$")
_PATH_LABEL_RE = re.compile(r"(?i)^path:\s*(?P<rest>.+?)\s*$")
_COMMAND_LABEL_RE = re.compile(r"(?i)^command:\s*(?P<rest>.+?)\s*$")
_RESULT_PROSE_PREFIXES = ("result:", "observed", "expected")
_FILES_CHANGED_IGNORE_PREFIXES = (
    "reason:",
    "notes:",
    "note:",
    "status:",
    "result:",
)
_RESULT_PROSE_EXACT = frozenset({"passed", "ok", "fail", "failed"})
_BACKTICK_VALUE_RE = re.compile(r"`([^`]+)`")
_PATH_DESC_SEP_RE = re.compile(r"\s*(?:—|-{2,})\s*|\s+\(")
_LOOKS_LIKE_PATH_RE = re.compile(r"[/.]")
_COMMAND_FENCE_LANGS = frozenset({"powershell", "bash", "shell", "sh", "zsh", "ps1"})


def _strip_bullet_or_number(line: str) -> str:
    match = _BULLET_RE.match(line) or _NUMBERED_RE.match(line)
    if match is not None:
        return match.group("rest").strip()
    return line.strip()


def _extract_file_path(candidate: str) -> str:
    """Return the repo-relative path from a bullet entry candidate.

    Prefers the first backtick-wrapped value when present; otherwise
    strips any description separator and everything after it. Returns
    empty string when the result does not look like a file path.
    """
    bt_match = _BACKTICK_VALUE_RE.search(candidate)
    if bt_match is not None:
        path = bt_match.group(1).strip()
    else:
        path = _PATH_DESC_SEP_RE.split(candidate, maxsplit=1)[0].strip()
    if not _LOOKS_LIKE_PATH_RE.search(path):
        return ""
    return path


def _extract_changed_files(body: str) -> tuple[str, ...]:
    """Extract ordered repo-relative path strings from a files-changed body.

    Only processes entry-introducing lines (bullets ``-``/``*``, numbered
    ``1.``, or ``path:`` labels). Continuation lines that do not begin a
    new entry are skipped so that helper-name prose and description text
    following a path bullet are never treated as changed-file entries.
    Within each entry, only the path portion is retained; backtick-wrapped
    paths are preferred and description suffixes after ``—``, ``--``, or
    ``(`` are stripped. Entries that do not look like file paths (no ``/``
    or ``.``) are dropped. Duplicates are dropped while preserving first
    appearance.
    """

    seen: set[str] = set()
    results: list[str] = []
    for line in body.splitlines():
        entry_match = _BULLET_RE.match(line) or _NUMBERED_RE.match(line)
        if entry_match is None:
            continue
        candidate = entry_match.group("rest").strip()
        path_match = _PATH_LABEL_RE.match(candidate)
        if path_match is not None:
            candidate = path_match.group("rest").strip()
        path = _extract_file_path(candidate)
        if not path:
            continue
        if path in seen:
            continue
        seen.add(path)
        results.append(path)
    return tuple(results)


def _iter_command_lines(body: str):
    """Yield command-candidate lines from ``body``.

    When the body contains no fenced code blocks, yield every line
    so that plain bullet lists of commands are processed normally.

    When fences are present the behavior depends on whether any
    fences use a named shell language (``powershell``, ``bash``,
    ``shell``, ``sh``, ``zsh``, ``ps1``):

    - If at least one command-language fence is present, yield only
      lines from those fences. Unlabeled fences and non-shell-language
      fences (``json``, ``text``, etc.) are skipped so that CLI output
      and JSON fragments do not surface as command candidates.
    - If no command-language fence is present (e.g. a single unlabeled
      fence containing only commands), yield lines from unlabeled fences.
      This preserves compatibility with simple ````` ``` ````` blocks.
    """

    lines = body.splitlines()
    if not any(_FENCE_RE.match(line) for line in lines):
        yield from lines
        return

    has_cmd_lang_fence = False
    inside = False
    for line in lines:
        lang_match = _FENCE_LANG_RE.match(line)
        if lang_match is not None:
            if not inside:
                if lang_match.group(1).lower() in _COMMAND_FENCE_LANGS:
                    has_cmd_lang_fence = True
                    break
                inside = True
            else:
                inside = False

    inside = False
    is_cmd = False
    for line in lines:
        lang_match = _FENCE_LANG_RE.match(line)
        if lang_match is not None:
            if not inside:
                lang = lang_match.group(1).lower()
                if has_cmd_lang_fence:
                    is_cmd = lang in _COMMAND_FENCE_LANGS
                else:
                    is_cmd = lang == "" or lang in _COMMAND_FENCE_LANGS
                inside = True
            else:
                inside = False
                is_cmd = False
            continue
        if inside and is_cmd:
            yield line


def _extract_verification_commands(body: str) -> tuple[str, ...]:
    """Extract ordered verification command strings from a body.

    Accepts fenced-command bodies (returning the lines inside
    shell-language fences only), bullet (``-``, ``*``) entries,
    numbered entries, and a ``command:`` label prefix. Output
    fences (unlabeled or non-shell-language) are skipped so that
    CLI output, JSON fragments, and result prose never appear as
    command candidates. Ignores pure result-prose lines such as
    ``passed``, ``OK``, ``result: passed``, and
    ``observed output: ...``. Duplicates are dropped while
    preserving first appearance.
    """

    seen: set[str] = set()
    results: list[str] = []
    for raw in _iter_command_lines(body):
        candidate = _strip_bullet_or_number(raw)
        if not candidate:
            continue
        command_match = _COMMAND_LABEL_RE.match(candidate)
        if command_match is not None:
            candidate = command_match.group("rest").strip()
        if not candidate:
            continue
        lower = candidate.lower()
        if lower in _RESULT_PROSE_EXACT:
            continue
        if any(lower.startswith(prefix) for prefix in _RESULT_PROSE_PREFIXES):
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        results.append(candidate)
    return tuple(results)


def _iter_extraction_lines(body: str):
    """Yield extraction-candidate lines from ``body``.

    When the body contains one or more fenced code blocks, yield
    only the lines inside the fences (with the fence delimiters
    themselves skipped). When no fences are present, yield every
    line. Empty lines pass through; the caller is responsible for
    skipping them.
    """

    lines = body.splitlines()
    has_fence = any(_FENCE_RE.match(line) for line in lines)
    if not has_fence:
        yield from lines
        return
    inside = False
    for line in lines:
        if _FENCE_RE.match(line):
            inside = not inside
            continue
        if inside:
            yield line


@dataclass(frozen=True)
class ReviewPromptRenderResult:
    """Rendered markdown content for a review prompt.

    ``content`` is the full markdown body that a reviewer should
    treat as the prompt. ``valid`` is ``True`` iff ``errors`` is
    empty; when invalid, ``content`` is ``""`` so callers cannot
    accidentally write unusable content to disk.
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


def render_review_prompt(
    template: ReviewPromptTemplate,
    posture_path: str | None = None,
    *,
    review_report_contract: tuple[str, ...] = (),
) -> ReviewPromptRenderResult:
    """Render a deterministic review-prompt markdown body for ``template``.

    Composes :func:`validate_review_prompt_template`. Returns
    ``valid=False``, ``content=""``, and the deterministic error
    tuple when any validation error is present. Otherwise emits
    plain-Markdown content with stable section ordering and
    caller-supplied list ordering preserved. Never writes files,
    never inspects the filesystem, and never raises for
    constructible templates.

    ``posture_path`` (M011-S01) selects the memory operating/posture file cited
    in the ``## llloom Integration Posture`` section. When ``None`` (the public
    default) the historical ``05_governance/llloom_operating_model.md`` is used,
    so direct public-call bytes are unchanged; project composition passes the
    selected non-legacy profile's posture path instead.
    """

    errors = validate_review_prompt_template(template)
    if errors:
        return ReviewPromptRenderResult(
            content="",
            valid=False,
            errors=errors,
        )
    return ReviewPromptRenderResult(
        content=_render_review_markdown(
            template,
            posture_path=posture_path,
            review_report_contract=review_report_contract,
        ),
        valid=True,
        errors=(),
    )


_DEFAULT_LEGACY_POSTURE_PATH = "05_governance/llloom_operating_model.md"


def _render_review_markdown(
    template: ReviewPromptTemplate,
    posture_path: str | None = None,
    *,
    review_report_contract: tuple[str, ...] = (),
) -> str:
    posture = posture_path if posture_path else _DEFAULT_LEGACY_POSTURE_PATH
    sequence_text = format_prompt_sequence(template.sequence) or "???"
    lines: list[str] = []

    lines.append(f"# Review Prompt {sequence_text}: frutlups {template.slice_id} {template.title}")
    lines.append("")

    lines.append("## Role")
    lines.append("")
    lines.append(template.role_instructions.strip())
    lines.append("")
    lines.append(
        "Your role is logical: `reviewer`. Do not assume the package is provider-specific."
    )
    lines.append("")

    lines.append("## Pairing")
    lines.append("")
    lines.append(f"Active roadmap milestone: `{template.milestone_id}`")
    lines.append("")
    lines.append(f"Detailed roadmap slice: `{template.slice_id}: {template.title}`")
    lines.append("")
    lines.append(f"Coding prompt: `{template.coding_prompt_path}`")
    lines.append("")
    lines.append(f"Coder self-report: `{template.self_report_path}`")
    lines.append("")
    lines.append(f"Review output: `{template.review_output_path}`")
    lines.append("")
    if template.prior_review_paths:
        lines.append("Prior review reports:")
        lines.append("")
        for entry in template.prior_review_paths:
            lines.append(f"- `{entry}`")
        lines.append("")

    lines.append("## Required Reading")
    lines.append("")
    for entry in template.required_reading:
        lines.append(f"- `{entry}`")
    lines.append("")

    lines.append("## Expected Changed Files")
    lines.append("")
    for entry in template.expected_changed_files:
        lines.append(f"- `{entry}`")
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

    lines.append("## Review Checks")
    lines.append("")
    lines.append(
        "Verify the slice against its coding prompt and the project framework. At minimum:"
    )
    lines.append("")
    lines.append(
        "1. **Scope** — the changes stay inside the documented expected "
        "changed files and do not silently widen the slice."
    )
    lines.append(
        "2. **Evidence** — the coder self-report covers files changed, "
        "behavior implemented, tests added or updated, verification "
        "commands and results, known limits, memory usage, and the "
        "matching review prompt path."
    )
    lines.append(
        "3. **Regressions** — the full verification baseline passes; "
        "prior milestones and slices behave compatibly."
    )
    lines.append(
        "4. **Non-goals** — none of the slice's documented non-goals "
        "were implemented or partially implemented."
    )
    lines.append("")

    lines.append("## Severity Guidance")
    lines.append("")
    lines.append("Order findings by severity before stating the verdict.")
    lines.append("")
    for entry in template.severity_guidance:
        lines.append(f"- {entry}")
    lines.append("")
    lines.append(
        "Blockers and majors should justify `needs_work`. Minors and nits alone should not."
    )
    lines.append("")

    lines.append("## Verdict Requirements")
    lines.append("")
    lines.append("Choose exactly one verdict:")
    lines.append("")
    for entry in template.verdict_choices:
        lines.append(f"- `{entry}`")
    lines.append("")
    lines.append("State the verdict after the severity-ordered findings.")
    lines.append("")
    for entry in review_report_contract:
        lines.append(f"- {entry}")
    if review_report_contract:
        lines.append("")

    if template.non_goals:
        lines.append("## Non-Goals")
        lines.append("")
        for entry in template.non_goals:
            lines.append(f"- {entry}")
        lines.append("")

    lines.append("## llloom Integration Posture")
    lines.append("")
    lines.append(
        "Confirm that the slice preserves the future memory boundary "
        f"defined in `{posture}`: no "
        "memory reads, no memory writes, no command coupling, and no "
        "assumptions about unstable upstream internals. Normal coding "
        "or review slices must not mutate `llloom` workspaces."
    )
    lines.append("")

    if template.notes:
        lines.append("## Notes")
        lines.append("")
        for entry in template.notes:
            lines.append(f"- {entry}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# M006-S04: deterministic filename, dry-run preview, explicit write surface
# ---------------------------------------------------------------------------

REVIEW_PROMPT_DIR = "prompts/for_review_agent"
"""Repo-relative directory where review prompts are written."""


def review_prompt_filename(template: ReviewPromptTemplate) -> str:
    """Construct the deterministic review-prompt filename for ``template``.

    Returns ``""`` when ``sequence`` cannot be formatted or when ``slug``
    is not a non-empty string. Uses the ``{seq}_review_{slug}.md``
    convention. Never raises and never inspects the filesystem.
    """
    sequence = format_prompt_sequence(template.sequence)
    if not sequence:
        return ""
    slug = template.slug if isinstance(template.slug, str) else ""
    slug = slug.strip()
    if not slug:
        return ""
    return f"{sequence}_review_{slug}.md"


@dataclass(frozen=True)
class ReviewPromptPreview:
    """Dry-run preview of where a review prompt would be written.

    Never touches the filesystem, never writes, and ``wrote`` is always
    ``False``. ``would_write`` is ``True`` only when the template
    validates; an invalid template would not produce a write.
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


def preview_review_prompt(
    template: ReviewPromptTemplate,
    prompt_dir: str = REVIEW_PROMPT_DIR,
) -> ReviewPromptPreview:
    """Return a dry-run preview for the review prompt described by ``template``.

    Composes :func:`validate_review_prompt_template` and
    :func:`review_prompt_filename`. ``prompt_dir`` is the repo-relative directory
    the prompt would be written to (defaults to the legacy
    ``prompts/for_review_agent``); layout profiles pass their configured review
    prompt directory so the preview target reflects where the write will land.
    Never inspects the filesystem, never writes, and never raises for
    constructible templates.
    """
    errors = validate_review_prompt_template(template)
    valid = not errors
    filename = review_prompt_filename(template)
    sequence_value: int | None
    if isinstance(template.sequence, bool) or not isinstance(template.sequence, int):
        sequence_value = None
    else:
        sequence_value = template.sequence
    sequence_formatted = format_prompt_sequence(template.sequence)
    directory = prompt_dir if isinstance(prompt_dir, str) and prompt_dir else REVIEW_PROMPT_DIR
    target_path = f"{directory}/{filename}" if filename else ""
    return ReviewPromptPreview(
        kind="review",
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
class ReviewPromptWriteCommand:
    """Explicit request to write a review prompt file.

    The command is the only sanctioned entry point for package code to
    write a review prompt. Content is rendered internally via
    :func:`render_review_prompt` so callers cannot supply stale content.
    Construction never touches the filesystem; only
    :func:`write_review_prompt` does.
    """

    project_root: Path
    template: ReviewPromptTemplate
    overwrite: bool = False
    prompt_dir: str = REVIEW_PROMPT_DIR


@dataclass(frozen=True)
class ReviewPromptWriteResult:
    """Result of an explicit review-prompt write attempt.

    ``wrote`` is ``True`` only after a file has actually been written.
    ``overwrote`` is ``True`` only when an existing file at the target
    path was replaced (``overwrite=True`` had to be set on the command).
    On failure, ``target_path`` is the string form of the resolved
    target where possible, or ``""`` when failure happened before a
    path could be safely computed.
    """

    preview: ReviewPromptPreview
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


def _write_review_prompt_content(
    *,
    project_root: Path,
    template: ReviewPromptTemplate,
    content: str,
    overwrite: bool,
    prompt_dir: str,
) -> ReviewPromptWriteResult:
    """The one private review-write core (M003-S03 correction).

    Factors path confinement, preview validation, overwrite behavior,
    directory creation, and the file write around already-rendered content.
    The public :func:`write_review_prompt` compatibility wrapper renders
    through the hard-coded renderer and then calls this same core; configured
    plan writers call it with the validated plan render bytes. Never exported.
    """

    prompt_dir = prompt_dir if isinstance(prompt_dir, str) else ""
    if not prompt_dir:
        prompt_dir = REVIEW_PROMPT_DIR

    preview = preview_review_prompt(template, prompt_dir=prompt_dir)
    errors: list[str] = list(preview.errors)

    if not is_safe_relative(prompt_dir):
        errors.append("prompt_dir must be a safe repo-relative path inside the template root")
    if not isinstance(content, str) or not content:
        errors.append("rendered review content must be a non-empty string")

    if errors:
        return ReviewPromptWriteResult(
            preview=preview,
            target_path="",
            wrote=False,
            errors=tuple(errors),
            overwrote=False,
        )

    project_root = Path(project_root)
    review_dir = project_root / PurePosixPath(prompt_dir)
    target = review_dir / preview.filename

    try:
        resolved_root = project_root.resolve(strict=False)
        resolved_target_parent = target.parent.resolve(strict=False)
        resolved_review_dir = review_dir.resolve(strict=False)
    except OSError as exc:
        return ReviewPromptWriteResult(
            preview=preview,
            target_path="",
            wrote=False,
            errors=(f"target path could not be resolved: {exc}",),
            overwrote=False,
        )

    if resolved_target_parent != resolved_review_dir:
        return ReviewPromptWriteResult(
            preview=preview,
            target_path="",
            wrote=False,
            errors=(f"target path must resolve inside {prompt_dir}/",),
            overwrote=False,
        )
    if not _is_within(resolved_review_dir, resolved_root):
        return ReviewPromptWriteResult(
            preview=preview,
            target_path="",
            wrote=False,
            errors=("target directory must resolve inside the project/template root",),
            overwrote=False,
        )

    target_existed = target.exists()
    if target_existed and not overwrite:
        return ReviewPromptWriteResult(
            preview=preview,
            target_path=str(target),
            wrote=False,
            errors=(f"{preview.filename} already exists; pass overwrite=True to replace it",),
            overwrote=False,
        )

    review_dir.mkdir(parents=True, exist_ok=True)
    # Exact bytes: the validated render is persisted verbatim (LF), never
    # platform-translated.
    target.write_bytes(content.encode("utf-8"))

    return ReviewPromptWriteResult(
        preview=preview,
        target_path=str(target),
        wrote=True,
        errors=(),
        overwrote=target_existed,
    )


def write_review_prompt(
    command: ReviewPromptWriteCommand,
) -> ReviewPromptWriteResult:
    """Write a review prompt file when ``command`` is valid.

    Composes :func:`preview_review_prompt` and
    :func:`render_review_prompt`, refusing to write when template
    validation or rendering fails. Refuses to write outside the configured
    review-prompt directory (``command.prompt_dir``, defaulting to the legacy
    ``prompts/for_review_agent``), which must be a safe repo-relative path that
    does not escape ``project_root``. Creates the directory only on a successful
    write. Returns deterministic errors for all failure paths. This is the only
    function in this module that touches the filesystem, and it performs every
    write through the same private core configured plan writers use.

    Validation aggregation and order are the accepted historical contract:
    typed-template (preview) errors first, then the unsafe-directory error;
    the legacy render is attempted only when that set is empty.
    """

    prompt_dir = command.prompt_dir if isinstance(command.prompt_dir, str) else ""
    if not prompt_dir:
        prompt_dir = REVIEW_PROMPT_DIR

    preview = preview_review_prompt(command.template, prompt_dir=prompt_dir)
    errors: list[str] = list(preview.errors)

    if not is_safe_relative(prompt_dir):
        errors.append("prompt_dir must be a safe repo-relative path inside the template root")

    if errors:
        return ReviewPromptWriteResult(
            preview=preview,
            target_path="",
            wrote=False,
            errors=tuple(errors),
            overwrote=False,
        )

    render_result = render_review_prompt(command.template)
    if not render_result.valid or not render_result.content:
        render_errors = (
            list(render_result.errors)
            if render_result.errors
            else ["render_review_prompt returned invalid or empty content"]
        )
        return ReviewPromptWriteResult(
            preview=preview,
            target_path="",
            wrote=False,
            errors=tuple(render_errors),
            overwrote=False,
        )

    return _write_review_prompt_content(
        project_root=command.project_root,
        template=command.template,
        content=render_result.content,
        overwrite=command.overwrite,
        prompt_dir=command.prompt_dir,
    )
