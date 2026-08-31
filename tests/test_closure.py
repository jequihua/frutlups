"""Tests for M005-S01 Closure Decision parsing and the three-field receipt.

Falsifiers are literal: the twenty released Template V3.1.0 closure fixtures
under the self-contained release-authority fixture bundle with hand-copied
expected results, plus hand-written mutation cases. No expectation is
generated from the implementation's enums or tables. Each case records its
parsed dimensions or refusal codes as the causal witness.
"""

import json
import tempfile
import unittest
from pathlib import Path

from frutlups.closure import (
    CLOSURE_REASON_CODES,
    ClosureReceipt,
    FrutlupsRoute,
    ObjectiveStatus,
    build_closure_receipt,
    parse_closure_decision_file,
    parse_closure_decision_text,
)
from frutlups.review_report import ReviewVerdict

_FIXTURE_DIR = (
    Path(__file__).resolve().parent / "fixtures" / "release_v0_2_0" / "slice_contract"
)
_MANIFEST = _FIXTURE_DIR / "manifest.json"

_EVIDENCE = (
    "the replay stopped at case 1 with the preserved Java exception; "
    "joined_ledger.json carries zero rows"
)
_MOVE = "the owner rules on reopening M002 with a fresh replay authority"

# Released fixture -> (released result, exact reason codes the contract
# checker emits, in order). The manifest lists the one named code per
# malformed fixture; the exact tuple is the full checker output, copied by
# hand from a checker run, never from the implementation.
_RELEASED_FIXTURES = {
    "review_report_closure_after_verdict.md": ("fail", ("closure_after_verdict",)),
    "review_report_closure_duplicate.md": ("fail", ("closure_section_duplicate",)),
    "review_report_closure_missing.md": ("fail", ("closure_section_missing",)),
    "review_report_closure_missing_evidence_line.md": (
        "fail",
        ("closure_line_count", "objective_evidence_line_missing"),
    ),
    "review_report_closure_not_adjacent.md": ("fail", ("closure_not_adjacent",)),
    "review_report_closure_third_line.md": ("fail", ("closure_line_count",)),
    "review_report_closure_valid.md": ("pass", ()),
    "review_report_evidence_duplicate.md": ("pass", ()),
    "review_report_fake_opener_duplicates_valid.md": ("pass", ()),
    "review_report_heading_in_example_invalid.md": ("fail", ("closure_section_duplicate",)),
    "review_report_indented_fence_valid.md": ("pass", ()),
    "review_report_long_backtick_fence_valid.md": ("pass", ()),
    "review_report_status_duplicate.md": ("pass", ()),
    "review_report_status_in_verdict.md": ("fail", ("objective_status_in_verdict",)),
    "review_report_status_invalid.md": ("fail", ("objective_status_invalid",)),
    "review_report_status_line_missing.md": ("fail", ("objective_status_line_missing",)),
    "review_report_tilde_fenced_example_valid.md": ("pass", ()),
    "review_report_verdict_duplicate.md": ("fail", ("verdict_section_duplicate",)),
    "review_report_verdict_footer_invalid.md": ("fail", ("verdict_footer_invalid",)),
    "review_report_verdict_missing.md": ("fail", ("verdict_section_missing",)),
}


def _report(closure_lines, verdict_footer, *, before="", between="", after=""):
    """Assemble a report from literal parts (headings are written verbatim)."""

    return (
        "# Review Report: M002-S02 attempt 002\n\n## Findings\n\n- none blocking\n"
        + before
        + "\n## Closure Decision\n\n"
        + closure_lines
        + between
        + "\n## Verdict\n\n"
        + verdict_footer
        + "\n"
        + after
    )


_VALID_CLOSURE = f"Objective status: not_achieved\nObjective evidence: {_EVIDENCE}\n"
_VALID_FOOTER = f"Verdict: pass - next: {_MOVE}"


class ReleasedFixtureTests(unittest.TestCase):
    """Every released closure fixture reproduces its released expected result."""

    def test_fixture_directory_holds_exactly_the_twenty_released_fixtures(self):
        names = sorted(p.name for p in _FIXTURE_DIR.glob("review_report_*.md"))
        self.assertEqual(names, sorted(_RELEASED_FIXTURES))
        self.assertEqual(len(names), 20)

    def test_each_released_fixture_reproduces_its_released_result(self):
        for name, (result, codes) in _RELEASED_FIXTURES.items():
            with self.subTest(fixture=name):
                parsed = parse_closure_decision_file(_FIXTURE_DIR / name)
                self.assertEqual(parsed.reason_codes, codes)
                self.assertEqual(parsed.valid, result == "pass")
                if result == "pass":
                    self.assertEqual(parsed.verdict, ReviewVerdict.PASS)
                    self.assertEqual(parsed.objective_status, ObjectiveStatus.NOT_ACHIEVED)
                    self.assertEqual(parsed.objective_evidence, _EVIDENCE)
                    self.assertEqual(parsed.next_move, _MOVE)
                else:
                    self.assertIsNone(parsed.verdict)
                    self.assertIsNone(parsed.objective_status)
                    self.assertEqual(parsed.objective_evidence, "")
                    self.assertEqual(parsed.next_move, "")

    def test_released_manifest_codes_are_reproduced(self):
        """The manifest's named code per fixture is among the parsed codes."""

        manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
        entries = {
            Path(f["path"]).name: f["expected"]
            for f in manifest["fixtures"]
            if Path(f["path"]).name.startswith("review_report_")
        }
        self.assertEqual(sorted(entries), sorted(_RELEASED_FIXTURES))
        for name, expected in entries.items():
            with self.subTest(fixture=name):
                parsed = parse_closure_decision_file(_FIXTURE_DIR / name)
                self.assertEqual(parsed.valid, expected["result"] == "pass")
                for code in expected["codes"]:
                    self.assertIn(code, parsed.reason_codes)


class HandWrittenMutationTests(unittest.TestCase):
    """Literal mutation cases around the finite grammar."""

    def test_each_mutation_refuses_with_its_exact_codes(self):
        cases = [
            (
                "closure lines swapped",
                _report(
                    f"Objective evidence: {_EVIDENCE}\nObjective status: not_achieved\n",
                    _VALID_FOOTER,
                ),
                ("objective_status_line_missing",),
            ),
            (
                "empty evidence value",
                _report("Objective status: achieved\nObjective evidence:\n", _VALID_FOOTER),
                ("objective_evidence_line_missing",),
            ),
            (
                "capitalised objective value",
                _report(f"Objective status: Achieved\nObjective evidence: {_EVIDENCE}\n", _VALID_FOOTER),
                ("objective_status_invalid",),
            ),
            (
                "conflated objective value carrying a verdict",
                _report(
                    f"Objective status: pass\nObjective evidence: {_EVIDENCE}\n", _VALID_FOOTER
                ),
                ("objective_status_invalid",),
            ),
            (
                "conflated verdict value carrying an objective",
                _report(_VALID_CLOSURE, f"Verdict: pass/achieved - next: {_MOVE}"),
                ("verdict_footer_invalid",),
            ),
            (
                "route value in the verdict footer",
                _report(_VALID_CLOSURE, f"Verdict: milestone_complete - next: {_MOVE}"),
                ("verdict_footer_invalid",),
            ),
            (
                "empty next move",
                _report(_VALID_CLOSURE, "Verdict: pass - next: "),
                ("verdict_footer_invalid",),
            ),
            (
                "en dash separator",
                _report(_VALID_CLOSURE, f"Verdict: pass – next: {_MOVE}"),
                ("verdict_footer_invalid",),
            ),
            (
                "uppercase verdict value",
                _report(_VALID_CLOSURE, f"Verdict: PASS - next: {_MOVE}"),
                ("verdict_footer_invalid",),
            ),
            (
                "verdict section empty",
                _report(_VALID_CLOSURE, ""),
                ("verdict_footer_invalid",),
            ),
            (
                "closure section empty",
                _report("", _VALID_FOOTER),
                ("closure_line_count", "objective_status_line_missing"),
            ),
            (
                "closure heading quoted in a later fenced example",
                _report(
                    _VALID_CLOSURE,
                    _VALID_FOOTER,
                    after="\n```markdown\n## Closure Decision\n```\n",
                ),
                ("closure_section_duplicate",),
            ),
            (
                "verdict heading quoted in an earlier fenced example",
                _report(
                    _VALID_CLOSURE,
                    _VALID_FOOTER,
                    before="\n```markdown\n## Verdict\n```\n",
                ),
                ("verdict_section_duplicate",),
            ),
            (
                "both headings missing",
                "# Review Report\n\n## Findings\n\n- none\n",
                ("closure_section_missing", "verdict_section_missing"),
            ),
            (
                "closure after verdict and objective line inside verdict",
                "## Verdict\n\nVerdict: pass - next: x\nObjective status: achieved\n\n"
                "## Closure Decision\n\nObjective status: achieved\nObjective evidence: e\n",
                ("closure_after_verdict", "objective_status_in_verdict"),
            ),
            (
                "third-level heading is not the canonical section",
                "### Closure Decision\n\nObjective status: achieved\nObjective evidence: e\n\n"
                "## Verdict\n\nVerdict: pass - next: x\n",
                ("closure_section_missing",),
            ),
            (
                "non-string content",
                b"## Closure Decision\n",
                ("report_unreadable",),
            ),
        ]
        for label, content, codes in cases:
            with self.subTest(case=label):
                parsed = parse_closure_decision_text(content)
                self.assertFalse(parsed.valid)
                self.assertEqual(parsed.reason_codes, codes)
                self.assertIsNone(parsed.verdict)
                self.assertIsNone(parsed.objective_status)
                for code in codes:
                    self.assertIn(code, CLOSURE_REASON_CODES)

    def test_each_valid_combination_parses_to_separate_dimensions(self):
        cases = [
            ("pass achieved", "pass", "achieved"),
            ("pass not_achieved", "pass", "not_achieved"),
            ("pass not_applicable", "pass", "not_applicable"),
            ("pass indeterminate", "pass", "indeterminate"),
            ("needs_work achieved", "needs_work", "achieved"),
            ("needs_work not_achieved", "needs_work", "not_achieved"),
            ("blocked indeterminate", "blocked", "indeterminate"),
            ("blocked not_applicable", "blocked", "not_applicable"),
            ("override achieved", "override", "achieved"),
            ("override not_applicable", "override", "not_applicable"),
        ]
        for label, verdict, status in cases:
            with self.subTest(case=label):
                content = _report(
                    f"Objective status: {status}\nObjective evidence: cited artifact\n",
                    f"Verdict: {verdict} - next: one move",
                )
                parsed = parse_closure_decision_text(content)
                self.assertTrue(parsed.valid)
                self.assertEqual(parsed.reason_codes, ())
                self.assertEqual(parsed.verdict.value, verdict)
                self.assertEqual(parsed.objective_status.value, status)
                self.assertEqual(parsed.objective_evidence, "cited artifact")
                self.assertEqual(parsed.next_move, "one move")

    def test_tolerances_are_exactly_the_checker_tolerances(self):
        """Heading trailing whitespace, no space after the label colon, blank
        lines inside the closure section, and CRLF endings are accepted
        because the released checker accepts them; nothing wider is."""

        content = (
            "# R\r\n\r\n## Closure Decision   \r\n\r\nObjective status:achieved\r\n\r\n"
            "Objective evidence: e\r\n\r\n## Verdict\r\n\r\nVerdict: override - next: m\r\n"
        )
        parsed = parse_closure_decision_text(content)
        self.assertTrue(parsed.valid)
        self.assertEqual(parsed.verdict, ReviewVerdict.OVERRIDE)
        self.assertEqual(parsed.objective_status, ObjectiveStatus.ACHIEVED)
        self.assertEqual(parsed.next_move, "m")

    def test_to_dict_serialises_plain_values(self):
        parsed = parse_closure_decision_text(_report(_VALID_CLOSURE, _VALID_FOOTER))
        self.assertEqual(
            parsed.to_dict(),
            {
                "valid": True,
                "verdict": "pass",
                "objective_status": "not_achieved",
                "objective_evidence": _EVIDENCE,
                "next_move": _MOVE,
                "reason_codes": [],
            },
        )


class FileReadTests(unittest.TestCase):
    def test_missing_directory_and_non_utf8_paths_refuse_unreadable(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            root = Path(tmp)
            bad_bytes = root / "bad.md"
            bad_bytes.write_bytes(b"## Closure Decision\n\xff\xfe\n## Verdict\n")
            cases = [
                ("missing", root / "absent.md"),
                ("directory", root),
                ("non-utf8", bad_bytes),
                ("not a Path", "closure.md"),
            ]
            for label, path in cases:
                with self.subTest(case=label):
                    parsed = parse_closure_decision_file(path)
                    self.assertFalse(parsed.valid)
                    self.assertEqual(parsed.reason_codes, ("report_unreadable",))


class ReceiptSeparationTests(unittest.TestCase):
    def test_every_vocabulary_combination_builds_three_separate_fields(self):
        verdicts = ("pass", "needs_work", "blocked", "override")
        statuses = ("achieved", "not_achieved", "not_applicable", "indeterminate")
        routes = (
            "advance_to_next_slice",
            "milestone_complete",
            "recode_same_slice",
            "unblock_same_slice",
            "human_override_required",
            "invalid",
        )
        seen = 0
        for verdict in verdicts:
            for status in statuses:
                for route in routes:
                    with self.subTest(verdict=verdict, status=status, route=route):
                        built = build_closure_receipt(verdict, status, route)
                        self.assertTrue(built.valid, built.reason)
                        self.assertEqual(
                            built.receipt.to_dict(),
                            {"verdict": verdict, "objective_status": status, "route": route},
                        )
                        seen += 1
        self.assertEqual(seen, 96)

    def test_enum_members_are_admitted_as_their_own_dimension(self):
        built = build_closure_receipt(
            ReviewVerdict.OVERRIDE, ObjectiveStatus.ACHIEVED, FrutlupsRoute.MILESTONE_COMPLETE
        )
        self.assertEqual(
            built.receipt,
            ClosureReceipt(
                ReviewVerdict.OVERRIDE, ObjectiveStatus.ACHIEVED, FrutlupsRoute.MILESTONE_COMPLETE
            ),
        )
        self.assertEqual(built.reason, "")

    def test_conflated_and_out_of_vocabulary_values_refuse(self):
        cases = [
            ("objective in verdict field", ("achieved", "achieved", "invalid"), "verdict_conflated_with_objective_status"),
            ("route in verdict field", ("milestone_complete", "achieved", "invalid"), "verdict_conflated_with_route"),
            ("verdict in objective field", ("pass", "pass", "invalid"), "objective_status_conflated_with_verdict"),
            ("route in objective field", ("pass", "invalid", "invalid"), "objective_status_conflated_with_route"),
            ("verdict in route field", ("pass", "achieved", "pass"), "route_conflated_with_verdict"),
            ("objective in route field", ("pass", "achieved", "achieved"), "route_conflated_with_objective_status"),
            ("verdict enum in objective field", ("pass", ReviewVerdict.PASS, "invalid"), "objective_status_conflated_with_verdict"),
            ("combined verdict and objective", ("pass/achieved", "achieved", "invalid"), "verdict_invalid"),
            ("combined objective and route", ("pass", "achieved milestone_complete", "invalid"), "objective_status_invalid"),
            ("footer text as verdict", ("pass - next: go", "achieved", "invalid"), "verdict_invalid"),
            ("uppercase verdict", ("PASS", "achieved", "invalid"), "verdict_invalid"),
            ("padded objective", ("pass", " achieved", "invalid"), "objective_status_invalid"),
            ("unknown route", ("pass", "achieved", "advance"), "route_invalid"),
            ("None route", ("pass", "achieved", None), "route_invalid"),
            ("integer verdict", (1, "achieved", "invalid"), "verdict_invalid"),
            ("empty objective", ("pass", "", "invalid"), "objective_status_invalid"),
        ]
        for label, (verdict, status, route), reason in cases:
            with self.subTest(case=label):
                built = build_closure_receipt(verdict, status, route)
                self.assertIsNone(built.receipt)
                self.assertFalse(built.valid)
                self.assertEqual(built.reason, reason)


if __name__ == "__main__":
    unittest.main()
