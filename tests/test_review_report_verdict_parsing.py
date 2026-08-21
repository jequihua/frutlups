"""Tests for M007-S02 review report verdict parsing."""

import tempfile
import unittest
from pathlib import Path

from frutlups.review_report import (
    ReviewReportSchema,
    ReviewReportVerdictParseCommand,
    ReviewReportVerdictParseResult,
    ReviewVerdict,
    default_review_report_schema,
    parse_review_report_verdict,
    parse_review_report_verdict_text,
)

_SIMPLE_PASS = "# Review Report\n\n## Verdict\n\npass\n\n## Findings\n\n- ok\n"
_SIMPLE_NEEDS_WORK = "## Verdict\n\nneeds_work\n"
_SIMPLE_BLOCKED = "## Verdict\n\nblocked\n"
_SIMPLE_OVERRIDE = "## Verdict\n\noverride\n"
_MULTIPLE_VERDICT_SECTIONS_ERROR = (
    "multiple verdict sections found; refusing to resolve an ambiguous verdict"
)


class EachCanonicalVerdictTests(unittest.TestCase):
    def test_pass(self):
        r = parse_review_report_verdict_text(_SIMPLE_PASS)
        self.assertTrue(r.valid)
        self.assertEqual(r.verdict, ReviewVerdict.PASS)
        self.assertEqual(r.raw_verdict, "pass")
        self.assertEqual(r.errors, ())

    def test_needs_work(self):
        r = parse_review_report_verdict_text(_SIMPLE_NEEDS_WORK)
        self.assertTrue(r.valid)
        self.assertEqual(r.verdict, ReviewVerdict.NEEDS_WORK)

    def test_blocked(self):
        r = parse_review_report_verdict_text(_SIMPLE_BLOCKED)
        self.assertTrue(r.valid)
        self.assertEqual(r.verdict, ReviewVerdict.BLOCKED)

    def test_override(self):
        r = parse_review_report_verdict_text(_SIMPLE_OVERRIDE)
        self.assertTrue(r.valid)
        self.assertEqual(r.verdict, ReviewVerdict.OVERRIDE)


class CaseInsensitiveTests(unittest.TestCase):
    def test_uppercase_verdict(self):
        r = parse_review_report_verdict_text("## Verdict\n\nPASS\n")
        self.assertTrue(r.valid)
        self.assertEqual(r.verdict, ReviewVerdict.PASS)

    def test_mixed_case_verdict(self):
        r = parse_review_report_verdict_text("## Verdict\n\nNeeds_Work\n")
        self.assertTrue(r.valid)
        self.assertEqual(r.verdict, ReviewVerdict.NEEDS_WORK)

    def test_uppercase_heading(self):
        r = parse_review_report_verdict_text("## VERDICT\n\npass\n")
        self.assertTrue(r.valid)
        self.assertEqual(r.verdict, ReviewVerdict.PASS)

    def test_mixed_case_heading(self):
        r = parse_review_report_verdict_text("## Verdict\n\nBLOCKED\n")
        self.assertTrue(r.valid)
        self.assertEqual(r.verdict, ReviewVerdict.BLOCKED)


class ListPrefixTests(unittest.TestCase):
    def test_dash_prefix(self):
        r = parse_review_report_verdict_text("## Verdict\n\n- pass\n")
        self.assertTrue(r.valid)
        self.assertEqual(r.verdict, ReviewVerdict.PASS)
        self.assertEqual(r.raw_verdict, "pass")

    def test_asterisk_prefix(self):
        r = parse_review_report_verdict_text("## Verdict\n\n* needs_work\n")
        self.assertTrue(r.valid)
        self.assertEqual(r.verdict, ReviewVerdict.NEEDS_WORK)

    def test_numbered_dot_prefix(self):
        r = parse_review_report_verdict_text("## Verdict\n\n1. blocked\n")
        self.assertTrue(r.valid)
        self.assertEqual(r.verdict, ReviewVerdict.BLOCKED)

    def test_numbered_paren_prefix(self):
        r = parse_review_report_verdict_text("## Verdict\n\n1) override\n")
        self.assertTrue(r.valid)
        self.assertEqual(r.verdict, ReviewVerdict.OVERRIDE)


class InlineCodeTests(unittest.TestCase):
    def test_backtick_needs_work(self):
        content = "# Review Report\n\n## Verdict\n\n`needs_work`\n\n## Findings\n\n- major: example\n"
        r = parse_review_report_verdict_text(content)
        self.assertTrue(r.valid)
        self.assertEqual(r.verdict, ReviewVerdict.NEEDS_WORK)
        self.assertEqual(r.raw_verdict, "needs_work")
        self.assertEqual(r.errors, ())

    def test_backtick_pass(self):
        r = parse_review_report_verdict_text("## Verdict\n\n`pass`\n")
        self.assertTrue(r.valid)
        self.assertEqual(r.verdict, ReviewVerdict.PASS)

    def test_backtick_list_prefix(self):
        r = parse_review_report_verdict_text("## Verdict\n\n- `blocked`\n")
        self.assertTrue(r.valid)
        self.assertEqual(r.verdict, ReviewVerdict.BLOCKED)


class InlineVerdictLineTests(unittest.TestCase):
    """Reports that state the verdict inline (no ## Verdict heading) still parse."""

    def test_inline_backtick_pass(self):
        content = (
            "# Review Report: M016-S04\n\nReview prompt:\n`x`\n\n"
            "Verdict: `pass`\n\n## Findings\n\n- minor: example\n"
        )
        r = parse_review_report_verdict_text(content)
        self.assertTrue(r.valid)
        self.assertEqual(r.verdict, ReviewVerdict.PASS)

    def test_inline_plain_pass(self):
        r = parse_review_report_verdict_text("# Review\n\nVerdict: pass\n")
        self.assertTrue(r.valid)
        self.assertEqual(r.verdict, ReviewVerdict.PASS)

    def test_inline_needs_work(self):
        r = parse_review_report_verdict_text("# Review\n\nVerdict: needs_work\n")
        self.assertTrue(r.valid)
        self.assertEqual(r.verdict, ReviewVerdict.NEEDS_WORK)

    def test_heading_section_still_preferred(self):
        # When a ## Verdict section exists, it wins over any inline mention.
        content = "## Verdict\n\nneeds_work\n\n## Notes\n\nVerdict: pass mentioned in prose\n"
        r = parse_review_report_verdict_text(content)
        self.assertTrue(r.valid)
        self.assertEqual(r.verdict, ReviewVerdict.NEEDS_WORK)

    def test_prose_mention_does_not_match(self):
        # A non-leading mention of "verdict:" must not be treated as the verdict.
        content = "# Review\n\nThe required verdict: pass is documented elsewhere.\n"
        r = parse_review_report_verdict_text(content)
        self.assertFalse(r.valid)

    def test_no_verdict_anywhere_fails(self):
        r = parse_review_report_verdict_text("# Review\n\nNo verdict here.\n")
        self.assertFalse(r.valid)

    def test_empty_heading_section_with_later_inline_is_invalid(self):
        # A present-but-empty ## Verdict section (immediately followed by another
        # heading) stays invalid even though a later line says "Verdict: pass":
        # the inline fallback applies only when NO verdict heading exists at all.
        r = parse_review_report_verdict_text("## Verdict\n\n## Findings\n\nVerdict: pass\n")
        self.assertFalse(r.valid)

    def test_empty_heading_then_subheading_with_inline_is_invalid(self):
        r = parse_review_report_verdict_text("## Verdict\n\n### Sub\n\nVerdict: pass\n")
        self.assertFalse(r.valid)

    def test_heading_section_with_redundant_inline_label(self):
        # A ## Verdict section whose content line is "Verdict: pass" (a common
        # reviewer habit) parses; the redundant label is stripped.
        r = parse_review_report_verdict_text(
            "# Review\n\n## Verdict\n\nVerdict: pass\n\n## Findings\n\n- ok\n"
        )
        self.assertTrue(r.valid)
        self.assertEqual(r.verdict, ReviewVerdict.PASS)

    def test_heading_section_with_redundant_inline_label_and_backticks(self):
        r = parse_review_report_verdict_text("## Verdict\n\nVerdict: `needs_work`\n")
        self.assertTrue(r.valid)
        self.assertEqual(r.verdict, ReviewVerdict.NEEDS_WORK)


class FlexibleHeadingTests(unittest.TestCase):
    def test_each_atx_level_and_casing_still_parses(self):
        headings = (
            "Verdict",
            "VERDICT",
            "verdict",
            "VeRdIcT",
            "Verdict",
            "VERDICT",
        )
        for level, heading in enumerate(headings, start=1):
            with self.subTest(level=level, heading=heading):
                r = parse_review_report_verdict_text(
                    f"{'#' * level} {heading}\n\npass\n"
                )
                self.assertTrue(r.valid)
                self.assertEqual(r.verdict, ReviewVerdict.PASS)

    def test_level_1(self):
        r = parse_review_report_verdict_text("# Verdict\n\npass\n")
        self.assertTrue(r.valid)

    def test_level_3(self):
        r = parse_review_report_verdict_text("### Verdict\n\npass\n")
        self.assertTrue(r.valid)

    def test_level_6(self):
        r = parse_review_report_verdict_text("###### Verdict\n\npass\n")
        self.assertTrue(r.valid)

    def test_lowercase_heading_text(self):
        r = parse_review_report_verdict_text("## verdict\n\npass\n")
        self.assertTrue(r.valid)


class MultipleVerdictSectionTests(unittest.TestCase):
    def test_two_sections_refuse_in_either_order(self):
        for first, second in (("needs_work", "pass"), ("pass", "needs_work")):
            with self.subTest(first=first, second=second):
                r = parse_review_report_verdict_text(
                    f"## Verdict\n\n{first}\n\n## Findings\n\n- x\n\n"
                    f"## Verdict\n\n{second}\n"
                )
                self.assertIsNone(r.verdict)
                self.assertEqual(r.raw_verdict, "")
                self.assertFalse(r.valid)
                self.assertEqual(r.errors, (_MULTIPLE_VERDICT_SECTIONS_ERROR,))

    def test_three_sections_refuse(self):
        r = parse_review_report_verdict_text(
            "# Verdict\n\nneeds_work\n\n"
            "### VERDICT\n\nblocked\n\n"
            "###### verdict\n\npass\n"
        )
        self.assertIsNone(r.verdict)
        self.assertEqual(r.raw_verdict, "")
        self.assertFalse(r.valid)
        self.assertEqual(r.errors, (_MULTIPLE_VERDICT_SECTIONS_ERROR,))

    def test_one_heading_plus_inline_verdict_elsewhere_is_not_multiple(self):
        r = parse_review_report_verdict_text(
            "Verdict: pass\n\n## Verdict\n\nneeds_work\n\n"
            "## Notes\n\nVerdict: pass\n"
        )
        self.assertTrue(r.valid)
        self.assertEqual(r.verdict, ReviewVerdict.NEEDS_WORK)


class IgnoredPreVerdictTextTests(unittest.TestCase):
    def test_text_before_verdict_section(self):
        content = (
            "# Review Report\n\n"
            "## Summary\n\nSome summary.\n\n"
            "## Findings\n\n- minor: something\n\n"
            "## Verdict\n\npass\n"
        )
        r = parse_review_report_verdict_text(content)
        self.assertTrue(r.valid)
        self.assertEqual(r.verdict, ReviewVerdict.PASS)

    def test_word_verdict_in_body_does_not_match(self):
        content = "The verdict is unclear.\n\n## Verdict\n\npass\n"
        r = parse_review_report_verdict_text(content)
        self.assertTrue(r.valid)
        self.assertEqual(r.verdict, ReviewVerdict.PASS)

    def test_verdict_token_before_section_ignored(self):
        content = "pass\n\n## Verdict\n\nneeds_work\n"
        r = parse_review_report_verdict_text(content)
        self.assertTrue(r.valid)
        self.assertEqual(r.verdict, ReviewVerdict.NEEDS_WORK)


class MissingVerdictSectionTests(unittest.TestCase):
    def test_no_headings(self):
        r = parse_review_report_verdict_text("Just some text.\n")
        self.assertFalse(r.valid)
        self.assertIsNone(r.verdict)
        self.assertTrue(any("no verdict section" in e for e in r.errors))

    def test_wrong_heading(self):
        r = parse_review_report_verdict_text("## Findings\n\npass\n")
        self.assertFalse(r.valid)
        self.assertTrue(any("no verdict section" in e for e in r.errors))

    def test_heading_with_extra_words_not_matched(self):
        r = parse_review_report_verdict_text("## Final Verdict\n\npass\n")
        self.assertFalse(r.valid)
        self.assertTrue(any("no verdict section" in e for e in r.errors))


class EmptyVerdictSectionTests(unittest.TestCase):
    def test_verdict_section_empty(self):
        r = parse_review_report_verdict_text("## Verdict\n\n## Next\n\nstuff\n")
        self.assertFalse(r.valid)
        self.assertIsNone(r.verdict)
        self.assertTrue(any("empty" in e for e in r.errors))

    def test_verdict_section_only_blank_lines(self):
        r = parse_review_report_verdict_text("## Verdict\n\n\n\n## Next\n\nstuff\n")
        self.assertFalse(r.valid)
        self.assertTrue(any("empty" in e for e in r.errors))

    def test_verdict_section_at_end_no_content(self):
        r = parse_review_report_verdict_text("## Verdict\n\n")
        self.assertFalse(r.valid)
        self.assertTrue(any("empty" in e for e in r.errors))


class InvalidVerdictTokenTests(unittest.TestCase):
    def test_unrecognized_token(self):
        r = parse_review_report_verdict_text("## Verdict\n\napproved\n")
        self.assertFalse(r.valid)
        self.assertIsNone(r.verdict)
        self.assertEqual(r.raw_verdict, "approved")
        self.assertTrue(any("allowed verdicts" in e for e in r.errors))

    def test_partial_match_not_accepted(self):
        r = parse_review_report_verdict_text("## Verdict\n\npas\n")
        self.assertFalse(r.valid)

    def test_empty_after_stripping(self):
        r = parse_review_report_verdict_text("## Verdict\n\n``\n")
        self.assertFalse(r.valid)


class NonStringAndEmptyContentTests(unittest.TestCase):
    def test_none_content(self):
        r = parse_review_report_verdict_text(None)
        self.assertFalse(r.valid)
        self.assertTrue(any("string" in e for e in r.errors))

    def test_int_content(self):
        r = parse_review_report_verdict_text(42)
        self.assertFalse(r.valid)

    def test_empty_string(self):
        r = parse_review_report_verdict_text("")
        self.assertFalse(r.valid)
        self.assertTrue(any("empty" in e or "whitespace" in e for e in r.errors))

    def test_whitespace_only(self):
        r = parse_review_report_verdict_text("   \n\n   ")
        self.assertFalse(r.valid)


class CustomSchemaTests(unittest.TestCase):
    def test_custom_schema_restricts_verdicts(self):
        schema = ReviewReportSchema(
            required_fields=("verdict", "findings", "review notes",
                             "verification", "residual risk", "memory"),
            allowed_verdicts=(ReviewVerdict.PASS,),
        )
        r = parse_review_report_verdict_text("## Verdict\n\npass\n", schema=schema)
        self.assertTrue(r.valid)
        self.assertEqual(r.verdict, ReviewVerdict.PASS)

    def test_custom_schema_rejects_excluded_verdict(self):
        schema = ReviewReportSchema(
            required_fields=("verdict", "findings", "review notes",
                             "verification", "residual risk", "memory"),
            allowed_verdicts=(ReviewVerdict.PASS,),
        )
        r = parse_review_report_verdict_text("## Verdict\n\nneeds_work\n", schema=schema)
        self.assertFalse(r.valid)
        self.assertIsNone(r.verdict)

    def test_none_schema_uses_default(self):
        r = parse_review_report_verdict_text("## Verdict\n\npass\n", schema=None)
        self.assertTrue(r.valid)


class InvalidSchemaTests(unittest.TestCase):
    def test_non_schema_object_produces_errors(self):
        r = parse_review_report_verdict_text("## Verdict\n\npass\n", schema=42)
        self.assertFalse(r.valid)
        self.assertTrue(len(r.errors) > 0)

    def test_schema_with_empty_allowed_verdicts_produces_errors(self):
        schema = ReviewReportSchema(
            required_fields=("verdict", "findings", "review notes",
                             "verification", "residual risk", "memory"),
            allowed_verdicts=(),
        )
        r = parse_review_report_verdict_text("## Verdict\n\npass\n", schema=schema)
        self.assertFalse(r.valid)

    def test_none_allowed_verdicts_produces_errors(self):
        schema = ReviewReportSchema(
            required_fields=("verdict", "findings", "review notes",
                             "verification", "residual risk", "memory"),
            allowed_verdicts=None,
        )
        r = parse_review_report_verdict_text("## Verdict\n\npass\n", schema=schema)
        self.assertFalse(r.valid)
        self.assertTrue(len(r.errors) > 0)


class FenceSkippingTests(unittest.TestCase):
    def test_fence_line_skipped(self):
        content = "## Verdict\n\n```\nneeds_work\n```\n\npass\n"
        r = parse_review_report_verdict_text(content)
        self.assertTrue(r.valid)
        self.assertEqual(r.verdict, ReviewVerdict.NEEDS_WORK)

    def test_tilde_fence_skipped(self):
        content = "## Verdict\n\n~~~\npass\n"
        r = parse_review_report_verdict_text(content)
        self.assertTrue(r.valid)
        self.assertEqual(r.verdict, ReviewVerdict.PASS)


class FileCommandSuccessTests(unittest.TestCase):
    def test_file_command_success(self):
        content = "## Verdict\n\npass\n"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            tmp = Path(f.name)
        try:
            cmd = ReviewReportVerdictParseCommand(path=tmp)
            r = parse_review_report_verdict(cmd)
            self.assertTrue(r.valid)
            self.assertEqual(r.verdict, ReviewVerdict.PASS)
            self.assertEqual(r.path, str(tmp))
        finally:
            tmp.unlink(missing_ok=True)

    def test_file_command_path_in_result(self):
        content = "## Verdict\n\nneeds_work\n"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            tmp = Path(f.name)
        try:
            cmd = ReviewReportVerdictParseCommand(path=tmp)
            r = parse_review_report_verdict(cmd)
            self.assertIn(str(tmp), r.path)
        finally:
            tmp.unlink(missing_ok=True)

    def test_file_command_propagates_multiple_section_refusal(self):
        content = "## Verdict\n\nneeds_work\n\n## Verdict\n\npass\n"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            tmp = Path(f.name)
        try:
            r = parse_review_report_verdict(ReviewReportVerdictParseCommand(path=tmp))
            self.assertEqual(r.path, str(tmp))
            self.assertIsNone(r.verdict)
            self.assertEqual(r.raw_verdict, "")
            self.assertFalse(r.valid)
            self.assertEqual(r.errors, (_MULTIPLE_VERDICT_SECTIONS_ERROR,))
        finally:
            tmp.unlink(missing_ok=True)


class MalformedCommandPathTests(unittest.TestCase):
    """Regression tests for M007-S02 corrective: command path never raises."""

    def test_int_path_returns_failed_result(self):
        cmd = ReviewReportVerdictParseCommand(path=42)
        r = parse_review_report_verdict(cmd)
        self.assertFalse(r.valid)
        self.assertIsNone(r.verdict)
        self.assertEqual(r.raw_verdict, "")
        self.assertTrue(len(r.errors) > 0)

    def test_none_path_returns_failed_result(self):
        cmd = ReviewReportVerdictParseCommand(path=None)
        r = parse_review_report_verdict(cmd)
        self.assertFalse(r.valid)
        self.assertIsNone(r.verdict)
        self.assertEqual(r.raw_verdict, "")
        self.assertTrue(len(r.errors) > 0)

    def test_string_path_returns_failed_result(self):
        cmd = ReviewReportVerdictParseCommand(path="x.md")
        r = parse_review_report_verdict(cmd)
        self.assertFalse(r.valid)
        self.assertIsNone(r.verdict)
        self.assertEqual(r.raw_verdict, "")
        self.assertTrue(len(r.errors) > 0)

    def test_int_path_does_not_raise(self):
        cmd = ReviewReportVerdictParseCommand(path=42)
        try:
            parse_review_report_verdict(cmd)
        except Exception as exc:
            self.fail(f"parse_review_report_verdict raised unexpectedly: {exc}")

    def test_none_path_does_not_raise(self):
        cmd = ReviewReportVerdictParseCommand(path=None)
        try:
            parse_review_report_verdict(cmd)
        except Exception as exc:
            self.fail(f"parse_review_report_verdict raised unexpectedly: {exc}")

    def test_string_path_does_not_raise(self):
        cmd = ReviewReportVerdictParseCommand(path="x.md")
        try:
            parse_review_report_verdict(cmd)
        except Exception as exc:
            self.fail(f"parse_review_report_verdict raised unexpectedly: {exc}")

    def test_int_path_result_has_path_string(self):
        cmd = ReviewReportVerdictParseCommand(path=42)
        r = parse_review_report_verdict(cmd)
        self.assertEqual(r.path, "42")

    def test_none_path_result_has_path_string(self):
        cmd = ReviewReportVerdictParseCommand(path=None)
        r = parse_review_report_verdict(cmd)
        self.assertEqual(r.path, "None")

    def test_string_path_result_has_path_string(self):
        cmd = ReviewReportVerdictParseCommand(path="x.md")
        r = parse_review_report_verdict(cmd)
        self.assertEqual(r.path, "x.md")

    def test_malformed_path_to_dict_contains_plain_values(self):
        cmd = ReviewReportVerdictParseCommand(path=42)
        r = parse_review_report_verdict(cmd)
        d = r.to_dict()
        self.assertIsInstance(d["path"], str)
        self.assertIsNone(d["verdict"])
        self.assertIsInstance(d["errors"], list)

    def test_normal_path_still_works(self):
        content = "## Verdict\n\npass\n"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            tmp = Path(f.name)
        try:
            cmd = ReviewReportVerdictParseCommand(path=tmp)
            r = parse_review_report_verdict(cmd)
            self.assertTrue(r.valid)
            self.assertEqual(r.verdict, ReviewVerdict.PASS)
        finally:
            tmp.unlink(missing_ok=True)


class FileCommandErrorTests(unittest.TestCase):
    def test_missing_file(self):
        tmp = Path(tempfile.gettempdir()) / "nonexistent_frutlups_test_12345.md"
        cmd = ReviewReportVerdictParseCommand(path=tmp)
        r = parse_review_report_verdict(cmd)
        self.assertFalse(r.valid)
        self.assertTrue(any("not found" in e or "not exist" in e.lower() for e in r.errors))
        self.assertEqual(r.path, str(tmp))

    def test_directory_path(self):
        tmp_dir = Path(tempfile.gettempdir())
        cmd = ReviewReportVerdictParseCommand(path=tmp_dir)
        r = parse_review_report_verdict(cmd)
        self.assertFalse(r.valid)
        self.assertTrue(any("directory" in e for e in r.errors))

    def test_invalid_schema_rejected_before_file_access(self):
        content = "## Verdict\n\npass\n"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            tmp = Path(f.name)
        try:
            schema = ReviewReportSchema(
                required_fields=("verdict", "findings", "review notes",
                                  "verification", "residual risk", "memory"),
                allowed_verdicts=(),
            )
            cmd = ReviewReportVerdictParseCommand(path=tmp, schema=schema)
            r = parse_review_report_verdict(cmd)
            self.assertFalse(r.valid)
            self.assertTrue(len(r.errors) > 0)
        finally:
            tmp.unlink(missing_ok=True)


class FrozenBehaviorTests(unittest.TestCase):
    def test_result_frozen(self):
        r = parse_review_report_verdict_text("## Verdict\n\npass\n")
        with self.assertRaises((AttributeError, TypeError)):
            r.valid = False

    def test_command_frozen(self):
        cmd = ReviewReportVerdictParseCommand(path=Path("/tmp/x.md"))
        with self.assertRaises((AttributeError, TypeError)):
            cmd.path = Path("/tmp/y.md")

    def test_result_to_dict_shape(self):
        r = parse_review_report_verdict_text("## Verdict\n\npass\n")
        d = r.to_dict()
        self.assertIn("path", d)
        self.assertIn("verdict", d)
        self.assertIn("raw_verdict", d)
        self.assertIn("valid", d)
        self.assertIn("errors", d)

    def test_result_to_dict_plain_values(self):
        r = parse_review_report_verdict_text("## Verdict\n\nneeds_work\n")
        d = r.to_dict()
        self.assertIsInstance(d["verdict"], str)
        self.assertIsInstance(d["raw_verdict"], str)
        self.assertIsInstance(d["valid"], bool)
        self.assertIsInstance(d["errors"], list)

    def test_result_to_dict_no_enum_objects(self):
        r = parse_review_report_verdict_text("## Verdict\n\npass\n")
        d = r.to_dict()
        for v in d.values():
            self.assertNotIsInstance(v, ReviewVerdict)

    def test_failed_result_to_dict_verdict_none(self):
        r = parse_review_report_verdict_text("no verdict section here")
        d = r.to_dict()
        self.assertIsNone(d["verdict"])
        self.assertFalse(d["valid"])

    def test_path_empty_string_for_text_parser(self):
        r = parse_review_report_verdict_text("## Verdict\n\npass\n")
        self.assertEqual(r.path, "")
        self.assertEqual(r.to_dict()["path"], "")


class NoFilesystemSideEffectsTests(unittest.TestCase):
    def test_text_parser_does_not_write(self):
        import os
        before = set(os.listdir(tempfile.gettempdir()))
        parse_review_report_verdict_text("## Verdict\n\npass\n")
        after = set(os.listdir(tempfile.gettempdir()))
        self.assertEqual(before, after)

    def test_file_command_does_not_write(self):
        import os
        tmp = Path(tempfile.gettempdir()) / "frutlups_nonexistent_dir_scan_test"
        before = set(os.listdir(tempfile.gettempdir()))
        cmd = ReviewReportVerdictParseCommand(path=tmp)
        parse_review_report_verdict(cmd)
        after = set(os.listdir(tempfile.gettempdir()))
        self.assertEqual(before, after)


class ParserProbeTests(unittest.TestCase):
    """Reproduces the probe from the coding prompt definition of done."""

    def test_prompt_probe(self):
        content = (
            "# Review Report\n\n"
            "## Verdict\n\n"
            "`needs_work`\n\n"
            "## Findings\n\n"
            "- major: example\n"
        )
        r = parse_review_report_verdict_text(content)
        self.assertTrue(r.valid)
        self.assertEqual(r.verdict.value if r.verdict else None, "needs_work")
        self.assertEqual(r.raw_verdict, "needs_work")
        self.assertEqual(r.errors, ())
        d = r.to_dict()
        self.assertIsInstance(d["verdict"], str)
        self.assertIsInstance(d["raw_verdict"], str)
        self.assertIsInstance(d["valid"], bool)
        self.assertIsInstance(d["errors"], list)


class HistoricalNextFooterCompatibilityTests(unittest.TestCase):
    """Prompt 030 bounded parser-compatibility rule: the accepted historical
    ``Verdict: <verdict> - next: <recommended move>`` footer (M003 governance
    reports 017-028) parses to the canonical typed verdict; hostile near
    misses stay invalid and no verdict is ever converted into another."""

    def test_headingless_inline_footer_parses_all_canonical_verdicts(self) -> None:
        # Reports 017-027 shape: no ## Verdict heading, single-line footer.
        for token in ("pass", "needs_work", "blocked", "override"):
            with self.subTest(token=token):
                text = (
                    "# Report\n\n## 6. Recommended next move\n\nDo the thing.\n\n"
                    f"Verdict: {token} - next: emit the receipt and commit the boundary\n"
                )
                result = parse_review_report_verdict_text(text)
                self.assertTrue(result.valid, result.errors)
                self.assertEqual(result.verdict, ReviewVerdict(token))
                self.assertEqual(result.raw_verdict, token)

    def test_heading_section_footer_with_wrapped_annotation_parses(self) -> None:
        # Report 028 shape: ## Verdict heading, labeled footer, annotation
        # hard-wrapped onto the next line.
        text = (
            "# Report\n\n## Verdict\n\n"
            "Verdict: needs_work - next: execute one focused correction,\n"
            "containment, identity, and durable-snapshot correction\n"
        )
        result = parse_review_report_verdict_text(text)
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.verdict, ReviewVerdict.NEEDS_WORK)
        self.assertEqual(result.raw_verdict, "needs_work")

    def test_unlabeled_section_footer_parses(self) -> None:
        text = "## Verdict\n\npass - next: emit the receipt\n"
        result = parse_review_report_verdict_text(text)
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.verdict, ReviewVerdict.PASS)

    def test_near_misses_stay_invalid(self) -> None:
        cases = (
            "## Verdict\n\npass - next:\n",  # empty annotation
            "## Verdict\n\npass - next:   \n",  # whitespace-only annotation
            "## Verdict\n\npass -next: x\n",  # missing space before separator
            "## Verdict\n\npass - Next: x\n",  # cased separator
            "## Verdict\n\npass or fail - next: x\n",  # extra tokens
            "## Verdict\n\npassing - next: x\n",  # unknown verdict token
            "Verdict: pass- next: x\n",  # fused token/hyphen
            "Verdict: pass - later: x\n",  # different annotation label
        )
        for text in cases:
            with self.subTest(text=text):
                result = parse_review_report_verdict_text(text)
                self.assertFalse(result.valid)
                self.assertIsNone(result.verdict)

    def test_rule_never_converts_verdicts(self) -> None:
        # The token before the separator is matched exactly against the
        # allowed set; annotation content cannot leak into the verdict.
        text = "## Verdict\n\nneeds_work - next: pass everything and accept\n"
        result = parse_review_report_verdict_text(text)
        self.assertTrue(result.valid)
        self.assertEqual(result.verdict, ReviewVerdict.NEEDS_WORK)

    def test_file_command_parses_historical_footer(self) -> None:
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            report = Path(tmp) / "m003_s02_x_review_report.md"
            report.write_text(
                "# Report\n\nVerdict: pass - next: emit the receipt\n",
                encoding="utf-8",
            )
            result = parse_review_report_verdict(
                ReviewReportVerdictParseCommand(path=report)
            )
            self.assertTrue(result.valid, result.errors)
            self.assertEqual(result.verdict, ReviewVerdict.PASS)


if __name__ == "__main__":
    unittest.main()
