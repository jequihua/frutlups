"""Configured workflow-metadata prompt pairing (layout ``prompts.pairing``).

Final-form tests for the owner-authorized compatibility correction: with
``numbering: "global_flat_sequence"`` and ``pairing: "workflow_metadata"``
coding/review prompts pair by validated workflow metadata and explicit
references — never by equal numeric sequence, parity, filename slug, or
proximity — and health diagnostics describe the configured convention.
The same-sequence strategy remains the default.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from frutlups.layout import load_layout_profile
from frutlups.project import (
    LoopResumeStep,
    _build_coding_prompt_plan_from_status,
    _build_review_prompt_plan_from_status,
    build_loop_resume_status,
    build_status,
)
from frutlups.prompts import (
    PromptArtifact,
    PromptKind,
    compute_prompt_health,
)

LAYOUT_METADATA = """schema_version: frutlups_layout_config_v0
profile_id: artifact_first_template_v3
prompts:
  coding_prompt_dir: "prompts/for_coding_agent"
  review_prompt_dir: "prompts/for_review_agent"
  coding_template: ""
  review_template: ""
  numbering: "global_flat_sequence"
  pairing: "workflow_metadata"
  metadata:
    parse_front_matter: true
    milestone_field: "milestone"
    slice_field: "slice"
    title_field: "title"
reports:
  reviews_dir: "05_governance/reviews"
  discovery: "recursive_contained"
"""

SELF_REPORT = """# Coder Self-Report

## Intent

Implement the fixture slice.

## Files Changed

- fixture_module.py

## Behavior Implemented

Fixture behavior.

## Tests Added Or Updated

- test_fixture.py

## Verification Run

All green.

## Definition Of Done Audit

All criteria met.

## Non-Goals Confirmed

No non-goal implemented.

## Deviations From Prompt

none

## Memory Used

none

## Memory Update Requested

none

## Known Limits / Follow-Up

None.

## Recommended Next Move

Independent review.
"""


def coding_prompt(sequence: int, slice_id: str, title: str = "first fixture slice") -> str:
    return (
        f"# Coding Prompt {sequence:03d}: {slice_id} {title}\n"
        "\n"
        "Workflow metadata:\n"
        "\n"
        "```yaml\n"
        f"milestone: {slice_id.split('-')[0]}\n"
        f"slice: {slice_id}\n"
        f"title: {title}\n"
        "role: coder\n"
        "```\n"
        "\n"
        "## Task\n"
        "\n"
        "Do the fixture work.\n"
        "\n"
        "## Self-Report\n"
        "\n"
        f"Write the self-report at `05_governance/reviews/{slice_id.replace('-', '_').lower()}_self_report.md`.\n"
    )


def review_prompt(
    sequence: int,
    slice_id: str,
    *,
    round_value: int | None = 1,
    coding_ref: str | None = None,
    title: str = "first fixture slice",
) -> str:
    metadata = [
        f"milestone: {slice_id.split('-')[0]}",
        f"slice: {slice_id}",
        f"title: {title}",
        "role: reviewer",
    ]
    if round_value is not None:
        metadata.append(f"round: {round_value}")
    body = (
        f"# Review Prompt {sequence:03d}: {slice_id}\n"
        "\n"
        "Workflow metadata:\n"
        "\n"
        "```yaml\n" + "\n".join(metadata) + "\n```\n"
        "\n"
        "## Review Checks\n"
        "\n"
        "Review the slice.\n"
    )
    if coding_ref is not None:
        body += f"\nReviewed coding prompt: `{coding_ref}`\n"
    return body


def make_project(root: Path, *, layout: str | None = LAYOUT_METADATA) -> Path:
    (root / "00_brief").mkdir(parents=True)
    (root / "prompts" / "for_coding_agent").mkdir(parents=True)
    (root / "prompts" / "for_review_agent").mkdir(parents=True)
    (root / "03_experiments").mkdir()
    (root / "05_governance" / "reviews").mkdir(parents=True)
    (root / "PROJECT_STATE.md").write_text("# Project State\n", encoding="utf-8")
    (root / "03_experiments" / "active_roadmap_fixture.md").write_text(
        "# Roadmap\n\n### M001: First Milestone\n\nStatus: active\n",
        encoding="utf-8",
    )
    (root / "03_experiments" / "development_roadmap_fixture.md").write_text(
        "# Development Roadmap\n\n### M001: First Milestone\n\nStatus: active\n"
        "\nSlices:\n\n- M001-S01: first fixture slice\n- M001-S02: second fixture slice\n",
        encoding="utf-8",
    )
    if layout is not None:
        (root / "frutlups.layout.yaml").write_text(layout, encoding="utf-8")
    return root


def write(root: Path, rel: str, text: str) -> Path:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def artifact(kind: PromptKind, filename: str, sequence: int | None) -> PromptArtifact:
    return PromptArtifact(
        kind=kind, path=Path(filename), filename=filename, sequence=sequence
    )


LAYOUT_SAME_SEQUENCE = LAYOUT_METADATA.replace(
    '  numbering: "global_flat_sequence"\n  pairing: "workflow_metadata"\n', ""
)


class GeneratedCodingPromptMetadataTests(unittest.TestCase):
    """Generation must satisfy the same one pairing decision it configures.

    Template-v3 profiles do not parse the roadmap-item body, so a generated
    coding prompt without a metadata region would be invisible to the
    workflow-metadata pairing and the loop could never advance past its own
    make_coding_prompt step.
    """

    def test_generated_coding_prompt_carries_validated_pairing_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            root = make_project(Path(tmp) / "project")
            status = build_status(root)
            plan = _build_coding_prompt_plan_from_status(status)
            self.assertTrue(plan.valid, plan.errors)
            self.assertIn("Workflow metadata:", plan.render.content)
            self.assertIn("```yaml\nmilestone: M001\nslice: M001-S01\n", plan.render.content)
            self.assertIn("role: coder", plan.render.content)
            target = root / plan.coding_prompt_dir / plan.preview.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(plan.render.content, encoding="utf-8")
            resume = build_loop_resume_status(root)
            self.assertEqual(resume.step, LoopResumeStep.EXECUTE_CODING_PROMPT)
            self.assertEqual(
                resume.coding_prompt_path,
                f"prompts/for_coding_agent/{plan.preview.filename}",
            )

    def test_default_same_sequence_generation_is_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            root = make_project(Path(tmp) / "project", layout=LAYOUT_SAME_SEQUENCE)
            status = build_status(root)
            plan = _build_coding_prompt_plan_from_status(status)
            self.assertTrue(plan.valid, plan.errors)
            self.assertNotIn("Workflow metadata:", plan.render.content)


class GlobalFlatHealthTests(unittest.TestCase):
    def test_alternating_global_sequence_is_healthy(self) -> None:
        # The historical drive shape: one global flat 001..006 sequence with
        # coding and review prompts alternating. No per-kind gap or
        # unmatched-pair findings may be reported for the configured mode.
        artifacts = (
            artifact(PromptKind.CODING, "001_a.md", 1),
            artifact(PromptKind.CODING, "003_b.md", 3),
            artifact(PromptKind.CODING, "005_c.md", 5),
            artifact(PromptKind.REVIEW, "002_review_a.md", 2),
            artifact(PromptKind.REVIEW, "004_review_b.md", 4),
            artifact(PromptKind.REVIEW, "006_review_c.md", 6),
        )
        health = compute_prompt_health(
            artifacts,
            numbering="global_flat_sequence",
            pairing="workflow_metadata",
        )
        self.assertTrue(health.ok, [f.message for f in health.findings])
        self.assertEqual(health.findings, ())

    def test_global_gap_is_reported_once_without_kind_split(self) -> None:
        artifacts = (
            artifact(PromptKind.CODING, "001_a.md", 1),
            artifact(PromptKind.REVIEW, "002_review_a.md", 2),
            artifact(PromptKind.CODING, "004_b.md", 4),
        )
        health = compute_prompt_health(
            artifacts,
            numbering="global_flat_sequence",
            pairing="workflow_metadata",
        )
        gap_findings = [
            finding
            for finding in health.findings
            if finding.code == "missing_prompt_sequence"
        ]
        self.assertEqual([finding.sequence for finding in gap_findings], [3])
        self.assertEqual([finding.kind for finding in gap_findings], [None])
        self.assertEqual(
            [f for f in health.findings if "unmatched" in f.code], []
        )

    def test_global_duplicate_across_kinds_is_reported(self) -> None:
        artifacts = (
            artifact(PromptKind.CODING, "001_a.md", 1),
            artifact(PromptKind.REVIEW, "001_review_a.md", 1),
        )
        health = compute_prompt_health(
            artifacts,
            numbering="global_flat_sequence",
            pairing="workflow_metadata",
        )
        duplicates = [
            finding
            for finding in health.findings
            if finding.code == "duplicate_prompt_sequence"
        ]
        self.assertEqual(len(duplicates), 1)
        self.assertEqual(duplicates[0].sequence, 1)

    def test_default_same_sequence_behavior_is_unchanged(self) -> None:
        artifacts = (
            artifact(PromptKind.CODING, "001_a.md", 1),
            artifact(PromptKind.CODING, "003_b.md", 3),
            artifact(PromptKind.REVIEW, "002_review_a.md", 2),
        )
        health = compute_prompt_health(artifacts)
        codes = sorted(finding.code for finding in health.findings)
        self.assertEqual(
            codes,
            [
                "missing_prompt_sequence",
                "missing_prompt_sequence",
                "unmatched_coding_prompt",
                "unmatched_coding_prompt",
                "unmatched_review_prompt",
            ],
        )


class MetadataPairingProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = make_project(Path(self._tmp.name) / "project")
        write(
            self.root,
            "prompts/for_coding_agent/001_m001_s01_first.md",
            coding_prompt(1, "M001-S01"),
        )
        write(
            self.root,
            "05_governance/reviews/m001_s01_self_report.md",
            SELF_REPORT,
        )

    def test_metadata_paired_review_prompt_is_selected_across_sequences(self) -> None:
        # Review prompt 002 pairs with coding prompt 001 by validated
        # metadata; equal-sequence matching would find nothing.
        write(
            self.root,
            "prompts/for_review_agent/002_review_m001_s01_first.md",
            review_prompt(
                2,
                "M001-S01",
                coding_ref="prompts/for_coding_agent/001_m001_s01_first.md",
            ),
        )
        resume = build_loop_resume_status(self.root)
        self.assertEqual(resume.step, LoopResumeStep.EXECUTE_REVIEW_PROMPT)
        self.assertEqual(
            resume.review_prompt_path,
            "prompts/for_review_agent/002_review_m001_s01_first.md",
        )

    def test_absent_reference_still_pairs_by_unique_slice_metadata(self) -> None:
        write(
            self.root,
            "prompts/for_review_agent/002_review_m001_s01_first.md",
            review_prompt(2, "M001-S01"),
        )
        resume = build_loop_resume_status(self.root)
        self.assertEqual(resume.step, LoopResumeStep.EXECUTE_REVIEW_PROMPT)
        self.assertEqual(
            resume.review_prompt_path,
            "prompts/for_review_agent/002_review_m001_s01_first.md",
        )

    def test_no_qualifying_review_prompt_requests_generation(self) -> None:
        resume = build_loop_resume_status(self.root)
        self.assertEqual(resume.step, LoopResumeStep.MAKE_REVIEW_PROMPT)
        self.assertEqual(resume.review_prompt_path, "")

    def test_round_metadata_disambiguates_after_validation(self) -> None:
        write(
            self.root,
            "prompts/for_review_agent/002_review_round1.md",
            review_prompt(2, "M001-S01", round_value=1),
        )
        write(
            self.root,
            "prompts/for_review_agent/004_review_round2.md",
            review_prompt(4, "M001-S01", round_value=2),
        )
        resume = build_loop_resume_status(self.root)
        self.assertEqual(resume.step, LoopResumeStep.EXECUTE_REVIEW_PROMPT)
        self.assertEqual(
            resume.review_prompt_path,
            "prompts/for_review_agent/004_review_round2.md",
        )

    def test_ambiguous_candidates_fail_closed_without_latest_wins(self) -> None:
        write(
            self.root,
            "prompts/for_review_agent/002_review_dup_a.md",
            review_prompt(2, "M001-S01", round_value=1),
        )
        write(
            self.root,
            "prompts/for_review_agent/004_review_dup_b.md",
            review_prompt(4, "M001-S01", round_value=1),
        )
        resume = build_loop_resume_status(self.root)
        self.assertEqual(resume.step, LoopResumeStep.FIX_REVIEW_REPORT)
        self.assertEqual(resume.review_prompt_path, "")
        self.assertTrue(
            any("ambiguous" in diag for diag in resume.diagnostics),
            resume.diagnostics,
        )

    def test_explicit_reference_mismatch_excludes_stale_review_prompt(self) -> None:
        # A corrective coding prompt (higher sequence, same slice) supersedes
        # the original; the round-one review prompt explicitly references the
        # original coding prompt and must not pair with the corrective one.
        write(
            self.root,
            "prompts/for_coding_agent/003_m001_s01_corrective.md",
            coding_prompt(3, "M001-S01"),
        )
        write(
            self.root,
            "prompts/for_review_agent/002_review_m001_s01_first.md",
            review_prompt(
                2,
                "M001-S01",
                coding_ref="prompts/for_coding_agent/001_m001_s01_first.md",
            ),
        )
        resume = build_loop_resume_status(self.root)
        self.assertEqual(resume.step, LoopResumeStep.MAKE_REVIEW_PROMPT)
        self.assertEqual(resume.review_prompt_path, "")

    def test_malformed_metadata_review_prompt_never_pairs_or_repairs(self) -> None:
        # A dual-region routing conflict keeps its existing owned refusal;
        # the filename slug must not repair the pairing.
        conflicted = (
            "---\n"
            "milestone: M001\n"
            "slice: M001-S01\n"
            "title: conflicting\n"
            "---\n"
            "\n"
            "# Review Prompt 002\n"
            "\n"
            "```yaml\n"
            "milestone: M002\n"
            "slice: M002-S09\n"
            "title: other\n"
            "```\n"
            "\n"
            "## Review Checks\n\nReview.\n"
        )
        write(
            self.root,
            "prompts/for_review_agent/002_review_m001_s01_first.md",
            conflicted,
        )
        resume = build_loop_resume_status(self.root)
        self.assertEqual(resume.step, LoopResumeStep.MAKE_REVIEW_PROMPT)
        self.assertEqual(resume.review_prompt_path, "")

    def test_generation_selects_frontier_coding_prompt_with_global_sequence(self) -> None:
        status = build_status(self.root)
        plan = _build_review_prompt_plan_from_status(status)
        self.assertTrue(plan.valid, plan.errors)
        self.assertEqual(
            plan.selected_coding_prompt.filename, "001_m001_s01_first.md"
        )
        self.assertEqual(plan.preview.sequence, 2)
        self.assertTrue(plan.preview.filename.startswith("002_"))

    def test_generation_refuses_when_already_paired_by_metadata(self) -> None:
        write(
            self.root,
            "prompts/for_review_agent/002_review_m001_s01_first.md",
            review_prompt(
                2,
                "M001-S01",
                coding_ref="prompts/for_coding_agent/001_m001_s01_first.md",
            ),
        )
        status = build_status(self.root)
        plan = _build_review_prompt_plan_from_status(status)
        self.assertFalse(plan.valid)
        self.assertTrue(
            any("paired" in error for error in plan.errors), plan.errors
        )

    def test_generation_fails_closed_on_ambiguous_pairing(self) -> None:
        write(
            self.root,
            "prompts/for_review_agent/002_review_dup_a.md",
            review_prompt(2, "M001-S01", round_value=1),
        )
        write(
            self.root,
            "prompts/for_review_agent/004_review_dup_b.md",
            review_prompt(4, "M001-S01", round_value=1),
        )
        status = build_status(self.root)
        plan = _build_review_prompt_plan_from_status(status)
        self.assertFalse(plan.valid)
        self.assertTrue(
            any("ambiguous" in error for error in plan.errors), plan.errors
        )

    def test_no_false_health_findings_in_project_status(self) -> None:
        write(
            self.root,
            "prompts/for_review_agent/002_review_m001_s01_first.md",
            review_prompt(2, "M001-S01"),
        )
        status = build_status(self.root)
        self.assertTrue(
            status.prompt_health.ok,
            [f.message for f in status.prompt_health.findings],
        )
        self.assertEqual(status.prompt_health.findings, ())


class LayoutModeParsingTests(unittest.TestCase):
    def test_configured_modes_are_read_from_layout(self) -> None:
        with TemporaryDirectory() as tmp:
            root = make_project(Path(tmp) / "project")
            loaded = load_layout_profile(root)
            self.assertEqual(loaded.profile.prompt_numbering, "global_flat_sequence")
            self.assertEqual(loaded.profile.prompt_pairing, "workflow_metadata")
            self.assertEqual(loaded.profile.reports_discovery, "recursive_contained")

    def test_absent_modes_keep_compatible_defaults(self) -> None:
        with TemporaryDirectory() as tmp:
            root = make_project(Path(tmp) / "project", layout=None)
            loaded = load_layout_profile(root)
            self.assertEqual(loaded.profile.prompt_numbering, "per_kind_sequence")
            self.assertEqual(loaded.profile.prompt_pairing, "same_sequence")
            self.assertEqual(loaded.profile.reports_discovery, "flat")

    def test_unknown_mode_value_is_error_diagnostic_with_fallback(self) -> None:
        bad_layout = LAYOUT_METADATA.replace(
            'pairing: "workflow_metadata"', 'pairing: "psychic_guessing"'
        )
        with TemporaryDirectory() as tmp:
            root = make_project(Path(tmp) / "project", layout=bad_layout)
            loaded = load_layout_profile(root)
            self.assertEqual(loaded.profile.prompt_pairing, "same_sequence")
            self.assertTrue(
                any(
                    diagnostic.severity.value == "error"
                    for diagnostic in loaded.diagnostics
                ),
                loaded.diagnostics,
            )


if __name__ == "__main__":
    unittest.main()
