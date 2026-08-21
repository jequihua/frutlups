"""Tests for M008-S02: frutlups make-coding-prompt command and CodingPromptPlan."""

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest import mock

from frutlups.cli import main
from frutlups.project import (
    CodingPromptPlan,
    VerdictRecordWriteCommand,
    _build_status_with_evidence,
    _derive_slug,
    build_coding_prompt_plan,
    build_rework_declaration_plan,
    build_verdict_record_plan,
    write_verdict_record,
)
from frutlups.rework import (
    ReworkDeclarationWriteCommand,
    write_rework_declaration,
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


def _simple_project(root: Path) -> None:
    """Create a project with M001 active and M002 planned (M001 exhausted)."""
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
        "### M001: First\n\nSlices:\n\n- M001-S01: do the thing\n\n"
        "### M002: Second\n\nSlices:\n\n- M002-S01: next thing\n\n",
    )
    _write_review_report(root, "m001_s01_foo_review_report.md", "pass")


def _run_mcp(root: Path, extra: list[str] | None = None) -> tuple[int, str]:
    buf = StringIO()
    args = ["make-coding-prompt", str(root)] + (extra or [])
    with redirect_stdout(buf):
        code = main(args)
    return code, buf.getvalue()


# ---------------------------------------------------------------------------
# CLI help
# ---------------------------------------------------------------------------

class CliHelpTests(unittest.TestCase):
    def test_help_includes_make_coding_prompt(self) -> None:
        buf = StringIO()
        with redirect_stdout(buf):
            try:
                main(["--help"])
            except SystemExit:
                pass
        self.assertIn("make-coding-prompt", buf.getvalue())

    def test_make_coding_prompt_help_accessible(self) -> None:
        buf = StringIO()
        with redirect_stdout(buf):
            try:
                main(["make-coding-prompt", "--help"])
            except SystemExit:
                pass
        output = buf.getvalue()
        self.assertIn("make-coding-prompt", output.lower())


# ---------------------------------------------------------------------------
# Slug derivation
# ---------------------------------------------------------------------------

class SlugDerivationTests(unittest.TestCase):
    def test_slug_for_m008_s02_contains_milestone_and_slice(self) -> None:
        slug = _derive_slug("M008-S02", "`frutlups make-coding-prompt`")
        self.assertIn("m008", slug)
        self.assertIn("s02", slug)

    def test_slug_starts_with_frutlups(self) -> None:
        slug = _derive_slug("M008-S02", "`frutlups make-coding-prompt`")
        self.assertTrue(slug.startswith("frutlups_"))

    def test_slug_contains_sanitized_title(self) -> None:
        slug = _derive_slug("M008-S02", "`frutlups make-coding-prompt`")
        self.assertIn("make_coding_prompt", slug)

    def test_slug_is_lowercase_alphanumeric_underscores(self) -> None:
        slug = _derive_slug("M008-S02", "`frutlups make-coding-prompt`")
        import re
        self.assertRegex(slug, r"^[a-z0-9_]+$")

    def test_slug_no_path_separators(self) -> None:
        slug = _derive_slug("M008-S02", "`frutlups make-coding-prompt`")
        self.assertNotIn("/", slug)
        self.assertNotIn("\\", slug)

    def test_slug_deterministic(self) -> None:
        s1 = _derive_slug("M008-S02", "`frutlups make-coding-prompt`")
        s2 = _derive_slug("M008-S02", "`frutlups make-coding-prompt`")
        self.assertEqual(s1, s2)

    def test_slug_different_slices_produce_different_slugs(self) -> None:
        s1 = _derive_slug("M008-S01", "`frutlups next`")
        s2 = _derive_slug("M008-S02", "`frutlups make-coding-prompt`")
        self.assertNotEqual(s1, s2)


# ---------------------------------------------------------------------------
# build_coding_prompt_plan: valid plan
# ---------------------------------------------------------------------------

class ValidPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _simple_project(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_plan_is_valid(self) -> None:
        plan = build_coding_prompt_plan(self.root)
        self.assertTrue(plan.valid)
        self.assertEqual(plan.errors, ())

    def test_plan_has_inferred_slice(self) -> None:
        plan = build_coding_prompt_plan(self.root)
        self.assertIsNotNone(plan.frontier.inferred_slice)
        self.assertEqual(plan.frontier.inferred_slice.slice_id, "M002-S01")

    def test_plan_sequence_is_positive_int(self) -> None:
        plan = build_coding_prompt_plan(self.root)
        self.assertIsInstance(plan.sequence, int)
        self.assertGreater(plan.sequence, 0)

    def test_plan_slug_starts_with_frutlups(self) -> None:
        plan = build_coding_prompt_plan(self.root)
        self.assertTrue(plan.slug.startswith("frutlups_"))

    def test_plan_template_not_none(self) -> None:
        plan = build_coding_prompt_plan(self.root)
        self.assertIsNotNone(plan.template)

    def test_plan_render_valid(self) -> None:
        plan = build_coding_prompt_plan(self.root)
        self.assertIsNotNone(plan.render)
        self.assertTrue(plan.render.valid)

    def test_plan_preview_not_none(self) -> None:
        plan = build_coding_prompt_plan(self.root)
        self.assertIsNotNone(plan.preview)

    def test_plan_preview_would_write(self) -> None:
        plan = build_coding_prompt_plan(self.root)
        self.assertTrue(plan.preview.would_write)


# ---------------------------------------------------------------------------
# Content requirements: CLAUDE.md, README.md, self-report path
# ---------------------------------------------------------------------------

class ContentRequirementsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _simple_project(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_required_reading_includes_claude_md(self) -> None:
        plan = build_coding_prompt_plan(self.root)
        self.assertIn("CLAUDE.md", plan.template.required_reading)

    def test_required_reading_includes_readme_md(self) -> None:
        plan = build_coding_prompt_plan(self.root)
        self.assertIn("README.md", plan.template.required_reading)

    def test_rendered_content_contains_claude_md(self) -> None:
        plan = build_coding_prompt_plan(self.root)
        self.assertIn("CLAUDE.md", plan.render.content)

    def test_rendered_content_contains_readme_md(self) -> None:
        plan = build_coding_prompt_plan(self.root)
        self.assertIn("README.md", plan.render.content)

    def test_self_report_path_in_rendered_content(self) -> None:
        plan = build_coding_prompt_plan(self.root)
        self.assertIn("self_report", plan.template.self_report_path)
        self.assertIn(plan.template.self_report_path, plan.render.content)

    def test_review_prompt_path_in_rendered_content(self) -> None:
        plan = build_coding_prompt_plan(self.root)
        self.assertIn("for_review_agent", plan.render.content)


# ---------------------------------------------------------------------------
# No frontier: deterministic failure
# ---------------------------------------------------------------------------

class NoFrontierPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _make_template(self.root)
        _write_active_roadmap(
            self.root,
            "# Active Roadmap\n\n### M001: Only\n\nStatus: active\n\n",
        )
        _write_detailed_roadmap(
            self.root,
            "# Detailed Roadmap\n\n### M001: Only\n\nSlices:\n\n- M001-S01: only\n\n",
        )
        _write_review_report(self.root, "m001_s01_foo_review_report.md", "pass")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_no_frontier_plan_invalid(self) -> None:
        plan = build_coding_prompt_plan(self.root)
        self.assertFalse(plan.valid)

    def test_no_frontier_plan_has_errors(self) -> None:
        plan = build_coding_prompt_plan(self.root)
        self.assertGreater(len(plan.errors), 0)

    def test_no_frontier_plan_does_not_raise(self) -> None:
        plan = build_coding_prompt_plan(self.root)
        self.assertIsInstance(plan, CodingPromptPlan)

    def test_no_frontier_plan_template_is_none(self) -> None:
        plan = build_coding_prompt_plan(self.root)
        self.assertIsNone(plan.template)


# ---------------------------------------------------------------------------
# Sequence override and validation
# ---------------------------------------------------------------------------

class SequenceValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _simple_project(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_sequence_override_accepted(self) -> None:
        plan = build_coding_prompt_plan(self.root, sequence=42)
        self.assertTrue(plan.valid)
        self.assertEqual(plan.sequence, 42)

    def test_sequence_zero_rejected(self) -> None:
        plan = build_coding_prompt_plan(self.root, sequence=0)
        self.assertFalse(plan.valid)
        self.assertTrue(any("sequence" in e for e in plan.errors))

    def test_sequence_negative_rejected(self) -> None:
        plan = build_coding_prompt_plan(self.root, sequence=-1)
        self.assertFalse(plan.valid)

    def test_sequence_too_large_rejected(self) -> None:
        plan = build_coding_prompt_plan(self.root, sequence=1000)
        self.assertFalse(plan.valid)
        self.assertTrue(any("999" in e or "at most" in e for e in plan.errors))

    def test_sequence_999_accepted(self) -> None:
        plan = build_coding_prompt_plan(self.root, sequence=999)
        self.assertTrue(plan.valid)


class CorrectionRoundTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _simple_project(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _metadata_status(self):
        status, evidence = _build_status_with_evidence(self.root)
        profile = replace(status.layout.profile, prompt_pairing="workflow_metadata")
        layout = replace(status.layout, profile=profile)
        return replace(status, layout=layout), evidence

    def test_cli_round_two_qualifies_path_and_emits_truthful_metadata(self) -> None:
        status, evidence = self._metadata_status()
        with mock.patch(
            "frutlups.cli._build_status_with_evidence",
            return_value=(status, evidence),
        ):
            code, output = _run_mcp(
                self.root,
                ["--correction-round", "2", "--dry-run", "--json"],
            )
        payload = json.loads(output)
        self.assertEqual(code, 0)
        self.assertEqual(
            payload["template"]["self_report_path"],
            "05_governance/reviews/"
            "m002_s01_next_thing_round_002_self_report.md",
        )
        self.assertIn(
            "\nround: 2\nrole: coder\n",
            payload["render"]["content"],
        )

    def test_omitted_and_round_one_plans_are_byte_identical(self) -> None:
        omitted = build_coding_prompt_plan(self.root)
        round_one = build_coding_prompt_plan(self.root, correction_round=1)
        self.assertEqual(round_one.to_dict(), omitted.to_dict())

    def test_round_one_workflow_metadata_plan_is_byte_identical(self) -> None:
        status, evidence = self._metadata_status()
        with mock.patch(
            "frutlups.project._build_status_with_evidence",
            return_value=(status, evidence),
        ):
            omitted = build_coding_prompt_plan(self.root)
            round_one = build_coding_prompt_plan(self.root, correction_round=1)
        self.assertEqual(round_one.to_dict(), omitted.to_dict())
        self.assertNotIn("\nround:", round_one.render.content)

    def test_invalid_round_values_fail_closed(self) -> None:
        for value in (0, -1, 1000, True, "2", 2.0):
            with self.subTest(value=value):
                plan = build_coding_prompt_plan(self.root, correction_round=value)
                self.assertFalse(plan.valid)
                self.assertTrue(any("correction round" in error for error in plan.errors))

    def test_cli_non_integer_round_is_rejected(self) -> None:
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as raised:
            _run_mcp(self.root, ["--correction-round", "two", "--dry-run"])
        self.assertNotEqual(raised.exception.code, 0)

    def test_active_rework_and_round_two_refuse(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            _write_active_roadmap(
                root,
                "# Active Roadmap\n\n### M001: First\n\nStatus: active\n\n",
            )
            _write_detailed_roadmap(
                root,
                "# Detailed Roadmap\n\n### M001: First\n\n"
                "Slices:\n\n- M001-S01: first slice\n\n",
            )
            name = "m001_s01_first_slice_review_report.md"
            _write_review_report(root, name, "pass")
            verdict = build_verdict_record_plan(
                root, root / "05_governance" / "reviews" / name
            )
            self.assertTrue(verdict.valid, verdict.errors)
            receipt = write_verdict_record(
                VerdictRecordWriteCommand(project_root=root, plan=verdict)
            )
            self.assertTrue(receipt.wrote)
            declaration = build_rework_declaration_plan(
                root,
                pass_id="corrective_pass_001",
                slice_ids=("M001-S01",),
            )
            self.assertTrue(declaration.valid, declaration.errors)
            declared = write_rework_declaration(
                ReworkDeclarationWriteCommand(project_root=root, plan=declaration)
            )
            self.assertTrue(declared.wrote)
            plan = build_coding_prompt_plan(root, correction_round=2)
            self.assertFalse(plan.valid)
            self.assertIn(
                "correction rounds cannot be combined with an active rework declaration",
                plan.errors,
            )


# ---------------------------------------------------------------------------
# Slug override
# ---------------------------------------------------------------------------

class SlugOverrideTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _simple_project(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_slug_override_used(self) -> None:
        plan = build_coding_prompt_plan(self.root, slug="my_custom_slug")
        self.assertTrue(plan.valid)
        self.assertEqual(plan.slug, "my_custom_slug")

    def test_empty_slug_rejected(self) -> None:
        plan = build_coding_prompt_plan(self.root, slug="")
        self.assertFalse(plan.valid)
        self.assertTrue(any("slug" in e for e in plan.errors))

    def test_whitespace_slug_rejected(self) -> None:
        plan = build_coding_prompt_plan(self.root, slug="   ")
        self.assertFalse(plan.valid)


# ---------------------------------------------------------------------------
# to_dict() plain values
# ---------------------------------------------------------------------------

class PlanToDictTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _simple_project(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_to_dict_is_json_serializable(self) -> None:
        plan = build_coding_prompt_plan(self.root)
        serialized = json.dumps(plan.to_dict())
        self.assertIsInstance(serialized, str)

    def test_to_dict_contains_expected_keys(self) -> None:
        plan = build_coding_prompt_plan(self.root)
        d = plan.to_dict()
        for key in ("frontier", "sequence", "slug", "valid", "errors",
                    "template", "render", "preview"):
            self.assertIn(key, d, f"missing key: {key}")

    def test_to_dict_sequence_is_int(self) -> None:
        plan = build_coding_prompt_plan(self.root)
        d = plan.to_dict()
        self.assertIsInstance(d["sequence"], int)

    def test_to_dict_errors_is_list(self) -> None:
        plan = build_coding_prompt_plan(self.root)
        d = plan.to_dict()
        self.assertIsInstance(d["errors"], list)

    def test_to_dict_template_has_required_reading(self) -> None:
        plan = build_coding_prompt_plan(self.root)
        d = plan.to_dict()
        reading = d["template"]["required_reading"]
        self.assertIn("CLAUDE.md", reading)
        self.assertIn("README.md", reading)

    def test_to_dict_preview_has_target_path(self) -> None:
        plan = build_coding_prompt_plan(self.root)
        d = plan.to_dict()
        self.assertIn("target_path", d["preview"])
        self.assertIsInstance(d["preview"]["target_path"], str)

    def test_to_dict_preview_has_would_write(self) -> None:
        plan = build_coding_prompt_plan(self.root)
        d = plan.to_dict()
        self.assertIn("would_write", d["preview"])
        self.assertIsInstance(d["preview"]["would_write"], bool)

    def test_to_dict_frontier_has_inferred_slice(self) -> None:
        plan = build_coding_prompt_plan(self.root)
        d = plan.to_dict()
        self.assertIsNotNone(d["frontier"]["inferred_slice"])

    def test_to_dict_none_template_when_invalid(self) -> None:
        _write_review_report(self.root, "m002_s01_foo_review_report.md", "pass")
        plan = build_coding_prompt_plan(self.root)
        d = plan.to_dict()
        self.assertIsNone(d["template"])


# ---------------------------------------------------------------------------
# Plan is frozen
# ---------------------------------------------------------------------------

class PlanFrozenTests(unittest.TestCase):
    def test_plan_is_frozen(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _simple_project(root)
            plan = build_coding_prompt_plan(root)
        with self.assertRaises((AttributeError, TypeError)):
            plan.slug = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# CLI dry-run
# ---------------------------------------------------------------------------

class CliDryRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _simple_project(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_dry_run_exits_zero(self) -> None:
        code, _ = _run_mcp(self.root, ["--dry-run"])
        self.assertEqual(code, 0)

    def test_dry_run_does_not_write_file(self) -> None:
        _run_mcp(self.root, ["--dry-run"])
        coding_dir = self.root / "prompts" / "for_coding_agent"
        md_files = list(coding_dir.glob("*.md")) if coding_dir.exists() else []
        self.assertEqual(len(md_files), 0, "dry-run must not write files")

    def test_dry_run_output_contains_sequence(self) -> None:
        _, output = _run_mcp(self.root, ["--dry-run"])
        self.assertIn("Sequence:", output)

    def test_dry_run_output_contains_slug(self) -> None:
        _, output = _run_mcp(self.root, ["--dry-run"])
        self.assertIn("Slug:", output)

    def test_dry_run_output_contains_target(self) -> None:
        _, output = _run_mcp(self.root, ["--dry-run"])
        self.assertIn("Target:", output)

    def test_dry_run_output_contains_would_write(self) -> None:
        _, output = _run_mcp(self.root, ["--dry-run"])
        self.assertIn("Would write:", output)

    def test_dry_run_json_exits_zero(self) -> None:
        code, _ = _run_mcp(self.root, ["--dry-run", "--json"])
        self.assertEqual(code, 0)

    def test_dry_run_json_is_valid(self) -> None:
        _, output = _run_mcp(self.root, ["--dry-run", "--json"])
        payload = json.loads(output)
        self.assertIsInstance(payload, dict)

    def test_dry_run_json_has_sequence(self) -> None:
        _, output = _run_mcp(self.root, ["--dry-run", "--json"])
        payload = json.loads(output)
        self.assertIn("sequence", payload)

    def test_dry_run_json_has_slug(self) -> None:
        _, output = _run_mcp(self.root, ["--dry-run", "--json"])
        payload = json.loads(output)
        self.assertIn("slug", payload)

    def test_dry_run_json_has_target_path(self) -> None:
        _, output = _run_mcp(self.root, ["--dry-run", "--json"])
        payload = json.loads(output)
        self.assertIsNotNone(payload["preview"]["target_path"])

    def test_dry_run_json_has_inferred_slice(self) -> None:
        _, output = _run_mcp(self.root, ["--dry-run", "--json"])
        payload = json.loads(output)
        self.assertIsNotNone(payload["frontier"]["inferred_slice"])

    def test_dry_run_json_has_would_write(self) -> None:
        _, output = _run_mcp(self.root, ["--dry-run", "--json"])
        payload = json.loads(output)
        self.assertIn("would_write", payload["preview"])


# ---------------------------------------------------------------------------
# CLI actual write
# ---------------------------------------------------------------------------

class CliWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _simple_project(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_write_exits_zero(self) -> None:
        code, _ = _run_mcp(self.root)
        self.assertEqual(code, 0)

    def test_write_creates_file(self) -> None:
        _run_mcp(self.root)
        coding_dir = self.root / "prompts" / "for_coding_agent"
        md_files = list(coding_dir.glob("*.md"))
        self.assertEqual(len(md_files), 1)

    def test_written_file_contains_claude_md(self) -> None:
        _run_mcp(self.root)
        coding_dir = self.root / "prompts" / "for_coding_agent"
        md_files = list(coding_dir.glob("*.md"))
        content = md_files[0].read_text(encoding="utf-8")
        self.assertIn("CLAUDE.md", content)

    def test_written_file_contains_readme_md(self) -> None:
        _run_mcp(self.root)
        coding_dir = self.root / "prompts" / "for_coding_agent"
        md_files = list(coding_dir.glob("*.md"))
        content = md_files[0].read_text(encoding="utf-8")
        self.assertIn("README.md", content)

    def test_write_uses_render_coding_prompt(self) -> None:
        plan = build_coding_prompt_plan(self.root)
        _run_mcp(self.root)
        coding_dir = self.root / "prompts" / "for_coding_agent"
        md_files = list(coding_dir.glob("*.md"))
        content = md_files[0].read_text(encoding="utf-8")
        self.assertEqual(content, plan.render.content)

    def test_write_json_has_write_result(self) -> None:
        _, output = _run_mcp(self.root, ["--json"])
        payload = json.loads(output)
        self.assertIn("write_result", payload)
        self.assertTrue(payload["write_result"]["wrote"])


# ---------------------------------------------------------------------------
# Existing file overwrite behavior
# ---------------------------------------------------------------------------

class OverwriteBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _simple_project(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_existing_file_without_overwrite_rejected(self) -> None:
        _run_mcp(self.root, ["--sequence", "42"])  # first write
        code, _ = _run_mcp(self.root, ["--sequence", "42"])  # same sequence
        self.assertNotEqual(code, 0)

    def test_existing_file_without_overwrite_preserves_content(self) -> None:
        _run_mcp(self.root, ["--sequence", "42"])
        coding_dir = self.root / "prompts" / "for_coding_agent"
        md_files = list(coding_dir.glob("*.md"))
        original_content = md_files[0].read_text(encoding="utf-8")
        _run_mcp(self.root, ["--sequence", "42"])  # same sequence, no overwrite
        self.assertEqual(
            md_files[0].read_text(encoding="utf-8"), original_content
        )

    def test_existing_file_with_overwrite_succeeds(self) -> None:
        _run_mcp(self.root, ["--sequence", "42"])
        code, _ = _run_mcp(self.root, ["--sequence", "42", "--overwrite"])
        self.assertEqual(code, 0)


# ---------------------------------------------------------------------------
# Missing project root
# ---------------------------------------------------------------------------

class MissingRootTests(unittest.TestCase):
    def test_missing_root_exits_nonzero(self) -> None:
        buf = StringIO()
        with redirect_stdout(buf):
            code = main(["make-coding-prompt", "/no/such/path/frutlups"])
        self.assertNotEqual(code, 0)


# ---------------------------------------------------------------------------
# Status/next commands still work
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
            _simple_project(root)
            buf = StringIO()
            with redirect_stdout(buf):
                code = main(["next", str(root)])
            self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
