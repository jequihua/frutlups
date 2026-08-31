"""Closure Decision parsing and the three-dimension closure receipt (M005-S01).

The review-side closure record is defined by the contract-v1 slice prompt
contract (section 9) and the review protocol's Closure Record: a report holds
exactly one line-start ``## Closure Decision`` heading and exactly one
line-start ``## Verdict`` heading, the closure section is the section
immediately before the verdict section, it carries exactly two non-empty
lines (``Objective status:`` then ``Objective evidence:``), the verdict
footer is the first non-empty line under ``## Verdict`` in the exact shape
``Verdict: <value> - next: <one move>``, and no objective line stands inside
the verdict section.

Reading is section-local and line-based with no fence semantics: a heading
line inside a fenced example still counts (the contract forbids that
residue), and objective lines outside the closure section are not authority
and are not counted. The reason codes are the released checker's
review-report codes, emitted in the same order and cardinality.

Finite domain (Prompt 006): the grammar above; objective values ``achieved``,
``not_achieved``, ``not_applicable``, ``indeterminate``; verdict values
``pass``, ``needs_work``, ``blocked``, ``override``; route values
``advance_to_next_slice``, ``milestone_complete``, ``recode_same_slice``,
``unblock_same_slice``, ``human_override_required``, ``invalid``. Nothing here
claims arbitrary prose grammars or route values outside that vocabulary.

Surfaces:

- :class:`ObjectiveStatus`, :class:`FrutlupsRoute` — the closed vocabularies
  (``ReviewVerdict`` is reused from :mod:`frutlups.review_report`)
- :func:`parse_closure_decision_text` / :func:`parse_closure_decision_file`
  — the closure-record parser returning :class:`ClosureParseResult`
- :class:`ClosureReceipt` and :func:`build_closure_receipt` — verdict,
  objective status, and route as three separate fields, each drawn only from
  its own vocabulary; a conflated or out-of-vocabulary value refuses

Routing is not decided here: this module only exposes the route dimension.
The frontier-v2 transition lives in :mod:`frutlups.frontier`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from frutlups.review_report import ReviewVerdict

__all__ = [
    "CLOSURE_HEADING",
    "CLOSURE_REASON_CODES",
    "ClosureParseResult",
    "ClosureReceipt",
    "ClosureReceiptResult",
    "FrutlupsRoute",
    "ObjectiveStatus",
    "VERDICT_HEADING",
    "build_closure_receipt",
    "parse_closure_decision_file",
    "parse_closure_decision_text",
]


class ObjectiveStatus(StrEnum):
    """The four canonical objective status values of the closure record."""

    ACHIEVED = "achieved"
    NOT_ACHIEVED = "not_achieved"
    NOT_APPLICABLE = "not_applicable"
    INDETERMINATE = "indeterminate"


class FrutlupsRoute(StrEnum):
    """The six canonical frutlups route values (the operating tool's dimension)."""

    ADVANCE_TO_NEXT_SLICE = "advance_to_next_slice"
    MILESTONE_COMPLETE = "milestone_complete"
    RECODE_SAME_SLICE = "recode_same_slice"
    UNBLOCK_SAME_SLICE = "unblock_same_slice"
    HUMAN_OVERRIDE_REQUIRED = "human_override_required"
    INVALID = "invalid"


CLOSURE_HEADING = "Closure Decision"
VERDICT_HEADING = "Verdict"

CLOSURE_REASON_CODES: tuple[str, ...] = (
    "closure_section_missing",
    "closure_section_duplicate",
    "closure_after_verdict",
    "closure_not_adjacent",
    "closure_line_count",
    "objective_status_line_missing",
    "objective_status_invalid",
    "objective_evidence_line_missing",
    "verdict_section_missing",
    "verdict_section_duplicate",
    "verdict_footer_invalid",
    "objective_status_in_verdict",
    "report_unreadable",
)
"""The contract review-report reason codes plus the file-read refusal."""

_HEADING_RE = re.compile(r"^## (.+?)\s*$")
_VERDICT_FOOTER_RE = re.compile(
    r"^Verdict: (?P<value>pass|needs_work|blocked|override) - next: (?P<move>\S.*)$"
)
_STATUS_LABEL = "Objective status:"
_EVIDENCE_LABEL = "Objective evidence:"


@dataclass(frozen=True)
class ClosureParseResult:
    """Parsed closure dimensions or the contract refusal reasons.

    ``valid`` is ``True`` exactly when ``reason_codes`` is empty; then
    ``verdict``, ``objective_status``, ``objective_evidence``, and
    ``next_move`` are populated from the closure section and verdict footer.
    On refusal the dimensions are ``None``/``""`` and ``reason_codes`` lists
    every contract code the report violates, in checker order.
    """

    valid: bool
    verdict: ReviewVerdict | None
    objective_status: ObjectiveStatus | None
    objective_evidence: str
    next_move: str
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "verdict": self.verdict.value if self.verdict is not None else None,
            "objective_status": (
                self.objective_status.value if self.objective_status is not None else None
            ),
            "objective_evidence": self.objective_evidence,
            "next_move": self.next_move,
            "reason_codes": list(self.reason_codes),
        }


def _refusal(*codes: str) -> ClosureParseResult:
    return ClosureParseResult(
        valid=False,
        verdict=None,
        objective_status=None,
        objective_evidence="",
        next_move="",
        reason_codes=tuple(codes),
    )


def _heading_sections(text: str) -> tuple[list[str], dict[str, list[str]]]:
    """Line-start ``## `` headings in file order and each heading's body lines.

    Fences are ignored by design (contract section 9).
    """

    order: list[str] = []
    bodies: dict[str, list[str]] = {"": []}
    current = ""
    for line in text.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            current = match.group(1)
            order.append(current)
            bodies.setdefault(current, [])
            continue
        bodies.setdefault(current, []).append(line)
    return order, bodies


def parse_closure_decision_text(content: object) -> ClosureParseResult:
    """Parse the closure record and verdict footer from report text.

    Mirrors the released contract checker's review-report rules line for
    line, so every released closure fixture reproduces its released reason
    codes. Never raises for constructible inputs; a non-string refuses with
    ``report_unreadable``.
    """

    if not isinstance(content, str):
        return _refusal("report_unreadable")

    codes: list[str] = []
    order, bodies = _heading_sections(content)
    closure_n = order.count(CLOSURE_HEADING)
    verdict_n = order.count(VERDICT_HEADING)
    if closure_n == 0:
        codes.append("closure_section_missing")
    elif closure_n > 1:
        codes.append("closure_section_duplicate")
    if verdict_n == 0:
        codes.append("verdict_section_missing")
    elif verdict_n > 1:
        codes.append("verdict_section_duplicate")
    if closure_n != 1 or verdict_n != 1:
        return _refusal(*codes)

    closure_index = order.index(CLOSURE_HEADING)
    verdict_index = order.index(VERDICT_HEADING)
    if closure_index > verdict_index:
        codes.append("closure_after_verdict")
    elif verdict_index != closure_index + 1:
        codes.append("closure_not_adjacent")

    lines = [line for line in bodies.get(CLOSURE_HEADING, []) if line.strip()]
    if len(lines) != 2:
        codes.append("closure_line_count")
    status_lines = [line for line in lines if line.startswith(_STATUS_LABEL)]
    status_value = ""
    evidence_value = ""
    if len(status_lines) != 1 or lines[0] != status_lines[0]:
        codes.append("objective_status_line_missing")
    else:
        status_value = status_lines[0].split(":", 1)[1].strip()
        if status_value not in ObjectiveStatus.__members__.values():
            codes.append("objective_status_invalid")
        evidence_line = lines[1] if len(lines) > 1 else ""
        if evidence_line.startswith(_EVIDENCE_LABEL):
            evidence_value = evidence_line.split(":", 1)[1].strip()
        if not evidence_value:
            codes.append("objective_evidence_line_missing")

    verdict_lines = [line for line in bodies.get(VERDICT_HEADING, []) if line.strip()]
    footer = _VERDICT_FOOTER_RE.match(verdict_lines[0]) if verdict_lines else None
    if footer is None:
        codes.append("verdict_footer_invalid")
    if any(line.startswith(_STATUS_LABEL) for line in verdict_lines):
        codes.append("objective_status_in_verdict")

    if codes:
        return _refusal(*codes)
    assert footer is not None  # guarded by verdict_footer_invalid above
    return ClosureParseResult(
        valid=True,
        verdict=ReviewVerdict(footer.group("value")),
        objective_status=ObjectiveStatus(status_value),
        objective_evidence=evidence_value,
        next_move=footer.group("move"),
        reason_codes=(),
    )


def parse_closure_decision_file(path: Path) -> ClosureParseResult:
    """Read one report file as UTF-8 and parse its closure record.

    A missing, unreadable, directory, or non-UTF-8 path refuses with
    ``report_unreadable``. Never writes, scans, or raises for such paths.
    """

    if not isinstance(path, Path):
        return _refusal("report_unreadable")
    try:
        content = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        return _refusal("report_unreadable")
    return parse_closure_decision_text(content)


@dataclass(frozen=True)
class ClosureReceipt:
    """Verdict, objective status, and route as three separate dimensions."""

    verdict: ReviewVerdict
    objective_status: ObjectiveStatus
    route: FrutlupsRoute

    def to_dict(self) -> dict[str, str]:
        return {
            "verdict": self.verdict.value,
            "objective_status": self.objective_status.value,
            "route": self.route.value,
        }


@dataclass(frozen=True)
class ClosureReceiptResult:
    """A built receipt, or the refusal reason when a dimension is not admissible."""

    receipt: ClosureReceipt | None
    reason: str

    @property
    def valid(self) -> bool:
        return self.receipt is not None


_DIMENSIONS: tuple[tuple[str, type[StrEnum]], ...] = (
    ("verdict", ReviewVerdict),
    ("objective_status", ObjectiveStatus),
    ("route", FrutlupsRoute),
)


def _admit(field_name: str, value: object, vocabulary: type[StrEnum]) -> tuple[StrEnum | None, str]:
    """Admit ``value`` into exactly ``vocabulary`` or name the refusal.

    A value that is an exact member of another dimension's vocabulary is a
    conflated receipt (``<field>_conflated``); anything else outside the
    field's own vocabulary is ``<field>_invalid``. Values are compared
    exactly: no case folding, stripping, or splitting of combined values.
    """

    if isinstance(value, vocabulary):
        return value, ""
    if type(value) is str and value in vocabulary.__members__.values():
        return vocabulary(value), ""
    for other_name, other in _DIMENSIONS:
        if other is vocabulary:
            continue
        if isinstance(value, other) or (type(value) is str and value in other.__members__.values()):
            return None, f"{field_name}_conflated_with_{other_name}"
    return None, f"{field_name}_invalid"


def build_closure_receipt(
    verdict: object, objective_status: object, route: object
) -> ClosureReceiptResult:
    """Build the three-field receipt, refusing conflated or foreign values.

    Each argument is an enum member of its own dimension or that member's
    exact string. The first refused dimension in ``verdict``,
    ``objective_status``, ``route`` order names the reason. Never raises.
    """

    admitted: list[StrEnum] = []
    for (field_name, vocabulary), value in zip(
        _DIMENSIONS, (verdict, objective_status, route), strict=True
    ):
        member, reason = _admit(field_name, value, vocabulary)
        if member is None:
            return ClosureReceiptResult(receipt=None, reason=reason)
        admitted.append(member)
    return ClosureReceiptResult(
        receipt=ClosureReceipt(
            verdict=ReviewVerdict(admitted[0]),
            objective_status=ObjectiveStatus(admitted[1]),
            route=FrutlupsRoute(admitted[2]),
        ),
        reason="",
    )
