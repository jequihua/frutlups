"""Tests for slice parsing and next-actionable-slice inference."""

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from frutlups.cli import main
from frutlups.project import (
    _find_accepted_slice_ids,
    _has_pass_verdict,
    build_status,
)
from frutlups.state import (
    RoadmapSlice,
    next_actionable_slice,
    parse_slices,
)


class ParseSlicesTests(unittest.TestCase):
    def test_parses_slice_bullets_under_milestone(self) -> None:
        path = self._write_roadmap(
            "### M002: Roadmap Parser and Slice Model\n\n"
            "Slices:\n\n"
            "- M002-S01: parse milestone headings, status lines, and done criteria\n"
            "- M002-S02: represent statuses\n"
            "- M002-S03: infer the next actionable slice\n"
            "- M002-S04: diagnostics for missing or ambiguous roadmap state\n\n"
            "Acceptance:\n\n"
            "- trailing prose\n"
        )

        slices = parse_slices(path)

        self.assertEqual(
            [s.slice_id for s in slices],
            ["M002-S01", "M002-S02", "M002-S03", "M002-S04"],
        )
        self.assertTrue(all(s.milestone_id == "M002" for s in slices))
        self.assertEqual(
            slices[0].title,
            "parse milestone headings, status lines, and done criteria",
        )

    def test_joins_wrapped_slice_titles(self) -> None:
        path = self._write_roadmap(
            "### M002: X\n\n"
            "Slices:\n\n"
            "- M002-S02: represent `planned`, `active`, `completed`, `blocked`, and\n"
            "  `needs_review`\n"
            "- M002-S03: next slice\n"
        )

        slices = parse_slices(path)

        self.assertEqual(slices[0].slice_id, "M002-S02")
        self.assertEqual(
            slices[0].title,
            "represent `planned`, `active`, `completed`, `blocked`, and `needs_review`",
        )
        self.assertEqual(slices[1].slice_id, "M002-S03")

    def test_attaches_slices_to_their_parent_milestone(self) -> None:
        path = self._write_roadmap(
            "### M001: First\n\n"
            "Slices:\n\n"
            "- M001-S01: a\n"
            "- M001-S02: b\n\n"
            "### M002: Second\n\n"
            "Slices:\n\n"
            "- M002-S01: c\n"
        )

        slices = parse_slices(path)

        m1 = [s for s in slices if s.milestone_id == "M001"]
        m2 = [s for s in slices if s.milestone_id == "M002"]
        self.assertEqual([s.slice_id for s in m1], ["M001-S01", "M001-S02"])
        self.assertEqual([s.slice_id for s in m2], ["M002-S01"])

    def test_milestone_with_no_slices_section_yields_none(self) -> None:
        path = self._write_roadmap(
            "### M001: Bare\n\nGoal: nothing here.\n\n"
            "### M002: Has slices\n\nSlices:\n\n- M002-S01: real\n"
        )

        slices = parse_slices(path)

        self.assertEqual([s.slice_id for s in slices], ["M002-S01"])

    def test_ignores_non_slice_bullets_inside_slices_section(self) -> None:
        path = self._write_roadmap(
            "### M002: X\n\n"
            "Slices:\n\n"
            "- not a slice id\n"
            "- M002-S01: a real slice\n"
            "- another non-slice bullet\n"
            "- M002-S02: another real slice\n"
        )

        slices = parse_slices(path)

        self.assertEqual([s.slice_id for s in slices], ["M002-S01", "M002-S02"])

    def test_next_milestone_heading_terminates_slices_section(self) -> None:
        path = self._write_roadmap(
            "### M001: First\n\n"
            "Slices:\n\n"
            "- M001-S01: a\n"
            "### M002: Second\n\n"
            "Slices:\n\n"
            "- M002-S01: b\n"
        )

        slices = parse_slices(path)

        self.assertEqual(len(slices), 2)
        self.assertEqual(slices[0].milestone_id, "M001")
        self.assertEqual(slices[1].milestone_id, "M002")

    def test_to_dict_exposes_id_milestone_and_title(self) -> None:
        slc = RoadmapSlice(
            slice_id="M002-S03",
            milestone_id="M002",
            title="infer",
        )

        self.assertEqual(
            slc.to_dict(),
            {"id": "M002-S03", "milestone_id": "M002", "title": "infer"},
        )

    def _write_roadmap(self, body: str) -> Path:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        path = Path(self._tmp.name) / "detailed.md"
        path.write_text(body, encoding="utf-8")
        return path


class NextActionableSliceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.slices = (
            RoadmapSlice("M001-S01", "M001", "scaffold"),
            RoadmapSlice("M002-S01", "M002", "parse"),
            RoadmapSlice("M002-S02", "M002", "statuses"),
            RoadmapSlice("M002-S03", "M002", "infer"),
            RoadmapSlice("M003-S01", "M003", "generate"),
        )

    def test_returns_first_slice_for_milestone_when_none_accepted(self) -> None:
        result = next_actionable_slice(self.slices, "M002", ())
        self.assertIsNotNone(result)
        self.assertEqual(result.slice_id, "M002-S01")

    def test_skips_accepted_slices_in_order(self) -> None:
        result = next_actionable_slice(self.slices, "M002", ("M002-S01", "M002-S02"))
        self.assertIsNotNone(result)
        self.assertEqual(result.slice_id, "M002-S03")

    def test_returns_none_when_all_milestone_slices_accepted(self) -> None:
        result = next_actionable_slice(
            self.slices, "M002", ("M002-S01", "M002-S02", "M002-S03")
        )
        self.assertIsNone(result)

    def test_filters_strictly_by_milestone_id(self) -> None:
        result = next_actionable_slice(self.slices, "M003", ())
        self.assertIsNotNone(result)
        self.assertEqual(result.slice_id, "M003-S01")

    def test_accepted_match_is_case_insensitive(self) -> None:
        result = next_actionable_slice(
            self.slices, "M002", ("m002-s01", "M002-s02")
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.slice_id, "M002-S03")

    def test_unknown_milestone_returns_none(self) -> None:
        self.assertIsNone(next_actionable_slice(self.slices, "M999", ()))

    def test_milestone_id_argument_is_case_insensitive(self) -> None:
        # Regression: prior implementation compared milestone IDs by exact
        # string equality, so a lowercase milestone_id returned None even
        # when matching slices existed. See M002-S03 review report.
        for variant in ("m002", "M002", "m002".upper(), "M002".lower()):
            with self.subTest(milestone_id=variant):
                result = next_actionable_slice(self.slices, variant, ())
                self.assertIsNotNone(result)
                self.assertEqual(result.slice_id, "M002-S01")

    def test_milestone_id_argument_case_insensitive_with_accepted(self) -> None:
        result = next_actionable_slice(
            self.slices, "m002", ("m002-s01",)
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.slice_id, "M002-S02")

    def test_lowercase_candidate_milestone_id_still_matches(self) -> None:
        mixed_slices = (
            RoadmapSlice("m002-s01", "m002", "lowercase"),
            RoadmapSlice("M002-S02", "M002", "uppercase"),
        )
        result = next_actionable_slice(mixed_slices, "M002", ())
        self.assertIsNotNone(result)
        self.assertEqual(result.slice_id, "m002-s01")


class VerdictDetectionTests(unittest.TestCase):
    def test_has_pass_verdict_true_for_pass_token(self) -> None:
        path = self._write_report(
            "# Some Review\n\n## Verdict\n\npass\n\nMore notes.\n"
        )
        self.assertTrue(_has_pass_verdict(path))

    def test_has_pass_verdict_case_insensitive(self) -> None:
        path = self._write_report("## Verdict\n\nPASS\n")
        self.assertTrue(_has_pass_verdict(path))

    def test_has_pass_verdict_false_for_needs_work(self) -> None:
        path = self._write_report("## Verdict\n\nneeds_work\n")
        self.assertFalse(_has_pass_verdict(path))

    def test_has_pass_verdict_false_for_blocked(self) -> None:
        path = self._write_report("## Verdict\n\nblocked\n")
        self.assertFalse(_has_pass_verdict(path))

    def test_has_pass_verdict_false_when_no_verdict_section(self) -> None:
        path = self._write_report("# Notes\n\nNo verdict here.\n")
        self.assertFalse(_has_pass_verdict(path))

    def test_has_pass_verdict_true_for_backticked_pass(self) -> None:
        # Regression: review reports commonly write the verdict as inline code,
        # e.g. "`pass`". Accepted-slice detection must honor the same syntax as
        # record-verdict (the canonical parser), not only a bare "pass".
        path = self._write_report("# Some Review\n\n## Verdict\n\n`pass`\n")
        self.assertTrue(_has_pass_verdict(path))

    def test_has_pass_verdict_true_for_bulleted_backticked_pass(self) -> None:
        path = self._write_report("## Verdict\n\n- `pass`\n")
        self.assertTrue(_has_pass_verdict(path))

    def test_has_pass_verdict_false_for_backticked_needs_work(self) -> None:
        path = self._write_report("## Verdict\n\n`needs_work`\n")
        self.assertFalse(_has_pass_verdict(path))

    def test_has_pass_verdict_false_for_override(self) -> None:
        path = self._write_report("## Verdict\n\n`override`\n")
        self.assertFalse(_has_pass_verdict(path))

    def test_find_accepted_slice_ids_accepts_backticked_pass(self) -> None:
        # Frontier-level regression for the backticked-pass acceptance bug.
        with TemporaryDirectory() as tmp:
            reviews = Path(tmp)
            (reviews / "m002_s01_thing_review_report.md").write_text(
                "## Verdict\n\n`pass`\n", encoding="utf-8"
            )
            (reviews / "m002_s02_thing_review_report.md").write_text(
                "## Verdict\n\n- `pass`\n", encoding="utf-8"
            )
            (reviews / "m002_s03_thing_review_report.md").write_text(
                "## Verdict\n\n`needs_work`\n", encoding="utf-8"
            )

            accepted = _find_accepted_slice_ids(reviews)

        self.assertEqual(accepted, ("M002-S01", "M002-S02"))

    def test_find_accepted_slice_ids_reads_filenames_and_verdicts(self) -> None:
        with TemporaryDirectory() as tmp:
            reviews = Path(tmp)
            (reviews / "m002_s01_roadmap_parser_review_report.md").write_text(
                "## Verdict\n\npass\n", encoding="utf-8"
            )
            (reviews / "m002_s02_status_model_review_report.md").write_text(
                "## Verdict\n\npass\n", encoding="utf-8"
            )
            (reviews / "m002_s03_thing_review_report.md").write_text(
                "## Verdict\n\nneeds_work\n", encoding="utf-8"
            )
            (reviews / "m001_scaffold_review_report.md").write_text(
                "## Verdict\n\npass\n", encoding="utf-8"
            )
            (reviews / "random_notes.md").write_text("nothing", encoding="utf-8")

            accepted = _find_accepted_slice_ids(reviews)

        self.assertEqual(accepted, ("M002-S01", "M002-S02"))

    def test_find_accepted_slice_ids_missing_dir_returns_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does_not_exist"
            self.assertEqual(_find_accepted_slice_ids(missing), ())

    def _write_report(self, body: str) -> Path:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        path = Path(self._tmp.name) / "report.md"
        path.write_text(body, encoding="utf-8")
        return path


class IntegrationStatusTests(unittest.TestCase):
    def test_build_status_infers_first_slice_when_no_reviews_present(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
                "### M002: Active One\n\nStatus: active\n", encoding="utf-8"
            )
            (root / "03_experiments" / "development_roadmap_frutlups.md").write_text(
                "### M002: Active One\n\nSlices:\n\n"
                "- M002-S01: first\n"
                "- M002-S02: second\n",
                encoding="utf-8",
            )

            status = build_status(root)

        self.assertIsNotNone(status.next_slice)
        self.assertEqual(status.next_slice.slice_id, "M002-S01")
        self.assertEqual(status.accepted_slice_ids, ())
        self.assertEqual(len(status.slices), 2)
        self.assertEqual(
            status.detailed_roadmap,
            root / "03_experiments" / "development_roadmap_frutlups.md",
        )

    def test_build_status_skips_slices_with_accepted_review_reports(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
                "### M002: Active One\n\nStatus: active\n", encoding="utf-8"
            )
            (root / "03_experiments" / "development_roadmap_frutlups.md").write_text(
                "### M002: Active One\n\nSlices:\n\n"
                "- M002-S01: first\n"
                "- M002-S02: second\n"
                "- M002-S03: third\n",
                encoding="utf-8",
            )
            reviews = root / "05_governance" / "reviews"
            reviews.mkdir(parents=True, exist_ok=True)
            (reviews / "m002_s01_first_review_report.md").write_text(
                "## Verdict\n\npass\n", encoding="utf-8"
            )
            (reviews / "m002_s02_second_review_report.md").write_text(
                "## Verdict\n\npass\n", encoding="utf-8"
            )

            status = build_status(root)

        self.assertIsNotNone(status.next_slice)
        self.assertEqual(status.next_slice.slice_id, "M002-S03")
        self.assertEqual(status.accepted_slice_ids, ("M002-S01", "M002-S02"))

    def test_build_status_skips_slices_with_backticked_pass_reports(self) -> None:
        # Regression: a slice whose review report verdict is "`pass`" must be
        # treated as accepted, so the frontier advances and the loop does not
        # re-propose an already accepted slice.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
                "### M002: Active One\n\nStatus: active\n", encoding="utf-8"
            )
            (root / "03_experiments" / "development_roadmap_frutlups.md").write_text(
                "### M002: Active One\n\nSlices:\n\n"
                "- M002-S01: first\n"
                "- M002-S02: second\n",
                encoding="utf-8",
            )
            reviews = root / "05_governance" / "reviews"
            reviews.mkdir(parents=True, exist_ok=True)
            (reviews / "m002_s01_first_review_report.md").write_text(
                "## Verdict\n\n`pass`\n", encoding="utf-8"
            )

            status = build_status(root)

        self.assertIn("M002-S01", status.accepted_slice_ids)
        self.assertIsNotNone(status.next_slice)
        self.assertEqual(status.next_slice.slice_id, "M002-S02")

    def test_build_status_next_slice_none_when_no_detailed_roadmap(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
                "### M002: Active One\n\nStatus: active\n", encoding="utf-8"
            )

            status = build_status(root)

        self.assertIsNone(status.next_slice)
        self.assertIsNone(status.detailed_roadmap)
        self.assertEqual(status.slices, ())

    def test_build_status_next_slice_none_when_milestone_has_no_slices(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
                "### M002: Active One\n\nStatus: active\n", encoding="utf-8"
            )
            (root / "03_experiments" / "development_roadmap_frutlups.md").write_text(
                "### M003: Other\n\nSlices:\n\n- M003-S01: x\n", encoding="utf-8"
            )

            status = build_status(root)

        self.assertIsNone(status.next_slice)
        self.assertEqual(len(status.slices), 1)


class CliNextSliceJsonTests(unittest.TestCase):
    def test_status_json_exposes_next_slice(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
                "### M002: Active\n\nStatus: active\n", encoding="utf-8"
            )
            (root / "03_experiments" / "development_roadmap_frutlups.md").write_text(
                "### M002: Active\n\nSlices:\n\n"
                "- M002-S01: first\n"
                "- M002-S02: second\n",
                encoding="utf-8",
            )
            reviews = root / "05_governance" / "reviews"
            reviews.mkdir(parents=True, exist_ok=True)
            (reviews / "m002_s01_first_review_report.md").write_text(
                "## Verdict\n\npass\n", encoding="utf-8"
            )
            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["status", str(root), "--json"])

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["next_milestone"]["id"], "M002")
        self.assertEqual(payload["next_slice"]["id"], "M002-S02")
        self.assertEqual(payload["next_slice"]["milestone_id"], "M002")
        self.assertEqual(payload["accepted_slice_ids"], ["M002-S01"])


def _make_template(root: Path) -> None:
    for name in (
        "00_brief",
        "03_experiments",
        "05_governance",
        "06_infra",
        "08_pkg",
        "prompts/for_coding_agent",
        "prompts/for_review_agent",
    ):
        (root / name).mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    unittest.main()
