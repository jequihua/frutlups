from itertools import combinations, permutations
from pathlib import Path

import pytest

from frutlups import verdict
from frutlups._loop_evidence import combined_review


def report(rows, result="pass", status="achieved", evidence="Fixture evidence."):
    text = (
        "# Review: M001-S01 round 2\n\n## Findings\n\n"
        "| id | severity | disposition | summary |\n| --- | --- | --- | --- |\n"
        + "\n".join(rows)
        + f"\n\n## Closure Decision\nObjective status: {status}\n"
        f"Objective evidence: {evidence}\n\n## Verdict\n"
        f"Verdict: {result} - next: Follow {result}.\n"
    )
    return verdict.parse(text), text


def test_combined_review_merges_recorded_corrective_reports():
    # Exact copies of the three M004-S01 round 2 reports under 05_governance/reviews/m004/.
    fixtures = Path(__file__).parent / "fixtures" / "reviews"
    reviews = []
    for seat in ("reviewer", "claude", "gpt6"):
        text = (fixtures / f"M004-S01_r2_review_{seat}.md").read_text(encoding="utf-8")
        reviews.append((verdict.parse(text), text))

    text = combined_review(reviews)
    merged = verdict.parse(text)
    assert len(merged.findings) == 10
    assert [(f.id, f.severity, f.disposition) for f in merged.findings] == [
        ("M004-S01-01", "P2", "closed_by_review"),
        ("M004-S01-11", "P2", "closed_by_review"),
        ("M004-S01-21", "P2", "closed_by_review"),
        ("M004-S01-03", "P3", "closed_by_review"),
        ("M004-S01-22", "P3", "closed_by_review"),
        ("M004-S01-23", "P3", "carried"),
        ("M004-S01-04", "P3", "open"),
        ("M004-S01-12", "P3", "carried"),
        ("M004-S01-13", "P3", "carried"),
        ("M004-S01-25", "P3", "carried"),
    ]
    expected_rows = (
        verdict.finding_rows(reviews[0][1], tuple(f.id for f in reviews[0][0].findings))
        + verdict.finding_rows(reviews[1][1], ("M004-S01-12", "M004-S01-13"))
        + verdict.finding_rows(reviews[2][1], ("M004-S01-25",))
    )
    assert verdict.finding_rows(text, tuple(f.id for f in merged.findings)) == expected_rows
    assert merged.verdict == "pass"
    assert merged.objective_status == "achieved"
    assert merged.objective_evidence == reviews[0][0].objective_evidence
    assert merged.next_move == reviews[0][0].next_move


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    ("stricter", "weaker"),
    combinations(("open", "carried", "closed_by_review", "waived_by_human"), 2),
)
def test_combined_review_prioritizes_disposition_over_severity(stricter, weaker, reverse):
    winning = "| M001-S01-F1 | P3 | " + stricter + " | Keep this seat's summary. |"
    other = "| M001-S01-F1 | P0 | " + weaker + " | Other seat's summary. |"
    reviews = [report([winning]), report([other])]
    text = combined_review(reviews[::-1] if reverse else reviews)
    merged = verdict.parse(text)
    assert len(merged.findings) == 1
    assert merged.findings[0].severity == "P3"
    assert merged.findings[0].disposition == stricter
    assert merged.findings[0].summary == "Keep this seat's summary."
    assert verdict.finding_rows(text, ("M001-S01-F1",)) == (winning,)


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(("stricter", "weaker"), [("P0", "P1"), ("P1", "P2"), ("P2", "P3")])
def test_combined_review_keeps_stricter_severity_for_shared_new_id(stricter, weaker, reverse):
    winning = f"| M001-S01-NEW | {stricter} | open | Stricter new finding. |"
    other = f"| M001-S01-NEW | {weaker} | open | Weaker new finding. |"
    reviews = [report([winning], "needs_work"), report([other], "needs_work")]
    text = combined_review(reviews[::-1] if reverse else reviews)
    merged = verdict.parse(text)
    assert len(merged.findings) == 1
    assert merged.findings[0].severity == stricter
    assert merged.findings[0].disposition == "open"
    assert merged.findings[0].summary == "Stricter new finding."
    assert verdict.finding_rows(text, ("M001-S01-NEW",)) == (winning,)


@pytest.mark.parametrize("order", permutations(("pass", "needs_work", "blocked")))
def test_combined_review_keeps_strictest_verdict_and_its_closure(order):
    statuses = {"pass": "achieved", "needs_work": "not_achieved", "blocked": "indeterminate"}
    reviews = [report([], result, statuses[result], f"Evidence for {result}.") for result in order]
    merged = verdict.parse(combined_review(reviews))
    assert merged.verdict == "blocked"
    assert merged.objective_status == "indeterminate"
    assert merged.objective_evidence == "Evidence for blocked."
    assert merged.next_move == "Follow blocked."


def test_combined_review_preserves_distinct_id_order_when_replacing_shared_row():
    first = "| M001-S01-F1 | P3 | carried | First finding. |"
    shared = "| M001-S01-F2 | P3 | carried | Earlier summary. |"
    last = "| M001-S01-F3 | P3 | carried | Last finding. |"
    winning = "| M001-S01-F2 | P3 | open | Later stricter summary. |"
    text = combined_review([report([first, shared]), report([last, winning])])
    merged = verdict.parse(text)
    assert [f.id for f in merged.findings] == ["M001-S01-F1", "M001-S01-F2", "M001-S01-F3"]
    assert verdict.finding_rows(text, tuple(f.id for f in merged.findings)) == (
        first, winning, last,
    )


def test_combined_review_returns_single_seat_text_unchanged():
    _, text = report(["| M001-S01-F1 | P3 | carried | Keep exact formatting. |"])
    text = "```markdown\n" + text + "```\n"
    assert combined_review([(verdict.parse(text), text)]) == text
