"""Tests for M013-S02: second-pass evidence model and collector.

Covers the pure, JSON-safe follow-up / known-divergence models, deterministic
validation, the conservative accepted-follow-up collector (pass + verdict
record gating, needs_work exclusion, corrective citation), known-divergence
markdown parsing and diagnostics, and the combined bundle. File-based tests use
temporary directories only and assert no repository files are created.
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from frutlups.second_pass_evidence import (
    FollowUpCollectionResult,
    FollowUpItem,
    FollowUpKind,
    KnownDivergence,
    SecondPassEvidence,
    build_second_pass_evidence,
    collect_accepted_follow_ups,
    collect_known_divergences,
    collect_second_pass_evidence,
    extract_follow_ups_from_review_text,
    parse_known_divergences_text,
    validate_follow_up_item,
    validate_known_divergence,
    validate_second_pass_evidence,
)


_PASS_REPORT = """# Review Report: M020-S01 Example

## Findings

No findings.

## Residual Risk

The model validates shape but not roadmap existence.

## Known Limits and Intentional Deferrals

Second-pass prompt rendering is deferred to M013-S03.

## Verdict

pass
"""

_NEEDS_WORK_REPORT = """# Review Report: M021-S01 Example

## Residual Risk

This should not be collected because the verdict is not pass.

## Verdict

needs_work
"""

_CORRECTIVE_REPORT = """# Review Report: M021-S01 Corrective Example

## Review Notes

This corrects the finding in
`05_governance/reviews/m021_s01_example_review_report.md`.

## Residual Risk

None.

## Verdict

pass
"""

_KNOWN_DIVERGENCES = """# Known Divergences

## 2026-05-24: Inherited Illustrative Prompt Files Remain

The prompt folders still contain inherited illustrative files.

- keep them for now

## Plain Heading Without Date

Some body text here.
"""


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Models + serialization
# ---------------------------------------------------------------------------

class FollowUpItemTests(unittest.TestCase):
    def test_serializes_json_safe(self) -> None:
        item = FollowUpItem(
            source_path="05_governance/reviews/m013_s01_x_review_report.md",
            text="residual risk text",
            kind=FollowUpKind.RESIDUAL_RISK,
            source_slice_id="M013-S01",
            accepted=True,
        )
        d = item.to_dict()
        json.dumps(d)
        self.assertEqual(d["kind"], "residual_risk")
        self.assertEqual(d["source_slice_id"], "M013-S01")
        self.assertTrue(d["accepted"])

    def test_valid_item_no_errors(self) -> None:
        item = FollowUpItem(source_path="a/b.md", text="t")
        self.assertEqual(validate_follow_up_item(item), ())

    def test_empty_text_flagged(self) -> None:
        item = FollowUpItem(source_path="a/b.md", text="   ")
        self.assertTrue(any("text" in e for e in validate_follow_up_item(item)))

    def test_empty_source_path_flagged(self) -> None:
        item = FollowUpItem(source_path="", text="t")
        self.assertTrue(any("source_path" in e for e in validate_follow_up_item(item)))

    def test_absolute_source_path_flagged(self) -> None:
        for bad in ("/etc/x.md", "C:\\\\x.md"):
            item = FollowUpItem(source_path=bad, text="t")
            self.assertTrue(
                any("relative" in e for e in validate_follow_up_item(item)),
                msg=bad,
            )

    def test_bad_slice_id_flagged(self) -> None:
        item = FollowUpItem(source_path="a/b.md", text="t", source_slice_id="nope")
        self.assertTrue(
            any("source_slice_id" in e for e in validate_follow_up_item(item))
        )

    def test_bad_kind_flagged(self) -> None:
        item = FollowUpItem(source_path="a/b.md", text="t", kind="residual")  # type: ignore[arg-type]
        self.assertTrue(any("kind" in e for e in validate_follow_up_item(item)))


class KnownDivergenceTests(unittest.TestCase):
    def test_serializes_json_safe(self) -> None:
        div = KnownDivergence(
            source_path="05_governance/known_divergences.md",
            identifier="2026-05-24: Inherited Files",
            title="Inherited Files",
            body="body",
        )
        d = div.to_dict()
        json.dumps(d)
        self.assertEqual(d["title"], "Inherited Files")

    def test_valid_divergence_no_errors(self) -> None:
        div = KnownDivergence(source_path="a.md", identifier="h", title="h", body="")
        self.assertEqual(validate_known_divergence(div), ())

    def test_empty_identifier_flagged(self) -> None:
        div = KnownDivergence(source_path="a.md", identifier=" ", title="t", body="")
        self.assertTrue(any("identifier" in e for e in validate_known_divergence(div)))

    def test_absolute_source_flagged(self) -> None:
        div = KnownDivergence(source_path="/a.md", identifier="h", title="t", body="")
        self.assertTrue(any("relative" in e for e in validate_known_divergence(div)))


# ---------------------------------------------------------------------------
# Combined bundle
# ---------------------------------------------------------------------------

class SecondPassEvidenceTests(unittest.TestCase):
    def _bundle(self) -> SecondPassEvidence:
        return build_second_pass_evidence(
            slice_id="M013-S02",
            accepted_follow_ups=(
                FollowUpItem(source_path="r1.md", text="a", kind=FollowUpKind.RESIDUAL_RISK),
                FollowUpItem(source_path="r2.md", text="b", kind=FollowUpKind.DEFERRAL),
            ),
            known_divergences=(
                KnownDivergence(source_path="kd.md", identifier="h1", title="h1", body="x"),
                KnownDivergence(source_path="kd.md", identifier="h2", title="h2", body="y"),
            ),
        )

    def test_preserves_ordering(self) -> None:
        b = self._bundle()
        self.assertEqual([i.text for i in b.accepted_follow_ups], ["a", "b"])
        self.assertEqual([d.identifier for d in b.known_divergences], ["h1", "h2"])

    def test_valid_bundle_no_errors(self) -> None:
        self.assertEqual(validate_second_pass_evidence(self._bundle()), ())

    def test_to_dict_json_safe(self) -> None:
        d = self._bundle().to_dict()
        json.dumps(d)
        self.assertEqual(d["slice_id"], "M013-S02")
        self.assertIsInstance(d["accepted_follow_ups"], list)
        self.assertIsInstance(d["known_divergences"], list)

    def test_bad_slice_id_flagged(self) -> None:
        b = build_second_pass_evidence(slice_id="bad")
        self.assertTrue(any("slice_id" in e for e in validate_second_pass_evidence(b)))

    def test_malformed_member_prefixed(self) -> None:
        b = build_second_pass_evidence(
            accepted_follow_ups=(FollowUpItem(source_path="", text=""),),
            known_divergences=(KnownDivergence(source_path="", identifier="", title="", body=""),),
        )
        errs = validate_second_pass_evidence(b)
        self.assertTrue(any(e.startswith("accepted_follow_ups[0]:") for e in errs))
        self.assertTrue(any(e.startswith("known_divergences[0]:") for e in errs))

    def test_not_an_instance_flagged(self) -> None:
        self.assertTrue(validate_second_pass_evidence(object()))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Follow-up text extraction
# ---------------------------------------------------------------------------

class ExtractFollowUpsTests(unittest.TestCase):
    def test_extracts_stable_sections(self) -> None:
        items = extract_follow_ups_from_review_text(
            _PASS_REPORT, "r.md", source_slice_id="M020-S01", accepted=True
        )
        kinds = {i.kind for i in items}
        self.assertIn(FollowUpKind.RESIDUAL_RISK, kinds)
        self.assertIn(FollowUpKind.KNOWN_LIMIT, kinds)
        self.assertTrue(all(i.accepted for i in items))
        self.assertTrue(all(i.source_slice_id == "M020-S01" for i in items))

    def test_skips_noop_bodies(self) -> None:
        items = extract_follow_ups_from_review_text(_CORRECTIVE_REPORT, "r.md")
        # Residual Risk body is "None." -> skipped
        self.assertEqual(items, ())

    def test_non_string_content_is_empty(self) -> None:
        self.assertEqual(extract_follow_ups_from_review_text(None, "r.md"), ())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Known divergence parsing
# ---------------------------------------------------------------------------

class KnownDivergenceParseTests(unittest.TestCase):
    def test_parses_sections_and_strips_date(self) -> None:
        entries, diags = parse_known_divergences_text(
            _KNOWN_DIVERGENCES, "05_governance/known_divergences.md"
        )
        self.assertEqual(diags, ())
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].title, "Inherited Illustrative Prompt Files Remain")
        self.assertIn("inherited illustrative", entries[0].body.lower())
        self.assertEqual(entries[1].title, "Plain Heading Without Date")

    def test_empty_content_diagnostic(self) -> None:
        entries, diags = parse_known_divergences_text("   ", "kd.md")
        self.assertEqual(entries, ())
        self.assertTrue(any("empty" in d for d in diags))

    def test_heading_less_text_diagnostic(self) -> None:
        entries, diags = parse_known_divergences_text(
            "Just prose with no level-2 headings.\nMore prose.", "kd.md"
        )
        self.assertEqual(entries, ())
        self.assertTrue(any("no divergence sections" in d for d in diags))


# ---------------------------------------------------------------------------
# File-reading collectors (temporary directories only)
# ---------------------------------------------------------------------------

class CollectAcceptedFollowUpsTests(unittest.TestCase):
    def test_accepted_pass_with_record_produces_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "m020_s01_example_review_report.md", _PASS_REPORT)
            _write(root / "m020_s01_example_verdict_record.md", "# Verdict Record")
            result = collect_accepted_follow_ups(root, path_prefix="")
            self.assertTrue(result.items)
            self.assertTrue(all(i.accepted for i in result.items))
            self.assertEqual(set(root.glob("*")), {
                root / "m020_s01_example_review_report.md",
                root / "m020_s01_example_verdict_record.md",
            })

    def test_missing_verdict_record_blocks_acceptance(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "m020_s01_example_review_report.md", _PASS_REPORT)
            result = collect_accepted_follow_ups(root, path_prefix="")
            self.assertEqual(result.items, ())
            self.assertTrue(any("no verdict record" in d for d in result.diagnostics))

    def test_needs_work_not_accepted(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "m021_s01_example_review_report.md", _NEEDS_WORK_REPORT)
            _write(root / "m021_s01_example_verdict_record.md", "# Verdict Record")
            result = collect_accepted_follow_ups(root, path_prefix="")
            self.assertEqual(result.items, ())

    def test_corrective_cites_earlier_report(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "m021_s01_example_review_report.md", _NEEDS_WORK_REPORT)
            _write(
                root / "m021_s01_corrective_example_review_report.md",
                _CORRECTIVE_REPORT,
            )
            _write(
                root / "m021_s01_corrective_example_verdict_record.md",
                "# Verdict Record",
            )
            result = collect_accepted_follow_ups(root, path_prefix="")
            corrections = [
                i for i in result.items if i.kind == FollowUpKind.CORRECTION
            ]
            self.assertEqual(len(corrections), 1)
            self.assertTrue(
                corrections[0].source_path.endswith(
                    "m021_s01_example_review_report.md"
                )
            )
            self.assertEqual(corrections[0].source_slice_id, "M021-S01")

    def test_missing_reviews_dir_diagnostic(self) -> None:
        with TemporaryDirectory() as tmp:
            result = collect_accepted_follow_ups(Path(tmp) / "nope")
            self.assertEqual(result.items, ())
            self.assertTrue(any("not found" in d for d in result.diagnostics))


class CollectKnownDivergencesTests(unittest.TestCase):
    def test_reads_repo_relative_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            gov = root / "05_governance"
            gov.mkdir()
            _write(gov / "known_divergences.md", _KNOWN_DIVERGENCES)
            entries, diags = collect_known_divergences(root)
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0].source_path, "05_governance/known_divergences.md")
            self.assertEqual(diags, ())

    def test_missing_file_diagnostic(self) -> None:
        with TemporaryDirectory() as tmp:
            entries, diags = collect_known_divergences(Path(tmp))
            self.assertEqual(entries, ())
            self.assertTrue(any("not found" in d for d in diags))


class CollectSecondPassEvidenceTests(unittest.TestCase):
    def test_combined_bundle_is_valid_and_json_safe(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            reviews = root / "05_governance" / "reviews"
            reviews.mkdir(parents=True)
            _write(reviews / "m020_s01_example_review_report.md", _PASS_REPORT)
            _write(reviews / "m020_s01_example_verdict_record.md", "# Verdict Record")
            _write(
                root / "05_governance" / "known_divergences.md", _KNOWN_DIVERGENCES
            )
            before = set(root.rglob("*"))
            bundle = collect_second_pass_evidence(root, slice_id="M013-S02")
            self.assertEqual(validate_second_pass_evidence(bundle), ())
            self.assertTrue(bundle.accepted_follow_ups)
            self.assertEqual(len(bundle.known_divergences), 2)
            json.dumps(bundle.to_dict())
            # No files created by the collector.
            self.assertEqual(set(root.rglob("*")), before)


# ---------------------------------------------------------------------------
# Malformed-input JSON safety (M013-S02 corrective)
# ---------------------------------------------------------------------------

class MalformedSerializationTests(unittest.TestCase):
    def test_follow_up_malformed_fields_serialize(self) -> None:
        for item in (
            FollowUpItem(source_path=object(), text="x"),  # type: ignore[arg-type]
            FollowUpItem(source_path="x.md", text=object()),  # type: ignore[arg-type]
            FollowUpItem(source_path="x.md", text="t", source_slice_id=object()),  # type: ignore[arg-type]
            FollowUpItem(source_path="x.md", text="t", accepted=object()),  # type: ignore[arg-type]
            FollowUpItem(source_path="x.md", text="t", kind="residual"),  # type: ignore[arg-type]
        ):
            # validation still reports the malformed field(s)...
            self.assertTrue(validate_follow_up_item(item))
            # ...but to_dict() always survives json.dumps
            json.dumps(item.to_dict())

    def test_known_divergence_malformed_fields_serialize(self) -> None:
        for div in (
            KnownDivergence(source_path="kd.md", identifier=object(), title="t"),  # type: ignore[arg-type]
            KnownDivergence(source_path=object(), identifier="h", title="t"),  # type: ignore[arg-type]
            KnownDivergence(source_path="kd.md", identifier="h", title="t", body=object()),  # type: ignore[arg-type]
        ):
            self.assertTrue(validate_known_divergence(div))
            json.dumps(div.to_dict())

    def test_bundle_malformed_scalar_and_diagnostics_serialize(self) -> None:
        bundle = SecondPassEvidence(
            slice_id=object(),  # type: ignore[arg-type]
            diagnostics=(object(), "ok"),  # type: ignore[arg-type]
        )
        json.dumps(bundle.to_dict())

    def test_bundle_non_sequence_diagnostics_serialize(self) -> None:
        bundle = SecondPassEvidence(diagnostics=object())  # type: ignore[arg-type]
        json.dumps(bundle.to_dict())

    def test_bundle_malformed_nested_members_serialize_and_validate(self) -> None:
        bundle = SecondPassEvidence(
            slice_id="M013-S02",
            accepted_follow_ups=(FollowUpItem(source_path=object(), text=object()),),  # type: ignore[arg-type]
            known_divergences=(KnownDivergence(source_path="kd.md", identifier=object(), title="t"),),  # type: ignore[arg-type]
        )
        # nested malformed members still reported with prefixes
        errs = validate_second_pass_evidence(bundle)
        self.assertTrue(any(e.startswith("accepted_follow_ups[0]:") for e in errs))
        self.assertTrue(any(e.startswith("known_divergences[0]:") for e in errs))
        # and the payload still serializes
        json.dumps(bundle.to_dict())

    def test_bundle_non_model_member_serializes(self) -> None:
        bundle = SecondPassEvidence(
            accepted_follow_ups=(object(),),  # type: ignore[arg-type]
            known_divergences=(object(),),  # type: ignore[arg-type]
        )
        json.dumps(bundle.to_dict())

    def test_follow_up_collection_result_malformed_serializes(self) -> None:
        result = FollowUpCollectionResult(
            items=(object(),),  # type: ignore[arg-type]
            diagnostics=(object(),),  # type: ignore[arg-type]
        )
        json.dumps(result.to_dict())

    def test_valid_output_shape_unchanged(self) -> None:
        item = FollowUpItem(
            source_path="r.md",
            text="t",
            kind=FollowUpKind.RESIDUAL_RISK,
            source_slice_id="M013-S01",
            accepted=True,
        )
        self.assertEqual(
            item.to_dict(),
            {
                "source_path": "r.md",
                "text": "t",
                "kind": "residual_risk",
                "source_slice_id": "M013-S01",
                "accepted": True,
            },
        )
        div = KnownDivergence(
            source_path="kd.md", identifier="h", title="t", body="b"
        )
        self.assertEqual(
            div.to_dict(),
            {"source_path": "kd.md", "identifier": "h", "title": "t", "body": "b"},
        )
        bundle = build_second_pass_evidence(
            slice_id="M013-S02",
            accepted_follow_ups=(item,),
            known_divergences=(div,),
            diagnostics=("note",),
        )
        self.assertEqual(
            bundle.to_dict(),
            {
                "slice_id": "M013-S02",
                "accepted_follow_ups": [item.to_dict()],
                "known_divergences": [div.to_dict()],
                "diagnostics": ["note"],
            },
        )


if __name__ == "__main__":
    unittest.main()
