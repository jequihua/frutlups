"""Tests for M003-S06: the versioned planning-frontier output (Decision 6).

The ten deferred planning-frontier probe families of the accepted M001
contract (02_analysis authority contract §8.2 rows 8-17) are kept separately
identifiable, one test class per family:

1.  ``OutcomeBehaviorBindingTests``     — every outcome maps to exactly one behavior
2.  ``UnsupportedVersionRefusalTests``  — unsupported contract version refused
3.  ``UnknownOutcomeRefusalTests``      — unknown outcome value refused
4.  ``InvalidAmbiguousContradictoryTests`` — invalid/ambiguous roadmap and
    contradictory durable state, each refused separately
5.  ``EmptyFrontierVersusClosureTests`` — empty work never completes; explicit
    accepted closure evidence does
6.  ``ArchitectDispatchTests``          — exactly one bounded dispatch, then
    fresh durable recomputation
7.  ``BlockedCitationOwnerTests``       — blocked carries citation plus owner;
    partial blocks are invalid
8.  ``RetryAndProgressStopTests``       — retry exhaustion and no-progress stop
9.  ``OptionalRegisterInertnessTests``  — optional roadmap registers stay inert
10. ``ProfileMutationAuthorityTests``   — OKF/profile-shaped input grants nothing

Supporting classes pin the contract shape, the total nine-step mapping, gate
independence, and the read-only status surface.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import frutlups
from frutlups.gate import (
    PlanningFrontierDecision,
    build_human_gate,
    build_planning_frontier_status,
    decide_planning_frontier_step,
)
from frutlups.journal import journal_path_for, read_run_journal
from frutlups.layout import legacy_profile
from frutlups.project import (
    PLANNING_FRONTIER_CONTRACT_ID,
    PLANNING_FRONTIER_CONTRACT_VERSION,
    PLANNING_FRONTIER_SUPPORTED_VERSIONS,
    LoopResumeStatus,
    LoopResumeStep,
    PlanningFrontierOutcome,
    PlanningFrontierStatus,
    _AcceptanceEvidence,
    _compute_planning_frontier,
    build_status,
)
from frutlups.review_report import ReviewVerdict

from test_orchestrator import _make_template
from test_resumable_status import (
    _active_roadmap,
    _detailed_roadmap,
    _minimal_coding_prompt,
    _write_active_roadmap,
    _write_coding_prompt,
    _write_detailed_roadmap,
    _write_review_report,
    _write_self_report,
    _write_verdict_record,
)
from test_runner_policy import _run, _snapshot

_FRONTIER_KEYS = {
    "contract_id",
    "contract_version",
    "outcome",
    "action",
    "actor",
    "block_citation",
    "block_owner",
    "completion_evidence",
    "diagnostics",
}


def _fresh_project(root: Path) -> None:
    """A valid project whose frontier slice M001-S01 has no coding prompt."""

    _make_template(root)
    _write_active_roadmap(root, _active_roadmap())
    _write_detailed_roadmap(root, _detailed_roadmap())


def _accept_slice(root: Path, slice_id: str, stem: str) -> None:
    """Accept ``slice_id`` with a Source-only receipted pass report.

    The record carries only the ``## Source`` citation — deliberately NOT the
    generated closure fields — so ordinary bookkeeping can never look like an
    accepted closure receipt (Review 030 finding 2).
    """

    report = f"{stem}_review_report.md"
    _write_review_report(root, report, "pass")
    _write_verdict_record(root, f"{stem}_verdict_record.md", report)


def _record_via_real_writer(root: Path, report_rel: str) -> str:
    """Write the verdict record through the real plan/write path; return rel path."""

    from frutlups.layout import legacy_profile
    from frutlups.project import (
        VerdictRecordWriteCommand,
        _build_verdict_record_plan_from_profile,
        write_verdict_record,
    )

    status = build_status(root)
    profile = status.layout.profile if status.layout is not None else legacy_profile()
    plan = _build_verdict_record_plan_from_profile(
        root, profile, root / report_rel
    )
    assert plan.valid, plan.errors
    result = write_verdict_record(
        VerdictRecordWriteCommand(project_root=root, plan=plan, overwrite=False)
    )
    assert result.wrote, result.errors
    return plan.target_path.replace("\\", "/")


def _completed_project(root: Path, *, closure: bool = False) -> None:
    """All roadmap slices accepted and receipted; optional accepted closure.

    With ``closure=True`` the terminal slice's receipt is produced through
    the real ``build_verdict_record_plan``/``write_verdict_record`` path, so
    it carries the generated ``## Slice`` / ``## Parsed Verdict`` /
    ``## Next Action`` closure fields (``milestone_complete``, no next
    slice). With ``closure=False`` both receipts are Source-only bookkeeping
    and no accepted closure evidence exists.
    """

    _fresh_project(root)
    _accept_slice(root, "M001-S01", "m001_s01_first_slice")
    _write_review_report(root, "m001_s02_second_slice_review_report.md", "pass")
    if closure:
        _record_via_real_writer(
            root, "05_governance/reviews/m001_s02_second_slice_review_report.md"
        )
    else:
        _write_verdict_record(
            root,
            "m001_s02_second_slice_verdict_record.md",
            "m001_s02_second_slice_review_report.md",
        )


_CLOSURE_RECORD = "05_governance/reviews/m001_s02_second_slice_verdict_record.md"


def _frontier(root: Path) -> PlanningFrontierStatus:
    return build_planning_frontier_status(root)


def _forged(
    *,
    contract_id: str = PLANNING_FRONTIER_CONTRACT_ID,
    version: str = PLANNING_FRONTIER_CONTRACT_VERSION,
    outcome: str = "ready",
    action: str = "",
    actor: str = "",
    citation: str = "",
    owner: str = "",
    evidence: str = "",
) -> PlanningFrontierStatus:
    return PlanningFrontierStatus(
        contract_id=contract_id,
        contract_version=version,
        outcome=outcome,
        action=action,
        actor=actor,
        block_citation=citation,
        block_owner=owner,
        completion_evidence=evidence,
        diagnostics=(),
    )


def _raising_dispatch(_frontier_value: PlanningFrontierStatus) -> None:
    raise AssertionError("architect dispatch must be unreachable")


class ContractShapeTests(unittest.TestCase):
    """The emitted contract identifier, version, key set, and value safety."""

    def test_contract_identity_and_key_set(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fresh_project(root)
            frontier = _frontier(root)
            self.assertEqual(frontier.contract_id, "frutlups.planning_frontier")
            self.assertEqual(frontier.contract_version, "1")
            self.assertIn("1", PLANNING_FRONTIER_SUPPORTED_VERSIONS)
            payload = frontier.to_dict()
            self.assertEqual(set(payload), _FRONTIER_KEYS)
            json.dumps(payload)

    def test_deterministic_and_no_machine_local_value(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root, closure=True)
            first = _frontier(root)
            second = _frontier(root)
            self.assertEqual(first, second)
            for value in first.to_dict().values():
                for text in value if isinstance(value, list) else [value]:
                    if isinstance(text, str):
                        self.assertNotIn(tmp, text)
                        self.assertNotIn("\\", text)

    def test_only_outcome_required_fields_are_populated(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fresh_project(root)
            frontier = _frontier(root)
            self.assertEqual(frontier.outcome, PlanningFrontierOutcome.READY.value)
            self.assertEqual(frontier.action, "")
            self.assertEqual(frontier.actor, "")
            self.assertEqual(frontier.block_citation, "")
            self.assertEqual(frontier.block_owner, "")
            self.assertEqual(frontier.completion_evidence, "")


class NineStepTotalMappingTests(unittest.TestCase):
    """Decision 6 resolution 2: all nine loop-resume steps map totally."""

    _EXPECTED = {
        LoopResumeStep.NO_FRONTIER: PlanningFrontierOutcome.NEEDS_SPECIFICATION,
        LoopResumeStep.MAKE_CODING_PROMPT: PlanningFrontierOutcome.READY,
        LoopResumeStep.EXECUTE_CODING_PROMPT: PlanningFrontierOutcome.READY,
        LoopResumeStep.FIX_SELF_REPORT: PlanningFrontierOutcome.READY,
        LoopResumeStep.MAKE_REVIEW_PROMPT: PlanningFrontierOutcome.READY,
        LoopResumeStep.EXECUTE_REVIEW_PROMPT: PlanningFrontierOutcome.READY,
        LoopResumeStep.FIX_REVIEW_REPORT: PlanningFrontierOutcome.READY,
        LoopResumeStep.RECORD_VERDICT: PlanningFrontierOutcome.READY,
        LoopResumeStep.FRONTIER_RECORDED: PlanningFrontierOutcome.READY,
    }

    def _resume_for(self, step: LoopResumeStep) -> LoopResumeStatus:
        return LoopResumeStatus(
            step=step,
            message="",
            next_command="",
            frontier_slice_id="M001-S01",
            frontier_slice_title="first slice",
            coding_prompt_path="",
            self_report_path="",
            review_prompt_path="",
            review_report_path="05_governance/reviews/m001_s01_x_review_report.md",
            verdict_record_path="",
            diagnostics=(),
        )

    def test_every_step_maps_to_exactly_one_outcome(self) -> None:
        self.assertEqual(len(self._EXPECTED), len(LoopResumeStep))
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fresh_project(root)
            status = build_status(root)
            empty = _AcceptanceEvidence((), (), (), ())
            for step, expected in self._EXPECTED.items():
                with self.subTest(step=step.value):
                    frontier = _compute_planning_frontier(
                        status, self._resume_for(step), None, empty
                    )
                    self.assertEqual(frontier.outcome, expected.value)

    def test_gate_open_stop_final_handoff_never_rewrite_outcomes(self) -> None:
        # Decision 6 resolution 2: OPEN/STOP/FINAL_HANDOFF are independent
        # human gates; they never select or change a frontier outcome.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fresh_project(root)
            status = build_status(root)
            empty = _AcceptanceEvidence((), (), (), ())
            resume = self._resume_for(LoopResumeStep.MAKE_CODING_PROMPT)
            outcomes = {
                _compute_planning_frontier(
                    status, resume, None, empty, gate_state=gate
                ).outcome
                for gate in ("open", "stop", "final_handoff", "no_frontier", "")
            }
            self.assertEqual(outcomes, {PlanningFrontierOutcome.READY.value})


class OutcomeBehaviorBindingTests(unittest.TestCase):
    """Probe family 1: every accepted outcome maps to exactly one behavior."""

    def test_ready_continues_declared_loop_only(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fresh_project(root)
            before = _snapshot(root)
            decision = decide_planning_frontier_step(root)
            self.assertEqual(decision.frontier.outcome, "ready")
            self.assertEqual(decision.behavior, "continue_declared_loop")
            self.assertFalse(decision.dispatched)
            self.assertFalse(decision.success)
            self.assertEqual(_snapshot(root), before)

    def test_needs_specification_binds_to_architect_dispatch(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root)
            decision = decide_planning_frontier_step(root)
            self.assertEqual(decision.frontier.outcome, "needs_specification")
            self.assertEqual(decision.behavior, "dispatch_architect_and_recompute")
            self.assertFalse(decision.success)

    def test_blocked_binds_to_stop_blocked(self) -> None:
        decision = decide_planning_frontier_step(
            frontier=_forged(
                outcome="blocked",
                citation="05_governance/reviews/m001_s01_x_review_report.md",
                owner="human",
            )
        )
        self.assertEqual(decision.behavior, "stop_blocked")
        self.assertFalse(decision.success)

    def test_complete_binds_to_stop_complete_success(self) -> None:
        # Internally built from a real generated closure receipt (Prompt 031:
        # a legitimate internally built complete is not recomputed).
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root, closure=True)
            decision = decide_planning_frontier_step(root)
            self.assertEqual(decision.frontier.outcome, "complete")
            self.assertEqual(decision.behavior, "stop_complete")
            self.assertTrue(decision.success)

    def test_injected_complete_succeeds_only_when_durable_state_matches(self) -> None:
        # Prompt 031: a directly injected complete value is untrusted and is
        # checked once against the current durable frontier.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root, closure=True)
            matching = decide_planning_frontier_step(
                root, frontier=_forged(outcome="complete", evidence=_CLOSURE_RECORD)
            )
            self.assertEqual(matching.behavior, "stop_complete")
            self.assertTrue(matching.success)
            wrong_identity = decide_planning_frontier_step(
                root,
                frontier=_forged(
                    outcome="complete",
                    evidence="05_governance/reviews/m001_s01_first_slice_verdict_record.md",
                ),
            )
            self.assertEqual(wrong_identity.behavior, "stop_fail_closed")
            self.assertFalse(wrong_identity.success)

    def test_injected_complete_against_incomplete_state_fails_closed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root)  # no accepted closure receipt
            decision = decide_planning_frontier_step(
                root, frontier=_forged(outcome="complete", evidence=_CLOSURE_RECORD)
            )
            self.assertEqual(decision.behavior, "stop_fail_closed")
            self.assertFalse(decision.success)

    def test_invalid_binds_to_stop_fail_closed(self) -> None:
        decision = decide_planning_frontier_step(frontier=_forged(outcome="invalid"))
        self.assertEqual(decision.behavior, "stop_fail_closed")
        self.assertFalse(decision.success)

    def test_each_outcome_has_exactly_one_behavior(self) -> None:
        from frutlups.project import _ARCHITECT_FRONTIER_ACTION

        behaviors = {}
        for outcome, kwargs in (
            ("ready", {}),
            (
                "needs_specification",
                {"action": _ARCHITECT_FRONTIER_ACTION, "actor": "architect"},
            ),
            ("blocked", {"citation": "05_governance/reviews/x_review_report.md", "owner": "human"}),
            ("invalid", {}),
        ):
            decision = decide_planning_frontier_step(
                frontier=_forged(outcome=outcome, **kwargs)
            )
            behaviors[outcome] = decision.behavior
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root, closure=True)
            behaviors["complete"] = decide_planning_frontier_step(root).behavior
        self.assertEqual(
            behaviors,
            {
                "ready": "continue_declared_loop",
                "needs_specification": "dispatch_architect_and_recompute",
                "blocked": "stop_blocked",
                "complete": "stop_complete",
                "invalid": "stop_fail_closed",
            },
        )
        self.assertEqual(len(set(behaviors.values())), 5)


class UnsupportedVersionRefusalTests(unittest.TestCase):
    """Probe family 2: an unsupported contract version is refused fail-closed."""

    def test_newer_version_refused_naming_observed_and_supported(self) -> None:
        for version in ("2", "0", "", "1.1"):
            with self.subTest(version=version):
                decision = decide_planning_frontier_step(
                    frontier=_forged(version=version, outcome="ready"),
                    architect_dispatch=_raising_dispatch,
                )
                self.assertEqual(decision.behavior, "stop_fail_closed")
                self.assertFalse(decision.dispatched)
                self.assertFalse(decision.success)
                joined = " ".join(decision.diagnostics)
                self.assertIn("unsupported planning-frontier contract version", joined)
                self.assertIn(version or "(empty)", joined)
                self.assertIn("1", joined)
                self.assertLessEqual(max(len(d) for d in decision.diagnostics), 240)

    def test_unknown_contract_identifier_refused(self) -> None:
        decision = decide_planning_frontier_step(
            frontier=_forged(contract_id="other.contract", outcome="ready"),
            architect_dispatch=_raising_dispatch,
        )
        self.assertEqual(decision.behavior, "stop_fail_closed")
        self.assertFalse(decision.dispatched)

    def test_consumer_pinned_to_other_version_refuses_current(self) -> None:
        # The asymmetric direction: a consumer that does not implement the
        # emitted version refuses rather than best-effort interpreting.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fresh_project(root)
            before = _snapshot(root)
            decision = decide_planning_frontier_step(
                root, supported_versions=("2",), architect_dispatch=_raising_dispatch
            )
            self.assertEqual(decision.behavior, "stop_fail_closed")
            self.assertEqual(_snapshot(root), before)
            self.assertEqual(read_run_journal(journal_path_for(root)).entries, ())

    def test_refusal_writes_nothing_and_does_not_advance(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root)
            before = _snapshot(root)
            decision = decide_planning_frontier_step(
                root,
                frontier=_forged(version="9", outcome="needs_specification"),
                architect_dispatch=_raising_dispatch,
            )
            self.assertEqual(decision.behavior, "stop_fail_closed")
            self.assertIsNone(decision.recomputed)
            self.assertEqual(_snapshot(root), before)


class UnknownOutcomeRefusalTests(unittest.TestCase):
    """Probe family 3: an unknown outcome value is refused, never defaulted."""

    def test_unknown_outcomes_stop_fail_closed(self) -> None:
        for outcome in ("done", "success", "READY ", "Complete", "", "ready2"):
            with self.subTest(outcome=outcome):
                decision = decide_planning_frontier_step(
                    frontier=_forged(outcome=outcome),
                    architect_dispatch=_raising_dispatch,
                )
                self.assertEqual(decision.behavior, "stop_fail_closed")
                self.assertFalse(decision.success)
                self.assertFalse(decision.dispatched)

    def test_unknown_outcome_never_reports_ready_or_complete(self) -> None:
        decision = decide_planning_frontier_step(frontier=_forged(outcome="finished"))
        self.assertNotIn(decision.behavior, ("continue_declared_loop", "stop_complete"))
        joined = " ".join(decision.diagnostics)
        self.assertIn("unknown planning-frontier outcome", joined)


class InvalidAmbiguousContradictoryTests(unittest.TestCase):
    """Probe family 4: invalid roadmap, ambiguous roadmap, and contradictory
    durable state each fail closed individually."""

    def test_missing_active_roadmap_is_invalid(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            frontier = _frontier(root)
            self.assertEqual(frontier.outcome, "invalid")
            self.assertTrue(
                any("no_active_roadmap" in diag for diag in frontier.diagnostics)
            )

    def test_unparseable_roadmap_is_invalid(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            _write_active_roadmap(root, "just prose, no milestone headings\n")
            frontier = _frontier(root)
            self.assertEqual(frontier.outcome, "invalid")
            self.assertTrue(
                any("no_milestones_parsed" in diag for diag in frontier.diagnostics)
            )

    def test_ambiguous_roadmap_selection_is_invalid(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fresh_project(root)
            (root / "03_experiments" / "active_roadmap_second.md").write_text(
                _active_roadmap(), encoding="utf-8"
            )
            frontier = _frontier(root)
            self.assertEqual(frontier.outcome, "invalid")
            self.assertTrue(
                any("ambiguous roadmap selection" in diag for diag in frontier.diagnostics)
            )

    def test_contradictory_durable_state_is_invalid(self) -> None:
        # Decision 5 direction 3: a verdict record with no accepting report.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fresh_project(root)
            _write_verdict_record(
                root,
                "m001_s01_first_slice_verdict_record.md",
                "m001_s01_first_slice_review_report.md",
            )
            frontier = _frontier(root)
            self.assertEqual(frontier.outcome, "invalid")
            self.assertTrue(
                any("contradictory durable state" in diag for diag in frontier.diagnostics)
            )

    def test_authority_defect_is_invalid_separately(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fresh_project(root)
            _write_review_report(root, "m001_s01_first_slice_review_report.md", "pass")
            import frutlups.project as project_module

            real_is_within = project_module._is_within

            def _escape_report(child: Path, parent: Path) -> bool:
                if child.name == "m001_s01_first_slice_review_report.md":
                    return False
                return real_is_within(child, parent)

            with mock.patch.object(
                project_module, "_is_within", side_effect=_escape_report
            ):
                frontier = _frontier(root)
            self.assertEqual(frontier.outcome, "invalid")
            self.assertTrue(
                any("acceptance authority defect" in diag for diag in frontier.diagnostics)
            )

    def test_each_condition_is_named_not_merged(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            frontier = _frontier(root)
            self.assertNotIn("ambiguous", " ".join(frontier.diagnostics))
            self.assertNotIn("contradictory", " ".join(frontier.diagnostics))


class EmptyFrontierVersusClosureTests(unittest.TestCase):
    """Probe family 5: empty work never completes; only explicit accepted
    closure evidence produced by the Decision 5 authority path does."""

    def test_no_frontier_without_closure_is_needs_specification(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root)
            frontier = _frontier(root)
            self.assertEqual(frontier.outcome, "needs_specification")
            self.assertEqual(frontier.actor, "architect")
            self.assertNotEqual(frontier.action, "")
            self.assertEqual(frontier.completion_evidence, "")
            # Resolution 7: the native no_frontier observation is preserved.
            self.assertTrue(
                any("no_frontier" in diag for diag in frontier.diagnostics)
            )

    def test_real_generated_closure_receipt_is_complete(self) -> None:
        # The positive path uses the real build_verdict_record_plan /
        # write_verdict_record writer; the emitted evidence is the record —
        # the accepted closure receipt — never a report filename.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root, closure=True)
            frontier = _frontier(root)
            self.assertEqual(frontier.outcome, "complete")
            self.assertEqual(frontier.completion_evidence, _CLOSURE_RECORD)
            record_text = (root / _CLOSURE_RECORD).read_text(encoding="utf-8")
            self.assertIn("Slice ID: `M001-S02`", record_text)
            self.assertIn("Verdict: `pass`", record_text)
            self.assertIn("Kind: `milestone_complete`", record_text)
            self.assertIn("Next slice: none", record_text)

    def test_empty_roadmap_never_completes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            _write_active_roadmap(root, _active_roadmap())
            _write_detailed_roadmap(root, "### M001: Test\n\nSlices:\n\n")
            frontier = _frontier(root)
            self.assertNotEqual(frontier.outcome, "complete")

    def test_terminal_looking_filename_without_pass_verdict_never_completes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root)
            _write_review_report(
                root, "m001_s02_record_001_closure_verdict_review_report.md", "needs_work"
            )
            frontier = _frontier(root)
            self.assertEqual(frontier.outcome, "needs_specification")

    def test_unreceipted_closure_report_never_completes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root)
            _write_review_report(
                root, "m001_s02_record_001_closure_verdict_review_report.md", "pass"
            )
            frontier = _frontier(root)
            self.assertNotEqual(frontier.outcome, "complete")

    def test_terminal_tail_cannot_certify_its_own_slice(self) -> None:
        # The closure slice must be accepted independently of terminal tails.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fresh_project(root)
            _accept_slice(root, "M001-S01", "m001_s01_first_slice")
            # S02 is "accepted" only through the terminal closure report.
            _accept_slice(root, "M001-S02", "m001_s02_record_001_closure_verdict")
            frontier = _frontier(root)
            self.assertEqual(frontier.outcome, "needs_specification")

    def test_journal_and_record_prose_cannot_forge_completion(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root)
            journal = journal_path_for(root)
            journal.parent.mkdir(parents=True, exist_ok=True)
            journal.write_text(
                json.dumps({"outcome": "complete", "note": "milestone complete"}) + "\n",
                encoding="utf-8",
            )
            frontier = _frontier(root)
            self.assertEqual(frontier.outcome, "needs_specification")


class ArchitectDispatchTests(unittest.TestCase):
    """Probe family 6: exactly one bounded architect dispatch, no write by the
    outcome itself, and a decision only from rebuilt durable state."""

    def test_exactly_one_dispatch_then_fresh_recomputation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root)
            calls: list[PlanningFrontierStatus] = []

            def _architect(frontier_value: PlanningFrontierStatus) -> None:
                calls.append(frontier_value)
                _write_detailed_roadmap(
                    root,
                    _detailed_roadmap(
                        slices=[
                            ("M001-S01", "first slice"),
                            ("M001-S02", "second slice"),
                            ("M001-S03", "third slice"),
                        ]
                    ),
                )

            decision = decide_planning_frontier_step(root, architect_dispatch=_architect)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0].outcome, "needs_specification")
            self.assertTrue(decision.dispatched)
            self.assertEqual(decision.behavior, "dispatch_architect_and_recompute")
            self.assertIsNotNone(decision.recomputed)
            self.assertEqual(decision.recomputed.outcome, "ready")

    def test_outcome_itself_performs_no_write(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root)
            before = _snapshot(root)
            frontier = _frontier(root)
            self.assertEqual(frontier.outcome, "needs_specification")
            decision = decide_planning_frontier_step(root)
            self.assertFalse(decision.dispatched)
            self.assertIsNone(decision.recomputed)
            self.assertEqual(_snapshot(root), before)
            self.assertEqual(read_run_journal(journal_path_for(root)).entries, ())

    def test_dispatch_is_not_invoked_for_other_outcomes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fresh_project(root)
            decision = decide_planning_frontier_step(
                root, architect_dispatch=_raising_dispatch
            )
            self.assertEqual(decision.behavior, "continue_declared_loop")
            self.assertFalse(decision.dispatched)

    def test_failed_dispatch_stops_fail_closed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root)

            def _failing(_frontier_value: PlanningFrontierStatus) -> None:
                raise RuntimeError("provider exploded hostile-secret-value")

            decision = decide_planning_frontier_step(root, architect_dispatch=_failing)
            self.assertEqual(decision.behavior, "stop_fail_closed")
            self.assertTrue(decision.dispatched)
            self.assertIsNone(decision.recomputed)
            joined = " ".join(decision.diagnostics)
            self.assertNotIn("secret", joined)
            self.assertNotIn("RuntimeError", joined)


class BlockedCitationOwnerTests(unittest.TestCase):
    """Probe family 7: blocked names the citation and owner; partial blocks
    are invalid."""

    def _blocked_project(self, root: Path, verdict: str) -> None:
        _fresh_project(root)
        _write_coding_prompt(
            root, "001_frutlups_m001_s01_first_slice.md", _minimal_coding_prompt(1)
        )
        _write_self_report(
            root, "05_governance/reviews/m001_s01_first_slice_self_report.md"
        )
        from test_resumable_status import _write_review_prompt

        _write_review_prompt(root, "001_review_m001_s01_first_slice.md")
        _write_review_report(root, "m001_s01_first_slice_review_report.md", verdict)

    def test_blocked_verdict_names_citation_and_owner(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._blocked_project(root, "blocked")
            frontier = _frontier(root)
            self.assertEqual(frontier.outcome, "blocked")
            self.assertEqual(
                frontier.block_citation,
                "05_governance/reviews/m001_s01_first_slice_review_report.md",
            )
            self.assertEqual(frontier.block_owner, "human")
            decision = decide_planning_frontier_step(root)
            self.assertEqual(decision.behavior, "stop_blocked")
            joined = " ".join(decision.diagnostics)
            self.assertIn(frontier.block_citation, joined)
            self.assertIn("human", joined)

    def test_override_verdict_is_a_cited_human_block(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._blocked_project(root, "override")
            frontier = _frontier(root)
            self.assertEqual(frontier.outcome, "blocked")
            self.assertEqual(frontier.block_owner, "human")

    def test_partial_block_is_invalid_at_computation(self) -> None:
        # A gate-derived block with no citation source is refused as invalid
        # rather than accepted as a partially specified block.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fresh_project(root)
            status = build_status(root)
            resume = LoopResumeStatus(
                step=LoopResumeStep.MAKE_CODING_PROMPT,
                message="",
                next_command="",
                frontier_slice_id="M001-S01",
                frontier_slice_title="",
                coding_prompt_path="",
                self_report_path="",
                review_prompt_path="",
                review_report_path="",
                verdict_record_path="",
                diagnostics=(),
            )
            frontier = _compute_planning_frontier(
                status, resume, None, _AcceptanceEvidence((), (), (), ()), gate_state="blocked"
            )
            self.assertEqual(frontier.outcome, "invalid")

    def test_partial_block_is_refused_at_the_boundary(self) -> None:
        for citation, owner in (("", "human"), ("05_governance/reviews/x.md", ""), ("", "")):
            with self.subTest(citation=citation, owner=owner):
                decision = decide_planning_frontier_step(
                    frontier=_forged(outcome="blocked", citation=citation, owner=owner)
                )
                self.assertEqual(decision.behavior, "stop_fail_closed")


class RetryAndProgressStopTests(unittest.TestCase):
    """Probe family 8: retry exhaustion and no durable progress stop the run
    and report; neither is success and neither completes."""

    def test_retry_exhaustion_stops_without_completion(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fresh_project(root)
            decision = decide_planning_frontier_step(
                root, retry_exhausted=True, architect_dispatch=_raising_dispatch
            )
            self.assertEqual(decision.behavior, "stop_retry_exhausted")
            self.assertFalse(decision.success)
            self.assertFalse(decision.dispatched)

    def test_no_progress_stops_without_completion(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fresh_project(root)
            decision = decide_planning_frontier_step(
                root, no_durable_progress=True, architect_dispatch=_raising_dispatch
            )
            self.assertEqual(decision.behavior, "stop_no_progress")
            self.assertFalse(decision.success)

    def test_retry_exhaustion_cannot_become_complete(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root, closure=True)
            decision = decide_planning_frontier_step(root, retry_exhausted=True)
            self.assertEqual(decision.behavior, "stop_retry_exhausted")
            self.assertFalse(decision.success)

    def test_both_flags_report_both_conditions(self) -> None:
        decision = decide_planning_frontier_step(
            frontier=_forged(outcome="ready"),
            retry_exhausted=True,
            no_durable_progress=True,
        )
        self.assertEqual(decision.behavior, "stop_retry_exhausted")
        joined = " ".join(decision.diagnostics)
        self.assertIn("retry budget exhausted", joined)
        self.assertIn("no durable progress", joined)


class OptionalRegisterInertnessTests(unittest.TestCase):
    """Probe family 9: optional roadmap registers are never slices, counts,
    outcomes, actors, dispatches, or writable work."""

    _REGISTERS = (
        "## Not Yet Specified\n\n"
        "- M009-S01: speculative idea that must never execute\n\n"
        "## Ruled Out\n\n"
        "- M010-S01: rejected idea\n\n"
    )

    def test_registers_change_no_slice_count_and_no_outcome(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fresh_project(root)
            plain_status = build_status(root)
            plain = _frontier(root)
            _write_active_roadmap(root, _active_roadmap() + self._REGISTERS)
            _write_detailed_roadmap(root, _detailed_roadmap() + self._REGISTERS)
            with_registers_status = build_status(root)
            with_registers = _frontier(root)
            self.assertEqual(
                [s.slice_id for s in plain_status.slices],
                [s.slice_id for s in with_registers_status.slices],
            )
            self.assertEqual(plain.outcome, with_registers.outcome)
            self.assertEqual(plain.action, with_registers.action)
            self.assertEqual(plain.actor, with_registers.actor)

    def test_register_entries_never_enter_the_frontier(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root)
            _write_detailed_roadmap(root, _detailed_roadmap() + self._REGISTERS)
            status = build_status(root)
            self.assertNotIn("M009-S01", [s.slice_id for s in status.slices])
            self.assertNotIn("M010-S01", [s.slice_id for s in status.slices])
            frontier = _frontier(root)
            self.assertEqual(frontier.outcome, "needs_specification")
            self.assertNotIn("M009-S01", frontier.action)
            self.assertNotIn("speculative", frontier.action)

    def test_registers_cannot_forge_or_block_completion(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root, closure=True)
            _write_detailed_roadmap(root, _detailed_roadmap() + self._REGISTERS)
            frontier = _frontier(root)
            self.assertEqual(frontier.outcome, "complete")

    def test_no_write_touches_registers(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root)
            _write_detailed_roadmap(root, _detailed_roadmap() + self._REGISTERS)
            before = _snapshot(root)
            decide_planning_frontier_step(root)
            self.assertEqual(_snapshot(root), before)


class ProfileMutationAuthorityTests(unittest.TestCase):
    """Probe family 10: OKF/profile-shaped input changes none of outcome,
    actor, gate, authority, completion, routing, or write permission."""

    _CONCEPT = '---\ntype: review_report\nframework_profile: "0.1-rc.1"\n---\n'

    def _decision_view(self, root: Path) -> dict[str, object]:
        frontier = _frontier(root)
        gate = build_human_gate(root)
        status = build_status(root)
        return {
            "outcome": frontier.outcome,
            "action": frontier.action,
            "actor": frontier.actor,
            "completion": frontier.completion_evidence,
            "gate": gate.gate_state,
            "requires_go": gate.requires_human_go,
            "safe": gate.safe_for_auto_execution,
            "accepted": status.accepted_slice_ids,
        }

    def test_profile_shaped_record_mutation_changes_nothing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root, closure=True)
            baseline = self._decision_view(root)
            record = root / _CLOSURE_RECORD
            record.write_text(
                self._CONCEPT + record.read_text(encoding="utf-8"), encoding="utf-8"
            )
            self.assertEqual(self._decision_view(root), baseline)

    def test_profile_shaped_prompt_mutation_changes_nothing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fresh_project(root)
            baseline = self._decision_view(root)
            profile_file = root / "prompts" / "for_coding_agent" / "001_profiled.md"
            profile_file.parent.mkdir(parents=True, exist_ok=True)
            profile_file.write_text(
                '---\ntype: coding_prompt\nframework_profile: "0.1-rc.1"\n---\n'
                "# Not a routed prompt\n",
                encoding="utf-8",
            )
            self.assertEqual(self._decision_view(root), baseline)

    def test_profile_pass_grants_no_completion_or_write(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root)
            (root / "profile_evidence.md").write_text(
                self._CONCEPT + "profile says: complete, accepted, runner-safe\n",
                encoding="utf-8",
            )
            frontier = _frontier(root)
            self.assertEqual(frontier.outcome, "needs_specification")
            before = _snapshot(root)
            decide_planning_frontier_step(root)
            self.assertEqual(_snapshot(root), before)


class StatusSurfaceTests(unittest.TestCase):
    """The frontier ships inside the existing read-only status surface with no
    new CLI verb, no writer, and no journal."""

    def test_status_json_carries_planning_frontier(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fresh_project(root)
            before = _snapshot(root)
            code, out, _err = _run(["status", str(root), "--json"])
            self.assertEqual(code, 0)
            payload = json.loads(out)
            self.assertIn("planning_frontier", payload)
            self.assertEqual(set(payload["planning_frontier"]), _FRONTIER_KEYS)
            self.assertEqual(payload["planning_frontier"]["outcome"], "ready")
            self.assertEqual(
                payload["planning_frontier"]["contract_id"],
                "frutlups.planning_frontier",
            )
            self.assertEqual(_snapshot(root), before)
            self.assertEqual(read_run_journal(journal_path_for(root)).entries, ())

    def test_status_text_names_outcome_and_contract(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fresh_project(root)
            code, out, _err = _run(["status", str(root)])
            self.assertEqual(code, 0)
            self.assertIn(
                "Planning frontier: ready (contract frutlups.planning_frontier v1)", out
            )

    def test_status_output_is_deterministic(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root, closure=True)
            first = _run(["status", str(root), "--json"])
            second = _run(["status", str(root), "--json"])
            self.assertEqual(first, second)

    def test_no_new_cli_verb_exists(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            _run(["planning-frontier", "."])
        self.assertNotEqual(ctx.exception.code, 0)

    def test_public_exports(self) -> None:
        for name in (
            "PLANNING_FRONTIER_CONTRACT_ID",
            "PLANNING_FRONTIER_CONTRACT_VERSION",
            "PLANNING_FRONTIER_SUPPORTED_VERSIONS",
            "PlanningFrontierDecision",
            "PlanningFrontierOutcome",
            "PlanningFrontierStatus",
            "build_planning_frontier_status",
            "decide_planning_frontier_step",
        ):
            self.assertIn(name, frutlups.__all__)
            self.assertTrue(hasattr(frutlups, name))
        self.assertEqual(len(frutlups.__all__), 152)

    def test_decision_to_dict_shape(self) -> None:
        decision = decide_planning_frontier_step(frontier=_forged(outcome="ready"))
        self.assertIsInstance(decision, PlanningFrontierDecision)
        payload = decision.to_dict()
        self.assertEqual(
            set(payload),
            {"frontier", "behavior", "dispatched", "recomputed", "success", "diagnostics"},
        )
        json.dumps(payload)


class FrontierShapeValidationTests(unittest.TestCase):
    """Prompt 031 (Review 030 finding 1): every malformed same-version frontier
    shape fails closed before retry flags, dispatch, continuation, blocked
    stop, or successful completion."""

    def _fail_closed(self, frontier: PlanningFrontierStatus, root: Path) -> None:
        before = _snapshot(root)
        decision = decide_planning_frontier_step(
            root,
            frontier=frontier,
            architect_dispatch=_raising_dispatch,
            retry_exhausted=True,  # validation must preempt even the retry stop
        )
        self.assertEqual(decision.behavior, "stop_fail_closed")
        self.assertFalse(decision.success)
        self.assertFalse(decision.dispatched)
        self.assertIsNone(decision.recomputed)
        self.assertIn(
            "invalid version-1 planning-frontier shape", decision.diagnostics[0]
        )
        for diag in decision.diagnostics:
            self.assertLessEqual(len(diag), 240)
        self.assertEqual(_snapshot(root), before)
        self.assertEqual(read_run_journal(journal_path_for(root)).entries, ())

    def test_malformed_needs_specification_never_dispatches(self) -> None:
        # The exact Review 030 counterexample cells.
        from frutlups.project import _ARCHITECT_FRONTIER_ACTION

        cells = (
            ("", ""),
            ("do thing", ""),
            ("do thing", "coder"),
            ("", "architect"),
            (_ARCHITECT_FRONTIER_ACTION, "coder"),
            ("do thing", "architect"),
            (_ARCHITECT_FRONTIER_ACTION, "ARCHITECT"),
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fresh_project(root)
            for action, actor in cells:
                with self.subTest(action=action[:20], actor=actor):
                    self._fail_closed(
                        _forged(
                            outcome="needs_specification", action=action, actor=actor
                        ),
                        root,
                    )

    def test_cross_outcome_field_contamination_is_invalid(self) -> None:
        from frutlups.project import _ARCHITECT_FRONTIER_ACTION

        citation = "05_governance/reviews/x_review_report.md"
        cells = (
            _forged(outcome="ready", action="do thing"),
            _forged(outcome="ready", actor="architect"),
            _forged(outcome="ready", citation=citation),
            _forged(outcome="ready", owner="human"),
            _forged(outcome="ready", evidence=citation),
            _forged(
                outcome="needs_specification",
                action=_ARCHITECT_FRONTIER_ACTION,
                actor="architect",
                citation=citation,
            ),
            _forged(
                outcome="needs_specification",
                action=_ARCHITECT_FRONTIER_ACTION,
                actor="architect",
                evidence=citation,
            ),
            _forged(outcome="blocked", citation=citation, owner="human", action="x"),
            _forged(outcome="blocked", citation=citation, owner="human", actor="a"),
            _forged(
                outcome="blocked", citation=citation, owner="human", evidence=citation
            ),
            _forged(outcome="complete", evidence=citation, action="x"),
            _forged(outcome="complete", evidence=citation, citation=citation),
            _forged(outcome="invalid", action="x"),
            _forged(outcome="invalid", evidence=citation),
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fresh_project(root)
            for frontier in cells:
                with self.subTest(
                    outcome=frontier.outcome,
                    populated=[
                        name
                        for name in (
                            "action",
                            "actor",
                            "block_citation",
                            "block_owner",
                            "completion_evidence",
                        )
                        if getattr(frontier, name)
                    ],
                ):
                    self._fail_closed(frontier, root)

    def test_unsafe_blocked_citation_and_owner_are_invalid(self) -> None:
        cells = (
            {"citation": "", "owner": "human"},
            {"citation": "05_governance/reviews/x.md", "owner": ""},
            {"citation": "C:/evil/x.md", "owner": "human"},
            {"citation": "/abs/x.md", "owner": "human"},
            {"citation": "..\\..\\x.md", "owner": "human"},
            {"citation": "a/../x.md", "owner": "human"},
            {"citation": "a" * 300 + ".md", "owner": "human"},
            {"citation": "05_governance/reviews/x.md", "owner": "coder"},
            {"citation": "05_governance/reviews/x.md", "owner": "HUMAN"},
            {"citation": "05_governance/reviews/x.md", "owner": "human or admin"},
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fresh_project(root)
            for cell in cells:
                with self.subTest(owner=cell["owner"], citation=cell["citation"][:24]):
                    self._fail_closed(_forged(outcome="blocked", **cell), root)

    def test_unsafe_completion_evidence_is_invalid(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fresh_project(root)
            for evidence in ("C:/evil/rec.md", "/abs/rec.md", "..", "a\\b.md"):
                with self.subTest(evidence=evidence):
                    self._fail_closed(_forged(outcome="complete", evidence=evidence), root)

    def test_malformed_diagnostics_fail_closed_without_echo(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fresh_project(root)
            hostile = "HOSTILE_DIAG_662 " * 200
            for diagnostics in (
                "not a tuple",
                (12345,),
                (hostile,),
                tuple(f"d{i}" for i in range(200)),
            ):
                with self.subTest(kind=type(diagnostics).__name__):
                    frontier = PlanningFrontierStatus(
                        contract_id="frutlups.planning_frontier",
                        contract_version="1",
                        outcome="ready",
                        action="",
                        actor="",
                        block_citation="",
                        block_owner="",
                        completion_evidence="",
                        diagnostics=diagnostics,
                    )
                    decision = decide_planning_frontier_step(
                        root, frontier=frontier, architect_dispatch=_raising_dispatch
                    )
                    self.assertEqual(decision.behavior, "stop_fail_closed")
                    self.assertNotIn(
                        "HOSTILE_DIAG_662", " ".join(decision.diagnostics)
                    )

    def _ready_with_diagnostics(self, diagnostics) -> PlanningFrontierStatus:
        return PlanningFrontierStatus(
            contract_id="frutlups.planning_frontier",
            contract_version="1",
            outcome="ready",
            action="",
            actor="",
            block_citation="",
            block_owner="",
            completion_evidence="",
            diagnostics=diagnostics,
        )

    def test_diagnostics_exact_shape_boundaries(self) -> None:
        # Prompt 032 (Review 031 finding 2): tuple-only, count-bounded,
        # string-only, individually at most 240 characters. Exact boundaries:
        # 240 valid / 241 malformed; max count valid / max+1 malformed;
        # empty tuple valid / empty and non-empty lists malformed.
        from frutlups.gate import _FRONTIER_MAX_DIAGNOSTICS

        valid_cells = (
            ("empty tuple", ()),
            ("240-char member", ("x" * 240,)),
            ("max-count tuple", tuple(f"d{i}" for i in range(_FRONTIER_MAX_DIAGNOSTICS))),
        )
        for label, diagnostics in valid_cells:
            with self.subTest(valid=label):
                decision = decide_planning_frontier_step(
                    frontier=self._ready_with_diagnostics(diagnostics)
                )
                self.assertEqual(decision.behavior, "continue_declared_loop")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fresh_project(root)
            malformed_cells = (
                ("empty list", []),
                ("non-empty list", ["diag"]),
                ("241-char member", ("x" * 241,)),
                ("960-char member", ("x" * 960,)),
                ("961-char member", ("x" * 961,)),
                (
                    "count over maximum",
                    tuple(f"d{i}" for i in range(_FRONTIER_MAX_DIAGNOSTICS + 1)),
                ),
                ("generator-like non-tuple", "not a tuple"),
                ("non-string member", (b"bytes",)),
            )
            for label, diagnostics in malformed_cells:
                with self.subTest(malformed=label):
                    before = _snapshot(root)
                    decision = decide_planning_frontier_step(
                        root,
                        frontier=self._ready_with_diagnostics(diagnostics),
                        architect_dispatch=_raising_dispatch,
                    )
                    self.assertEqual(decision.behavior, "stop_fail_closed")
                    self.assertFalse(decision.success)
                    self.assertFalse(decision.dispatched)
                    self.assertIsNone(decision.recomputed)
                    for diag in decision.diagnostics:
                        self.assertLessEqual(len(diag), 240)
                    self.assertEqual(_snapshot(root), before)
                    self.assertEqual(
                        read_run_journal(journal_path_for(root)).entries, ()
                    )

    def test_malformed_diagnostics_fail_every_outcome_before_behavior(self) -> None:
        # For each of the five outcomes an otherwise valid shape with list or
        # 241-character diagnostics stops fail-closed before dispatch,
        # recomputation, retry behavior, or success.
        from frutlups.project import _ARCHITECT_FRONTIER_ACTION

        shapes = {
            "ready": {},
            "needs_specification": {
                "action": _ARCHITECT_FRONTIER_ACTION,
                "actor": "architect",
            },
            "blocked": {
                "citation": "05_governance/reviews/x_review_report.md",
                "owner": "human",
            },
            "complete": {"evidence": _CLOSURE_RECORD},
            "invalid": {},
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root, closure=True)
            for outcome, kwargs in shapes.items():
                for label, diagnostics in (("list", ["d"]), ("241-char", ("x" * 241,))):
                    with self.subTest(outcome=outcome, malformed=label):
                        base = _forged(outcome=outcome, **kwargs)
                        frontier = PlanningFrontierStatus(
                            **{**base.__dict__, "diagnostics": diagnostics}
                        )
                        decision = decide_planning_frontier_step(
                            root,
                            frontier=frontier,
                            architect_dispatch=_raising_dispatch,
                            retry_exhausted=True,
                            no_durable_progress=True,
                        )
                        self.assertEqual(decision.behavior, "stop_fail_closed")
                        self.assertFalse(decision.success)
                        self.assertFalse(decision.dispatched)
                        self.assertIsNone(decision.recomputed)

    def test_valid_shapes_keep_exactly_one_behavior_each(self) -> None:
        from frutlups.project import _ARCHITECT_FRONTIER_ACTION

        valid_cells = (
            (_forged(outcome="ready"), "continue_declared_loop"),
            (
                _forged(
                    outcome="needs_specification",
                    action=_ARCHITECT_FRONTIER_ACTION,
                    actor="architect",
                ),
                "dispatch_architect_and_recompute",
            ),
            (
                _forged(
                    outcome="blocked",
                    citation="05_governance/reviews/x_review_report.md",
                    owner="human",
                ),
                "stop_blocked",
            ),
            (_forged(outcome="invalid"), "stop_fail_closed"),
        )
        for frontier, behavior in valid_cells:
            with self.subTest(outcome=frontier.outcome):
                decision = decide_planning_frontier_step(frontier=frontier)
                self.assertEqual(decision.behavior, behavior)

    def test_validation_runs_before_retry_flags(self) -> None:
        # A malformed shape with retry flags set still reports the shape
        # refusal, not a retry stop.
        decision = decide_planning_frontier_step(
            frontier=_forged(outcome="ready", action="contaminated"),
            retry_exhausted=True,
            no_durable_progress=True,
        )
        self.assertEqual(decision.behavior, "stop_fail_closed")


class ClosureReceiptMatrixTests(unittest.TestCase):
    """Prompt 031 (Review 030 finding 2): only the real generated closure
    receipt qualifies; every bookkeeping, naming, tampering, and decoy shape
    stays non-completion."""

    def _frontier_outcome(self, root: Path) -> str:
        return _frontier(root).outcome

    def _closure_record_text(self, root: Path) -> str:
        return (root / _CLOSURE_RECORD).read_text(encoding="utf-8")

    def test_source_only_receipt_with_terminal_name_never_completes(self) -> None:
        # The exact Review 030 counterexample: content-free terminal-looking
        # pass report plus Source-only receipt.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root)
            _write_review_report(
                root, "m001_s02_record_001_closure_verdict_review_report.md", "pass"
            )
            _write_verdict_record(
                root,
                "m001_s02_record_001_closure_verdict_verdict_record.md",
                "m001_s02_record_001_closure_verdict_review_report.md",
            )
            frontier = _frontier(root)
            self.assertEqual(frontier.outcome, "needs_specification")
            self.assertEqual(frontier.completion_evidence, "")

    def test_missing_field_variants_never_complete(self) -> None:
        removals = (
            ("## Parsed Verdict", "## Tampered Verdict"),
            ("## Next Action", "## Tampered Action"),
            ("## Slice", "## Tampered Slice"),
            ("Kind: `milestone_complete`", "Kind: milestone_complete"),
            ("Next slice: none", "Next slice:"),
            ("Verdict: `pass`", "Verdict: `needs_work`"),
            ("Kind: `milestone_complete`", "Kind: `complete`"),
            ("Kind: `milestone_complete`", "Kind: `advance_to_next_slice`"),
            ("Slice ID: `M001-S02`", "Slice ID: `M001-S01`"),
            ("Milestone: `M001`", "Milestone: `M009`"),
        )
        for old, new in removals:
            with self.subTest(mutation=f"{old[:22]}->{new[:22]}"):
                with TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    _completed_project(root, closure=True)
                    record = root / _CLOSURE_RECORD
                    text = self._closure_record_text(root)
                    self.assertIn(old, text)
                    record.write_text(text.replace(old, new), encoding="utf-8")
                    self.assertNotEqual(self._frontier_outcome(root), "complete")

    def test_duplicate_sections_and_fields_never_complete(self) -> None:
        duplicates = (
            "\n## Slice\n\nSlice ID: `M001-S02`\nMilestone: `M001`\n",
            "\n## Parsed Verdict\n\nVerdict: `pass`\n",
            "\n## Next Action\n\nKind: `milestone_complete`\nNext slice: none\n",
        )
        for extra in duplicates:
            with self.subTest(extra=extra[:26]):
                with TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    _completed_project(root, closure=True)
                    record = root / _CLOSURE_RECORD
                    record.write_text(
                        self._closure_record_text(root) + extra, encoding="utf-8"
                    )
                    self.assertNotEqual(self._frontier_outcome(root), "complete")

    def test_fenced_and_indented_closure_decoys_never_complete(self) -> None:
        decoy = (
            "# Verdict Record: M001-S02\n\n"
            "## Source\n\n"
            "Review report: `05_governance/reviews/m001_s02_second_slice_review_report.md`\n\n"
            "```\n"
            "## Slice\n\nSlice ID: `M001-S02`\nMilestone: `M001`\n\n"
            "## Parsed Verdict\n\nVerdict: `pass`\n\n"
            "## Next Action\n\nKind: `milestone_complete`\nNext slice: none\n"
            "```\n"
        )
        indented_decoy = decoy.replace("```\n", "").replace("## Slice", "    ## Slice")
        for body in (decoy, indented_decoy):
            with self.subTest(kind="fenced" if "```" in body else "indented"):
                with TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    _completed_project(root, closure=True)
                    (root / _CLOSURE_RECORD).write_text(body, encoding="utf-8")
                    self.assertNotEqual(self._frontier_outcome(root), "complete")

    def test_non_terminal_closure_receipt_never_completes(self) -> None:
        # A real generated closure-shaped receipt for a NON-terminal slice
        # cannot complete the scope.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fresh_project(root)
            _write_review_report(root, "m001_s01_first_slice_review_report.md", "pass")
            _write_review_report(root, "m001_s02_second_slice_review_report.md", "pass")
            # Real writer for S01 (its record also carries milestone_complete
            # because both slices already show accepted pass reports).
            _record_via_real_writer(
                root, "05_governance/reviews/m001_s01_first_slice_review_report.md"
            )
            _write_verdict_record(
                root,
                "m001_s02_second_slice_verdict_record.md",
                "m001_s02_second_slice_review_report.md",
            )
            record_text = (
                root
                / "05_governance/reviews/m001_s01_first_slice_verdict_record.md"
            ).read_text(encoding="utf-8")
            self.assertIn("Kind: `milestone_complete`", record_text)
            frontier = _frontier(root)
            self.assertEqual(frontier.outcome, "needs_specification")

    def test_unreceipted_terminal_report_never_completes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fresh_project(root)
            _accept_slice(root, "M001-S01", "m001_s01_first_slice")
            _write_review_report(root, "m001_s02_second_slice_review_report.md", "pass")
            frontier = _frontier(root)
            self.assertNotEqual(frontier.outcome, "complete")

    def test_over_limit_record_never_completes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root, closure=True)
            record = root / _CLOSURE_RECORD
            record.write_text(
                self._closure_record_text(root) + ("filler\n" * 60000),
                encoding="utf-8",
            )
            self.assertNotEqual(self._frontier_outcome(root), "complete")

    def test_slice_free_roadmap_never_completes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root, closure=True)
            _write_detailed_roadmap(root, "### M001: Test\n\nSlices:\n\n")
            self.assertNotEqual(self._frontier_outcome(root), "complete")

    def test_tampered_report_breaks_completion_with_contradiction(self) -> None:
        # Tampering the paired report to needs_work makes the receipt
        # contradictory durable state, never a quieter completion.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root, closure=True)
            _write_review_report(
                root, "m001_s02_second_slice_review_report.md", "needs_work"
            )
            frontier = _frontier(root)
            self.assertEqual(frontier.outcome, "invalid")

    def test_closure_receipt_grants_no_slice_acceptance(self) -> None:
        # Decision 5: the receipt is closure evidence, never acceptance
        # authority. Removing the terminal pass report removes acceptance even
        # though the closure-shaped record still exists.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root, closure=True)
            (root / "05_governance/reviews/m001_s02_second_slice_review_report.md").unlink()
            status = build_status(root)
            self.assertNotIn("M001-S02", status.accepted_slice_ids)
            self.assertNotEqual(self._frontier_outcome(root), "complete")


if __name__ == "__main__":
    unittest.main()
