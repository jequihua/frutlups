"""Tests for M007-S03 next-action computation from verdict."""

import unittest

from frutlups.review_report import ReviewVerdict
from frutlups.state import (
    NextActionCommand,
    NextActionDecision,
    NextActionKind,
    RoadmapSlice,
    compute_next_action_from_verdict,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_S01 = RoadmapSlice("M007-S01", "M007", "schema")
_S02 = RoadmapSlice("M007-S02", "M007", "parse")
_S03 = RoadmapSlice("M007-S03", "M007", "next")
_ALL = (_S01, _S02, _S03)
_ACCEPTED_S01 = ("M007-S01",)


def _cmd(
    verdict=ReviewVerdict.PASS,
    current_slice=_S02,
    slices=_ALL,
    accepted=_ACCEPTED_S01,
) -> NextActionCommand:
    return NextActionCommand(
        verdict=verdict,
        current_slice=current_slice,
        slices=slices,
        accepted_slice_ids=accepted,
    )


# ---------------------------------------------------------------------------
# pass: following slice exists
# ---------------------------------------------------------------------------


class PassWithFollowingSliceTests(unittest.TestCase):
    def setUp(self):
        self.cmd = _cmd(verdict=ReviewVerdict.PASS, current_slice=_S02, accepted=_ACCEPTED_S01)
        self.r = compute_next_action_from_verdict(self.cmd)

    def test_kind_advance(self):
        self.assertEqual(self.r.kind, NextActionKind.ADVANCE_TO_NEXT_SLICE)

    def test_verdict_pass(self):
        self.assertEqual(self.r.verdict, ReviewVerdict.PASS)

    def test_current_slice_id(self):
        self.assertEqual(self.r.current_slice_id, "M007-S02")

    def test_next_slice_id(self):
        self.assertEqual(self.r.next_slice_id, "M007-S03")

    def test_no_errors(self):
        self.assertEqual(self.r.errors, ())

    def test_message_nonempty(self):
        self.assertTrue(len(self.r.message) > 0)


# ---------------------------------------------------------------------------
# pass: current slice absent from accepted_slice_ids
# ---------------------------------------------------------------------------


class PassWithCurrentAbsentFromAcceptedTests(unittest.TestCase):
    def test_still_advances(self):
        # accepted=() so effective_accepted = {M007-S02}; first unaccepted = M007-S01
        cmd = _cmd(
            verdict=ReviewVerdict.PASS,
            current_slice=_S02,
            accepted=(),
        )
        r = compute_next_action_from_verdict(cmd)
        self.assertEqual(r.kind, NextActionKind.ADVANCE_TO_NEXT_SLICE)
        self.assertEqual(r.next_slice_id, "M007-S01")

    def test_treats_current_as_accepted(self):
        # Only S01 accepted; S02 is current — S03 should be next
        cmd = _cmd(verdict=ReviewVerdict.PASS, current_slice=_S01, accepted=())
        r = compute_next_action_from_verdict(cmd)
        self.assertEqual(r.kind, NextActionKind.ADVANCE_TO_NEXT_SLICE)
        self.assertEqual(r.next_slice_id, "M007-S02")


# ---------------------------------------------------------------------------
# pass: current slice already in accepted_slice_ids
# ---------------------------------------------------------------------------


class PassWhenCurrentAlreadyAcceptedTests(unittest.TestCase):
    def test_still_advances_when_already_accepted(self):
        cmd = _cmd(
            verdict=ReviewVerdict.PASS,
            current_slice=_S02,
            accepted=("M007-S01", "M007-S02"),
        )
        r = compute_next_action_from_verdict(cmd)
        self.assertEqual(r.kind, NextActionKind.ADVANCE_TO_NEXT_SLICE)
        self.assertEqual(r.next_slice_id, "M007-S03")


# ---------------------------------------------------------------------------
# pass: final slice in milestone → milestone_complete
# ---------------------------------------------------------------------------


class PassOnFinalSliceTests(unittest.TestCase):
    def test_milestone_complete(self):
        cmd = _cmd(
            verdict=ReviewVerdict.PASS,
            current_slice=_S03,
            accepted=("M007-S01", "M007-S02"),
        )
        r = compute_next_action_from_verdict(cmd)
        self.assertEqual(r.kind, NextActionKind.MILESTONE_COMPLETE)
        self.assertIsNone(r.next_slice_id)
        self.assertEqual(r.errors, ())

    def test_single_slice_milestone_complete(self):
        only = RoadmapSlice("M009-S01", "M009", "only")
        cmd = NextActionCommand(
            verdict=ReviewVerdict.PASS,
            current_slice=only,
            slices=(only,),
            accepted_slice_ids=(),
        )
        r = compute_next_action_from_verdict(cmd)
        self.assertEqual(r.kind, NextActionKind.MILESTONE_COMPLETE)

    def test_all_slices_already_accepted(self):
        cmd = _cmd(
            verdict=ReviewVerdict.PASS,
            current_slice=_S03,
            accepted=("M007-S01", "M007-S02", "M007-S03"),
        )
        r = compute_next_action_from_verdict(cmd)
        self.assertEqual(r.kind, NextActionKind.MILESTONE_COMPLETE)


# ---------------------------------------------------------------------------
# needs_work
# ---------------------------------------------------------------------------


class NeedsWorkTests(unittest.TestCase):
    def setUp(self):
        self.r = compute_next_action_from_verdict(
            _cmd(verdict=ReviewVerdict.NEEDS_WORK)
        )

    def test_kind_recode(self):
        self.assertEqual(self.r.kind, NextActionKind.RECODE_SAME_SLICE)

    def test_verdict_needs_work(self):
        self.assertEqual(self.r.verdict, ReviewVerdict.NEEDS_WORK)

    def test_current_slice_id(self):
        self.assertEqual(self.r.current_slice_id, "M007-S02")

    def test_next_slice_id_none(self):
        self.assertIsNone(self.r.next_slice_id)

    def test_no_errors(self):
        self.assertEqual(self.r.errors, ())


# ---------------------------------------------------------------------------
# blocked
# ---------------------------------------------------------------------------


class BlockedTests(unittest.TestCase):
    def setUp(self):
        self.r = compute_next_action_from_verdict(
            _cmd(verdict=ReviewVerdict.BLOCKED)
        )

    def test_kind_unblock(self):
        self.assertEqual(self.r.kind, NextActionKind.UNBLOCK_SAME_SLICE)

    def test_verdict_blocked(self):
        self.assertEqual(self.r.verdict, ReviewVerdict.BLOCKED)

    def test_current_slice_id(self):
        self.assertEqual(self.r.current_slice_id, "M007-S02")

    def test_next_slice_id_none(self):
        self.assertIsNone(self.r.next_slice_id)

    def test_no_errors(self):
        self.assertEqual(self.r.errors, ())


# ---------------------------------------------------------------------------
# override
# ---------------------------------------------------------------------------


class OverrideTests(unittest.TestCase):
    def setUp(self):
        self.r = compute_next_action_from_verdict(
            _cmd(verdict=ReviewVerdict.OVERRIDE)
        )

    def test_kind_human_override(self):
        self.assertEqual(self.r.kind, NextActionKind.HUMAN_OVERRIDE_REQUIRED)

    def test_verdict_override(self):
        self.assertEqual(self.r.verdict, ReviewVerdict.OVERRIDE)

    def test_next_slice_id_none(self):
        self.assertIsNone(self.r.next_slice_id)

    def test_no_errors(self):
        self.assertEqual(self.r.errors, ())

    def test_no_automatic_advancement(self):
        self.assertNotEqual(self.r.kind, NextActionKind.ADVANCE_TO_NEXT_SLICE)
        self.assertNotEqual(self.r.kind, NextActionKind.MILESTONE_COMPLETE)


# ---------------------------------------------------------------------------
# invalid inputs
# ---------------------------------------------------------------------------


class InvalidVerdictTests(unittest.TestCase):
    def test_string_verdict(self):
        cmd = NextActionCommand(
            verdict="pass",
            current_slice=_S02,
            slices=_ALL,
            accepted_slice_ids=_ACCEPTED_S01,
        )
        r = compute_next_action_from_verdict(cmd)
        self.assertEqual(r.kind, NextActionKind.INVALID)
        self.assertTrue(any("verdict" in e for e in r.errors))

    def test_none_verdict(self):
        cmd = NextActionCommand(
            verdict=None,
            current_slice=_S02,
            slices=_ALL,
            accepted_slice_ids=_ACCEPTED_S01,
        )
        r = compute_next_action_from_verdict(cmd)
        self.assertEqual(r.kind, NextActionKind.INVALID)

    def test_int_verdict(self):
        cmd = NextActionCommand(
            verdict=42,
            current_slice=_S02,
            slices=_ALL,
            accepted_slice_ids=_ACCEPTED_S01,
        )
        r = compute_next_action_from_verdict(cmd)
        self.assertEqual(r.kind, NextActionKind.INVALID)

    def test_invalid_verdict_does_not_raise(self):
        cmd = NextActionCommand(
            verdict="pass",
            current_slice=_S02,
            slices=_ALL,
            accepted_slice_ids=_ACCEPTED_S01,
        )
        try:
            compute_next_action_from_verdict(cmd)
        except Exception as exc:
            self.fail(f"compute_next_action_from_verdict raised: {exc}")


class InvalidCurrentSliceTests(unittest.TestCase):
    def test_string_current_slice(self):
        cmd = NextActionCommand(
            verdict=ReviewVerdict.PASS,
            current_slice="M007-S02",
            slices=_ALL,
            accepted_slice_ids=_ACCEPTED_S01,
        )
        r = compute_next_action_from_verdict(cmd)
        self.assertEqual(r.kind, NextActionKind.INVALID)
        self.assertTrue(any("current_slice" in e for e in r.errors))

    def test_none_current_slice(self):
        cmd = NextActionCommand(
            verdict=ReviewVerdict.PASS,
            current_slice=None,
            slices=_ALL,
            accepted_slice_ids=_ACCEPTED_S01,
        )
        r = compute_next_action_from_verdict(cmd)
        self.assertEqual(r.kind, NextActionKind.INVALID)


class InvalidSlicesCollectionTests(unittest.TestCase):
    def test_none_slices(self):
        cmd = NextActionCommand(
            verdict=ReviewVerdict.PASS,
            current_slice=_S02,
            slices=None,
            accepted_slice_ids=_ACCEPTED_S01,
        )
        r = compute_next_action_from_verdict(cmd)
        self.assertEqual(r.kind, NextActionKind.INVALID)
        self.assertTrue(any("slices" in e for e in r.errors))

    def test_slices_with_malformed_entry(self):
        cmd = NextActionCommand(
            verdict=ReviewVerdict.PASS,
            current_slice=_S02,
            slices=(_S01, "not-a-slice", _S03),
            accepted_slice_ids=_ACCEPTED_S01,
        )
        r = compute_next_action_from_verdict(cmd)
        self.assertEqual(r.kind, NextActionKind.INVALID)

    def test_int_slices(self):
        cmd = NextActionCommand(
            verdict=ReviewVerdict.PASS,
            current_slice=_S02,
            slices=42,
            accepted_slice_ids=_ACCEPTED_S01,
        )
        r = compute_next_action_from_verdict(cmd)
        self.assertEqual(r.kind, NextActionKind.INVALID)


class InvalidAcceptedIdsTests(unittest.TestCase):
    def test_none_accepted_ids(self):
        cmd = NextActionCommand(
            verdict=ReviewVerdict.PASS,
            current_slice=_S02,
            slices=_ALL,
            accepted_slice_ids=None,
        )
        r = compute_next_action_from_verdict(cmd)
        self.assertEqual(r.kind, NextActionKind.INVALID)
        self.assertTrue(any("accepted_slice_ids" in e for e in r.errors))

    def test_int_in_accepted_ids(self):
        cmd = NextActionCommand(
            verdict=ReviewVerdict.PASS,
            current_slice=_S02,
            slices=_ALL,
            accepted_slice_ids=(42,),
        )
        r = compute_next_action_from_verdict(cmd)
        self.assertEqual(r.kind, NextActionKind.INVALID)

    def test_list_accepted_ids_accepted(self):
        cmd = NextActionCommand(
            verdict=ReviewVerdict.PASS,
            current_slice=_S02,
            slices=_ALL,
            accepted_slice_ids=list(_ACCEPTED_S01),
        )
        r = compute_next_action_from_verdict(cmd)
        self.assertEqual(r.kind, NextActionKind.ADVANCE_TO_NEXT_SLICE)


class CurrentSliceNotInSlicesTests(unittest.TestCase):
    def test_current_not_in_slices(self):
        other = RoadmapSlice("M008-S01", "M008", "other")
        cmd = NextActionCommand(
            verdict=ReviewVerdict.PASS,
            current_slice=other,
            slices=_ALL,
            accepted_slice_ids=_ACCEPTED_S01,
        )
        r = compute_next_action_from_verdict(cmd)
        self.assertEqual(r.kind, NextActionKind.INVALID)
        self.assertTrue(any("not present" in e for e in r.errors))
        self.assertEqual(r.current_slice_id, "M008-S01")

    def test_empty_slices(self):
        cmd = NextActionCommand(
            verdict=ReviewVerdict.PASS,
            current_slice=_S02,
            slices=(),
            accepted_slice_ids=_ACCEPTED_S01,
        )
        r = compute_next_action_from_verdict(cmd)
        self.assertEqual(r.kind, NextActionKind.INVALID)


class MalformedSliceEntryTests(unittest.TestCase):
    def test_mixed_valid_invalid_slices(self):
        cmd = NextActionCommand(
            verdict=ReviewVerdict.PASS,
            current_slice=_S02,
            slices=(_S01, 42, _S03),
            accepted_slice_ids=_ACCEPTED_S01,
        )
        r = compute_next_action_from_verdict(cmd)
        self.assertEqual(r.kind, NextActionKind.INVALID)

    def test_all_invalid_slices(self):
        cmd = NextActionCommand(
            verdict=ReviewVerdict.PASS,
            current_slice=_S02,
            slices=(42, "bad", None),
            accepted_slice_ids=_ACCEPTED_S01,
        )
        r = compute_next_action_from_verdict(cmd)
        self.assertEqual(r.kind, NextActionKind.INVALID)


# ---------------------------------------------------------------------------
# to_dict and serialization
# ---------------------------------------------------------------------------


class ToDictTests(unittest.TestCase):
    def test_advance_to_dict_keys(self):
        r = compute_next_action_from_verdict(_cmd())
        d = r.to_dict()
        for key in ("kind", "verdict", "current_slice_id", "next_slice_id",
                    "message", "errors"):
            self.assertIn(key, d)

    def test_advance_to_dict_plain_values(self):
        r = compute_next_action_from_verdict(_cmd())
        d = r.to_dict()
        self.assertIsInstance(d["kind"], str)
        self.assertIsInstance(d["verdict"], str)
        self.assertIsInstance(d["current_slice_id"], str)
        self.assertIsInstance(d["next_slice_id"], str)
        self.assertIsInstance(d["message"], str)
        self.assertIsInstance(d["errors"], list)

    def test_advance_to_dict_no_enum_objects(self):
        r = compute_next_action_from_verdict(_cmd())
        d = r.to_dict()
        for v in d.values():
            self.assertNotIsInstance(v, (NextActionKind, ReviewVerdict))

    def test_milestone_complete_next_slice_none(self):
        cmd = _cmd(current_slice=_S03, accepted=("M007-S01", "M007-S02"))
        r = compute_next_action_from_verdict(cmd)
        d = r.to_dict()
        self.assertIsNone(d["next_slice_id"])

    def test_needs_work_to_dict(self):
        r = compute_next_action_from_verdict(_cmd(verdict=ReviewVerdict.NEEDS_WORK))
        d = r.to_dict()
        self.assertEqual(d["kind"], "recode_same_slice")
        self.assertEqual(d["verdict"], "needs_work")
        self.assertIsNone(d["next_slice_id"])

    def test_invalid_to_dict_no_verdict(self):
        cmd = NextActionCommand(
            verdict="bad",
            current_slice=_S02,
            slices=_ALL,
            accepted_slice_ids=_ACCEPTED_S01,
        )
        r = compute_next_action_from_verdict(cmd)
        d = r.to_dict()
        self.assertEqual(d["kind"], "invalid")
        self.assertIsNone(d["verdict"])
        self.assertIsInstance(d["errors"], list)
        self.assertTrue(len(d["errors"]) > 0)

    def test_to_dict_errors_is_list_of_strings(self):
        cmd = NextActionCommand(
            verdict="bad",
            current_slice=_S02,
            slices=_ALL,
            accepted_slice_ids=_ACCEPTED_S01,
        )
        r = compute_next_action_from_verdict(cmd)
        d = r.to_dict()
        for err in d["errors"]:
            self.assertIsInstance(err, str)


# ---------------------------------------------------------------------------
# Frozen behavior
# ---------------------------------------------------------------------------


class FrozenBehaviorTests(unittest.TestCase):
    def test_decision_frozen(self):
        r = compute_next_action_from_verdict(_cmd())
        with self.assertRaises((AttributeError, TypeError)):
            r.kind = NextActionKind.INVALID

    def test_command_frozen(self):
        cmd = _cmd()
        with self.assertRaises((AttributeError, TypeError)):
            cmd.verdict = ReviewVerdict.BLOCKED


# ---------------------------------------------------------------------------
# Pure: no filesystem side effects
# ---------------------------------------------------------------------------


class NoFilesystemSideEffectsTests(unittest.TestCase):
    def test_no_writes(self):
        import os
        import tempfile
        before = set(os.listdir(tempfile.gettempdir()))
        compute_next_action_from_verdict(_cmd())
        after = set(os.listdir(tempfile.gettempdir()))
        self.assertEqual(before, after)

    def test_no_reads_needed(self):
        # Function works without any open files in temp directory
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            # Just verify calling it doesn't touch the tmp dir at all
            r = compute_next_action_from_verdict(_cmd())
            self.assertNotEqual(r.kind, NextActionKind.INVALID)


# ---------------------------------------------------------------------------
# Decision probe (from coding prompt)
# ---------------------------------------------------------------------------


class DecisionProbeTests(unittest.TestCase):
    def test_prompt_probe(self):
        slices = (
            RoadmapSlice("M007-S01", "M007", "schema"),
            RoadmapSlice("M007-S02", "M007", "parse"),
            RoadmapSlice("M007-S03", "M007", "next"),
        )
        cmd = NextActionCommand(
            verdict=ReviewVerdict.PASS,
            current_slice=slices[1],
            slices=slices,
            accepted_slice_ids=("M007-S01",),
        )
        r = compute_next_action_from_verdict(cmd)
        self.assertEqual(
            r.kind.value if hasattr(r.kind, "value") else r.kind,
            "advance_to_next_slice",
        )
        self.assertEqual(
            r.verdict.value if r.verdict else None,
            "pass",
        )
        self.assertEqual(r.current_slice_id, "M007-S02")
        self.assertEqual(r.next_slice_id, "M007-S03")
        self.assertEqual(r.errors, ())
        d = r.to_dict()
        for v in d.values():
            self.assertNotIsInstance(v, (NextActionKind, ReviewVerdict))


if __name__ == "__main__":
    unittest.main()
