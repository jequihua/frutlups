# Native idle and completion component contract

M008-S03 consumes the accepted T M002-S02 development contract at
`bc4e9d1290119f54ec62d9e231826a68c2eea34c`, against F's accepted S02 base
`f6d87fa32c38fe51cf33546cd354bb21cc5e0b77`. The exact packet, pin, source
review, regression matrix and six-case index are bound in
`02_analysis/m008_s03_admission/r1/`; D076/D077 and the r2 amendment own this
implementation boundary. The historical native inventory and fixtures are
unchanged. This document describes component coverage, not acceptance.

A nonempty roadmap may have no active milestone. That is a planning shape;
an empty ordinary frontier does not activate planned work or prove completion.
Reopened work retains priority. `ledger.status_text` renders slice states,
the literal `no active milestone`, and next selection. `cli._status` composes
run balances, Git-derived commit status, holistic states, unfinished invocations
and done-label warnings once, in template order. JSON exposes the corresponding
run, holistic, invocation and warning records; usage remains a separate projection.

`Loop._complete` remains a state predicate. The existing private iteration
boundary validates historical bindings and retains writer ownership and pending
operation guards before target handling. Before reporting completion it also
uses `ledger.check` for current integrity. Recorded changes in ledger order and
the existing committed-HEAD exception permit later ownership and committed owner
hotfixes. No current HEAD/index is rebound to a historical receipt witness.
Repeated confirmation writes no event, report, planning file, staged payload or
HEAD. Milestone-boundary output distinguishes `roadmap already done` from
`mark roadmap milestone done separately`; the latter is an architect action.

The autonomy engine is unchanged: every ever-admitted planned milestone refuses
with `run admission requires active milestone`, including boundary-completed,
superseded and overrun histories. Inactive done replay requires complete slices,
required holistic closure and settled attempts, otherwise it refuses with
`inactive run history requires completed milestone and settled attempts`.
Existing fresh dispatch admission still requires an active milestone and a valid
grant (`new admission requires active milestone` at F's private profile gate).
F has no new grant writer or close command. Manual observation neither charges
nor refunds attempts. Completing the admitted stop closes the existing run;
usage, retry history, reservations and superseded runs remain intact.

## Complete source-row mapping

Names without a module prefix below belong to `tests/test_idle_v2.py`.
The source rows are the accepted packet's 15-row regression matrix, also mapped
in assessment r3 section 5. The tests compare file bytes, ledger bytes, HEAD and
semantic staged entries. Existing Git lock bookkeeping is outside that product
snapshot. Idle/refusal tests forbid provider probes, job construction, seats and
full-verifier execution and check that the named jobs directory was not created.

| Row | Existing F surface | Concrete coverage |
| --- | --- | --- |
| 01 | Private completion; separate roadmap edit/render | `test_last_active_completion_preserves_planned_block_and_separate_edit`: earlier planned blocked history survives; only two planning files change in the separate edit. |
| 02 | Roadmap validation/render, ledger and CLI status | `test_empty_frontier_is_idle_not_completion`; `test_roadmap.test_milestone_status_rules_match_template`; `test_idle_shape_keeps_strict_nonempty_structure`. |
| 03 | `next_slice`, `next_milestone`, explicit envelope selection | `test_reopen_priority_and_explicit_selection_do_not_activate_plan`: round-two reopen, unchanged planned labels and no grant or attempt. |
| 04 | State predicate and private idle boundary | `test_done_label_does_not_complete_nonaccepted_state`: unstarted, coding, verifying, reviewing, accept_pending, blocked and fix. |
| 05 | `validate_history` and current `ledger.check` | `test_bad_bound_evidence_and_product_drift_refuse_without_effects`: exact native immutable-reference and product-drift diagnostics. |
| 06 | Existing latest recorded ownership and committed-HEAD exception | `test_housekeeping_later_accepted_owner_and_committed_hotfix`: actual Git housekeeping, later accepted overlapping product and committed hotfix. |
| 07 | `require_idle`, existing approval/recovery and CLI status | `test_unfinished_invocation_precedes_target_and_approval_flags`; `test_pending_intent_precedes_unknown_approval_and_commit_flags`: unknown targets and both existing approval commit-flag values refuse after pending guards. |
| 08 | Existing non-holistic private completion | `test_repeat_completion_is_label_independent_and_nonwriting`: repeated complete snapshots and fold equality. Template `close --commit` spelling is template-only; F adds no equivalent CLI or milestone event. Row 07 covers its existing approval flag boundary. |
| 09 | Explicit holistic review/close/recovery | `test_no_active_holistic_close_pending_stays_visible`; `test_milestones_v2.test_holistic_actual_iteration_and_exact_replay`, `test_fresh_holistic_recovery`, `test_blocked_then_external_resolution_starts_new_cycle`, `test_needs_work_groups_findings_once`. |
| 10 | CLI done-label warning versus evidence | `test_empty_frontier_is_idle_not_completion`; `test_done_label_does_not_complete_nonaccepted_state`; holistic pending test. |
| 11 | History replay and existing fresh dispatch gate | `test_done_profile_replay_and_manual_completion_preserve_accounting`: bound completed done replay; active-only admission remains. |
| 12 | Real native read/fold/history paths | `test_pinned_invalid_idle_histories_refuse_real_readers_before_effects`; `test_autonomy.test_profile_case` for all four added planned cases; `test_idle_relabelling_preserves_original_run_accounting`. |
| 13 | Inactive history admission | `test_bound_done_history_requires_complete_settled_milestone`: bound incomplete, missing-holistic and unsettled histories; pinned missing-holistic case. |
| 14 | Completed done plus never-admitted planned | `test_done_profile_replay_and_manual_completion_preserve_accounting`; exact `completed-done-plus-never-admitted-planned` corpus setup. |
| 15 | Label-independent evidence-qualified confirmation | `test_repeat_completion_is_label_independent_and_nonwriting` for active/planned/done, including separate-edit versus already-done output. |

`test_autonomy.test_profile_case` retains the original 128 cases and adds the
six exact S03 input cases: no-active planned admission, completed-to-planned,
overrun-to-planned, superseded-to-planned, required holistic closure and completed
done beside never-admitted planned. Development-time comparison verified all six cases against the admitted source.
Maintained tests read only the package corpus and use each case's declared setup,
diagnostic and expected state fields.
The adapter now respects `second_status`. Additional active-history comparisons
assert exact consumed/reserved units and supersession before planned refusal;
they do not alter corpus expectations.

`test_cli.test_status_and_usage_conform_to_template` compares the entire real
template stdout without filtering diagnostics. `test_loop.conform` preserves its
full comparison, explicitly composes the values and checks each diagnostic once;
its existing integration callers remain. Direct idle CLI tests check exact text,
while the holistic usage test checks complete JSON diagnostic values and retained
usage assertions. Public refusal on idle shapes is covered by
`test_idle_public_entry_points_remain_disabled` and the retained
`test_cli.test_v2_public_runner_and_append_refuse_before_effects`.

## Qualification boundary

Public /2 construction, prepare, run, iteration and general append remain refused.
The owned tests exercise existing private component boundaries, including real
Git and existing native evidence fixtures. They add no disconnected executor,
runtime template import, schema conversion, dependency or grant machinery.
Installed public parity remains M006-S02 after separately assessed M006-S03
integration. No production, live-provider, minimum-Python, POSIX, symlink or
sleep/resume qualification follows from these checks. All carried/open P3
identities and dispositions remain unchanged.

The coder's prescribed eight-module final focused gate has a finite 2400-second
ceiling clipped to the remaining allocation. Architect-owned uncapped full
verification and independent review remain separate required actions on final
bytes. The receipt metadata helper is not the full test execution and is not
run by the coder. No test result or acceptance is inferred from this mapping.


D078 separates historical S08 reconciliation from current behavior. The original
report remains byte-identical. `tests/fixtures/native_retirement_definitions.json`
is the exact architect-prepared snapshot of 413 accepted S08 definitions and
exact decorator lists at `5da6ec674931b8c79ec2dd218be5c751641e366c`.
The historical accounting test pins the raw report and snapshot, checks exact
membership, hashes every definition and retains all 1,317-node and 150-case
assertions. Cheap source/decorator tampering and missing/renamed-entry tests
refuse. This snapshot never qualifies current implementations; no maintained
historical test reads Git history or mutable current test source.

The original `active-milestone` parametrized node remains, now asserting valid
native idle shape instead of the superseded refusal. Development collection
compared the complete accepted 1,361-node set with 1,412 current nodes: zero
removed, 51 added. The historical 413 source/decorator identities were verified
against the accepted pre-S03 HEAD; later accepted unchanged modules complete
that baseline. Full collection is discovery evidence only, not test execution.
