"""Tests for inherited illustrative prompt recognition."""

from pathlib import Path
import unittest

from frutlups.project import build_status
from frutlups.prompts import (
    KNOWN_INHERITED_PROMPTS,
    PromptArtifact,
    PromptArtifactClassification,
    PromptClassification,
    PromptKind,
    analyze_prompt_inventory,
    classify_prompt_artifact,
    classify_prompt_inventory,
    filter_prompt_artifacts_for_analysis,
)


INHERITED_CODING_FILENAMES = (
    "001_geecomposer_core_foundations.md",
    "002_geecomposer_core_foundations_closure.md",
    "014_geecomposer_milestone_006_cleanup.md",
)

INHERITED_REVIEW_FILENAMES = (
    "001_review_core_foundations.md",
    "002_review_core_foundations_corrective.md",
    "014_review_observation_count_cleanup.md",
)


def _development_repo_root() -> Path | None:
    required = ("00_brief", "03_experiments", "05_governance", "06_infra", "08_pkg", "prompts")
    for candidate in Path(__file__).resolve().parents:
        if all((candidate / name).exists() for name in required):
            return candidate
    return None


def _coding(sequence: int | None, filename: str) -> PromptArtifact:
    return PromptArtifact(
        kind=PromptKind.CODING,
        path=Path(f"/tmp/prompts/for_coding_agent/{filename}"),
        filename=filename,
        sequence=sequence,
    )


def _review(sequence: int | None, filename: str) -> PromptArtifact:
    return PromptArtifact(
        kind=PromptKind.REVIEW,
        path=Path(f"/tmp/prompts/for_review_agent/{filename}"),
        filename=filename,
        sequence=sequence,
    )


class KnownInheritedSetTests(unittest.TestCase):
    def test_set_contains_exactly_the_documented_pairs(self) -> None:
        expected = {
            (PromptKind.CODING, name) for name in INHERITED_CODING_FILENAMES
        } | {
            (PromptKind.REVIEW, name) for name in INHERITED_REVIEW_FILENAMES
        }
        self.assertEqual(set(KNOWN_INHERITED_PROMPTS), expected)


class PromptClassificationEnumTests(unittest.TestCase):
    def test_classification_values_are_canonical_strings(self) -> None:
        self.assertEqual(PromptClassification.PROJECT_PROMPT.value, "project_prompt")
        self.assertEqual(
            PromptClassification.INHERITED_EXAMPLE.value, "inherited_example"
        )


class ClassifyPromptArtifactTests(unittest.TestCase):
    def test_all_known_inherited_coding_filenames_classified_inherited(self) -> None:
        for filename in INHERITED_CODING_FILENAMES:
            with self.subTest(filename=filename):
                result = classify_prompt_artifact(_coding(None, filename))
                self.assertEqual(
                    result.classification, PromptClassification.INHERITED_EXAMPLE
                )
                self.assertTrue(result.ignored_for_analysis)
                self.assertEqual(result.kind, PromptKind.CODING)
                self.assertEqual(result.filename, filename)
                self.assertIn("inherited", result.reason.lower())

    def test_all_known_inherited_review_filenames_classified_inherited(self) -> None:
        for filename in INHERITED_REVIEW_FILENAMES:
            with self.subTest(filename=filename):
                result = classify_prompt_artifact(_review(None, filename))
                self.assertEqual(
                    result.classification, PromptClassification.INHERITED_EXAMPLE
                )
                self.assertTrue(result.ignored_for_analysis)
                self.assertEqual(result.kind, PromptKind.REVIEW)
                self.assertEqual(result.filename, filename)

    def test_inherited_coding_filename_in_review_folder_is_project_prompt(self) -> None:
        # `001_geecomposer_core_foundations.md` is on the inherited coding
        # list. If it somehow appeared in the review folder it should not
        # be classified as inherited.
        result = classify_prompt_artifact(
            _review(1, "001_geecomposer_core_foundations.md")
        )
        self.assertEqual(
            result.classification, PromptClassification.PROJECT_PROMPT
        )
        self.assertFalse(result.ignored_for_analysis)

    def test_inherited_review_filename_in_coding_folder_is_project_prompt(self) -> None:
        result = classify_prompt_artifact(
            _coding(1, "001_review_core_foundations.md")
        )
        self.assertEqual(
            result.classification, PromptClassification.PROJECT_PROMPT
        )
        self.assertFalse(result.ignored_for_analysis)

    def test_real_frutlups_coding_prompt_is_project_prompt(self) -> None:
        result = classify_prompt_artifact(
            _coding(1, "001_frutlups_m002_s01_roadmap_parser.md")
        )
        self.assertEqual(
            result.classification, PromptClassification.PROJECT_PROMPT
        )
        self.assertFalse(result.ignored_for_analysis)

    def test_real_frutlups_review_prompt_is_project_prompt(self) -> None:
        result = classify_prompt_artifact(
            _review(1, "001_review_frutlups_m002_s01_roadmap_parser.md")
        )
        self.assertEqual(
            result.classification, PromptClassification.PROJECT_PROMPT
        )
        self.assertFalse(result.ignored_for_analysis)

    def test_non_conforming_filenames_remain_project_prompts(self) -> None:
        for name in ("README.md", "handoff.md", "geecomposer_notes.md"):
            with self.subTest(filename=name):
                result = classify_prompt_artifact(_coding(None, name))
                self.assertEqual(
                    result.classification, PromptClassification.PROJECT_PROMPT
                )
                self.assertFalse(result.ignored_for_analysis)

    def test_almost_matching_filename_is_not_classified_inherited(self) -> None:
        # The list is exact; near matches should not be classified as
        # inherited.
        for name in (
            "001_geecomposer_core_foundations_v2.md",
            "001_geecomposer_core_foundations",
            "geecomposer_core_foundations.md",
        ):
            with self.subTest(filename=name):
                result = classify_prompt_artifact(_coding(1, name))
                self.assertEqual(
                    result.classification, PromptClassification.PROJECT_PROMPT
                )


class ClassificationToDictTests(unittest.TestCase):
    def test_to_dict_shape_for_inherited(self) -> None:
        classification = PromptArtifactClassification(
            kind=PromptKind.CODING,
            filename="001_geecomposer_core_foundations.md",
            classification=PromptClassification.INHERITED_EXAMPLE,
            ignored_for_analysis=True,
            reason="inherited",
        )

        self.assertEqual(
            classification.to_dict(),
            {
                "kind": "coding",
                "filename": "001_geecomposer_core_foundations.md",
                "classification": "inherited_example",
                "ignored_for_analysis": True,
                "reason": "inherited",
            },
        )

    def test_to_dict_shape_for_project_prompt(self) -> None:
        classification = PromptArtifactClassification(
            kind=PromptKind.REVIEW,
            filename="001_review_frutlups_m002_s01_roadmap_parser.md",
            classification=PromptClassification.PROJECT_PROMPT,
            ignored_for_analysis=False,
            reason="frutlups",
        )

        payload = classification.to_dict()
        self.assertEqual(payload["kind"], "review")
        self.assertEqual(payload["classification"], "project_prompt")
        self.assertFalse(payload["ignored_for_analysis"])


class ClassifyPromptInventoryTests(unittest.TestCase):
    def test_preserves_input_order(self) -> None:
        artifacts = (
            _coding(1, "001_frutlups_a.md"),
            _coding(2, "002_geecomposer_core_foundations_closure.md"),
            _review(1, "001_review_frutlups_a.md"),
            _review(14, "014_review_observation_count_cleanup.md"),
        )

        classifications = classify_prompt_inventory(artifacts)

        self.assertEqual(
            [c.filename for c in classifications],
            [a.filename for a in artifacts],
        )
        self.assertEqual(
            [c.classification.value for c in classifications],
            [
                "project_prompt",
                "inherited_example",
                "project_prompt",
                "inherited_example",
            ],
        )

    def test_empty_iterable_returns_empty_tuple(self) -> None:
        self.assertEqual(classify_prompt_inventory(()), ())


class FilterPromptArtifactsForAnalysisTests(unittest.TestCase):
    def test_filters_out_inherited_examples(self) -> None:
        artifacts = (
            _coding(1, "001_frutlups_a.md"),
            _coding(1, "001_geecomposer_core_foundations.md"),
            _review(1, "001_review_frutlups_a.md"),
            _review(1, "001_review_core_foundations.md"),
        )

        filtered = filter_prompt_artifacts_for_analysis(artifacts)

        self.assertEqual(
            [a.filename for a in filtered],
            ["001_frutlups_a.md", "001_review_frutlups_a.md"],
        )

    def test_preserves_relative_order_of_non_inherited(self) -> None:
        artifacts = (
            _coding(2, "002_b.md"),
            _coding(1, "001_geecomposer_core_foundations.md"),
            _coding(1, "001_a.md"),
            _coding(14, "014_geecomposer_milestone_006_cleanup.md"),
            _coding(3, "003_c.md"),
        )

        filtered = filter_prompt_artifacts_for_analysis(artifacts)

        self.assertEqual(
            [a.filename for a in filtered],
            ["002_b.md", "001_a.md", "003_c.md"],
        )

    def test_no_inherited_returns_input_artifacts_unchanged(self) -> None:
        artifacts = (
            _coding(1, "001_a.md"),
            _review(1, "001_a.md"),
        )

        self.assertEqual(
            filter_prompt_artifacts_for_analysis(artifacts), artifacts
        )


class AnalyseOnFilteredFixtureTests(unittest.TestCase):
    def test_inherited_examples_do_not_cause_spurious_findings_after_filter(self) -> None:
        # The fixture combines real project prompts numbered 1..3 (matched
        # pairs) with inherited examples that share sequence numbers 1, 2,
        # and 14. The raw analyser should report duplicates and unmatched
        # findings caused by the inherited examples; after filtering only
        # the balanced project pairs remain and the analyser should
        # report no findings.
        artifacts = (
            # real project pairs
            _coding(1, "001_frutlups_a.md"),
            _coding(2, "002_frutlups_b.md"),
            _coding(3, "003_frutlups_c.md"),
            _review(1, "001_review_frutlups_a.md"),
            _review(2, "002_review_frutlups_b.md"),
            _review(3, "003_review_frutlups_c.md"),
            # inherited examples sharing sequences 1, 2, 14
            _coding(1, "001_geecomposer_core_foundations.md"),
            _coding(2, "002_geecomposer_core_foundations_closure.md"),
            _coding(14, "014_geecomposer_milestone_006_cleanup.md"),
            _review(1, "001_review_core_foundations.md"),
            _review(2, "002_review_core_foundations_corrective.md"),
            _review(14, "014_review_observation_count_cleanup.md"),
        )

        raw_findings = analyze_prompt_inventory(artifacts)
        self.assertTrue(
            any(f.code == "duplicate_prompt_sequence" for f in raw_findings),
            "raw analyser should report duplicates caused by inherited examples",
        )

        filtered = filter_prompt_artifacts_for_analysis(artifacts)
        filtered_findings = analyze_prompt_inventory(filtered)

        self.assertEqual(
            filtered_findings, (),
            f"unexpected findings on filtered fixture: {filtered_findings!r}",
        )


class LiveRepositoryClassificationTests(unittest.TestCase):
    def test_live_status_resolves_and_all_prompts_are_project_prompts(self) -> None:
        from frutlups.state import DiagnosticSeverity

        repo_root = _development_repo_root()
        if repo_root is None:
            self.skipTest("live artifact development repository is not present")
        status = build_status(repo_root)

        # The project may have reached its completed end state (no next
        # milestone); require only that the roadmap parsed cleanly to milestones.
        self.assertIsNotNone(status.active_roadmap)
        self.assertTrue(status.milestones)
        blocking = [
            diag
            for diag in status.diagnostics
            if diag.severity
            in (DiagnosticSeverity.WARNING, DiagnosticSeverity.ERROR)
        ]
        self.assertEqual(
            blocking, [],
            f"unexpected error/warning diagnostics: {blocking!r}",
        )

        classifications = classify_prompt_inventory(status.prompt_artifacts)
        for classification in classifications:
            self.assertEqual(
                classification.classification,
                PromptClassification.PROJECT_PROMPT,
                f"unexpected non-project classification for {classification.filename}",
            )
            self.assertFalse(classification.ignored_for_analysis)


if __name__ == "__main__":
    unittest.main()
