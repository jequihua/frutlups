# Private checker and recovery admission

This integration targets the accepted template development tree at
`4632a8915eb03859381ed2a97fc8e408c3814c39`. The 83-file fixture archive and manifest
are byte-exact copies. `checker_pin` checks the archive and member hashes, unique
safe relative names, regular files and modes before writing any member. Production
does not import template modules. Public native execution remains disabled.

## Invocation and ownership

`Loop._iteration_owned` enters `_preflight._owned_admission` before recovery of an
existing intent and before a selected slice or holistic transition. The context
remains open through that transition. Pure completed/idle reads without an intent
retain their existing behavior. `_native_support.Controller` supplies test-only
access to this same method; it is not another executor. Public constructor,
prepare, run, iteration and append refusals are unchanged.

Admission resolves the root, requires `05_governance/ledger.jsonl`, validates the
configured Git executable and binds authority bytes and actual Git directory
identities. Under existing common-directory writer ownership it rechecks these
identities, including the canonical worktree, validates history and rejects an
unfinished invocation. Runtime selection uses the frozen task declaration where
applicable. The exact checker invocation is:

```text
<selected absolute Python> -B scripts/ledger.py --root <canonical root> check
```

Its working directory is that canonical root. The environment removes ambient
Git and Python redirection variables, disables global/system Git configuration,
and sets `TEMPLATE_GIT_EXECUTABLE` to validated configured Git. Provider credential
allowlisting remains the existing policy. Windows supplied and resolved executable
names must both end in `.exe`; there is no shell interpretation or PATH fallback
for an explicit selection.

The existing managed process runner captures bounded streams in external temporary
storage. Streams are discarded; failures expose fixed diagnostics, not child text
or launch exception text. The checker creates no ledger event, job, reservation,
grant, retry or receipt. Its finite positive timeout is the smaller of the
verification cap and remaining caller deadline. Setup from entry to `_checker`,
process execution, cleanup and post-check witness observation all count toward
this interval. Reported lifetime and the final clock must also fit. Earlier lock
and history work counts against the caller deadline but does not start a second
checker cap. Failure is never retried here.

Successful exit is necessary but insufficient. Admission compares Git-listed raw
files, HEAD and semantic staged entries, canonical ledger bytes, ignored local
authority, and selected executable/checker bytes. It rechecks Git ownership
topology. Windows handle comparisons restore suffix-derived execute bits only for
comparison; the path mode used by the existing receipt hash remains unchanged.

Eligible pending approval/closure then uses existing exact recovery under the
same owner. Recovery may change only its already approved Git state; authority
must remain unchanged. Admission reloads model, config and events and validates
again. Completed witness replay is a no-op; an unresolved marker stays unresolved.
Only fresh dispatch requires a profile and a scoped, open, unexhausted grant.
Checker success never creates those permissions. Selected blocked slice/holistic
state retains its existing `blocked_verdict` stop before fresh-dispatch eligibility;
it does not launch work or bypass admission.

## Evidence map

Names below are in `tests/test_checker_v2.py` unless another module is specified.
These are bounded integration tests, not public or installed qualification.

| Group | Tests and observed boundary |
| --- | --- |
| C01 | `test_explicit_selection_refuses_without_launch`, `test_windows_shell_selection_is_not_an_executable`, `test_python_shell_selection_refuses_before_identity_child`; pinned helper selection/replacement cases exercise both real helper routes, invalid images and deletion with usable alternate Git. |
| C02 | `test_actual_checker_child_identity_at_supported_long_spaced_root` records the actual checker process's application module, CPython/PyYAML, cwd, argv, selected Git and real template Git calls using forwarding-only startup instrumentation. `test_owned_application_directory_and_both_real_git_routes` independently runs `_common.git` and `_git.run` in the owned process. `test_real_checker_unprofiled_read_and_fresh_dispatch_refusal` binds exact F invocation/cap/environment. |
| C03 | `test_independent_search_controls_use_selected_git` separates project shadow, PATH shadow, absent PATH and combined cases, with both unselected helper failures. The owned application-directory case copies named runtime binaries, verifies `GetModuleFileNameW` and tests both helpers against its invalid `git.exe` shadow. No shared interpreter is changed. |
| C04 | The actual child identity test launches at a supported long root containing spaces; all checker fixtures also contain spaces. `test_pinned_helper_selection_replacement_and_synthetic_framing` runs the pin's complete argv/diff-batch assertions, including 12288-byte and 512-byte reserve accounting. Arithmetic boundary strings are synthetic; they are not claimed as OS path-limit launches. |
| C05 | `test_owned_checker_read_allows_no_second_writer` holds F ownership while running the real checker and fresh manual/F contenders, including linked worktrees. `test_lock_boundary_change_refuses_before_checker` changes authority, replaces a Git directory behind its unchanged textual name, or redirects `core.worktree`; each refuses before checker execution. |
| C06 | `test_checker_terminal_refusals_are_private_and_nonwriting` exercises timeout, unknown status, exception, nonzero exit and both stream overflows at the real private transition, with spies refusing later probe/job/verifier/recovery effects. `test_real_checker_success_does_not_authorize_drift` changes authority, raw ledger, interpreter bytes, HEAD or semantic index flags after real checker success. Deadline tests cover reported/elapsed exit-zero overrun and one real managed timeout with an owned descendant's terminal disposition checked. Nonfinite/empty budgets never launch the checker. |
| C07 | `test_environment_rejects_ambient_redirection` covers Git repository/worktree/index/config/object and Python selectors. Terminal-refusal tests inject short fake secrets and paths and assert fixed private diagnostics with unchanged product/ledger and no job directory. This does not close the unrelated carried short-secret finding. |
| C08 | `test_configured_git_historical_and_head_reads_without_path` reads a historical committed manifest and accepts a committed owner hotfix with Git absent from PATH and bogus ambient `GIT_DIR`; invalid configured Git still fails. Explicit propagation covers ledger append/history/check, commit, receipt, attempts, render, loop evidence, private admission, Loop and CLI status. Restricted production modules contain only propagation or managed HEAD lookup changes. Review alone may close M004-S01-R4-002. |
| C09 | `test_fresh_process_checker_precedes_exact_pending_recovery` produces pending slice and milestone intents through real private transitions and restarts in fresh processes. It records checker-before-recovery and config reload, exactly one commit and repeat no-op with unchanged ledger, attempts and run accounting. Manual variants use the pinned template recovery route. `test_manual_pending_recovery_needs_no_profile_or_new_grant` creates genuine `by=human` template approval, then F recovery without a profile/grant. `test_checked_manual_pending_guards_preserve_state` covers approved-file drift, retained unknown marker and cancellation. Existing `test_commit_v2` interruption/cancellation/witness guards remain. |
| C10 | `test_fresh_dispatch_grant_guards_before_effects` covers missing, closed, job/time-exhausted and wrong-scope grants and rejects insertion of a profile over ordinary attempts. The manual pending test covers manual-to-F; fresh-process manual recovery variants cover F-to-manual with unchanged usage. Public refusals remain covered by `test_cli.py` and `test_attempts_v2.py`. |
| C11 | Preserve the 1412 baseline node identities and all unamended assertions, old fixtures and protected inventories. Full collection compares identities without running the suite. The designated final focused gate covers the eleven named modules once on final bytes. The separate uncapped full execution, metadata receipt and independent review belong to the architect. This document is not that receipt. |

## D080 timeout oracle

The exact existing node
`test_loop.py::test_seat_failure_classification_and_same_round_retry[timeout-done]`
formerly expected two ordinary profile-free coder dispatches. D080 supersedes
only that branch: it now requires the precise profile-free stop and unchanged
ledger, raw witness and authority, with no prompt, attempt, probe, seat, job,
counter or output. The other four parametrized cases retain their assertions.

`test_loop.py::test_granted_real_timeout_settles_lifetime_without_second_dispatch`
runs the real owned fake seat under a one-second reservation, observes drained
elapsed usage, retirement for overrun and no second dispatch or refund.
`test_attempts_v2.py::test_overrun_is_not_clamped_and_retires_run` and
`test_materialized_extension_cannot_revive_overrun` remain unchanged. Existing
transport retry tests retain their grant/remaining-allowance conditions.

## D081 local budget oracles

The retained `test_loop.py::test_budget_and_owner_stops` nodes
`jobs-budget_exhausted` and `wall-budget_exhausted` now assert precise no-effects
profile-free admission refusal. Their old positive dispatch oracles are explicitly
superseded by D081; STOP, rounds and blocked retain their assertions.
`test_ordinary_local_guard_threshold_without_dispatch[jobs]` and `[wall]` exercise
the actual ordinary `Loop._guard` with valid idle history, below-limit controls,
and exact local threshold reasons/diagnostics, without dispatch or state changes.
These component tests do not authorize ordinary execution. Granted durable usage,
settlement and retirement remain covered by the unchanged `test_attempts_v2`
regressions and D080's real granted timeout test. No grant insertion substitutes
for an ordinary guard test, and no recovery-call optimization is included.

## D082 nested ownership and Git diagnostic oracles

The retained `test_runtime_v2.py::test_nested_append_keeps_outer_lock_and_exception_releases`
now requires the precise profile-required refusal and unchanged ledger, authority
and product witness, with no prompt, attempt, probe, seat or job. D082 supersedes
only its old successful profile-free iteration and one-event assertions. Real
outer and nested ownership, public append refusal, a competing manual writer's
exclusion, and release after the exact deliberate exception remain exercised.

`test_runtime_v2.py::test_granted_nested_prompt_append_keeps_outer_lock_and_exception_releases`
preserves successful nested append coverage using an explicit native grant and
the actual private transition. It checks one new prompt event, its exact fields
and artifact digests, the unchanged prior ledger prefix, and durable history.
The real competing writer is excluded after append, during nested reentry and
after nested exit, then acquires after the outer exception releases ownership.
These nodes cover C05/C10 without enabling ordinary execution.

Only the `[git]` branch of
`test_cli.py::test_preflight_failure_classes_exit_two` now checks the exact
`configured Git must name an existing absolute executable file` error tuple.
Its exit-two, public refusal, diagnostic-prefix and unchanged-ledger assertions
remain, as do all other branches. This C01 regression uses a missing configured
executable and does not permit PATH fallback or change production diagnostics.

## Limits

Executable replacement resistance is bounded observation (invalid image,
deletion, changed bytes/authority), not a tamper-proof executable lease against
an adversarial concurrent same-user replacement. Witnesses compare endpoints and
do not observe transient changes restored between them. Ignored non-authority
stores and submodule interiors remain outside the existing product witness.

Actual owned application and long-path proofs are Windows CPython 3.14.3 with
PyYAML 6.0.3 and Git 2.32.0.windows.1. POSIX, minimum Python, native Windows
executable symlinks, sleep/resume, DEC03-I, live providers, installed/public
execution and publication remain unqualified. No dependency, production module,
receipt grammar, runtime digest or historical deadline eligibility is added or
changed. All unrelated open/carried findings retain their identities and status.
