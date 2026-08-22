"""Writer-side ordinary corrective-round detection (Q010)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from frutlups.layout import legacy_profile
from frutlups.project import (
    VerdictRecordWriteCommand,
    _build_coding_prompt_plan_from_status,
    _build_review_prompt_plan_from_status,
    _build_status_with_evidence,
    _detect_ordinary_correction_round,
    build_coding_prompt_plan,
    build_rework_declaration_plan,
    build_review_prompt_plan,
    build_status,
    build_verdict_record_plan,
    write_verdict_record,
)
from frutlups.rework import (
    ReworkDeclarationWriteCommand,
    write_rework_declaration,
)


REVIEWS = "05_governance/reviews"
SELF_REPORT = f"{REVIEWS}/m001_s01_test_slice_self_report.md"
ROUND_TWO_SELF_REPORT = f"{REVIEWS}/m001_s01_test_slice_round_002_self_report.md"
ROUND_THREE_SELF_REPORT = f"{REVIEWS}/m001_s01_test_slice_round_003_self_report.md"
ROUND_ONE_REPORT = f"{REVIEWS}/m001_s01_test_slice_review_report.md"
ROUND_TWO_REPORT = f"{REVIEWS}/m001_s01_test_slice_round_002_review_report.md"


def _make_project(root: Path) -> None:
    for name in (
        "00_brief",
        "03_experiments",
        REVIEWS,
        "06_infra",
        "08_pkg",
        "prompts/for_coding_agent",
        "prompts/for_review_agent",
    ):
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
        "# Active Roadmap\n\n### M001: First\n\nStatus: active\n",
        encoding="utf-8",
    )
    (root / "03_experiments" / "development_roadmap_frutlups.md").write_text(
        "# Detailed Roadmap\n\n### M001: First\n\n"
        "Slices:\n\n- M001-S01: test slice\n",
        encoding="utf-8",
    )


def _write_report(root: Path, relative: str, verdict_body: str) -> Path:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        f"# Review Report\n\n## Verdict\n\n{verdict_body}\n",
        encoding="utf-8",
    )
    return target


def _coding_prompt(self_report_path: str = SELF_REPORT, *, metadata: bool = False) -> str:
    metadata_block = (
        "Workflow metadata:\n\n"
        "```yaml\n"
        "milestone: M001\n"
        "slice: M001-S01\n"
        "title: test slice\n"
        "role: coder\n"
        "```\n\n"
        if metadata
        else ""
    )
    return (
        "# Coding Prompt 001: frutlups M001-S01 test slice\n\n"
        f"{metadata_block}"
        "## Active Roadmap Item\n\n"
        "Active roadmap milestone: `M001`\n\n"
        "Detailed roadmap slice: `M001-S01: test slice`\n\n"
        "## Required Reading\n\n- `CLAUDE.md`\n- `README.md`\n\n"
        "## Non-Goals\n\n- Do not do X.\n\n"
        "## Self-Report\n\n"
        f"Write the self-report at `{self_report_path}`.\n\n"
        "## Required Self-Report\n\n"
        f"Write the self-report at `{self_report_path}`.\n"
    )


def _legacy_self_report() -> str:
    return (
        "# Self-Report\n\n"
        "## Files Changed\n\n- 08_pkg/src/frutlups/project.py\n\n"
        "## Behavior Implemented\n\nImplemented.\n\n"
        "## Tests Added or Updated\n\n- test_corrective_round_detection\n\n"
        "## Verification Commands and Results\n\n"
        "```\npython -m unittest discover -s tests\n```\n\n"
        "## Live Status Summary\n\nCurrent fixture state.\n\n"
        "## Known Limits and Intentional Deferrals\n\nNone.\n\n"
        "## Memory Usage Statement\n\nNo memory used.\n\n"
        "## Matching Review Prompt Path Created by the Coder\n\nNone.\n\n"
        "## Blockers or Open Questions\n\nNone.\n"
    )


def _canonical_self_report() -> str:
    return (
        "# Coder Self-Report\n\n"
        "## Intent\n\nImplement the fixture.\n\n"
        "## Files Changed\n\n- fixture_module.py\n\n"
        "## Behavior Implemented\n\nImplemented.\n\n"
        "## Tests Added Or Updated\n\n- test_fixture.py\n\n"
        "## Verification Run\n\npython -m unittest discover -s tests\n\n"
        "## Definition Of Done Audit\n\nMet.\n\n"
        "## Non-Goals Confirmed\n\nConfirmed.\n\n"
        "## Memory Used\n\nNone.\n\n"
        "## Memory Update Requested\n\nNone.\n\n"
        "## Known Limits / Follow-Up\n\nNone.\n\n"
        "## Recommended Next Move\n\nReview.\n"
    )


def _write_review_inputs(
    root: Path,
    *,
    self_report_path: str = SELF_REPORT,
    metadata: bool = False,
    canonical_schema: bool = False,
) -> None:
    (root / "prompts" / "for_coding_agent" / "001_m001_s01_test_slice.md").write_text(
        _coding_prompt(self_report_path, metadata=metadata), encoding="utf-8"
    )
    target = root / self_report_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        _canonical_self_report() if canonical_schema else _legacy_self_report(),
        encoding="utf-8",
    )


def _metadata_layout(
    *,
    self_report_suffix: str = "_self_report.md",
    review_report_suffix: str = "_review_report.md",
    configured_review: bool = False,
) -> str:
    review_template = (
        '  review_template: "prompts/templates/review_prompt.md"\n'
        if configured_review
        else '  review_template: ""\n'
    )
    return (
        "schema_version: frutlups_layout_config_v0\n"
        "profile_id: artifact_first_template_v3\n"
        "prompts:\n"
        '  coding_template: ""\n'
        f"{review_template}"
        '  numbering: "global_flat_sequence"\n'
        '  pairing: "workflow_metadata"\n'
        "  metadata:\n"
        "    parse_front_matter: true\n"
        "    milestone_field: milestone\n"
        "    slice_field: slice\n"
        "    title_field: title\n"
        "reports:\n"
        f'  reviews_dir: "{REVIEWS}"\n'
        '  discovery: "recursive_contained"\n'
        f'  self_report_suffix: "{self_report_suffix}"\n'
        f'  review_report_suffix: "{review_report_suffix}"\n'
        '  verdict_record_suffix: "_verdict_record.md"\n'
    )


def _make_metadata_project(
    root: Path,
    *,
    self_report_suffix: str = "_self_report.md",
    review_report_suffix: str = "_review_report.md",
    configured_review: bool = False,
) -> None:
    _make_project(root)
    (root / "questions").mkdir()
    (root / "PROJECT_STATE.md").write_text(
        "# Project State\n\nMemory mode:\n- none\n\nFrutlups mode:\n- manual\n",
        encoding="utf-8",
    )
    (root / "frutlups.layout.yaml").write_text(
        _metadata_layout(
            self_report_suffix=self_report_suffix,
            review_report_suffix=review_report_suffix,
            configured_review=configured_review,
        ),
        encoding="utf-8",
    )
    if configured_review:
        fixture = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "front_repo_contract"
            / "review_prompt.md"
        )
        target = root / "prompts" / "templates" / "review_prompt.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")


class DetectionSemanticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        _make_project(self.root)
        self.profile = legacy_profile()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _detect(self) -> int | None:
        return _detect_ordinary_correction_round(
            "M001-S01", self.profile, self.root
        )

    def test_round_one_verdict_matrix(self) -> None:
        cases = {
            "needs_work": 2,
            "invalid": 2,
            "pass": None,
            "blocked": None,
            "override": None,
        }
        for verdict, expected in cases.items():
            with self.subTest(verdict=verdict):
                report = self.root / ROUND_ONE_REPORT
                report.unlink(missing_ok=True)
                body = verdict if verdict != "invalid" else "not_a_verdict"
                _write_report(self.root, ROUND_ONE_REPORT, body)
                self.assertEqual(self._detect(), expected)

    def test_no_matching_report_is_round_one(self) -> None:
        self.assertIsNone(self._detect())

    def test_refused_multi_verdict_advances(self) -> None:
        (self.root / ROUND_ONE_REPORT).write_text(
            "## Verdict\n\nneeds_work\n\n## Verdict\n\npass\n",
            encoding="utf-8",
        )
        self.assertEqual(self._detect(), 2)

    def test_only_highest_round_controls(self) -> None:
        _write_report(self.root, ROUND_ONE_REPORT, "pass")
        _write_report(self.root, ROUND_TWO_REPORT, "needs_work")
        self.assertEqual(self._detect(), 3)
        _write_report(
            self.root,
            f"{REVIEWS}/m001_s01_second_round_002_review_report.md",
            "blocked",
        )
        self.assertIsNone(self._detect())

    def test_informal_round2_spelling_remains_round_one(self) -> None:
        _write_report(
            self.root,
            f"{REVIEWS}/m001_s01_test_slice_round2_review_report.md",
            "needs_work",
        )
        self.assertEqual(self._detect(), 2)

    def test_recursive_contained_and_custom_suffixes(self) -> None:
        profile = replace(
            self.profile,
            reports_discovery="recursive_contained",
            review_report_suffix="_assessment.md",
        )
        _write_report(
            self.root,
            f"{REVIEWS}/m001/m001_s01_test_slice_assessment.md",
            "needs_work",
        )
        self.assertEqual(
            _detect_ordinary_correction_round("M001-S01", profile, self.root),
            2,
        )

    def test_round_999_requests_fail_closed_value(self) -> None:
        _write_report(
            self.root,
            f"{REVIEWS}/m001_s01_test_slice_round_999_review_report.md",
            "needs_work",
        )
        self.assertEqual(self._detect(), 1000)

    def test_read_failure_degrades_to_round_one(self) -> None:
        _write_report(self.root, ROUND_ONE_REPORT, "needs_work")
        invalid = mock.Mock(
            valid=False,
            verdict=None,
            errors=("could not read file: denied",),
        )
        with mock.patch(
            "frutlups.project.parse_review_report_verdict", return_value=invalid
        ):
            self.assertIsNone(self._detect())


class CodingWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        _make_project(self.root)
        self.status, self.evidence = _build_status_with_evidence(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _plan(self, *, metadata: bool = False, correction_round=None):
        status = self.status
        if metadata:
            profile = replace(status.layout.profile, prompt_pairing="workflow_metadata")
            status = replace(status, layout=replace(status.layout, profile=profile))
        return _build_coding_prompt_plan_from_status(
            status,
            correction_round=correction_round,
            evidence=self.evidence,
        )

    def test_needs_work_declares_round_two_and_truthful_metadata(self) -> None:
        _write_report(self.root, ROUND_ONE_REPORT, "needs_work")
        plan = self._plan(metadata=True)
        self.assertTrue(plan.valid, plan.errors)
        self.assertEqual(plan.template.self_report_path, ROUND_TWO_SELF_REPORT)
        self.assertIn("\nround: 2\nrole: coder\n", plan.render.content)

    def test_round_two_needs_work_declares_round_three(self) -> None:
        _write_report(self.root, ROUND_ONE_REPORT, "needs_work")
        _write_report(self.root, ROUND_TWO_REPORT, "needs_work")
        plan = self._plan(metadata=True)
        self.assertEqual(plan.template.self_report_path, ROUND_THREE_SELF_REPORT)
        self.assertIn("\nround: 3\nrole: coder\n", plan.render.content)

    def test_refused_multi_verdict_declares_round_two(self) -> None:
        (self.root / ROUND_ONE_REPORT).write_text(
            "## Verdict\n\nneeds_work\n\n## Verdict\n\npass\n",
            encoding="utf-8",
        )
        self.assertEqual(self._plan().template.self_report_path, ROUND_TWO_SELF_REPORT)

    def test_pass_blocked_and_no_report_keep_round_one_bytes(self) -> None:
        for metadata in (False, True):
            baseline = self._plan(metadata=metadata)
            for verdict in ("pass", "blocked"):
                with self.subTest(metadata=metadata, verdict=verdict):
                    _write_report(self.root, ROUND_ONE_REPORT, verdict)
                    candidate = self._plan(metadata=metadata)
                    self.assertEqual(candidate.render.content, baseline.render.content)
                    (self.root / ROUND_ONE_REPORT).unlink()

    def test_explicit_round_two_overrides_detected_round_three(self) -> None:
        baseline = self._plan(metadata=True, correction_round=2)
        _write_report(self.root, ROUND_TWO_REPORT, "needs_work")
        candidate = self._plan(metadata=True, correction_round=2)
        self.assertEqual(candidate.to_dict(), baseline.to_dict())

    def test_round_999_evidence_fails_closed(self) -> None:
        _write_report(
            self.root,
            f"{REVIEWS}/m001_s01_test_slice_round_999_review_report.md",
            "needs_work",
        )
        plan = self._plan()
        self.assertFalse(plan.valid)
        self.assertIn("correction round must be an integer from 1 to 999", plan.errors)

    def test_rework_declaration_keeps_rework_markers_for_both_verbs(self) -> None:
        passing = _write_report(self.root, ROUND_ONE_REPORT, "pass")
        verdict = build_verdict_record_plan(self.root, passing)
        receipt = write_verdict_record(
            VerdictRecordWriteCommand(project_root=self.root, plan=verdict)
        )
        self.assertTrue(receipt.wrote, receipt.errors)
        declaration = build_rework_declaration_plan(
            self.root,
            pass_id="corrective_pass_001",
            slice_ids=("M001-S01",),
        )
        declared = write_rework_declaration(
            ReworkDeclarationWriteCommand(project_root=self.root, plan=declaration)
        )
        self.assertTrue(declared.wrote, declared.errors)
        _write_report(
            self.root,
            f"{REVIEWS}/m001_s01_prior_rework_review_report.md",
            "needs_work",
        )

        coding = build_coding_prompt_plan(self.root)
        self.assertTrue(coding.valid, coding.errors)
        self.assertIn("_rework_001_corrective_pass_001_001", coding.template.self_report_path)
        self.assertNotIn("_round_", coding.template.self_report_path)
        prompt = self.root / coding.coding_prompt_dir / coding.preview.filename
        prompt.write_text(coding.render.content, encoding="utf-8")
        self_report = self.root / coding.template.self_report_path
        self_report.write_text(_legacy_self_report(), encoding="utf-8")

        review = build_review_prompt_plan(self.root)
        self.assertTrue(review.valid, review.errors)
        self.assertIn("_rework_001_corrective_pass_001_001", review.template.self_report_path)
        self.assertNotIn("_round_", review.template.review_output_path)


class ReviewWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        _make_project(self.root)
        _write_review_inputs(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_round_self_report(self, path: str) -> None:
        target = self.root / path
        target.write_text(_legacy_self_report(), encoding="utf-8")

    def test_round_one_coding_prompt_is_overridden_to_round_two(self) -> None:
        _write_report(self.root, ROUND_ONE_REPORT, "needs_work")
        self._write_round_self_report(ROUND_TWO_SELF_REPORT)
        plan = build_review_prompt_plan(self.root)
        self.assertTrue(plan.valid, plan.errors)
        self.assertEqual(plan.coding_prompt_meta.self_report_path, ROUND_TWO_SELF_REPORT)
        self.assertEqual(plan.template.review_output_path, ROUND_TWO_REPORT)
        self.assertIn(f"Coder self-report: `{ROUND_TWO_SELF_REPORT}`", plan.render.content)
        self.assertIn(f"Review output: `{ROUND_TWO_REPORT}`", plan.render.content)

    def test_round_two_coding_path_is_not_double_marked(self) -> None:
        prompt = self.root / "prompts" / "for_coding_agent" / "001_m001_s01_test_slice.md"
        prompt.write_text(_coding_prompt(ROUND_TWO_SELF_REPORT), encoding="utf-8")
        self._write_round_self_report(ROUND_TWO_SELF_REPORT)
        _write_report(self.root, ROUND_ONE_REPORT, "needs_work")
        plan = build_review_prompt_plan(self.root)
        self.assertTrue(plan.valid, plan.errors)
        self.assertEqual(plan.template.review_output_path, ROUND_TWO_REPORT)
        self.assertNotIn("round_002_round_002", plan.render.content)

    def test_round_two_needs_work_declares_round_three(self) -> None:
        _write_report(self.root, ROUND_ONE_REPORT, "needs_work")
        _write_report(self.root, ROUND_TWO_REPORT, "needs_work")
        self._write_round_self_report(ROUND_THREE_SELF_REPORT)
        plan = build_review_prompt_plan(self.root)
        self.assertTrue(plan.valid, plan.errors)
        self.assertEqual(plan.template.self_report_path, ROUND_THREE_SELF_REPORT)
        self.assertEqual(
            plan.template.review_output_path,
            f"{REVIEWS}/m001_s01_test_slice_round_003_review_report.md",
        )

    def test_refused_multi_verdict_declares_round_two(self) -> None:
        (self.root / ROUND_ONE_REPORT).write_text(
            "## Verdict\n\nneeds_work\n\n## Verdict\n\npass\n",
            encoding="utf-8",
        )
        self._write_round_self_report(ROUND_TWO_SELF_REPORT)
        plan = build_review_prompt_plan(self.root)
        self.assertTrue(plan.valid, plan.errors)
        self.assertEqual(plan.template.review_output_path, ROUND_TWO_REPORT)

    def test_pass_blocked_and_no_report_keep_round_one_bytes(self) -> None:
        frozen_status = build_status(self.root)
        baseline = _build_review_prompt_plan_from_status(frozen_status)
        self.assertTrue(baseline.valid, baseline.errors)
        for verdict in ("pass", "blocked"):
            with self.subTest(verdict=verdict):
                _write_report(self.root, ROUND_ONE_REPORT, verdict)
                candidate = _build_review_prompt_plan_from_status(frozen_status)
                self.assertTrue(candidate.valid, candidate.errors)
                self.assertEqual(candidate.render.content, baseline.render.content)
                (self.root / ROUND_ONE_REPORT).unlink()

    def test_round_999_evidence_fails_closed(self) -> None:
        _write_report(
            self.root,
            f"{REVIEWS}/m001_s01_test_slice_round_999_review_report.md",
            "needs_work",
        )
        plan = build_review_prompt_plan(self.root)
        self.assertFalse(plan.valid)
        self.assertIn("correction round must be an integer from 1 to 999", plan.errors)


class ConfiguredAndReaderContinuityTests(unittest.TestCase):
    def test_workflow_pairing_round_one_bytes_survive_pass_and_blocked(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_metadata_project(root)
            _write_review_inputs(root, metadata=True, canonical_schema=True)
            frozen_status = build_status(root)
            baseline = _build_review_prompt_plan_from_status(frozen_status)
            self.assertTrue(baseline.valid, baseline.errors)
            for verdict in ("pass", "blocked"):
                with self.subTest(verdict=verdict):
                    _write_report(root, ROUND_ONE_REPORT, verdict)
                    candidate = _build_review_prompt_plan_from_status(frozen_status)
                    self.assertTrue(candidate.valid, candidate.errors)
                    self.assertEqual(candidate.render.content, baseline.render.content)
                    (root / ROUND_ONE_REPORT).unlink()

    def test_configured_review_declares_round_two_without_round_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_metadata_project(root, configured_review=True)
            _write_review_inputs(
                root,
                metadata=True,
                canonical_schema=True,
            )
            (root / ROUND_TWO_SELF_REPORT).write_text(
                _canonical_self_report(), encoding="utf-8"
            )
            _write_report(root, ROUND_ONE_REPORT, "needs_work")
            plan = build_review_prompt_plan(root)
            self.assertTrue(plan.valid, plan.errors)
            self.assertIn(
                f"- Write the review report at `{ROUND_TWO_REPORT}`.",
                plan.render.content,
            )
            self.assertNotIn("\nround: 2\n", plan.render.content)

    def test_custom_suffixes_are_round_qualified_together(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self_suffix = "_coder.md"
            review_suffix = "_assessment.md"
            _make_metadata_project(
                root,
                self_report_suffix=self_suffix,
                review_report_suffix=review_suffix,
            )
            _write_review_inputs(
                root,
                self_report_path=f"{REVIEWS}/ignored_by_config.md",
                metadata=True,
                canonical_schema=True,
            )
            detected_self = f"{REVIEWS}/m001_s01_round_002{self_suffix}"
            (root / detected_self).write_text(
                _canonical_self_report(), encoding="utf-8"
            )
            _write_report(
                root,
                f"{REVIEWS}/m001_s01_test_slice{review_suffix}",
                "needs_work",
            )
            plan = build_review_prompt_plan(root)
            self.assertTrue(plan.valid, plan.errors)
            self.assertEqual(plan.template.self_report_path, detected_self)
            self.assertEqual(
                plan.template.review_output_path,
                f"{REVIEWS}/m001_s01_round_002{review_suffix}",
            )

    def test_round_two_pass_is_surfaced_and_receipted_with_round_two_stem(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            _write_report(root, ROUND_ONE_REPORT, "needs_work")
            coding = build_coding_prompt_plan(root)
            self.assertTrue(coding.valid, coding.errors)
            self.assertEqual(coding.template.self_report_path, ROUND_TWO_SELF_REPORT)
            round_two = _write_report(root, ROUND_TWO_REPORT, "pass")
            status = build_status(root)
            self.assertIn("M001-S01", status.accepted_slice_ids)
            verdict = build_verdict_record_plan(root, round_two)
            self.assertTrue(verdict.valid, verdict.errors)
            result = write_verdict_record(
                VerdictRecordWriteCommand(project_root=root, plan=verdict)
            )
            self.assertTrue(result.wrote, result.errors)
            self.assertTrue(result.target_path.endswith("_round_002_verdict_record.md"))


if __name__ == "__main__":
    unittest.main()
