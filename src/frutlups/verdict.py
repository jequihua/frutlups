"""Parse the strict review report grammar."""

import re
from dataclasses import dataclass
from typing import Literal


class VerdictError(ValueError):
    """A review report violates its grammar or verdict invariants."""


@dataclass(frozen=True)
class Finding:
    id: str
    severity: Literal["P0", "P1", "P2", "P3"]
    disposition: Literal["open", "closed_by_review", "carried", "waived_by_human"]
    summary: str


@dataclass(frozen=True)
class Review:
    findings: tuple[Finding, ...]
    objective_status: Literal["achieved", "not_achieved", "indeterminate"]
    objective_evidence: str
    verdict: Literal["pass", "needs_work", "blocked", "override"]
    next_move: str
    identity: str
    round: int | None


def _report_lines(text: str, names: tuple[str, ...]) -> list[str]:
    sections = [[]]  # Visible text, followed by each fenced block's content.
    fence = None
    for line in text.splitlines():
        match = re.match(r"^\s*(`{3,}|~{3,})", line)
        if match and fence is None:
            fence = match.group(1)
            sections.append([])
        elif fence and re.fullmatch(rf"\s*{fence[0]}{{{len(fence)},}}\s*", line):
            fence = None
        else:
            sections[-1 if fence else 0].append(line)
    reports = [section for section in sections if any(line.strip() in names for line in section)]
    if len(reports) != 1 or (fence and reports[0] is sections[-1]):
        return []
    return reports[0]


def _cells(line: str) -> list[str]:
    return [item.strip() for item in line.strip().strip("|").split("|")]


def parse(text: str) -> Review:
    names = ("## Findings", "## Closure Decision", "## Verdict")
    lines = _report_lines(text, names)
    headings = {
        name: [index for index, line in enumerate(lines) if line.strip() == name]
        for name in names
    }
    if any(len(found) != 1 for found in headings.values()):
        raise VerdictError(
            "review requires exactly one Findings, Closure Decision, and Verdict heading"
            "; the report must not be enclosed in a code fence"
        )
    findings_at, closure_at, verdict_at = (headings[name][0] for name in names)
    if not findings_at < closure_at < verdict_at:
        raise VerdictError("review sections are out of order")
    title_pattern = r"# Review: (M\d{3}(?:-S\d{2})?) round (\d+|holistic)"
    match = next(
        (
            match for line in lines[:findings_at]
            if (match := re.fullmatch(title_pattern, line.strip()))
        ),
        None,
    )
    if not match:
        raise VerdictError("review title is missing or invalid")
    identity, round_text = match.groups()
    table = [line for line in lines[findings_at + 1:closure_at]
             if line.strip().startswith("|")]
    header = _cells(table[0]) if table else []
    separator = _cells(table[1]) if len(table) > 1 else []
    if header != ["id", "severity", "disposition", "summary"]:
        raise VerdictError("findings table header or separator is missing")
    if not separator or not all(set(item) <= {"-", ":"} for item in separator):
        raise VerdictError("findings table header or separator is missing")
    findings, seen = [], set()
    for line in table[2:]:
        cells = _cells(line)
        if len(cells) == 4 and all(set(item) <= {"-", ":"} for item in cells):
            continue
        valid = (
            len(cells) == 4 and bool(cells[0]) and cells[0] not in seen
            and cells[1] in ("P0", "P1", "P2", "P3")
            and cells[2] in ("open", "closed_by_review", "carried", "waived_by_human")
            and bool(cells[3])
        )
        if not valid:
            raise VerdictError(f"invalid findings row: {line.strip()}")
        seen.add(cells[0])
        findings.append(Finding(*cells))
    closure = [line.strip() for line in lines[closure_at + 1:verdict_at] if line.strip()]
    statuses = [line.removeprefix("Objective status: ") for line in closure
                if line.startswith("Objective status: ")]
    evidence = [line.removeprefix("Objective evidence: ") for line in closure
                if line.startswith("Objective evidence: ")]
    valid = (
        len(statuses) == 1 and statuses[0] in ("achieved", "not_achieved", "indeterminate")
        and len(evidence) == 1 and bool(evidence[0])
    )
    if not valid:
        raise VerdictError("invalid closure decision")
    verdict_lines = [line.strip() for line in lines[verdict_at + 1:] if line.strip()]
    verdict_match = re.fullmatch(
        r"Verdict: (pass|needs_work|blocked|override) - next: (.+)",
        verdict_lines[0] if verdict_lines else "",
    )
    if not verdict_match or any(line.startswith("Verdict:") for line in verdict_lines[1:]):
        raise VerdictError("invalid verdict line")
    verdict, next_move = verdict_match.groups()
    open_ids = tuple(
        item.id for item in findings
        if item.severity in ("P0", "P1", "P2") and item.disposition == "open"
    )
    if verdict in ("pass", "override") and open_ids:
        raise VerdictError(f"{verdict} cannot have open P0-P2 findings")
    if round_text == "holistic":
        invalid_ids = [
            item.id for item in findings if item.severity in ("P0", "P1", "P2")
            and not re.fullmatch(rf"{identity}-S\d{{2}}-.+", item.id)
        ]
        if invalid_ids:
            raise VerdictError("every holistic P0-P2 finding id must start with a slice id")
    return Review(tuple(findings), statuses[0], evidence[0], verdict, next_move, identity,
                  None if round_text == "holistic" else int(round_text))


def open_blocking(review: Review) -> tuple[str, ...]:
    return tuple(
        item.id for item in review.findings
        if item.severity in ("P0", "P1", "P2") and item.disposition == "open"
    )


def finding_rows(text: str, ids: tuple[str, ...]) -> tuple[str, ...]:
    section = text.partition("## Findings")[2].partition("\n## ")[0]
    return tuple(
        line for line in section.splitlines() if line.lstrip().startswith("|")
        and line.strip().strip("|").split("|", 1)[0].strip() in ids
    )
