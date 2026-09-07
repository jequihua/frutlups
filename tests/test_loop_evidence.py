from dataclasses import replace
from itertools import combinations, permutations
from pathlib import Path
from types import SimpleNamespace

import pytest

from frutlups import loop, verdict
from frutlups._loop_evidence import combined_review, prior_finding_ids
from test_loop import conform, events_of, plan
from test_loop import project as project


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
    text = combined_review(
        reviews[::-1] if reverse else reviews,
        seat_names=("reviewer", "claude_reviewer"),
        prior_ids=("M001-S01-F1",),
    )
    merged = verdict.parse(text)
    assert len(merged.findings) == 1
    assert merged.findings[0].severity == "P3"
    assert merged.findings[0].disposition == stricter
    assert merged.findings[0].summary == "Keep this seat's summary."
    assert verdict.finding_rows(text, ("M001-S01-F1",)) == (winning,)


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("finding_id", ["M001-S01-F1", "M001-S01-F1-reviewer"])
@pytest.mark.parametrize(("stricter", "weaker"), [("P0", "P1"), ("P1", "P2"), ("P2", "P3")])
def test_combined_review_keeps_stricter_severity_for_prior_id(
    stricter, weaker, finding_id, reverse,
):
    winning = f"| {finding_id} | {stricter} | open | Stricter prior finding. |"
    other = f"| {finding_id} | {weaker} | open | Weaker prior finding. |"
    reviews = [report([winning], "needs_work"), report([other], "needs_work")]
    text = combined_review(
        reviews[::-1] if reverse else reviews,
        seat_names=("reviewer", "claude_reviewer"),
        prior_ids=(finding_id,),
    )
    merged = verdict.parse(text)
    assert len(merged.findings) == 1
    assert merged.findings[0].severity == stricter
    assert merged.findings[0].disposition == "open"
    assert merged.findings[0].id == finding_id
    assert merged.findings[0].summary == "Stricter prior finding."
    assert verdict.finding_rows(text, (finding_id,)) == (winning,)


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


@pytest.mark.parametrize("prior_ids", [(), ("M001-S01-F1",)])
def test_combined_review_returns_single_seat_text_unchanged(prior_ids):
    _, text = report(["| M001-S01-F1 | P3 | carried | Keep exact formatting. |"])
    text = "```markdown\n" + text + "```\n"
    assert combined_review(
        [(verdict.parse(text), text)], seat_names=("reviewer",), prior_ids=prior_ids,
    ) == text


@pytest.mark.parametrize("reverse", [False, True])
def test_combined_review_keeps_each_new_collision_and_single_seat_id(reverse):
    first = "  |  M001-S01-F1\t| P1 | open | Keep M001-S01-F1 in this summary. |  "
    second = "|M001-S01-F1 | P2 | open | Different finding, same invented id. |"
    unique = "| M001-S01-F2 | P3 | carried | Only one seat reports this. |"
    reviews = [report([first, unique], "needs_work"), report([second], "needs_work")]
    seats = ("reviewer", "claude_reviewer")
    if reverse:
        reviews.reverse()
        seats = seats[::-1]

    text = combined_review(reviews, seat_names=seats, prior_ids=())

    merged = verdict.parse(text)
    assert len(merged.findings) == 3
    findings = {f.id: f for f in merged.findings}
    assert findings["M001-S01-F1-reviewer"].severity == "P1"
    assert findings["M001-S01-F1-reviewer"].disposition == "open"
    assert findings["M001-S01-F1-claude_reviewer"].severity == "P2"
    assert findings["M001-S01-F1-claude_reviewer"].disposition == "open"
    assert verdict.finding_rows(text, ("M001-S01-F1-reviewer",)) == (
        "  |  M001-S01-F1-reviewer\t| P1 | open | Keep M001-S01-F1 in this summary. |  ",
    )
    assert verdict.finding_rows(text, ("M001-S01-F1-claude_reviewer",)) == (
        "|M001-S01-F1-claude_reviewer | P2 | open | Different finding, same invented id. |",
    )
    assert verdict.finding_rows(text, ("M001-S01-F2",)) == (unique,)
    assert set(verdict.open_blocking(merged)) == {
        "M001-S01-F1-reviewer", "M001-S01-F1-claude_reviewer",
    }


def test_combined_review_refuses_suffix_that_would_overwrite_another_finding():
    shared = "| M001-S01-F1 | P2 | open | New finding. |"
    existing = "| M001-S01-F1-reviewer | P3 | carried | Preserve this prior finding. |"
    reviews = [report([shared, existing], "needs_work"), report([shared], "needs_work")]

    with pytest.raises(ValueError, match="seat-suffixed finding id already exists: M001-S01-F1"):
        combined_review(
            reviews,
            seat_names=("reviewer", "claude_reviewer"),
            prior_ids=("M001-S01-F1-reviewer",),
        )


@pytest.mark.parametrize("with_report", [True, False])
def test_prior_ids_include_every_previous_row_and_reopen_reason(tmp_path, with_report):
    rows = [
        "| M001-S01-F1 | P0 | open | Open previous finding. |",
        "| M001-S01-F2 | P1 | closed_by_review | Closed previous finding. |",
        "| M001-S01-F3 | P2 | waived_by_human | Human-waived previous finding. |",
        "| M001-S01-F4-reviewer | P3 | carried | Carried suffixed previous finding. |",
    ]
    if with_report:
        (tmp_path / "prior.md").write_text(report(rows, "needs_work")[1], encoding="utf-8")
    current = SimpleNamespace(
        id="M001-S01",
        last_report="prior.md" if with_report else None,
        reopen_reason="holistic findings M001-S01-H1, M001-S01-H10",
    )

    ids = prior_finding_ids(tmp_path, current)

    expected = {"M001-S01-H1", "M001-S01-H10"}
    if with_report:
        expected.update({"M001-S01-F1", "M001-S01-F2", "M001-S01-F3", "M001-S01-F4-reviewer"})
    assert ids == expected
    current.last_report = None
    current.reopen_reason = None
    assert prior_finding_ids(tmp_path, current) == set()


def test_routed_new_findings_reach_corrective_prompt_and_merge_as_prior(project):
    root, cfg, roadmap = project
    roadmap = replace(
        roadmap,
        review_routing={**roadmap.review_routing, "ordinary": ("reviewer", "claude_reviewer")},
    )
    first = "| M001-S01-F1 | P1 | open | First seat's new finding. |"
    second = "| M001-S01-F1 | P2 | open | Second seat's different finding. |"
    suffixed = (
        "| M001-S01-F1-reviewer | P1 | open | First seat's new finding. |",
        "| M001-S01-F1-claude_reviewer | P2 | open | Second seat's different finding. |",
    )
    closed = [row.replace("| open |", "| closed_by_review |") for row in suffixed]
    plan(root, reviewer=[
        {"verdict": "needs_work", "rows": [first]},
        {"verdict": "needs_work", "rows": [second]},
        {"rows": closed},
        {"rows": closed},
    ])

    assert loop.run(root, cfg, roadmap) == loop.StopReason.done

    reviewed = events_of(root, cfg, "reviewed")
    assert len(reviewed) == 2
    assert reviewed[0].data["open"] == ("M001-S01-F1-reviewer", "M001-S01-F1-claude_reviewer")
    assert reviewed[1].data["open"] == ()
    prompts = events_of(root, cfg, "prompt")
    assert len(prompts) == 2
    corrective = (root / prompts[1].data["path"]).read_text(encoding="utf-8")
    assert suffixed[0] in corrective
    assert suffixed[1] in corrective
    final = (root / reviewed[1].data["report"]).read_text(encoding="utf-8")
    parsed = verdict.parse(final)
    assert tuple(f.id for f in parsed.findings) == (
        "M001-S01-F1-reviewer", "M001-S01-F1-claude_reviewer",
    )
    assert verdict.finding_rows(final, tuple(f.id for f in parsed.findings)) == tuple(closed)
    artifacts = events_of(root, cfg, "artifact")
    assert [(e.round, e.data["role"]) for e in artifacts] == [
        (1, "review_prompt"), (2, "review_prompt"),
    ]
    conform(root, cfg, roadmap)
