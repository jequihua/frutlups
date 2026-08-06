"""Typed schema, locator, content-validation, and findings surfaces for
coder self-report artifacts.

This module provides:

- the canonical typed self-report schema contract (M005-S01)
- a typed locator that derives the expected self-report path from a
  validated ``CodingPromptTemplate`` and a supplied project root,
  without parsing report content (M005-S02)
- a typed content validator that reads exactly one explicitly located
  self-report markdown file, parses ATX-heading sections, and checks
  that schema-required fields are present and non-empty (M005-S03)
- a typed aggregate-findings helper that iterates over an explicit
  set of ``CodingPromptTemplate`` instances and produces actionable
  per-template findings using the M005-S03 validator (M005-S04)

The findings helper does not scan ``05_governance/reviews/`` and
never writes any file. It evaluates only the templates the caller
supplied. Later slices add review-prompt generation (M006), verdict
handling (M007), and the local loop runner (M008).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from frutlups.layout import LayoutProfile
from frutlups.prompt_template import (
    CodingPromptTemplate,
    format_prompt_sequence,
    validate_coding_prompt_template,
)

SELF_REPORT_REQUIRED_FIELDS: tuple[str, ...] = (
    "files changed",
    "behavior implemented",
    "tests added or updated",
    "verification commands and results",
    "live status summary",
    "known limits and intentional deferrals",
    "memory usage statement",
    "matching review prompt path created by the coder",
    "blockers or open questions",
)
"""Baseline required fields for every coder self-report.

These are the minimum reportable items every self-report must include.
Future M005-S02/S03/S04 ingestion will assume the presence of these
fields without further negotiation.
"""


SELF_REPORT_OPTIONAL_FIELDS: tuple[str, ...] = (
    "focused probe result",
    "memory updates requested",
    "out-of-scope changes",
    "follow-up suggestions",
)
"""Conventional optional fields that frequently appear in self-reports.

These are not required by the default schema; they are documented so
later ingestion can treat them as known-good optional sections rather
than unknown noise.
"""


SELF_REPORT_SCHEMA_KIND = "coder_self_report"
"""Stable kind discriminator for the self-report schema type."""


SELF_REPORT_SCHEMA_VERSION = "self_report_schema_v1"
"""Stable schema version string for future evolution.

Bumping this string is a deliberate governance decision and should be
accompanied by an explicit migration of consuming code.
"""


@dataclass(frozen=True)
class SelfReportSchema:
    """Typed contract describing the fields a self-report must include.

    ``required_fields`` and ``optional_fields`` both preserve insertion
    order; that order is load-bearing because later prompt rendering and
    review-prompt generation will read the lists deterministically.
    ``kind`` and ``version`` are simple discriminators so a future
    consumer can detect whether it is looking at this schema and at the
    expected version without parsing the field list.
    """

    required_fields: tuple[str, ...]
    optional_fields: tuple[str, ...] = field(default=())
    kind: str = SELF_REPORT_SCHEMA_KIND
    version: str = SELF_REPORT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "version": self.version,
            "required_fields": list(self.required_fields),
            "optional_fields": list(self.optional_fields),
        }


def default_self_report_schema() -> SelfReportSchema:
    """Return the canonical default schema for coder self-reports."""

    return SelfReportSchema(
        required_fields=SELF_REPORT_REQUIRED_FIELDS,
        optional_fields=SELF_REPORT_OPTIONAL_FIELDS,
    )


def self_report_schema_from_headings(headings: tuple[str, ...]) -> SelfReportSchema:
    """Build a self-report schema from configured required headings.

    The headings are used as ``required_fields`` verbatim (their display casing is
    preserved so validation diagnostics name the configured heading exactly).
    Matching against report sections normalizes case/punctuation internally.
    """

    return SelfReportSchema(required_fields=tuple(headings))


def self_report_schema_for_profile(profile: LayoutProfile | None) -> SelfReportSchema:
    """Return the effective self-report schema for a resolved layout profile.

    When the profile declares ``self_report_required_headings``, that configured
    contract is the source of truth; otherwise the legacy/default baseline schema
    is used. This is the single helper all call sites (loop resume / status,
    make-review-prompt, reviewer handoff evidence) use so they agree on one schema.
    """

    if profile is not None and profile.self_report_required_headings:
        return self_report_schema_from_headings(profile.self_report_required_headings)
    return default_self_report_schema()


def validate_self_report_schema(
    schema: SelfReportSchema,
) -> tuple[str, ...]:
    """Return a tuple of validation error messages (empty when valid).

    Validation is pure and deterministic: it does not touch the
    filesystem, does not inspect live repository state, does not query
    or mutate any memory backend, and never raises for malformed
    collection fields (for example, ``required_fields=42`` or
    ``optional_fields=None``).

    Rules enforced:

    - ``required_fields`` and ``optional_fields`` must each be a
      ``tuple`` or ``list``
    - every entry in each collection must be a non-empty string
    - no collection may contain duplicate field names (compared
      case-sensitively to match the documented baseline strings)
    - ``required_fields`` must be non-empty
    - ``kind`` and ``version`` must be non-empty strings

    The hardcoded :data:`SELF_REPORT_REQUIRED_FIELDS` baseline is the *default*
    schema, not a universal floor: a layout profile may configure its own
    self-report headings, so a valid custom schema does NOT have to be a superset
    of the baseline.
    """

    errors: list[str] = []
    errors.extend(_validate_field_collection("required_fields", schema.required_fields))
    errors.extend(_validate_field_collection("optional_fields", schema.optional_fields))
    if isinstance(schema.required_fields, (tuple, list)) and not schema.required_fields:
        errors.append("required_fields must not be empty")
    for attr in ("kind", "version"):
        value = getattr(schema, attr)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{attr} must be a non-empty string")
    return tuple(errors)


def _validate_field_collection(
    field_name: str,
    value: object,
) -> list[str]:
    """Return deterministic errors for a self-report field collection.

    Mirrors the M004-S01 ``_validate_string_collection`` shape: rejects
    non-tuple / non-list inputs (catching ``None`` and bare ``int``
    without raising), then enforces non-empty string entries, then
    rejects duplicate entries.
    """

    errors: list[str] = []
    if not isinstance(value, (tuple, list)):
        errors.append(f"{field_name} must be a tuple or list of non-empty strings")
        return errors
    seen: set[str] = set()
    for index, entry in enumerate(value):
        if not isinstance(entry, str) or not entry.strip():
            errors.append(f"{field_name}[{index}] must be a non-empty string")
            continue
        if entry in seen:
            errors.append(f"{field_name} contains duplicate field: {entry}")
        else:
            seen.add(entry)
    return errors


@dataclass(frozen=True)
class SelfReportLocationCommand:
    """Request to locate the expected self-report for a coding prompt.

    Construction never touches the filesystem; only
    ``locate_expected_self_report`` does. ``project_root`` is the
    repository root the rendered ``template.self_report_path`` should
    resolve underneath.
    """

    project_root: Path
    template: CodingPromptTemplate


@dataclass(frozen=True)
class SelfReportLocationResult:
    """Result of locating the expected self-report path.

    ``expected_path`` is the resolved absolute path (string-formatted)
    where the self-report is expected to live. ``repo_relative_path``
    is the original ``template.self_report_path`` value (kept verbatim
    so callers see the author-supplied form). ``exists`` /
    ``is_file`` / ``is_dir`` describe the current filesystem state of
    that target without reading its content. ``errors`` is a tuple of
    deterministic human-readable validation or path-safety errors;
    when non-empty, ``expected_path`` is ``""`` and every state flag
    is ``False`` (no useful target could be computed).
    """

    expected_path: str
    repo_relative_path: str
    exists: bool
    is_file: bool
    is_dir: bool
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "expected_path": self.expected_path,
            "repo_relative_path": self.repo_relative_path,
            "exists": self.exists,
            "is_file": self.is_file,
            "is_dir": self.is_dir,
            "errors": list(self.errors),
        }


def locate_expected_self_report(
    command: SelfReportLocationCommand,
) -> SelfReportLocationResult:
    """Locate the expected self-report path for ``command.template``.

    Composes :func:`validate_coding_prompt_template` so malformed
    template fields surface the documented M004 errors. Treats
    ``template.self_report_path`` as a repo-relative path; rejects
    absolute paths and any path whose resolved form escapes
    ``command.project_root``. Never reads self-report content, never
    writes, never creates directories, and never raises for
    documented malformed inputs.
    """

    errors: list[str] = list(validate_coding_prompt_template(command.template))
    raw_path = command.template.self_report_path

    if errors:
        return SelfReportLocationResult(
            expected_path="",
            repo_relative_path=raw_path if isinstance(raw_path, str) else "",
            exists=False,
            is_file=False,
            is_dir=False,
            errors=tuple(errors),
        )

    candidate = Path(raw_path)
    if candidate.is_absolute():
        errors.append("self_report_path must be repo-relative")
        return SelfReportLocationResult(
            expected_path="",
            repo_relative_path=raw_path,
            exists=False,
            is_file=False,
            is_dir=False,
            errors=tuple(errors),
        )

    project_root = Path(command.project_root)
    target = project_root / candidate
    try:
        resolved_target = target.resolve(strict=False)
        resolved_root = project_root.resolve(strict=False)
    except OSError as exc:
        return SelfReportLocationResult(
            expected_path="",
            repo_relative_path=raw_path,
            exists=False,
            is_file=False,
            is_dir=False,
            errors=(f"self_report_path could not be resolved: {exc}",),
        )

    if not _is_relative_to(resolved_target, resolved_root):
        errors.append("self_report_path must resolve inside project root")
        return SelfReportLocationResult(
            expected_path="",
            repo_relative_path=raw_path,
            exists=False,
            is_file=False,
            is_dir=False,
            errors=tuple(errors),
        )

    return SelfReportLocationResult(
        expected_path=str(resolved_target),
        repo_relative_path=raw_path,
        exists=resolved_target.exists(),
        is_file=resolved_target.is_file(),
        is_dir=resolved_target.is_dir(),
        errors=(),
    )


def _is_relative_to(child: Path, parent: Path) -> bool:
    """Return ``True`` when ``child`` is at or under ``parent``.

    Uses ``Path.is_relative_to`` when available (Python 3.9+); falls
    back to a try/except form to remain robust on hostile inputs
    (this fallback is not exercised on supported Python versions but
    keeps the function defensive against future path-typing changes).
    """

    try:
        return child.is_relative_to(parent)
    except (AttributeError, ValueError):
        try:
            child.relative_to(parent)
            return True
        except ValueError:
            return False


@dataclass(frozen=True)
class SelfReportSection:
    """A single ATX-heading section parsed from a self-report markdown."""

    heading: str
    body: str

    def to_dict(self) -> dict[str, str]:
        return {"heading": self.heading, "body": self.body}


@dataclass(frozen=True)
class ParsedSelfReport:
    """Result of parsing one self-report markdown file into sections."""

    path: str
    sections: tuple[SelfReportSection, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "sections": [section.to_dict() for section in self.sections],
        }


@dataclass(frozen=True)
class SelfReportValidationCommand:
    """Request to validate the content of one explicitly located self-report.

    ``location`` reuses :class:`SelfReportLocationCommand` so this
    surface stays composable with M005-S02. ``schema`` defaults to
    :func:`default_self_report_schema` so the canonical contract
    applies unless a caller supplies a custom schema.
    """

    location: SelfReportLocationCommand
    schema: SelfReportSchema = field(default_factory=default_self_report_schema)


@dataclass(frozen=True)
class SelfReportValidationResult:
    """Outcome of validating one self-report against a schema.

    ``location`` carries the M005-S02 locator result so callers can
    inspect path resolution without re-running it. ``parsed`` is
    populated when the file could be read; even in failure cases (for
    example, missing required fields) the parsed section list is
    included so callers can inspect what was actually present.
    ``valid`` is ``True`` iff ``errors`` is empty.
    """

    location: SelfReportLocationResult
    parsed: ParsedSelfReport | None
    valid: bool
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "location": self.location.to_dict(),
            "parsed": self.parsed.to_dict() if self.parsed is not None else None,
            "valid": self.valid,
            "errors": list(self.errors),
        }


_FIELD_EXACT_ALIASES: dict[str, frozenset[str]] = {
    "files changed": frozenset({"files changed"}),
    "tests added or updated": frozenset({"tests added or updated"}),
    "verification commands and results": frozenset(
        {
            "verification commands and results",
            "verification commands",
            "verification",
            "verification run",
        }
    ),
    "verification run": frozenset(
        {
            "verification run",
            "verification commands and results",
            "verification commands",
            "verification",
        }
    ),
    "live status summary": frozenset(
        {
            "live status summary",
        }
    ),
    "known limits and intentional deferrals": frozenset(
        {
            "known limits and intentional deferrals",
            "known limits",
        }
    ),
    "memory usage statement": frozenset(
        {
            "memory usage statement",
            "memory usage",
        }
    ),
    "matching review prompt path created by the coder": frozenset(
        {
            "matching review prompt path created by the coder",
            "matching review prompt",
            "matching review prompt created",
        }
    ),
    "blockers or open questions": frozenset(
        {
            "blockers or open questions",
            "open questions or blockers",
            "open questions",
            "blockers",
        }
    ),
}


def validate_expected_self_report(
    command: SelfReportValidationCommand,
) -> SelfReportValidationResult:
    """Validate one explicitly located self-report against a schema.

    Composes :func:`validate_self_report_schema` and
    :func:`locate_expected_self_report`, then reads exactly the
    located file (when it exists and is a file), parses its headings
    into sections (ATX ``## Heading`` and plain ``Heading:`` template
    headings for the schema's configured/aliased labels), and checks
    that every schema-required field is present and has non-empty body
    content. Missing-field diagnostics name the configured heading
    exactly. Returns deterministic errors instead of raising. Never
    scans the reviews directory and never writes any file.
    """

    schema_errors = list(validate_self_report_schema(command.schema))
    location = locate_expected_self_report(command.location)

    errors: list[str] = []
    errors.extend(schema_errors)
    errors.extend(location.errors)

    if errors:
        return SelfReportValidationResult(
            location=location,
            parsed=None,
            valid=False,
            errors=tuple(errors),
        )

    if not location.exists:
        return SelfReportValidationResult(
            location=location,
            parsed=None,
            valid=False,
            errors=("self-report file is missing",),
        )
    if location.is_dir:
        return SelfReportValidationResult(
            location=location,
            parsed=None,
            valid=False,
            errors=("self-report path is a directory",),
        )

    target = Path(location.expected_path)
    try:
        content = target.read_text(encoding="utf-8")
    except OSError as exc:
        return SelfReportValidationResult(
            location=location,
            parsed=None,
            valid=False,
            errors=(f"self-report file could not be read: {exc}",),
        )

    sections = _parse_sections(content, _recognized_section_labels(command.schema))
    parsed = ParsedSelfReport(path=location.expected_path, sections=sections)

    content_errors: list[str] = []
    if isinstance(command.schema.required_fields, (tuple, list)):
        normalized_to_body: dict[str, str] = {
            _normalize_heading(section.heading): section.body for section in sections
        }
        for required in command.schema.required_fields:
            if not isinstance(required, str) or not required.strip():
                continue
            body = _find_field_body(required, normalized_to_body)
            if body is None:
                content_errors.append(f"self-report missing required field: {required}")
            elif not body.strip():
                content_errors.append(f"self-report required field is empty: {required}")

    return SelfReportValidationResult(
        location=location,
        parsed=parsed,
        valid=not content_errors,
        errors=tuple(content_errors),
    )


_HEADING_RE_PREFIX = "#"
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
# A plain "Heading:" line: a label with no embedded colon/backtick, then a colon.
# The label must normalize to a recognized schema/alias label (checked by the
# caller) before it is treated as a heading, so prose, URLs, Windows paths,
# ``command:`` / ``path:`` lines, and bullet values are not split.
_PLAIN_HEADING_RE = re.compile(r"^(?P<label>[A-Za-z][^:`]*?)\s*:(?P<rest>.*)$")


def _plain_heading_label(line: str, recognized_labels: frozenset[str]) -> tuple[str, str] | None:
    """Return ``(label, inline_rest)`` when ``line`` is a recognized plain heading.

    Conservative: the line must not be a bullet/numbered item, must match the
    ``Label:`` shape, and the label must normalize to one of ``recognized_labels``.
    Returns ``None`` otherwise.
    """

    if not recognized_labels:
        return None
    stripped = line.strip()
    if not stripped or stripped[0] in "-*#" or stripped[0].isdigit():
        return None
    match = _PLAIN_HEADING_RE.match(stripped)
    if match is None:
        return None
    label = match.group("label").strip()
    if not label or _normalize_heading(label) not in recognized_labels:
        return None
    return label, match.group("rest").strip()


def _parse_sections(
    content: str,
    recognized_labels: frozenset[str] = frozenset(),
) -> tuple[SelfReportSection, ...]:
    """Parse a self-report into deterministic heading sections.

    Recognizes two heading shapes:

    - ATX headings (``## Files Changed``); and
    - plain template headings (``Files Changed:``) whose normalized label is one
      of ``recognized_labels`` (the schema-required/optional labels and known
      aliases). Plain-heading detection is conservative and is suppressed inside
      fenced code blocks, so prose, URLs, Windows paths, command output, and
      bullet values are not split merely because a line contains a colon.

    Section body is the text up to the next heading, with blank edges stripped.
    Text before the first heading is discarded.
    """

    sections: list[SelfReportSection] = []
    current_heading: str | None = None
    current_body: list[str] = []
    in_fence = False

    def _flush() -> None:
        if current_heading is not None:
            sections.append(
                SelfReportSection(
                    heading=current_heading,
                    body=_strip_blank_edges(current_body),
                )
            )

    for line in content.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            if current_heading is not None:
                current_body.append(line)
            continue
        if not in_fence and _is_atx_heading(line):
            _flush()
            current_heading = _strip_heading_marker(line)
            current_body = []
            continue
        if not in_fence:
            plain = _plain_heading_label(line, recognized_labels)
            if plain is not None:
                label, inline_rest = plain
                _flush()
                current_heading = label
                current_body = [inline_rest] if inline_rest.strip() else []
                continue
        if current_heading is None:
            continue
        current_body.append(line)
    _flush()
    return tuple(sections)


def _recognized_section_labels(schema: SelfReportSchema) -> frozenset[str]:
    """Normalized labels (schema fields + known aliases) that may be plain headings."""

    recognized: set[str] = set()
    for collection in (schema.required_fields, schema.optional_fields):
        if isinstance(collection, (tuple, list)):
            for entry in collection:
                if isinstance(entry, str) and entry.strip():
                    recognized.add(_normalize_heading(entry))
    for key, values in _FIELD_EXACT_ALIASES.items():
        recognized.add(key)
        recognized.update(values)
    return frozenset(recognized)


def _is_atx_heading(line: str) -> bool:
    stripped = line.lstrip()
    if not stripped.startswith(_HEADING_RE_PREFIX):
        return False
    # Count leading hashes, then require a space after them.
    hash_count = 0
    for char in stripped:
        if char == "#":
            hash_count += 1
        else:
            break
    if hash_count == 0 or hash_count > 6:
        return False
    rest = stripped[hash_count:]
    if not rest.startswith(" "):
        return False
    return True


def _strip_heading_marker(line: str) -> str:
    stripped = line.lstrip()
    return stripped.lstrip("#").strip()


def _strip_blank_edges(body_lines: list[str]) -> str:
    start = 0
    end = len(body_lines)
    while start < end and not body_lines[start].strip():
        start += 1
    while end > start and not body_lines[end - 1].strip():
        end -= 1
    return "\n".join(body_lines[start:end])


def _normalize_heading(heading: str) -> str:
    text = heading.strip().lower()
    text = text.rstrip(":!?.;,")
    return " ".join(text.split())


def _find_field_body(
    schema_field: str,
    normalized_to_body: dict[str, str],
) -> str | None:
    """Return the body of the first section matching ``schema_field``.

    Uses small explicit aliases plus a special-case "ends with"
    rule for ``behavior implemented`` so slice-specific prefixes
    such as ``Locator Behavior Implemented`` count as matches.
    Returns ``None`` when no section matches.
    """

    key = _normalize_heading(schema_field)
    if key == "behavior implemented":
        for normalized, body in normalized_to_body.items():
            if normalized == "behavior implemented" or normalized.endswith(" behavior implemented"):
                return body
        return None
    aliases = _FIELD_EXACT_ALIASES.get(key, frozenset({key}))
    for normalized, body in normalized_to_body.items():
        if normalized in aliases:
            return body
    return None


def find_self_report_section(
    parsed: ParsedSelfReport,
    schema_field: str,
) -> SelfReportSection | None:
    """Return the first section in ``parsed`` matching ``schema_field``.

    Uses the same alias rules and normalisation as
    :func:`validate_expected_self_report`. Returns ``None`` when no
    section matches. Composable surface for callers (such as
    M006-S02 review-prompt evidence derivation) that want to look
    up structured sections without reusing the private validator
    helpers.

    Defensively never raises for constructible-but-malformed
    parsed shapes: non-``ParsedSelfReport`` inputs, non-iterable
    ``parsed.sections``, non-``SelfReportSection`` entries inside
    ``sections``, and sections with non-string ``heading`` values
    all return ``None`` or are silently skipped instead of
    raising.
    """

    if not isinstance(parsed, ParsedSelfReport):
        return None
    try:
        sections_iter = iter(parsed.sections)
    except TypeError:
        return None
    key = _normalize_heading(schema_field) if isinstance(schema_field, str) else ""
    if key == "behavior implemented":
        for section in sections_iter:
            if not isinstance(section, SelfReportSection):
                continue
            if not isinstance(section.heading, str):
                continue
            normalized = _normalize_heading(section.heading)
            if normalized == "behavior implemented" or normalized.endswith(" behavior implemented"):
                return section
        return None
    aliases = _FIELD_EXACT_ALIASES.get(key, frozenset({key}))
    for section in sections_iter:
        if not isinstance(section, SelfReportSection):
            continue
        if not isinstance(section.heading, str):
            continue
        normalized = _normalize_heading(section.heading)
        if normalized in aliases:
            return section
    return None


class SelfReportFindingSeverity(StrEnum):
    """Severity assigned to one aggregate self-report finding."""

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class SelfReportFinding:
    """A single actionable problem detected during aggregate validation.

    The finding preserves enough context for a future review-prompt
    generator or CLI to tell a local user exactly what needs fixing:
    the stable ``code``, the prompt sequence and milestone / slice
    IDs (best-effort even on malformed templates), the expected
    self-report path (when one could be derived), a human-readable
    ``message``, and the underlying validator errors verbatim.
    """

    code: str
    severity: SelfReportFindingSeverity
    sequence: int | None
    milestone_id: str
    slice_id: str
    self_report_path: str
    message: str
    errors: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "sequence": self.sequence,
            "milestone_id": self.milestone_id,
            "slice_id": self.slice_id,
            "self_report_path": self.self_report_path,
            "message": self.message,
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class SelfReportFindingsCommand:
    """Request to evaluate self-reports for an explicit set of templates.

    The helper iterates only over ``templates`` in the order
    supplied; it does not infer templates by reading prompt
    markdown files and does not scan the reviews directory.
    """

    project_root: Path
    templates: tuple[CodingPromptTemplate, ...]
    schema: SelfReportSchema = field(default_factory=default_self_report_schema)


@dataclass(frozen=True)
class SelfReportFindingsResult:
    """Aggregate result of self-report findings across templates.

    ``ok`` is ``True`` iff ``findings`` is empty. ``checked`` is the
    number of templates the helper actually iterated over (it equals
    ``len(command.templates)``; it is not the number of valid
    reports).
    """

    ok: bool
    checked: int
    findings: tuple[SelfReportFinding, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "checked": self.checked,
            "findings": [finding.to_dict() for finding in self.findings],
        }


def collect_self_report_findings(
    command: SelfReportFindingsCommand,
) -> SelfReportFindingsResult:
    """Produce deterministic findings for an explicit set of templates.

    Composes :func:`validate_expected_self_report` for each supplied
    template. A schema-level failure short-circuits with a single
    `invalid_self_report_schema` finding (no per-template work).
    Otherwise, each template's validation outcome is classified into
    exactly one finding code; valid reports produce no finding.
    Template order is preserved. Never raises for any constructible
    command, never scans the reviews directory, never writes files,
    and never reads any file other than the per-template explicit
    target.
    """

    checked = len(command.templates)
    schema_errors = validate_self_report_schema(command.schema)
    if schema_errors:
        finding = SelfReportFinding(
            code="invalid_self_report_schema",
            severity=SelfReportFindingSeverity.ERROR,
            sequence=None,
            milestone_id="",
            slice_id="",
            self_report_path="",
            message=("self-report schema is invalid; cannot validate per-template reports"),
            errors=tuple(schema_errors),
        )
        return SelfReportFindingsResult(
            ok=False,
            checked=checked,
            findings=(finding,),
        )

    findings: list[SelfReportFinding] = []
    for template in command.templates:
        validation = validate_expected_self_report(
            SelfReportValidationCommand(
                location=SelfReportLocationCommand(
                    project_root=command.project_root,
                    template=template,
                ),
                schema=command.schema,
            )
        )
        if validation.valid:
            continue
        findings.append(_classify_validation_failure(template, validation))

    return SelfReportFindingsResult(
        ok=not findings,
        checked=checked,
        findings=tuple(findings),
    )


def _classify_validation_failure(
    template: CodingPromptTemplate,
    validation: SelfReportValidationResult,
) -> SelfReportFinding:
    sequence = _safe_sequence(template)
    sequence_text = format_prompt_sequence(template.sequence) or "???"
    milestone_id = _safe_str(template.milestone_id)
    slice_id = _safe_str(template.slice_id)
    expected_path = validation.location.expected_path or _safe_str(template.self_report_path)
    location_context = f"coding prompt {sequence_text}{f' ({slice_id})' if slice_id else ''}"

    if validation.location.errors:
        if expected_path:
            message = (
                f"{location_context} has an invalid template or path "
                f"({expected_path}); cannot locate a self-report target"
            )
        else:
            message = (
                f"{location_context} has an invalid template or path; "
                "cannot locate a self-report target"
            )
        return SelfReportFinding(
            code="invalid_self_report_template",
            severity=SelfReportFindingSeverity.ERROR,
            sequence=sequence,
            milestone_id=milestone_id,
            slice_id=slice_id,
            self_report_path=expected_path,
            message=message,
            errors=tuple(validation.errors),
        )

    errors = tuple(validation.errors)
    if "self-report file is missing" in errors:
        message = (
            f"expected self-report for {location_context} is missing at {expected_path}"
            if expected_path
            else f"expected self-report for {location_context} is missing"
        )
        return SelfReportFinding(
            code="missing_self_report",
            severity=SelfReportFindingSeverity.ERROR,
            sequence=sequence,
            milestone_id=milestone_id,
            slice_id=slice_id,
            self_report_path=expected_path,
            message=message,
            errors=errors,
        )

    if "self-report path is a directory" in errors:
        message = (
            f"expected self-report path for {location_context} is a directory at {expected_path}"
        )
        return SelfReportFinding(
            code="self_report_path_is_directory",
            severity=SelfReportFindingSeverity.ERROR,
            sequence=sequence,
            milestone_id=milestone_id,
            slice_id=slice_id,
            self_report_path=expected_path,
            message=message,
            errors=errors,
        )

    if any(err.startswith("self-report file could not be read") for err in errors):
        message = f"self-report for {location_context} at {expected_path} could not be read"
        return SelfReportFinding(
            code="unreadable_self_report",
            severity=SelfReportFindingSeverity.ERROR,
            sequence=sequence,
            milestone_id=milestone_id,
            slice_id=slice_id,
            self_report_path=expected_path,
            message=message,
            errors=errors,
        )

    # Default: content-level errors from M005-S03 (missing or empty
    # required fields).
    message = f"self-report for {location_context} at {expected_path} is incomplete"
    return SelfReportFinding(
        code="incomplete_self_report",
        severity=SelfReportFindingSeverity.ERROR,
        sequence=sequence,
        milestone_id=milestone_id,
        slice_id=slice_id,
        self_report_path=expected_path,
        message=message,
        errors=errors,
    )


def _safe_sequence(template: CodingPromptTemplate) -> int | None:
    value = template.sequence
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _safe_str(value: object) -> str:
    if isinstance(value, str):
        return value
    return ""
