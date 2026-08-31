"""Tests for M003-S01: durable rework-context mapping and seat role purity.

Both surfaces are proven against the released Template artifacts, never against a
private restatement:

- The rework context is resolved from the released corrective fixture entry
  (``all_fields.slices.yaml`` ``M002-S02``) with its prior evidence and ruling
  materialised as real records under a temporary repository root. A forged
  digest, an absent record, and an escaping reference (whose outside target
  exists with the *correct* bytes) each refuse the whole mapping.
- The role/type matrix is frozen by hand from the contract document's table
  (``docs/template_framework/slice_prompt_contract.md`` section 4) as
  FROZEN_MATRIX; the test first proves the layout declaration equals that table
  and then proves every (seat, artifact type) cell admits or refuses exactly as
  the table says. Reserved-path mislabels and coder-labelled review artifacts
  refuse regardless of label.
- Attempt identity (corrective finding M003-R1-F1): on both public surfaces an
  omitted attempt derives the entry's declared ``002`` and admits the declared
  row, while any supplied mismatch (``003`` with the entry's path, ``003`` with
  an undeclared ``003`` path, or any attempt on an attempt-less entry) refuses
  with exactly ``attempt_mismatch`` and never reaches the manifest lookup
  (proven by making ``SliceEntry.writes`` raise).

Every temporary repository root is created beneath the repository's ignored
``local_state/`` (M003-R1-F2) and removed after each test; the ``local_state``
directory itself is never removed.

The layout and the fixture corpus live at the repository root, outside the
imported product tree, and are consumed read-only.
"""

from __future__ import annotations

import copy
import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from frutlups.rework_context import (
    REWORK_CONTEXT_CODES,
    SEAT_WRITE_REFUSAL_CODES,
    ControllingFinding,
    EvidenceIdentity,
    ReworkContext,
    check_seat_write,
    enforce_seat_writes,
    resolve_rework_context,
)
from frutlups.slice_prompt import ContractVocab, SliceEntry, WriteEntry, parse_sidecar

TEST_ROOT = Path(__file__).resolve().parent
REPO_ROOT = TEST_ROOT.parent
RELEASE_AUTHORITY = TEST_ROOT / "fixtures" / "release_v0_2_0"
LAYOUT_PATH = RELEASE_AUTHORITY / "frutlups.layout.yaml"
FIXTURES = RELEASE_AUTHORITY / "slice_contract"
# Declared, git-ignored local-state root (ENVIRONMENT.md / .gitignore); every
# temporary repository these tests create lives directly beneath it.
LOCAL_STATE = REPO_ROOT / "local_state"

CORRECTIVE_FIXTURE = ("all_fields.slices.yaml", "M002-S02")
ROUTINE_FIXTURE = ("all_fields.slices.yaml", "M001-S01")

# Section 4 of the contract document, frozen by hand — not read from the layout
# or derived from the implementation.
FROZEN_MATRIX = {
    "coder": {
        "implementation", "test", "evidence", "analysis", "documentation",
        "fixture", "generated_output", "config", "self_report",
    },
    "reviewer": {"review_prompt", "review_report", "evidence", "analysis", "documentation"},
    "architect_reviewer": {
        "implementation", "test", "evidence", "analysis", "documentation",
        "fixture", "generated_output", "config", "coding_prompt", "review_prompt",
        "review_report", "verdict_record", "acceptance_record", "framework_doc",
        "governance_record",
    },
    "human_owner": {
        "documentation", "coding_prompt", "review_prompt", "verdict_record",
        "acceptance_record", "governance_record", "framework_doc", "config",
    },
    "runner": {
        "coding_prompt", "review_prompt", "verdict_record", "routing_state",
        "generated_output", "evidence",
    },
}
ALL_TYPES = {
    "implementation", "test", "evidence", "analysis", "documentation", "fixture",
    "generated_output", "config", "self_report", "coding_prompt", "review_prompt",
    "review_report", "verdict_record", "acceptance_record", "routing_state",
    "framework_doc", "governance_record",
}

PRIOR_EVIDENCE_BYTES = b'{"ledger": "partial"}\n'
RULING_BYTES = b"# Owner note 003\n"


def _vocab() -> ContractVocab:
    return ContractVocab.from_layout(LAYOUT_PATH)


def _entry(name: str, slice_id: str) -> SliceEntry:
    parsed = parse_sidecar(FIXTURES / name, _vocab(), sidecar_path=FIXTURES / name)
    entry = parsed.entry(slice_id)
    assert entry is not None, f"{slice_id} not found in {name}"
    return entry


def _with_correction(entry: SliceEntry, **overrides: object) -> SliceEntry:
    data = copy.deepcopy(dict(entry.data))
    data["correction"] = {**data["correction"], **overrides}
    return SliceEntry(data)


def _write(root: Path, relative: str, data: bytes) -> str:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


class _CorrectiveRepo(unittest.TestCase):
    """A temporary repository root holding the fixture entry's records by identity."""

    def setUp(self) -> None:
        LOCAL_STATE.mkdir(exist_ok=True)
        self._tmp = TemporaryDirectory(prefix="m003_test_fixture_", dir=LOCAL_STATE)
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "repo"
        self.root.mkdir()
        self.base = _entry(*CORRECTIVE_FIXTURE)
        corr = self.base.data["correction"]
        self.evidence_path = corr["prior_evidence"][0]["path"]
        self.ruling_path = corr["controlling_ruling"]
        self.evidence_digest = _write(self.root, self.evidence_path, PRIOR_EVIDENCE_BYTES)
        _write(self.root, self.ruling_path, RULING_BYTES)
        self.entry = _with_correction(
            self.base, prior_evidence=[{"path": self.evidence_path, "sha256": self.evidence_digest}]
        )


class ReworkContextResolutionTests(_CorrectiveRepo):
    def test_fixture_root_is_beneath_ignored_local_state(self) -> None:
        self.assertEqual(Path(self._tmp.name).parent, LOCAL_STATE)
        self.assertTrue(Path(self._tmp.name).is_relative_to(REPO_ROOT / "local_state"))

    def test_reason_codes_are_stable(self) -> None:
        self.assertEqual(REWORK_CONTEXT_CODES, (
            "rework_context_not_corrective", "rework_finding_invalid", "rework_finding_duplicate",
            "rework_evidence_invalid", "rework_evidence_absent", "rework_evidence_digest_mismatch",
            "rework_ruling_absent",
        ))

    def test_corrective_entry_resolves_by_identity(self) -> None:
        result = resolve_rework_context(self.entry, self.root)

        self.assertTrue(result.ok)
        self.assertEqual(result.diagnostics, ())
        self.assertEqual(result.context, ReworkContext(
            slice_id="M002-S02",
            attempt="002",
            findings=(ControllingFinding(
                id="AL2-F1",
                violated_invariant="a no-ledger project carries no ledger row",
                prior_disposition="open",
                authority_action="owner note 003 authorizes exactly one bounded replay",
                coder_obligation="re-acquire the ledger under the frozen envelope and cite the rows",
                closure_proof="nine admitted rows per arm in joined_ledger.json",
            ),),
            prior_evidence=(EvidenceIdentity(self.evidence_path, self.evidence_digest),),
            controlling_ruling=self.ruling_path,
            ruling_disputed=False,
        ))

    def test_to_dict_is_the_durable_record_shape(self) -> None:
        context = resolve_rework_context(self.entry, self.root).context

        self.assertEqual(context.to_dict(), {
            "slice": "M002-S02",
            "attempt": "002",
            "findings": [{
                "id": "AL2-F1",
                "violated_invariant": "a no-ledger project carries no ledger row",
                "prior_disposition": "open",
                "authority_action": "owner note 003 authorizes exactly one bounded replay",
                "coder_obligation": "re-acquire the ledger under the frozen envelope and cite the rows",
                "closure_proof": "nine admitted rows per arm in joined_ledger.json",
            }],
            "prior_evidence": [{"path": self.evidence_path, "sha256": self.evidence_digest}],
            "controlling_ruling": self.ruling_path,
            "ruling_disputed": False,
        })

    def test_disputed_ruling_is_recorded_as_disputed(self) -> None:
        entry = _with_correction(self.entry, controlling_ruling={"disputed": self.ruling_path})

        result = resolve_rework_context(entry, self.root)

        self.assertTrue(result.ok)
        self.assertEqual(result.context.controlling_ruling, self.ruling_path)
        self.assertTrue(result.context.ruling_disputed)

    def test_forged_prior_evidence_digest_refuses_whole_mapping(self) -> None:
        forged = "0" * 64
        entry = _with_correction(self.entry, prior_evidence=[{"path": self.evidence_path, "sha256": forged}])

        result = resolve_rework_context(entry, self.root)

        self.assertFalse(result.ok)
        self.assertIsNone(result.context)
        self.assertEqual(result.diagnostic_codes(), ("rework_evidence_digest_mismatch",))

    def test_drifted_prior_evidence_bytes_refuse(self) -> None:
        (self.root / self.evidence_path).write_bytes(PRIOR_EVIDENCE_BYTES + b"tampered\n")

        result = resolve_rework_context(self.entry, self.root)

        self.assertFalse(result.ok)
        self.assertEqual(result.diagnostic_codes(), ("rework_evidence_digest_mismatch",))

    def test_escaping_evidence_reference_refuses_even_when_outside_bytes_match(self) -> None:
        outside_digest = _write(self.root.parent, "outside.json", PRIOR_EVIDENCE_BYTES)
        entry = _with_correction(self.entry, prior_evidence=[{"path": "../outside.json", "sha256": outside_digest}])

        result = resolve_rework_context(entry, self.root)

        self.assertFalse(result.ok)
        self.assertEqual(result.diagnostic_codes(), ("rework_evidence_invalid",))

    def test_refusal_table(self) -> None:
        cases = (
            ("routine entry has no rework context",
             _entry(*ROUTINE_FIXTURE), ("rework_context_not_corrective",)),
            ("absent prior evidence record",
             _with_correction(self.entry, prior_evidence=[{"path": "01_data/evidence/missing.json", "sha256": self.evidence_digest}]),
             ("rework_evidence_absent",)),
            ("prior evidence is a directory, not a record",
             _with_correction(self.entry, prior_evidence=[{"path": "01_data/evidence", "sha256": self.evidence_digest}]),
             ("rework_evidence_invalid",)),
            ("malformed digest",
             _with_correction(self.entry, prior_evidence=[{"path": self.evidence_path, "sha256": "abc"}]),
             ("rework_evidence_invalid",)),
            ("empty prior evidence",
             _with_correction(self.entry, prior_evidence=[]), ("rework_evidence_invalid",)),
            ("absent ruling record",
             _with_correction(self.entry, controlling_ruling="05_governance/human_owner_notes/999_missing.md"),
             ("rework_ruling_absent",)),
            ("absent disputed ruling record",
             _with_correction(self.entry, controlling_ruling={"disputed": "05_governance/human_owner_notes/999_missing.md"}),
             ("rework_ruling_absent",)),
            ("incomplete finding",
             _with_correction(self.entry, findings=[{"id": "F1"}]), ("rework_finding_invalid",)),
            ("empty findings",
             _with_correction(self.entry, findings=[]), ("rework_finding_invalid",)),
            ("duplicate finding id",
             _with_correction(self.entry, findings=[self.base.data["correction"]["findings"][0]] * 2),
             ("rework_finding_duplicate",)),
        )
        for story, entry, codes in cases:
            with self.subTest(story):
                result = resolve_rework_context(entry, self.root)
                self.assertFalse(result.ok)
                self.assertIsNone(result.context)
                self.assertEqual(result.diagnostic_codes(), codes)
                self.assertTrue(all(d.location == entry.slice_id for d in result.diagnostics))

    def test_every_refusal_is_reported_together(self) -> None:
        entry = _with_correction(
            self.entry,
            prior_evidence=[{"path": self.evidence_path, "sha256": "0" * 64}],
            controlling_ruling="05_governance/human_owner_notes/999_missing.md",
        )

        result = resolve_rework_context(entry, self.root)

        self.assertEqual(result.diagnostic_codes(), ("rework_evidence_digest_mismatch", "rework_ruling_absent"))


class SeatWriteMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vocab = _vocab()

    def test_refusal_codes_are_stable(self) -> None:
        self.assertEqual(SEAT_WRITE_REFUSAL_CODES, (
            "seat_role_invalid", "write_path_invalid", "reserved_artifact_mislabeled",
            "role_type_incompatible", "attempt_mismatch", "write_outside_manifest",
            "write_role_mismatch", "write_type_mismatch",
        ))

    def test_layout_matrix_equals_the_frozen_contract_table(self) -> None:
        self.assertEqual({k: set(v) for k, v in self.vocab.role_type_matrix.items()}, FROZEN_MATRIX)
        self.assertEqual(set(self.vocab.artifact_types), ALL_TYPES)
        self.assertEqual(set(self.vocab.role_owners), set(FROZEN_MATRIX))

    def test_every_role_type_cell_admits_or_refuses_per_frozen_table(self) -> None:
        # An unreserved path so only the (seat, label) cell decides.
        path = "02_analysis/cell_probe.md"
        for seat, allowed in FROZEN_MATRIX.items():
            for artifact_type in sorted(ALL_TYPES):
                with self.subTest(seat=seat, artifact_type=artifact_type):
                    entry = SliceEntry({"slice": "M009-S01", "writes": [
                        {"path": path, "artifact_type": artifact_type, "role_owner": seat, "retry_policy": "create_once"},
                    ]})
                    refusals = check_seat_write(entry, self.vocab, seat, path, artifact_type)
                    expected = () if artifact_type in allowed else ("role_type_incompatible",)
                    self.assertEqual(tuple(d.code for d in refusals), expected)


class SeatWriteRefusalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vocab = _vocab()
        self.entry = _entry(*CORRECTIVE_FIXTURE)
        self.self_report = "05_governance/reviews/m002/m002_s02_attempt_002_self_report.md"
        self.review_report = "05_governance/reviews/m002/m002_s02_attempt_002_review_report.md"
        self.foreign_self_report = "05_governance/reviews/m002/m002_s02_attempt_003_self_report.md"

    def test_coder_manifest_rows_are_admitted_at_the_declared_attempt(self) -> None:
        coder_rows = [w.resolved("{attempt}", "002") for w in self.entry.writes if w.role_owner == "coder"]

        self.assertEqual(len(coder_rows), 3)
        self.assertEqual(enforce_seat_writes(self.entry, self.vocab, "coder", coder_rows, attempt="002"), ())

    def test_reviewer_manifest_row_is_admitted_and_coder_is_refused_on_it(self) -> None:
        self.assertEqual(
            check_seat_write(self.entry, self.vocab, "reviewer", self.review_report, "review_report", attempt="002"), (),
        )
        refusals = check_seat_write(self.entry, self.vocab, "coder", self.review_report, "review_report", attempt="002")
        self.assertEqual(tuple(d.code for d in refusals), ("role_type_incompatible", "write_role_mismatch"))

    def test_refusal_table(self) -> None:
        cases = (
            ("reviewer writing the coder self-report",
             "reviewer", self.self_report, "self_report", "002",
             ("role_type_incompatible", "write_role_mismatch")),
            ("coder labelling a reserved review-report path governance_record",
             "coder", self.review_report, "governance_record", "002",
             ("reserved_artifact_mislabeled", "role_type_incompatible", "write_role_mismatch", "write_type_mismatch")),
            ("coder labelling a reserved verdict path documentation",
             "coder", "05_governance/reviews/m002/m002_s02_verdict_record.md", "documentation", "002",
             ("reserved_artifact_mislabeled", "role_type_incompatible", "write_outside_manifest")),
            ("coder writing under the review-prompt folder as documentation",
             "coder", "prompts/for_review_agent/004_m002_s02.md", "documentation", "002",
             ("reserved_artifact_mislabeled", "role_type_incompatible", "write_outside_manifest")),
            ("coder writing a path outside the manifest",
             "coder", "08_pkg/src/frutlups/extra.py", "implementation", "002",
             ("write_outside_manifest",)),
            ("coder writing another attempt's self-report under the declared attempt",
             "coder", self.foreign_self_report, "self_report", "002",
             ("write_outside_manifest",)),
            ("coder relabelling its own manifest row",
             "coder", self.self_report, "documentation", "002",
             ("reserved_artifact_mislabeled", "write_type_mismatch")),
            ("unknown seat role",
             "auditor", self.self_report, "self_report", "002",
             ("seat_role_invalid", "write_role_mismatch")),
            ("directory path",
             "coder", "05_governance/reviews/m002/", "self_report", "002",
             ("write_path_invalid",)),
            ("absolute path",
             "coder", "/etc/passwd", "evidence", "002",
             ("write_path_invalid",)),
            ("escaping path",
             "coder", "../outside.md", "evidence", "002",
             ("write_path_invalid",)),
        )
        for story, seat, path, artifact_type, attempt, codes in cases:
            with self.subTest(story):
                refusals = check_seat_write(self.entry, self.vocab, seat, path, artifact_type, attempt=attempt)
                self.assertEqual(tuple(d.code for d in refusals), codes)
                self.assertTrue(all(d.location == f"{self.entry.slice_id}:{path}" for d in refusals))

    def test_omitted_attempt_derives_the_entry_attempt_on_both_surfaces(self) -> None:
        self.assertEqual(self.entry.attempt, "002")
        writes = [WriteEntry(self.self_report, "self_report", "coder", "create_fresh_per_attempt")]

        self.assertEqual(check_seat_write(self.entry, self.vocab, "coder", self.self_report, "self_report"), ())
        self.assertEqual(enforce_seat_writes(self.entry, self.vocab, "coder", writes), ())
        # The undeclared attempt-003 path is outside the derived attempt-002 manifest.
        self.assertEqual(
            tuple(d.code for d in check_seat_write(self.entry, self.vocab, "coder", self.foreign_self_report, "self_report")),
            ("write_outside_manifest",),
        )

    def test_attempt_mismatch_refuses_before_manifest_lookup_on_both_surfaces(self) -> None:
        routine = _entry(*ROUTINE_FIXTURE)
        self.assertIsNone(routine.attempt)
        routine_path = routine.self_report_path("{attempt}", None)
        cases = (
            ("supplied 003 with the entry's declared 002 path", self.entry, self.self_report, "003"),
            ("supplied 003 with an undeclared 003 path", self.entry, self.foreign_self_report, "003"),
            ("supplied 001 with an undeclared 001 path", self.entry,
             "05_governance/reviews/m002/m002_s02_attempt_001_self_report.md", "001"),
            ("any supplied attempt on an attempt-less entry", routine, routine_path, "001"),
        )
        for story, entry, path, attempt in cases:
            with self.subTest(story):
                with mock.patch.object(SliceEntry, "writes", new_callable=mock.PropertyMock) as writes:
                    writes.side_effect = AssertionError("manifest lookup must not run after an attempt mismatch")
                    check = check_seat_write(entry, self.vocab, "coder", path, "self_report", attempt=attempt)
                    enforce = enforce_seat_writes(
                        entry, self.vocab, "coder",
                        [WriteEntry(path, "self_report", "coder", "create_fresh_per_attempt")], attempt=attempt,
                    )
                self.assertEqual(tuple(d.code for d in check), ("attempt_mismatch",))
                self.assertEqual(tuple(d.code for d in enforce), ("attempt_mismatch",))
                self.assertEqual(check[0].location, f"{entry.slice_id}:{path}")
                self.assertEqual(writes.call_count, 0)

    def test_enforce_reports_every_write_in_order(self) -> None:
        writes = [
            WriteEntry(self.self_report, "self_report", "coder", "create_fresh_per_attempt"),
            WriteEntry(self.review_report, "review_report", "coder", "create_fresh_per_attempt"),
        ]

        refusals = enforce_seat_writes(self.entry, self.vocab, "coder", writes, attempt="002")

        self.assertEqual(
            [(d.code, d.location) for d in refusals],
            [
                ("role_type_incompatible", f"M002-S02:{self.review_report}"),
                ("write_role_mismatch", f"M002-S02:{self.review_report}"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
