"""Tests for M008-S03: frutlups make-review-prompt command and ReviewPromptPlan."""

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from frutlups.cli import main
from frutlups.project import (
    CodingPromptMeta,
    ReviewPromptPlan,
    build_review_prompt_plan,
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


def _write_coding_prompt(root: Path, filename: str, content: str) -> None:
    (root / "prompts" / "for_coding_agent" / filename).write_text(
        content, encoding="utf-8"
    )


def _write_review_prompt(root: Path, filename: str, content: str = "# Review\n") -> None:
    (root / "prompts" / "for_review_agent" / filename).write_text(
        content, encoding="utf-8"
    )


def _write_self_report(root: Path, repo_relative_path: str, content: str) -> None:
    target = root / repo_relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _minimal_coding_prompt(
    sequence: int,
    *,
    milestone_id: str = "M001",
    slice_id: str = "M001-S01",
    title: str = "test slice",
    self_report_path: str = "05_governance/reviews/m001_s01_test_slice_self_report.md",
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


def _minimal_self_report(
    review_prompt_path: str = "prompts/for_review_agent/001_review_something.md",
) -> str:
    return (
        "# Self-Report\n\n"
        "## Files Changed\n\n"
        "- 08_pkg/src/frutlups/project.py\n\n"
        "## Behavior Implemented\n\n"
        "The behavior was implemented.\n\n"
        "## Tests Added or Updated\n\n"
        "- test_something\n\n"
        "## Verification Commands and Results\n\n"
        "```\npython -m unittest discover -s tests\n```\n\n"
        "## Live Status Summary\n\n"
        "Prompts: 1 coding, 0 review.\n\n"
        "## Known Limits and Intentional Deferrals\n\n"
        "None.\n\n"
        "## Memory Usage Statement\n\n"
        "No memory backend was queried or mutated.\n\n"
        "## Matching Review Prompt Path Created by the Coder\n\n"
        f"{review_prompt_path}\n\n"
        "## Blockers or Open Questions\n\n"
        "None.\n"
    )


_SR_PATH_001 = "05_governance/reviews/m001_s01_test_slice_self_report.md"
_CP_FILENAME_001 = "001_frutlups_m001_s01_test_slice.md"


def _simple_review_project(root: Path) -> None:
    """Set up a project with one unmatched coding prompt and a valid self-report."""
    _make_template(root)
    _write_active_roadmap(
        root,
        "# Active Roadmap\n\n"
        "### M001: First\n\nStatus: active\n\n"
        "### M002: Second\n\nStatus: planned\n\n",
    )
    _write_detailed_roadmap(
        root,
        "# Detailed Roadmap\n\n"
        "### M001: First\n\nSlices:\n\n- M001-S01: test slice\n\n"
        "### M002: Second\n\nSlices:\n\n- M002-S01: next thing\n\n",
    )
    _write_review_report(root, "m001_s01_foo_review_report.md", "pass")
    _write_coding_prompt(
        root,
        _CP_FILENAME_001,
        _minimal_coding_prompt(1, self_report_path=_SR_PATH_001),
    )
    _write_self_report(root, _SR_PATH_001, _minimal_self_report())


def _run_mrp(root: Path, extra: list[str] | None = None) -> tuple[int, str]:
    buf = StringIO()
    args = ["make-review-prompt", str(root)] + (extra or [])
    with redirect_stdout(buf):
        code = main(args)
    return code, buf.getvalue()


# ---------------------------------------------------------------------------
# CLI help
# ---------------------------------------------------------------------------

class CliHelpTests(unittest.TestCase):
    def test_help_includes_make_review_prompt(self) -> None:
        buf = StringIO()
        with redirect_stdout(buf):
            try:
                main(["--help"])
            except SystemExit:
                pass
        self.assertIn("make-review-prompt", buf.getvalue())

    def test_make_review_prompt_help_accessible(self) -> None:
        buf = StringIO()
        with redirect_stdout(buf):
            try:
                main(["make-review-prompt", "--help"])
            except SystemExit:
                pass
        output = buf.getvalue()
        self.assertIn("--json", output)
        self.assertIn("--dry-run", output)
        self.assertIn("--overwrite", output)
        self.assertIn("--sequence", output)
        self.assertIn("--slug", output)
        self.assertNotIn("--correction-round", output)


# ---------------------------------------------------------------------------
# build_review_prompt_plan: no unmatched coding prompt
# ---------------------------------------------------------------------------

class NoUnmatchedPromptTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _make_template(self.root)
        _write_active_roadmap(
            self.root,
            "# Active Roadmap\n\n### M001: Only\n\nStatus: active\n\n",
        )
        # One coding prompt, one matching review prompt
        _write_coding_prompt(
            self.root,
            "001_frutlups_m001_s01_test.md",
            _minimal_coding_prompt(1),
        )
        _write_review_prompt(self.root, "001_review_frutlups_m001_s01_test.md")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_no_unmatched_plan_invalid(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertFalse(plan.valid)

    def test_no_unmatched_plan_has_errors(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertGreater(len(plan.errors), 0)

    def test_no_unmatched_error_message(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertTrue(any("unmatched" in e for e in plan.errors))

    def test_cli_no_unmatched_exits_nonzero(self) -> None:
        code, _ = _run_mrp(self.root, ["--dry-run"])
        self.assertNotEqual(code, 0)


# ---------------------------------------------------------------------------
# build_review_prompt_plan: valid plan
# ---------------------------------------------------------------------------

class ValidPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _simple_review_project(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_plan_is_valid(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertTrue(plan.valid, plan.errors)

    def test_plan_has_no_errors(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertEqual(plan.errors, ())

    def test_plan_sequence_is_one(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertEqual(plan.sequence, 1)

    def test_plan_selected_coding_prompt_not_none(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertIsNotNone(plan.selected_coding_prompt)

    def test_plan_selected_filename(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertEqual(
            plan.selected_coding_prompt.filename, _CP_FILENAME_001
        )

    def test_plan_slug_from_filename(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertEqual(plan.slug, "frutlups_m001_s01_test_slice")

    def test_plan_self_report_valid(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertIsNotNone(plan.self_report)
        self.assertTrue(plan.self_report.valid)

    def test_plan_evidence_no_errors(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertIsNotNone(plan.evidence)
        self.assertEqual(plan.evidence.errors, ())

    def test_plan_template_not_none(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertIsNotNone(plan.template)

    def test_plan_render_valid(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertIsNotNone(plan.render)
        self.assertTrue(plan.render.valid)

    def test_plan_preview_would_write(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertIsNotNone(plan.preview)
        self.assertTrue(plan.preview.would_write)

    def test_plan_preview_target_under_review_agent(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertIn("for_review_agent", plan.preview.target_path)

    def test_plan_template_has_required_reading(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertIn("CLAUDE.md", plan.template.required_reading)
        self.assertIn("README.md", plan.template.required_reading)

    def test_plan_template_has_four_severity_entries(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertEqual(len(plan.template.severity_guidance), 4)

    def test_plan_template_has_four_verdict_choices(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertEqual(len(plan.template.verdict_choices), 4)

    def test_plan_template_has_prior_review_path(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertIn(
            "05_governance/reviews/m008_s02_make_coding_prompt_review_report.md",
            plan.template.prior_review_paths,
        )

    def test_plan_render_content_contains_claude_md(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertIn("CLAUDE.md", plan.render.content)

    def test_plan_render_content_contains_readme_md(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertIn("README.md", plan.render.content)

    def test_plan_render_content_contains_self_report_path(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertIn(plan.template.self_report_path, plan.render.content)

    def test_plan_render_content_contains_review_output_path(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertIn(plan.template.review_output_path, plan.render.content)


# ---------------------------------------------------------------------------
# Missing self-report
# ---------------------------------------------------------------------------

class MissingSelfReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _make_template(self.root)
        _write_active_roadmap(
            self.root, "# Active Roadmap\n\n### M001: Only\n\nStatus: active\n\n"
        )
        _write_coding_prompt(
            self.root,
            _CP_FILENAME_001,
            _minimal_coding_prompt(1, self_report_path=_SR_PATH_001),
        )
        # self-report intentionally not written

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_missing_sr_plan_invalid(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertFalse(plan.valid)

    def test_missing_sr_self_report_field_present(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertIsNotNone(plan.self_report)
        self.assertFalse(plan.self_report.valid)

    def test_missing_sr_errors_mention_missing(self) -> None:
        plan = build_review_prompt_plan(self.root)
        combined = " ".join(plan.errors)
        self.assertTrue(
            "missing" in combined.lower() or "self-report" in combined.lower()
        )


# ---------------------------------------------------------------------------
# Incomplete self-report
# ---------------------------------------------------------------------------

class InvalidSelfReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _make_template(self.root)
        _write_active_roadmap(
            self.root, "# Active Roadmap\n\n### M001: Only\n\nStatus: active\n\n"
        )
        _write_coding_prompt(
            self.root,
            _CP_FILENAME_001,
            _minimal_coding_prompt(1, self_report_path=_SR_PATH_001),
        )
        # Write an incomplete self-report (missing required fields)
        _write_self_report(
            self.root, _SR_PATH_001, "# Self-Report\n\n## Files Changed\n\n- something\n"
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_invalid_sr_plan_invalid(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertFalse(plan.valid)

    def test_invalid_sr_self_report_field_has_errors(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertIsNotNone(plan.self_report)
        self.assertFalse(plan.self_report.valid)
        self.assertGreater(len(plan.self_report.errors), 0)


# ---------------------------------------------------------------------------
# Explicit --sequence
# ---------------------------------------------------------------------------

class ExplicitSequenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _make_template(self.root)
        _write_active_roadmap(
            self.root, "# Active Roadmap\n\n### M001: Only\n\nStatus: active\n\n"
        )
        # Two unmatched coding prompts: 001 and 002
        self._sr1 = "05_governance/reviews/m001_s01_test_slice_self_report.md"
        self._sr2 = "05_governance/reviews/m001_s02_second_slice_self_report.md"
        _write_coding_prompt(
            self.root,
            "001_frutlups_m001_s01_test_slice.md",
            _minimal_coding_prompt(1, self_report_path=self._sr1),
        )
        _write_coding_prompt(
            self.root,
            "002_frutlups_m001_s02_second_slice.md",
            _minimal_coding_prompt(
                2, slice_id="M001-S02", title="second slice",
                self_report_path=self._sr2,
            ),
        )
        _write_self_report(self.root, self._sr1, _minimal_self_report())
        _write_self_report(self.root, self._sr2, _minimal_self_report())

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_auto_selects_highest_sequence(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertEqual(plan.sequence, 2)

    def test_explicit_sequence_one_selects_lower(self) -> None:
        plan = build_review_prompt_plan(self.root, sequence=1)
        self.assertEqual(plan.sequence, 1)
        self.assertTrue(plan.valid, plan.errors)

    def test_explicit_sequence_two_selects_higher(self) -> None:
        plan = build_review_prompt_plan(self.root, sequence=2)
        self.assertEqual(plan.sequence, 2)
        self.assertTrue(plan.valid, plan.errors)

    def test_explicit_sequence_not_found_invalid(self) -> None:
        plan = build_review_prompt_plan(self.root, sequence=99)
        self.assertFalse(plan.valid)
        self.assertTrue(any("099" in e or "no coding prompt" in e for e in plan.errors))

    def test_explicit_sequence_zero_invalid(self) -> None:
        plan = build_review_prompt_plan(self.root, sequence=0)
        self.assertFalse(plan.valid)

    def test_explicit_sequence_1000_invalid(self) -> None:
        plan = build_review_prompt_plan(self.root, sequence=1000)
        self.assertFalse(plan.valid)


# ---------------------------------------------------------------------------
# Existing review prompt / overwrite
# ---------------------------------------------------------------------------

class ExistingReviewPromptTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _simple_review_project(self.root)
        # Write a matching review prompt so sequence 001 is "matched"
        _write_review_prompt(
            self.root, "001_review_frutlups_m001_s01_test_slice.md"
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_existing_review_prompt_without_overwrite_invalid(self) -> None:
        plan = build_review_prompt_plan(self.root, sequence=1)
        self.assertFalse(plan.valid)
        self.assertTrue(any("already exists" in e for e in plan.errors))

    def test_existing_review_prompt_with_overwrite_valid(self) -> None:
        plan = build_review_prompt_plan(self.root, sequence=1, overwrite=True)
        self.assertTrue(plan.valid, plan.errors)

    def test_cli_existing_without_overwrite_exits_nonzero(self) -> None:
        code, _ = _run_mrp(self.root, ["--dry-run", "--sequence", "1"])
        self.assertNotEqual(code, 0)

    def test_cli_existing_with_overwrite_dry_run_exits_zero(self) -> None:
        code, _ = _run_mrp(self.root, ["--dry-run", "--sequence", "1", "--overwrite"])
        self.assertEqual(code, 0)


# ---------------------------------------------------------------------------
# Slug override
# ---------------------------------------------------------------------------

class SlugOverrideTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _simple_review_project(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_slug_override_used_in_plan(self) -> None:
        plan = build_review_prompt_plan(self.root, slug="my_custom_slug")
        self.assertTrue(plan.valid, plan.errors)
        self.assertEqual(plan.slug, "my_custom_slug")

    def test_slug_override_in_preview_filename(self) -> None:
        plan = build_review_prompt_plan(self.root, slug="my_custom_slug")
        self.assertIn("my_custom_slug", plan.preview.target_path)

    def test_whitespace_slug_falls_back_to_derived(self) -> None:
        plan = build_review_prompt_plan(self.root, slug="   ")
        self.assertTrue(plan.valid, plan.errors)
        self.assertNotEqual(plan.slug, "")
        self.assertNotEqual(plan.slug.strip(), "")


# ---------------------------------------------------------------------------
# to_dict() plain values
# ---------------------------------------------------------------------------

class PlanToDictTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _simple_review_project(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_to_dict_is_json_serializable(self) -> None:
        plan = build_review_prompt_plan(self.root)
        serialized = json.dumps(plan.to_dict())
        self.assertIsInstance(serialized, str)

    def test_to_dict_sequence_is_int(self) -> None:
        plan = build_review_prompt_plan(self.root)
        d = plan.to_dict()
        self.assertIsInstance(d["sequence"], int)

    def test_to_dict_errors_is_list(self) -> None:
        plan = build_review_prompt_plan(self.root)
        d = plan.to_dict()
        self.assertIsInstance(d["errors"], list)

    def test_to_dict_selected_coding_prompt_has_filename(self) -> None:
        plan = build_review_prompt_plan(self.root)
        d = plan.to_dict()
        self.assertIsNotNone(d["selected_coding_prompt"])
        self.assertIn("filename", d["selected_coding_prompt"])

    def test_to_dict_self_report_nested_under_validation(self) -> None:
        plan = build_review_prompt_plan(self.root)
        d = plan.to_dict()
        self.assertIn("self_report", d)
        self.assertIsNotNone(d["self_report"])
        self.assertIn("validation", d["self_report"])
        self.assertIn("valid", d["self_report"]["validation"])

    def test_to_dict_self_report_validation_valid_true(self) -> None:
        plan = build_review_prompt_plan(self.root)
        d = plan.to_dict()
        self.assertTrue(d["self_report"]["validation"]["valid"])

    def test_to_dict_evidence_errors_is_list(self) -> None:
        plan = build_review_prompt_plan(self.root)
        d = plan.to_dict()
        self.assertIsInstance(d["evidence"]["errors"], list)

    def test_to_dict_preview_target_path_is_string(self) -> None:
        plan = build_review_prompt_plan(self.root)
        d = plan.to_dict()
        self.assertIsInstance(d["preview"]["target_path"], str)
        self.assertIn("for_review_agent", d["preview"]["target_path"])

    def test_to_dict_preview_would_write_is_true(self) -> None:
        plan = build_review_prompt_plan(self.root)
        d = plan.to_dict()
        self.assertTrue(d["preview"]["would_write"])

    def test_to_dict_render_valid_is_true(self) -> None:
        plan = build_review_prompt_plan(self.root)
        d = plan.to_dict()
        self.assertTrue(d["render"]["valid"])

    def test_to_dict_contains_all_expected_top_keys(self) -> None:
        plan = build_review_prompt_plan(self.root)
        d = plan.to_dict()
        for key in (
            "frontier", "sequence", "slug", "valid", "errors",
            "selected_coding_prompt", "coding_prompt_meta",
            "self_report", "evidence", "template", "render", "preview",
        ):
            self.assertIn(key, d, f"missing key: {key}")


# ---------------------------------------------------------------------------
# Plan is frozen
# ---------------------------------------------------------------------------

class PlanFrozenTests(unittest.TestCase):
    def test_plan_is_frozen(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _simple_review_project(root)
            plan = build_review_prompt_plan(root)
        with self.assertRaises((AttributeError, TypeError)):
            plan.slug = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# CLI dry-run
# ---------------------------------------------------------------------------

class CliDryRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _simple_review_project(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_dry_run_exits_zero(self) -> None:
        code, _ = _run_mrp(self.root, ["--dry-run"])
        self.assertEqual(code, 0)

    def test_dry_run_does_not_write_file(self) -> None:
        _run_mrp(self.root, ["--dry-run"])
        review_dir = self.root / "prompts" / "for_review_agent"
        md_files = list(review_dir.glob("*.md")) if review_dir.exists() else []
        self.assertEqual(len(md_files), 0, "dry-run must not write files")

    def test_dry_run_output_contains_coding_prompt(self) -> None:
        _, output = _run_mrp(self.root, ["--dry-run"])
        self.assertIn("Coding prompt:", output)

    def test_dry_run_output_contains_sequence(self) -> None:
        _, output = _run_mrp(self.root, ["--dry-run"])
        self.assertIn("Sequence:", output)

    def test_dry_run_output_contains_slug(self) -> None:
        _, output = _run_mrp(self.root, ["--dry-run"])
        self.assertIn("Slug:", output)

    def test_dry_run_output_contains_target(self) -> None:
        _, output = _run_mrp(self.root, ["--dry-run"])
        self.assertIn("Target:", output)

    def test_dry_run_output_contains_self_report_status(self) -> None:
        _, output = _run_mrp(self.root, ["--dry-run"])
        self.assertIn("Self-report:", output)

    def test_dry_run_json_exits_zero(self) -> None:
        code, _ = _run_mrp(self.root, ["--dry-run", "--json"])
        self.assertEqual(code, 0)

    def test_dry_run_json_is_valid(self) -> None:
        _, output = _run_mrp(self.root, ["--dry-run", "--json"])
        payload = json.loads(output)
        self.assertIsInstance(payload, dict)

    def test_dry_run_json_sequence(self) -> None:
        _, output = _run_mrp(self.root, ["--dry-run", "--json"])
        payload = json.loads(output)
        self.assertEqual(payload["sequence"], 1)

    def test_dry_run_json_selected_coding_prompt_filename(self) -> None:
        _, output = _run_mrp(self.root, ["--dry-run", "--json"])
        payload = json.loads(output)
        self.assertEqual(
            payload["selected_coding_prompt"]["filename"], _CP_FILENAME_001
        )

    def test_dry_run_json_self_report_validation_valid(self) -> None:
        _, output = _run_mrp(self.root, ["--dry-run", "--json"])
        payload = json.loads(output)
        self.assertTrue(payload["self_report"]["validation"]["valid"])

    def test_dry_run_json_evidence_errors_empty(self) -> None:
        _, output = _run_mrp(self.root, ["--dry-run", "--json"])
        payload = json.loads(output)
        self.assertEqual(payload["evidence"]["errors"], [])

    def test_dry_run_json_preview_target_under_review_agent(self) -> None:
        _, output = _run_mrp(self.root, ["--dry-run", "--json"])
        payload = json.loads(output)
        self.assertIn("for_review_agent", payload["preview"]["target_path"])

    def test_dry_run_json_preview_would_write_true(self) -> None:
        _, output = _run_mrp(self.root, ["--dry-run", "--json"])
        payload = json.loads(output)
        self.assertTrue(payload["preview"]["would_write"])

    def test_dry_run_json_render_valid_true(self) -> None:
        _, output = _run_mrp(self.root, ["--dry-run", "--json"])
        payload = json.loads(output)
        self.assertTrue(payload["render"]["valid"])


# ---------------------------------------------------------------------------
# CLI actual write
# ---------------------------------------------------------------------------

class CliWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _simple_review_project(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_write_exits_zero(self) -> None:
        code, _ = _run_mrp(self.root)
        self.assertEqual(code, 0)

    def test_write_creates_file(self) -> None:
        _run_mrp(self.root)
        review_dir = self.root / "prompts" / "for_review_agent"
        md_files = list(review_dir.glob("*.md"))
        self.assertEqual(len(md_files), 1)

    def test_written_file_contains_claude_md(self) -> None:
        _run_mrp(self.root)
        review_dir = self.root / "prompts" / "for_review_agent"
        content = list(review_dir.glob("*.md"))[0].read_text(encoding="utf-8")
        self.assertIn("CLAUDE.md", content)

    def test_written_file_contains_readme_md(self) -> None:
        _run_mrp(self.root)
        review_dir = self.root / "prompts" / "for_review_agent"
        content = list(review_dir.glob("*.md"))[0].read_text(encoding="utf-8")
        self.assertIn("README.md", content)

    def test_write_json_has_write_result(self) -> None:
        _, output = _run_mrp(self.root, ["--json"])
        payload = json.loads(output)
        self.assertIn("write_result", payload)
        self.assertTrue(payload["write_result"]["wrote"])

    def test_write_content_matches_rendered_plan(self) -> None:
        plan = build_review_prompt_plan(self.root)
        _run_mrp(self.root)
        review_dir = self.root / "prompts" / "for_review_agent"
        written = list(review_dir.glob("*.md"))[0].read_text(encoding="utf-8")
        self.assertEqual(written, plan.render.content)


# ---------------------------------------------------------------------------
# Overwrite behavior
# ---------------------------------------------------------------------------

class OverwriteBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _simple_review_project(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_second_write_without_overwrite_exits_nonzero(self) -> None:
        _run_mrp(self.root)  # first write
        code, _ = _run_mrp(self.root)  # second write, no --overwrite
        self.assertNotEqual(code, 0)

    def test_second_write_preserves_content_without_overwrite(self) -> None:
        _run_mrp(self.root)
        review_dir = self.root / "prompts" / "for_review_agent"
        original = list(review_dir.glob("*.md"))[0].read_text(encoding="utf-8")
        _run_mrp(self.root)
        current = list(review_dir.glob("*.md"))[0].read_text(encoding="utf-8")
        self.assertEqual(original, current)

    def test_overwrite_succeeds(self) -> None:
        _run_mrp(self.root)
        code, _ = _run_mrp(self.root, ["--overwrite", "--sequence", "1"])
        self.assertEqual(code, 0)

    def test_overwrite_json_shows_overwrote_true(self) -> None:
        _run_mrp(self.root)
        _, output = _run_mrp(self.root, ["--overwrite", "--sequence", "1", "--json"])
        payload = json.loads(output)
        self.assertTrue(payload["write_result"]["overwrote"])


# ---------------------------------------------------------------------------
# Missing project root
# ---------------------------------------------------------------------------

class MissingRootTests(unittest.TestCase):
    def test_missing_root_exits_nonzero(self) -> None:
        buf = StringIO()
        with redirect_stdout(buf):
            code = main(["make-review-prompt", "/no/such/path/frutlups"])
        self.assertNotEqual(code, 0)


# ---------------------------------------------------------------------------
# CodingPromptMeta parsing
# ---------------------------------------------------------------------------

class CodingPromptMetaTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _make_template(self.root)
        _write_active_roadmap(
            self.root, "# Active Roadmap\n\n### M001: Only\n\nStatus: active\n\n"
        )
        _write_coding_prompt(
            self.root,
            _CP_FILENAME_001,
            _minimal_coding_prompt(1, self_report_path=_SR_PATH_001),
        )
        _write_self_report(self.root, _SR_PATH_001, _minimal_self_report())

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_meta_milestone_id_parsed(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertEqual(plan.coding_prompt_meta.milestone_id, "M001")

    def test_meta_slice_id_parsed(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertEqual(plan.coding_prompt_meta.slice_id, "M001-S01")

    def test_meta_title_parsed(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertEqual(plan.coding_prompt_meta.title, "test slice")

    def test_meta_slug_from_filename(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertEqual(plan.coding_prompt_meta.slug, "frutlups_m001_s01_test_slice")

    def test_meta_self_report_path_parsed(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertEqual(plan.coding_prompt_meta.self_report_path, _SR_PATH_001)

    def test_meta_review_output_path_derived(self) -> None:
        plan = build_review_prompt_plan(self.root)
        expected = _SR_PATH_001.replace("_self_report.md", "_review_report.md")
        self.assertEqual(plan.coding_prompt_meta.review_output_path, expected)

    def test_meta_required_reading_has_claude_md(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertIn("CLAUDE.md", plan.coding_prompt_meta.required_reading)

    def test_meta_required_reading_has_readme_md(self) -> None:
        plan = build_review_prompt_plan(self.root)
        self.assertIn("README.md", plan.coding_prompt_meta.required_reading)

    def test_meta_to_dict_is_serializable(self) -> None:
        plan = build_review_prompt_plan(self.root)
        serialized = json.dumps(plan.coding_prompt_meta.to_dict())
        self.assertIsInstance(serialized, str)


class RoundQualifiedReviewOutputTests(unittest.TestCase):
    def test_round_two_coding_prompt_derives_matching_review_output(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            _write_active_roadmap(
                root, "# Active Roadmap\n\n### M001: Only\n\nStatus: active\n\n"
            )
            self_report = (
                "05_governance/reviews/"
                "m001_s01_test_slice_round_002_self_report.md"
            )
            _write_coding_prompt(
                root,
                _CP_FILENAME_001,
                _minimal_coding_prompt(1, self_report_path=self_report),
            )
            _write_self_report(root, self_report, _minimal_self_report())
            plan = build_review_prompt_plan(root)
            expected = (
                "05_governance/reviews/"
                "m001_s01_test_slice_round_002_review_report.md"
            )
            self.assertTrue(plan.valid, plan.errors)
            self.assertEqual(plan.coding_prompt_meta.review_output_path, expected)
            self.assertEqual(plan.template.review_output_path, expected)
            self.assertIn(f"Review output: `{expected}`", plan.render.content)


# ---------------------------------------------------------------------------
# Command compatibility
# ---------------------------------------------------------------------------

class CommandCompatibilityTests(unittest.TestCase):
    def test_status_still_works(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            _write_active_roadmap(
                root,
                "# Active Roadmap\n\n### M001: Milestone\n\nStatus: active\n\n",
            )
            buf = StringIO()
            with redirect_stdout(buf):
                code = main(["status", str(root)])
            self.assertEqual(code, 0)
            self.assertIn("Project:", buf.getvalue())

    def test_next_still_works(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _simple_review_project(root)
            buf = StringIO()
            with redirect_stdout(buf):
                code = main(["next", str(root)])
            self.assertEqual(code, 0)

    def test_make_coding_prompt_still_works(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            _write_active_roadmap(
                root,
                "# Active Roadmap\n\n"
                "### M001: First\n\nStatus: active\n\n"
                "### M002: Second\n\nStatus: planned\n\n",
            )
            _write_detailed_roadmap(
                root,
                "# Detailed Roadmap\n\n"
                "### M001: First\n\nSlices:\n\n- M001-S01: do thing\n\n"
                "### M002: Second\n\nSlices:\n\n- M002-S01: next\n\n",
            )
            (root / "05_governance" / "reviews").mkdir(parents=True, exist_ok=True)
            _write_review_report(root, "m001_s01_foo_review_report.md", "pass")
            buf = StringIO()
            with redirect_stdout(buf):
                code = main(["make-coding-prompt", str(root), "--dry-run"])
            self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
