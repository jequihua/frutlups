"""Tests for M008-S05: resumable loop status after each step."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from frutlups.cli import main
from frutlups.project import (
    LoopResumeStatus,
    LoopResumeStep,
    build_loop_resume_status,
    build_status,
)


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


def _write_active_roadmap(root: Path, content: str) -> None:
    (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
        content, encoding="utf-8"
    )


def _write_detailed_roadmap(root: Path, content: str) -> None:
    (root / "03_experiments" / "development_roadmap_frutlups.md").write_text(
        content, encoding="utf-8"
    )


def _write_review_report(root: Path, filename: str, verdict: str = "pass") -> None:
    (root / "05_governance" / "reviews" / filename).write_text(
        f"# Review\n\n## Verdict\n\n{verdict}\n", encoding="utf-8"
    )


def _write_verdict_record(root: Path, filename: str, report_filename: str) -> None:
    """Write a verdict record in the real generated shape.

    M003-S05: a valid receipt carries a live ``## Source`` citation of the
    review report it records; bare records are contradictory durable state.
    """
    (root / "05_governance" / "reviews" / filename).write_text(
        "# Verdict Record\n\n## Source\n\n"
        f"Review report: `05_governance/reviews/{report_filename}`\n",
        encoding="utf-8",
    )


def _write_coding_prompt(root: Path, filename: str, content: str) -> None:
    (root / "prompts" / "for_coding_agent" / filename).write_text(
        content, encoding="utf-8"
    )


def _write_review_prompt(root: Path, filename: str) -> None:
    (root / "prompts" / "for_review_agent" / filename).write_text(
        "# Review\n", encoding="utf-8"
    )


def _write_self_report(root: Path, repo_relative_path: str, content: str | None = None) -> None:
    target = root / repo_relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    if content is None:
        content = _minimal_self_report()
    target.write_text(content, encoding="utf-8")


def _minimal_self_report(
    review_prompt_path: str = "prompts/for_review_agent/001_review_something.md",
) -> str:
    return (
        "# Self-Report\n\n"
        "## Files Changed\n\nsome/file.py\n\n"
        "## Behavior Implemented\n\nDid stuff.\n\n"
        "## Tests Added or Updated\n\nAdded 5 tests.\n\n"
        "## Verification Commands and Results\n\npython -m unittest\n\n"
        "## Live Status Summary\n\nAll good.\n\n"
        "## Known Limits and Intentional Deferrals\n\nNone.\n\n"
        "## Memory Usage Statement\n\nNo memory used.\n\n"
        f"## Matching Review Prompt Path Created by the Coder\n\n`{review_prompt_path}`\n\n"
        "## Blockers or Open Questions\n\nNone.\n"
    )


def _active_roadmap(mid: str = "M001", title: str = "Test", status: str = "active") -> str:
    return f"### {mid}: {title}\n\nStatus: {status}\n\n"


def _detailed_roadmap(
    mid: str = "M001",
    title: str = "Test",
    slices: list[tuple[str, str]] | None = None,
) -> str:
    if slices is None:
        slices = [("M001-S01", "first slice"), ("M001-S02", "second slice")]
    lines = [f"### {mid}: {title}\n\nSlices:\n"]
    for sid, stitle in slices:
        lines.append(f"- {sid}: {stitle}\n")
    lines.append("\n")
    return "".join(lines)


def _minimal_coding_prompt(
    sequence: int = 1,
    *,
    milestone_id: str = "M001",
    slice_id: str = "M001-S01",
    title: str = "first slice",
    self_report_path: str = "05_governance/reviews/m001_s01_first_slice_self_report.md",
) -> str:
    seq = f"{sequence:03d}"
    return (
        f"# Coding Prompt {seq}: frutlups {slice_id} {title}\n\n"
        "## Active Roadmap Item\n\n"
        f"Active roadmap milestone: `{milestone_id}`\n\n"
        f"Detailed roadmap slice: `{slice_id}: {title}`\n\n"
        "## Required Reading\n\n"
        "- `CLAUDE.md`\n- `README.md`\n\n"
        "## Non-Goals\n\n"
        "- Do not do X.\n\n"
        "## Required Self-Report\n\n"
        "Write a self-report at:\n\n"
        f"`{self_report_path}`\n\n"
        "The self-report must include all required fields.\n"
    )


def _run(args: list[str]) -> tuple[int, str, str]:
    out_buf = StringIO()
    err_buf = StringIO()
    with redirect_stdout(out_buf), redirect_stderr(err_buf):
        code = main(args)
    return code, out_buf.getvalue(), err_buf.getvalue()


def _status_json(root: Path) -> dict:
    _, out, _ = _run(["status", str(root), "--json"])
    return json.loads(out)


# ---------------------------------------------------------------------------
# JSON output: loop_resume key present with stable shape
# ---------------------------------------------------------------------------

class StatusJsonResumeKeyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_roadmap(self.root, _active_roadmap())
        _write_detailed_roadmap(self.root, _detailed_roadmap())

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_loop_resume_key_present(self) -> None:
        data = _status_json(self.root)
        self.assertIn("loop_resume", data)

    def test_loop_resume_has_step(self) -> None:
        data = _status_json(self.root)
        self.assertIn("step", data["loop_resume"])

    def test_loop_resume_has_message(self) -> None:
        data = _status_json(self.root)
        self.assertIn("message", data["loop_resume"])

    def test_loop_resume_has_next_command(self) -> None:
        data = _status_json(self.root)
        self.assertIn("next_command", data["loop_resume"])

    def test_loop_resume_has_artifact_paths(self) -> None:
        data = _status_json(self.root)
        lr = data["loop_resume"]
        for key in (
            "coding_prompt_path",
            "self_report_path",
            "review_prompt_path",
            "review_report_path",
            "verdict_record_path",
        ):
            self.assertIn(key, lr, f"missing key: {key}")

    def test_loop_resume_no_enum_or_path_objects(self) -> None:
        _, out, _ = _run(["status", str(self.root), "--json"])
        self.assertNotIn("Path(", out)
        self.assertNotIn("LoopResumeStep", out)

    def test_loop_resume_step_is_string(self) -> None:
        data = _status_json(self.root)
        self.assertIsInstance(data["loop_resume"]["step"], str)

    def test_loop_resume_diagnostics_is_list(self) -> None:
        data = _status_json(self.root)
        self.assertIsInstance(data["loop_resume"]["diagnostics"], list)


# ---------------------------------------------------------------------------
# Human output: loop step line present
# ---------------------------------------------------------------------------

class StatusHumanOutputResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_roadmap(self.root, _active_roadmap())
        _write_detailed_roadmap(self.root, _detailed_roadmap())

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_loop_step_line_in_output(self) -> None:
        _, out, _ = _run(["status", str(self.root)])
        self.assertIn("Loop step:", out)

    def test_loop_step_line_has_message(self) -> None:
        _, out, _ = _run(["status", str(self.root)])
        lines = [ln for ln in out.splitlines() if "Loop step:" in ln]
        self.assertTrue(lines)
        self.assertGreater(len(lines[0]), len("Loop step:"))


# ---------------------------------------------------------------------------
# Step: no_frontier (no unaccepted slices)
# ---------------------------------------------------------------------------

class NoFrontierStepTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_detailed_roadmap(
            self.root, _detailed_roadmap(slices=[("M001-S01", "only slice")])
        )
        # Accept the only slice with both review report and verdict record so the
        # pre-check for unrecorded pass verdicts does not trigger before no_frontier.
        _write_review_report(self.root, "m001_s01_only_slice_review_report.md", "pass")
        _write_verdict_record(self.root, "m001_s01_only_slice_verdict_record.md", "m001_s01_only_slice_review_report.md")

    def tearDown(self) -> None:
        self._td.cleanup()

    def _resume(self) -> LoopResumeStatus:
        return build_loop_resume_status(build_status(self.root))

    def test_step_is_no_frontier(self) -> None:
        self.assertEqual(self._resume().step, LoopResumeStep.NO_FRONTIER)

    def test_next_command_empty(self) -> None:
        self.assertEqual(self._resume().next_command, "")

    def test_no_artifact_paths(self) -> None:
        r = self._resume()
        self.assertEqual(r.frontier_slice_id, "")


# ---------------------------------------------------------------------------
# Step: make_coding_prompt (no coding prompt for frontier slice)
# ---------------------------------------------------------------------------

class MakeCodingPromptStepTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_roadmap(self.root, _active_roadmap())
        _write_detailed_roadmap(self.root, _detailed_roadmap())

    def tearDown(self) -> None:
        self._td.cleanup()

    def _resume(self) -> LoopResumeStatus:
        return build_loop_resume_status(build_status(self.root))

    def test_step_is_make_coding_prompt(self) -> None:
        self.assertEqual(self._resume().step, LoopResumeStep.MAKE_CODING_PROMPT)

    def test_next_command_contains_make_coding_prompt(self) -> None:
        self.assertIn("make-coding-prompt", self._resume().next_command)

    def test_frontier_slice_id_populated(self) -> None:
        self.assertEqual(self._resume().frontier_slice_id, "M001-S01")


# ---------------------------------------------------------------------------
# Step: execute_coding_prompt (coding prompt exists, self-report missing)
# ---------------------------------------------------------------------------

class ExecuteCodingPromptStepTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_roadmap(self.root, _active_roadmap())
        _write_detailed_roadmap(self.root, _detailed_roadmap())
        _write_coding_prompt(
            self.root,
            "001_frutlups_m001_s01_first_slice.md",
            _minimal_coding_prompt(1),
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def _resume(self) -> LoopResumeStatus:
        return build_loop_resume_status(build_status(self.root))

    def test_step_is_execute_coding_prompt(self) -> None:
        self.assertEqual(self._resume().step, LoopResumeStep.EXECUTE_CODING_PROMPT)

    def test_coding_prompt_path_populated(self) -> None:
        self.assertIn("001_frutlups_m001_s01_first_slice.md", self._resume().coding_prompt_path)

    def test_self_report_path_populated(self) -> None:
        self.assertIn("self_report", self._resume().self_report_path)

    def test_next_command_empty(self) -> None:
        self.assertEqual(self._resume().next_command, "")


# ---------------------------------------------------------------------------
# Step: fix_self_report (self-report exists but is invalid)
# ---------------------------------------------------------------------------

class FixSelfReportStepTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_roadmap(self.root, _active_roadmap())
        _write_detailed_roadmap(self.root, _detailed_roadmap())
        _write_coding_prompt(
            self.root,
            "001_frutlups_m001_s01_first_slice.md",
            _minimal_coding_prompt(1),
        )
        # Write an invalid self-report (missing required fields)
        _write_self_report(
            self.root,
            "05_governance/reviews/m001_s01_first_slice_self_report.md",
            "# Self-Report\n\nThis is missing required fields.\n",
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def _resume(self) -> LoopResumeStatus:
        return build_loop_resume_status(build_status(self.root))

    def test_step_is_fix_self_report(self) -> None:
        self.assertEqual(self._resume().step, LoopResumeStep.FIX_SELF_REPORT)

    def test_diagnostics_mention_missing_fields(self) -> None:
        r = self._resume()
        self.assertTrue(
            any("missing required field" in d or "self-report" in d.lower() for d in r.diagnostics),
            r.diagnostics,
        )

    def test_next_command_empty(self) -> None:
        self.assertEqual(self._resume().next_command, "")


# ---------------------------------------------------------------------------
# Step: make_review_prompt (valid self-report, no review prompt)
# ---------------------------------------------------------------------------

class MakeReviewPromptStepTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_roadmap(self.root, _active_roadmap())
        _write_detailed_roadmap(self.root, _detailed_roadmap())
        _write_coding_prompt(
            self.root,
            "001_frutlups_m001_s01_first_slice.md",
            _minimal_coding_prompt(1),
        )
        _write_self_report(
            self.root,
            "05_governance/reviews/m001_s01_first_slice_self_report.md",
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def _resume(self) -> LoopResumeStatus:
        return build_loop_resume_status(build_status(self.root))

    def test_step_is_make_review_prompt(self) -> None:
        self.assertEqual(self._resume().step, LoopResumeStep.MAKE_REVIEW_PROMPT)

    def test_next_command_contains_make_review_prompt(self) -> None:
        self.assertIn("make-review-prompt", self._resume().next_command)

    def test_next_command_contains_sequence(self) -> None:
        self.assertIn("001", self._resume().next_command)

    def test_self_report_path_populated(self) -> None:
        self.assertIn("self_report", self._resume().self_report_path)


# ---------------------------------------------------------------------------
# Step: execute_review_prompt (review prompt exists, review report missing)
# ---------------------------------------------------------------------------

class ExecuteReviewPromptStepTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_roadmap(self.root, _active_roadmap())
        _write_detailed_roadmap(self.root, _detailed_roadmap())
        _write_coding_prompt(
            self.root,
            "001_frutlups_m001_s01_first_slice.md",
            _minimal_coding_prompt(1),
        )
        _write_self_report(
            self.root,
            "05_governance/reviews/m001_s01_first_slice_self_report.md",
        )
        _write_review_prompt(self.root, "001_review_m001_s01_first_slice.md")

    def tearDown(self) -> None:
        self._td.cleanup()

    def _resume(self) -> LoopResumeStatus:
        return build_loop_resume_status(build_status(self.root))

    def test_step_is_execute_review_prompt(self) -> None:
        self.assertEqual(self._resume().step, LoopResumeStep.EXECUTE_REVIEW_PROMPT)

    def test_review_prompt_path_populated(self) -> None:
        self.assertIn("001_review_m001_s01_first_slice.md", self._resume().review_prompt_path)

    def test_next_command_empty(self) -> None:
        self.assertEqual(self._resume().next_command, "")


# ---------------------------------------------------------------------------
# Step: fix_review_report (review report exists but no parseable verdict)
# ---------------------------------------------------------------------------

class FixReviewReportStepTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_roadmap(self.root, _active_roadmap())
        _write_detailed_roadmap(self.root, _detailed_roadmap())
        _write_coding_prompt(
            self.root,
            "001_frutlups_m001_s01_first_slice.md",
            _minimal_coding_prompt(1),
        )
        _write_self_report(
            self.root,
            "05_governance/reviews/m001_s01_first_slice_self_report.md",
        )
        _write_review_prompt(self.root, "001_review_m001_s01_first_slice.md")
        # Write review report with no verdict section
        (self.root / "05_governance" / "reviews" / "m001_s01_first_slice_review_report.md").write_text(
            "# Review\n\nNo verdict here.\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def _resume(self) -> LoopResumeStatus:
        return build_loop_resume_status(build_status(self.root))

    def test_step_is_fix_review_report(self) -> None:
        self.assertEqual(self._resume().step, LoopResumeStep.FIX_REVIEW_REPORT)

    def test_review_report_path_populated(self) -> None:
        self.assertIn("review_report", self._resume().review_report_path)

    def test_next_command_empty(self) -> None:
        self.assertEqual(self._resume().next_command, "")


# ---------------------------------------------------------------------------
# Step: record_verdict (parseable review report, no verdict record)
# ---------------------------------------------------------------------------

class RecordVerdictStepTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_roadmap(self.root, _active_roadmap())
        _write_detailed_roadmap(self.root, _detailed_roadmap())
        _write_coding_prompt(
            self.root,
            "001_frutlups_m001_s01_first_slice.md",
            _minimal_coding_prompt(1),
        )
        _write_self_report(
            self.root,
            "05_governance/reviews/m001_s01_first_slice_self_report.md",
        )
        _write_review_prompt(self.root, "001_review_m001_s01_first_slice.md")
        # Use needs_work so M001-S01 stays unaccepted (pass would advance the frontier)
        _write_review_report(self.root, "m001_s01_first_slice_review_report.md", "needs_work")

    def tearDown(self) -> None:
        self._td.cleanup()

    def _resume(self) -> LoopResumeStatus:
        return build_loop_resume_status(build_status(self.root))

    def test_step_is_record_verdict(self) -> None:
        self.assertEqual(self._resume().step, LoopResumeStep.RECORD_VERDICT)

    def test_next_command_contains_record_verdict(self) -> None:
        self.assertIn("record-verdict", self._resume().next_command)

    def test_verdict_record_path_populated(self) -> None:
        self.assertIn("verdict_record", self._resume().verdict_record_path)

    def test_review_report_path_in_next_command(self) -> None:
        self.assertIn("review_report", self._resume().next_command)


# ---------------------------------------------------------------------------
# Step: non-pass review report plus verdict record (M003-S05 contradiction)
#
# Superseded by Decision 5: this fixture previously asserted
# ``frontier_recorded`` for a ``needs_work`` report with a verdict record.
# Under M003-S05 a verdict record paired to a non-``pass`` report is
# contradictory durable state and fails closed as ``fix_review_report``.
# ---------------------------------------------------------------------------

class NonPassReceiptContradictionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_roadmap(self.root, _active_roadmap())
        _write_detailed_roadmap(self.root, _detailed_roadmap())
        _write_coding_prompt(
            self.root,
            "001_frutlups_m001_s01_first_slice.md",
            _minimal_coding_prompt(1),
        )
        _write_self_report(
            self.root,
            "05_governance/reviews/m001_s01_first_slice_self_report.md",
        )
        _write_review_prompt(self.root, "001_review_m001_s01_first_slice.md")
        # needs_work so M001-S01 stays unaccepted; the receipt makes it a
        # contradiction, not acceptance.
        _write_review_report(self.root, "m001_s01_first_slice_review_report.md", "needs_work")
        # Write the verdict record
        _write_verdict_record(self.root, "m001_s01_first_slice_verdict_record.md", "m001_s01_first_slice_review_report.md")

    def tearDown(self) -> None:
        self._td.cleanup()

    def _resume(self) -> LoopResumeStatus:
        return build_loop_resume_status(build_status(self.root))

    def test_step_is_fix_review_report(self) -> None:
        self.assertEqual(self._resume().step, LoopResumeStep.FIX_REVIEW_REPORT)

    def test_next_command_is_empty(self) -> None:
        self.assertEqual(self._resume().next_command, "")

    def test_both_artifact_paths_populated(self) -> None:
        r = self._resume()
        self.assertTrue(r.review_report_path.endswith("_review_report.md"))
        self.assertTrue(r.verdict_record_path.endswith("_verdict_record.md"))
        self.assertIn("contradicts review evidence", r.message)


# ---------------------------------------------------------------------------
# Duplicate coding prompts produce diagnostic
# ---------------------------------------------------------------------------

class DuplicateCodingPromptDiagnosticTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_roadmap(self.root, _active_roadmap())
        _write_detailed_roadmap(self.root, _detailed_roadmap())
        _write_coding_prompt(
            self.root,
            "001_frutlups_m001_s01_first_slice.md",
            _minimal_coding_prompt(1),
        )
        _write_coding_prompt(
            self.root,
            "002_frutlups_m001_s01_first_slice_corrective.md",
            _minimal_coding_prompt(2),
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_duplicate_diagnostic_present(self) -> None:
        r = build_loop_resume_status(build_status(self.root))
        self.assertTrue(
            any("multiple" in d.lower() or "duplicate" in d.lower() for d in r.diagnostics),
            r.diagnostics,
        )

    def test_uses_highest_sequence(self) -> None:
        r = build_loop_resume_status(build_status(self.root))
        self.assertIn("002", r.coding_prompt_path)


# ---------------------------------------------------------------------------
# build_loop_resume_status accepts Path or str
# ---------------------------------------------------------------------------

class BuildLoopResumeStatusInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_roadmap(self.root, _active_roadmap())
        _write_detailed_roadmap(self.root, _detailed_roadmap())

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_accepts_path(self) -> None:
        r = build_loop_resume_status(self.root)
        self.assertIsInstance(r, LoopResumeStatus)

    def test_accepts_string(self) -> None:
        r = build_loop_resume_status(str(self.root))
        self.assertIsInstance(r, LoopResumeStatus)

    def test_accepts_project_status(self) -> None:
        status = build_status(self.root)
        r = build_loop_resume_status(status)
        self.assertIsInstance(r, LoopResumeStatus)


# ---------------------------------------------------------------------------
# to_dict produces plain JSON values
# ---------------------------------------------------------------------------

class LoopResumeStatusToDictTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_roadmap(self.root, _active_roadmap())
        _write_detailed_roadmap(self.root, _detailed_roadmap())

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_to_dict_serializable(self) -> None:
        r = build_loop_resume_status(build_status(self.root))
        d = r.to_dict()
        json.dumps(d)  # must not raise

    def test_step_is_string_in_dict(self) -> None:
        r = build_loop_resume_status(build_status(self.root))
        self.assertIsInstance(r.to_dict()["step"], str)

    def test_no_path_objects_in_dict(self) -> None:
        r = build_loop_resume_status(build_status(self.root))
        text = json.dumps(r.to_dict())
        self.assertNotIn("Path(", text)


# ---------------------------------------------------------------------------
# Compatibility: existing commands unaffected
# ---------------------------------------------------------------------------

class CompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_roadmap(self.root, _active_roadmap())
        _write_detailed_roadmap(self.root, _detailed_roadmap())

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_status_exits_zero(self) -> None:
        code, _, _ = _run(["status", str(self.root)])
        self.assertEqual(code, 0)

    def test_status_json_exits_zero(self) -> None:
        code, _, _ = _run(["status", str(self.root), "--json"])
        self.assertEqual(code, 0)

    def test_next_exits_zero(self) -> None:
        code, _, _ = _run(["next", str(self.root)])
        self.assertEqual(code, 0)

    def test_next_json_exits_zero(self) -> None:
        code, _, _ = _run(["next", str(self.root), "--json"])
        self.assertEqual(code, 0)

    def test_status_json_still_has_existing_keys(self) -> None:
        data = _status_json(self.root)
        for key in ("root", "ok", "milestones", "prompt_health", "memory"):
            self.assertIn(key, data, f"missing key: {key}")


# ---------------------------------------------------------------------------
# Regression: pass verdict without verdict record must surface record_verdict
# even when the accepted-slice scan has advanced the frontier past that slice.
# ---------------------------------------------------------------------------

class PassVerdictUnrecordedTests(unittest.TestCase):
    """Regression for the pass-verdict resume gap.

    A `pass` review report advances the accepted-slice scan, moving the
    frontier to the next slice. Before this fix, `status` would skip the
    `record_verdict` step for the passed slice and report a step from the
    new frontier instead. This test verifies that the unrecorded `pass`
    verdict is surfaced first.
    """

    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        # Two slices: S01 (full pass chain) and S02 (nothing yet)
        _write_active_roadmap(self.root, _active_roadmap())
        _write_detailed_roadmap(
            self.root,
            _detailed_roadmap(slices=[("M001-S01", "first slice"), ("M001-S02", "second slice")]),
        )
        _write_coding_prompt(
            self.root,
            "001_frutlups_m001_s01_first_slice.md",
            _minimal_coding_prompt(1),
        )
        _write_self_report(
            self.root,
            "05_governance/reviews/m001_s01_first_slice_self_report.md",
        )
        _write_review_prompt(self.root, "001_review_m001_s01_first_slice.md")
        # pass verdict advances the accepted-slice scan; verdict record is absent
        _write_review_report(self.root, "m001_s01_first_slice_review_report.md", "pass")

    def tearDown(self) -> None:
        self._td.cleanup()

    def _resume(self) -> LoopResumeStatus:
        return build_loop_resume_status(build_status(self.root))

    def test_step_is_record_verdict(self) -> None:
        self.assertEqual(self._resume().step, LoopResumeStep.RECORD_VERDICT)

    def test_frontier_slice_id_is_passed_slice(self) -> None:
        self.assertEqual(self._resume().frontier_slice_id, "M001-S01")

    def test_next_command_contains_record_verdict(self) -> None:
        self.assertIn("record-verdict", self._resume().next_command)

    def test_review_report_path_in_next_command(self) -> None:
        self.assertIn("m001_s01", self._resume().next_command)

    def test_verdict_record_path_populated(self) -> None:
        self.assertIn("verdict_record", self._resume().verdict_record_path)

    def test_review_report_path_populated(self) -> None:
        self.assertIn("m001_s01", self._resume().review_report_path)

    def test_step_advances_after_verdict_record_written(self) -> None:
        # Once the verdict record is written, the step should move past record_verdict
        reviews = self.root / "05_governance" / "reviews"
        _write_verdict_record(self.root, "m001_s01_first_slice_verdict_record.md", "m001_s01_first_slice_review_report.md")
        r = self._resume()
        self.assertNotEqual(r.step, LoopResumeStep.RECORD_VERDICT)

    def test_json_output_step_is_record_verdict(self) -> None:
        data = _status_json(self.root)
        self.assertEqual(data["loop_resume"]["step"], "record_verdict")

    def test_json_output_frontier_slice_id_is_passed_slice(self) -> None:
        data = _status_json(self.root)
        self.assertEqual(data["loop_resume"]["frontier_slice_id"], "M001-S01")


# ---------------------------------------------------------------------------
# M018-S01-C01: pending corrective review for an already-accepted slice
# ---------------------------------------------------------------------------


class PendingCorrectiveReviewPreCheckTests(unittest.TestCase):
    """A corrective review prompt for an already-accepted slice whose review
    report is missing must be surfaced as ``execute_review_prompt`` (the normal
    frontier scan would otherwise skip the accepted slice)."""

    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_roadmap(self.root, _active_roadmap())
        _write_detailed_roadmap(self.root, _detailed_roadmap())
        # M001-S01 accepted: full pass chain + verdict record.
        _write_coding_prompt(
            self.root, "001_frutlups_m001_s01_first_slice.md", _minimal_coding_prompt(1)
        )
        _write_self_report(
            self.root, "05_governance/reviews/m001_s01_first_slice_self_report.md"
        )
        _write_review_prompt(self.root, "001_review_m001_s01_first_slice.md")
        _write_review_report(self.root, "m001_s01_first_slice_review_report.md", "pass")
        _write_verdict_record(self.root, "m001_s01_first_slice_verdict_record.md", "m001_s01_first_slice_review_report.md")
        # A newer corrective review prompt for the SAME accepted slice, declaring a
        # review-report output that does not exist yet.
        (self.root / "prompts" / "for_review_agent" / "002_review_m001_s01_corrective.md").write_text(
            "# Review Prompt 002\n\n## Review Output Location\n\n"
            "`05_governance/reviews/m001_s01_corrective_review_report.md`\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def _resume(self) -> LoopResumeStatus:
        return build_loop_resume_status(build_status(self.root))

    def test_pending_corrective_review_surfaced(self) -> None:
        r = self._resume()
        self.assertEqual(r.step, LoopResumeStep.EXECUTE_REVIEW_PROMPT)
        self.assertEqual(r.frontier_slice_id, "M001-S01")
        self.assertIn("002_review_m001_s01_corrective.md", r.review_prompt_path)
        self.assertIn("m001_s01_corrective_review_report.md", r.review_report_path)

    def test_does_not_fire_once_corrective_report_written(self) -> None:
        # Once the corrective review report exists, the pre-check no longer fires.
        _write_review_report(self.root, "m001_s01_corrective_review_report.md", "pass")
        r = self._resume()
        self.assertNotEqual(r.step, LoopResumeStep.EXECUTE_REVIEW_PROMPT)

    def test_bare_review_prompt_without_output_location_does_not_fire(self) -> None:
        # A review prompt that declares no review-output location is ignored, so
        # normal per-slice flow is unaffected.
        (self.root / "prompts" / "for_review_agent" / "002_review_m001_s01_corrective.md").write_text(
            "# Review\n", encoding="utf-8"
        )
        r = self._resume()
        self.assertNotEqual(r.step, LoopResumeStep.EXECUTE_REVIEW_PROMPT)

    def test_does_not_fire_when_project_is_complete(self) -> None:
        # When no frontier remains (every roadmap slice accepted), a trailing
        # closure-review prompt must NOT reopen the project: the loop stays at
        # no_frontier rather than surfacing the pending corrective review.
        _write_self_report(
            self.root, "05_governance/reviews/m001_s02_second_slice_self_report.md"
        )
        _write_review_prompt(self.root, "003_review_m001_s02_second_slice.md")
        _write_review_report(self.root, "m001_s02_second_slice_review_report.md", "pass")
        _write_verdict_record(self.root, "m001_s02_second_slice_verdict_record.md", "m001_s02_second_slice_review_report.md")
        # Now both M001-S01 and M001-S02 are accepted -> no frontier. The pending
        # corrective review prompt 002 (for accepted M001-S01) must not fire.
        r = self._resume()
        self.assertEqual(r.step, LoopResumeStep.NO_FRONTIER)


# ---------------------------------------------------------------------------
# M018-S02 hardening: terminal closure verdict-chain stop criterion
# ---------------------------------------------------------------------------


class TerminalClosureStopTests(unittest.TestCase):
    """On a completed roadmap (no frontier), an unrecorded pass review report whose
    sole purpose is to review a verdict-recording closure slice must NOT perpetuate
    record_verdict; ordinary unrecorded pass reports still surface."""

    def _completed_project(self, root: Path) -> None:
        # Single-slice roadmap, fully accepted -> no inferred frontier.
        _make_template(root)
        _write_active_roadmap(root, _active_roadmap())
        _write_detailed_roadmap(root, _detailed_roadmap(slices=[("M001-S01", "first slice")]))
        _write_coding_prompt(root, "001_frutlups_m001_s01_first_slice.md", _minimal_coding_prompt(1))
        _write_self_report(root, "05_governance/reviews/m001_s01_first_slice_self_report.md")
        _write_review_prompt(root, "001_review_m001_s01_first_slice.md")
        _write_review_report(root, "m001_s01_first_slice_review_report.md", "pass")
        _write_verdict_record(root, "m001_s01_first_slice_verdict_record.md", "m001_s01_first_slice_review_report.md")

    def _resume(self, root: Path) -> LoopResumeStatus:
        return build_loop_resume_status(build_status(root))

    def test_completed_roadmap_base_is_no_frontier(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._completed_project(root)
            self.assertEqual(self._resume(root).step, LoopResumeStep.NO_FRONTIER)

    def test_terminal_closure_tail_does_not_perpetuate_record_verdict(self) -> None:
        # Positive case: the slice is accepted INDEPENDENTLY (a non-terminal pass
        # report + verdict record), so the terminal tail is skipped -> no_frontier.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._completed_project(root)
            _write_review_report(
                root, "m001_s01_record_001_review_verdict_review_report.md", "pass"
            )
            self.assertEqual(self._resume(root).step, LoopResumeStep.NO_FRONTIER)

    def test_self_accepting_terminal_tail_still_records(self) -> None:
        # Review-098 finding: a terminal-tail report must not certify its OWN slice
        # as accepted and then be skipped. With no independent (non-terminal)
        # acceptance evidence for M001-S01 -- only the terminal tail itself -- the
        # loop must still surface record_verdict, not no_frontier.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            _write_active_roadmap(root, _active_roadmap())
            _write_detailed_roadmap(root, _detailed_roadmap(slices=[("M001-S01", "first slice")]))
            # The ONLY review report is the unrecorded terminal closure tail.
            _write_review_report(
                root, "m001_s01_record_001_review_verdict_review_report.md", "pass"
            )
            r = self._resume(root)
        self.assertEqual(r.step, LoopResumeStep.RECORD_VERDICT)
        self.assertIn("m001_s01_record_001_review_verdict_review_report.md", r.review_report_path)

    def test_two_terminal_tails_cannot_cross_certify(self) -> None:
        # Two terminal tails for the same slice, no independent acceptance: neither
        # may certify the other, so record_verdict still surfaces.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            _write_active_roadmap(root, _active_roadmap())
            _write_detailed_roadmap(root, _detailed_roadmap(slices=[("M001-S01", "first slice")]))
            _write_review_report(
                root, "m001_s01_record_001_review_verdict_review_report.md", "pass"
            )
            _write_review_report(
                root, "m001_s01_record_002_review_verdict_review_report.md", "pass"
            )
            r = self._resume(root)
        self.assertEqual(r.step, LoopResumeStep.RECORD_VERDICT)

    def test_completed_roadmap_ordinary_unrecorded_pass_still_records(self) -> None:
        # A non-terminal unrecorded pass report on a completed roadmap is NOT
        # skipped: the existing record_verdict behavior is preserved.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._completed_project(root)
            _write_review_report(root, "m001_s01_extra_feature_review_report.md", "pass")
            r = self._resume(root)
        self.assertEqual(r.step, LoopResumeStep.RECORD_VERDICT)
        self.assertIn("m001_s01_extra_feature_review_report.md", r.review_report_path)

    def test_active_frontier_unrecorded_pass_still_records(self) -> None:
        # With an active frontier, an unrecorded pass report still surfaces
        # record_verdict (the terminal-skip only applies when no frontier remains).
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            _write_active_roadmap(root, _active_roadmap())
            _write_detailed_roadmap(root, _detailed_roadmap())  # M001-S01 + M001-S02
            _write_coding_prompt(
                root, "001_frutlups_m001_s01_first_slice.md", _minimal_coding_prompt(1)
            )
            _write_self_report(root, "05_governance/reviews/m001_s01_first_slice_self_report.md")
            _write_review_prompt(root, "001_review_m001_s01_first_slice.md")
            _write_review_report(root, "m001_s01_first_slice_review_report.md", "pass")
            # No verdict record yet; M001-S02 is the unaccepted frontier.
            r = self._resume(root)
        self.assertEqual(r.step, LoopResumeStep.RECORD_VERDICT)
        self.assertEqual(r.frontier_slice_id, "M001-S01")

    def test_terminal_pattern_for_active_work_still_records(self) -> None:
        # Even a terminal-pattern-named report still records while a frontier
        # exists (the skip is gated on a completed roadmap).
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            _write_active_roadmap(root, _active_roadmap())
            _write_detailed_roadmap(root, _detailed_roadmap())  # M001-S01 + M001-S02
            _write_coding_prompt(
                root, "001_frutlups_m001_s01_first_slice.md", _minimal_coding_prompt(1)
            )
            _write_self_report(root, "05_governance/reviews/m001_s01_first_slice_self_report.md")
            _write_review_prompt(root, "001_review_m001_s01_first_slice.md")
            _write_review_report(
                root, "m001_s01_record_001_review_verdict_review_report.md", "pass"
            )
            r = self._resume(root)
        self.assertEqual(r.step, LoopResumeStep.RECORD_VERDICT)


if __name__ == "__main__":
    unittest.main()
