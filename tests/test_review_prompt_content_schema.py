"""Tests for the M006-S03 review-prompt renderer and tightened
severity / verdict governance validation."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from frutlups.review_prompt_template import (
    REVIEW_SEVERITY_CATEGORIES,
    REVIEW_VERDICT_CHOICES,
    ReviewPromptRenderResult,
    ReviewPromptTemplate,
    render_review_prompt,
    validate_review_prompt_template,
)


def _valid_template(**overrides: object) -> ReviewPromptTemplate:
    defaults: dict[str, object] = dict(
        sequence=25,
        milestone_id="M006",
        slice_id="M006-S03",
        slug="frutlups_m006_s03_review_prompt_severity_verdict",
        title="Review Prompt Severity And Verdict",
        role_instructions="You are the review agent for frutlups.",
        required_reading=(
            "CLAUDE.md",
            "README.md",
            "08_pkg/CONTEXT.md",
        ),
        coding_prompt_path=(
            "prompts/for_coding_agent/"
            "025_frutlups_m006_s03_review_prompt_severity_verdict.md"
        ),
        self_report_path=(
            "05_governance/reviews/"
            "m006_s03_review_prompt_severity_verdict_self_report.md"
        ),
        review_output_path=(
            "05_governance/reviews/"
            "m006_s03_review_prompt_severity_verdict_review_report.md"
        ),
        expected_changed_files=(
            "08_pkg/src/frutlups/review_prompt_template.py",
            "08_pkg/tests/test_review_prompt_content_schema.py",
        ),
        verification_commands=(
            "python -m unittest discover -s tests",
            "python -m frutlups status ..",
        ),
        severity_guidance=(
            "blocker: correctness, safety, or scope failure",
            "major: missing required behavior or tests",
            "minor: small completeness gap",
            "nit: style or wording only",
        ),
        verdict_choices=("pass", "needs_work", "blocked", "override"),
        prior_review_paths=(
            "05_governance/reviews/"
            "m006_s02_corrective_evidence_never_raises_review_report.md",
        ),
        non_goals=(
            "do not write review prompt files",
            "do not parse verdicts",
        ),
        notes=("render only",),
    )
    defaults.update(overrides)
    return ReviewPromptTemplate(**defaults)  # type: ignore[arg-type]


class GovernanceConstantsTests(unittest.TestCase):
    def test_severity_categories(self) -> None:
        self.assertEqual(
            REVIEW_SEVERITY_CATEGORIES, ("blocker", "major", "minor", "nit")
        )

    def test_verdict_choices(self) -> None:
        self.assertEqual(
            REVIEW_VERDICT_CHOICES,
            ("pass", "needs_work", "blocked", "override"),
        )


class RenderResultShapeTests(unittest.TestCase):
    def test_to_dict_shape_for_valid_render(self) -> None:
        payload = render_review_prompt(_valid_template()).to_dict()
        self.assertEqual(set(payload.keys()), {"content", "valid", "errors"})
        self.assertIsInstance(payload["content"], str)
        self.assertIsInstance(payload["valid"], bool)
        self.assertIsInstance(payload["errors"], list)
        self.assertTrue(payload["valid"])

    def test_to_dict_shape_for_invalid_render(self) -> None:
        payload = render_review_prompt(
            _valid_template(sequence=0)
        ).to_dict()
        self.assertFalse(payload["valid"])
        self.assertEqual(payload["content"], "")
        self.assertGreater(len(payload["errors"]), 0)

    def test_result_is_frozen(self) -> None:
        result = render_review_prompt(_valid_template())
        with self.assertRaises(Exception):
            result.content = ""  # type: ignore[misc]

    def test_result_type_identity(self) -> None:
        self.assertIsInstance(
            render_review_prompt(_valid_template()),
            ReviewPromptRenderResult,
        )


class ValidRenderContainsRequiredSectionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.result = render_review_prompt(_valid_template())
        self.content = self.result.content

    def test_render_is_valid(self) -> None:
        self.assertTrue(self.result.valid, self.result.errors)
        self.assertEqual(self.result.errors, ())
        self.assertNotEqual(self.content, "")

    def test_title_line(self) -> None:
        first = self.content.splitlines()[0]
        self.assertTrue(
            first.startswith("# Review Prompt 025:"), first
        )
        self.assertIn("frutlups", first)
        self.assertIn("M006-S03", first)
        self.assertIn("Review Prompt Severity And Verdict", first)

    def test_all_required_section_headings_present(self) -> None:
        for heading in (
            "## Role",
            "## Pairing",
            "## Required Reading",
            "## Expected Changed Files",
            "## Verification Commands",
            "## Review Checks",
            "## Severity Guidance",
            "## Verdict Requirements",
            "## Non-Goals",
            "## llloom Integration Posture",
        ):
            with self.subTest(heading=heading):
                self.assertIn(heading, self.content)

    def test_role_section_contains_logical_reviewer_role(self) -> None:
        role_block = self.content.split("## Role", 1)[1].split(
            "## Pairing", 1
        )[0]
        self.assertIn("You are the review agent for frutlups.", role_block)
        self.assertIn("reviewer", role_block.lower())

    def test_pairing_section_names_milestone_slice_and_paths(self) -> None:
        block = self.content.split("## Pairing", 1)[1].split(
            "## Required Reading", 1
        )[0]
        self.assertIn("`M006`", block)
        self.assertIn("`M006-S03: Review Prompt Severity And Verdict`", block)
        self.assertIn(
            "prompts/for_coding_agent/"
            "025_frutlups_m006_s03_review_prompt_severity_verdict.md",
            block,
        )
        self.assertIn(
            "m006_s03_review_prompt_severity_verdict_self_report.md", block
        )
        self.assertIn(
            "m006_s03_review_prompt_severity_verdict_review_report.md", block
        )

    def test_pairing_includes_prior_review_paths_when_present(self) -> None:
        block = self.content.split("## Pairing", 1)[1].split(
            "## Required Reading", 1
        )[0]
        self.assertIn(
            "m006_s02_corrective_evidence_never_raises_review_report.md",
            block,
        )

    def test_required_reading_lists_baseline_and_caller_entries(self) -> None:
        block = self.content.split("## Required Reading", 1)[1].split(
            "## Expected Changed Files", 1
        )[0]
        self.assertIn("- `CLAUDE.md`", block)
        self.assertIn("- `README.md`", block)
        self.assertIn("- `08_pkg/CONTEXT.md`", block)

    def test_expected_changed_files_order_preserved(self) -> None:
        block = self.content.split("## Expected Changed Files", 1)[1].split(
            "## Verification Commands", 1
        )[0]
        first = block.index("review_prompt_template.py")
        second = block.index("test_review_prompt_content_schema.py")
        self.assertLess(first, second)

    def test_verification_commands_inside_fenced_powershell(self) -> None:
        block = self.content.split("## Verification Commands", 1)[1].split(
            "## Review Checks", 1
        )[0]
        self.assertIn("```powershell", block)
        self.assertIn("python -m unittest discover -s tests", block)
        self.assertIn("python -m frutlups status ..", block)
        # closing fence appears after the commands
        first = block.index("python -m unittest discover -s tests")
        closing = block.rindex("```")
        self.assertLess(first, closing)

    def test_review_checks_section_addresses_scope_evidence_regressions_non_goals(self) -> None:
        block = self.content.split("## Review Checks", 1)[1].split(
            "## Severity Guidance", 1
        )[0]
        lower = block.lower()
        for keyword in ("scope", "evidence", "regress", "non-goal"):
            with self.subTest(keyword=keyword):
                self.assertIn(keyword, lower)

    def test_severity_guidance_order_preserved(self) -> None:
        block = self.content.split("## Severity Guidance", 1)[1].split(
            "## Verdict Requirements", 1
        )[0]
        prev = -1
        for entry in (
            "blocker: correctness, safety, or scope failure",
            "major: missing required behavior or tests",
            "minor: small completeness gap",
            "nit: style or wording only",
        ):
            with self.subTest(entry=entry):
                index = block.index(entry)
                self.assertGreater(index, prev)
                prev = index

    def test_verdict_requirements_order_preserved(self) -> None:
        block = self.content.split("## Verdict Requirements", 1)[1].split(
            "## Non-Goals", 1
        )[0]
        prev = -1
        for verdict in ("pass", "needs_work", "blocked", "override"):
            with self.subTest(verdict=verdict):
                index = block.index(f"`{verdict}`")
                self.assertGreater(index, prev)
                prev = index

    def test_non_goals_listed_when_present(self) -> None:
        block = self.content.split("## Non-Goals", 1)[1].split(
            "## llloom Integration Posture", 1
        )[0]
        self.assertIn("do not write review prompt files", block)
        self.assertIn("do not parse verdicts", block)

    def test_llloom_posture_present(self) -> None:
        block = self.content.split("## llloom Integration Posture", 1)[1]
        self.assertIn("llloom_operating_model.md", block)
        self.assertIn("memory", block.lower())

    def test_notes_section_present_when_notes_non_empty(self) -> None:
        self.assertIn("## Notes", self.content)
        self.assertIn("render only", self.content)

    def test_content_ends_with_single_newline(self) -> None:
        self.assertTrue(self.content.endswith("\n"))
        self.assertFalse(self.content.endswith("\n\n"))


class OptionalSectionOmissionTests(unittest.TestCase):
    def test_notes_section_omitted_when_notes_empty(self) -> None:
        content = render_review_prompt(_valid_template(notes=())).content
        self.assertNotIn("## Notes", content)

    def test_prior_review_paths_omitted_when_empty(self) -> None:
        content = render_review_prompt(
            _valid_template(prior_review_paths=())
        ).content
        self.assertNotIn("Prior review reports:", content)

    def test_non_goals_section_omitted_when_empty(self) -> None:
        content = render_review_prompt(_valid_template(non_goals=())).content
        self.assertNotIn("## Non-Goals", content)


class DeterminismTests(unittest.TestCase):
    def test_same_template_renders_same_content(self) -> None:
        a = render_review_prompt(_valid_template()).content
        b = render_review_prompt(_valid_template()).content
        self.assertEqual(a, b)

    def test_no_machine_local_paths(self) -> None:
        content = render_review_prompt(_valid_template()).content
        for forbidden in ("C:\\Users", "/Users/", "/home/", "/tmp/"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, content)

    def test_no_timestamps_or_uuids(self) -> None:
        content = render_review_prompt(_valid_template()).content
        for forbidden in ("2026-", "UTC", "T00:00:00"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, content)


class InvalidRenderTests(unittest.TestCase):
    def test_invalid_sequence_yields_empty_content(self) -> None:
        result = render_review_prompt(_valid_template(sequence=0))
        self.assertFalse(result.valid)
        self.assertEqual(result.content, "")
        self.assertIn("sequence must be a positive integer", result.errors)

    def test_missing_baseline_required_reading_yields_empty_content(self) -> None:
        result = render_review_prompt(
            _valid_template(required_reading=("README.md", "x.md"))
        )
        self.assertFalse(result.valid)
        self.assertEqual(result.content, "")
        self.assertIn("required_reading must include CLAUDE.md", result.errors)

    def test_malformed_collection_does_not_raise(self) -> None:
        try:
            result = render_review_prompt(
                _valid_template(severity_guidance=42)  # type: ignore[arg-type]
            )
        except Exception as exc:  # pragma: no cover - guard rail
            self.fail(f"render raised {type(exc).__name__}: {exc}")
        self.assertFalse(result.valid)
        self.assertEqual(result.content, "")


class SeverityGuidanceGovernanceTests(unittest.TestCase):
    """The validator must require an entry for each of the four
    severity categories: blocker, major, minor, nit."""

    def test_missing_blocker_reported(self) -> None:
        errors = validate_review_prompt_template(
            _valid_template(
                severity_guidance=(
                    "major: m",
                    "minor: n",
                    "nit: z",
                )
            )
        )
        self.assertIn(
            "severity_guidance must include a blocker entry", errors
        )

    def test_missing_major_reported(self) -> None:
        errors = validate_review_prompt_template(
            _valid_template(
                severity_guidance=(
                    "blocker: b",
                    "minor: n",
                    "nit: z",
                )
            )
        )
        self.assertIn(
            "severity_guidance must include a major entry", errors
        )

    def test_missing_minor_reported(self) -> None:
        errors = validate_review_prompt_template(
            _valid_template(
                severity_guidance=(
                    "blocker: b",
                    "major: m",
                    "nit: z",
                )
            )
        )
        self.assertIn(
            "severity_guidance must include a minor entry", errors
        )

    def test_missing_nit_reported(self) -> None:
        errors = validate_review_prompt_template(
            _valid_template(
                severity_guidance=(
                    "blocker: b",
                    "major: m",
                    "minor: n",
                )
            )
        )
        self.assertIn(
            "severity_guidance must include a nit entry", errors
        )

    def test_category_matching_is_case_insensitive(self) -> None:
        # Capitalised category prefixes are still counted.
        errors = validate_review_prompt_template(
            _valid_template(
                severity_guidance=(
                    "BLOCKER: b",
                    "Major: m",
                    "MINOR: n",
                    "Nit: z",
                )
            )
        )
        self.assertEqual(errors, ())

    def test_severity_without_colon_does_not_count(self) -> None:
        # A bare word `blocker` without `:` does not satisfy the
        # prefix rule.
        errors = validate_review_prompt_template(
            _valid_template(
                severity_guidance=(
                    "blocker is a kind of finding",
                    "major: m",
                    "minor: n",
                    "nit: z",
                )
            )
        )
        self.assertIn(
            "severity_guidance must include a blocker entry", errors
        )

    def test_non_iterable_severity_does_not_fire_category_check(self) -> None:
        # Non-iterable severity must surface the collection error
        # but must not also fire four "must include a <cat> entry"
        # errors (the prefix scan only runs on iterable input).
        errors = validate_review_prompt_template(
            _valid_template(severity_guidance=42)  # type: ignore[arg-type]
        )
        self.assertIn(
            "severity_guidance must be a tuple or list of non-empty strings",
            errors,
        )
        for category in REVIEW_SEVERITY_CATEGORIES:
            with self.subTest(category=category):
                self.assertNotIn(
                    f"severity_guidance must include a {category} entry",
                    errors,
                )


class VerdictChoiceGovernanceTests(unittest.TestCase):
    """The validator must require every canonical verdict choice."""

    def test_missing_pass_reported(self) -> None:
        errors = validate_review_prompt_template(
            _valid_template(verdict_choices=("needs_work", "blocked", "override"))
        )
        self.assertIn("verdict_choices must include pass", errors)

    def test_missing_needs_work_reported(self) -> None:
        errors = validate_review_prompt_template(
            _valid_template(verdict_choices=("pass", "blocked", "override"))
        )
        self.assertIn("verdict_choices must include needs_work", errors)

    def test_missing_blocked_reported(self) -> None:
        errors = validate_review_prompt_template(
            _valid_template(verdict_choices=("pass", "needs_work", "override"))
        )
        self.assertIn("verdict_choices must include blocked", errors)

    def test_missing_override_reported(self) -> None:
        errors = validate_review_prompt_template(
            _valid_template(verdict_choices=("pass", "needs_work", "blocked"))
        )
        self.assertIn("verdict_choices must include override", errors)

    def test_verdict_matching_is_case_insensitive(self) -> None:
        errors = validate_review_prompt_template(
            _valid_template(
                verdict_choices=("PASS", "Needs_Work", "Blocked", "OVERRIDE")
            )
        )
        self.assertEqual(errors, ())

    def test_non_iterable_verdict_does_not_fire_missing_choice(self) -> None:
        errors = validate_review_prompt_template(
            _valid_template(verdict_choices=42)  # type: ignore[arg-type]
        )
        self.assertIn(
            "verdict_choices must be a tuple or list of non-empty strings",
            errors,
        )
        for verdict in REVIEW_VERDICT_CHOICES:
            with self.subTest(verdict=verdict):
                self.assertNotIn(
                    f"verdict_choices must include {verdict}", errors
                )


class RendererDoesNotWriteFilesTests(unittest.TestCase):
    def test_render_does_not_create_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = sorted(p.relative_to(root) for p in root.rglob("*"))
            render_review_prompt(_valid_template())
            after = sorted(p.relative_to(root) for p in root.rglob("*"))
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
