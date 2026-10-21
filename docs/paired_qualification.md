# Installed exact-pair offline qualification (M006-S02)

Candidate record for M006-S02 round 3. It describes the qualification harness
and the per-row evidence it produces; it is not slice acceptance. The separately
authorized architect full run, independent review and acceptance remain pending.
Rows P01-P14 are from `02_analysis/m006_s02_admission/r1/matrix.md`; the round-2
corrections follow `r1/intake/assessment.md`, `r1/intake/contract_clarifications.md`
and `r2/handoff.md`, and the round-3 idle additions follow `r2/intake/assessment.md`
and `r3/handoff.md`, all in the same admission packet.

## Exact artifacts and toolchain

| Input | Bytes | Raw SHA-256 |
| --- | ---: | --- |
| `frutlups-0.4.0-py3-none-any.whl` (built from `7351589890e55461133db3a83022747568a4f629`) | 130893 | `628c2eb95723d03e8353dce5b70f0ff24d41f38408ab97d5b83882c8deb3d2e4` |
| `pyyaml-6.0.3-cp314-cp314-win_amd64.whl` | 156429 | `4a2e8cebe2ff6ab7d1050ecd59c25d4c8bd7e6f400f5f82b96557ac0abafd0ac` |
| `tests/fixtures/v5_final/template-5.0.0.tar` (T `711bce067eb0b3d05abf8cdec54f3cc812f62a5d`) | 460800 | `a8d3764d87804e0ae9b880f02a4f53592e553dc53c2d24d51b22d6a7bf4ec04a` |
| `tests/fixtures/v5_final/protocol_v2.json` (16 cases) | 25323 | `59a19d634ccfbd8d8c37616196cb59974b06310b168810e6ba71fda3b8a39891` |
| `tests/fixtures/v5_final/autonomy_v1.json` (134 cases) | 343766 | `ca8d6a2b159bb3f468ce725ab0645cd6153338bdb70a19f58027ab16a1c63150` |
| `tests/fixtures/v5_final.manifest.json` | 141317 | `c533c82f3e3d66f35ed6140d76554adbee0a9c2eb1cad55e2e5c2c292acf9c0c` |

The two wheels are read from their pinned S04 evidence paths
(`02_analysis/m006_s04_admission/r1/artifacts/`). Toolchain identities are
checked against `02_analysis/m006_s02_admission/r1/toolchain.json`: selected
Python 3.14.3 (`97c3228a...`), configured Git (`1a2319cd...`, the Git for Windows
`cmd` entry point resolved from `PATH`) and the setuptools console launcher used
for fake seats (`bbb3de57...`). A missing or different input raises
`ArtifactError` before any install or launch; nothing is skipped, fetched,
rebuilt or substituted. Run the module from an environment whose `git` resolves
to that configured identity.

## Harness

`tests/_installed_pair.py` owns the repeated invariant:

- **Install, once per pytest session.** A fresh `venv` in session scratch,
  `pip install --no-index --no-deps --no-cache-dir --require-hashes` from owned
  copies of the two wheels, `pip check`, then complete RECORD and member checks
  of every installed file and the console launcher.
- **Children.** Every child receives a minimal environment: venv/Git/System32
  `PATH`, scratch `TEMP`/`HOME`, `PIP_CONFIG_FILE`, `PIP_NO_INDEX`, isolated Git
  configuration and `TEMPLATE_GIT_EXECUTABLE`. Before every launch the harness
  asserts that no `PYTEST*`, `FRUTLUPS_S02*`, `PYTHONPATH`, `PYTHONHOME` or
  `PYTHONSTARTUP` key is present, so controller instrumentation never reaches an
  installed or template child. There is no inherited `GIT_*` or provider
  credential.
- **Projects.** Each test materializes the 69 verified final T members into its
  own scratch project. Owned setup is committed before any grant: roadmap,
  `frutlups.toml`, and the ignored `frutlups.local.toml`/`project.local.toml`
  selecting the venv Python, configured Git and fake seats.
- **Processes.** Commands run through `frutlups.procs.run_process`, which owns
  the process tree and retains complete stdout/stderr. F runs as the real
  installed `frutlups.exe`; T runs as its exported `scripts/*.py` under the
  selected Python; Git is the configured executable.
- **Fake seats.** `fake-pi.exe`/`fake-claude.exe` are the pinned launcher plus
  shims running under the venv Python with `-I -B`. They load byte-exact copies
  of `tests/fake_pi.py` and `tests/fake_claude.py`. Harness-only behaviour lives
  in `pair_seat.py` and is driven by a `pair` key in `local_state/plan.json`:
  - exact-byte, bulk, huge, raw-byte and deleted files, and worktree moves;
  - Pi floods beyond capture, oversized records and usage variants;
  - Claude raw padding;
  - typed coder outcomes;
  - a reviewer that reads all bound evidence in canonical order;
  - a reservation observer and secret canaries.

  No real provider CLI, credential or network is involved.
- **Fault injection, named and confined.** `FAULT` intercepts one approval
  boundary: before recovery, before staging, after staging or after commit
  creation. It records that the boundary was reached, then either raises
  (`exception`) or terminates its own process (`death`, exit 137, no cleanup).
  `FSYNC_FAULT` fails the first fsync after the real coder seat returns. Both run
  in a fresh owned process and otherwise call the real installed code. Every
  restart is the unmodified public console.
- **Utility drivers.** `CORPUS_F` folds corpus cases through the installed
  reader. Protocol cases, and any request marked `ledger_file`, are written to a
  real ledger file and read back with `ledger.read`; the driver reports whether
  those bytes changed and which attempts are unfinished. `SCRUB_UTILITY` scrubs
  and parses one synthetic report through the installed `receipt`/`verdict`
  modules. Both run as isolated (`-I -B`) venv processes. They are utility
  evidence, not public acceptance.

Only the test controller imports the development tree, for process ownership.
Every lifecycle decision, reader and recovery that counts as installed evidence
runs in a fresh venv process.

## Commands

| Purpose | Command |
| --- | --- |
| Focused development and the round-2 final selection | `python -B -m pytest -q -vv 08_pkg/frutlups/tests/test_interoperability.py -p no:cacheprovider --basetemp <scratch>` (exact `r1/targeted_argv.json`) |
| Round-3 final selection | the same command with the four `test_p04_r3_idle_*` function selectors in place of the module (exact `r3/targeted_argv.json`) |
| F, installed | `frutlups {preflight,run,status} <project> [--once] [--until slice\|milestone] [--json] [--usage] [--historical-assessment PATH --historical-assessment-sha SHA]` |
| T, final export | `scripts/ledger.py --root <project> {status,check,run admit,run extend,coded,blocked,record,accept,resolve,reopen,recover [--execute],cancel,reconcile}`, `scripts/prompt.py`, `scripts/roadmap.py render`, `scripts/verify.py` |

### Command log

`installed_pair_timings.jsonl` is external evidence: it lives in the pytest
base temporary directory, never in the repository. Each line has one `record`
type:

- `command` — one actual owned child, written once: `label`, `kind`, `utc_start`, the actual
  `argv` and `cwd`, `exit`, `timed_out`, `exception`, `timeout`, `elapsed`, the
  pytest node, and `streams` with each stream's actual retained file `path`,
  `bytes`, raw `sha256` and `truncated` flag. Kinds are `install` (venv, pip,
  `pip check`), `public` (the installed console, the `FAULT`/`FSYNC_FAULT`
  launches of installed F and the `CORPUS_F` reader), `template` (final T scripts
  and the T corpus reader), `git` and `utility` (the scrub driver).
- `synthetic` — a derived total (`install-total`, `corpus-reader-total`,
  `relabel-reader-total`) with an
  explicit `overlaps` list naming the command records it spans. It is never added
  to command time.
- `project` — a registered scratch project root, so its own ledger can be read.

The actual fields keep machine-local values. `portable_argv`, `portable_cwd`,
`portable_root` and each stream's `portable_path` are display copies with
`<venv>`, `<session>`, `<git>`, `<basetemp>`, `<repo>` and `<selected-python>`
substituted; they are distinguished from, and never replace, the actual values.

`test_p14_*` writes `installed_pair_summary.json` beside the log:

- `actual_commands` — count and elapsed time per kind, from command records only.
- `within_public_F` — fake-seat and F verification seconds from every registered
  ledger's `attempt_finished` usage, and the public-F residual. Those seconds run
  inside public F commands, so they overlap that time. The residual is combined
  product, checker, Git and process overhead, not a causal profile.
- `synthetic_totals`, `projects`, and the labels of any unexpected timeout or
  exception. The deliberate P11 owner termination (`owner-killed`) is the only
  intended timeout.

### Controller phase observer

The final selection runs with the admitted
`02_analysis/m006_s02_admission/r2/s02_phase_observer.py`, which reuses the
maintained `scripts/check_project.py` start/phase/skip/session logging. It is
enabled only in the controller process environment: `PYTEST_ADDOPTS` removed,
`PYTHONPATH` replaced by the r2 directory, `PYTEST_PLUGINS=s02_phase_observer`,
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONDONTWRITEBYTECODE=1` and
`FRUTLUPS_S02_PHASE_JOURNAL` naming a fresh external journal. The journal
records `observer_ready`, one `collection`, every node's `start` and
setup/call/teardown `phase`, and `session_finish`. It is not installed, not in
the wheel and not package qualification; the child-environment check above keeps
it out of every installed child.

## Row map

Each row lists its exact nodes and the observation they assert. "No effects"
means the complete project snapshot is unchanged: raw ledger, HEAD, semantic and
raw index, Git status, every Git-visible file, job directories and local
configuration.

| Row | Nodes (`tests/test_interoperability.py::`) | Installed observation | Contracts |
| --- | --- | --- | --- |
| P01 | `test_p01_frozen_artifacts_and_isolated_installation`, `test_p01_wrong_artifact_identity_refuses_before_install` | See [P01](#p01-artifacts-and-installation) | V09, G06 |
| P02 | `test_p02_all_150_cases_through_installed_reader_and_final_template`, `test_p02_template_policy_history_refuses_installed_execution[3 cases]` | See [P02](#p02-corpus) | Q01, V01, V08, G01 |
| P03 | `test_p03_installed_lifecycle_both_handoffs` | See [P03](#p03-ordinary-lifecycle-and-both-handoffs) | Q11, V02, V05, V08, G02, G03 |
| P04 | `test_p04_no_active_shapes_are_idle_and_non_writing[3 shapes]`, `test_p04_last_active_completion_preserves_earlier_planned_blocked_history`, `test_p04_manual_completion_confirms_independently_of_label`, `test_p04_frontier_reopen_relabel_and_planned_authority`, `test_p04_r3_idle_strict_structure[4 shapes]`, `test_p04_r3_idle_unfinished_done[4 steps]`, `test_p04_r3_idle_holistic_pending`, `test_p04_r3_idle_relabel_accounting` | See [P04](#p04-boundaries) and the [idle-row map](#idle-row-map) | Q10, V01, V04, G04 |
| P05 | `test_p05_exact_recovery_after_interrupted_approval[8 cases]`, `test_p05_recovery_preserves_unrelated_tracked_staged_and_unstaged_content`, `test_p05_cancellation_resolves_only_the_named_intent` | See [P05](#p05-exact-recovery) | Q03, V04, G04 |
| P06 | `test_p06_typed_blocker_retains_edits_and_resolves_once`, `test_p06_identical_no_change_failures_stop_without_extra_experiment` | See [P06](#p06-blocker-and-no-progress) | Q04, V02, G02 |
| P07 | `test_p07_partial_routed_review_resumes_from_exact_checkpoint`, `test_p07_findings_closure_carry_and_unrelated_pass`, `test_p07_human_waiver_recorded_and_nonhuman_waiver_refused`, `test_p07_holistic_block_resolution_and_correction` | See [P07](#p07-findings-and-checkpoints) | Q09, Q10, V02, G02, G04 |
| P08 | `test_p08_large_literal_evidence_late_defect_and_exact_staging`, `test_p08_empty_product_selection_approves_only_bound_evidence`; unrelated tracked content in P05 | See [P08](#p08-large-and-literal-evidence) | Q02, Q06, V07, G05 |
| P09 | `test_p09_pi_beyond_capture_completes_with_truthful_usage`, `test_p09_report_canaries_are_never_disclosed[secret, path]`, `test_p09_installed_scrub_utility_preserves_report_grammar`, `test_p09_output_and_record_limits_are_genuine_refusals[pi-record, claude-raw]` | See [P09](#p09-output-and-usage) | Q05, Q07, G05 |
| P10 | `test_p10_raw_and_normalized_identities_custom_argv_and_prompt_fallback`, `test_p10_same_status_drift_refuses_despite_normalized_equality[content, flags, head]` | See [P10](#p10-integrity-and-hashes) | Q07, Q08, Q11, V07, G05 |
| P11 | `test_p11_live_owned_child_writer_exclusion_then_owner_death`, `test_p11_selected_python_and_configured_git_despite_hostile_path[shadowed, missing]`, `test_p11_invalid_selection_refuses_without_fallback[4 cases]`, `test_p11_stop_precedes_pending_recovery_and_checker_never_dispatches` | See [P11](#p11-ownership-and-identity) | Q12, V03, V04, G04 |
| P12 | `test_p12_grant_and_schema_refusals_precede_probes_jobs_and_verifier[6 cases]`, `test_p12_old_receipt_schema_refuses_before_probe_job_or_verifier`, `test_p12_reservation_precedes_seat_and_fsync_fault_is_not_refunded`, `test_p12_transport_retry_extension_and_unknown_metrics_keep_charges` | See [P12](#p12-grants-and-attempts) | Q01, Q12, V08, G01, G04 |
| P13 | `test_p13_historical_assessment_pair_on_supported_final_template_history` | See [P13](#p13-history) | V06, G02, G04 |
| P14 | `test_p14_continuity_rehash_and_measured_usefulness` | See [P14](#p14-continuity-and-usefulness) | Q11, Q12 offline subset, V09, G06 |

### P01 artifacts and installation

- Both wheels have complete RECORD and member hashes, and their metadata
  declares `PyYAML<7,>=6.0.3` and `>=3.11`.
- The 25-file frutlups payload equals the committed source blobs at
  `7351589`.
- The final archive, its 69 members and both corpora match the manifest.
- The installed import and distribution report `0.4.0`/`6.0.3`, with origins
  contained in the venv. Direct URLs are archives, not editable installs, and
  every installed file's RECORD hash matches.
- The single entry point is `frutlups.cli:main`, and the Pi extension is
  byte-exact.
- `frutlups.exe` with no arguments, run outside the checkout, prints the usage
  text and exits 2.
- Tampered and missing artifacts raise `ArtifactError` before install. pip's own
  `--require-hashes` also refuses altered wheel bytes, and nothing is installed.
- The venv, install and `pip check` commands are separate command records; the
  `install-total` synthetic row spans them.

### P02 corpus

- An isolated installed-F reader driver and the final T `scripts/ledger.py`
  fold all 150 cases. They report observations; the test compares them with
  each case's oracle. Neither reader changes the request bytes or any case
  ledger.
- For every case, both readers agree with every declared run field (revision,
  allowance, consumed, remaining, closed), head, membership, slice
  step/round, checkpoint and intent counts, holistic step and done flag.
- Refusals give F's exact native diagnostic, and T's message contains the
  corpus diagnostic. Totals: 46 valid, 101 refused, 3 `template_policy`;
  protocol 6 valid/10 refused, autonomy 40 valid/94 refused.
- Each case's canonical-JSON SHA-256 equals the manifest `source_case_sha256`.
  The three `template_policy` cases also refuse installed `run`, `preflight`
  and `status` with `unsupported legacy history; use pinned v4`, with no
  effects.
- **Original D023 corpus invariants**, read from the pinned predecessor corpora
  `tests/fixtures/protocol_v2.json` and `tests/fixtures/autonomy_v1.json`:
  schemas `template.conformance/2` and `template.autonomy-conformance/1`; 16 and
  128 cases with unique names; 40 valid autonomy cases. The final corpora are
  `template.conformance/3` and `template.autonomy-conformance/2`; protocol names
  are identical, and autonomy keeps all 128 names and adds exactly six.
- **Two separate accounts.** The 144-case D023 predecessor subset is exactly the
  set of original names, matches the manifest's `prior_D023_case` flags and
  `D023_predecessors`, and contains the 3 `template_policy` cases. The six
  non-predecessor cases are exactly the added names.

  | Development disposition | 144 predecessors | 6 additions | All 150 |
  | --- | ---: | ---: | ---: |
  | `retained_native` | 129 | 0 | 129 |
  | `converted_native` | 12 | 0 | 12 |
  | `new_native_refusal` | 3 | 5 | 8 |
  | `new_native_positive` | 0 | 1 | 1 |

- Corpus reading is reader parity, not runtime production.

### P03 ordinary lifecycle and both handoffs

1. Manual T completes M001-S01 with no profile, using its prompt, coded, verify,
   review prompt, record and accept/commit commands.
2. Installed `run` refuses `fresh dispatch requires an autonomy profile` with
   no effects.
3. The owner declares the profile at that settled history. The only new event
   is the owner's `run_admitted`.
4. F `preflight` (two probes) and `run --once` issue M001-S02.
5. Manual T completes that F-issued round with unchanged run balances and no
   seat.
6. F completes M001-S03 with its coder, frozen verification, native receipt,
   routed review, acceptance and Git commit. It consumes T's artifacts.
7. Holistic closure first refuses `holistic outside current grant boundary`
   with no effects, because R001 covered only S02/S03.
8. The successor grant R002 supersedes R001 with its charges preserved. F
   closes the milestone.
9. A restart is idle, and `status --usage` reports no unresolved attempts.
10. F and T status text agree at each handoff, and the T check passes.

### P04 boundaries

- **No-history shapes.** All-planned, all-done and done-plus-planned are idle.
  Status reports `no active milestone\nnext: none`, and a done label adds
  `warning: M001 declared done without ledger closure` (absent for all-planned).
  Each command's exact result, with empty stderr and no effects after each:
  `run --json` exits 1 with `{"action": "stop", "reason": "internal", "detail":
  "LedgerError: profile requires current grant; owner must inspect"}`;
  `preflight` exits 0 with `preflight: ok` (no active route, so no probe); a
  repeated `run --json` gives the first result again. No attempt or job
  directory exists.
- **Last active completion (idle row 01).** M001 is planned and M002 active.
  Under grant R001, `run --until milestone` completes M002 and stops with
  `milestone completed; mark roadmap milestone done separately`. The owner then
  relabels M001 active only to record manual blocked history on it (T prompt and
  T `blocked`, both by the architect) and returns its label to planned. Status
  shows `M001-S01 r1 blocked` beside `M002-S01 r1 accepted`. The next run stops
  with `current grant boundary completed`, with no effects and no dispatch of
  the blocked slice. A separate planning edit (M002 done plus T `roadmap
  render`) changes exactly `docs/roadmap.md` and `roadmap.yaml`, with ledger,
  HEAD and index unchanged. After the owner commits it, the blocked history is
  unchanged and a run again stops at the grant boundary with no effects.
- **Label-independent confirmation (idle rows 15 and 08).** Accepted manual
  non-holistic work with no profile is confirmed under active, planned and done
  labels: `run --json`, its repeat and `run --json --until milestone` each exit
  0 with exactly `all admitted roadmap milestones completed`, empty stderr, no
  effects and no attempts. Status is exactly `M001-S01 r1 accepted`, then
  `no active milestone` for planned/done, `next: none` and the completed commit
  intent; no done warning appears because ledger closure exists.
- **Frontier.** M001 is closed under R001 while active, ungranted M002 is never
  dispatched. The completed-grant restart is non-writing.
- **Reopen.** A T reopen of M001-S01 takes precedence over M002 (`next:
  M001-S01`) and funds nothing. A done label on the unaccepted reopened slice
  refuses `inactive run history requires completed milestone and settled
  attempts`.
- **Correction.** Grant R002 corrects and recloses M001.
- **Relabels.** Done-plus-never-admitted-planned is idle and unfunded. Relabelling
  the admitted M001 as planned refuses in F status and T check with
  `run admission requires active milestone`.
- **Strict structure (idle row 02).** A committed planned roadmap with an empty
  milestone list, an empty slice list, an invalid milestone ID or an invalid
  slice ID refuses at load, with no effects. F `status` exits 1 with
  `internal: RoadmapError: <errors>; owner must inspect`. F `run --json` (twice)
  and `preflight` exit 2 with one preflight row or line per error. T `roadmap.py
  check` and `ledger.py check` exit 2 with the same errors on stderr. The invalid
  milestone ID also makes its unchanged slice ID fail
  (`milestones[0].slices[0].id must match invalid-Snn`); the other shapes give
  exactly the source test's single error.
- **Unfinished work under a done label (idle row 04).** Manual unprofiled T
  history stops at `verifying` (prompt, coded), `reviewing` (plus T verify),
  `accept_pending` (plus the T review prompt and recorded pass review) or
  `blocked` (T `blocked`, recorded as a `blocked_authority` coded event). Only
  `roadmap.yaml` is then relabelled done and committed. F and T status are
  exactly `M001-S01 r1 <step>`, `no active milestone`, `next: none` and the done
  warning. `run --json` exits 3 with `needs_specification` / `architect must
  admit the next milestone`, `preflight` exits 0 without a probe, and the repeat
  run matches. The history, attempts (none) and complete snapshot are unchanged,
  no job directory exists and T check passes. Unstarted, coding and fix remain
  covered by the no-history shapes, P11 and the frontier reopen.
- **Holistic close pending (idle row 09).** A manual holistic milestone has
  M001-S01 accepted and committed through T. T `prompt.py --holistic M001`
  issues the review; T `record --milestone M001` records the pass report
  (`M001 holistic pass`). With M001 relabelled done, F and T status are exactly
  the accepted slice, `no active milestone`, `next: none`, the completed slice
  commit intent, `M001 holistic: close_pending` and the done warning. Run and
  preflight give the same results as row 04, with no `milestone_done`, no effects
  and a passing T check.
- **Relabel accounting (idle row 12).** The four pinned planned-refusal autonomy
  cases are folded by the isolated installed reader from real ledger files.
  Under an active label each gives the source test's exact R1 closed state
  (`None`, `boundary`, `overrun`, `superseded`), consumed jobs and seconds, zero
  retries, remaining allowance, revision and head; the superseded case has head
  R2 and R1 revision 1, and the unclosed case keeps `I1` unfinished. Under the
  pinned planned label the same unchanged bytes refuse exactly `run admission
  requires active milestone`. No coding workflow or corpus edit is involved.

### P05 exact recovery

- **Case grid.** The approval is interrupted in eight ways: slice and milestone
  scopes × four boundaries, alternating caught exception and real owner death
  (exit 137).
- **Restart.** Before staging and after staging, an unattributed in-flight
  marker stops the fresh public process with no effects (`ValueError: prior
  Git dispatch is unresolved; architect must attribute its exit`). The T reader
  also refuses until attribution. T `recover --git-resolved` with human
  attribution archives the marker, and the unmodified public restart then
  completes exactly once.
- **After commit creation.** The T reader already reports `completed`, and the
  public restart recognizes the commit rather than redoing it.
- **What is preserved.** There is one approval and one completed intent per
  scope, with no repeated seat, verifier or charge, and unique commit subjects.
  Ignored owner files stay unstaged and byte-exact. The only status entry is
  F's derived `backlog.md`, which T reconcile reproduces exactly.
- **Unrelated tracked content.** Dispatch admission is clean; a caught fault
  before recovery leaves one pending intent. The owner then appends to tracked
  `docs/glossary.md` (unstaged, with a CRLF line) and to tracked
  `docs/front_repo.md` (staged).
  - The public restart exits 2 with one `unrecorded change` preflight row per
    file and no effects.
  - The supported recovery boundary is the final T owner command. Both
    `recover --execute` and read-only `recover` exit 2 with `error: unrelated
    staged paths; index preserved: docs/front_repo.md`; the snapshot and that
    index entry are unchanged.
  - After the owner unstages the file, the intent is still pending. T `recover
    --execute` exits 0 with the intent `completed` at one new commit. That commit
    changes `07_app/result.txt` and no `docs/` path; both docs files keep the
    owner's exact bytes, unstaged, with HEAD and index equal to their originals.
    Attempts and seat counts are unchanged; F and T status show the commit
    completed.
  - F still refuses the unrecorded edits identically. Once the owner commits
    them, the intent stays completed and replay is idle.
- **Later commits.** A later owner commit keeps every earlier witness
  completed.
- **Cancellation.** T `cancel` records `commit_cancelled`. F then creates no
  fresh approval or commit, and leaves the index and files preserved.

### P06 blocker and no-progress

- **Typed blocker.** A `blocked_authority` outcome keeps the coder's edits and
  makes one durable stop; a repeat is non-writing. T `resolve` with committed
  authority triggers round 2, whose envelope keeps acceptance, read_first,
  allowed/forbidden prefixes, full, focused and runtime, and cites the
  authority path. Charges match the attempts.
- **No-progress.** Two identical no-change verification failures stop with
  `identical product and verification failure`, with exactly two coders, two
  verifications and no third prompt.

### P07 findings and checkpoints

- **Partial routed review.** A release-risk review checkpoints the first
  reviewer, then stops with `run transport_retries allowance exhausted` on the
  second reviewer's transport failure. A changed or missing checkpoint report
  refuses with no effects (`immutable evidence drift` / `missing regular
  evidence`). After a T `run extend`, only the failed review is dispatched
  again.
- **Uncommitted extension authority.** The extension's authority file is
  recorded by reference and left uncommitted. In development, committing it
  between verification and review moved HEAD, which F correctly refused with
  `HEAD/index changed after verification`.
- **Findings across slices.** One grant covers M001-S01..S03. The S01 round-1
  review opens F1 (P2) and carries F2 and F3 (P3). The S01 round-2 review closes
  F1. The later S02 review closes S01's F2: its report contains the update row
  naming S01's original report path, that report's SHA-256 and the ID. T
  reconcile reproduces F's backlog bytes, in which both findings are
  `closed_by_review` with that exact source and hash.
- **Unrelated pass.** The S03 report has no update section; F3 stays the single
  `carried` row.
- **Later accepted ownership (idle row 06).** S03 rewrites S01's product path,
  so HEAD holds the later accepted owner's bytes. A housekeeping commit and a
  committed owner hotfix follow; a run is `[done]` with no effects, F and T
  status agree, and T check passes.
- **Waivers.** A human waiver records through T `--by human`. T refuses the
  architect with `override or waiver requires --by human`. A reviewer seat's
  waiver is rejected as `autonomous seats cannot grant human waivers`, which F
  feeds back in the format-repair prompt. It ends with `review format retry
  allowance exhausted`, with no review or acceptance recorded.
- **Holistic block.** A holistic block is resolved by T; the holistic finding
  is corrected and closed by the slice reviewer, then the milestone closes.

### P08 large and literal evidence

- **Workflow.** 720 generated literal-name paths (spaces, Unicode, brackets,
  braces, leading dashes, `!`, `^`, `~`) are combined with a worktree rename, a
  deletion, a defect-free 12 MiB `07_app/huge evidence.txt` and a separate small
  `07_app/zz late small defect.txt` after it in canonical order.
- **Seats never stage.** The shared fake's `git mv` rename was stopped with
  `path_violation: seat changed HEAD or index` during development, so the
  harness rename is worktree-only.
- **Reviewer.** In each round the scripted reviewer reads every bound path in
  canonical order, in full, verifying each against its stored normalized hash.
  The huge file precedes the late file. Round 1 finds the defect only in the
  late file and opens one finding; the round-2 correction passes and closes it.
- **Bounds.** At least 723 paths and more than 12,000,000 bytes are verified per
  round. The manifest is sorted, has at least 723 paths and more than 40,000
  characters, records the deletions and additions, and binds the late file at
  round 2 with the corrected bytes' hash. Review prompts are at most 48 KiB.
- **Commit.** The approval commit contains every approved path and no ignored
  file, and its late file is exactly the corrected bytes. `git grep` with a
  positive control over the 12 MiB blob shows the defect token absent from
  `07_app`. The only status entry is the derived backlog, which T reconciles
  identically.
- **Empty selection.** A coder that changes nothing yields an empty
  `frutlups.manifest/2`. F verifies, accepts and commits only bound evidence
  and prompts (`05_governance/`, `prompts/`); the run is `verified`, `accepted`
  (at the new HEAD) and `done`. Replay is idle and T check passes.
- **Unrelated tracked staged/unstaged content.** Covered in P05 at the supported
  T recovery boundary.

### P09 output and usage

- **Pi flood.** Pi emits more than 9 MiB before final settlement. The retained
  stdout is capped at 8 MiB while the full stream is observed, and the coder
  completes.
- **Usage.** Complete scripted usage yields a `complete` slice row. Probe usage
  from the shared fakes remains truthfully `partial`.
- **Canaries.** A reviewer report carrying either a secret-named environment
  canary or an absolute scratch path is never rewritten into an accepted
  report. F stops with `review output contains private text; inspect retained
  streams` and records no review. The reviewer attempt stays unresolved for
  owner inspection. The canary appears in no Git-visible file, ledger row or
  CLI output; the raw output remains only in ignored local job streams.
- **Scrub utility (utility evidence).** Separately, the isolated installed
  utility driver scrubs a synthetic report containing the canary and a private
  path outside its root. The result contains neither, has `<redacted>` and
  `<absolute-path>`, and parses with identity, round, verdict, the finding row
  and `not_achieved` status intact. This shows grammar-preserving redaction in
  the installed modules; it is not public acceptance of private report text.
- **Pi record limit.** A 3 MiB Pi record stops with `seat_output: coder: Pi
  JSONL record exceeds 2 MiB limit (2097152 bytes; limits.pi_record_mib)`.
- **Claude capture limit.** 9 MiB of Claude raw output fails the reviewer
  attempt. Its bound `result.json` records `failure_class` `output` with
  `Claude raw stdout exceeds 8 MiB capture limit` and null tokens, and F stops
  with `review failure needs external inspection`.
- **After either refusal.** Each failed attempt keeps `unknown` usage, nothing
  is accepted, and a repeat run is non-writing.

### P10 integrity and hashes

- **Identities.** LF, CRLF, lone CR, NUL and 64 KiB chunk-boundary bytes are
  retained raw in the worktree. Manifests store normalized evidence hashes,
  distinct from raw hashes where CRLF occurs. A clean `git clone` of the
  approved commit reproduces every normalized identity.
- **Prompt fallback.** A custom coding template omits most placeholders. The
  issued prompt still binds the complete frozen envelope by path and hash and
  states the typed-outcome rule, and the envelope retains the acceptance and
  prefixes. The dispatched prompt the fake received carries the outcome
  contract, and both are within 16 KiB.
- **Non-Python verification.** `git rev-parse --is-inside-work-tree` is
  recorded in the native receipt.
- **Same-status drift.** Each refuses before review, with no effects:
  - content drift that is normalized-equal: `raw product changed after
    verification`;
  - skip-worktree index flag drift: `HEAD/index changed after verification`;
  - HEAD movement: `HEAD/index changed after verification`.

  `NUL`-containing evidence uses a non-reserved name, because `nul.*` is a
  Windows device name.

### P11 ownership and identity

- **Live owned child.** While the fake coder is genuinely alive, a second
  public writer refuses `another writer owns the repository; status remains
  available` and a T mutation also refuses; status still works.
- **Owner death.** The killed owner's seat dies with it. `run` and `preflight`
  exit 2 with exactly `preflight: unresolved invocation prevents admission`, with
  no effects, and the coder is never redispatched.
- **Unsettled done label (idle row 13).** Relabelling that milestone done makes
  F status exit 1 with exactly `internal: LedgerError: inactive run history
  requires completed milestone and settled attempts; owner must inspect`, with
  no effects. Back on the active label, status names the unresolved invocation
  and usage reports one unresolved attempt.
- **Hostile PATH.** A shadowed or missing `PATH` still uses the selected Python
  and configured Git.
- **Invalid selections.** Each refuses with no fallback and no effects:
  - missing configured Git;
  - missing selected Python;
  - the same valid grant history under a custom ledger path (`runner admission
    requires the canonical ledger`);
  - roadmap runtime 3.99.
- **STOP ordering.** STOP refuses before pending recovery. A pending intent with
  a passing checker recovers exactly and dispatches nothing new.

### P12 grants and attempts

- **Refusals.** Missing grant, exhausted grant, wrong-scope grant, changed grant
  authority, legacy roadmap schema and an unknown ledger schema each refuse with
  an exact row before any probe, job, seat, verifier or ledger effect.
- **Old receipt schema.** A real T-recorded coded round is followed by a
  controlled `frutlups.receipt/1` verification receipt, bound by its correct raw
  hash. This is negative evidence only. Exact results, each with no effects,
  no verification attempt and no job directory:
  - `run --json` exits 2: `verified: receipt identity mismatch`;
  - `preflight` exits 2: `preflight: verified: receipt identity mismatch`;
  - `status` exits 1: `internal: LedgerError: verified: receipt identity
    mismatch; owner must inspect`;
  - T `check` exits 2: `error: verified M001-S01: unsupported legacy receipt;
    use pinned v4`.

  Two controls show this is the schema check, not a hash failure. The same
  bytes under a wrong recorded hash give `verified: immutable evidence drift:
  <receipt path>`. With only the schema value changed to `frutlups.receipt/2`,
  F status folds the round as `reviewing`. T check refuses `invalid receipt
  fields`, and F `run` refuses `project checker refused the project` with no
  effects.
- **Wrong identities (reused, not duplicated).** Wrong artifact identity: P01.
  Changed or missing checkpoint report: P07. Assessment hash mismatch and wrong
  authority: P13. Changed grant authority: the refusal case above. Grant
  supersession: P03 step 8.
- **Reservation.** The fake coder reads its own durable, unfinished reservation
  before acting.
- **fsync fault.** A named fsync fault after the seat leaves the attempt
  unresolved and charged. The restart refuses, and the coder is not repeated.
- **Retry and extension.** A transport retry, then a T extension, preserve
  prior consumption and do not repeat completed work.
- **Missing evidence and unknown usage.** Bound job results are not optional:
  moving the job evidence away makes `status --usage --json` exit 1 with
  `missing regular evidence: local_state/frutlups/jobs/...`, and no usage row is
  synthesized. With the evidence restored, the retry whose seat reported no usage
  leaves the slice `partial`, never `complete`, and the remaining jobs equal the
  extended allowance minus every charged attempt (no refund).
- These are process tests; no power-loss durability is claimed.

### P13 history

- **Building the history.** The final T route produces round 1, a blocked
  review, an owner `resolve`, and round 2 with coder notes. T's actual verifier,
  run with an owner `--timeout`, records a passing receipt that exceeds the
  frozen one-second deadline.
- **Strict path.** Installed `run` refuses `accepted receipt exceeded frozen
  deadline`. Each invalid selection refuses exactly, with no effects:
  - a half pair: `must be given together`;
  - a hash mismatch: `raw hash mismatch`;
  - an assessment naming the wrong authority: `authority is not a resolution
    member`.
- **Adopted pair.** With the owner-adopted pair, installed `preflight` passes and
  `run` closes the milestone. Historical evidence bytes are unchanged and run
  balances match the attempts.
- **New work.** A later F-dispatched slice under the same pair still has its own
  verification time out, so it is not accepted. T check passes on the resulting
  history.

### P14 continuity and usefulness

- **Continuity.** At the end of the session, the installed payload, both wheels,
  the archive, the manifest and the corpora are rehashed, and the installed
  version is inspected again.
- **Usefulness.** The summary described under [Command log](#command-log)
  separates actual commands, durable seat and verification time inside public
  F, and synthetic totals.
- **Interpretation.** No speed target is asserted.

### Idle-row map

Source rows are the 15 rows of `docs/idle_contract.md`. "Component" names the
existing private test there and is not installed proof.

| Row | Installed node and assertion | Component only |
| --- | --- | --- |
| 01 | `test_p04_last_active_completion_preserves_earlier_planned_blocked_history`: blocked history preserved through last-active completion and two runs; separate edit changes exactly two planning files | none |
| 02 | `test_p04_no_active_shapes_are_idle_and_non_writing[all-planned, all-done, done-plus-planned]`: `no active milestone`, exact per-command results, no effects; `test_p04_r3_idle_strict_structure[no-milestones, no-slices, milestone-id, slice-id]`: exact load errors in F and T, no effects | none |
| 03 | `test_p04_frontier_reopen_relabel_and_planned_authority`: reopen gives `next: M001-S01`, completed grant funds nothing, M002 never dispatched | explicit envelope selection: a private-only option with no public analogue to add |
| 04 | all-done shape (unstarted slice with done label is not completion); frontier reopen (`fix`) and P11 row 13 (`coding`, unresolved) refuse; `test_p04_r3_idle_unfinished_done[verifying, reviewing, accept_pending, blocked]`: exact status, `needs_specification` run, no effects or attempts | none |
| 05 | P12 `run_admitted: immutable evidence drift`, old-receipt hash control and `missing regular evidence`; P07 checkpoint drift; P05 `unrecorded change`; P10 drift refusals | `ledger.check` `drift: <path>` text; the earlier public integrity refusals establish the no-effects property |
| 06 | `test_p07_findings_closure_carry_and_unrelated_pass`: later accepted overlapping owner, housekeeping, committed hotfix, idle `[done]`; P05 later owner commit | none |
| 07 | P11 owner death (`unresolved invocation prevents admission`) and STOP before pending recovery; P05 pending intent recovered before any dispatch | unknown targets and approval commit-flag values: private-only options with no public analogue to add |
| 08 | `test_p04_manual_completion_confirms_independently_of_label` repeats; P05 and P08 replays: snapshot-equal | template `close --commit` spelling (template-only; F adds no close command) |
| 09 | P03 holistic grant-boundary refusal then closure; P05 milestone-scope recovery; P07 holistic block resolution and correction; `test_p04_r3_idle_holistic_pending`: `M001 holistic: close_pending` in F and T status with no active milestone, safe run, no effects | none |
| 10 | no-history shapes: done warning iff a done label lacks closure; row 15 status has no warning with closure | none |
| 11 | frontier done-plus-planned replay (`[GRANT_DONE]`, no effects); row 15 manual completion with no attempts; planned relabel refuses `run admission requires active milestone` | none |
| 12 | P02 fold of the six added autonomy cases through both readers; frontier planned relabel refusal; `test_p04_r3_idle_relabel_accounting`: active-form accounting of the four pinned planned-refusal cases, then their exact planned refusal on unchanged ledger bytes | none |
| 13 | P11 unsettled done label (exact refusal); frontier done label on incomplete reopened slice; P02 `done-history-requires-holistic-closure` | none |
| 14 | frontier done-plus-planned; P02 `completed-done-plus-never-admitted-planned` | none |
| 15 | `test_p04_manual_completion_confirms_independently_of_label`: exact `done` result under active/planned/done; row 01 run shows `mark roadmap milestone done separately` | `roadmap already done` boundary text: a private branch, publicly unreachable with valid history because F dispatches only an active milestone; the public done result suffices |

The six added autonomy cases in P02 are `no-active-planned-admitted-history-refuses`,
`completed-run-cannot-become-planned`, `overrun-run-cannot-become-planned`,
`superseded-run-cannot-become-planned`, `done-history-requires-holistic-closure`
(all `new_native_refusal`) and `completed-done-plus-never-admitted-planned`
(`new_native_positive`).

### Original D023 test and invariant map

`docs/native_conformance.md` inventories 64 original D023-baseline functions.
Each falls into one group. Partial entries name exactly what the installed
evidence shows; everything else in that function stays component coverage.

- **Installed P02 corpus assertions.**
  - `test_protocol_v2.py::test_base_event_corpus` and
    `test_autonomy.py::test_profile_case`: both readers fold each of their
    cases against its oracle.
  - `test_protocol_v2.py::test_base_corpus_has_all_sixteen_event_cases` and
    `test_autonomy.py::test_corpus_identity_and_case_counts`: the original
    corpus invariants above.
  - `test_protocol_v2.py::test_legacy_prefix_and_writer_preserve_crlf`, a
    retired legacy positive: the `legacy-prefix-new-suffix` template-policy
    refusal.
  - `test_protocol_v2.py::test_v2_strict_timestamp_fields_and_references`
    (partial): the `noncanonical-v2-extension-timestamp` refusal.
- **Installed public observations in this module.**
  - Evidence hashes (`test_evidence_sha_normalizes_text_but_not_nul_bytes`,
    `test_evidence_sha_matches_template_script`,
    `test_evidence_sha_hashes_git_blob_bytes`): P10 identities and clean clone.
  - `test_check_reports_immutable_artifact_and_notes_drift` (partial): P07 and
    P12 `immutable evidence drift`.
  - `test_status_text_matches_template_script` and
    `test_next_slice_matches_template_on_same_ledger`: F/T status text equality
    at every `status_pair`, including `next:`.
  - `test_roadmap.py::test_next_slice_prioritizes_sticky_reopens_then_active_order_then_none`:
    P04 reopen priority.
  - `test_roadmap.py::test_milestone_status_rules_match_template` (partial): P04
    shapes and relabels.
  - `test_holistic_artifacts_and_milestone_done_after_acceptance`: P03 and P07
    holistic closure.
  - `test_corrective_prompt_count_and_reopen_flag_clear_on_accept` (partial): P04
    reopen correction and P06 round 2.
  - `test_read_names_line_for_strict_failures` (partial): P12
    `line 2: invalid schema or event`.
  - `test_cli.py` (each partial):
    - `test_once_outputs_one_transition_and_resumes`: P03 `--once`.
    - `test_until_overrides_configuration`: P03 `--until slice`, P04
      `--until milestone`.
    - `test_status_and_usage_conform_to_template`: P03/P09/P12 status and usage.
    - `test_internal_error_is_exit_one_json`: P12 exit-1 rows.
    - `test_preflight_failure_classes_exit_two`: P11/P12 exit-2 rows.
    - `test_preflight_only_probes_active_routes`: P04 idle `preflight: ok` with
      no probe.
    - `test_preflight_success_and_configuration_loaded_once`: P03 two-probe
      success only.
    - `test_preflight_collects_independent_refusals`: P05's two independent
      `unrecorded change` rows.
    - `test_stopped_run_names_reason_and_human_step`: P06 stop rows.
    - `test_commit_failure_reports_slice_and_internal_exit`: P05 caught faults
      exit 1 `internal`.
    - `test_default_root_and_unknown_usage`: P01 no-argument usage, exit 2.
- **Component only.**
  - `test_protocol_v2.py`: `test_oversized_record_uses_bounded_read`,
    `test_ledger_requires_utf8`.
  - `test_roadmap.py`: `test_load_refuses_each_template_validation_error`,
    `test_loads_fixture_roadmap_as_frozen_model`,
    `test_render_markdown_matches_template_script`,
    `test_slice_size_warning_does_not_refuse_load`,
    `test_v2_retains_runtime_profile_and_verification_declarations`,
    `test_strict_v2_declarations`.
  - `test_ledger.py`: `test_fold_table_rows`,
    `test_legacy_and_v2_event_types_are_declared`,
    `test_optional_cost_preserves_numeric_value`,
    `test_cost_refuses_invalid_numeric_values`,
    `test_append_folds_before_write_and_preserves_bytes_on_refusal`,
    `test_append_writes_lf_and_fsyncs`, `test_append_loads_adjacent_roadmap_by_default`,
    `test_fold_rejects_invalid_scope_timing_transition_and_round`,
    `test_check_accepts_products_restored_to_head_or_absent_from_head`,
    `test_check_accepts_crlf_working_copy_of_lf_blob_like_template`,
    `test_check_reports_product_drift_from_latest_and_head`.
  - `test_fixture_integrity.py`: all three archival checks. P01 checks the
    final export instead.
  - `test_cli.py`: the four usage-attribution tests,
    `test_main_drops_rows_on_broken_stdout`,
    `test_preflight_names_each_missing_windows_variable`,
    `test_preflight_probes_each_distinct_claude_model`,
    `test_preflight_probe_plan_preserves_roles_and_keeps_only_failed_job`,
    `test_auth_refusal_retains_streams_and_scrubs_tail`,
    `test_unsupported_options_rejected`,
    `test_claude_missing_auth_verb_uses_read_only_probe`,
    `test_prompt_and_stop_rows_survive_cp1252_stdout`,
    `test_holistic_artifact_rows_label_the_round`, and
    `test_v2_public_runner_and_append_refuse_before_effects`. The last predates
    native public execution; refusal before effects is shown by P12.

## Results and measured limits

This document is part of the final candidate bytes, so it cannot contain the
final run's own result. Each round's single final execution (round 2: the whole
module, `r1/targeted_argv.json`; round 3: the four new idle functions,
`r3/targeted_argv.json`) is retained as external evidence for the architect to
import: actual and portable argv, literal node outcomes, streams, the phase
journal, witnesses, package hashes, `installed_pair_timings.jsonl` and
`installed_pair_summary.json`. Neither scoped selection is the full run.

Development evidence only, not a gate. Round 1's final selection passed 54
nodes. Round 2 adds six nodes (60 in the module), and one complete development
run of the 60 nodes under the phase observer passed (about 59 minutes). Round 3
adds ten nodes in the four `test_p04_r3_idle_*` functions (70 in the module);
their observed development selection passed in about 90 seconds.

| Measurement | Development value |
| --- | --- |
| Session install | about 10-17 s |
| Simple F/T status or check | about 1-3 s each |
| Full slice cycle (probes, coder, verification, review, acceptance, commit) | about 50-75 s |
| Holistic closure | about 70 s |
| P08 large workflow (two rounds) | about 200 s |
| Complete 60-node module | about 59 min |

Individual fake-seat and verifier operations take 0.1-2 s. The remainder is
combined product, checker, Git and process overhead, not a causal profile. No
timeout or retry was raised in response to a failure.

Product limits are unchanged. The harness's 600-second per-command guard is a
test guard for incidental setup only. Deliberate limit cases use their own
small values: 1-second verification, 2-job and 0-retry grants, and the 2 MiB,
8 MiB and 1 MiB caps.

## Not qualified here

- Live providers, model quality, delegation and the real Pi loader. These and
  Q12's final live portion belong to M007 under separate authority.
- Minimum Python, POSIX, native symlink privileges, sleep/resume and power-loss
  durability.
- Arbitrary helper scale, escaped orphans and two-hour reliability.
- No M009 speed promise is inherited or implied.

Inherited private tests support the rationale but do not count as installed
evidence. The 1890 baseline nodes (the original 1830 plus the 54 round-1 and 6
round-2 additions) are retained; the hermetic full run that discovers them
together with the round-3 nodes is architect-owned and pending.
