# Public native execution

The public entry points now run native `/2` projects through the same owned
checker, recovery, frontier and typed writers as the private controller. There
is no second runner, bypass flag, alternate executor or legacy-schema admission.
`ledger.append` and `Loop.append` stay refused; durable effects come only from
the existing typed writers.

## Entry points

```text
frutlups preflight [root] [--historical-assessment PATH --historical-assessment-sha SHA256]
frutlups run [root] [--until slice|milestone|roadmap] [--once] [--json]
             [--historical-assessment PATH --historical-assessment-sha SHA256]
frutlups status [root] [--usage] [--json]
```

The Python API matches the CLI: `loop.preflight(root, cfg=None, roadmap=None, *,
historical_assessment=None)`, `loop.prepare(...)` (same keywords),
`loop.run(..., until=None, once=False, emit=None, historical_assessment=None)` and
`Loop(root, cfg, roadmap, until=None, ..., historical_assessment=None)`.

Exit codes are unchanged: 0 for completion, a boundary or one `--once` step;
2 for an admission refusal; 3 for a stop that needs a person; 1 for an internal
stop, which the owner must inspect.

## Authority and ordering

1. Before any effect, admission loads configuration and roadmap and checks:
   - the native `/2` schema;
   - the effective `until` (`slice`, `milestone` or `roadmap`), including a
     programmatic `Config` and an explicit override;
   - the shape of the historical pair;
   - the configured Git.
2. Admission then takes the common-directory writer lock and refuses if:
   - authority bytes changed while it was acquiring the lock;
   - the supplied roadmap or configuration differs from the canonical on-disk
     authority. Dataclass-only changes do not count as authority.
3. It runs the existing static checks with fresh-work grant gates deferred, so
   pending recovery and terminal completion are not refused first. An unresolved
   invocation refuses here, because a run cannot recover it.
4. A supplied pair goes through the full owned historical validation
   (`_owned_history` plus `_select_historical`) before anything else happens.
5. The `Loop` runs the existing owned iteration. In order, it handles:
   - safety, including STOP under ownership before any checker or pending
     recovery, whether or not a historical pair is supplied;
   - the selected historical validation;
   - the checker and exact recovery when an approved commit is pending, with
     STOP rechecked immediately before recovery effects, so a STOP that appears
     during the checker still prevents recovery;
   - a reload of authority and history;
   - early and terminal stops;
   - the grant gate (`_profile_gate`), then dispatch.

   Both STOP refusals are reported as `kill_switch`. The rest of `_guard` (its
   `require_idle` check) still follows valid recovery, which still precedes the
   fresh-grant gate.
6. A public run probes once, immediately before its first model-seat dispatch.
   It probes one seat per distinct adapter, provider and model on the routes still
   open, after every refusal and recovery above.
   - **Coder dispatch:** readiness completes before any coder-start uncertainty is
     recorded. The coding marker and `*_started.json` are written only after the
     probes succeed; history, slice state and the append prefix are refreshed
     after the probe events. A genuine prior start or an unresolved invocation
     still refuses before readiness.
   - **Reviewer and holistic dispatch:** readiness runs at the start of seat
     dispatch.
   - **Probe reuse:** a probe context is `(scope, round, role, contract)`. A
     context is reused only if a completed probe with that exact contract belongs
     to the current run's folded invocation membership (`run["roots"]`, which
     includes retry chains). Another run's success with the same contract is never
     borrowed.
   - **Failed probes** stay charged and their initial context stays consumed;
     readiness in that run then needs a new owner grant.
   - Verification, prompt issuance and recovery never probe.
7. `preflight` probes only when fresh dispatch is admissible under the current
   grant. Before its first probe it runs the real project checker through the
   existing checked owned boundary (`_owned_history` plus `_admit_checked`), under
   the same ownership and with the same historical selection and finite deadline.
   A checker refusal stops admission before any probe reservation, job or adapter
   call. When no fresh work is admissible, it reports the static result alone,
   with no checker or probe.

`Loop` construction has no effects. It validates the schema, `until` and the
pair's shape. It then resolves the selected runtime the same way as static and
checked admission (`_admitted_item`):

- A milestone-first frontier slice uses its frozen runtime when it is admitted
  work. Under an autonomy profile that means the current open grant names it;
  without a profile, the frontier slice is current, as before.
- Otherwise the project runtime is used: a pending holistic closure, a completed
  or closed grant, its exact pending-commit recovery, or a no-work restart.

A later or ungranted milestone's frozen declaration therefore cannot select or
veto an earlier closure, its recovery or the completed grant's ordinary stop.
Once a grant admits that slice, its unavailable frozen runtime still refuses
before effects. Completion is not permission to ignore drift: canonical
authority, history and evidence validation, the checker before recovery and
exact recovery evidence are unchanged. Every `iteration()` acquires ownership. A directly constructed Loop also rechecks canonical authority on
each iteration. `run()` skips that recheck only because it already ran it inside
the same ownership interval. This restores the constructor's `until` guard
requested by carried finding M008-S08-02, which stays open until review.

## Historical selection

The public pair is the same raw `(project_relative_path, raw_sha256)` tuple the
private route accepts. Supply both flags or neither: half a pair, a context
object, a mapping or a nested pair is refused before any effect. There is no
automatic discovery, environment fallback or caller-built context. Without a
pair the strict route is unchanged. Each run re-validates the pair against its
bound ledger prefix. No schema, deadline or history is changed.

## Restart and refusal behaviour

- **Interrupted approved commit:** a fresh process runs the checker, recovers
  exactly that commit and then continues. It never repeats a seat, verifier,
  approval, commit or usage record, and needs no manual ledger or index repair.
  Replaying the restart changes nothing.
- **Unfinished attempt** (for example, an owner-killed process tree during a
  seat): `run` and `preflight` refuse with exit 2 and redispatch nothing. An
  explicit human or architect resolution is required.
- **Refused with no effects:**
  - `STOP`;
  - another writer;
  - an invalid `until`;
  - a missing grant or an exhausted grant;
  - no-profile dispatch;
  - unrecorded workspace drift;
  - a half or malformed pair;
  - a legacy roadmap schema.
- **A fresh run on a completed roadmap stops with `done`.** A process that itself
  closes the milestone stops at that milestone `boundary`.

## Acceptance-to-test map

| Acceptance | Evidence |
| --- | --- |
| C01 bounded offline boundary | Admission bindings; production changes are limited to `cli.py`, `loop.py` and `_preflight.py` |
| C02 owned path, ordering, no bypass | `test_public_v2.py` (all), `test_runtime_v2.py::test_authority_changed_during_lock_acquisition_refuses`, `test_cli.py::test_once_outputs_one_transition_and_resumes`; append refusals in the converted cases below |
| C03 `until` before effects | `test_public_v2.py::test_public_refusals_have_no_effects[until]`, `test_cli.py::test_until_overrides_configuration`, `test_cli.py::test_v2_public_runner_and_append_refuse_before_effects`, `test_native_retirement.py::test_public_native_execution_and_append_have_no_effects` |
| C04 public historical pair | `test_public_v2.py::test_public_historical_selection_pair`, `test_historical_deadlines_v2.py::test_public_and_caller_context_refusals`, the `half-selection` and `malformed-selection` refusals |
| C05 fresh-process lifecycle and opposite consumer | `test_public_v2.py::test_public_lifecycle_handoffs_and_opposite_consumer` |
| Round 2, M006-S03-001: STOP precedes pending recovery | `loop.py` `_iteration_owned` (owned STOP before checked admission on the non-historical path; kill-switch mapping on every path) and `_preflight.py` `_admit_checked` (STOP rechecked before `commit.recover`). `test_public_v2.py::test_public_direct_iteration_stop_precedes_pending_recovery[existing]` (no checker and no recovery) and `[during-checker]` (one real checker, then no recovery). Both assert unchanged HEAD, semantic index, ledger and approved payload, and positive exact recovery once STOP is removed. |
| Round 2, M006-S03-002: readiness before coder-start uncertainty | `loop.py` `_coding_slice` (probe before the marker and `*_started.json`, then refresh history and prefix). `test_public_v2.py::test_public_failed_readiness_records_no_coder_start`: a failed and a same-run repeated readiness leave no marker, started artifact or coder launch and keep both failed probes charged; after a repair and a new owner grant, a fresh public run dispatches the coder exactly once. |
| Round 2, M006-S03-003: current-run probe reuse | `_preflight.py` `_probed` (completed probe must be in `run["roots"]`). `test_public_v2.py::test_public_probe_reuse_is_scoped_to_current_run`: current-run reuse makes no extra probe; R002 failures with R001's identical contract refuse readiness even after repair; R003 transport-retry successes in the current run are reused with no further probe. |
| Continuation A: checker before preflight probes | `test_public_v2.py::test_public_preflight_checks_project_before_first_probe` (forwarding order: one real checker, then probes) and `::test_public_preflight_checker_refusal_precedes_any_probe` (real refusing project checker, no probe, unchanged ledger, index, workspace and jobs) |
| Completion continuation: completed grant, restart and pending closure | `test_public_v2.py::test_public_constructor_uses_milestone_first_runtime` (the same runner closes M001, then stops at the ordinary milestone boundary despite ungranted M002's 3.99 declaration), `::test_public_completed_grant_restart_ignores_ungranted_runtime` (fresh public `run`, `preflight` and direct construction and iteration report the completed grant with no new work, accounting or repair), `::test_public_pending_closure_recovers_despite_ungranted_runtime[valid]` (an interrupted approved M001 closure is recovered exactly once by a fresh public run; replay is a no-op) and `[tampered]` (invalid pending evidence refuses before recovery) |
| Continuation B: milestone-first construction | `test_public_v2.py::test_public_constructor_uses_milestone_first_runtime` (public construction and iteration close M001 despite M002's unavailable 3.99 declaration) and `::test_public_current_frozen_runtime_drift_still_refuses` (once M002 is granted and current, construction and public run refuse) |
| C06 restart and refusals | `test_public_v2.py::test_public_restart_recovers_interrupted_commit_exactly`, `::test_public_unfinished_attempt_refuses_without_redispatch`, `::test_public_refusals_have_no_effects` |
| C07 conversions | Table below |
| C08 documentation | This file, README, `usage.md`, `historical_eligibility.md` |

The lifecycle test grants run R001 through the final template's own
`scripts/ledger.py run admit`, then drives the real `frutlups` console script in
fresh processes:

1. `preflight`.
2. `run --once`, which issues the prompt.
3. `run --until slice`: coder, real frozen verification, routed review by both
   configured reviewers, and the accepted commit with the useful output in HEAD.
4. Runner-to-manual handoff: the template's `status` and `check` agree with F.
5. A manual `run extend`.
6. Manual-to-runner handoff: `run` replays the extended balances, then performs
   holistic review and the milestone-close commit.
7. An idle restart, then `status --usage`.
8. A final template `check` as the opposite consumer.

Fake Pi and Claude seats are real configured executables: a launcher, or a
shebang script on POSIX, that runs the shared fakes. The public CLI itself is
unpatched. Crash cases inject one exception at the approved-commit boundary in
their own process, then restart without the patch.

## Converted tests

Every baseline node ID, decorator and parameter ID is preserved. Each blanket
"not enabled" assertion became the genuine refusal or the positive public
result for that fixture.

| Test | Conversion |
| --- | --- |
| `test_cli.py::test_once_outputs_one_transition_and_resumes` | Before the grant, a no-profile owned refusal (exit 1, one row, no effects). After the grant, real public `run --once` and resume to `done`; same row and usage assertions. |
| `test_cli.py::test_until_overrides_configuration` | Public `run --until` both before the grant (owned refusal) and after it (exact boundary or done). |
| `test_cli.py::test_main_drops_rows_on_broken_stdout[run-0-*]` | The parameter ID is kept. The no-profile `run --once` now exits 1 from the owned refusal (expected value 2 changed to 1); every other assertion is unchanged. |
| `test_cli.py::test_preflight_failure_classes_exit_two` | Public output must equal exactly the genuine refusals from the private guard sequence. |
| `test_cli.py::test_preflight_success_and_configuration_loaded_once` | Preflight `ok` with configuration loaded once; `run --once` reaches the owned refusal (exit 1) with configuration still loaded once. |
| `test_cli.py::test_prompt_and_stop_rows_survive_cp1252_stdout` | The public run now emits the injected `seat_output` stop through cp1252 `backslashreplace` (exit 3). |
| `test_cli.py::test_v2_public_runner_and_append_refuse_before_effects` | Profile: the uncommitted roadmap edit refuses as unrecorded change. Ordinary: preflight `ok`, then the owned no-profile refusal. Construction is effect-free and checks schema and `until`; direct iteration reaches the owned refusal; append refusals are unchanged. |
| `test_attempts_v2.py::test_private_writer_cannot_grant_or_resolve_and_public_stays_refused` | The private writer refusal is unchanged; public admission and direct iteration refuse the dataclass-only configuration. |
| `test_historical_deadlines_v2.py::test_public_and_caller_context_refusals` | Contexts, mappings and nested pairs refuse through the public API and constructor; a public hash mismatch refuses; a public run without a pair stays strict, and only the generated findings projection is reconciled. |
| `test_idle_v2.py::test_idle_public_entry_points_remain_disabled` | Idle authority is committed first. Public prepare and run, and direct iteration, return the exact idle stops in `IDLE_PUBLIC` with no probe, job or dispatch. |
| `test_native_retirement.py::test_public_native_execution_and_append_have_no_effects` | Canonical-configuration refusal; construction is effect-free with the `until` guard; direct iteration refuses; append is refused. |
| `test_outcomes_v2.py::test_private_coding_writer_cannot_expand_authority` | The private writer refusal is unchanged; public admission is not a write path. |
| `test_profile_backlog.py::test_static_and_clean_share_projection_policy_without_public_enablement` | The static and clean policy assertions are unchanged; public admission and direct iteration refuse the dataclass-only roadmap. |
| `test_runtime_v2.py::test_direct_iteration_static_refusal_preserves_evidence` | With the scratch configuration treated as canonical, public preflight and run show the static seat refusal without reaching the iteration. |
| `test_runtime_v2.py::test_authority_changed_during_lock_acquisition_refuses` | Admission now takes the lock and refuses the concurrent authority edit, which stays on disk for inspection. |
| `test_runtime_v2.py::test_profile_static_accepts_bound_files_without_public_dispatch` | The static assertions are unchanged; public prepare refuses the dataclass-only runtime and verification change. |
| `test_selector_v2.py::test_public_and_legacy_refusals_remain_without_effects` | Canonical-configuration refusal; direct iteration refuses; the append, render and legacy refusals are unchanged. |

## Observed usefulness

These are development observations on Windows with CPython 3.14.3, real Git and
fake seats. They are not a performance qualification. They come from one coder
run of `test_public_v2.py`; the final focused run's figures are in the coder's
handoff.

- **Lifecycle:** about 88 s of public process time for grant to closed milestone.
  - Preflight 3.2 s; `--once` 2.3 s; slice 27.5 s; extension resume to closure
    34.5 s.
  - Idle restart 5.8 s; `status --usage` 1.3 s.
  - Durable operation seconds: probes 2.8 s over 5 attempts, coder 0.5 s,
    reviewers 1.2 s over 2, holistic 0.6 s, real verifier 0.7 s.
  - The remainder (checker runs, Git, evidence validation, process start-up) is
    combined overhead and is not profiled causally.
- **Restart:**
  - Crash before the slice commit 24.6 s, then restart through closure 35.6 s.
  - Crash before the closure commit 28.5 s, then restart 8.0 s.
  - Replays 5.7 s.
- **Refusals:** 0.3–1.6 s each.
- **Useful output:** the coder's file is in the accepted commit. Duplicate seats,
  verifications, approvals, commits and usage: zero. Manual repair: none.
- **Probes** are readiness checks, charged one job each; they are not seat work.

The continuation's refusal case uses an owner-committed project
`scripts/ledger.py` whose `check` exits non-zero. The production `_checker` still
launches that real project checker; only the project's own script is stricter.
Against the retained r1 bytes, the two checker tests and the construction test
fail. The drift contrast also fails there, but only in its setup step, which
closes M001 through the constructor.

Round 2 negative control: an external copy holds the retained pre-round-2
`_preflight.py` and `loop.py`, and its harness routes the test subprocesses to
that copy's source. Every round-2 case fails there, at the stage the review
describes:
- an existing STOP still ran the checker and one `commit.recover`;
- a STOP arriving during the checker still allowed recovery;
- a failed probe left the coding marker behind;
- R002's `preflight` borrowed R001's success and returned ready.

## Limits

Installed wheel, live providers, release, minimum Python, POSIX, symlinked
launchers, sleep/resume, arbitrary delegation scale and two-hour runs are not
qualified here. M006-S04, M006-S02 and M007 remain separate. Probe repetition
is bounded only by the run grammar's one-per-context rule. A grant must budget
one job per probe.

On the development host, the session environment sets
`NoDefaultCurrentDirectoryInExePath=1`. With it set,
`test_checker_v2.py::test_independent_search_controls_use_selected_git[root]`
fails, and it passes when the variable is removed. That test uses only
unchanged checker paths; this is a host property, not an S03 change.

The completion continuation corrected a previously observed defect. After the
earlier milestone closed, the next owned iteration entered checked admission
(approved-commit intents exist) and selected its checker runtime from the new
global frontier, an ungranted later milestone. With M002's unavailable 3.99
declaration it stopped `internal` instead of at the milestone boundary.
`_admitted_item` now limits frozen-runtime selection to granted work, in static
admission, checked admission and construction alike. Against the retained
previous candidate, the restored boundary assertion fails at its second
iteration with that internal 3.99 refusal. With its subprocesses pointed at the
retained bytes (external copy only), the fresh-restart case fails in its initial
closing run, and the valid pending-closure case is refused 3.99 on restart. The
tampered case refuses on both candidates.
