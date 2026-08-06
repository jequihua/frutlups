"""Tests for the typed self-report content validation surface."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from frutlups.prompt_template import CodingPromptTemplate
from frutlups.self_report import (
    ParsedSelfReport,
    SELF_REPORT_REQUIRED_FIELDS,
    SelfReportLocationCommand,
    SelfReportSchema,
    SelfReportSection,
    SelfReportValidationCommand,
    SelfReportValidationResult,
    default_self_report_schema,
    validate_expected_self_report,
)


VALID_REPORT_BODY = """\
# Self Report

## Files Changed

- path: x.py

## Behavior Implemented

implemented parser

## Tests Added Or Updated

unit tests

## Verification Commands And Results

python -m unittest discover -s tests: passed

## Live Status Summary

ok

## Known Limits And Intentional Deferrals

later slices

## Memory Usage

memory: not_used

## Matching Review Prompt Path Created By The Coder

prompts/for_review_agent/019_review_frutlups_m005_s03_self_report_content_validation.md

## Blockers Or Open Questions

none
"""


def _valid_template(**overrides: object) -> CodingPromptTemplate:
    defaults: dict[str, object] = dict(
        sequence=19,
        milestone_id="M005",
        slice_id="M005-S03",
        slug="frutlups_m005_s03_self_report_content_validation",
        title="Self-Report Content Validation",
        role_instructions="You are the coding agent for frutlups.",
        required_reading=("CLAUDE.md", "README.md"),
        scope_paths=("08_pkg/src/frutlups/",),
        non_goals=("do not generate review prompts",),
        definition_of_done=("validator exists",),
        verification_commands=("python -m unittest discover -s tests",),
        self_report_path="05_governance/reviews/report.md",
    )
    defaults.update(overrides)
    return CodingPromptTemplate(**defaults)  # type: ignore[arg-type]


def _write_report(root: Path, body: str = VALID_REPORT_BODY) -> Path:
    target = root / "05_governance" / "reviews" / "report.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return target


def _command(
    root: Path,
    *,
    template: CodingPromptTemplate | None = None,
    schema: SelfReportSchema | None = None,
) -> SelfReportValidationCommand:
    location = SelfReportLocationCommand(
        project_root=root,
        template=template if template is not None else _valid_template(),
    )
    if schema is None:
        return SelfReportValidationCommand(location=location)
    return SelfReportValidationCommand(location=location, schema=schema)


class ShapeAndSerializationTests(unittest.TestCase):
    def test_section_to_dict_shape(self) -> None:
        self.assertEqual(
            SelfReportSection(heading="X", body="y").to_dict(),
            {"heading": "X", "body": "y"},
        )

    def test_parsed_to_dict_shape(self) -> None:
        parsed = ParsedSelfReport(
            path="report.md",
            sections=(SelfReportSection(heading="A", body=""),),
        )
        payload = parsed.to_dict()
        self.assertEqual(set(payload.keys()), {"path", "sections"})
        self.assertEqual(payload["sections"], [{"heading": "A", "body": ""}])

    def test_validation_command_is_frozen(self) -> None:
        with TemporaryDirectory() as tmp:
            command = _command(Path(tmp))
            with self.assertRaises(Exception):
                command.location = command.location  # type: ignore[misc]

    def test_validation_result_is_frozen(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_report(root)
            result = validate_expected_self_report(_command(root))
            with self.assertRaises(Exception):
                result.valid = False  # type: ignore[misc]

    def test_result_to_dict_shape_for_valid_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_report(root)
            result = validate_expected_self_report(_command(root))

        payload = result.to_dict()
        self.assertEqual(
            set(payload.keys()),
            {"location", "parsed", "valid", "errors"},
        )
        self.assertIsInstance(payload["location"], dict)
        self.assertIsInstance(payload["parsed"], dict)
        self.assertIsInstance(payload["valid"], bool)
        self.assertIsInstance(payload["errors"], list)

    def test_result_to_dict_with_no_parse(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            # missing file
            result = validate_expected_self_report(_command(root))
        payload = result.to_dict()
        self.assertFalse(payload["valid"])
        self.assertIsNone(payload["parsed"])


class HappyPathTests(unittest.TestCase):
    def test_valid_report_yields_valid_true_and_no_errors(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_report(root)
            result = validate_expected_self_report(_command(root))

        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.errors, ())
        self.assertIsNotNone(result.parsed)
        self.assertTrue(result.location.exists)
        self.assertTrue(result.location.is_file)

    def test_parsed_sections_preserve_order(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_report(root)
            result = validate_expected_self_report(_command(root))

        headings = [section.heading for section in result.parsed.sections]
        # The valid report uses these top-level sections in this order.
        expected_first_five = [
            "Self Report",
            "Files Changed",
            "Behavior Implemented",
            "Tests Added Or Updated",
            "Verification Commands And Results",
        ]
        self.assertEqual(headings[: len(expected_first_five)], expected_first_five)

    def test_section_bodies_have_leading_and_trailing_blank_lines_stripped(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_report(root)
            result = validate_expected_self_report(_command(root))

        files_changed = next(
            section for section in result.parsed.sections
            if section.heading == "Files Changed"
        )
        self.assertEqual(files_changed.body, "- path: x.py")


class MissingOrBadTargetTests(unittest.TestCase):
    def test_missing_file_returns_deterministic_error(self) -> None:
        with TemporaryDirectory() as tmp:
            result = validate_expected_self_report(_command(Path(tmp)))

        self.assertFalse(result.valid)
        self.assertIn("self-report file is missing", result.errors)
        self.assertIsNone(result.parsed)
        self.assertFalse(result.location.exists)

    def test_directory_target_returns_deterministic_error(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "05_governance" / "reviews" / "report.md"
            target.mkdir(parents=True)

            result = validate_expected_self_report(_command(root))

        self.assertFalse(result.valid)
        self.assertIn("self-report path is a directory", result.errors)
        self.assertIsNone(result.parsed)


class InvalidTemplateOrSchemaTests(unittest.TestCase):
    def test_invalid_template_surfaces_locator_errors(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = _command(
                root, template=_valid_template(sequence=0)
            )
            result = validate_expected_self_report(command)

        self.assertFalse(result.valid)
        self.assertIn(
            "sequence must be a positive integer", result.errors
        )
        self.assertIsNone(result.parsed)

    def test_invalid_schema_surfaces_schema_errors(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_report(root)
            schema = SelfReportSchema(required_fields=42)  # type: ignore[arg-type]
            command = _command(root, schema=schema)

            result = validate_expected_self_report(command)

        self.assertFalse(result.valid)
        self.assertIn(
            "required_fields must be a tuple or list of non-empty strings",
            result.errors,
        )
        self.assertIsNone(result.parsed)

    def test_absolute_self_report_path_surfaces_path_safety_error(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = _command(
                root,
                template=_valid_template(
                    self_report_path=str(Path(tmp) / "absolute.md")
                ),
            )

            result = validate_expected_self_report(command)

        self.assertFalse(result.valid)
        self.assertIn(
            "self_report_path must be repo-relative", result.errors
        )


class MissingOrEmptyRequiredFieldTests(unittest.TestCase):
    def test_missing_required_section_reports_error(self) -> None:
        body = VALID_REPORT_BODY.replace(
            "## Memory Usage\n\nmemory: not_used\n", ""
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_report(root, body=body)

            result = validate_expected_self_report(_command(root))

        self.assertFalse(result.valid)
        self.assertIn(
            "self-report missing required field: memory usage statement",
            result.errors,
        )
        # The parsed section list is still present in the result.
        self.assertIsNotNone(result.parsed)

    def test_empty_required_section_reports_error(self) -> None:
        body = VALID_REPORT_BODY.replace(
            "## Memory Usage\n\nmemory: not_used\n",
            "## Memory Usage\n\n   \n",
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_report(root, body=body)

            result = validate_expected_self_report(_command(root))

        self.assertFalse(result.valid)
        self.assertIn(
            "self-report required field is empty: memory usage statement",
            result.errors,
        )

    def test_missing_files_changed_section_reports_error(self) -> None:
        body = VALID_REPORT_BODY.replace(
            "## Files Changed\n\n- path: x.py\n\n", ""
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_report(root, body=body)

            result = validate_expected_self_report(_command(root))

        self.assertFalse(result.valid)
        self.assertIn(
            "self-report missing required field: files changed",
            result.errors,
        )


class AliasMatchingTests(unittest.TestCase):
    def test_behavior_implemented_accepts_slice_specific_prefix(self) -> None:
        body = VALID_REPORT_BODY.replace(
            "## Behavior Implemented\n\nimplemented parser\n",
            "## Locator Behavior Implemented\n\nimplemented parser\n",
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_report(root, body=body)

            result = validate_expected_self_report(_command(root))

        self.assertTrue(result.valid, result.errors)

    def test_verification_commands_accepts_short_alias(self) -> None:
        body = VALID_REPORT_BODY.replace(
            "## Verification Commands And Results\n",
            "## Verification Commands\n",
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_report(root, body=body)

            result = validate_expected_self_report(_command(root))

        self.assertTrue(result.valid, result.errors)

    def test_blockers_alias_open_questions_is_accepted(self) -> None:
        body = VALID_REPORT_BODY.replace(
            "## Blockers Or Open Questions\n\nnone\n",
            "## Open Questions\n\nnone\n",
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_report(root, body=body)

            result = validate_expected_self_report(_command(root))

        self.assertTrue(result.valid, result.errors)

    def test_memory_usage_short_alias_is_accepted(self) -> None:
        body = VALID_REPORT_BODY  # uses "## Memory Usage" already
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_report(root, body=body)

            result = validate_expected_self_report(_command(root))

        self.assertTrue(result.valid, result.errors)

    def test_normalisation_strips_trailing_punctuation(self) -> None:
        body = VALID_REPORT_BODY.replace(
            "## Memory Usage\n\nmemory: not_used\n",
            "## Memory Usage:\n\nmemory: not_used\n",
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_report(root, body=body)

            result = validate_expected_self_report(_command(root))

        self.assertTrue(result.valid, result.errors)


class NoSideEffectTests(unittest.TestCase):
    def test_validator_does_not_write_any_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = _write_report(root)
            before = sorted(
                path.relative_to(root)
                for path in root.rglob("*")
            )

            validate_expected_self_report(_command(root))

            after = sorted(
                path.relative_to(root)
                for path in root.rglob("*")
            )
            # Target must still exist inside the temp tree; nothing new
            # should have been written.
            self.assertTrue(target.exists())
            self.assertEqual(before, after)

    def test_validator_does_not_scan_other_reviews_files(self) -> None:
        # Smoke check: write extra files alongside the explicit
        # target and confirm the validator returns a parsed result
        # whose path equals the explicit target only. The result type
        # does not expose any other file, so a scan would have to
        # leak through some other observable channel.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = _write_report(root)
            distractor = (
                root / "05_governance" / "reviews" / "other_report.md"
            )
            distractor.write_text(
                "# Distractor\n\n## Files Changed\n\nSHOULD NOT APPEAR\n",
                encoding="utf-8",
            )

            result = validate_expected_self_report(_command(root))

        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.parsed.path, str(target.resolve()))
        for section in result.parsed.sections:
            self.assertNotIn("SHOULD NOT APPEAR", section.body)


class NeverRaisesTests(unittest.TestCase):
    def test_fully_malformed_inputs_do_not_raise(self) -> None:
        with TemporaryDirectory() as tmp:
            command = SelfReportValidationCommand(
                location=SelfReportLocationCommand(
                    project_root=Path(tmp),
                    template=_valid_template(
                        sequence=0,
                        slug="",
                        required_reading=42,  # type: ignore[arg-type]
                        self_report_path="",
                    ),
                ),
                schema=SelfReportSchema(
                    required_fields=42,  # type: ignore[arg-type]
                    optional_fields=None,  # type: ignore[arg-type]
                    kind="",
                    version="",
                ),
            )
            try:
                result = validate_expected_self_report(command)
            except Exception as exc:  # pragma: no cover - guard rail
                self.fail(f"validator raised {type(exc).__name__}: {exc}")

            self.assertFalse(result.valid)
            self.assertGreater(len(result.errors), 0)
            self.assertIsNone(result.parsed)


class DefaultSchemaIsUsedTests(unittest.TestCase):
    def test_command_without_schema_uses_default(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_report(root)
            command = _command(root)
            self.assertEqual(
                command.schema.required_fields, SELF_REPORT_REQUIRED_FIELDS
            )
            self.assertEqual(
                command.schema.kind,
                default_self_report_schema().kind,
            )


class ResultTypeTests(unittest.TestCase):
    def test_result_type_identity(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_report(root)
            self.assertIsInstance(
                validate_expected_self_report(_command(root)),
                SelfReportValidationResult,
            )


if __name__ == "__main__":
    unittest.main()
