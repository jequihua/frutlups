import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest
from frutlups.verdict import VerdictError, _cells, open_blocking, parse


def _report(
    rows: str = "",
    verdict: str = "pass",
    identity: str = "M001-S01",
    round_text: str = "1",
) -> str:
    return f"""# Review: {identity} round {round_text}

## Findings
| id | severity | disposition | summary |
| --- | --- | --- | --- |
{rows}
## Closure Decision
Objective status: achieved
Objective evidence: Tests passed.

## Verdict
Verdict: {verdict} - next: accept
"""


def test_parse_returns_frozen_review_and_open_blocking_ids() -> None:
    review = parse(_report("| M001-S01-F1 | P2 | open | Fix it. |", "needs_work"))

    assert review.identity == "M001-S01"
    assert review.round == 1
    assert open_blocking(review) == ("M001-S01-F1",)


@pytest.mark.parametrize(
    ("row", "summary"),
    [
        ("| F1 | P2 | open | Use `left|right`. |", "Use `left | right`."),
        (r"| F1 | P2 | open | Use left\|right. |", "Use left|right."),
        (r"| F1 | P2 | open | `left|right` and left\|right. |",
         "`left | right` and left|right."),
        (r"| F1 | P2 | open | Ends in \|", "Ends in |"),
        (r"|F1|P2|open|Ends in \||", "Ends in |"),
        ("| F1 | P2 | open |", None),
        ("| F1 | P2 | open |   |", None),
    ],
    ids=("backticks", "gfm-escaped", "mixed", "escaped-ending", "compact-escaped-ending",
         "three-cells", "empty-summary"),
)
def test_findings_rows_match_template(row: str, summary: str | None) -> None:
    text = _report(row, "needs_work")
    result = subprocess.run(
        [sys.executable, "-B", "-c", (
            "import json, sys\n"
            "from _evidence import parse_review\n"
            "try:\n"
            "    result = parse_review(sys.stdin.read())\n"
            "except ValueError as exc:\n"
            "    result = {'error': str(exc)}\n"
            "print(json.dumps(result))\n"
        )],
        cwd=Path(__file__).parent / "fixtures/v4_project/scripts",
        input=text, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    template = json.loads(result.stdout)

    if summary is None:
        with pytest.raises(VerdictError) as caught:
            parse(text)
        assert str(caught.value) == f"invalid findings row: {row}"
        assert str(caught.value) == template["error"]
    else:
        review = parse(text)
        assert len(review.findings) == 1
        finding = review.findings[0]
        assert finding.id == "F1"
        assert finding.severity == "P2"
        assert finding.disposition == "open"
        assert finding.summary == summary
        assert [asdict(finding)] == template["findings"]


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (" F1 | P2 | open | Summary ", ["F1", "P2", "open", "Summary"]),
        ("|F1|P2|open|Summary", ["F1", "P2", "open", "Summary"]),
        ("F1|P2|open|Summary|", ["F1", "P2", "open", "Summary"]),
        ("||F1|P2|open|Summary||", ["", "F1", "P2", "open | Summary | "]),
        (r"|F1|P2|open|two \\| pipes|", ["F1", "P2", "open", r"two \| pipes"]),
    ],
    ids=("no-outer-pipes", "leading-only", "trailing-only", "one-outer-pipe-only",
         "immediate-backslash"),
)
def test_cells_delimiters_and_immediate_backslash_rule(row, expected):
    assert _cells(row) == expected


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (
            _report("| M001-S01-F1 | P2 | open | Fix it. |", "pass"),
            "pass cannot have open P0-P2",
        ),
        (_report().replace("## Verdict\n", ""), "exactly one"),
        (_report() + "\n## Verdict\nVerdict: pass - next: again\n", "exactly one"),
        (_report("| F1 | P9 | open | Invalid severity. |", "needs_work"), "invalid findings row"),
        (
            _report("| H1-F1 | P1 | open | Missing slice. |", "needs_work", "M001", "holistic"),
            "holistic P0-P2 finding id",
        ),
    ],
    ids=("pass-open", "missing-verdict", "duplicate-verdict", "bad-row", "holistic-id"),
)
def test_parse_refuses_invalid_reports(text: str, message: str) -> None:
    with pytest.raises(VerdictError, match=message):
        parse(text)


@pytest.mark.parametrize("trailing_prose", ["", "\nThe review is complete.\n"],
                         ids=("canary-2026-09-05", "canary-2026-09-05-trailing-prose"))
def test_parse_accepts_complete_fenced_report_with_prose(trailing_prose: str) -> None:
    report = _report("| M001-S01-F1 | P2 | open | Fix it. |", "needs_work")
    reply = f"Here is the complete review report.\n\n```markdown\n{report}```\n{trailing_prose}"

    review = parse(reply)

    assert review == parse(report)
    assert open_blocking(review) == ("M001-S01-F1",)


@pytest.mark.parametrize(
    "text",
    [
        _report().replace("## Verdict\n", "```markdown\n## Verdict\n") + "```\n",
        "```markdown\n" + _report().replace(
            "## Verdict\n", "```\n\n```markdown\n## Verdict\n"
        ) + "```\n",
        _report() + "\n```markdown\n## Verdict\n```\n",
        "```markdown\n" + _report() + "```\n\n```markdown\n" + _report() + "```\n",
        "```markdown\n" + _report(),
    ],
    ids=("mixed", "split-two-fences", "visible-and-fenced", "two-reports", "unclosed"),
)
def test_parse_refuses_ambiguous_or_incomplete_fenced_report(text: str) -> None:
    with pytest.raises(VerdictError) as error:
        parse(text)

    assert str(error.value) == (
        "review requires exactly one Findings, Closure Decision, and Verdict heading"
        "; the report must not be enclosed in a code fence"
    )


def test_fenced_report_still_refuses_pass_with_open_blocking_findings() -> None:
    report = _report("| M001-S01-F1 | P2 | open | Fix it. |")

    with pytest.raises(VerdictError, match="pass cannot have open P0-P2"):
        parse(f"```markdown\n{report}```\n")


@pytest.mark.parametrize(("opening", "closing"), [("```", "```"), ("~~~markdown", "~~~"),
                                                  ("````markdown", "`````")])
def test_fenced_holistic_pass_uses_matching_fence(opening: str, closing: str) -> None:
    report = _report(identity="M001", round_text="holistic")

    assert parse(f"{opening}\n{report}{closing}\n") == parse(report)


@pytest.mark.parametrize("fenced", [False, True], ids=("plain", "fenced"))
def test_unrelated_code_block_does_not_change_report(fenced: bool) -> None:
    report = _report()
    reply = f"```markdown\n{report}```\n" if fenced else report

    assert parse("```text\nSome context.\n```\n" + reply) == parse(report)
