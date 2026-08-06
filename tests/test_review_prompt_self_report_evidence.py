"""Tests for the M006-S02 review-prompt self-report evidence bridge."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from frutlups.prompt_template import CodingPromptTemplate
from frutlups.review_prompt_template import (
    ReviewPromptEvidenceCommand,
    ReviewPromptEvidenceResult,
    derive_review_prompt_evidence,
)
from frutlups.self_report import (
    ParsedSelfReport,
    SelfReportFinding,
    SelfReportFindingSeverity,
    SelfReportFindingsCommand,
    SelfReportLocationCommand,
    SelfReportLocationResult,
    SelfReportSection,
    SelfReportValidationCommand,
    SelfReportValidationResult,
    collect_self_report_findings,
    find_self_report_section,
    validate_expected_self_report,
)


def _validation_from_sections(
    sections: tuple[SelfReportSection, ...],
    *,
    valid: bool = True,
    errors: tuple[str, ...] = (),
    expected_path: str = "/tmp/report.md",
) -> SelfReportValidationResult:
    location = SelfReportLocationResult(
        expected_path=expected_path,
        repo_relative_path="05_governance/reviews/report.md",
        exists=True,
        is_file=True,
        is_dir=False,
        errors=(),
    )
    parsed = ParsedSelfReport(path=expected_path, sections=sections)
    return SelfReportValidationResult(
        location=location,
        parsed=parsed,
        valid=valid,
        errors=errors,
    )


def _validation_invalid(
    errors: tuple[str, ...], *, parsed: ParsedSelfReport | None = None
) -> SelfReportValidationResult:
    location = SelfReportLocationResult(
        expected_path="",
        repo_relative_path="",
        exists=False,
        is_file=False,
        is_dir=False,
        errors=(),
    )
    return SelfReportValidationResult(
        location=location,
        parsed=parsed,
        valid=False,
        errors=errors,
    )


class ResultShapeTests(unittest.TestCase):
    def test_result_to_dict_shape(self) -> None:
        result = ReviewPromptEvidenceResult(
            expected_changed_files=("a.py",),
            verification_commands=("python -m unittest discover -s tests",),
            errors=(),
        )
        payload = result.to_dict()
        self.assertEqual(
            set(payload.keys()),
            {"expected_changed_files", "verification_commands", "errors"},
        )
        self.assertIsInstance(payload["expected_changed_files"], list)
        self.assertIsInstance(payload["verification_commands"], list)
        self.assertIsInstance(payload["errors"], list)

    def test_result_is_frozen(self) -> None:
        result = ReviewPromptEvidenceResult(
            expected_changed_files=(),
            verification_commands=(),
            errors=("e",),
        )
        with self.assertRaises(Exception):
            result.errors = ()  # type: ignore[misc]

    def test_command_is_frozen(self) -> None:
        command = ReviewPromptEvidenceCommand(
            validation=_validation_from_sections(())
        )
        with self.assertRaises(Exception):
            command.validation = command.validation  # type: ignore[misc]

    def test_helper_returns_correct_type(self) -> None:
        validation = _validation_from_sections(
            (
                SelfReportSection(
                    heading="Files Changed", body="- 08_pkg/a.py"
                ),
                SelfReportSection(
                    heading="Verification Commands",
                    body="- python -m unittest discover -s tests",
                ),
            )
        )
        self.assertIsInstance(
            derive_review_prompt_evidence(
                ReviewPromptEvidenceCommand(validation=validation)
            ),
            ReviewPromptEvidenceResult,
        )


class FilesChangedExtractionTests(unittest.TestCase):
    def test_bullet_files_changed(self) -> None:
        validation = _validation_from_sections(
            (
                SelfReportSection(
                    heading="Files Changed",
                    body="- 08_pkg/src/frutlups/a.py\n- 08_pkg/tests/test_a.py",
                ),
                SelfReportSection(
                    heading="Verification Commands",
                    body="- python -m unittest discover -s tests",
                ),
            )
        )
        result = derive_review_prompt_evidence(
            ReviewPromptEvidenceCommand(validation=validation)
        )
        self.assertEqual(result.errors, ())
        self.assertEqual(
            result.expected_changed_files,
            ("08_pkg/src/frutlups/a.py", "08_pkg/tests/test_a.py"),
        )

    def test_path_label_files_changed(self) -> None:
        validation = _validation_from_sections(
            (
                SelfReportSection(
                    heading="Files Changed",
                    body=(
                        "- path: 08_pkg/src/frutlups/a.py\n"
                        "  reason: implemented x\n"
                        "- path: 08_pkg/tests/test_a.py\n"
                        "  reason: covered x"
                    ),
                ),
                SelfReportSection(
                    heading="Verification Commands",
                    body="- python -m unittest discover -s tests",
                ),
            )
        )
        result = derive_review_prompt_evidence(
            ReviewPromptEvidenceCommand(validation=validation)
        )
        # Both paths extracted via `path:` label; reason lines also
        # surface as raw text (the helper does not filter them
        # because the prompt asks for ordered useful lines without
        # overfitting). The required behaviour is that `path:`
        # entries appear and are not lost; the prose lines that
        # appear alongside them are acceptable in this slice.
        self.assertIn(
            "08_pkg/src/frutlups/a.py", result.expected_changed_files
        )
        self.assertIn(
            "08_pkg/tests/test_a.py", result.expected_changed_files
        )

    def test_files_changed_order_preserved(self) -> None:
        validation = _validation_from_sections(
            (
                SelfReportSection(
                    heading="Files Changed",
                    body="- zeta.py\n- alpha.py\n- mid.py",
                ),
                SelfReportSection(
                    heading="Verification Commands",
                    body="- python -m unittest discover -s tests",
                ),
            )
        )
        result = derive_review_prompt_evidence(
            ReviewPromptEvidenceCommand(validation=validation)
        )
        self.assertEqual(
            result.expected_changed_files, ("zeta.py", "alpha.py", "mid.py")
        )

    def test_files_changed_duplicates_dropped_preserving_first(self) -> None:
        validation = _validation_from_sections(
            (
                SelfReportSection(
                    heading="Files Changed",
                    body=(
                        "- 08_pkg/a.py\n"
                        "- 08_pkg/b.py\n"
                        "- 08_pkg/a.py\n"
                        "- path: 08_pkg/b.py"
                    ),
                ),
                SelfReportSection(
                    heading="Verification Commands",
                    body="- python -m unittest discover -s tests",
                ),
            )
        )
        result = derive_review_prompt_evidence(
            ReviewPromptEvidenceCommand(validation=validation)
        )
        # First-appearance preserved; later duplicates dropped.
        self.assertEqual(
            result.expected_changed_files, ("08_pkg/a.py", "08_pkg/b.py")
        )

    def test_empty_files_changed_section_reports_error(self) -> None:
        validation = _validation_from_sections(
            (
                SelfReportSection(heading="Files Changed", body=""),
                SelfReportSection(
                    heading="Verification Commands",
                    body="- python -m unittest discover -s tests",
                ),
            )
        )
        result = derive_review_prompt_evidence(
            ReviewPromptEvidenceCommand(validation=validation)
        )
        self.assertEqual(result.expected_changed_files, ())
        self.assertTrue(
            any("files changed" in err for err in result.errors),
            result.errors,
        )


class VerificationCommandExtractionTests(unittest.TestCase):
    def test_bullet_verification_commands(self) -> None:
        validation = _validation_from_sections(
            (
                SelfReportSection(heading="Files Changed", body="- a.py"),
                SelfReportSection(
                    heading="Verification Commands",
                    body=(
                        "- python -m unittest discover -s tests\n"
                        "- python -m frutlups status .."
                    ),
                ),
            )
        )
        result = derive_review_prompt_evidence(
            ReviewPromptEvidenceCommand(validation=validation)
        )
        self.assertEqual(result.errors, ())
        self.assertEqual(
            result.verification_commands,
            (
                "python -m unittest discover -s tests",
                "python -m frutlups status ..",
            ),
        )

    def test_fenced_verification_commands(self) -> None:
        body = (
            "```powershell\n"
            "python -m unittest discover -s tests\n"
            "python -m frutlups status ..\n"
            "```"
        )
        validation = _validation_from_sections(
            (
                SelfReportSection(heading="Files Changed", body="- a.py"),
                SelfReportSection(
                    heading="Verification Commands And Results", body=body
                ),
            )
        )
        result = derive_review_prompt_evidence(
            ReviewPromptEvidenceCommand(validation=validation)
        )
        self.assertEqual(result.errors, ())
        self.assertEqual(
            result.verification_commands,
            (
                "python -m unittest discover -s tests",
                "python -m frutlups status ..",
            ),
        )

    def test_command_label_with_result_prose_skipped(self) -> None:
        body = (
            "- command: python -m unittest discover -s tests\n"
            "  result: passed\n"
            "- command: python -m frutlups status ..\n"
            "  result: passed\n"
            "OK"
        )
        validation = _validation_from_sections(
            (
                SelfReportSection(heading="Files Changed", body="- a.py"),
                SelfReportSection(
                    heading="Verification Commands", body=body
                ),
            )
        )
        result = derive_review_prompt_evidence(
            ReviewPromptEvidenceCommand(validation=validation)
        )
        self.assertEqual(result.errors, ())
        self.assertEqual(
            result.verification_commands,
            (
                "python -m unittest discover -s tests",
                "python -m frutlups status ..",
            ),
        )

    def test_alias_short_verification_heading_accepted(self) -> None:
        validation = _validation_from_sections(
            (
                SelfReportSection(heading="Files Changed", body="- a.py"),
                SelfReportSection(
                    heading="Verification",
                    body="- python -m unittest discover -s tests",
                ),
            )
        )
        result = derive_review_prompt_evidence(
            ReviewPromptEvidenceCommand(validation=validation)
        )
        self.assertEqual(result.errors, ())
        self.assertEqual(
            result.verification_commands,
            ("python -m unittest discover -s tests",),
        )

    def test_verification_duplicates_dropped_preserving_first(self) -> None:
        validation = _validation_from_sections(
            (
                SelfReportSection(heading="Files Changed", body="- a.py"),
                SelfReportSection(
                    heading="Verification Commands",
                    body=(
                        "- python -m unittest discover -s tests\n"
                        "- python -m frutlups status ..\n"
                        "- python -m unittest discover -s tests"
                    ),
                ),
            )
        )
        result = derive_review_prompt_evidence(
            ReviewPromptEvidenceCommand(validation=validation)
        )
        self.assertEqual(
            result.verification_commands,
            (
                "python -m unittest discover -s tests",
                "python -m frutlups status ..",
            ),
        )

    def test_empty_verification_section_reports_error(self) -> None:
        validation = _validation_from_sections(
            (
                SelfReportSection(heading="Files Changed", body="- a.py"),
                SelfReportSection(
                    heading="Verification Commands", body="passed\n  OK"
                ),
            )
        )
        result = derive_review_prompt_evidence(
            ReviewPromptEvidenceCommand(validation=validation)
        )
        self.assertEqual(result.verification_commands, ())
        self.assertTrue(
            any("verification commands" in err for err in result.errors),
            result.errors,
        )


class MissingSectionTests(unittest.TestCase):
    def test_missing_files_changed_section_reports_error(self) -> None:
        validation = _validation_from_sections(
            (
                SelfReportSection(
                    heading="Verification Commands",
                    body="- python -m unittest discover -s tests",
                ),
            )
        )
        result = derive_review_prompt_evidence(
            ReviewPromptEvidenceCommand(validation=validation)
        )
        self.assertEqual(result.expected_changed_files, ())
        self.assertEqual(result.verification_commands, ())
        self.assertTrue(
            any("files changed" in err for err in result.errors)
        )

    def test_missing_verification_section_reports_error(self) -> None:
        validation = _validation_from_sections(
            (
                SelfReportSection(
                    heading="Files Changed", body="- a.py"
                ),
            )
        )
        result = derive_review_prompt_evidence(
            ReviewPromptEvidenceCommand(validation=validation)
        )
        self.assertEqual(result.expected_changed_files, ())
        self.assertEqual(result.verification_commands, ())
        self.assertTrue(
            any("verification commands" in err for err in result.errors)
        )

    def test_both_missing_reports_both_errors(self) -> None:
        validation = _validation_from_sections(())
        result = derive_review_prompt_evidence(
            ReviewPromptEvidenceCommand(validation=validation)
        )
        joined = " | ".join(result.errors)
        self.assertIn("files changed", joined)
        self.assertIn("verification commands", joined)


class InvalidValidationTests(unittest.TestCase):
    def test_invalid_validation_propagates_errors(self) -> None:
        validation = _validation_invalid(("self-report file is missing",))
        result = derive_review_prompt_evidence(
            ReviewPromptEvidenceCommand(validation=validation)
        )
        self.assertEqual(result.expected_changed_files, ())
        self.assertEqual(result.verification_commands, ())
        self.assertIn("self-report file is missing", result.errors)

    def test_missing_parsed_returns_deterministic_error(self) -> None:
        # valid=True but parsed=None is an unusual but constructible
        # combination; the helper must fail closed deterministically.
        validation = SelfReportValidationResult(
            location=SelfReportLocationResult(
                expected_path="x",
                repo_relative_path="x",
                exists=True,
                is_file=True,
                is_dir=False,
                errors=(),
            ),
            parsed=None,
            valid=True,
            errors=(),
        )
        result = derive_review_prompt_evidence(
            ReviewPromptEvidenceCommand(validation=validation)
        )
        self.assertEqual(result.expected_changed_files, ())
        self.assertEqual(result.verification_commands, ())
        self.assertTrue(
            any("could not be parsed" in err for err in result.errors),
            result.errors,
        )


class HelperIsPureTests(unittest.TestCase):
    def test_helper_does_not_write_any_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            validation = _validation_from_sections(
                (
                    SelfReportSection(heading="Files Changed", body="- a.py"),
                    SelfReportSection(
                        heading="Verification Commands",
                        body="- python -m unittest discover -s tests",
                    ),
                )
            )
            before = sorted(p.relative_to(root) for p in root.rglob("*"))

            derive_review_prompt_evidence(
                ReviewPromptEvidenceCommand(validation=validation)
            )

            after = sorted(p.relative_to(root) for p in root.rglob("*"))
            self.assertEqual(before, after)

    def test_helper_does_not_read_validation_path(self) -> None:
        # The helper should rely on validation.parsed only; it must
        # not attempt to re-read validation.location.expected_path.
        # We confirm this indirectly: an expected_path that does NOT
        # exist on disk still produces evidence as long as parsed
        # carries usable sections.
        validation = _validation_from_sections(
            (
                SelfReportSection(heading="Files Changed", body="- a.py"),
                SelfReportSection(
                    heading="Verification Commands",
                    body="- python -m unittest discover -s tests",
                ),
            ),
            expected_path="/does/not/exist/report.md",
        )
        result = derive_review_prompt_evidence(
            ReviewPromptEvidenceCommand(validation=validation)
        )
        self.assertEqual(result.errors, ())
        self.assertEqual(result.expected_changed_files, ("a.py",))


class FindSelfReportSectionTests(unittest.TestCase):
    def test_returns_first_section_matching_alias(self) -> None:
        parsed = ParsedSelfReport(
            path="x",
            sections=(
                SelfReportSection(heading="Files Changed", body="x"),
                SelfReportSection(
                    heading="Verification Commands", body="y"
                ),
            ),
        )
        section = find_self_report_section(parsed, "verification commands and results")
        self.assertIsNotNone(section)
        self.assertEqual(section.heading, "Verification Commands")

    def test_returns_none_when_no_section_matches(self) -> None:
        parsed = ParsedSelfReport(
            path="x",
            sections=(SelfReportSection(heading="Other", body="x"),),
        )
        self.assertIsNone(
            find_self_report_section(parsed, "files changed")
        )

    def test_behavior_implemented_ends_with_rule(self) -> None:
        parsed = ParsedSelfReport(
            path="x",
            sections=(
                SelfReportSection(
                    heading="Locator Behavior Implemented", body="z"
                ),
            ),
        )
        section = find_self_report_section(parsed, "behavior implemented")
        self.assertIsNotNone(section)


class NeverRaisesOnMalformedParsedTests(unittest.TestCase):
    """Regression: derive_review_prompt_evidence and
    find_self_report_section must never raise for constructible
    malformed parsed self-report shapes (see M006-S02 review)."""

    def test_non_string_files_changed_body_fails_closed(self) -> None:
        validation = _validation_from_sections(
            (
                SelfReportSection(heading="Files Changed", body=None),  # type: ignore[arg-type]
                SelfReportSection(
                    heading="Verification Commands",
                    body="- python -m unittest discover -s tests",
                ),
            )
        )
        try:
            result = derive_review_prompt_evidence(
                ReviewPromptEvidenceCommand(validation=validation)
            )
        except Exception as exc:  # pragma: no cover - guard rail
            self.fail(f"helper raised {type(exc).__name__}: {exc}")

        self.assertEqual(result.expected_changed_files, ())
        self.assertEqual(result.verification_commands, ())
        self.assertIn(
            "self-report files changed section body must be a string",
            result.errors,
        )

    def test_non_string_verification_body_fails_closed(self) -> None:
        validation = _validation_from_sections(
            (
                SelfReportSection(heading="Files Changed", body="- a.py"),
                SelfReportSection(
                    heading="Verification Commands",
                    body=42,  # type: ignore[arg-type]
                ),
            )
        )
        try:
            result = derive_review_prompt_evidence(
                ReviewPromptEvidenceCommand(validation=validation)
            )
        except Exception as exc:  # pragma: no cover - guard rail
            self.fail(f"helper raised {type(exc).__name__}: {exc}")

        self.assertEqual(result.expected_changed_files, ())
        self.assertEqual(result.verification_commands, ())
        self.assertIn(
            "self-report verification commands section body must be a string",
            result.errors,
        )

    def test_both_non_string_bodies_report_both_errors(self) -> None:
        validation = _validation_from_sections(
            (
                SelfReportSection(heading="Files Changed", body=None),  # type: ignore[arg-type]
                SelfReportSection(
                    heading="Verification Commands",
                    body=None,  # type: ignore[arg-type]
                ),
            )
        )
        result = derive_review_prompt_evidence(
            ReviewPromptEvidenceCommand(validation=validation)
        )
        self.assertEqual(result.expected_changed_files, ())
        self.assertEqual(result.verification_commands, ())
        self.assertIn(
            "self-report files changed section body must be a string",
            result.errors,
        )
        self.assertIn(
            "self-report verification commands section body must be a string",
            result.errors,
        )

    def test_find_section_skips_non_section_entries(self) -> None:
        parsed = ParsedSelfReport(
            path="x",
            sections=(
                object(),  # type: ignore[arg-type]
                SelfReportSection(heading="Files Changed", body="- a.py"),
            ),
        )
        try:
            section = find_self_report_section(parsed, "files changed")
        except Exception as exc:  # pragma: no cover - guard rail
            self.fail(f"helper raised {type(exc).__name__}: {exc}")
        self.assertIsNotNone(section)
        self.assertEqual(section.heading, "Files Changed")

    def test_find_section_skips_non_string_headings(self) -> None:
        parsed = ParsedSelfReport(
            path="x",
            sections=(
                SelfReportSection(heading=123, body="ignored"),  # type: ignore[arg-type]
                SelfReportSection(heading="Files Changed", body="- a.py"),
            ),
        )
        try:
            section = find_self_report_section(parsed, "files changed")
        except Exception as exc:  # pragma: no cover - guard rail
            self.fail(f"helper raised {type(exc).__name__}: {exc}")
        self.assertIsNotNone(section)
        self.assertEqual(section.heading, "Files Changed")

    def test_find_section_returns_none_for_object_only_sections(self) -> None:
        parsed = ParsedSelfReport(
            path="x", sections=(object(),)  # type: ignore[arg-type]
        )
        try:
            self.assertIsNone(
                find_self_report_section(parsed, "files changed")
            )
        except Exception as exc:  # pragma: no cover - guard rail
            self.fail(f"helper raised {type(exc).__name__}: {exc}")

    def test_find_section_returns_none_for_non_parsed_input(self) -> None:
        try:
            self.assertIsNone(
                find_self_report_section(None, "files changed")  # type: ignore[arg-type]
            )
            self.assertIsNone(
                find_self_report_section(42, "files changed")  # type: ignore[arg-type]
            )
        except Exception as exc:  # pragma: no cover - guard rail
            self.fail(f"helper raised {type(exc).__name__}: {exc}")

    def test_full_adversarial_probe_does_not_raise(self) -> None:
        # Mirrors the coding-prompt's focused adversarial probe.
        validation = _validation_from_sections(
            (
                object(),  # type: ignore[arg-type]
                SelfReportSection(heading=123, body="ignored"),  # type: ignore[arg-type]
                SelfReportSection(heading="Files Changed", body=None),  # type: ignore[arg-type]
                SelfReportSection(
                    heading="Verification Commands", body=None  # type: ignore[arg-type]
                ),
            )
        )
        try:
            result = derive_review_prompt_evidence(
                ReviewPromptEvidenceCommand(validation=validation)
            )
        except Exception as exc:  # pragma: no cover - guard rail
            self.fail(f"helper raised {type(exc).__name__}: {exc}")
        self.assertEqual(result.expected_changed_files, ())
        self.assertEqual(result.verification_commands, ())
        self.assertIn(
            "self-report files changed section body must be a string",
            result.errors,
        )
        self.assertIn(
            "self-report verification commands section body must be a string",
            result.errors,
        )

    def test_alias_behavior_preserved_for_valid_sections(self) -> None:
        # Regression: skipping malformed entries must not break the
        # short-alias and ends-with rules on valid sections that
        # follow.
        parsed = ParsedSelfReport(
            path="x",
            sections=(
                object(),  # type: ignore[arg-type]
                SelfReportSection(heading="Verification", body="cmd"),
            ),
        )
        section = find_self_report_section(
            parsed, "verification commands and results"
        )
        self.assertIsNotNone(section)
        self.assertEqual(section.heading, "Verification")


class FilesChangedMetadataPolishTests(unittest.TestCase):
    """Polish: files-changed extraction must ignore common
    metadata-continuation lines (`reason:`, `notes:`, `note:`,
    `status:`, `result:`)."""

    def test_reason_and_notes_lines_ignored(self) -> None:
        validation = _validation_from_sections(
            (
                SelfReportSection(
                    heading="Files Changed",
                    body=(
                        "- path: 08_pkg/src/frutlups/review_prompt_template.py\n"
                        "  reason: hardened parser\n"
                        "  notes: no prompt writing\n"
                        "- path: 08_pkg/tests/"
                        "test_review_prompt_self_report_evidence.py"
                    ),
                ),
                SelfReportSection(
                    heading="Verification Commands",
                    body="- python -m unittest discover -s tests",
                ),
            )
        )
        result = derive_review_prompt_evidence(
            ReviewPromptEvidenceCommand(validation=validation)
        )
        self.assertEqual(result.errors, ())
        self.assertEqual(
            result.expected_changed_files,
            (
                "08_pkg/src/frutlups/review_prompt_template.py",
                "08_pkg/tests/test_review_prompt_self_report_evidence.py",
            ),
        )

    def test_every_ignored_label_is_dropped(self) -> None:
        body = (
            "- path: a.py\n"
            "  reason: r\n"
            "  notes: n\n"
            "  note: n2\n"
            "  status: s\n"
            "  result: passed\n"
            "- path: b.py"
        )
        validation = _validation_from_sections(
            (
                SelfReportSection(heading="Files Changed", body=body),
                SelfReportSection(
                    heading="Verification Commands",
                    body="- python -m unittest discover -s tests",
                ),
            )
        )
        result = derive_review_prompt_evidence(
            ReviewPromptEvidenceCommand(validation=validation)
        )
        self.assertEqual(result.expected_changed_files, ("a.py", "b.py"))
        # Negative assertion: none of the prose values surface as a
        # changed-file entry.
        for prose in ("r", "n", "n2", "s", "passed"):
            with self.subTest(prose=prose):
                self.assertNotIn(prose, result.expected_changed_files)

    def test_ignored_labels_are_case_insensitive(self) -> None:
        body = (
            "- path: a.py\n"
            "  Reason: r\n"
            "  NOTES: n\n"
            "  STATUS: s"
        )
        validation = _validation_from_sections(
            (
                SelfReportSection(heading="Files Changed", body=body),
                SelfReportSection(
                    heading="Verification Commands",
                    body="- python -m unittest discover -s tests",
                ),
            )
        )
        result = derive_review_prompt_evidence(
            ReviewPromptEvidenceCommand(validation=validation)
        )
        self.assertEqual(result.expected_changed_files, ("a.py",))


class M005S04MinorFixTests(unittest.TestCase):
    """The accepted M005-S04 minor: invalid_self_report_template
    findings must surface the self-report path in their human
    message when one could be derived."""

    def _template_with_absolute_path(self, abs_path: str) -> CodingPromptTemplate:
        return CodingPromptTemplate(
            sequence=23,
            milestone_id="M006",
            slice_id="M006-S02",
            slug="frutlups_m006_s02_review_prompt_self_report_evidence",
            title="Review Prompt Self-Report Evidence",
            role_instructions="coder",
            required_reading=("CLAUDE.md", "README.md"),
            scope_paths=("08_pkg/src/frutlups/",),
            non_goals=("do not render markdown",),
            definition_of_done=("evidence helper exists",),
            verification_commands=("python -m unittest discover -s tests",),
            self_report_path=abs_path,
        )

    def test_invalid_template_message_includes_path_when_available(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            absolute = str(root / "outside.md")
            template = self._template_with_absolute_path(absolute)

            result = collect_self_report_findings(
                SelfReportFindingsCommand(
                    project_root=root,
                    templates=(template,),
                )
            )

        self.assertEqual(len(result.findings), 1)
        finding = result.findings[0]
        self.assertEqual(finding.code, "invalid_self_report_template")
        # The structured field already carried the path; the human
        # message must include it too.
        self.assertEqual(finding.self_report_path, absolute)
        self.assertIn(absolute, finding.message)


class EvidenceFilteringRegressionTests(unittest.TestCase):
    """Regression tests for M008-S03 corrective: evidence extraction must
    reject continuation prose, helper names, command output, and JSON
    fragments that appeared as evidence in polluted generated review
    prompts."""

    # ------------------------------------------------------------------ #
    # Files-changed: continuation-line and helper-name pollution           #
    # ------------------------------------------------------------------ #

    def test_wrapped_bullet_continuation_lines_skipped(self) -> None:
        body = (
            "- `08_pkg/src/frutlups/project.py` — added `CodingPromptMeta`, `ReviewPromptPlan`,\n"
            "  `_sections_from_text`, `_extract_bullet_backtick_items`, `_extract_bullet_text_items`,\n"
            "  `_parse_coding_prompt_meta`, `_make_invalid_review_plan`, `build_review_prompt_plan`;\n"
            "  added imports for `PromptKind`, M006 review-prompt surfaces, and M005 self-report surfaces.\n"
            "- `08_pkg/src/frutlups/cli.py` — added `make-review-prompt` subparser\n"
            "  added `_format_review_prompt_plan`; imported `ReviewPromptPlan`.\n"
            "- `08_pkg/tests/test_make_review_prompt.py` — new test file (95 tests)."
        )
        validation = _validation_from_sections(
            (
                SelfReportSection(heading="Files Changed", body=body),
                SelfReportSection(
                    heading="Verification Commands",
                    body="- python -m unittest discover -s tests",
                ),
            )
        )
        result = derive_review_prompt_evidence(
            ReviewPromptEvidenceCommand(validation=validation)
        )
        self.assertEqual(result.errors, ())
        self.assertEqual(
            result.expected_changed_files,
            (
                "08_pkg/src/frutlups/project.py",
                "08_pkg/src/frutlups/cli.py",
                "08_pkg/tests/test_make_review_prompt.py",
            ),
        )

    def test_backtick_wrapped_path_with_description_stripped(self) -> None:
        body = "- `08_pkg/src/frutlups/project.py` — changed helpers"
        validation = _validation_from_sections(
            (
                SelfReportSection(heading="Files Changed", body=body),
                SelfReportSection(
                    heading="Verification Commands",
                    body="- python -m unittest discover -s tests",
                ),
            )
        )
        result = derive_review_prompt_evidence(
            ReviewPromptEvidenceCommand(validation=validation)
        )
        self.assertEqual(result.expected_changed_files, ("08_pkg/src/frutlups/project.py",))

    def test_em_dash_description_stripped_without_backticks(self) -> None:
        body = "- 08_pkg/src/frutlups/project.py — modified"
        validation = _validation_from_sections(
            (
                SelfReportSection(heading="Files Changed", body=body),
                SelfReportSection(
                    heading="Verification Commands",
                    body="- python -m unittest discover -s tests",
                ),
            )
        )
        result = derive_review_prompt_evidence(
            ReviewPromptEvidenceCommand(validation=validation)
        )
        self.assertEqual(result.expected_changed_files, ("08_pkg/src/frutlups/project.py",))

    def test_paren_description_stripped(self) -> None:
        body = "- 08_pkg/src/frutlups/project.py (modified)"
        validation = _validation_from_sections(
            (
                SelfReportSection(heading="Files Changed", body=body),
                SelfReportSection(
                    heading="Verification Commands",
                    body="- python -m unittest discover -s tests",
                ),
            )
        )
        result = derive_review_prompt_evidence(
            ReviewPromptEvidenceCommand(validation=validation)
        )
        self.assertEqual(result.expected_changed_files, ("08_pkg/src/frutlups/project.py",))

    def test_helper_name_only_continuation_not_a_changed_file(self) -> None:
        body = (
            "- `08_pkg/src/frutlups/project.py` — added helpers\n"
            "  `_sections_from_text`, `_extract_bullet_backtick_items`"
        )
        validation = _validation_from_sections(
            (
                SelfReportSection(heading="Files Changed", body=body),
                SelfReportSection(
                    heading="Verification Commands",
                    body="- python -m unittest discover -s tests",
                ),
            )
        )
        result = derive_review_prompt_evidence(
            ReviewPromptEvidenceCommand(validation=validation)
        )
        self.assertEqual(
            result.expected_changed_files, ("08_pkg/src/frutlups/project.py",)
        )
        for bad in ("_sections_from_text", "_extract_bullet_backtick_items"):
            self.assertNotIn(bad, result.expected_changed_files)

    def test_backtick_helper_name_without_path_rejected(self) -> None:
        body = (
            "- `08_pkg/src/frutlups/project.py` — added helpers\n"
            "- `_sections_from_text`"
        )
        validation = _validation_from_sections(
            (
                SelfReportSection(heading="Files Changed", body=body),
                SelfReportSection(
                    heading="Verification Commands",
                    body="- python -m unittest discover -s tests",
                ),
            )
        )
        result = derive_review_prompt_evidence(
            ReviewPromptEvidenceCommand(validation=validation)
        )
        self.assertEqual(
            result.expected_changed_files, ("08_pkg/src/frutlups/project.py",)
        )
        self.assertNotIn("_sections_from_text", result.expected_changed_files)

    # ------------------------------------------------------------------ #
    # Verification commands: mixed-fence pollution                         #
    # ------------------------------------------------------------------ #

    def test_output_fence_after_command_fence_skipped(self) -> None:
        body = (
            "```powershell\n"
            "python -m unittest discover -s tests\n"
            "```\n"
            "```\n"
            "Ran 980 tests in 2.48s — OK\n"
            "```"
        )
        validation = _validation_from_sections(
            (
                SelfReportSection(heading="Files Changed", body="- a.py"),
                SelfReportSection(
                    heading="Verification Commands", body=body
                ),
            )
        )
        result = derive_review_prompt_evidence(
            ReviewPromptEvidenceCommand(validation=validation)
        )
        self.assertEqual(result.errors, ())
        self.assertEqual(
            result.verification_commands,
            ("python -m unittest discover -s tests",),
        )
        self.assertNotIn(
            "Ran 980 tests in 2.48s — OK", result.verification_commands
        )

    def test_json_fence_skipped(self) -> None:
        body = (
            "```powershell\n"
            "python -m frutlups status .. --json\n"
            "```\n"
            "```json\n"
            '{\n'
            '  "sequence": 34,\n'
            '  "valid": true\n'
            "}\n"
            "```"
        )
        validation = _validation_from_sections(
            (
                SelfReportSection(heading="Files Changed", body="- a.py"),
                SelfReportSection(
                    heading="Verification Commands", body=body
                ),
            )
        )
        result = derive_review_prompt_evidence(
            ReviewPromptEvidenceCommand(validation=validation)
        )
        self.assertEqual(result.errors, ())
        self.assertEqual(
            result.verification_commands,
            ("python -m frutlups status .. --json",),
        )
        for bad in ("{", "}", '"sequence": 34,', '"valid": true'):
            with self.subTest(bad=bad):
                self.assertNotIn(bad, result.verification_commands)

    def test_cli_output_lines_after_commands_not_included(self) -> None:
        body = (
            "```powershell\n"
            "python -m frutlups status ..\n"
            "```\n"
            "```\n"
            "Project: <PROJECT_ROOT>\n"
            "Sequence: 034\n"
            "```"
        )
        validation = _validation_from_sections(
            (
                SelfReportSection(heading="Files Changed", body="- a.py"),
                SelfReportSection(
                    heading="Verification Commands", body=body
                ),
            )
        )
        result = derive_review_prompt_evidence(
            ReviewPromptEvidenceCommand(validation=validation)
        )
        self.assertEqual(result.errors, ())
        self.assertEqual(
            result.verification_commands,
            ("python -m frutlups status ..",),
        )
        self.assertNotIn(
            "Project: <PROJECT_ROOT>",
            result.verification_commands,
        )
        self.assertNotIn("Sequence: 034", result.verification_commands)

    def test_multiple_powershell_fences_all_included(self) -> None:
        body = (
            "```powershell\n"
            "python -m unittest discover -s tests\n"
            "```\n"
            "```\n"
            "Ran 980 tests — OK\n"
            "```\n"
            "```powershell\n"
            "python -m frutlups status ..\n"
            "```\n"
            "```\n"
            "Prompts: 34 coding, 34 review\n"
            "```"
        )
        validation = _validation_from_sections(
            (
                SelfReportSection(heading="Files Changed", body="- a.py"),
                SelfReportSection(
                    heading="Verification Commands", body=body
                ),
            )
        )
        result = derive_review_prompt_evidence(
            ReviewPromptEvidenceCommand(validation=validation)
        )
        self.assertEqual(result.errors, ())
        self.assertEqual(
            result.verification_commands,
            (
                "python -m unittest discover -s tests",
                "python -m frutlups status ..",
            ),
        )

    def test_env_setup_line_in_powershell_fence_included(self) -> None:
        body = (
            "```powershell\n"
            "$env:PYTHONPATH='src'\n"
            "python -m unittest discover -s tests\n"
            "```"
        )
        validation = _validation_from_sections(
            (
                SelfReportSection(heading="Files Changed", body="- a.py"),
                SelfReportSection(
                    heading="Verification Commands", body=body
                ),
            )
        )
        result = derive_review_prompt_evidence(
            ReviewPromptEvidenceCommand(validation=validation)
        )
        self.assertEqual(result.errors, ())
        self.assertIn("$env:PYTHONPATH='src'", result.verification_commands)
        self.assertIn(
            "python -m unittest discover -s tests",
            result.verification_commands,
        )

    def test_real_m008_s03_self_report_files_shape(self) -> None:
        """Reproduce the exact files-changed shape from the M008-S03 self-report
        that produced 8 entries; after the fix exactly 3 paths are returned."""
        body = (
            "- `08_pkg/src/frutlups/project.py` — added `CodingPromptMeta`, `ReviewPromptPlan`,\n"
            "  `_sections_from_text`, `_extract_bullet_backtick_items`, `_extract_bullet_text_items`,\n"
            "  `_parse_coding_prompt_meta`, `_make_invalid_review_plan`, `build_review_prompt_plan`;\n"
            "  added imports for `PromptKind`, M006 review-prompt surfaces, and M005 self-report surfaces.\n"
            "- `08_pkg/src/frutlups/cli.py` — added `make-review-prompt` subparser and handler in `main()`,\n"
            "  added `_format_review_prompt_plan`; imported `ReviewPromptPlan`, `build_review_prompt_plan`,\n"
            "  `ReviewPromptWriteCommand`, `ReviewPromptWriteResult`, `write_review_prompt`.\n"
            "- `08_pkg/tests/test_make_review_prompt.py` — new test file (95 tests)."
        )
        validation = _validation_from_sections(
            (
                SelfReportSection(heading="Files Changed", body=body),
                SelfReportSection(
                    heading="Verification Commands",
                    body="- python -m unittest discover -s tests",
                ),
            )
        )
        result = derive_review_prompt_evidence(
            ReviewPromptEvidenceCommand(validation=validation)
        )
        self.assertEqual(result.errors, ())
        self.assertEqual(
            result.expected_changed_files,
            (
                "08_pkg/src/frutlups/project.py",
                "08_pkg/src/frutlups/cli.py",
                "08_pkg/tests/test_make_review_prompt.py",
            ),
        )

    def test_real_m008_s03_self_report_commands_shape(self) -> None:
        """Reproduce the verification-section shape from the M008-S03
        self-report that produced 31 command entries; after the fix only
        real powershell commands are returned."""
        body = (
            "```powershell\n"
            "python -m unittest discover -s tests\n"
            "```\n"
            "Result: `Ran 980 tests in 2.48s — OK`\n"
            "\n"
            "```powershell\n"
            "python -m frutlups status ..\n"
            "```\n"
            "Result: `Prompts: 34 coding, 33 review`\n"
            "\n"
            "```powershell\n"
            "python -m frutlups make-review-prompt .. --dry-run\n"
            "```\n"
            "Result before self-report: `frutlups: self-report file is missing`\n"
            "\n"
            "```powershell\n"
            "python -m frutlups make-review-prompt .. --dry-run\n"
            "```\n"
            "Result after self-report:\n"
            "\n"
            "```\n"
            "Project: <PROJECT_ROOT>\n"
            "Sequence: 034\n"
            "```\n"
            "\n"
            "```powershell\n"
            "python -m frutlups make-review-prompt .. --dry-run --json\n"
            "```\n"
            "Result after self-report:\n"
            "\n"
            "```json\n"
            '{\n'
            '  "sequence": 34,\n'
            '  "valid": true\n'
            "}\n"
            "```\n"
            "\n"
            "```powershell\n"
            "python -m compileall -q src\n"
            "```\n"
        )
        validation = _validation_from_sections(
            (
                SelfReportSection(heading="Files Changed", body="- a.py"),
                SelfReportSection(
                    heading="Verification Commands and Results", body=body
                ),
            )
        )
        result = derive_review_prompt_evidence(
            ReviewPromptEvidenceCommand(validation=validation)
        )
        self.assertEqual(result.errors, ())
        # Duplicate powershell command should be deduplicated
        expected = (
            "python -m unittest discover -s tests",
            "python -m frutlups status ..",
            "python -m frutlups make-review-prompt .. --dry-run",
            "python -m frutlups make-review-prompt .. --dry-run --json",
            "python -m compileall -q src",
        )
        self.assertEqual(result.verification_commands, expected)
        # Negative: none of the output/JSON lines must appear
        for bad in (
            "Project: <PROJECT_ROOT>",
            "Sequence: 034",
            "{",
            '"sequence": 34,',
            '"valid": true',
        ):
            with self.subTest(bad=bad):
                self.assertNotIn(bad, result.verification_commands)


if __name__ == "__main__":
    unittest.main()
