"""Tests for M005-S01 frontier-v2: routing from separated closure receipts.

Every expected route below is written by hand from the governing contracts
(review protocol Closure Record; closure convergence "Objective Status Is
Not A Verdict"), never generated from the implementation. Each case asserts
the route, the completion flag, the branch reason (causal witness), and the
three separated receipt fields.
"""

import unittest
from pathlib import Path
from unittest import mock

import frutlups.frontier as frontier_module
from frutlups.closure import (
    ClosureParseResult,
    FrutlupsRoute,
    ObjectiveStatus,
    parse_closure_decision_file,
    parse_closure_decision_text,
)
from frutlups.frontier import (
    ACCEPTING_VERDICTS,
    FrontierTransition,
    compute_frontier_transition,
    frontier_transition_from_report_text,
)
from frutlups.review_report import ReviewVerdict

_FIXTURE_DIR = (
    Path(__file__).resolve().parent / "fixtures" / "release_v0_2_0" / "slice_contract"
)


def _closure(verdict: str, status: str) -> ClosureParseResult:
    return ClosureParseResult(
        valid=True,
        verdict=ReviewVerdict(verdict),
        objective_status=ObjectiveStatus(status),
        objective_evidence="cited artifact",
        next_move="one move",
        reason_codes=(),
    )


def _report(verdict: str, status: str) -> str:
    return (
        "# Review Report\n\n## Findings\n\n- none\n\n## Closure Decision\n\n"
        f"Objective status: {status}\nObjective evidence: cited artifact\n\n"
        f"## Verdict\n\nVerdict: {verdict} - next: one move\n"
    )


# (verdict, objective, is_last_slice) -> (route, milestone_complete, reason)
# with no explicit routing status. All 32 combinations, written by hand.
_ROUTING_TABLE = [
    ("pass", "achieved", False, "advance_to_next_slice", False, "accepted_achieved_advances"),
    ("pass", "achieved", True, "milestone_complete", True, "accepted_achieved_last_slice"),
    ("pass", "not_achieved", False, "human_override_required", False, "accepted_not_achieved_requires_human_routing"),
    ("pass", "not_achieved", True, "human_override_required", False, "accepted_not_achieved_requires_human_routing"),
    ("pass", "not_applicable", False, "human_override_required", False, "accepted_not_applicable_without_compatible_routing_status"),
    ("pass", "not_applicable", True, "human_override_required", False, "accepted_not_applicable_without_compatible_routing_status"),
    ("pass", "indeterminate", False, "human_override_required", False, "accepted_indeterminate_requires_human_routing"),
    ("pass", "indeterminate", True, "human_override_required", False, "accepted_indeterminate_requires_human_routing"),
    ("override", "achieved", False, "advance_to_next_slice", False, "accepted_achieved_advances"),
    ("override", "achieved", True, "milestone_complete", True, "accepted_achieved_last_slice"),
    ("override", "not_achieved", False, "human_override_required", False, "accepted_not_achieved_requires_human_routing"),
    ("override", "not_achieved", True, "human_override_required", False, "accepted_not_achieved_requires_human_routing"),
    ("override", "not_applicable", False, "human_override_required", False, "accepted_not_applicable_without_compatible_routing_status"),
    ("override", "not_applicable", True, "human_override_required", False, "accepted_not_applicable_without_compatible_routing_status"),
    ("override", "indeterminate", False, "human_override_required", False, "accepted_indeterminate_requires_human_routing"),
    ("override", "indeterminate", True, "human_override_required", False, "accepted_indeterminate_requires_human_routing"),
    ("needs_work", "achieved", False, "recode_same_slice", False, "needs_work_recodes_same_slice"),
    ("needs_work", "achieved", True, "recode_same_slice", False, "needs_work_recodes_same_slice"),
    ("needs_work", "not_achieved", False, "recode_same_slice", False, "needs_work_recodes_same_slice"),
    ("needs_work", "not_achieved", True, "recode_same_slice", False, "needs_work_recodes_same_slice"),
    ("needs_work", "not_applicable", False, "recode_same_slice", False, "needs_work_recodes_same_slice"),
    ("needs_work", "not_applicable", True, "recode_same_slice", False, "needs_work_recodes_same_slice"),
    ("needs_work", "indeterminate", False, "recode_same_slice", False, "needs_work_recodes_same_slice"),
    ("needs_work", "indeterminate", True, "recode_same_slice", False, "needs_work_recodes_same_slice"),
    ("blocked", "achieved", False, "unblock_same_slice", False, "blocked_unblocks_same_slice"),
    ("blocked", "achieved", True, "unblock_same_slice", False, "blocked_unblocks_same_slice"),
    ("blocked", "not_achieved", False, "unblock_same_slice", False, "blocked_unblocks_same_slice"),
    ("blocked", "not_achieved", True, "unblock_same_slice", False, "blocked_unblocks_same_slice"),
    ("blocked", "not_applicable", False, "unblock_same_slice", False, "blocked_unblocks_same_slice"),
    ("blocked", "not_applicable", True, "unblock_same_slice", False, "blocked_unblocks_same_slice"),
    ("blocked", "indeterminate", False, "unblock_same_slice", False, "blocked_unblocks_same_slice"),
    ("blocked", "indeterminate", True, "unblock_same_slice", False, "blocked_unblocks_same_slice"),
]


class RoutingTableTests(unittest.TestCase):
    def test_accepting_verdicts_are_exactly_pass_and_override(self):
        self.assertEqual(
            ACCEPTING_VERDICTS, frozenset({ReviewVerdict.PASS, ReviewVerdict.OVERRIDE})
        )

    def test_every_verdict_objective_position_combination_routes_as_tabled(self):
        self.assertEqual(len(_ROUTING_TABLE), 32)
        for verdict, status, last, route, complete, reason in _ROUTING_TABLE:
            with self.subTest(verdict=verdict, status=status, last=last):
                for label, transition in (
                    ("parsed", compute_frontier_transition(_closure(verdict, status), is_last_slice=last)),
                    ("text", frontier_transition_from_report_text(_report(verdict, status), is_last_slice=last)),
                ):
                    with self.subTest(path=label):
                        self.assertEqual(transition.route.value, route)
                        self.assertEqual(transition.milestone_complete, complete)
                        self.assertEqual(transition.reason, reason)
                        self.assertEqual(
                            transition.receipt.to_dict(),
                            {"verdict": verdict, "objective_status": status, "route": route},
                        )

    def test_only_two_combinations_complete_without_explicit_routing(self):
        completing = [
            (v, s, last) for v, s, last, _r, complete, _reason in _ROUTING_TABLE if complete
        ]
        self.assertEqual(
            completing, [("pass", "achieved", True), ("override", "achieved", True)]
        )


class ExplicitRoutingStatusTests(unittest.TestCase):
    def test_not_applicable_completes_only_with_explicit_milestone_complete(self):
        cases = [
            ("pass last explicit complete", "pass", True, "milestone_complete", "milestone_complete", True, "accepted_not_applicable_explicit_milestone_complete"),
            ("pass not last explicit complete", "pass", False, "milestone_complete", "milestone_complete", True, "accepted_not_applicable_explicit_milestone_complete"),
            ("override last explicit complete", "override", True, FrutlupsRoute.MILESTONE_COMPLETE, "milestone_complete", True, "accepted_not_applicable_explicit_milestone_complete"),
            ("pass explicit advance", "pass", True, "advance_to_next_slice", "advance_to_next_slice", False, "accepted_not_applicable_explicit_advance"),
            ("pass explicit recode is not compatible", "pass", True, "recode_same_slice", "human_override_required", False, "accepted_not_applicable_without_compatible_routing_status"),
            ("pass explicit unblock is not compatible", "pass", True, "unblock_same_slice", "human_override_required", False, "accepted_not_applicable_without_compatible_routing_status"),
            ("pass explicit human override", "pass", True, "human_override_required", "human_override_required", False, "accepted_not_applicable_without_compatible_routing_status"),
            ("pass explicit invalid", "pass", True, "invalid", "human_override_required", False, "accepted_not_applicable_without_compatible_routing_status"),
            ("pass no status", "pass", True, None, "human_override_required", False, "accepted_not_applicable_without_compatible_routing_status"),
        ]
        for label, verdict, last, explicit, route, complete, reason in cases:
            with self.subTest(case=label):
                transition = compute_frontier_transition(
                    _closure(verdict, "not_applicable"),
                    is_last_slice=last,
                    explicit_routing_status=explicit,
                )
                self.assertEqual(transition.route.value, route)
                self.assertEqual(transition.milestone_complete, complete)
                self.assertEqual(transition.reason, reason)
                self.assertEqual(transition.receipt.objective_status, ObjectiveStatus.NOT_APPLICABLE)
                self.assertEqual(transition.receipt.verdict.value, verdict)

    def test_explicit_status_never_overrides_other_objective_statuses(self):
        cases = [
            ("pass not_achieved last", "pass", "not_achieved", True, "human_override_required"),
            ("pass indeterminate last", "pass", "indeterminate", True, "human_override_required"),
            ("override not_achieved", "override", "not_achieved", False, "human_override_required"),
            ("needs_work achieved last", "needs_work", "achieved", True, "recode_same_slice"),
            ("blocked achieved last", "blocked", "achieved", True, "unblock_same_slice"),
            ("pass achieved not last", "pass", "achieved", False, "advance_to_next_slice"),
        ]
        for label, verdict, status, last, route in cases:
            with self.subTest(case=label):
                transition = compute_frontier_transition(
                    _closure(verdict, status),
                    is_last_slice=last,
                    explicit_routing_status="milestone_complete",
                )
                self.assertEqual(transition.route.value, route)
                self.assertFalse(transition.milestone_complete)

    def test_out_of_vocabulary_explicit_status_routes_invalid(self):
        for label, explicit in (
            ("unknown", "complete"),
            ("uppercase", "MILESTONE_COMPLETE"),
            ("verdict value", "pass"),
            ("objective value", "achieved"),
            ("enum of another dimension", ObjectiveStatus.ACHIEVED),
            ("integer", 1),
        ):
            with self.subTest(case=label):
                transition = compute_frontier_transition(
                    _closure("pass", "achieved"), is_last_slice=True, explicit_routing_status=explicit
                )
                self.assertEqual(transition.route, FrutlupsRoute.INVALID)
                self.assertFalse(transition.milestone_complete)
                self.assertEqual(transition.reason, "explicit_routing_status_invalid")
                self.assertIsNone(transition.receipt)


class AbsentOrRefusedEvidenceTests(unittest.TestCase):
    def test_absent_evidence_never_completes_even_on_the_last_slice(self):
        for last in (True, False):
            with self.subTest(last=last):
                transition = compute_frontier_transition(None, is_last_slice=last)
                self.assertEqual(
                    transition,
                    FrontierTransition(FrutlupsRoute.INVALID, False, "closure_evidence_absent", None),
                )

    def test_refused_closure_routes_invalid_with_its_codes(self):
        cases = [
            ("review_report_closure_missing.md", "closure_refused:closure_section_missing"),
            ("review_report_verdict_missing.md", "closure_refused:verdict_section_missing"),
            ("review_report_closure_after_verdict.md", "closure_refused:closure_after_verdict"),
            ("review_report_status_invalid.md", "closure_refused:objective_status_invalid"),
            ("review_report_verdict_footer_invalid.md", "closure_refused:verdict_footer_invalid"),
            (
                "review_report_closure_missing_evidence_line.md",
                "closure_refused:closure_line_count,objective_evidence_line_missing",
            ),
        ]
        for name, reason in cases:
            with self.subTest(fixture=name):
                transition = compute_frontier_transition(
                    parse_closure_decision_file(_FIXTURE_DIR / name), is_last_slice=True
                )
                self.assertEqual(transition.route, FrutlupsRoute.INVALID)
                self.assertFalse(transition.milestone_complete)
                self.assertEqual(transition.reason, reason)
                self.assertIsNone(transition.receipt)

    def test_released_valid_fixture_is_a_legal_non_completing_receipt(self):
        """pass + not_achieved on the last slice: legal, never completion."""

        parsed = parse_closure_decision_file(_FIXTURE_DIR / "review_report_closure_valid.md")
        transition = compute_frontier_transition(parsed, is_last_slice=True)
        self.assertEqual(transition.route, FrutlupsRoute.HUMAN_OVERRIDE_REQUIRED)
        self.assertFalse(transition.milestone_complete)
        self.assertEqual(
            transition.receipt.to_dict(),
            {"verdict": "pass", "objective_status": "not_achieved", "route": "human_override_required"},
        )

    def test_malformed_inputs_route_invalid(self):
        for label, closure, reason in (
            ("non-result object", object(), "closure_incoherent:type"),
            ("text instead of result", "## Closure Decision", "closure_incoherent:type"),
            ("non-string report text", parse_closure_decision_text(42), "closure_refused:report_unreadable"),
        ):
            with self.subTest(case=label):
                transition = compute_frontier_transition(closure, is_last_slice=True)
                self.assertEqual(transition.route, FrutlupsRoute.INVALID)
                self.assertFalse(transition.milestone_complete)
                self.assertEqual(transition.reason, reason)
                self.assertIsNone(transition.receipt)

    def test_to_dict_serialises_route_and_receipt(self):
        transition = compute_frontier_transition(_closure("pass", "achieved"), is_last_slice=True)
        self.assertEqual(
            transition.to_dict(),
            {
                "route": "milestone_complete",
                "milestone_complete": True,
                "reason": "accepted_achieved_last_slice",
                "receipt": {"verdict": "pass", "objective_status": "achieved", "route": "milestone_complete"},
            },
        )


class _Foreign:
    """A non-ClosureParseResult object carrying every coherent-looking field."""

    valid = True
    reason_codes = ()
    verdict = ReviewVerdict.PASS
    objective_status = ObjectiveStatus.ACHIEVED
    objective_evidence = "cited artifact"
    next_move = "one move"


def _result(**overrides) -> ClosureParseResult:
    """A coherent accepted `achieved` result, mutated by hand per row.

    Built with ``object.__new__`` plus ``__dict__`` so contradictory field
    states (the public constructible surface under test) are set literally
    without any product predicate deciding what is admissible.
    """

    fields = {
        "valid": True,
        "verdict": ReviewVerdict.PASS,
        "objective_status": ObjectiveStatus.ACHIEVED,
        "objective_evidence": "cited artifact",
        "next_move": "one move",
        "reason_codes": (),
    }
    fields.update(overrides)
    instance = object.__new__(ClosureParseResult)
    object.__setattr__(instance, "__dict__", fields)
    return instance


def _missing(field_name: str) -> ClosureParseResult:
    instance = _result()
    del instance.__dict__[field_name]
    return instance


# M005-R1-F1 coherence matrix: (label, closure, expected reason). Every row
# is written by hand; expected reasons are not generated from the product.
_INCOHERENT_ROWS = [
    # the three exact round-1 falsifiers (accepted achieved, last slice)
    ("R1 falsifier: valid=True, empty objective evidence", _result(objective_evidence=""), "closure_incoherent:objective_evidence"),
    ("R1 falsifier: valid=True, empty next move", _result(next_move=""), "closure_incoherent:next_move"),
    ("R1 falsifier: valid=True, refusal code verdict_footer_invalid", _result(reason_codes=("verdict_footer_invalid",)), "closure_incoherent:reason_codes"),
    # valid flag states
    ("valid=False with empty codes", _result(valid=False), "closure_incoherent:valid"),
    ("valid=1 (truthy non-boolean)", _result(valid=1), "closure_incoherent:valid"),
    ("valid='True' (truthy string)", _result(valid="True"), "closure_incoherent:valid"),
    ("valid=None", _result(valid=None), "closure_incoherent:valid"),
    ("valid missing", _missing("valid"), "closure_incoherent:valid"),
    # reason-code states
    ("valid=True with two codes", _result(reason_codes=("closure_line_count", "objective_evidence_line_missing")), "closure_incoherent:reason_codes"),
    ("valid=True with a non-contract code", _result(reason_codes=("made_up",)), "closure_incoherent:reason_codes"),
    ("valid=False with a non-contract code", _result(valid=False, reason_codes=("made_up",)), "closure_incoherent:reason_codes"),
    ("valid=False with an empty-string code", _result(valid=False, reason_codes=("",)), "closure_incoherent:reason_codes"),
    ("reason_codes as list", _result(reason_codes=["verdict_footer_invalid"]), "closure_incoherent:reason_codes"),
    ("reason_codes as string", _result(reason_codes="verdict_footer_invalid"), "closure_incoherent:reason_codes"),
    ("reason_codes None", _result(reason_codes=None), "closure_incoherent:reason_codes"),
    ("reason_codes missing", _missing("reason_codes"), "closure_incoherent:reason_codes"),
    # verdict states
    ("verdict None", _result(verdict=None), "closure_incoherent:verdict"),
    ("verdict foreign string 'pass'", _result(verdict="pass"), "closure_incoherent:verdict"),
    ("verdict objective enum", _result(verdict=ObjectiveStatus.ACHIEVED), "closure_incoherent:verdict"),
    ("verdict missing", _missing("verdict"), "closure_incoherent:verdict"),
    # objective states
    ("objective None", _result(objective_status=None), "closure_incoherent:objective_status"),
    ("objective foreign string 'achieved'", _result(objective_status="achieved"), "closure_incoherent:objective_status"),
    ("objective verdict enum", _result(objective_status=ReviewVerdict.PASS), "closure_incoherent:objective_status"),
    ("objective missing", _missing("objective_status"), "closure_incoherent:objective_status"),
    # evidence states
    ("evidence whitespace", _result(objective_evidence="   \t"), "closure_incoherent:objective_evidence"),
    ("evidence None", _result(objective_evidence=None), "closure_incoherent:objective_evidence"),
    ("evidence bytes", _result(objective_evidence=b"cited"), "closure_incoherent:objective_evidence"),
    ("evidence missing", _missing("objective_evidence"), "closure_incoherent:objective_evidence"),
    # next-move states
    ("next move whitespace", _result(next_move=" \n"), "closure_incoherent:next_move"),
    ("next move None", _result(next_move=None), "closure_incoherent:next_move"),
    ("next move integer", _result(next_move=7), "closure_incoherent:next_move"),
    ("next move missing", _missing("next_move"), "closure_incoherent:next_move"),
    # foreign objects
    ("foreign object with coherent-looking fields", _Foreign(), "closure_incoherent:type"),
    ("plain dict of fields", dict(_result().__dict__), "closure_incoherent:type"),
    ("string", "valid", "closure_incoherent:type"),
]


class PublicResultCoherenceTests(unittest.TestCase):
    """M005-R1-F1: admission fails closed before route selection or receipt building."""

    def _spied(self, closure, *, is_last_slice=True, explicit=None):
        with (
            mock.patch.object(frontier_module, "_route_for", wraps=frontier_module._route_for) as route_spy,
            mock.patch.object(
                frontier_module, "build_closure_receipt", wraps=frontier_module.build_closure_receipt
            ) as receipt_spy,
        ):
            transition = compute_frontier_transition(
                closure, is_last_slice=is_last_slice, explicit_routing_status=explicit
            )
        return transition, route_spy.call_count, receipt_spy.call_count

    def test_the_three_round1_falsifiers_never_complete(self):
        for label, closure, reason in _INCOHERENT_ROWS[:3]:
            with self.subTest(case=label):
                self.assertTrue(label.startswith("R1 falsifier"))
                transition, routes, receipts = self._spied(closure, is_last_slice=True)
                self.assertEqual(transition.route, FrutlupsRoute.INVALID)
                self.assertFalse(transition.milestone_complete)
                self.assertIsNone(transition.receipt)
                self.assertEqual(transition.reason, reason)
                self.assertEqual((routes, receipts), (0, 0))

    def test_every_incoherent_row_routes_invalid_with_zero_route_or_receipt_calls(self):
        self.assertEqual(len(_INCOHERENT_ROWS), 35)
        for label, closure, reason in _INCOHERENT_ROWS:
            for last in (True, False):
                with self.subTest(case=label, last=last):
                    transition, routes, receipts = self._spied(
                        closure, is_last_slice=last, explicit="milestone_complete"
                    )
                    self.assertEqual(
                        transition,
                        FrontierTransition(FrutlupsRoute.INVALID, False, reason, None),
                    )
                    self.assertEqual((routes, receipts), (0, 0))

    def test_incoherent_mutation_of_the_explicit_status_still_checks_coherence_first(self):
        transition, routes, receipts = self._spied(_result(next_move=""), explicit="bogus")
        self.assertEqual(transition.reason, "closure_incoherent:next_move")
        self.assertEqual((routes, receipts), (0, 0))

    def test_parser_refusals_keep_bounded_refusal_evidence_and_zero_calls(self):
        cases = [
            ("released missing closure fixture", parse_closure_decision_file(_FIXTURE_DIR / "review_report_closure_missing.md"), "closure_refused:closure_section_missing"),
            ("hand-built two-code refusal", _result(valid=False, verdict=None, objective_status=None, objective_evidence="", next_move="", reason_codes=("closure_line_count", "objective_evidence_line_missing")), "closure_refused:closure_line_count,objective_evidence_line_missing"),
            ("unreadable file refusal", parse_closure_decision_file(_FIXTURE_DIR / "absent.md"), "closure_refused:report_unreadable"),
        ]
        for label, closure, reason in cases:
            with self.subTest(case=label):
                transition, routes, receipts = self._spied(closure)
                self.assertEqual(transition, FrontierTransition(FrutlupsRoute.INVALID, False, reason, None))
                self.assertEqual((routes, receipts), (0, 0))

    def test_coherent_controls_reach_their_reviewed_branches(self):
        controls = [
            ("hand-built accepted achieved last", _result(), True, None, "milestone_complete", True, "accepted_achieved_last_slice"),
            ("hand-built accepted achieved not last", _result(), False, None, "advance_to_next_slice", False, "accepted_achieved_advances"),
            ("hand-built override indeterminate", _result(verdict=ReviewVerdict.OVERRIDE, objective_status=ObjectiveStatus.INDETERMINATE), True, None, "human_override_required", False, "accepted_indeterminate_requires_human_routing"),
            ("hand-built pass not_applicable explicit complete", _result(objective_status=ObjectiveStatus.NOT_APPLICABLE), False, "milestone_complete", "milestone_complete", True, "accepted_not_applicable_explicit_milestone_complete"),
            ("hand-built needs_work", _result(verdict=ReviewVerdict.NEEDS_WORK), True, None, "recode_same_slice", False, "needs_work_recodes_same_slice"),
            ("hand-built blocked", _result(verdict=ReviewVerdict.BLOCKED), True, None, "unblock_same_slice", False, "blocked_unblocks_same_slice"),
            ("evidence and move with surrounding whitespace", _result(objective_evidence="  e  ", next_move="\tm "), True, None, "milestone_complete", True, "accepted_achieved_last_slice"),
            ("parser-emitted text result", parse_closure_decision_text(_report("pass", "achieved")), True, None, "milestone_complete", True, "accepted_achieved_last_slice"),
            ("released valid fixture", parse_closure_decision_file(_FIXTURE_DIR / "review_report_closure_valid.md"), True, None, "human_override_required", False, "accepted_not_achieved_requires_human_routing"),
        ]
        for label, closure, last, explicit, route, complete, reason in controls:
            with self.subTest(case=label):
                transition, routes, receipts = self._spied(closure, is_last_slice=last, explicit=explicit)
                self.assertEqual(transition.route.value, route)
                self.assertEqual(transition.milestone_complete, complete)
                self.assertEqual(transition.reason, reason)
                self.assertEqual(transition.receipt.route.value, route)
                self.assertEqual((routes, receipts), (1, 1))


if __name__ == "__main__":
    unittest.main()
