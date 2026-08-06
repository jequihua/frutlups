"""Tests for M013-S04: first-pass evidence preservation model and collector.

Covers the pure preservation models (artifact reference, per-slice bundle,
manifest), JSON-safe serialization including malformed constructible inputs,
deterministic validation, and the read-only collector: stable relative paths,
byte sizes, SHA-256 digests, missing-artifact diagnostics, deterministic
ordering, and known-divergence preservation. File-based tests use temporary
repository-shaped fixtures and assert no files are created.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from frutlups.first_pass_evidence import (
    FirstPassManifest,
    PreservedArtifact,
    PreservedArtifactKind,
    SlicePreservation,
    collect_first_pass_evidence,
    collect_slice_preservation,
    validate_first_pass_manifest,
    validate_preserved_artifact,
    validate_slice_preservation,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _fixture(root: Path) -> None:
    """Create a minimal repo-shaped fixture with M013-S01 first-pass artifacts."""
    _write(
        root / "prompts" / "for_coding_agent" / "063_frutlups_m013_s01_pass_frontier_data_model.md",
        "# coding prompt 063\n",
    )
    _write(
        root / "prompts" / "for_review_agent" / "063_review_frutlups_m013_s01_pass_frontier_data_model.md",
        "# review prompt 063\n",
    )
    reviews = root / "05_governance" / "reviews"
    _write(reviews / "m013_s01_pass_frontier_data_model_self_report.md", "# self report\n")
    _write(reviews / "m013_s01_pass_frontier_data_model_review_report.md", "# review report\n")
    _write(reviews / "m013_s01_pass_frontier_data_model_verdict_record.md", "# verdict record\n")
    _write(root / "05_governance" / "known_divergences.md", "# Known Divergences\n\n## D\n\nbody\n")


# ---------------------------------------------------------------------------
# Models + serialization
# ---------------------------------------------------------------------------

class PreservedArtifactTests(unittest.TestCase):
    def test_serializes_json_safe(self) -> None:
        art = PreservedArtifact(
            kind=PreservedArtifactKind.CODING_PROMPT,
            path="prompts/for_coding_agent/063_x.md",
            source_slice_id="M013-S01",
            size_bytes=10,
            sha256="abc",
            exists=True,
        )
        d = art.to_dict()
        json.dumps(d)
        self.assertEqual(d["kind"], "coding_prompt")
        self.assertEqual(d["source_slice_id"], "M013-S01")
        self.assertTrue(d["exists"])

    def test_valid_artifact_no_errors(self) -> None:
        art = PreservedArtifact(kind=PreservedArtifactKind.OTHER, path="a/b.md")
        self.assertEqual(validate_preserved_artifact(art), ())

    def test_absolute_path_flagged(self) -> None:
        for bad in ("/etc/x.md", "C:\\\\x.md"):
            art = PreservedArtifact(kind=PreservedArtifactKind.OTHER, path=bad)
            self.assertTrue(
                any("relative" in e for e in validate_preserved_artifact(art)),
                msg=bad,
            )

    def test_empty_path_flagged(self) -> None:
        art = PreservedArtifact(kind=PreservedArtifactKind.OTHER, path="  ")
        self.assertTrue(any("path" in e for e in validate_preserved_artifact(art)))

    def test_bad_slice_id_flagged(self) -> None:
        art = PreservedArtifact(kind=PreservedArtifactKind.OTHER, path="a.md", source_slice_id="nope")
        self.assertTrue(
            any("source_slice_id" in e for e in validate_preserved_artifact(art))
        )

    def test_negative_size_flagged(self) -> None:
        art = PreservedArtifact(kind=PreservedArtifactKind.OTHER, path="a.md", size_bytes=-1)
        self.assertTrue(any("size_bytes" in e for e in validate_preserved_artifact(art)))


class MalformedSerializationTests(unittest.TestCase):
    def test_malformed_artifact_serializes(self) -> None:
        art = PreservedArtifact(
            kind="coding",  # type: ignore[arg-type]
            path=object(),  # type: ignore[arg-type]
            source_slice_id=object(),  # type: ignore[arg-type]
            size_bytes=object(),  # type: ignore[arg-type]
            sha256=object(),  # type: ignore[arg-type]
            exists=object(),  # type: ignore[arg-type]
            diagnostics=object(),  # type: ignore[arg-type]
        )
        self.assertTrue(validate_preserved_artifact(art))
        json.dumps(art.to_dict())

    def test_malformed_slice_preservation_serializes(self) -> None:
        sp = SlicePreservation(
            slice_id=object(),  # type: ignore[arg-type]
            artifacts=(object(),),  # type: ignore[arg-type]
            diagnostics=(object(),),  # type: ignore[arg-type]
        )
        self.assertTrue(validate_slice_preservation(sp))
        json.dumps(sp.to_dict())

    def test_malformed_manifest_serializes(self) -> None:
        m = FirstPassManifest(
            baseline_slice_ids=(object(),),  # type: ignore[arg-type]
            slices=(object(),),  # type: ignore[arg-type]
            governance_artifacts=(object(),),  # type: ignore[arg-type]
            diagnostics=(object(),),  # type: ignore[arg-type]
        )
        errs = validate_first_pass_manifest(m)
        self.assertTrue(any(e.startswith("slices[0]:") for e in errs))
        self.assertTrue(any(e.startswith("governance_artifacts[0]:") for e in errs))
        json.dumps(m.to_dict())

    def test_nested_validation_prefixes(self) -> None:
        sp = SlicePreservation(
            slice_id="M013-S01",
            artifacts=(PreservedArtifact(kind=PreservedArtifactKind.OTHER, path=""),),
        )
        m = FirstPassManifest(baseline_slice_ids=("M013-S01",), slices=(sp,))
        errs = validate_first_pass_manifest(m)
        self.assertTrue(any(e.startswith("slices[0]: artifacts[0]:") for e in errs))


# ---------------------------------------------------------------------------
# Collector (temporary fixtures only)
# ---------------------------------------------------------------------------

class CollectSliceTests(unittest.TestCase):
    def test_collects_all_artifact_kinds_with_digests(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fixture(root)
            before = set(root.rglob("*"))
            sp = collect_slice_preservation(root, "M013-S01")
            self.assertEqual(validate_slice_preservation(sp), ())
            kinds = {a.kind for a in sp.artifacts}
            self.assertEqual(
                kinds,
                {
                    PreservedArtifactKind.CODING_PROMPT,
                    PreservedArtifactKind.REVIEW_PROMPT,
                    PreservedArtifactKind.SELF_REPORT,
                    PreservedArtifactKind.REVIEW_REPORT,
                    PreservedArtifactKind.VERDICT_RECORD,
                },
            )
            # every artifact has a stable relative path, size, and digest
            for a in sp.artifacts:
                self.assertTrue(a.exists)
                self.assertFalse(a.path.startswith("/"))
                self.assertIn("/", a.path)
                self.assertIsInstance(a.size_bytes, int)
                self.assertIsInstance(a.sha256, str)
            json.dumps(sp.to_dict())
            # read-only: no files created
            self.assertEqual(set(root.rglob("*")), before)

    def test_digest_matches_file_bytes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fixture(root)
            sp = collect_slice_preservation(root, "M013-S01")
            coding = next(
                a for a in sp.artifacts if a.kind == PreservedArtifactKind.CODING_PROMPT
            )
            data = (root / coding.path).read_bytes()
            self.assertEqual(coding.sha256, hashlib.sha256(data).hexdigest())
            self.assertEqual(coding.size_bytes, len(data))

    def test_deterministic_ordering(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fixture(root)
            first = collect_slice_preservation(root, "M013-S01")
            second = collect_slice_preservation(root, "M013-S01")
            self.assertEqual(
                [a.path for a in first.artifacts],
                [a.path for a in second.artifacts],
            )

    def test_missing_slice_diagnostic_not_raise(self) -> None:
        with TemporaryDirectory() as tmp:
            sp = collect_slice_preservation(Path(tmp), "M099-S09")
            self.assertEqual(sp.artifacts, ())
            self.assertTrue(any("no first-pass artifacts" in d for d in sp.diagnostics))

    def test_malformed_slice_id_diagnostic(self) -> None:
        with TemporaryDirectory() as tmp:
            sp = collect_slice_preservation(Path(tmp), "nope")
            self.assertTrue(any("slice_id" in d for d in sp.diagnostics))

    def test_token_boundary_does_not_match_s010(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / "prompts" / "for_coding_agent" / "100_frutlups_m013_s010_other.md",
                "# unrelated\n",
            )
            sp = collect_slice_preservation(root, "M013-S01")
            self.assertEqual(sp.artifacts, ())


class PartialMissingArtifactTests(unittest.TestCase):
    def _coding_only(self, root: Path) -> None:
        _write(
            root / "prompts" / "for_coding_agent" / "063_frutlups_m013_s01_only.md",
            "# only coding prompt\n",
        )

    def _review_report_only(self, root: Path) -> None:
        _write(
            root / "05_governance" / "reviews" / "m013_s01_only_review_report.md",
            "# only review report\n",
        )

    def test_coding_only_surfaces_missing_siblings(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._coding_only(root)
            sp = collect_slice_preservation(root, "M013-S01")
            # the one present kind is still preserved
            self.assertEqual(
                [a.kind for a in sp.artifacts], [PreservedArtifactKind.CODING_PROMPT]
            )
            # and every missing sibling kind is named in diagnostics
            joined = "\n".join(sp.diagnostics)
            for kind in ("review_prompt", "self_report", "review_report", "verdict_record"):
                self.assertIn(f"missing expected {kind} artifact for M013-S01", joined)
            # the present kind is NOT named missing
            self.assertNotIn("missing expected coding_prompt", joined)
            self.assertEqual(validate_slice_preservation(sp), ())
            json.dumps(sp.to_dict())

    def test_review_report_only_surfaces_missing_siblings(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._review_report_only(root)
            sp = collect_slice_preservation(root, "M013-S01")
            self.assertEqual(
                [a.kind for a in sp.artifacts], [PreservedArtifactKind.REVIEW_REPORT]
            )
            joined = "\n".join(sp.diagnostics)
            for kind in ("coding_prompt", "review_prompt", "self_report", "verdict_record"):
                self.assertIn(f"missing expected {kind} artifact for M013-S01", joined)
            self.assertNotIn("missing expected review_report", joined)
            json.dumps(sp.to_dict())

    def test_full_fixture_emits_no_missing_diagnostics(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fixture(root)
            sp = collect_slice_preservation(root, "M013-S01")
            self.assertFalse(
                any("missing expected" in d for d in sp.diagnostics),
                msg=sp.diagnostics,
            )

    def test_missing_diagnostics_deterministic_order(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._coding_only(root)
            first = collect_slice_preservation(root, "M013-S01").diagnostics
            second = collect_slice_preservation(root, "M013-S01").diagnostics
            self.assertEqual(first, second)
            # order follows the expected-kind order: review_prompt, self_report,
            # review_report, verdict_record
            self.assertEqual(
                first,
                (
                    "missing expected review_prompt artifact for M013-S01",
                    "missing expected self_report artifact for M013-S01",
                    "missing expected review_report artifact for M013-S01",
                    "missing expected verdict_record artifact for M013-S01",
                ),
            )


class CollectManifestTests(unittest.TestCase):
    def test_manifest_includes_slices_and_known_divergences(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fixture(root)
            before = set(root.rglob("*"))
            manifest = collect_first_pass_evidence(root, ("M013-S01",))
            self.assertEqual(validate_first_pass_manifest(manifest), ())
            self.assertEqual(manifest.baseline_slice_ids, ("M013-S01",))
            self.assertEqual(len(manifest.slices), 1)
            kd = manifest.governance_artifacts[0]
            self.assertEqual(kd.kind, PreservedArtifactKind.KNOWN_DIVERGENCES)
            self.assertTrue(kd.exists)
            self.assertIsInstance(kd.sha256, str)
            json.dumps(manifest.to_dict())
            self.assertEqual(set(root.rglob("*")), before)

    def test_malformed_baseline_id_skipped_with_diagnostic(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fixture(root)
            manifest = collect_first_pass_evidence(root, ("M013-S01", "bad", "M013-S01"))
            # duplicate deduped, malformed skipped
            self.assertEqual(manifest.baseline_slice_ids, ("M013-S01",))
            self.assertTrue(any("malformed" in d for d in manifest.diagnostics))

    def test_can_exclude_known_divergences(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fixture(root)
            manifest = collect_first_pass_evidence(
                root, ("M013-S01",), include_known_divergences=False
            )
            self.assertEqual(manifest.governance_artifacts, ())

    def test_missing_known_divergences_diagnostic(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            # no known_divergences.md written
            _write(
                root / "prompts" / "for_coding_agent" / "063_frutlups_m013_s01_x.md",
                "# x\n",
            )
            manifest = collect_first_pass_evidence(root, ("M013-S01",))
            kd = manifest.governance_artifacts[0]
            self.assertFalse(kd.exists)
            self.assertTrue(any("not found" in d for d in kd.diagnostics))

    def test_empty_baseline_diagnostic(self) -> None:
        with TemporaryDirectory() as tmp:
            manifest = collect_first_pass_evidence(Path(tmp), ())
            self.assertEqual(manifest.slices, ())
            self.assertTrue(any("no baseline" in d for d in manifest.diagnostics))


if __name__ == "__main__":
    unittest.main()
