"""Tests for M013-S01: pass/frontier data model.

Covers the pure, JSON-safe pass/frontier model: identity and frontier
serialization, the combined model preserving baseline ids and evidence paths,
deterministic duplicate handling, deterministic validation for malformed inputs,
and the optional LoopFrontier convenience constructor. No filesystem IO.
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from frutlups.second_pass import (
    Frontier,
    FrontierSelectionKind,
    PassFrontier,
    PassIdentity,
    PassKind,
    build_pass_frontier,
    pass_frontier_from_loop_frontier,
    validate_frontier,
    validate_pass_frontier,
    validate_pass_identity,
)
from frutlups.project import build_next_frontier


def _frontier(**overrides: object) -> Frontier:
    defaults: dict[str, object] = dict(
        milestone_id="M013",
        slice_id="M013-S01",
        title="pass/frontier data model",
        selection_kind=FrontierSelectionKind.ARTIFACT_INFERRED,
    )
    defaults.update(overrides)
    return Frontier(**defaults)  # type: ignore[arg-type]


def _model(**overrides: object) -> PassFrontier:
    defaults: dict[str, object] = dict(
        pass_number=2,
        label="second pass over M001",
        kind=PassKind.SECOND_PASS,
        frontier=_frontier(),
        accepted_baseline_slice_ids=("M001-S01", "M001-S02"),
        evidence_paths=(
            "05_governance/reviews/m012_s06_mock_adapter_tests_review_report.md",
        ),
    )
    defaults.update(overrides)
    return build_pass_frontier(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

class IdentityTests(unittest.TestCase):
    def test_initial_pass_serializes(self) -> None:
        d = PassIdentity(number=1, label="initial pass").to_dict()
        json.dumps(d)
        self.assertEqual(d, {"number": 1, "label": "initial pass", "kind": "initial"})

    def test_second_pass_serializes(self) -> None:
        d = PassIdentity(number=2, label="second", kind=PassKind.SECOND_PASS).to_dict()
        json.dumps(d)
        self.assertEqual(d["kind"], "second_pass")

    def test_valid_identity_no_errors(self) -> None:
        self.assertEqual(validate_pass_identity(PassIdentity(1, "x")), ())

    def test_bad_number(self) -> None:
        self.assertTrue(any("number" in e for e in validate_pass_identity(PassIdentity(0, "x"))))
        self.assertTrue(any("number" in e for e in validate_pass_identity(PassIdentity(True, "x"))))  # type: ignore[arg-type]

    def test_bad_label(self) -> None:
        self.assertTrue(any("label" in e for e in validate_pass_identity(PassIdentity(1, "  "))))


# ---------------------------------------------------------------------------
# Frontier
# ---------------------------------------------------------------------------

class FrontierTests(unittest.TestCase):
    def test_artifact_inferred_serializes(self) -> None:
        d = _frontier().to_dict()
        json.dumps(d)
        self.assertEqual(d["milestone_id"], "M013")
        self.assertEqual(d["slice_id"], "M013-S01")
        self.assertEqual(d["title"], "pass/frontier data model")
        self.assertEqual(d["selection_kind"], "artifact_inferred")

    def test_human_selected_serializes(self) -> None:
        d = _frontier(selection_kind=FrontierSelectionKind.HUMAN_SELECTED).to_dict()
        self.assertEqual(d["selection_kind"], "human_selected")

    def test_valid_frontier_no_errors(self) -> None:
        self.assertEqual(validate_frontier(_frontier()), ())

    def test_bad_milestone_id(self) -> None:
        self.assertTrue(any("milestone_id" in e for e in validate_frontier(_frontier(milestone_id="13"))))

    def test_bad_slice_id(self) -> None:
        self.assertTrue(any("slice_id" in e for e in validate_frontier(_frontier(slice_id="S01"))))

    def test_mismatched_prefix(self) -> None:
        errs = validate_frontier(_frontier(milestone_id="M013", slice_id="M014-S01"))
        self.assertTrue(any("belong" in e for e in errs))

    def test_bad_selection_kind(self) -> None:
        errs = validate_frontier(_frontier(selection_kind="nope"))  # type: ignore[arg-type]
        self.assertTrue(any("selection_kind" in e for e in errs))


# ---------------------------------------------------------------------------
# Combined model
# ---------------------------------------------------------------------------

class PassFrontierTests(unittest.TestCase):
    def test_valid_model_no_errors(self) -> None:
        self.assertEqual(validate_pass_frontier(_model()), ())

    def test_preserves_baseline_and_evidence(self) -> None:
        m = _model()
        self.assertEqual(m.accepted_baseline_slice_ids, ("M001-S01", "M001-S02"))
        self.assertEqual(
            m.evidence_paths,
            ("05_governance/reviews/m012_s06_mock_adapter_tests_review_report.md",),
        )

    def test_to_dict_json_safe(self) -> None:
        d = _model().to_dict()
        json.dumps(d)
        self.assertEqual(d["identity"]["kind"], "second_pass")
        self.assertEqual(d["frontier"]["slice_id"], "M013-S01")
        self.assertIsInstance(d["accepted_baseline_slice_ids"], list)

    def test_builder_dedupes_baseline(self) -> None:
        m = _model(accepted_baseline_slice_ids=("M001-S01", "m001-s01", "M001-S02"))
        # case-insensitive dedupe, first occurrence kept, order preserved
        self.assertEqual(m.accepted_baseline_slice_ids, ("M001-S01", "M001-S02"))
        self.assertEqual(validate_pass_frontier(m), ())

    def test_direct_duplicate_flagged_by_validation(self) -> None:
        m = PassFrontier(
            identity=PassIdentity(2, "p", PassKind.SECOND_PASS),
            frontier=_frontier(),
            accepted_baseline_slice_ids=("M001-S01", "M001-S01"),
        )
        errs = validate_pass_frontier(m)
        self.assertTrue(any("duplicate" in e for e in errs))

    def test_bad_baseline_id(self) -> None:
        m = PassFrontier(identity=PassIdentity(1, "p"), frontier=_frontier(),
                         accepted_baseline_slice_ids=("not-a-slice",))
        self.assertTrue(any("accepted_baseline_slice_ids" in e for e in validate_pass_frontier(m)))

    def test_absolute_evidence_path_flagged(self) -> None:
        m = build_pass_frontier(
            pass_number=1, label="p", frontier=_frontier(),
            evidence_paths=("/etc/passwd",),
        )
        self.assertTrue(any("relative" in e for e in validate_pass_frontier(m)))

    def test_absolute_windows_evidence_path_flagged(self) -> None:
        m = build_pass_frontier(
            pass_number=1, label="p", frontier=_frontier(),
            evidence_paths=("C:\\\\evidence.md",),
        )
        self.assertTrue(any("relative" in e for e in validate_pass_frontier(m)))

    def test_empty_evidence_entry_flagged(self) -> None:
        m = build_pass_frontier(
            pass_number=1, label="p", frontier=_frontier(), evidence_paths=("",),
        )
        self.assertTrue(any("evidence_paths" in e for e in validate_pass_frontier(m)))

    def test_nested_validation_prefixes(self) -> None:
        m = build_pass_frontier(pass_number=0, label="p", frontier=_frontier(slice_id="bad"))
        errs = validate_pass_frontier(m)
        self.assertTrue(any(e.startswith("identity:") for e in errs))
        self.assertTrue(any(e.startswith("frontier:") for e in errs))


# ---------------------------------------------------------------------------
# LoopFrontier convenience constructor
# ---------------------------------------------------------------------------

class FromLoopFrontierTests(unittest.TestCase):
    def _live_frontier(self):
        # Build a frontier from a controlled temporary project with a guaranteed
        # pending slice, rather than the real repository: once all of frutlups'
        # own roadmap slices are accepted, ``build_next_frontier("..")`` returns
        # an empty frontier, which would make these model-construction tests
        # brittle against repository state. This keeps them deterministic and
        # honors the "no real-repo dependency" intent of this module.
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for rel in (
            "00_brief",
            "prompts/for_coding_agent",
            "prompts/for_review_agent",
            "03_experiments",
            "05_governance/reviews",
            "06_infra",
            "08_pkg",
        ):
            (root / rel).mkdir(parents=True, exist_ok=True)
        (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
            "### M002: Active One\n\nStatus: active\n", encoding="utf-8"
        )
        (root / "03_experiments" / "development_roadmap_frutlups.md").write_text(
            "### M002: Active One\n\nSlices:\n\n- M002-S01: first slice\n",
            encoding="utf-8",
        )
        return build_next_frontier(root)

    def test_builds_valid_model_from_live_frontier(self) -> None:
        lf = self._live_frontier()
        model = pass_frontier_from_loop_frontier(
            lf, pass_number=1, label="initial pass",
        )
        self.assertEqual(validate_pass_frontier(model), ())
        # the live inferred frontier is an M### slice
        self.assertTrue(model.frontier.slice_id)
        json.dumps(model.to_dict())

    def test_second_pass_cites_baseline_and_evidence(self) -> None:
        lf = self._live_frontier()
        model = pass_frontier_from_loop_frontier(
            lf,
            pass_number=2,
            label="second pass",
            kind=PassKind.SECOND_PASS,
            selection_kind=FrontierSelectionKind.HUMAN_SELECTED,
            evidence_paths=(
                "05_governance/reviews/m012_s06_mock_adapter_tests_review_report.md",
                "05_governance/reviews/m012_s06_mock_adapter_tests_verdict_record.md",
            ),
        )
        self.assertEqual(model.identity.kind, PassKind.SECOND_PASS)
        self.assertEqual(model.frontier.selection_kind, FrontierSelectionKind.HUMAN_SELECTED)
        self.assertEqual(len(model.evidence_paths), 2)
        self.assertEqual(validate_pass_frontier(model), ())

    def test_missing_inferred_slice_is_deterministic(self) -> None:
        class _EmptyFrontier:
            inferred_slice = None
            inferred_milestone = None
            accepted_slice_ids = ()

        model = pass_frontier_from_loop_frontier(
            _EmptyFrontier(), pass_number=1, label="p"  # type: ignore[arg-type]
        )
        self.assertEqual(model.frontier.slice_id, "")
        self.assertEqual(model.frontier.milestone_id, "")
        # empty frontier fields are surfaced by validation, not a crash
        self.assertTrue(validate_pass_frontier(model))


# ---------------------------------------------------------------------------
# No filesystem IO
# ---------------------------------------------------------------------------

class NoFilesystemTests(unittest.TestCase):
    def test_model_construction_creates_no_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = set(root.rglob("*"))
            m = _model()
            validate_pass_frontier(m)
            json.dumps(m.to_dict())
            self.assertEqual(set(root.rglob("*")), before)


if __name__ == "__main__":
    unittest.main()
