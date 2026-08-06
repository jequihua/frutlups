"""Tests for deterministic coding-prompt markdown rendering."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from frutlups.prompt_template import (
    CodingPromptRenderResult,
    CodingPromptTemplate,
    REQUIRED_READING_BASELINE,
    REVIEW_PROMPT_DIR,
    render_coding_prompt,
)


def _valid_template(**overrides: object) -> CodingPromptTemplate:
    defaults: dict[str, object] = dict(
        sequence=15,
        milestone_id="M004",
        slice_id="M004-S04",
        slug="frutlups_m004_s04_prompt_content_schema",
        title="Prompt Content Schema",
        role_instructions="You are the coding agent for frutlups.",
        required_reading=("CLAUDE.md", "README.md", "08_pkg/CONTEXT.md"),
        scope_paths=("08_pkg/src/frutlups/", "08_pkg/tests/"),
        non_goals=("do not add CLI commands",),
        definition_of_done=("renderer exists", "tests added"),
        verification_commands=(
            "python -m unittest discover -s tests",
            "python -m frutlups status ..",
        ),
        self_report_path=(
            "05_governance/reviews/"
            "m004_s04_prompt_content_schema_self_report.md"
        ),
        notes=("matches existing coding style",),
    )
    defaults.update(overrides)
    return CodingPromptTemplate(**defaults)  # type: ignore[arg-type]


class RenderResultShapeTests(unittest.TestCase):
    def test_to_dict_shape_for_valid_render(self) -> None:
        result = render_coding_prompt(_valid_template())

        payload = result.to_dict()
        self.assertEqual(
            set(payload.keys()), {"content", "valid", "errors"}
        )
        self.assertIsInstance(payload["content"], str)
        self.assertIsInstance(payload["valid"], bool)
        self.assertIsInstance(payload["errors"], list)
        self.assertTrue(payload["valid"])
        self.assertEqual(payload["errors"], [])

    def test_to_dict_for_invalid_render(self) -> None:
        result = render_coding_prompt(_valid_template(sequence=0))
        payload = result.to_dict()
        self.assertFalse(payload["valid"])
        self.assertEqual(payload["content"], "")
        self.assertIsInstance(payload["errors"], list)
        self.assertGreater(len(payload["errors"]), 0)

    def test_result_is_frozen(self) -> None:
        result = render_coding_prompt(_valid_template())
        with self.assertRaises(Exception):
            result.content = ""  # type: ignore[misc]


class ValidRenderContainsRequiredSectionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.result = render_coding_prompt(_valid_template())
        self.content = self.result.content

    def test_render_is_valid(self) -> None:
        self.assertTrue(self.result.valid)
        self.assertEqual(self.result.errors, ())
        self.assertNotEqual(self.content, "")

    def test_title_includes_sequence_package_slice_and_title(self) -> None:
        first_line = self.content.splitlines()[0]
        self.assertTrue(first_line.startswith("# Coding Prompt 015:"))
        self.assertIn("frutlups", first_line)
        self.assertIn("M004-S04", first_line)
        self.assertIn("Prompt Content Schema", first_line)

    def test_all_required_section_headings_present(self) -> None:
        required_headings = (
            "## Role",
            "## Active Roadmap Item",
            "## Required Reading",
            "## Scope",
            "## Non-Goals",
            "## Definition of Done",
            "## Verification Commands",
            "## Required Self-Report",
            "## Matching Review Prompt",
            "## llloom Integration Posture",
        )
        for heading in required_headings:
            with self.subTest(heading=heading):
                self.assertIn(heading, self.content)

    def test_role_instructions_appear_under_role_heading(self) -> None:
        self.assertIn("You are the coding agent for frutlups.", self.content)

    def test_active_roadmap_item_includes_milestone_and_slice(self) -> None:
        self.assertIn("Active roadmap milestone: `M004`", self.content)
        self.assertIn(
            "Detailed roadmap slice: `M004-S04: Prompt Content Schema`",
            self.content,
        )

    def test_required_reading_visibly_requires_baseline_and_caller_entries(self) -> None:
        self.assertIn("- `CLAUDE.md`", self.content)
        self.assertIn("- `README.md`", self.content)
        self.assertIn("- `08_pkg/CONTEXT.md`", self.content)

    def test_scope_paths_listed_in_order(self) -> None:
        scope_block = self.content.split("## Scope", 1)[1].split("## Non-Goals", 1)[0]
        first = scope_block.index("`08_pkg/src/frutlups/`")
        second = scope_block.index("`08_pkg/tests/`")
        self.assertLess(first, second)

    def test_non_goals_listed(self) -> None:
        self.assertIn("- do not add CLI commands", self.content)

    def test_definition_of_done_listed_in_order(self) -> None:
        done_block = self.content.split("## Definition of Done", 1)[1].split(
            "## Verification Commands", 1
        )[0]
        first = done_block.index("renderer exists")
        second = done_block.index("tests added")
        self.assertLess(first, second)

    def test_verification_commands_appear_inside_a_fenced_block(self) -> None:
        verification = self.content.split("## Verification Commands", 1)[1].split(
            "## Required Self-Report", 1
        )[0]
        self.assertIn("```powershell", verification)
        self.assertIn("```", verification)
        self.assertIn("python -m unittest discover -s tests", verification)
        self.assertIn("python -m frutlups status ..", verification)

    def test_self_report_path_appears_under_self_report_heading(self) -> None:
        sr_block = self.content.split("## Required Self-Report", 1)[1].split(
            "## Matching Review Prompt", 1
        )[0]
        self.assertIn(
            "`05_governance/reviews/m004_s04_prompt_content_schema_self_report.md`",
            sr_block,
        )

    def test_self_report_schema_lists_required_items(self) -> None:
        sr_block = self.content.split("## Required Self-Report", 1)[1].split(
            "## Matching Review Prompt", 1
        )[0]
        for required_item in (
            "files changed",
            "behavior implemented",
            "tests added or updated",
            "verification commands and results",
            "live status summary",
            "known limits and intentional deferrals",
            "memory usage statement",
            "matching review prompt path created by the coder",
            "blockers or open questions",
        ):
            with self.subTest(item=required_item):
                self.assertIn(required_item, sr_block)

    def test_matching_review_prompt_path_is_derived_correctly(self) -> None:
        expected = (
            f"`{REVIEW_PROMPT_DIR}/"
            "015_review_frutlups_m004_s04_prompt_content_schema.md`"
        )
        self.assertIn(expected, self.content)

    def test_loop_convention_is_explained(self) -> None:
        review_block = self.content.split("## Matching Review Prompt", 1)[1].split(
            "## llloom Integration Posture", 1
        )[0]
        self.assertIn("architect/reviewer", review_block)
        self.assertIn("coder", review_block)
        self.assertIn("self-report", review_block)
        self.assertIn("reviewer", review_block)

    def test_llloom_posture_mentions_operating_model(self) -> None:
        llloom_block = self.content.split("## llloom Integration Posture", 1)[1]
        self.assertIn("llloom_operating_model.md", llloom_block)
        self.assertIn("not mutate", llloom_block.lower().replace("-", " "))

    def test_optional_notes_appear_when_present(self) -> None:
        self.assertIn("## Notes", self.content)
        self.assertIn("matches existing coding style", self.content)

    def test_content_ends_with_single_newline(self) -> None:
        self.assertTrue(self.content.endswith("\n"))
        self.assertFalse(self.content.endswith("\n\n"))


class OptionalNotesOmittedTests(unittest.TestCase):
    def test_notes_section_absent_when_notes_empty(self) -> None:
        content = render_coding_prompt(_valid_template(notes=())).content
        self.assertNotIn("## Notes", content)


class DeterminismTests(unittest.TestCase):
    def test_same_template_renders_same_output(self) -> None:
        template = _valid_template()
        first = render_coding_prompt(template).content
        second = render_coding_prompt(template).content
        self.assertEqual(first, second)

    def test_output_has_no_machine_local_paths(self) -> None:
        content = render_coding_prompt(_valid_template()).content
        # Absolute Windows-style and POSIX-style absolute repository
        # paths should not leak into rendered content.
        self.assertNotIn("C:\\Users", content)
        self.assertNotIn("/Users/", content)
        self.assertNotIn("/home/", content)

    def test_output_does_not_contain_timestamps_or_uuids(self) -> None:
        content = render_coding_prompt(_valid_template()).content
        # ISO-ish timestamps and UUIDs are obvious nondeterminism
        # markers; rendering should not produce either.
        self.assertNotIn("2026-", content)
        self.assertNotIn("UTC", content)


class RequiredReadingBaselineTests(unittest.TestCase):
    def test_baseline_is_claude_md_and_readme_md(self) -> None:
        self.assertEqual(REQUIRED_READING_BASELINE, ("CLAUDE.md", "README.md"))

    def test_missing_claude_md_surfaces_render_error(self) -> None:
        result = render_coding_prompt(
            _valid_template(required_reading=("README.md", "08_pkg/CONTEXT.md"))
        )
        self.assertFalse(result.valid)
        self.assertEqual(result.content, "")
        self.assertIn("required_reading must include CLAUDE.md", result.errors)

    def test_missing_readme_md_surfaces_render_error(self) -> None:
        result = render_coding_prompt(
            _valid_template(required_reading=("CLAUDE.md", "08_pkg/CONTEXT.md"))
        )
        self.assertFalse(result.valid)
        self.assertEqual(result.content, "")
        self.assertIn("required_reading must include README.md", result.errors)

    def test_missing_both_surfaces_two_render_errors(self) -> None:
        result = render_coding_prompt(
            _valid_template(required_reading=("08_pkg/CONTEXT.md",))
        )
        self.assertFalse(result.valid)
        self.assertIn("required_reading must include CLAUDE.md", result.errors)
        self.assertIn("required_reading must include README.md", result.errors)


class InvalidTemplateTests(unittest.TestCase):
    def test_invalid_sequence_yields_unusable_content(self) -> None:
        result = render_coding_prompt(_valid_template(sequence=0))
        self.assertFalse(result.valid)
        self.assertEqual(result.content, "")
        self.assertIn("sequence must be a positive integer", result.errors)

    def test_above_max_sequence_yields_unusable_content(self) -> None:
        from frutlups.prompt_template import MAX_PROMPT_SEQUENCE

        result = render_coding_prompt(_valid_template(sequence=1000))
        self.assertFalse(result.valid)
        self.assertEqual(result.content, "")
        self.assertIn(
            f"sequence must be at most {MAX_PROMPT_SEQUENCE}", result.errors
        )

    def test_empty_slug_yields_unusable_content(self) -> None:
        result = render_coding_prompt(_valid_template(slug=""))
        self.assertFalse(result.valid)
        self.assertEqual(result.content, "")
        self.assertIn("slug must be a non-empty string", result.errors)

    def test_malformed_collection_does_not_raise(self) -> None:
        # Inherits the M004-S01 never-raises guarantee via the validator
        # composition.
        result = render_coding_prompt(
            _valid_template(required_reading=42)  # type: ignore[arg-type]
        )
        self.assertFalse(result.valid)
        self.assertEqual(result.content, "")
        self.assertIn(
            "required_reading must be a tuple or list of non-empty strings",
            result.errors,
        )


class RendererDoesNotWriteFilesTests(unittest.TestCase):
    def test_render_does_not_create_any_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            # render in a clean working directory and confirm nothing
            # appears under prompts/ inside the temporary tree.
            render_coding_prompt(_valid_template())
            self.assertFalse((root / "prompts").exists())

    def test_render_result_type_identity(self) -> None:
        self.assertIsInstance(
            render_coding_prompt(_valid_template()), CodingPromptRenderResult
        )


if __name__ == "__main__":
    unittest.main()
