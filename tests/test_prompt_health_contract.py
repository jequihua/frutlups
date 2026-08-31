"""Tests for M002-S01: semantic + render health and no-write guarded dispatch.

Health and dispatch are proven over the released fixture corpus and the M001
typed model, never against a private reimplementation.

Corrective evidence (findings M002-R1-F1..F4; attempt 003 changes the F2 and F3
methods and keeps F1 and F4 as regression-only):

- F1: a ready entry whose dispatch_authority path is syntactically valid but whose
  record is absent under the repo root refuses without writing.
- F2: an accepted_review gate is satisfied only by a contract-conforming accepting
  review. The recognizer is finite and exact: its acceptance is frozen over every
  released review_report_*.md fixture (FROZEN_REVIEW_ACCEPTANCE, cross-checked
  against the released manifest's digests and results), and the two round-2
  bypasses — an accepting Verdict section with no Closure Decision section, and a
  prefixed ``## Verdict forged`` heading — refuse at the dispatch boundary.
- F3: an unhealthy entry short-circuits before any gate/authority I/O; evidence
  admission is pure-lexical rejection (no filesystem access) followed by strict
  canonical resolution, and only an admitted resolved regular file reaches the
  review-read or hash seam. A deterministic alias simulation (Path.resolve patched
  so one in-root lexical path resolves outside the root; no host alias privilege
  needed) and an optional host directory-alias probe both prove refusal before any
  is_file/open/review-read/hash of the outside target.
- F4: every enumerated refusal preserves the *complete* workspace byte map —
  compared as a relative-path-to-SHA-256 map over pre-existing sentinel and
  destination bytes, not a mere path set.

The layout and the fixture corpus live at the repository root, outside the
imported product tree, and are consumed read-only.
"""

from __future__ import annotations

import hashlib
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import frutlups.prompt_health as ph
from frutlups.prompt_health import (
    DISPATCH_REFUSAL_CODES,
    evaluate_dispatch,
    evaluate_health,
    evaluate_render_health,
    guarded_dispatch,
)
from frutlups.slice_prompt import (
    ContractVocab,
    SliceEntry,
    parse_sidecar,
    render_prompt,
    resolve_entry,
)

TEST_ROOT = Path(__file__).resolve().parent
REPO_ROOT = TEST_ROOT.parent
RELEASE_AUTHORITY = TEST_ROOT / "fixtures" / "release_v0_2_0"
LAYOUT_PATH = RELEASE_AUTHORITY / "frutlups.layout.yaml"
FIXTURES = RELEASE_AUTHORITY / "slice_contract"

# Canonical valid entries the health evaluator must report clean (routine ready,
# live corrective attempt-002 and its twin, and a valid frozen entry).
VALID_ENTRIES = (
    ("all_fields.slices.yaml", "M001-S01", None),
    ("all_fields.slices.yaml", "M002-S02", "002"),
    ("all_fields_attempt_001.slices.yaml", "M002-S02", "001"),
    ("frozen_entry_valid.slices.yaml", "M001-S01", None),
)

# One negative fixture per semantic reason-code family: the evaluator must surface
# the declared code as a health defect.
SEMANTIC_DEFECT_CASES = (
    ("write_path_directory.slices.yaml", "write_path_directory"),
    ("status_invalid.slices.yaml", "status_invalid"),
    ("objective_missing.slices.yaml", "objective_missing"),
    ("ready_without_dispatch_authority.slices.yaml", "dispatch_authority_missing"),
    ("role_owner_invalid.slices.yaml", "role_owner_invalid"),
)

# F2: the frozen accepted-review boolean for every released review-report fixture
# (tests/fixtures/slice_contract/review_report_*.md). True only for a
# contract-conforming closure record whose verdict accepts; every fixture the
# released checker refuses is False. Frozen here by hand, not derived from the
# implementation; the test proves the fixture set, the released digests, and the
# manifest's pass/fail results agree with this table before comparing the product.
FROZEN_REVIEW_ACCEPTANCE = {
    "review_report_closure_after_verdict.md": False,
    "review_report_closure_duplicate.md": False,
    "review_report_closure_missing.md": False,
    "review_report_closure_missing_evidence_line.md": False,
    "review_report_closure_not_adjacent.md": False,
    "review_report_closure_third_line.md": False,
    "review_report_closure_valid.md": True,
    "review_report_evidence_duplicate.md": True,
    "review_report_fake_opener_duplicates_valid.md": True,
    "review_report_heading_in_example_invalid.md": False,
    "review_report_indented_fence_valid.md": True,
    "review_report_long_backtick_fence_valid.md": True,
    "review_report_status_duplicate.md": True,
    "review_report_status_in_verdict.md": False,
    "review_report_status_invalid.md": False,
    "review_report_status_line_missing.md": False,
    "review_report_tilde_fenced_example_valid.md": True,
    "review_report_verdict_duplicate.md": False,
    "review_report_verdict_footer_invalid.md": False,
    "review_report_verdict_missing.md": False,
}


def _vocab() -> ContractVocab:
    return ContractVocab.from_layout(LAYOUT_PATH)


def _entry(name: str, slice_id: str) -> SliceEntry:
    parsed = parse_sidecar(FIXTURES / name, _vocab(), sidecar_path=FIXTURES / name)
    entry = parsed.entry(slice_id)
    assert entry is not None, f"{slice_id} not found in {name}"
    return entry


def _first_entry(name: str) -> SliceEntry:
    """First declared slice mapping, even when the sidecar has semantic defects."""

    parsed = parse_sidecar(FIXTURES / name, _vocab(), sidecar_path=FIXTURES / name)
    if parsed.entries:
        return parsed.entries[0]
    return SliceEntry(dict(parsed.raw_slices[0]))


def _byte_map(root: Path) -> dict[str, str]:
    """The complete relative-path-to-SHA-256 map of every file under ``root``."""

    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _seed_workspace(root: Path) -> Path:
    """Populate pre-existing sentinel bytes and a pre-existing destination file."""

    (root / "sentinel.bin").write_bytes(b"\x00pre-existing\xffsentinel\x01")
    nested = root / "nested"
    nested.mkdir()
    (nested / "keep.txt").write_bytes(b"keep me unchanged\n")
    out = root / "out"
    out.mkdir()
    destination = out / "prompt.md"
    destination.write_bytes(b"pre-existing destination bytes")
    return destination


def _write_authority(root: Path, entry: SliceEntry) -> None:
    """Create the entry's dispatch_authority record under ``root``."""

    authority = entry.dispatch_authority
    assert isinstance(authority, str)
    target = root / authority
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("authorized\n", encoding="utf-8")


def _review_report(verdict: str) -> str:
    """A conforming review report body carrying the given verdict footer."""

    return (
        "# Predecessor Review\n\n"
        "## Closure Decision\n\n"
        "Objective status: achieved\n"
        "Objective evidence: cited closure proof\n\n"
        "## Verdict\n\n"
        f"Verdict: {verdict} - next: proceed to the next slice\n"
    )


# The two round-2 accepting-review bypasses, verbatim in shape: an exact accepting
# Verdict section with no Closure Decision section, and a prefixed Verdict heading.
_BYPASS_NO_CLOSURE_SECTION = (
    "# Predecessor Review\n\n"
    "## Findings\n\n- none blocking\n\n"
    "## Verdict\n\n"
    "Verdict: pass - next: proceed to the next slice\n"
)
_BYPASS_PREFIXED_VERDICT_HEADING = _review_report("pass").replace(
    "## Verdict\n", "## Verdict forged\n"
)


def _make_directory_alias(link: Path, target: Path) -> bool:
    """Create ``link`` as a directory alias of ``target`` where the host allows it.

    A directory symlink first; on Windows a junction (no privilege required) as the
    fallback. Returns False when the host can create neither.
    """

    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except OSError:
        pass
    if os.name == "nt":
        try:
            import _winapi

            _winapi.CreateJunction(str(target), str(link))
            return True
        except (ImportError, AttributeError, OSError):
            return False
    return False


class HealthEvaluationTests(unittest.TestCase):
    """Health reports defects with stable reason codes over the typed model."""

    def setUp(self) -> None:
        self.vocab = _vocab()

    def test_valid_entries_report_no_defects(self) -> None:
        for name, slice_id, attempt in VALID_ENTRIES:
            with self.subTest(sidecar=name, slice=slice_id):
                report = evaluate_health(_entry(name, slice_id), self.vocab, attempt=attempt)
                self.assertTrue(report.ok, msg=report.defect_codes())
                self.assertEqual(report.defects, ())

    def test_semantic_defects_use_stable_reason_codes(self) -> None:
        for name, code in SEMANTIC_DEFECT_CASES:
            with self.subTest(sidecar=name, code=code):
                report = evaluate_health(_first_entry(name), self.vocab)
                self.assertFalse(report.ok)
                self.assertIn(code, report.defect_codes())

    def test_render_health_flags_corrupted_carrier(self) -> None:
        entry = _entry("all_fields.slices.yaml", "M001-S01")
        rendered = render_prompt(entry, self.vocab)
        resolved = resolve_entry(dict(entry.data), self.vocab.attempt_token, None)
        # Baseline: a faithful rendering is render-healthy.
        self.assertEqual(evaluate_render_health(rendered, resolved, self.vocab), [])
        # Corrupt one carried value so the block no longer equals the entry. The
        # double-quoted form appears only in the Typed Entry carrier, not the plain
        # workflow-metadata title line.
        corrupted = rendered.replace(
            'title: "Add the bounded route-cost ledger"',
            'title: "Add a different ledger"',
        )
        self.assertNotEqual(corrupted, rendered)
        codes = [d.code for d in evaluate_render_health(corrupted, resolved, self.vocab)]
        self.assertIn("typed_entry_mismatch", codes)

    def test_render_health_flags_status_disagreement_and_sentinel(self) -> None:
        entry = _entry("all_fields.slices.yaml", "M001-S01")
        rendered = render_prompt(entry, self.vocab)
        resolved = resolve_entry(dict(entry.data), self.vocab.attempt_token, None)
        cases = {
            "rendered_status_disagreement": rendered.replace(
                "status: ready", "status: frozen", 1
            ),
            "rendered_sentinel_residue": rendered.replace(
                "Add the bounded route-cost ledger",
                "Add the bounded route-cost ledger TBD",
            ),
        }
        for code, mutated in cases.items():
            with self.subTest(code=code):
                self.assertNotEqual(mutated, rendered)
                got = [d.code for d in evaluate_render_health(mutated, resolved, self.vocab)]
                self.assertIn(code, got)


class AcceptedReviewRecognizerTests(unittest.TestCase):
    """F2: the finite exact recognizer is frozen over every released review fixture."""

    def setUp(self) -> None:
        self.vocab = _vocab()

    def test_released_review_fixtures_match_frozen_acceptance_table(self) -> None:
        manifest = json.loads((FIXTURES / "manifest.json").read_bytes())
        released = {
            Path(item["path"]).name: item
            for item in manifest["fixtures"]
            if item.get("mode") == "review"
        }
        present = {path.name for path in FIXTURES.glob("review_report_*.md")}
        # The frozen table covers exactly the released fixture set, both ways.
        self.assertEqual(present, set(FROZEN_REVIEW_ACCEPTANCE))
        self.assertEqual(set(released), set(FROZEN_REVIEW_ACCEPTANCE))
        for name, expected in FROZEN_REVIEW_ACCEPTANCE.items():
            with self.subTest(fixture=name):
                path = FIXTURES / name
                raw = path.read_bytes()
                # The bytes under test are the released ones (manifest digest pin).
                self.assertEqual(hashlib.sha256(raw).hexdigest(), released[name]["sha256"])
                # Parity: every released fixture the checker passes carries a pass
                # footer, so the frozen boolean equals the manifest's result.
                self.assertEqual(expected, released[name]["expected"]["result"] == "pass")
                if expected:
                    self.assertIn(b"\nVerdict: pass - next: ", raw)
                self.assertIs(ph._accepting_review(path, self.vocab), expected)


class GuardedDispatchTests(unittest.TestCase):
    """The enumerated invalid cases refuse without writing; valid ones dispatch."""

    def setUp(self) -> None:
        self.vocab = _vocab()

    def _assert_refuses_without_writing(self, entry, expected_code, *, attempt=None, prepare=None):
        """A refusal preserves the complete workspace byte map and writes nothing.

        F4: compares the full relative-path-to-SHA-256 map over pre-existing
        sentinel and destination bytes before and after, not a mere path set.
        """

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            destination = _seed_workspace(root)
            if prepare is not None:
                prepare(root)
            before = _byte_map(root)
            result = guarded_dispatch(
                entry, self.vocab, repo_root=root, destination=destination, attempt=attempt
            )
            after = _byte_map(root)
            self.assertFalse(result.written)
            self.assertIsNone(result.path)
            self.assertEqual(after, before, "refusal must preserve the complete workspace byte map")
            self.assertEqual(destination.read_bytes(), b"pre-existing destination bytes")
            self.assertIn(expected_code, result.decision.refusal_codes())
            self.assertIn(expected_code, DISPATCH_REFUSAL_CODES)
            return result

    def test_frozen_entry_refuses_without_writing(self) -> None:
        self._assert_refuses_without_writing(
            _entry("frozen_entry_valid.slices.yaml", "M001-S01"), "entry_frozen"
        )

    def test_absent_authority_record_refuses_without_writing(self) -> None:
        # F1: a healthy, ready, gateless entry whose authority path is syntactically
        # valid but whose granting record does not exist under repo_root.
        result = self._assert_refuses_without_writing(
            _entry("all_fields.slices.yaml", "M001-S01"), "dispatch_authority_missing"
        )
        self.assertTrue(result.decision.health.ok, msg=result.decision.health.defect_codes())

    def test_missing_authority_field_is_unhealthy_and_refuses_without_writing(self) -> None:
        # A ready entry with no dispatch_authority field is semantically unhealthy;
        # it short-circuits to entry_unhealthy and writes nothing.
        entry = _first_entry("ready_without_dispatch_authority.slices.yaml")
        result = self._assert_refuses_without_writing(entry, "entry_unhealthy")
        self.assertIn("dispatch_authority_missing", result.decision.health.defect_codes())

    def test_unsatisfied_opening_gate_refuses_without_writing(self) -> None:
        # A valid ready entry whose opening-gate evidence is absent under repo_root;
        # its authority record is present so the gate is the sole cause.
        entry = _entry("all_fields.slices.yaml", "M002-S02")
        result = self._assert_refuses_without_writing(
            entry, "opening_gate_unsatisfied", attempt="002",
            prepare=lambda root: _write_authority(root, entry),
        )
        self.assertTrue(result.decision.health.ok, msg=result.decision.health.defect_codes())

    def test_non_ready_status_refuses_without_writing(self) -> None:
        # A prompt write not backed by a ready entry (status neither ready nor frozen).
        good = _entry("all_fields.slices.yaml", "M001-S01")
        mutated = SliceEntry({**dict(good.data), "status": "proposed"})
        self._assert_refuses_without_writing(mutated, "entry_not_ready")

    def test_invalid_gate_reference_reaches_no_evidence_io(self) -> None:
        # F3 read-spy: an escaping artifact_identity reference is flagged by semantic
        # health; the dispatcher must not stat/open/hash on the way to refusal.
        good = _entry("all_fields.slices.yaml", "M001-S01")
        entry = SliceEntry({**dict(good.data), "opening_gates": [
            {"kind": "artifact_identity", "reference": "../outside.bin", "sha256": "0" * 64},
        ]})
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_authority(root, entry)
            with mock.patch.object(ph, "_sha256_file") as hash_spy, \
                 mock.patch.object(ph, "_accepting_review") as read_spy:
                decision = evaluate_dispatch(entry, self.vocab, repo_root=root)
        self.assertFalse(decision.dispatchable)
        self.assertIn("entry_unhealthy", decision.refusal_codes())
        self.assertIn("gate_reference_invalid", decision.health.defect_codes())
        hash_spy.assert_not_called()
        read_spy.assert_not_called()

    def test_escaping_gate_reference_is_rejected_lexically_before_any_io(self) -> None:
        # F3 phase one: the gate helper rejects an escaping accepted_review reference
        # lexically — before resolution, stat, or read — even when reached directly.
        good = _entry("all_fields.slices.yaml", "M001-S01")
        entry = SliceEntry({**dict(good.data), "opening_gates": [
            {"kind": "accepted_review", "reference": "../escape_review_report.md"},
        ]})
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(Path, "resolve", autospec=True) as resolve_spy, \
                 mock.patch.object(ph, "_accepting_review") as read_spy, \
                 mock.patch.object(ph, "_sha256_file") as hash_spy:
                defects = ph._unsatisfied_gate_defects(entry, self.vocab, root)
        self.assertIn("opening_gate_unsatisfied", [d.code for d in defects])
        resolve_spy.assert_not_called()
        read_spy.assert_not_called()
        hash_spy.assert_not_called()

    def test_lexical_phase_rejects_escaping_absolute_and_directory_without_io(self) -> None:
        # F3 phase one is pure: no Path.resolve call for any input, accepted or not.
        cases = {
            "../outside.bin": None,
            "a/../../x": None,
            "05_governance/reviews/": None,
            "/rooted/path.md": None,
            "": None,
            None: None,
            "a/b.md": "a/b.md",
            "a/./b.md": "a/b.md",
        }
        with mock.patch.object(Path, "resolve", autospec=True) as resolve_spy:
            for reference, expected in cases.items():
                with self.subTest(reference=reference):
                    self.assertEqual(ph._lexical_local_reference(reference), expected)
        resolve_spy.assert_not_called()

    def test_admission_requires_a_regular_file_strictly_resolved_under_root(self) -> None:
        # F3 phase two on a real filesystem: absent and directory targets are not
        # admitted; a regular file is admitted as its resolved path, never the
        # lexical one.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "dir").mkdir()
            (root / "dir" / "file.md").write_text("x\n", encoding="utf-8")
            self.assertIsNone(ph._admitted_local_file(root, "dir/absent.md"))
            self.assertIsNone(ph._admitted_local_file(root, "dir"))
            self.assertIsNone(ph._admitted_local_file(root / "absent_root", "dir/file.md"))
            admitted = ph._admitted_local_file(root, "dir/file.md")
            self.assertEqual(admitted, (root / "dir" / "file.md").resolve(strict=True))
            self.assertTrue(admitted.is_relative_to(root.resolve(strict=True)))

    def test_simulated_alias_resolving_outside_root_reaches_no_content_io(self) -> None:
        # F3 deterministic alias simulation: an in-root lexical evidence path whose
        # strict resolution lands outside the canonical repository root is refused
        # before any is_file/open/review-read/hash of the outside target. Path.resolve
        # is patched for exactly that lexical path, so no host alias privilege is
        # needed. The outside target would satisfy each consumer if it were read: a
        # regular authority record, a conforming pass review, a matching digest.
        good = _entry("all_fields.slices.yaml", "M001-S01")
        review = _review_report("pass")
        digest = hashlib.sha256(review.encode("utf-8")).hexdigest()
        consumers = {
            "dispatch_authority": (
                lambda ref: SliceEntry({**dict(good.data), "dispatch_authority": ref}),
                "dispatch_authority_missing",
            ),
            "accepted_review": (
                lambda ref: SliceEntry({**dict(good.data), "opening_gates": [
                    {"kind": "accepted_review", "reference": ref},
                ]}),
                "opening_gate_unsatisfied",
            ),
            "artifact_identity": (
                lambda ref: SliceEntry({**dict(good.data), "opening_gates": [
                    {"kind": "artifact_identity", "reference": ref, "sha256": digest},
                ]}),
                "opening_gate_unsatisfied",
            ),
        }
        real_resolve = Path.resolve
        for name, (build, expected_code) in consumers.items():
            with self.subTest(consumer=name), TemporaryDirectory() as tmp, TemporaryDirectory() as out:
                root, outside = Path(tmp), Path(out)
                reference = f"05_governance/aliased/{name}.md"
                entry = build(reference)
                if name != "dispatch_authority":
                    _write_authority(root, entry)
                target = outside / f"{name}.md"
                target.write_text(review, encoding="utf-8")
                lexical = root / reference

                def aliased_resolve(self, strict=False, *, _lexical=lexical, _target=target):
                    if self == _lexical:
                        return real_resolve(_target, strict=strict)
                    return real_resolve(self, strict=strict)

                with mock.patch.object(Path, "resolve", aliased_resolve), \
                     mock.patch.object(Path, "is_file", autospec=True, side_effect=Path.is_file) as is_file_spy, \
                     mock.patch.object(Path, "open", autospec=True, side_effect=Path.open) as open_spy, \
                     mock.patch.object(ph, "_accepting_review", wraps=ph._accepting_review) as read_spy, \
                     mock.patch.object(ph, "_sha256_file", wraps=ph._sha256_file) as hash_spy:
                    decision = evaluate_dispatch(entry, self.vocab, repo_root=root)
                    touched = [call.args[0] for call in is_file_spy.call_args_list + open_spy.call_args_list]
                self.assertTrue(decision.health.ok, msg=decision.health.defect_codes())
                self.assertFalse(decision.dispatchable)
                self.assertIn(expected_code, decision.refusal_codes())
                read_spy.assert_not_called()
                hash_spy.assert_not_called()
                resolved_root = root.resolve(strict=True)
                for path in touched:
                    self.assertTrue(path.resolve().is_relative_to(resolved_root), msg=str(path))
        # Positive control with no alias: the same conforming review at the same
        # in-root path dispatches, and the review-read seam receives the admitted
        # resolved path, not the lexical one.
        entry = consumers["accepted_review"][0]("05_governance/aliased/accepted_review.md")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_authority(root, entry)
            lexical = root / "05_governance" / "aliased" / "accepted_review.md"
            lexical.parent.mkdir(parents=True)
            lexical.write_text(review, encoding="utf-8")
            with mock.patch.object(ph, "_accepting_review", wraps=ph._accepting_review) as read_spy:
                decision = evaluate_dispatch(entry, self.vocab, repo_root=root)
            self.assertTrue(decision.dispatchable, msg=decision.refusal_codes())
            read_spy.assert_called_once()
            self.assertEqual(read_spy.call_args.args[0], lexical.resolve(strict=True))

    def test_host_directory_alias_outside_root_is_refused_before_content_io(self) -> None:
        # F3 optional host probe: a real directory alias (symlink, or a Windows
        # junction which needs no privilege) inside the root pointing at an outside
        # directory that holds a conforming pass review. Skipped honestly when the
        # host can create neither alias.
        good = _entry("all_fields.slices.yaml", "M001-S01")
        gate_ref = "05_governance/aliased/review_report.md"
        entry = SliceEntry({**dict(good.data), "opening_gates": [
            {"kind": "accepted_review", "reference": gate_ref},
        ]})
        with TemporaryDirectory() as tmp, TemporaryDirectory() as out:
            root, outside = Path(tmp), Path(out)
            (outside / "review_report.md").write_text(_review_report("pass"), encoding="utf-8")
            link = root / "05_governance" / "aliased"
            link.parent.mkdir(parents=True)
            if not _make_directory_alias(link, outside):
                self.skipTest("host cannot create a directory symlink or junction")
            try:
                _write_authority(root, entry)
                # The lexical path reads through the alias: only resolution refuses it.
                self.assertTrue((root / gate_ref).is_file())
                self.assertFalse((root / gate_ref).resolve(strict=True).is_relative_to(root.resolve(strict=True)))
                with mock.patch.object(ph, "_accepting_review", wraps=ph._accepting_review) as read_spy, \
                     mock.patch.object(ph, "_sha256_file", wraps=ph._sha256_file) as hash_spy:
                    decision = evaluate_dispatch(entry, self.vocab, repo_root=root)
                self.assertTrue(decision.health.ok, msg=decision.health.defect_codes())
                self.assertFalse(decision.dispatchable)
                self.assertIn("opening_gate_unsatisfied", decision.refusal_codes())
                read_spy.assert_not_called()
                hash_spy.assert_not_called()
            finally:
                # Remove the alias itself, never its target.
                try:
                    os.rmdir(link)
                except OSError:
                    os.unlink(link)

    def test_accepted_review_gate_requires_conforming_accepting_verdict(self) -> None:
        # F2 at the dispatch boundary: only a conforming accepting review (pass/
        # override) satisfies the gate. Arbitrary, blocked, needs_work, and absent
        # evidence refuse, and so do the two round-2 bypasses — an accepting Verdict
        # section without a Closure Decision section, and a prefixed Verdict heading.
        good = _entry("all_fields.slices.yaml", "M001-S01")
        gate_ref = "05_governance/reviews/predecessor_review_report.md"
        entry = SliceEntry({**dict(good.data), "opening_gates": [
            {"kind": "accepted_review", "reference": gate_ref},
        ]})
        cases = {
            "arbitrary_file": ("not a review\n", False),
            "blocked_verdict": (_review_report("blocked"), False),
            "needs_work_verdict": (_review_report("needs_work"), False),
            "absent_evidence": (None, False),
            "no_closure_decision_section": (_BYPASS_NO_CLOSURE_SECTION, False),
            "prefixed_verdict_heading": (_BYPASS_PREFIXED_VERDICT_HEADING, False),
            "pass_verdict": (_review_report("pass"), True),
            "override_verdict": (_review_report("override"), True),
        }
        for name, (content, expect_dispatchable) in cases.items():
            with self.subTest(review=name):
                with TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    _write_authority(root, entry)
                    if content is not None:
                        ref = root / gate_ref
                        ref.parent.mkdir(parents=True, exist_ok=True)
                        ref.write_text(content, encoding="utf-8")
                    decision = evaluate_dispatch(entry, self.vocab, repo_root=root)
                    self.assertEqual(
                        decision.dispatchable, expect_dispatchable,
                        msg=(name, decision.refusal_codes()),
                    )
                    if not expect_dispatchable:
                        self.assertIn("opening_gate_unsatisfied", decision.refusal_codes())

    def test_ready_healthy_entry_dispatches_and_writes_exactly_the_prompt(self) -> None:
        entry = _entry("all_fields.slices.yaml", "M001-S01")  # ready, no gates, valid
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_authority(root, entry)  # authority record present (F1)
            destination = root / "out" / "rendered_prompt.md"
            destination.parent.mkdir(parents=True, exist_ok=True)
            result = guarded_dispatch(
                entry, self.vocab, repo_root=root, destination=destination
            )
            self.assertTrue(result.written, msg=result.decision.refusal_codes())
            self.assertEqual(result.path, str(destination))
            self.assertTrue(destination.is_file())
            self.assertEqual(destination.read_text(encoding="utf-8"), render_prompt(entry, self.vocab))
            self.assertEqual(result.decision.refusals, ())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
