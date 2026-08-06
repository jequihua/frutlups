"""Tests for M007-S04 explicit human override authorization."""

import unittest

from frutlups.review_report import ReviewVerdict
from frutlups.state import (
    HumanOverrideCommand,
    HumanOverrideDecision,
    HumanOverrideTarget,
    NextActionDecision,
    NextActionKind,
    authorize_human_override,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_OVERRIDE_PRIOR = NextActionDecision(
    kind=NextActionKind.HUMAN_OVERRIDE_REQUIRED,
    verdict=ReviewVerdict.OVERRIDE,
    current_slice_id="M007-S04",
    next_slice_id=None,
    message="override requires human authorization",
    errors=(),
)

_RATIONALE = "Human owner accepts the risk and chooses the next slice explicitly."


def _cmd(
    prior=_OVERRIDE_PRIOR,
    target=HumanOverrideTarget.ADVANCE_TO_SLICE,
    rationale=_RATIONALE,
    target_slice_id="M008-S01",
    actor=None,
) -> HumanOverrideCommand:
    return HumanOverrideCommand(
        prior_decision=prior,
        target=target,
        rationale=rationale,
        target_slice_id=target_slice_id,
        actor=actor,
    )


# ---------------------------------------------------------------------------
# Valid override: recode same slice
# ---------------------------------------------------------------------------


class ValidOverrideRecodeTests(unittest.TestCase):
    def setUp(self):
        self.r = authorize_human_override(
            _cmd(target=HumanOverrideTarget.RECODE_SAME_SLICE, target_slice_id=None)
        )

    def test_valid(self):
        self.assertTrue(self.r.valid)

    def test_target(self):
        self.assertEqual(self.r.target, HumanOverrideTarget.RECODE_SAME_SLICE)

    def test_source_kind(self):
        self.assertEqual(self.r.source_kind, NextActionKind.HUMAN_OVERRIDE_REQUIRED)

    def test_current_slice_id(self):
        self.assertEqual(self.r.current_slice_id, "M007-S04")

    def test_next_slice_id_none(self):
        self.assertIsNone(self.r.next_slice_id)

    def test_no_errors(self):
        self.assertEqual(self.r.errors, ())

    def test_rationale_preserved(self):
        self.assertEqual(self.r.rationale, _RATIONALE)


# ---------------------------------------------------------------------------
# Valid override: unblock same slice
# ---------------------------------------------------------------------------


class ValidOverrideUnblockTests(unittest.TestCase):
    def setUp(self):
        self.r = authorize_human_override(
            _cmd(target=HumanOverrideTarget.UNBLOCK_SAME_SLICE, target_slice_id=None)
        )

    def test_valid(self):
        self.assertTrue(self.r.valid)

    def test_target(self):
        self.assertEqual(self.r.target, HumanOverrideTarget.UNBLOCK_SAME_SLICE)

    def test_no_errors(self):
        self.assertEqual(self.r.errors, ())

    def test_next_slice_id_none(self):
        self.assertIsNone(self.r.next_slice_id)


# ---------------------------------------------------------------------------
# Valid override: advance to explicit next slice
# ---------------------------------------------------------------------------


class ValidOverrideAdvanceTests(unittest.TestCase):
    def setUp(self):
        self.r = authorize_human_override(_cmd())

    def test_valid(self):
        self.assertTrue(self.r.valid)

    def test_target(self):
        self.assertEqual(self.r.target, HumanOverrideTarget.ADVANCE_TO_SLICE)

    def test_source_kind(self):
        self.assertEqual(self.r.source_kind, NextActionKind.HUMAN_OVERRIDE_REQUIRED)

    def test_current_slice_id(self):
        self.assertEqual(self.r.current_slice_id, "M007-S04")

    def test_next_slice_id(self):
        self.assertEqual(self.r.next_slice_id, "M008-S01")

    def test_no_errors(self):
        self.assertEqual(self.r.errors, ())

    def test_rationale_preserved(self):
        self.assertEqual(self.r.rationale, _RATIONALE)

    def test_message_nonempty(self):
        self.assertTrue(len(self.r.message) > 0)


# ---------------------------------------------------------------------------
# Valid override: milestone complete
# ---------------------------------------------------------------------------


class ValidOverrideMilestoneCompleteTests(unittest.TestCase):
    def setUp(self):
        self.r = authorize_human_override(
            _cmd(
                target=HumanOverrideTarget.MILESTONE_COMPLETE,
                target_slice_id=None,
            )
        )

    def test_valid(self):
        self.assertTrue(self.r.valid)

    def test_target(self):
        self.assertEqual(self.r.target, HumanOverrideTarget.MILESTONE_COMPLETE)

    def test_next_slice_id_none(self):
        self.assertIsNone(self.r.next_slice_id)

    def test_no_errors(self):
        self.assertEqual(self.r.errors, ())


# ---------------------------------------------------------------------------
# Non-override prior decision rejected
# ---------------------------------------------------------------------------


class NonOverridePriorRejectedTests(unittest.TestCase):
    def _pass_prior(self):
        return NextActionDecision(
            kind=NextActionKind.ADVANCE_TO_NEXT_SLICE,
            verdict=ReviewVerdict.PASS,
            current_slice_id="M007-S04",
            next_slice_id="M008-S01",
            message="advance",
            errors=(),
        )

    def test_pass_prior_rejected(self):
        cmd = _cmd(prior=self._pass_prior())
        r = authorize_human_override(cmd)
        self.assertFalse(r.valid)
        self.assertTrue(len(r.errors) > 0)

    def test_needs_work_prior_rejected(self):
        prior = NextActionDecision(
            kind=NextActionKind.RECODE_SAME_SLICE,
            verdict=ReviewVerdict.NEEDS_WORK,
            current_slice_id="M007-S04",
            next_slice_id=None,
            message="recode",
            errors=(),
        )
        cmd = _cmd(prior=prior)
        r = authorize_human_override(cmd)
        self.assertFalse(r.valid)
        self.assertTrue(any("kind" in e or "HUMAN_OVERRIDE_REQUIRED" in e for e in r.errors))

    def test_does_not_raise(self):
        cmd = _cmd(prior=self._pass_prior())
        try:
            authorize_human_override(cmd)
        except Exception as exc:
            self.fail(f"authorize_human_override raised: {exc}")


# ---------------------------------------------------------------------------
# Prior decision with override verdict but wrong kind rejected
# ---------------------------------------------------------------------------


class WrongKindWithOverrideVerdictTests(unittest.TestCase):
    def setUp(self):
        # Override verdict but kind is ADVANCE (unusual/malformed state)
        self.weird_prior = NextActionDecision(
            kind=NextActionKind.ADVANCE_TO_NEXT_SLICE,
            verdict=ReviewVerdict.OVERRIDE,
            current_slice_id="M007-S04",
            next_slice_id="M008-S01",
            message="weird",
            errors=(),
        )

    def test_rejected(self):
        cmd = _cmd(prior=self.weird_prior)
        r = authorize_human_override(cmd)
        self.assertFalse(r.valid)
        self.assertTrue(any("kind" in e or "HUMAN_OVERRIDE_REQUIRED" in e for e in r.errors))

    def test_does_not_raise(self):
        cmd = _cmd(prior=self.weird_prior)
        try:
            authorize_human_override(cmd)
        except Exception as exc:
            self.fail(f"authorize_human_override raised: {exc}")


# ---------------------------------------------------------------------------
# Missing / whitespace rationale
# ---------------------------------------------------------------------------


class MissingRationaleTests(unittest.TestCase):
    def test_none_rationale(self):
        cmd = HumanOverrideCommand(
            prior_decision=_OVERRIDE_PRIOR,
            target=HumanOverrideTarget.ADVANCE_TO_SLICE,
            rationale=None,
            target_slice_id="M008-S01",
        )
        r = authorize_human_override(cmd)
        self.assertFalse(r.valid)
        self.assertTrue(any("rationale" in e for e in r.errors))

    def test_whitespace_rationale(self):
        cmd = _cmd(rationale="   ")
        r = authorize_human_override(cmd)
        self.assertFalse(r.valid)
        self.assertTrue(any("rationale" in e for e in r.errors))

    def test_empty_string_rationale(self):
        cmd = _cmd(rationale="")
        r = authorize_human_override(cmd)
        self.assertFalse(r.valid)
        self.assertTrue(any("rationale" in e for e in r.errors))

    def test_whitespace_does_not_raise(self):
        cmd = _cmd(rationale="   ")
        try:
            authorize_human_override(cmd)
        except Exception as exc:
            self.fail(f"authorize_human_override raised: {exc}")


# ---------------------------------------------------------------------------
# Malformed target
# ---------------------------------------------------------------------------


class MalformedTargetTests(unittest.TestCase):
    def test_string_target(self):
        cmd = HumanOverrideCommand(
            prior_decision=_OVERRIDE_PRIOR,
            target="advance_to_slice",
            rationale=_RATIONALE,
            target_slice_id="M008-S01",
        )
        r = authorize_human_override(cmd)
        self.assertFalse(r.valid)
        self.assertTrue(any("target" in e for e in r.errors))

    def test_none_target(self):
        cmd = HumanOverrideCommand(
            prior_decision=_OVERRIDE_PRIOR,
            target=None,
            rationale=_RATIONALE,
            target_slice_id="M008-S01",
        )
        r = authorize_human_override(cmd)
        self.assertFalse(r.valid)

    def test_int_target(self):
        cmd = HumanOverrideCommand(
            prior_decision=_OVERRIDE_PRIOR,
            target=42,
            rationale=_RATIONALE,
            target_slice_id="M008-S01",
        )
        r = authorize_human_override(cmd)
        self.assertFalse(r.valid)

    def test_does_not_raise(self):
        cmd = HumanOverrideCommand(
            prior_decision=_OVERRIDE_PRIOR,
            target="bad",
            rationale=_RATIONALE,
            target_slice_id="M008-S01",
        )
        try:
            authorize_human_override(cmd)
        except Exception as exc:
            self.fail(f"authorize_human_override raised: {exc}")


# ---------------------------------------------------------------------------
# Missing target_slice_id for advance
# ---------------------------------------------------------------------------


class MissingTargetSliceIdTests(unittest.TestCase):
    def test_none_target_slice_id_for_advance(self):
        cmd = _cmd(target_slice_id=None)
        r = authorize_human_override(cmd)
        self.assertFalse(r.valid)
        self.assertTrue(any("target_slice_id" in e for e in r.errors))

    def test_empty_target_slice_id_for_advance(self):
        cmd = _cmd(target_slice_id="")
        r = authorize_human_override(cmd)
        self.assertFalse(r.valid)

    def test_whitespace_target_slice_id_for_advance(self):
        cmd = _cmd(target_slice_id="   ")
        r = authorize_human_override(cmd)
        self.assertFalse(r.valid)

    def test_does_not_raise(self):
        cmd = _cmd(target_slice_id=None)
        try:
            authorize_human_override(cmd)
        except Exception as exc:
            self.fail(f"authorize_human_override raised: {exc}")


# ---------------------------------------------------------------------------
# Unexpected target_slice_id for same-slice / milestone-complete targets
# ---------------------------------------------------------------------------


class UnexpectedTargetSliceIdTests(unittest.TestCase):
    def test_recode_with_slice_id(self):
        cmd = _cmd(
            target=HumanOverrideTarget.RECODE_SAME_SLICE,
            target_slice_id="M008-S01",
        )
        r = authorize_human_override(cmd)
        self.assertFalse(r.valid)
        self.assertTrue(any("target_slice_id" in e for e in r.errors))

    def test_unblock_with_slice_id(self):
        cmd = _cmd(
            target=HumanOverrideTarget.UNBLOCK_SAME_SLICE,
            target_slice_id="M008-S01",
        )
        r = authorize_human_override(cmd)
        self.assertFalse(r.valid)
        self.assertTrue(any("target_slice_id" in e for e in r.errors))

    def test_milestone_complete_with_slice_id(self):
        cmd = _cmd(
            target=HumanOverrideTarget.MILESTONE_COMPLETE,
            target_slice_id="M008-S01",
        )
        r = authorize_human_override(cmd)
        self.assertFalse(r.valid)
        self.assertTrue(any("target_slice_id" in e for e in r.errors))

    def test_does_not_raise(self):
        cmd = _cmd(
            target=HumanOverrideTarget.RECODE_SAME_SLICE,
            target_slice_id="M008-S01",
        )
        try:
            authorize_human_override(cmd)
        except Exception as exc:
            self.fail(f"authorize_human_override raised: {exc}")


# ---------------------------------------------------------------------------
# Malformed command rejected without raising
# ---------------------------------------------------------------------------


class MalformedCommandTests(unittest.TestCase):
    def test_non_command_object(self):
        r = authorize_human_override(42)
        self.assertFalse(r.valid)
        self.assertTrue(any("command" in e for e in r.errors))

    def test_none_command(self):
        r = authorize_human_override(None)
        self.assertFalse(r.valid)

    def test_string_command(self):
        r = authorize_human_override("bad")
        self.assertFalse(r.valid)

    def test_non_command_does_not_raise(self):
        try:
            authorize_human_override(42)
        except Exception as exc:
            self.fail(f"authorize_human_override raised: {exc}")

    def test_command_with_bad_prior(self):
        cmd = HumanOverrideCommand(
            prior_decision=42,
            target=HumanOverrideTarget.ADVANCE_TO_SLICE,
            rationale=_RATIONALE,
            target_slice_id="M008-S01",
        )
        r = authorize_human_override(cmd)
        self.assertFalse(r.valid)

    def test_command_with_bad_prior_does_not_raise(self):
        cmd = HumanOverrideCommand(
            prior_decision="not-a-decision",
            target=HumanOverrideTarget.ADVANCE_TO_SLICE,
            rationale=_RATIONALE,
            target_slice_id="M008-S01",
        )
        try:
            authorize_human_override(cmd)
        except Exception as exc:
            self.fail(f"authorize_human_override raised: {exc}")


# ---------------------------------------------------------------------------
# to_dict correctness
# ---------------------------------------------------------------------------


class ToDictTests(unittest.TestCase):
    def test_valid_advance_to_dict_keys(self):
        r = authorize_human_override(_cmd())
        d = r.to_dict()
        for key in ("valid", "target", "source_kind", "current_slice_id",
                    "next_slice_id", "rationale", "message", "errors"):
            self.assertIn(key, d)

    def test_valid_advance_to_dict_plain_values(self):
        r = authorize_human_override(_cmd())
        d = r.to_dict()
        self.assertIsInstance(d["valid"], bool)
        self.assertIsInstance(d["target"], str)
        self.assertIsInstance(d["source_kind"], str)
        self.assertIsInstance(d["current_slice_id"], str)
        self.assertIsInstance(d["next_slice_id"], str)
        self.assertIsInstance(d["rationale"], str)
        self.assertIsInstance(d["message"], str)
        self.assertIsInstance(d["errors"], list)

    def test_valid_advance_to_dict_no_enum_objects(self):
        r = authorize_human_override(_cmd())
        d = r.to_dict()
        for v in d.values():
            self.assertNotIsInstance(v, (HumanOverrideTarget, NextActionKind))

    def test_failed_to_dict_target_none(self):
        r = authorize_human_override(_cmd(rationale="   "))
        d = r.to_dict()
        self.assertFalse(d["valid"])
        self.assertIsNone(d["target"])

    def test_failed_to_dict_errors_list(self):
        r = authorize_human_override(_cmd(rationale="   "))
        d = r.to_dict()
        self.assertIsInstance(d["errors"], list)
        self.assertTrue(len(d["errors"]) > 0)
        for e in d["errors"]:
            self.assertIsInstance(e, str)

    def test_recode_to_dict_next_slice_none(self):
        r = authorize_human_override(
            _cmd(target=HumanOverrideTarget.RECODE_SAME_SLICE, target_slice_id=None)
        )
        d = r.to_dict()
        self.assertIsNone(d["next_slice_id"])

    def test_advance_to_dict_target_string(self):
        r = authorize_human_override(_cmd())
        d = r.to_dict()
        self.assertEqual(d["target"], "advance_to_slice")

    def test_advance_to_dict_source_kind_string(self):
        r = authorize_human_override(_cmd())
        d = r.to_dict()
        self.assertEqual(d["source_kind"], "human_override_required")


# ---------------------------------------------------------------------------
# Frozen behavior
# ---------------------------------------------------------------------------


class FrozenBehaviorTests(unittest.TestCase):
    def test_decision_frozen(self):
        r = authorize_human_override(_cmd())
        with self.assertRaises((AttributeError, TypeError)):
            r.valid = False

    def test_command_frozen(self):
        cmd = _cmd()
        with self.assertRaises((AttributeError, TypeError)):
            cmd.rationale = "changed"


# ---------------------------------------------------------------------------
# Pure: no filesystem side effects
# ---------------------------------------------------------------------------


class NoFilesystemSideEffectsTests(unittest.TestCase):
    def test_no_writes(self):
        import os
        import tempfile
        before = set(os.listdir(tempfile.gettempdir()))
        authorize_human_override(_cmd())
        after = set(os.listdir(tempfile.gettempdir()))
        self.assertEqual(before, after)


# ---------------------------------------------------------------------------
# Override probe (from coding prompt)
# ---------------------------------------------------------------------------


class OverrideProbeTests(unittest.TestCase):
    def test_positive_probe(self):
        prior = NextActionDecision(
            kind=NextActionKind.HUMAN_OVERRIDE_REQUIRED,
            verdict=ReviewVerdict.OVERRIDE,
            current_slice_id="M007-S04",
            next_slice_id=None,
            message="override requires human authorization",
            errors=(),
        )
        cmd = HumanOverrideCommand(
            prior_decision=prior,
            target=HumanOverrideTarget.ADVANCE_TO_SLICE,
            rationale="Human owner accepts the risk and chooses the next slice explicitly.",
            target_slice_id="M008-S01",
        )
        r = authorize_human_override(cmd)
        self.assertTrue(r.valid)
        self.assertEqual(
            r.target.value if hasattr(r.target, "value") else r.target,
            "advance_to_slice",
        )
        self.assertEqual(
            r.source_kind.value if hasattr(r.source_kind, "value") else r.source_kind,
            "human_override_required",
        )
        self.assertEqual(r.current_slice_id, "M007-S04")
        self.assertEqual(r.next_slice_id, "M008-S01")
        self.assertEqual(r.errors, ())
        d = r.to_dict()
        for v in d.values():
            self.assertNotIsInstance(v, (HumanOverrideTarget, NextActionKind))

    def test_negative_probe_whitespace_rationale(self):
        prior = NextActionDecision(
            kind=NextActionKind.HUMAN_OVERRIDE_REQUIRED,
            verdict=ReviewVerdict.OVERRIDE,
            current_slice_id="M007-S04",
            next_slice_id=None,
            message="override requires human authorization",
            errors=(),
        )
        cmd = HumanOverrideCommand(
            prior_decision=prior,
            target=HumanOverrideTarget.ADVANCE_TO_SLICE,
            rationale="   ",
            target_slice_id="M008-S01",
        )
        r = authorize_human_override(cmd)
        self.assertFalse(r.valid)
        self.assertTrue(any("rationale" in e for e in r.errors))
        # Confirm no exception was raised (probe requirement)


if __name__ == "__main__":
    unittest.main()
