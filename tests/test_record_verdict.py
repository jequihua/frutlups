"""Tests for M008-S04: frutlups record-verdict command."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from frutlups.cli import main
from frutlups.project import (
    VerdictRecordPlan,
    VerdictRecordWriteCommand,
    VerdictRecordWriteResult,
    build_verdict_record_plan,
    write_verdict_record,
)
from frutlups.review_report import ReviewVerdict
from frutlups.state import NextActionKind


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_template(root: Path) -> None:
    for name in (
        "00_brief",
        "03_experiments",
        "05_governance/reviews",
        "06_infra",
        "08_pkg",
        "prompts/for_coding_agent",
        "prompts/for_review_agent",
    ):
        (root / name).mkdir(parents=True, exist_ok=True)


def _write_detailed_roadmap(root: Path, content: str) -> None:
    (root / "03_experiments" / "development_roadmap_frutlups.md").write_text(
        content, encoding="utf-8"
    )


def _write_review_report(root: Path, filename: str, verdict: str = "pass") -> None:
    (root / "05_governance" / "reviews" / filename).write_text(
        f"# Review\n\n## Verdict\n\n{verdict}\n", encoding="utf-8"
    )


def _milestone_block(mid: str, title: str, slices: list[tuple[str, str]]) -> str:
    lines = [f"### {mid}: {title}\n\nSlices:\n"]
    for sid, stitle in slices:
        lines.append(f"- {sid}: {stitle}\n")
    lines.append("\n")
    return "".join(lines)


def _run(
    args: list[str],
    *,
    capture_stderr: bool = False,
) -> tuple[int, str, str]:
    out_buf = StringIO()
    err_buf = StringIO()
    with redirect_stdout(out_buf), redirect_stderr(err_buf):
        code = main(args)
    return code, out_buf.getvalue(), err_buf.getvalue()


# ---------------------------------------------------------------------------
# CLI help
# ---------------------------------------------------------------------------

class CliHelpTests(unittest.TestCase):
    def test_record_verdict_in_main_help(self) -> None:
        buf = StringIO()
        with redirect_stdout(buf):
            try:
                main(["--help"])
            except SystemExit:
                pass
        self.assertIn("record-verdict", buf.getvalue())

    def test_record_verdict_subcommand_help(self) -> None:
        buf = StringIO()
        with redirect_stdout(buf):
            try:
                main(["record-verdict", "--help"])
            except SystemExit:
                pass
        out = buf.getvalue()
        self.assertIn("--review-report", out)
        self.assertIn("--json", out)
        self.assertIn("--dry-run", out)
        self.assertIn("--overwrite", out)


# ---------------------------------------------------------------------------
# Happy path: pass → advance_to_next_slice
# ---------------------------------------------------------------------------

class PassVerdictAdvanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_detailed_roadmap(
            self.root,
            _milestone_block(
                "M001", "Test milestone",
                [("M001-S01", "first slice"), ("M001-S02", "second slice")],
            ),
        )
        _write_review_report(self.root, "m001_s01_first_slice_review_report.md", "pass")

    def tearDown(self) -> None:
        self._td.cleanup()

    def _plan(self) -> VerdictRecordPlan:
        rr = self.root / "05_governance" / "reviews" / "m001_s01_first_slice_review_report.md"
        return build_verdict_record_plan(self.root, rr)

    def test_plan_valid(self) -> None:
        self.assertTrue(self._plan().valid)

    def test_slice_id(self) -> None:
        self.assertEqual(self._plan().reviewed_slice.slice_id, "M001-S01")

    def test_verdict_pass(self) -> None:
        self.assertEqual(self._plan().parse_result.verdict, ReviewVerdict.PASS)

    def test_next_action_advance(self) -> None:
        self.assertEqual(
            self._plan().next_action.kind, NextActionKind.ADVANCE_TO_NEXT_SLICE
        )

    def test_next_slice_id(self) -> None:
        self.assertEqual(self._plan().next_action.next_slice_id, "M001-S02")

    def test_target_path_convention(self) -> None:
        plan = self._plan()
        self.assertEqual(
            plan.target_path,
            "05_governance/reviews/m001_s01_first_slice_verdict_record.md",
        )

    def test_no_errors(self) -> None:
        self.assertEqual(self._plan().errors, ())


# ---------------------------------------------------------------------------
# Happy path: final-slice pass → milestone_complete
# ---------------------------------------------------------------------------

class PassFinalSliceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_detailed_roadmap(
            self.root,
            _milestone_block("M001", "Test", [("M001-S01", "only slice")]),
        )
        _write_review_report(self.root, "m001_s01_only_slice_review_report.md", "pass")

    def tearDown(self) -> None:
        self._td.cleanup()

    def _plan(self) -> VerdictRecordPlan:
        rr = self.root / "05_governance" / "reviews" / "m001_s01_only_slice_review_report.md"
        return build_verdict_record_plan(self.root, rr)

    def test_plan_valid(self) -> None:
        self.assertTrue(self._plan().valid)

    def test_next_action_milestone_complete(self) -> None:
        self.assertEqual(
            self._plan().next_action.kind, NextActionKind.MILESTONE_COMPLETE
        )

    def test_next_slice_id_none(self) -> None:
        self.assertFalse(self._plan().next_action.next_slice_id)


# ---------------------------------------------------------------------------
# needs_work → recode_same_slice
# ---------------------------------------------------------------------------

class NeedsWorkVerdictTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_detailed_roadmap(
            self.root,
            _milestone_block(
                "M001", "Test",
                [("M001-S01", "first slice"), ("M001-S02", "second slice")],
            ),
        )
        _write_review_report(self.root, "m001_s01_first_slice_review_report.md", "needs_work")

    def tearDown(self) -> None:
        self._td.cleanup()

    def _plan(self) -> VerdictRecordPlan:
        rr = self.root / "05_governance" / "reviews" / "m001_s01_first_slice_review_report.md"
        return build_verdict_record_plan(self.root, rr)

    def test_plan_valid(self) -> None:
        self.assertTrue(self._plan().valid)

    def test_next_action_recode(self) -> None:
        self.assertEqual(
            self._plan().next_action.kind, NextActionKind.RECODE_SAME_SLICE
        )


# ---------------------------------------------------------------------------
# blocked → unblock_same_slice
# ---------------------------------------------------------------------------

class BlockedVerdictTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_detailed_roadmap(
            self.root,
            _milestone_block(
                "M001", "Test",
                [("M001-S01", "first slice"), ("M001-S02", "second slice")],
            ),
        )
        _write_review_report(self.root, "m001_s01_first_slice_review_report.md", "blocked")

    def tearDown(self) -> None:
        self._td.cleanup()

    def _plan(self) -> VerdictRecordPlan:
        rr = self.root / "05_governance" / "reviews" / "m001_s01_first_slice_review_report.md"
        return build_verdict_record_plan(self.root, rr)

    def test_plan_valid(self) -> None:
        self.assertTrue(self._plan().valid)

    def test_next_action_unblock(self) -> None:
        self.assertEqual(
            self._plan().next_action.kind, NextActionKind.UNBLOCK_SAME_SLICE
        )


# ---------------------------------------------------------------------------
# override → human_override_required (no auto-advance)
# ---------------------------------------------------------------------------

class OverrideVerdictTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_detailed_roadmap(
            self.root,
            _milestone_block(
                "M001", "Test",
                [("M001-S01", "first slice"), ("M001-S02", "second slice")],
            ),
        )
        _write_review_report(self.root, "m001_s01_first_slice_review_report.md", "override")

    def tearDown(self) -> None:
        self._td.cleanup()

    def _plan(self) -> VerdictRecordPlan:
        rr = self.root / "05_governance" / "reviews" / "m001_s01_first_slice_review_report.md"
        return build_verdict_record_plan(self.root, rr)

    def test_plan_valid(self) -> None:
        self.assertTrue(self._plan().valid)

    def test_next_action_human_override(self) -> None:
        self.assertEqual(
            self._plan().next_action.kind, NextActionKind.HUMAN_OVERRIDE_REQUIRED
        )

    def test_no_auto_advance(self) -> None:
        plan = self._plan()
        self.assertNotEqual(
            plan.next_action.kind, NextActionKind.ADVANCE_TO_NEXT_SLICE
        )


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

class MissingReportFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_detailed_roadmap(
            self.root,
            _milestone_block("M001", "Test", [("M001-S01", "first slice")]),
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_plan_invalid(self) -> None:
        rr = self.root / "05_governance" / "reviews" / "m001_s01_first_slice_review_report.md"
        plan = build_verdict_record_plan(self.root, rr)
        self.assertFalse(plan.valid)

    def test_error_message(self) -> None:
        rr = self.root / "05_governance" / "reviews" / "m001_s01_first_slice_review_report.md"
        plan = build_verdict_record_plan(self.root, rr)
        self.assertTrue(any("not found" in e or "missing" in e.lower() or "No such" in e or "does not exist" in e.lower() for e in plan.errors), plan.errors)


class MalformedVerdictTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_detailed_roadmap(
            self.root,
            _milestone_block("M001", "Test", [("M001-S01", "first slice")]),
        )
        (self.root / "05_governance" / "reviews" / "m001_s01_first_slice_review_report.md").write_text(
            "# Review\n\n## Verdict\n\nGARBAGE_VERDICT\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_plan_invalid(self) -> None:
        rr = self.root / "05_governance" / "reviews" / "m001_s01_first_slice_review_report.md"
        plan = build_verdict_record_plan(self.root, rr)
        self.assertFalse(plan.valid)

    def test_has_errors(self) -> None:
        rr = self.root / "05_governance" / "reviews" / "m001_s01_first_slice_review_report.md"
        plan = build_verdict_record_plan(self.root, rr)
        self.assertTrue(plan.errors)


class UnparseableFilenameTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_detailed_roadmap(
            self.root,
            _milestone_block("M001", "Test", [("M001-S01", "first slice")]),
        )
        (self.root / "05_governance" / "reviews" / "bad_name_review_report.md").write_text(
            "# Review\n\n## Verdict\n\npass\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_plan_invalid(self) -> None:
        rr = self.root / "05_governance" / "reviews" / "bad_name_review_report.md"
        plan = build_verdict_record_plan(self.root, rr)
        self.assertFalse(plan.valid)

    def test_error_mentions_filename(self) -> None:
        rr = self.root / "05_governance" / "reviews" / "bad_name_review_report.md"
        plan = build_verdict_record_plan(self.root, rr)
        self.assertTrue(any("bad_name" in e or "slice ID" in e or "filename" in e for e in plan.errors), plan.errors)


class SliceNotFoundTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_detailed_roadmap(
            self.root,
            _milestone_block("M002", "Other milestone", [("M002-S01", "other slice")]),
        )
        _write_review_report(self.root, "m001_s01_first_slice_review_report.md", "pass")

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_plan_invalid(self) -> None:
        rr = self.root / "05_governance" / "reviews" / "m001_s01_first_slice_review_report.md"
        plan = build_verdict_record_plan(self.root, rr)
        self.assertFalse(plan.valid)

    def test_error_mentions_slice(self) -> None:
        rr = self.root / "05_governance" / "reviews" / "m001_s01_first_slice_review_report.md"
        plan = build_verdict_record_plan(self.root, rr)
        self.assertTrue(any("M001-S01" in e or "not found" in e for e in plan.errors), plan.errors)


class WrongFilenameSuffixTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_detailed_roadmap(
            self.root,
            _milestone_block("M001", "Test", [("M001-S01", "first slice")]),
        )
        (self.root / "05_governance" / "reviews" / "m001_s01_first_slice.md").write_text(
            "# Review\n\n## Verdict\n\npass\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_plan_invalid_bad_suffix(self) -> None:
        rr = self.root / "05_governance" / "reviews" / "m001_s01_first_slice.md"
        plan = build_verdict_record_plan(self.root, rr)
        self.assertFalse(plan.valid)

    def test_error_mentions_convention(self) -> None:
        rr = self.root / "05_governance" / "reviews" / "m001_s01_first_slice.md"
        plan = build_verdict_record_plan(self.root, rr)
        self.assertTrue(any("convention" in e or "_review_report.md" in e for e in plan.errors), plan.errors)


# ---------------------------------------------------------------------------
# Dry-run: does not write sidecar
# ---------------------------------------------------------------------------

class DryRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_detailed_roadmap(
            self.root,
            _milestone_block(
                "M001", "Test",
                [("M001-S01", "first slice"), ("M001-S02", "second slice")],
            ),
        )
        _write_review_report(self.root, "m001_s01_first_slice_review_report.md", "pass")
        self.rr = str(
            self.root / "05_governance" / "reviews" / "m001_s01_first_slice_review_report.md"
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_dry_run_no_file_written(self) -> None:
        code, out, _ = _run(
            ["record-verdict", str(self.root),
             "--review-report", self.rr, "--dry-run"]
        )
        self.assertEqual(code, 0)
        target = self.root / "05_governance" / "reviews" / "m001_s01_first_slice_verdict_record.md"
        self.assertFalse(target.exists())

    def test_dry_run_human_output_contains_would_write(self) -> None:
        _, out, _ = _run(
            ["record-verdict", str(self.root),
             "--review-report", self.rr, "--dry-run"]
        )
        self.assertIn("dry-run", out.lower())

    def test_dry_run_json_no_write_result(self) -> None:
        code, out, _ = _run(
            ["record-verdict", str(self.root),
             "--review-report", self.rr, "--dry-run", "--json"]
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertNotIn("write_result", data)

    def test_dry_run_json_valid(self) -> None:
        _, out, _ = _run(
            ["record-verdict", str(self.root),
             "--review-report", self.rr, "--dry-run", "--json"]
        )
        data = json.loads(out)
        self.assertTrue(data["valid"])


# ---------------------------------------------------------------------------
# Write: creates sidecar
# ---------------------------------------------------------------------------

class WriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_detailed_roadmap(
            self.root,
            _milestone_block(
                "M001", "Test",
                [("M001-S01", "first slice"), ("M001-S02", "second slice")],
            ),
        )
        _write_review_report(self.root, "m001_s01_first_slice_review_report.md", "pass")
        self.rr = self.root / "05_governance" / "reviews" / "m001_s01_first_slice_review_report.md"
        self.target = self.root / "05_governance" / "reviews" / "m001_s01_first_slice_verdict_record.md"

    def tearDown(self) -> None:
        self._td.cleanup()

    def _plan(self) -> VerdictRecordPlan:
        return build_verdict_record_plan(self.root, self.rr)

    def test_write_creates_file(self) -> None:
        plan = self._plan()
        result = write_verdict_record(VerdictRecordWriteCommand(
            project_root=self.root, plan=plan, overwrite=False
        ))
        self.assertTrue(result.wrote)
        self.assertTrue(self.target.exists())

    def test_written_file_contains_slice_id(self) -> None:
        plan = self._plan()
        write_verdict_record(VerdictRecordWriteCommand(
            project_root=self.root, plan=plan, overwrite=False
        ))
        content = self.target.read_text(encoding="utf-8")
        self.assertIn("M001-S01", content)

    def test_written_file_contains_verdict(self) -> None:
        plan = self._plan()
        write_verdict_record(VerdictRecordWriteCommand(
            project_root=self.root, plan=plan, overwrite=False
        ))
        content = self.target.read_text(encoding="utf-8")
        self.assertIn("pass", content)

    def test_written_file_contains_no_mutation_note(self) -> None:
        plan = self._plan()
        write_verdict_record(VerdictRecordWriteCommand(
            project_root=self.root, plan=plan, overwrite=False
        ))
        content = self.target.read_text(encoding="utf-8")
        self.assertIn("No roadmap mutation", content)

    def test_write_result_target_path(self) -> None:
        plan = self._plan()
        result = write_verdict_record(VerdictRecordWriteCommand(
            project_root=self.root, plan=plan, overwrite=False
        ))
        self.assertIn("m001_s01_first_slice_verdict_record.md", result.target_path)


# ---------------------------------------------------------------------------
# Overwrite behavior
# ---------------------------------------------------------------------------

class OverwriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_detailed_roadmap(
            self.root,
            _milestone_block(
                "M001", "Test",
                [("M001-S01", "first slice"), ("M001-S02", "second slice")],
            ),
        )
        _write_review_report(self.root, "m001_s01_first_slice_review_report.md", "pass")
        self.rr = self.root / "05_governance" / "reviews" / "m001_s01_first_slice_review_report.md"
        self.target = self.root / "05_governance" / "reviews" / "m001_s01_first_slice_verdict_record.md"
        self.target.write_text("old content", encoding="utf-8")

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_plan_invalid_without_overwrite(self) -> None:
        plan = build_verdict_record_plan(self.root, self.rr, overwrite=False)
        self.assertFalse(plan.valid)

    def test_plan_error_mentions_overwrite(self) -> None:
        plan = build_verdict_record_plan(self.root, self.rr, overwrite=False)
        self.assertTrue(any("overwrite" in e.lower() for e in plan.errors), plan.errors)

    def test_overwrite_flag_makes_plan_valid(self) -> None:
        plan = build_verdict_record_plan(self.root, self.rr, overwrite=True)
        self.assertTrue(plan.valid)

    def test_overwrite_replaces_file(self) -> None:
        plan = build_verdict_record_plan(self.root, self.rr, overwrite=True)
        result = write_verdict_record(VerdictRecordWriteCommand(
            project_root=self.root, plan=plan, overwrite=True
        ))
        self.assertTrue(result.wrote)
        self.assertTrue(result.overwrote)
        content = self.target.read_text(encoding="utf-8")
        self.assertNotEqual(content, "old content")

    def test_write_without_overwrite_flag_fails_on_existing(self) -> None:
        plan = build_verdict_record_plan(self.root, self.rr, overwrite=True)
        result = write_verdict_record(VerdictRecordWriteCommand(
            project_root=self.root, plan=plan, overwrite=False
        ))
        self.assertFalse(result.wrote)


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------

class JsonOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_detailed_roadmap(
            self.root,
            _milestone_block(
                "M001", "Test",
                [("M001-S01", "first slice"), ("M001-S02", "second slice")],
            ),
        )
        _write_review_report(self.root, "m001_s01_first_slice_review_report.md", "pass")
        self.rr = str(
            self.root / "05_governance" / "reviews" / "m001_s01_first_slice_review_report.md"
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_json_output_parses(self) -> None:
        _, out, _ = _run(
            ["record-verdict", str(self.root),
             "--review-report", self.rr, "--json"]
        )
        data = json.loads(out)
        self.assertIsInstance(data, dict)

    def test_json_contains_parse_result(self) -> None:
        _, out, _ = _run(
            ["record-verdict", str(self.root),
             "--review-report", self.rr, "--json"]
        )
        data = json.loads(out)
        self.assertIn("parse_result", data)

    def test_json_contains_next_action(self) -> None:
        _, out, _ = _run(
            ["record-verdict", str(self.root),
             "--review-report", self.rr, "--json"]
        )
        data = json.loads(out)
        self.assertIn("next_action", data)

    def test_json_contains_target_path(self) -> None:
        _, out, _ = _run(
            ["record-verdict", str(self.root),
             "--review-report", self.rr, "--json"]
        )
        data = json.loads(out)
        self.assertIn("target_path", data)

    def test_json_contains_write_result(self) -> None:
        _, out, _ = _run(
            ["record-verdict", str(self.root),
             "--review-report", self.rr, "--json"]
        )
        data = json.loads(out)
        self.assertIn("write_result", data)

    def test_json_only_plain_values(self) -> None:
        _, out, _ = _run(
            ["record-verdict", str(self.root),
             "--review-report", self.rr, "--json"]
        )
        # json.loads succeeds means all values are JSON-serializable.
        # Verify no unexpected types leaked through.
        text = out
        self.assertNotIn("Path(", text)
        self.assertNotIn("ReviewVerdict", text)

    def test_json_verdict_is_string(self) -> None:
        _, out, _ = _run(
            ["record-verdict", str(self.root),
             "--review-report", self.rr, "--json"]
        )
        data = json.loads(out)
        verdict = data["parse_result"]["verdict"]
        self.assertIsInstance(verdict, str)
        self.assertEqual(verdict, "pass")

    def test_json_next_action_kind_is_string(self) -> None:
        _, out, _ = _run(
            ["record-verdict", str(self.root),
             "--review-report", self.rr, "--json"]
        )
        data = json.loads(out)
        kind = data["next_action"]["kind"]
        self.assertIsInstance(kind, str)


# ---------------------------------------------------------------------------
# CLI-level: missing --review-report is an argparse error
# ---------------------------------------------------------------------------

class MissingReportArgTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_missing_review_report_exits_nonzero(self) -> None:
        err_buf = StringIO()
        exit_code = None
        try:
            with redirect_stderr(err_buf):
                main(["record-verdict", str(self.root)])
        except SystemExit as exc:
            exit_code = exc.code
        self.assertNotEqual(exit_code, 0)


# ---------------------------------------------------------------------------
# Accepted-slice logic: prior accepted slices are respected
# ---------------------------------------------------------------------------

class AcceptedSliceLogicTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_detailed_roadmap(
            self.root,
            _milestone_block(
                "M001", "Test",
                [
                    ("M001-S01", "first slice"),
                    ("M001-S02", "second slice"),
                    ("M001-S03", "third slice"),
                ],
            ),
        )
        # S01 is already accepted
        _write_review_report(self.root, "m001_s01_first_slice_review_report.md", "pass")
        # S02 is the current subject
        _write_review_report(self.root, "m001_s02_second_slice_review_report.md", "pass")

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_pass_on_s02_advances_to_s03(self) -> None:
        rr = self.root / "05_governance" / "reviews" / "m001_s02_second_slice_review_report.md"
        plan = build_verdict_record_plan(self.root, rr)
        self.assertTrue(plan.valid)
        self.assertEqual(plan.next_action.kind, NextActionKind.ADVANCE_TO_NEXT_SLICE)
        self.assertEqual(plan.next_action.next_slice_id, "M001-S03")


# ---------------------------------------------------------------------------
# Compatibility: existing commands still work
# ---------------------------------------------------------------------------

class CompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        (self.root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
            "### M001: Test\n\nStatus: active\n\n", encoding="utf-8"
        )
        _write_detailed_roadmap(
            self.root,
            _milestone_block("M001", "Test", [("M001-S01", "first slice")]),
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_status_still_works(self) -> None:
        code, out, _ = _run(["status", str(self.root)])
        self.assertEqual(code, 0)
        self.assertIn("Project:", out)

    def test_next_still_works(self) -> None:
        code, out, _ = _run(["next", str(self.root)])
        self.assertEqual(code, 0)
        self.assertIn("Project:", out)

    def test_status_json_still_works(self) -> None:
        code, out, _ = _run(["status", str(self.root), "--json"])
        self.assertEqual(code, 0)
        json.loads(out)


if __name__ == "__main__":
    unittest.main()
