"""Tests for the aggregate self-report findings surface."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from frutlups.prompt_template import CodingPromptTemplate
from frutlups.self_report import (
    SELF_REPORT_REQUIRED_FIELDS,
    SelfReportFinding,
    SelfReportFindingSeverity,
    SelfReportFindingsCommand,
    SelfReportFindingsResult,
    SelfReportSchema,
    collect_self_report_findings,
    default_self_report_schema,
)


VALID_REPORT_BODY = """\
# Self Report

## Files Changed

- path: x.py

## Behavior Implemented

implemented findings

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

prompts/for_review_agent/020_review_frutlups_m005_s04_self_report_findings.md

## Blockers Or Open Questions

none
"""


def _template(
    *,
    sequence: int = 20,
    milestone_id: str = "M005",
    slice_id: str = "M005-S04",
    slug: str = "frutlups_m005_s04_self_report_findings",
    title: str = "Self-Report Findings",
    self_report_path: str = "05_governance/reviews/report.md",
    **overrides: object,
) -> CodingPromptTemplate:
    defaults: dict[str, object] = dict(
        sequence=sequence,
        milestone_id=milestone_id,
        slice_id=slice_id,
        slug=slug,
        title=title,
        role_instructions="You are the coding agent for frutlups.",
        required_reading=("CLAUDE.md", "README.md"),
        scope_paths=("08_pkg/src/frutlups/",),
        non_goals=("do not generate review prompts",),
        definition_of_done=("findings exist",),
        verification_commands=("python -m unittest discover -s tests",),
        self_report_path=self_report_path,
    )
    defaults.update(overrides)
    return CodingPromptTemplate(**defaults)  # type: ignore[arg-type]


def _write_report(
    root: Path,
    *,
    relative_path: str = "05_governance/reviews/report.md",
    body: str = VALID_REPORT_BODY,
) -> Path:
    target = root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return target


class ShapeAndSerializationTests(unittest.TestCase):
    def test_severity_enum_values(self) -> None:
        self.assertEqual(SelfReportFindingSeverity.ERROR.value, "error")
        self.assertEqual(SelfReportFindingSeverity.WARNING.value, "warning")

    def test_finding_to_dict_shape(self) -> None:
        finding = SelfReportFinding(
            code="missing_self_report",
            severity=SelfReportFindingSeverity.ERROR,
            sequence=21,
            milestone_id="M006",
            slice_id="M006-S01",
            self_report_path="/tmp/x.md",
            message="missing",
            errors=("e",),
        )
        self.assertEqual(
            finding.to_dict(),
            {
                "code": "missing_self_report",
                "severity": "error",
                "sequence": 21,
                "milestone_id": "M006",
                "slice_id": "M006-S01",
                "self_report_path": "/tmp/x.md",
                "message": "missing",
                "errors": ["e"],
            },
        )

    def test_command_is_frozen(self) -> None:
        with TemporaryDirectory() as tmp:
            command = SelfReportFindingsCommand(
                project_root=Path(tmp),
                templates=(),
            )
            with self.assertRaises(Exception):
                command.templates = ()  # type: ignore[misc]

    def test_result_is_frozen(self) -> None:
        with TemporaryDirectory() as tmp:
            result = collect_self_report_findings(
                SelfReportFindingsCommand(
                    project_root=Path(tmp),
                    templates=(),
                )
            )
            with self.assertRaises(Exception):
                result.ok = False  # type: ignore[misc]

    def test_result_to_dict_shape(self) -> None:
        with TemporaryDirectory() as tmp:
            result = collect_self_report_findings(
                SelfReportFindingsCommand(
                    project_root=Path(tmp),
                    templates=(_template(),),
                )
            )
        payload = result.to_dict()
        self.assertEqual(set(payload.keys()), {"ok", "checked", "findings"})
        self.assertIsInstance(payload["ok"], bool)
        self.assertIsInstance(payload["checked"], int)
        self.assertIsInstance(payload["findings"], list)

    def test_command_default_schema_is_canonical(self) -> None:
        with TemporaryDirectory() as tmp:
            command = SelfReportFindingsCommand(
                project_root=Path(tmp),
                templates=(),
            )
            self.assertEqual(
                command.schema.required_fields, SELF_REPORT_REQUIRED_FIELDS
            )
            self.assertEqual(
                command.schema.kind, default_self_report_schema().kind
            )

    def test_result_type_identity(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertIsInstance(
                collect_self_report_findings(
                    SelfReportFindingsCommand(
                        project_root=Path(tmp),
                        templates=(),
                    )
                ),
                SelfReportFindingsResult,
            )


class HappyPathTests(unittest.TestCase):
    def test_empty_template_tuple_is_ok(self) -> None:
        with TemporaryDirectory() as tmp:
            result = collect_self_report_findings(
                SelfReportFindingsCommand(
                    project_root=Path(tmp),
                    templates=(),
                )
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.checked, 0)
        self.assertEqual(result.findings, ())

    def test_single_valid_report_yields_no_findings(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_report(root)
            result = collect_self_report_findings(
                SelfReportFindingsCommand(
                    project_root=root,
                    templates=(_template(),),
                )
            )

        self.assertTrue(result.ok, result.findings)
        self.assertEqual(result.checked, 1)
        self.assertEqual(result.findings, ())

    def test_multiple_valid_reports_yield_no_findings(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_report(root, relative_path="05_governance/reviews/a.md")
            _write_report(root, relative_path="05_governance/reviews/b.md")
            t_a = _template(
                sequence=20,
                slug="a",
                self_report_path="05_governance/reviews/a.md",
            )
            t_b = _template(
                sequence=21,
                milestone_id="M006",
                slice_id="M006-S01",
                slug="b",
                self_report_path="05_governance/reviews/b.md",
            )

            result = collect_self_report_findings(
                SelfReportFindingsCommand(
                    project_root=root,
                    templates=(t_a, t_b),
                )
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.checked, 2)


class PerCodeTests(unittest.TestCase):
    def test_missing_self_report_finding(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = _template(
                sequence=21,
                milestone_id="M006",
                slice_id="M006-S01",
                slug="future_missing",
                self_report_path="05_governance/reviews/missing.md",
            )

            result = collect_self_report_findings(
                SelfReportFindingsCommand(
                    project_root=root,
                    templates=(template,),
                )
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.checked, 1)
        self.assertEqual(len(result.findings), 1)
        finding = result.findings[0]
        self.assertEqual(finding.code, "missing_self_report")
        self.assertEqual(finding.severity, SelfReportFindingSeverity.ERROR)
        self.assertEqual(finding.sequence, 21)
        self.assertEqual(finding.milestone_id, "M006")
        self.assertEqual(finding.slice_id, "M006-S01")
        self.assertIn("self-report file is missing", finding.errors)
        self.assertIn("021", finding.message)

    def test_directory_target_finding(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            target_dir = root / "05_governance" / "reviews" / "report.md"
            target_dir.mkdir(parents=True)

            result = collect_self_report_findings(
                SelfReportFindingsCommand(
                    project_root=root,
                    templates=(_template(),),
                )
            )

        self.assertFalse(result.ok)
        self.assertEqual(len(result.findings), 1)
        self.assertEqual(
            result.findings[0].code, "self_report_path_is_directory"
        )
        self.assertIn(
            "self-report path is a directory", result.findings[0].errors
        )

    def test_invalid_template_finding(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad = _template(sequence=0)  # validator rejects

            result = collect_self_report_findings(
                SelfReportFindingsCommand(
                    project_root=root,
                    templates=(bad,),
                )
            )

        self.assertFalse(result.ok)
        self.assertEqual(len(result.findings), 1)
        finding = result.findings[0]
        self.assertEqual(finding.code, "invalid_self_report_template")
        self.assertIn(
            "sequence must be a positive integer", finding.errors
        )
        # Sequence is captured best-effort (zero is still an int).
        self.assertEqual(finding.sequence, 0)

    def test_invalid_template_path_safety_finding(self) -> None:
        # An absolute self_report_path is a path-safety failure from
        # the M005-S02 locator.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = _template(
                self_report_path=str(Path(tmp) / "outside.md")
            )

            result = collect_self_report_findings(
                SelfReportFindingsCommand(
                    project_root=root,
                    templates=(template,),
                )
            )

        self.assertFalse(result.ok)
        finding = result.findings[0]
        self.assertEqual(finding.code, "invalid_self_report_template")
        self.assertIn(
            "self_report_path must be repo-relative", finding.errors
        )

    def test_incomplete_self_report_finding(self) -> None:
        body = VALID_REPORT_BODY.replace(
            "## Memory Usage\n\nmemory: not_used\n", ""
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_report(root, body=body)

            result = collect_self_report_findings(
                SelfReportFindingsCommand(
                    project_root=root,
                    templates=(_template(),),
                )
            )

        self.assertFalse(result.ok)
        finding = result.findings[0]
        self.assertEqual(finding.code, "incomplete_self_report")
        self.assertIn(
            "self-report missing required field: memory usage statement",
            finding.errors,
        )

    def test_invalid_schema_yields_single_schema_finding(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_report(root)
            bad_schema = SelfReportSchema(required_fields=42)  # type: ignore[arg-type]
            template = _template()

            result = collect_self_report_findings(
                SelfReportFindingsCommand(
                    project_root=root,
                    templates=(template, template),
                    schema=bad_schema,
                )
            )

        self.assertFalse(result.ok)
        # A single schema-level finding short-circuits per-template work.
        self.assertEqual(len(result.findings), 1)
        finding = result.findings[0]
        self.assertEqual(finding.code, "invalid_self_report_schema")
        self.assertEqual(finding.sequence, None)
        self.assertEqual(finding.milestone_id, "")
        self.assertEqual(finding.slice_id, "")
        self.assertIn(
            "required_fields must be a tuple or list of non-empty strings",
            finding.errors,
        )
        # `checked` still reflects the number of supplied templates.
        self.assertEqual(result.checked, 2)


class OrderAndAggregateTests(unittest.TestCase):
    def test_preserves_template_order(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_report(root, relative_path="05_governance/reviews/middle.md")

            t1 = _template(
                sequence=20,
                slug="a",
                self_report_path="05_governance/reviews/first.md",  # missing
            )
            t2 = _template(
                sequence=21,
                slug="b",
                self_report_path="05_governance/reviews/middle.md",  # valid
            )
            t3 = _template(
                sequence=22,
                slug="c",
                self_report_path="05_governance/reviews/third.md",  # missing
            )

            result = collect_self_report_findings(
                SelfReportFindingsCommand(
                    project_root=root,
                    templates=(t1, t2, t3),
                )
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.checked, 3)
        self.assertEqual([f.sequence for f in result.findings], [20, 22])
        self.assertEqual(
            [f.code for f in result.findings],
            ["missing_self_report", "missing_self_report"],
        )

    def test_checked_equals_supplied_template_count(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_report(root)
            result = collect_self_report_findings(
                SelfReportFindingsCommand(
                    project_root=root,
                    templates=(_template(), _template(), _template()),
                )
            )

        # All three reference the same valid path; checked == 3.
        self.assertTrue(result.ok)
        self.assertEqual(result.checked, 3)


class MalformedInputTests(unittest.TestCase):
    def test_fully_malformed_template_does_not_raise(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            try:
                result = collect_self_report_findings(
                    SelfReportFindingsCommand(
                        project_root=root,
                        templates=(
                            _template(
                                sequence=0,
                                slug="",
                                title="",
                                role_instructions="",
                                required_reading=42,  # type: ignore[arg-type]
                                self_report_path="",
                            ),
                        ),
                    )
                )
            except Exception as exc:  # pragma: no cover - guard rail
                self.fail(f"helper raised {type(exc).__name__}: {exc}")

            self.assertFalse(result.ok)
            self.assertEqual(len(result.findings), 1)
            self.assertEqual(
                result.findings[0].code, "invalid_self_report_template"
            )
            # sequence=0 is still an int, so best-effort context.
            self.assertEqual(result.findings[0].sequence, 0)

    def test_fully_malformed_schema_yields_schema_finding_without_raising(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            try:
                result = collect_self_report_findings(
                    SelfReportFindingsCommand(
                        project_root=root,
                        templates=(_template(),),
                        schema=SelfReportSchema(
                            required_fields=42,  # type: ignore[arg-type]
                            optional_fields=None,  # type: ignore[arg-type]
                            kind="",
                            version="",
                        ),
                    )
                )
            except Exception as exc:  # pragma: no cover - guard rail
                self.fail(f"helper raised {type(exc).__name__}: {exc}")

            self.assertFalse(result.ok)
            self.assertEqual(len(result.findings), 1)
            self.assertEqual(
                result.findings[0].code, "invalid_self_report_schema"
            )


class NoSideEffectTests(unittest.TestCase):
    def test_helper_does_not_write_any_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_report(root)
            before = sorted(
                path.relative_to(root) for path in root.rglob("*")
            )

            collect_self_report_findings(
                SelfReportFindingsCommand(
                    project_root=root,
                    templates=(_template(),),
                )
            )

            after = sorted(
                path.relative_to(root) for path in root.rglob("*")
            )
            self.assertEqual(before, after)

    def test_helper_does_not_pick_up_distractor_reviews(self) -> None:
        # Smoke check: extra files alongside the explicit target must
        # not influence the result.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_report(root)
            distractor = (
                root / "05_governance" / "reviews" / "distractor.md"
            )
            distractor.write_text(
                "# Distractor\n\nSHOULD NOT APPEAR\n",
                encoding="utf-8",
            )

            result = collect_self_report_findings(
                SelfReportFindingsCommand(
                    project_root=root,
                    templates=(_template(),),
                )
            )

        self.assertTrue(result.ok)
        for finding in result.findings:
            self.assertNotIn("SHOULD NOT APPEAR", finding.message)
            for err in finding.errors:
                self.assertNotIn("SHOULD NOT APPEAR", err)


if __name__ == "__main__":
    unittest.main()
