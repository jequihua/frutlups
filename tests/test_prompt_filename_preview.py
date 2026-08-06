"""Tests for deterministic filename and dry-run preview helpers."""

import unittest

from frutlups.prompt_template import (
    CODING_PROMPT_DIR,
    CodingPromptPreview,
    CodingPromptTemplate,
    coding_prompt_filename,
    format_prompt_sequence,
    preview_coding_prompt,
)


def _valid_template(**overrides: object) -> CodingPromptTemplate:
    defaults: dict[str, object] = dict(
        sequence=12,
        milestone_id="M004",
        slice_id="M004-S02",
        slug="frutlups_m004_s02_prompt_filename_preview",
        title="Prompt Filename Preview",
        role_instructions="You are the coding agent for frutlups.",
        required_reading=("CLAUDE.md", "README.md"),
        scope_paths=("08_pkg/src/frutlups/",),
        non_goals=("do not render markdown",),
        definition_of_done=("preview exists",),
        verification_commands=("python -m unittest discover -s tests",),
        self_report_path=(
            "05_governance/reviews/"
            "m004_s02_prompt_filename_preview_self_report.md"
        ),
    )
    defaults.update(overrides)
    return CodingPromptTemplate(**defaults)  # type: ignore[arg-type]


class FormatPromptSequenceTests(unittest.TestCase):
    def test_zero_pads_to_three_digits(self) -> None:
        self.assertEqual(format_prompt_sequence(1), "001")
        self.assertEqual(format_prompt_sequence(12), "012")
        self.assertEqual(format_prompt_sequence(100), "100")
        self.assertEqual(format_prompt_sequence(999), "999")

    def test_zero_and_negative_return_empty(self) -> None:
        for bad in (0, -1, -999):
            with self.subTest(sequence=bad):
                self.assertEqual(format_prompt_sequence(bad), "")

    def test_non_int_returns_empty(self) -> None:
        for bad in ("12", None, 1.5, [1], (1,), {"sequence": 1}):
            with self.subTest(sequence=bad):
                self.assertEqual(format_prompt_sequence(bad), "")

    def test_bool_returns_empty(self) -> None:
        # ``True``/`False` are technically int subclasses but should not
        # be accepted as sequence numbers.
        self.assertEqual(format_prompt_sequence(True), "")
        self.assertEqual(format_prompt_sequence(False), "")

    def test_never_raises(self) -> None:
        for value in (0, -1, "x", None, 1.5, True, [], {}, object()):
            format_prompt_sequence(value)


class CodingPromptFilenameTests(unittest.TestCase):
    def test_builds_filename_from_sequence_and_slug(self) -> None:
        template = _valid_template(sequence=1, slug="m002_s01_roadmap_parser")
        self.assertEqual(
            coding_prompt_filename(template),
            "001_m002_s01_roadmap_parser.md",
        )

    def test_uses_three_digit_zero_padded_sequence(self) -> None:
        template = _valid_template(sequence=12, slug="preview")
        self.assertEqual(coding_prompt_filename(template), "012_preview.md")

    def test_returns_empty_string_when_slug_is_empty(self) -> None:
        template = _valid_template(slug="")
        self.assertEqual(coding_prompt_filename(template), "")

    def test_returns_empty_string_when_slug_is_whitespace(self) -> None:
        template = _valid_template(slug="   ")
        self.assertEqual(coding_prompt_filename(template), "")

    def test_returns_empty_string_when_sequence_is_invalid(self) -> None:
        for bad in (0, -1, "1", None):
            with self.subTest(sequence=bad):
                template = _valid_template(sequence=bad)  # type: ignore[arg-type]
                self.assertEqual(coding_prompt_filename(template), "")

    def test_does_not_normalise_human_slug(self) -> None:
        # Slugs are used verbatim (after stripping surrounding
        # whitespace) so author mistakes are visible.
        template = _valid_template(slug="UPPER_case-Slug")
        self.assertEqual(
            coding_prompt_filename(template), "012_UPPER_case-Slug.md"
        )

    def test_strips_only_surrounding_whitespace(self) -> None:
        template = _valid_template(slug="  preview  ")
        self.assertEqual(coding_prompt_filename(template), "012_preview.md")

    def test_never_raises_for_non_string_slug(self) -> None:
        # The validator will flag this; the filename helper must not
        # raise on its own.
        template = _valid_template(slug=42)  # type: ignore[arg-type]
        self.assertEqual(coding_prompt_filename(template), "")


class CodingPromptPreviewToDictTests(unittest.TestCase):
    def test_to_dict_for_valid_template(self) -> None:
        preview = preview_coding_prompt(_valid_template())

        self.assertEqual(
            preview.to_dict(),
            {
                "kind": "coding",
                "sequence": 12,
                "sequence_formatted": "012",
                "filename": "012_frutlups_m004_s02_prompt_filename_preview.md",
                "target_path": (
                    "prompts/for_coding_agent/"
                    "012_frutlups_m004_s02_prompt_filename_preview.md"
                ),
                "valid": True,
                "errors": [],
                "would_write": True,
                "wrote": False,
            },
        )

    def test_to_dict_uses_plain_python_types(self) -> None:
        payload = preview_coding_prompt(_valid_template()).to_dict()
        self.assertIsInstance(payload["kind"], str)
        self.assertIsInstance(payload["sequence"], int)
        self.assertIsInstance(payload["sequence_formatted"], str)
        self.assertIsInstance(payload["filename"], str)
        self.assertIsInstance(payload["target_path"], str)
        self.assertIsInstance(payload["valid"], bool)
        self.assertIsInstance(payload["errors"], list)
        self.assertIsInstance(payload["would_write"], bool)
        self.assertIsInstance(payload["wrote"], bool)


class PreviewCodingPromptTests(unittest.TestCase):
    def test_valid_template_yields_writable_preview(self) -> None:
        preview = preview_coding_prompt(_valid_template())

        self.assertEqual(preview.kind, "coding")
        self.assertEqual(preview.sequence, 12)
        self.assertEqual(preview.sequence_formatted, "012")
        self.assertEqual(
            preview.filename, "012_frutlups_m004_s02_prompt_filename_preview.md"
        )
        self.assertEqual(
            preview.target_path,
            (
                "prompts/for_coding_agent/"
                "012_frutlups_m004_s02_prompt_filename_preview.md"
            ),
        )
        self.assertTrue(preview.valid)
        self.assertEqual(preview.errors, ())
        self.assertTrue(preview.would_write)
        self.assertFalse(preview.wrote)

    def test_target_path_uses_repo_relative_coding_directory(self) -> None:
        preview = preview_coding_prompt(_valid_template())
        self.assertTrue(
            preview.target_path.startswith(f"{CODING_PROMPT_DIR}/"),
            preview.target_path,
        )

    def test_invalid_sequence_surfaces_validation_errors(self) -> None:
        preview = preview_coding_prompt(_valid_template(sequence=0))

        self.assertFalse(preview.valid)
        self.assertIn("sequence must be a positive integer", preview.errors)
        self.assertFalse(preview.would_write)
        self.assertFalse(preview.wrote)
        self.assertEqual(preview.sequence, 0)
        self.assertEqual(preview.sequence_formatted, "")
        self.assertEqual(preview.filename, "")
        self.assertEqual(preview.target_path, "")

    def test_empty_slug_surfaces_validation_errors(self) -> None:
        preview = preview_coding_prompt(_valid_template(slug=""))

        self.assertFalse(preview.valid)
        self.assertIn("slug must be a non-empty string", preview.errors)
        self.assertFalse(preview.would_write)
        self.assertFalse(preview.wrote)
        self.assertEqual(preview.filename, "")
        self.assertEqual(preview.target_path, "")

    def test_invalid_collection_does_not_raise(self) -> None:
        # The corrective M004-S01 validator never raises; the preview
        # must inherit that guarantee.
        preview = preview_coding_prompt(
            _valid_template(required_reading=42)  # type: ignore[arg-type]
        )

        self.assertFalse(preview.valid)
        self.assertIn(
            "required_reading must be a tuple or list of non-empty strings",
            preview.errors,
        )
        self.assertFalse(preview.would_write)

    def test_non_int_sequence_yields_none_sequence(self) -> None:
        preview = preview_coding_prompt(_valid_template(sequence="12"))  # type: ignore[arg-type]
        self.assertIsNone(preview.sequence)
        self.assertEqual(preview.sequence_formatted, "")
        self.assertEqual(preview.filename, "")
        self.assertFalse(preview.would_write)

    def test_wrote_is_always_false_for_dry_run(self) -> None:
        for overrides in (
            {},  # valid
            {"sequence": 0},
            {"slug": ""},
            {"required_reading": ()},  # type: ignore[arg-type]
        ):
            with self.subTest(overrides=overrides):
                preview = preview_coding_prompt(_valid_template(**overrides))
                self.assertFalse(preview.wrote)

    def test_preview_is_pure_no_filesystem_required(self) -> None:
        # The probe constructs a template referencing a non-existent
        # repo path; the preview should still succeed because it does
        # not touch the filesystem.
        template = _valid_template(
            scope_paths=("/does/not/exist/path/",),
            required_reading=("/does/not/exist/CLAUDE.md",),
        )
        preview = preview_coding_prompt(template)
        self.assertTrue(preview.valid)
        self.assertTrue(preview.would_write)


class PreviewFrozenInvariantTests(unittest.TestCase):
    def test_preview_is_frozen(self) -> None:
        preview = preview_coding_prompt(_valid_template())
        with self.assertRaises(Exception):
            preview.wrote = True  # type: ignore[misc]


class SequenceUpperBoundTests(unittest.TestCase):
    """Regression: the project convention is exactly three digits for
    sequences 1..999. See M004-S02 review report."""

    def test_format_prompt_sequence_999_still_returns_three_digit_string(self) -> None:
        self.assertEqual(format_prompt_sequence(999), "999")

    def test_format_prompt_sequence_returns_empty_for_1000_and_above(self) -> None:
        for bad in (1000, 1001, 1234, 9999):
            with self.subTest(sequence=bad):
                self.assertEqual(format_prompt_sequence(bad), "")

    def test_coding_prompt_filename_empty_for_sequence_above_999(self) -> None:
        for bad in (1000, 1001, 9999):
            with self.subTest(sequence=bad):
                template = _valid_template(sequence=bad, slug="too_high")
                self.assertEqual(coding_prompt_filename(template), "")

    def test_validator_reports_sequence_above_999(self) -> None:
        from frutlups.prompt_template import (
            MAX_PROMPT_SEQUENCE,
            validate_coding_prompt_template,
        )

        errors = validate_coding_prompt_template(_valid_template(sequence=1000))
        self.assertIn(f"sequence must be at most {MAX_PROMPT_SEQUENCE}", errors)
        # And the prior "positive integer" message must NOT also fire for a
        # positive but too-large sequence (otherwise the user would see two
        # contradictory errors).
        self.assertNotIn("sequence must be a positive integer", errors)

    def test_validator_still_reports_zero_and_negative_with_existing_message(self) -> None:
        from frutlups.prompt_template import validate_coding_prompt_template

        for bad in (0, -1, -999):
            with self.subTest(sequence=bad):
                errors = validate_coding_prompt_template(
                    _valid_template(sequence=bad)
                )
                self.assertIn("sequence must be a positive integer", errors)

    def test_preview_is_non_writable_for_sequence_above_999(self) -> None:
        from frutlups.prompt_template import MAX_PROMPT_SEQUENCE

        for bad in (1000, 1001, 9999):
            with self.subTest(sequence=bad):
                preview = preview_coding_prompt(
                    _valid_template(sequence=bad, slug="too_high")
                )
                self.assertFalse(preview.valid)
                self.assertEqual(preview.filename, "")
                self.assertEqual(preview.target_path, "")
                self.assertFalse(preview.would_write)
                self.assertFalse(preview.wrote)
                self.assertEqual(preview.sequence_formatted, "")
                # The integer is preserved for debugging visibility even
                # though the sequence is out of bounds.
                self.assertEqual(preview.sequence, bad)
                self.assertIn(
                    f"sequence must be at most {MAX_PROMPT_SEQUENCE}",
                    preview.errors,
                )


if __name__ == "__main__":
    unittest.main()
