"""Typed verdict enum, review report schema, and verdict parsing for M007.

M007-S01 foundations:

- ``ReviewVerdict`` — the four canonical review verdict values as a
  ``StrEnum`` so values compare equal to their plain string equivalents
- ``REVIEW_REPORT_REQUIRED_FIELDS`` — baseline section names every review
  report must include
- ``REVIEW_REPORT_OPTIONAL_FIELDS`` — conventional optional section names
- ``REVIEW_REPORT_SCHEMA_KIND`` — stable kind discriminator
- ``REVIEW_REPORT_SCHEMA_VERSION`` — stable version string
- ``ReviewReportSchema`` — frozen dataclass describing the review report
  contract (required fields, optional fields, allowed verdicts)
- ``default_review_report_schema()`` — returns the canonical M007-S01 schema
- ``validate_review_report_schema(schema)`` — pure, deterministic validator;
  never raises for constructible malformed inputs

M007-S02 verdict parsing:

- ``ReviewReportVerdictParseResult`` — frozen result dataclass
- ``ReviewReportVerdictParseCommand`` — frozen file-reading command dataclass
- ``parse_review_report_verdict_text(content, schema)`` — pure text parser
- ``parse_review_report_verdict(command)`` — file-reading command

Verdict parsing is M007-S02.
Next-action computation is M007-S03.
Explicit human override handling is M007-S04.
Recording verdicts in governance artifacts is later local-loop work.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class ReviewVerdict(StrEnum):
    """The four canonical review verdict values.

    ``StrEnum`` makes ``.value`` a plain string and allows direct string
    comparison without casting. The canonical ordering is ``pass``,
    ``needs_work``, ``blocked``, ``override``; this order is preserved in
    the default schema's ``allowed_verdicts`` tuple.
    """

    PASS = "pass"
    NEEDS_WORK = "needs_work"
    BLOCKED = "blocked"
    OVERRIDE = "override"


REVIEW_REPORT_REQUIRED_FIELDS: tuple[str, ...] = (
    "verdict",
    "findings",
    "review notes",
    "verification",
    "residual risk",
    "memory",
)
"""Baseline section names every review report must include.

These are the minimum reportable items. Future M007-S02 parsing will
assume the presence of these sections without further negotiation.
"""


REVIEW_REPORT_OPTIONAL_FIELDS: tuple[str, ...] = (
    "open questions",
    "follow-up suggestions",
    "inline comments",
)
"""Conventional optional section names for review reports.

Not required by the default schema; documented so later ingestion can
treat them as known-good optional sections rather than unknown noise.
"""


REVIEW_REPORT_SCHEMA_KIND = "review_report"
"""Stable kind discriminator for the review report schema type."""


REVIEW_REPORT_SCHEMA_VERSION = "review_report_schema_v1"
"""Stable schema version string for future evolution.

Bumping this string is a deliberate governance decision and must be
accompanied by explicit migration of any consuming code.
"""


@dataclass(frozen=True)
class ReviewReportSchema:
    """Typed contract describing the structure a review report must satisfy.

    ``required_fields`` and ``optional_fields`` preserve insertion order;
    that order is load-bearing for future parsing and validation slices.
    ``allowed_verdicts`` is a tuple of :class:`ReviewVerdict` values in
    the canonical ordering. ``kind`` and ``version`` are stable
    discriminators for schema evolution.
    """

    required_fields: tuple[str, ...]
    optional_fields: tuple[str, ...] = field(default=())
    kind: str = REVIEW_REPORT_SCHEMA_KIND
    version: str = REVIEW_REPORT_SCHEMA_VERSION
    allowed_verdicts: tuple[ReviewVerdict, ...] = field(
        default_factory=lambda: tuple(ReviewVerdict)
    )

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "version": self.version,
            "required_fields": list(self.required_fields),
            "optional_fields": list(self.optional_fields),
            "allowed_verdicts": [v.value for v in self.allowed_verdicts],
        }


def default_review_report_schema() -> ReviewReportSchema:
    """Return the canonical M007-S01 review report schema."""

    return ReviewReportSchema(
        required_fields=REVIEW_REPORT_REQUIRED_FIELDS,
        optional_fields=REVIEW_REPORT_OPTIONAL_FIELDS,
    )


def validate_review_report_schema(
    schema: ReviewReportSchema,
) -> tuple[str, ...]:
    """Return a tuple of validation error messages (empty when valid).

    Pure, deterministic, and never raises for constructible malformed
    ``ReviewReportSchema`` inputs such as ``required_fields=42``,
    ``optional_fields=None``, non-:class:`ReviewVerdict` items in
    ``allowed_verdicts``, duplicate fields, or duplicate verdicts.

    Rules enforced:

    - ``required_fields`` and ``optional_fields`` must each be a
      ``tuple`` or ``list`` of non-empty strings with no duplicates
    - ``required_fields`` must include every entry in
      :data:`REVIEW_REPORT_REQUIRED_FIELDS`
    - ``kind`` and ``version`` must be non-empty strings
    - ``allowed_verdicts`` must be a ``tuple`` or ``list`` of
      :class:`ReviewVerdict` instances with no duplicates
    - ``allowed_verdicts`` must include every canonical
      :class:`ReviewVerdict` value
    """

    errors: list[str] = []

    errors.extend(_validate_field_collection("required_fields", schema.required_fields))
    errors.extend(_validate_field_collection("optional_fields", schema.optional_fields))

    if isinstance(schema.required_fields, (tuple, list)):
        present = {entry for entry in schema.required_fields if isinstance(entry, str)}
        for baseline in REVIEW_REPORT_REQUIRED_FIELDS:
            if baseline not in present:
                errors.append(f"required_fields must include {baseline}")

    for attr in ("kind", "version"):
        value = getattr(schema, attr)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{attr} must be a non-empty string")

    errors.extend(_validate_verdict_collection("allowed_verdicts", schema.allowed_verdicts))

    if isinstance(schema.allowed_verdicts, (tuple, list)):
        present_verdicts = {v for v in schema.allowed_verdicts if isinstance(v, ReviewVerdict)}
        for verdict in ReviewVerdict:
            if verdict not in present_verdicts:
                errors.append(f"allowed_verdicts must include {verdict.value}")

    return tuple(errors)


def _validate_field_collection(
    field_name: str,
    value: object,
) -> list[str]:
    """Return deterministic errors for a review report field collection.

    Mirrors the M005-S01 ``_validate_field_collection`` shape: rejects
    non-tuple / non-list inputs without raising, enforces non-empty
    string entries, and rejects duplicates. Never raises.
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


def _validate_verdict_collection(
    field_name: str,
    value: object,
) -> list[str]:
    """Return deterministic errors for the allowed verdicts collection.

    Rejects non-tuple / non-list inputs, enforces
    :class:`ReviewVerdict` instances (plain strings are not accepted),
    and rejects duplicates. Missing canonical verdicts are checked
    separately in :func:`validate_review_report_schema` and only when
    the collection is iterable. Never raises.
    """

    errors: list[str] = []
    if not isinstance(value, (tuple, list)):
        errors.append(f"{field_name} must be a tuple or list of ReviewVerdict values")
        return errors
    seen: set[ReviewVerdict] = set()
    for index, entry in enumerate(value):
        if not isinstance(entry, ReviewVerdict):
            errors.append(f"{field_name}[{index}] must be a ReviewVerdict instance")
            continue
        if entry in seen:
            errors.append(f"{field_name} contains duplicate verdict: {entry.value}")
        else:
            seen.add(entry)
    return errors


# ---------------------------------------------------------------------------
# M007-S02: Verdict parsing
# ---------------------------------------------------------------------------

_VERDICT_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$")
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})")
_LIST_PREFIX_RE = re.compile(r"^(?:-|\*|\d+[.)]) +")
_BACKTICK_RE = re.compile(r"^`(.+)`$")
_INLINE_VERDICT_RE = re.compile(r"^verdict\s*:\s*(.+?)\s*$", re.IGNORECASE)

# Bounded parser-compatibility rule for the accepted historical footer
# ``Verdict: <verdict> - next: <recommended next move>`` (M003 governance
# reports 017-028; reproduced and authorized by Coding Prompt 030). Applied
# during candidate cleanup after the optional ``Verdict:`` label strip, in
# both the ``## Verdict`` section path and the inline fallback: a
# single-token verdict candidate followed by the exact literal separator
# `` - next: `` and a non-empty annotation yields the bare verdict token; the
# annotation is dropped as decoration. Near misses stay invalid: a missing or
# empty annotation, a differently spelled/cased separator (``-next:``,
# ``- Next:``), and extra tokens before the separator are all unchanged, and
# the rule never converts one verdict into another.
_INLINE_NEXT_FOOTER_RE = re.compile(r"^(?P<verdict>\S+) - next: (?=\S)")


def _validate_schema_for_parsing(schema: object) -> tuple[str, ...]:
    """Validate only what the verdict parser needs from a custom schema.

    Lighter than :func:`validate_review_report_schema`: requires only that
    ``schema`` is a :class:`ReviewReportSchema` instance and that its
    ``allowed_verdicts`` is a non-empty tuple/list of
    :class:`ReviewVerdict` instances with no duplicates. Does NOT enforce
    that all four canonical verdicts are present, so callers may pass a
    schema that restricts the accepted verdict set. Never raises.
    """

    if not isinstance(schema, ReviewReportSchema):
        return ("schema must be a ReviewReportSchema instance",)
    errors = list(_validate_verdict_collection("allowed_verdicts", schema.allowed_verdicts))
    if isinstance(schema.allowed_verdicts, (tuple, list)) and not schema.allowed_verdicts:
        errors.append("allowed_verdicts must not be empty")
    return tuple(errors)


@dataclass(frozen=True)
class ReviewReportVerdictParseResult:
    """Result of parsing a review verdict from markdown content or a file.

    ``path`` is the file path string when produced by the file-reading
    command, or the empty string when produced by the text parser alone.
    ``verdict`` is ``None`` when parsing fails; ``raw_verdict`` preserves
    the candidate text after markdown and list-prefix cleanup for
    auditability. ``to_dict()`` serializes ``verdict`` as its ``.value``
    string or ``None``.
    """

    path: str
    verdict: ReviewVerdict | None
    raw_verdict: str
    valid: bool
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "verdict": self.verdict.value if self.verdict is not None else None,
            "raw_verdict": self.raw_verdict,
            "valid": self.valid,
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class ReviewReportVerdictParseCommand:
    """Command to read a single review report file and parse its verdict.

    ``path`` must be an explicit :class:`~pathlib.Path` to one file.
    ``schema`` is optional; when ``None`` the default schema is used.
    The command never scans directories, never writes files, and never
    creates directories.
    """

    path: Path
    schema: ReviewReportSchema | None = None


def parse_review_report_verdict_text(
    content: object,
    schema: ReviewReportSchema | None = None,
) -> ReviewReportVerdictParseResult:
    """Parse a verdict from a review report markdown string.

    Finds the ``## Verdict`` section (ATX heading, levels 1–6,
    case-insensitive), extracts the first non-empty, non-fence,
    non-bullet-prefix line as the verdict candidate, strips a common list
    prefix, a redundant inline ``Verdict:`` label (so ``## Verdict`` followed by
    ``Verdict: pass`` parses), and inline code backticks, then matches
    case-insensitively against ``schema.allowed_verdicts``.

    When (and only when) no ``## Verdict`` heading is present at all, falls back
    to the first inline ``Verdict: <value>`` line (after an optional list prefix),
    so review reports that state the verdict inline rather than under a heading are
    still parsed. The line must begin with ``Verdict:`` so report prose cannot
    match by accident. A present-but-empty ``## Verdict`` section stays invalid
    even if a later line says ``Verdict: pass``.

    Footer compatibility rule (M003, Coding Prompt 030): the accepted
    historical footer ``Verdict: <verdict> - next: <recommended move>`` parses
    to the bare verdict token, both under a ``## Verdict`` heading and in the
    inline fallback. The rule requires exactly one token before the literal
    `` - next: `` separator and a non-empty annotation on the same line, and
    never converts one verdict into another; near misses (empty annotation,
    ``-next:``, ``- Next:``, extra tokens before the separator) remain
    invalid.

    Returns a :class:`ReviewReportVerdictParseResult`. Never raises for
    constructible malformed inputs. The ``path`` field is always ``""``
    when called directly; use :func:`parse_review_report_verdict` for
    file-sourced results with a populated path.
    """

    def _fail(*msgs: str) -> ReviewReportVerdictParseResult:
        return ReviewReportVerdictParseResult(
            path="",
            verdict=None,
            raw_verdict="",
            valid=False,
            errors=tuple(msgs),
        )

    if not isinstance(content, str):
        return _fail("content must be a string")

    if not content.strip():
        return _fail("content must not be empty or whitespace-only")

    if schema is not None:
        schema_errors = _validate_schema_for_parsing(schema)
        if schema_errors:
            return _fail(*schema_errors)
        effective_schema = schema
    else:
        effective_schema = default_review_report_schema()

    lines = content.splitlines()
    in_verdict_section = False
    verdict_candidate_raw: str | None = None

    for line in lines:
        heading_match = _VERDICT_HEADING_RE.match(line)
        if heading_match:
            heading_text = heading_match.group(1).strip().lower()
            if heading_text == "verdict":
                in_verdict_section = True
                continue
            elif in_verdict_section:
                break  # entered a new section

        if not in_verdict_section:
            continue

        stripped = line.strip()
        if not stripped:
            continue

        if _FENCE_RE.match(stripped):
            continue

        verdict_candidate_raw = stripped
        break

    if verdict_candidate_raw is None and not in_verdict_section:
        # Fallback: an inline ``Verdict: <value>`` line (used by review reports
        # that state the verdict inline rather than under a ``## Verdict``
        # heading). The line must start with ``Verdict:`` (after an optional list
        # prefix), so report prose cannot match by accident. This fallback applies
        # ONLY when no ``## Verdict`` heading is present at all: a present-but-empty
        # ``## Verdict`` section stays invalid even if a later line says
        # ``Verdict: pass``.
        for raw_line in lines:
            stripped = raw_line.strip()
            inline_list = _LIST_PREFIX_RE.match(stripped)
            if inline_list:
                stripped = stripped[inline_list.end() :].strip()
            inline_match = _INLINE_VERDICT_RE.match(stripped)
            if inline_match:
                verdict_candidate_raw = inline_match.group(1).strip()
                break

    if verdict_candidate_raw is None:
        if in_verdict_section:
            return _fail("verdict section is present but empty")
        return _fail("no verdict section or inline verdict line found")

    candidate = verdict_candidate_raw
    list_match = _LIST_PREFIX_RE.match(candidate)
    if list_match:
        candidate = candidate[list_match.end() :].strip()

    # Tolerate a redundant inline label inside the ``## Verdict`` section, e.g.
    # ``## Verdict`` followed by ``Verdict: pass`` (a common reviewer habit). The
    # label is stripped to leave the bare verdict (backticks are removed below).
    label_match = _INLINE_VERDICT_RE.match(candidate)
    if label_match:
        candidate = label_match.group(1).strip()

    # Accepted historical ``<verdict> - next: <recommended move>`` footer
    # (M003 governance reports 017-028): keep only the verdict token; the
    # annotation is decoration, never verdict bytes.
    footer_match = _INLINE_NEXT_FOOTER_RE.match(candidate)
    if footer_match:
        candidate = footer_match.group("verdict")

    backtick_match = _BACKTICK_RE.match(candidate)
    if backtick_match:
        candidate = backtick_match.group(1)

    raw_verdict = candidate
    candidate_lower = raw_verdict.lower()

    matched_verdict: ReviewVerdict | None = None
    for v in effective_schema.allowed_verdicts:
        if v.value.lower() == candidate_lower:
            matched_verdict = v
            break

    if matched_verdict is None:
        return ReviewReportVerdictParseResult(
            path="",
            verdict=None,
            raw_verdict=raw_verdict,
            valid=False,
            errors=(f"verdict candidate {raw_verdict!r} is not one of the allowed verdicts",),
        )

    return ReviewReportVerdictParseResult(
        path="",
        verdict=matched_verdict,
        raw_verdict=raw_verdict,
        valid=True,
        errors=(),
    )


def parse_review_report_verdict(
    command: ReviewReportVerdictParseCommand,
) -> ReviewReportVerdictParseResult:
    """Read a review report file and parse its verdict.

    Reads exactly the file at ``command.path``. Never scans directories,
    never writes files, never creates directories. Returns deterministic
    errors for missing file, directory path, unreadable file, invalid
    schema, invalid content, or missing/invalid verdict. Never raises.
    """

    try:
        path = command.path
        path_str = str(path)
    except Exception:
        return ReviewReportVerdictParseResult(
            path="",
            verdict=None,
            raw_verdict="",
            valid=False,
            errors=("invalid path",),
        )

    if not isinstance(path, Path):
        return ReviewReportVerdictParseResult(
            path=path_str,
            verdict=None,
            raw_verdict="",
            valid=False,
            errors=("path must be a Path instance",),
        )

    def _fail(*msgs: str) -> ReviewReportVerdictParseResult:
        return ReviewReportVerdictParseResult(
            path=path_str,
            verdict=None,
            raw_verdict="",
            valid=False,
            errors=tuple(msgs),
        )

    if command.schema is not None:
        schema_errors = _validate_schema_for_parsing(command.schema)
        if schema_errors:
            return _fail(*schema_errors)

    if not path.exists():
        return _fail(f"file not found: {path_str}")

    if path.is_dir():
        return _fail(f"path is a directory: {path_str}")

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as exc:
        return _fail(f"could not read file: {exc}")

    text_result = parse_review_report_verdict_text(content, schema=command.schema)

    return ReviewReportVerdictParseResult(
        path=path_str,
        verdict=text_result.verdict,
        raw_verdict=text_result.raw_verdict,
        valid=text_result.valid,
        errors=text_result.errors,
    )
