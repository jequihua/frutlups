"""Tests for the pure prompt inventory analysis helper."""

from pathlib import Path
import unittest

from frutlups.prompts import (
    PromptArtifact,
    PromptInventoryFinding,
    PromptKind,
    analyze_prompt_inventory,
)


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


def _balanced_fixture(n: int) -> tuple[PromptArtifact, ...]:
    artifacts: list[PromptArtifact] = []
    for i in range(1, n + 1):
        artifacts.append(_coding(i, f"{i:03d}_coding_{i}.md"))
        artifacts.append(_review(i, f"{i:03d}_review_{i}.md"))
    return tuple(artifacts)


class FindingTypeTests(unittest.TestCase):
    def test_to_dict_uses_canonical_kind_string(self) -> None:
        finding = PromptInventoryFinding(
            code="duplicate_prompt_sequence",
            kind=PromptKind.CODING,
            sequence=1,
            filenames=("001_a.md", "001_b.md"),
            message="dup",
        )

        self.assertEqual(
            finding.to_dict(),
            {
                "code": "duplicate_prompt_sequence",
                "kind": "coding",
                "sequence": 1,
                "filenames": ["001_a.md", "001_b.md"],
                "message": "dup",
            },
        )

    def test_to_dict_allows_none_kind_and_sequence(self) -> None:
        finding = PromptInventoryFinding(
            code="custom_finding",
            kind=None,
            sequence=None,
            filenames=(),
            message="x",
        )

        payload = finding.to_dict()
        self.assertIsNone(payload["kind"])
        self.assertIsNone(payload["sequence"])
        self.assertEqual(payload["filenames"], [])


class AnalyzeBalancedTests(unittest.TestCase):
    def test_no_findings_for_balanced_001_002_003(self) -> None:
        findings = analyze_prompt_inventory(_balanced_fixture(3))
        self.assertEqual(findings, ())

    def test_no_findings_for_empty_inventory(self) -> None:
        self.assertEqual(analyze_prompt_inventory(()), ())


class AnalyzeMissingTests(unittest.TestCase):
    def test_detects_missing_coding_sequence(self) -> None:
        artifacts = (
            _coding(1, "001_a.md"),
            _coding(3, "003_c.md"),
            _review(1, "001_a.md"),
            _review(2, "002_b.md"),
            _review(3, "003_c.md"),
        )

        findings = analyze_prompt_inventory(artifacts)

        missing_coding = [
            f for f in findings
            if f.code == "missing_prompt_sequence" and f.kind == PromptKind.CODING
        ]
        self.assertEqual(len(missing_coding), 1)
        self.assertEqual(missing_coding[0].sequence, 2)
        self.assertEqual(missing_coding[0].filenames, ())
        self.assertIn("002", missing_coding[0].message)

    def test_detects_missing_review_sequence(self) -> None:
        artifacts = (
            _coding(1, "001_a.md"),
            _coding(2, "002_b.md"),
            _review(2, "002_b.md"),
        )

        findings = analyze_prompt_inventory(artifacts)

        missing_review = [
            f for f in findings
            if f.code == "missing_prompt_sequence" and f.kind == PromptKind.REVIEW
        ]
        self.assertEqual(len(missing_review), 1)
        self.assertEqual(missing_review[0].sequence, 1)

    def test_no_missing_when_sequences_start_at_one_and_are_dense(self) -> None:
        artifacts = (
            _coding(1, "001_a.md"),
            _coding(2, "002_b.md"),
            _coding(3, "003_c.md"),
            _review(1, "001_a.md"),
            _review(2, "002_b.md"),
            _review(3, "003_c.md"),
        )

        findings = analyze_prompt_inventory(artifacts)
        missing = [f for f in findings if f.code == "missing_prompt_sequence"]
        self.assertEqual(missing, [])

    def test_missing_includes_gaps_starting_at_one(self) -> None:
        artifacts = (
            _coding(3, "003_a.md"),
            _review(3, "003_a.md"),
        )

        findings = analyze_prompt_inventory(artifacts)

        missing_coding = [
            f for f in findings
            if f.code == "missing_prompt_sequence" and f.kind == PromptKind.CODING
        ]
        self.assertEqual([f.sequence for f in missing_coding], [1, 2])


class AnalyzeDuplicateTests(unittest.TestCase):
    def test_detects_duplicate_coding_sequence(self) -> None:
        artifacts = (
            _coding(1, "001_a.md"),
            _coding(1, "001_b.md"),
            _review(1, "001_a.md"),
        )

        findings = analyze_prompt_inventory(artifacts)

        duplicates = [
            f for f in findings if f.code == "duplicate_prompt_sequence"
        ]
        self.assertEqual(len(duplicates), 1)
        self.assertEqual(duplicates[0].kind, PromptKind.CODING)
        self.assertEqual(duplicates[0].sequence, 1)
        self.assertEqual(duplicates[0].filenames, ("001_a.md", "001_b.md"))

    def test_detects_duplicate_review_sequence(self) -> None:
        artifacts = (
            _coding(1, "001_a.md"),
            _review(1, "001_a.md"),
            _review(1, "001_b.md"),
        )

        findings = analyze_prompt_inventory(artifacts)

        duplicates = [
            f for f in findings
            if f.code == "duplicate_prompt_sequence" and f.kind == PromptKind.REVIEW
        ]
        self.assertEqual(len(duplicates), 1)
        self.assertEqual(duplicates[0].sequence, 1)
        self.assertEqual(duplicates[0].filenames, ("001_a.md", "001_b.md"))

    def test_duplicate_filenames_are_sorted(self) -> None:
        artifacts = (
            _coding(2, "002_zzz.md"),
            _coding(2, "002_aaa.md"),
            _coding(2, "002_mmm.md"),
            _coding(1, "001_a.md"),
            _review(1, "001_a.md"),
            _review(2, "002_b.md"),
        )

        findings = analyze_prompt_inventory(artifacts)
        duplicates = [
            f for f in findings if f.code == "duplicate_prompt_sequence"
        ]
        self.assertEqual(
            duplicates[0].filenames, ("002_aaa.md", "002_mmm.md", "002_zzz.md")
        )


class AnalyzeUnmatchedTests(unittest.TestCase):
    def test_detects_unmatched_coding_prompt(self) -> None:
        artifacts = (
            _coding(1, "001_a.md"),
            _coding(2, "002_b.md"),
            _review(1, "001_a.md"),
        )

        findings = analyze_prompt_inventory(artifacts)

        unmatched = [f for f in findings if f.code == "unmatched_coding_prompt"]
        self.assertEqual(len(unmatched), 1)
        self.assertEqual(unmatched[0].kind, PromptKind.CODING)
        self.assertEqual(unmatched[0].sequence, 2)
        self.assertEqual(unmatched[0].filenames, ("002_b.md",))

    def test_detects_unmatched_review_prompt(self) -> None:
        artifacts = (
            _coding(1, "001_a.md"),
            _review(1, "001_a.md"),
            _review(2, "002_b.md"),
        )

        findings = analyze_prompt_inventory(artifacts)

        unmatched = [f for f in findings if f.code == "unmatched_review_prompt"]
        self.assertEqual(len(unmatched), 1)
        self.assertEqual(unmatched[0].kind, PromptKind.REVIEW)
        self.assertEqual(unmatched[0].sequence, 2)
        self.assertEqual(unmatched[0].filenames, ("002_b.md",))

    def test_unmatched_filenames_for_duplicates_are_sorted(self) -> None:
        # If sequence 2 is duplicated on the coding side and missing on the
        # review side, the unmatched finding should list both filenames in
        # sorted order.
        artifacts = (
            _coding(1, "001_a.md"),
            _coding(2, "002_zzz.md"),
            _coding(2, "002_aaa.md"),
            _review(1, "001_a.md"),
        )

        findings = analyze_prompt_inventory(artifacts)
        unmatched = [f for f in findings if f.code == "unmatched_coding_prompt"]
        self.assertEqual(unmatched[0].filenames, ("002_aaa.md", "002_zzz.md"))


class AnalyzeIgnoresNonConformingTests(unittest.TestCase):
    def test_artifacts_with_sequence_none_are_ignored(self) -> None:
        artifacts = (
            _coding(None, "handoff.md"),
            _coding(None, "README.md"),
            _coding(1, "001_a.md"),
            _review(1, "001_a.md"),
            _review(None, "notes.md"),
        )

        findings = analyze_prompt_inventory(artifacts)

        self.assertEqual(findings, ())


class AnalyzeDeterministicOrderTests(unittest.TestCase):
    def test_full_finding_order_follows_spec(self) -> None:
        # Construct a fixture that simultaneously triggers every kind of
        # finding so we can assert the documented total ordering:
        #
        #   1. missing coding (asc)
        #   2. missing review (asc)
        #   3. duplicate coding (asc)
        #   4. duplicate review (asc)
        #   5. unmatched coding (asc)
        #   6. unmatched review (asc)
        artifacts = (
            # Coding: 1 (dup), 3, 4
            _coding(1, "001_c_a.md"),
            _coding(1, "001_c_b.md"),
            _coding(3, "003_c.md"),
            _coding(4, "004_c.md"),
            # Review: 1, 2, 4 (dup), 5
            _review(1, "001_r.md"),
            _review(2, "002_r.md"),
            _review(4, "004_r_a.md"),
            _review(4, "004_r_b.md"),
            _review(5, "005_r.md"),
        )

        findings = analyze_prompt_inventory(artifacts)
        observed = [(f.code, f.kind.value, f.sequence) for f in findings]

        # Missing coding: 2
        # Missing review: 3
        # Duplicate coding: 1
        # Duplicate review: 4
        # Unmatched coding: 3 (coding has 3 but review has 3? no, review
        #   has 1,2,4,5 → coding-only sequences are {3}). Coding sequences
        #   = {1, 3, 4}; review sequences = {1, 2, 4, 5}; coding-only = {3};
        #   review-only = {2, 5}.
        # Unmatched review: 2, then 5
        expected = [
            ("missing_prompt_sequence", "coding", 2),
            ("missing_prompt_sequence", "review", 3),
            ("duplicate_prompt_sequence", "coding", 1),
            ("duplicate_prompt_sequence", "review", 4),
            ("unmatched_coding_prompt", "coding", 3),
            ("unmatched_review_prompt", "review", 2),
            ("unmatched_review_prompt", "review", 5),
        ]

        self.assertEqual(observed, expected)


if __name__ == "__main__":
    unittest.main()
