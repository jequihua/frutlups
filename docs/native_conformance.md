# Native lifecycle retirement and conformance inventory

## Final distribution adoption (M008-S07 candidate)

D092 and answered Q055 admit M008-S07 as a fixture/test/documentation slice on
F `ae14bf4a52a021e8f5bc9ea50b85d36b98b0d55a`. It binds the accepted final T
product `711bce067eb0b3d05abf8cdec54f3cc812f62a5d` (T M004-S01 round 1,
accepted 2026-10-03T15:08:25Z). No production source changes. This section is a
candidate account: the architect-owned full run, independent review and F
adoption are pending, and no finding below is closed by it.

| Final artifact | Bytes | Raw SHA-256 |
| --- | ---: | --- |
| `tests/fixtures/v5_final/template-5.0.0.tar` | 460800 | `a8d3764d87804e0ae9b880f02a4f53592e553dc53c2d24d51b22d6a7bf4ec04a` |
| `tests/fixtures/v5_final/protocol_v2.json` | 25323 | `59a19d634ccfbd8d8c37616196cb59974b06310b168810e6ba71fda3b8a39891` |
| `tests/fixtures/v5_final/autonomy_v1.json` | 343766 | `ca8d6a2b159bb3f468ce725ab0645cd6153338bdb70a19f58027ab16a1c63150` |
| `tests/fixtures/v5_final.manifest.json` | 141317 | `c533c82f3e3d66f35ed6140d76554adbee0a9c2eb1cad55e2e5c2c292acf9c0c` |

The archive's pax comment is the full T commit. It has 69 regular members
(389,077 bytes, tar mode `0664`, source Git mode `100644`) and 23 directory
entries (mode `0775`), with no links. `v5_final/project/` holds exactly the 69
regular members, written member by member after validation; the corpora sit
beside it as separate same-commit artifacts. Older pins (`v4_project`,
`v2_project`, `v5_development`, `template_4632a891`, `v5_selector_9fff0cd`,
`v5_verification_de35316`, historical eligibility) are unchanged.

### Distribution, source and development membership

| Inventory | Files | Meaning |
| --- | ---: | --- |
| Distribution export | 69 | `template-5.0.0.tar` regular members; no `tests/` subtree |
| Final committed source | 85 | the 69 plus 16 source-only paths: `tests/fixtures/{protocol_v2,autonomy_v1}.json` and 14 T test modules |
| Development source `bc4e9d1` | 83 | the adopted S01 tree; final source adds `docs/glossary.md` and `docs/walkthrough.md` |

Distribution members equal their final source blobs. The source-only corpora are
delivered separately and equal the development corpora byte for byte. Neither
count describes missing runtime files. Because the export carries no tests, F
collection needs no new exclusion and `conftest.py` is unchanged.

### Development-to-final delta (33 paths)

The full `bc4e9d1` to `711bce0` source delta has 33 paths. The last release
commit (`e55cdef` to `711bce0`) changes only `README.md` and `pyproject.toml`;
that release step is not the whole delta.

| Group | Paths | Account |
| --- | --- | --- |
| Release identity (2) | `README.md`, `pyproject.toml` | Package name/version become `agentic-project-template` 5.0.0; the only last-release-commit changes |
| Configured Git selection (2) | `scripts/_common.py`, `scripts/_git.py` | `TEMPLATE_GIT_EXECUTABLE` selection, refusal and argv bounds; accepted T M002-S04 contract consumed by F M008-S04 |
| Selector and evidence presentation (3) | `scripts/_evidence.py`, `scripts/_findings.py`, `scripts/prompt.py` | Normalized-evidence hash labels, review/report rules and historical resolution context; accepted selector/presentation work (T M002-S03/M003-S01; F S02/S05) |
| Documentation and instructions (17) | `AGENTS.md`; `docs/{autonomy,contracts,front_repo,ledger,memory,method,operating,project_checks,upgrading}.md`; added `docs/glossary.md`, `docs/walkthrough.md`; `initialization/{architect,coder,reviewer}.md`; `prompts/templates/{coding,review}_prompt.md` | Markdown text only; reference data in F, never workspace instructions |
| Development tests (9) | `tests/test_{commit_v2,integrity_v2,project_checks,prompt_dependability,prompts_v2,protocol_v2,remediation,scaffold,verification_v2}.py` | T qualification only; not distributed and not imported or run by F |

Against the DEC02-tested checker pin `4632a8915eb03859381ed2a97fc8e408c3814c39`,
only `_evidence.py`, `_findings.py` and `prompt.py` differ among the 16
distributed scripts. `ledger.py`, `_common.py`, `_git.py`, `_commit.py`,
`_protocol.py` and `_integrity.py` keep their pinned bytes. The manifest records
every delta path with its group, and
`test_fixture_integrity.py::test_final_distribution_source_and_development_delta_accounting`
recomputes the delta from the development and source inventories.

### Old and current case map

Both final corpora are byte-identical to the development corpora that M008-S06
and M008-S03 consumed. No case is added, removed or changed. Every one of the
150 cases keeps its whole-case SHA-256, target, validity, owner and disposition,
and the per-case tables in the historical sections below still apply to these
cases.

| Corpus | Cases | Shared native | Template policy | D023 predecessors | Native reader node |
| --- | ---: | ---: | ---: | ---: | --- |
| `protocol_v2.json` (`template.conformance/3`) | 16 | 14 | 2 | 16 | `test_protocol_v2.py::test_base_event_corpus[<case>]` |
| `autonomy_v1.json` (`template.autonomy-conformance/2`) | 134 | 133 | 1 | 128 | `test_autonomy.py::test_profile_case[<case>]` |

| Consumer input | Before S07 | S07 candidate |
| --- | --- | --- |
| Native case source | `v5_development/tests/fixtures/<corpus>` | `v5_final/<corpus>` (same bytes) |
| Reader roadmap | `v5_development/roadmap.yaml` | `v5_final/project/roadmap.yaml` (same bytes) |
| Parametrization | original D023 corpus names, plus six idle additions for autonomy | unchanged; node IDs unchanged |

A final case name missing from the native mapping fails with `KeyError`. Nothing
falls back to the development copies. New nodes
`test_protocol_v2.py::test_final_native_corpus_is_pinned_and_covers_every_predecessor`
and `test_autonomy.py::test_final_native_corpus_is_pinned_and_covers_predecessors_and_additions`
derive membership from the actual final corpora. Every expected field, head,
membership and exact diagnostic assertion is unchanged. The three
template-policy cases (`legacy-prefix-new-suffix`, `illegal-version-reversal`,
`legacy-nonattempt-prefix-is-preserved`) keep their policy classification: the
F reader refuses them, which is not template-policy execution. All 144 D023
predecessors map to the same-named current case, with original dispositions.
Manifest `coverage.cases` records each row's `final_reader_node` and
qualification.

These modules still read `v5_development` for the roadmap or initialized
project: `conftest.py`, `test_ledger.py`, `test_receipt.py`, `test_render.py`,
`test_roadmap.py` and `test_native_retirement.py`. They were outside the S07
write boundary and are not final-pin qualification. The roadmap bytes are
identical in both trees.

### Evidence ownership

| Claim | Evidence | Owner and status |
| --- | --- | --- |
| Final T acceptance, archive/corpora identity | T M004-S01 reviews and acceptance; `02_analysis/m008_s07_admission/r1/intake.json` and `assessment.md` | T and F architect intake; inherited, not new F execution |
| Exact pins, safe membership, modes, clean Git materialization, delta and case accounting | `test_fixture_integrity.py::test_final_*` and `::test_every_final_member_survives_clean_git_materialization` | M008-S07 coder candidate; independent review pending |
| Native reader/fold on the actual final corpora | `test_protocol_v2.py`, `test_autonomy.py` | M008-S07 candidate; private F only |
| Representative final-export private integration | `test_final_template_v2.py` (below) | M008-S07 candidate |
| Full package on final bytes | one uncapped architect-owned `full_gate.py --execute` with the unchanged maintained verifier | Architect, pending; shared with independent review |
| Installed pair, public /2 dispatch, live use | frozen wheel/export pair; integrated candidate; separate live authority | M006-S02, M006-S03 and M007, pending |

`test_final_template_v2.py` defines a local `checker_pin` that verifies the
pinned manifest and archive, extracts the 69 members to session scratch and
checks them against `v5_final/project/`. The accepted `checker_project`,
`owned_history` and `managed` helpers then run on that final tree with no
fallback to the 4632a891 pin; each story asserts the final script hashes. The
accepted test functions are reused through module-qualified calls, and none is
imported into collection:

1. `test_final_checker_read_and_profile_free_dispatch_refusal`: the real T
   checker runs under ownership with the selected Python, configured Git,
   canonical `--root`/ledger and a finite timeout. Profile-free fresh dispatch
   refuses with no job, provider or witness effect.
2. `test_final_configured_git_shadow_and_both_template_routes` reuses the
   managed application-directory shadow experiment for both T Git routes.
   `test_final_invalid_supplied_git_refuses_without_path_fallback[...]` puts a
   working Git on PATH as a control, then shows that relative, missing,
   directory and `.cmd` selections refuse in both helpers rather than falling
   back.
3. `test_final_historical_selection_absent_valid_and_invalid`: an invalid
   selection refuses with no effects at both private entries. Absent selection
   gives strict historical refusal. A valid selection is consumed once, and the
   final T checker accepts the unchanged emitted artifacts afterwards.
4. `test_final_pending_recovery_and_exact_replay`: a genuine pending approved
   commit refuses an invalid selection, then runs the checker before exactly one
   recovery. Replay adds no commit, ledger byte, attempt, run or job.

Strict current-operation deadline, settlement and refusal tests stay in their
accepted modules, unchanged.

### Verification and limits

The coder runs focused checks only, plus one final four-module selection under
an enclosing cap of min(1800 seconds, remaining coder seconds minus 300). The
architect alone runs one uncapped full on final bytes with the reused
`02_analysis/m008_s07_admission/r1/full_gate.py --execute`. D092 supersedes only
S07's old 2400-second full cap. The 600-second metadata validation does not
execute tests. Runtime, checker, Git and operation deadlines are unchanged.

POSIX, the full minimum-Python range, native symlinks, sleep/resume, installed,
public and live behavior remain unqualified. Windows cannot independently prove
POSIX mode behavior. Historical usage and interrupted-run dispositions stay
unknown where they were unknown. M008-S06-01, M008-S01-R2-01 and every other
finding keep their recorded dispositions.

## Current S08 candidate qualification

D071-D073 admit native-only lifecycle retirement after S01 and S06 at
`26daadb268d714a47700d5864af86ebff7279f1d`. The candidate accepts lifecycle
roadmap/2, ledger/2 and receipt/2 only. Unsupported, legacy-only and mixed
histories refuse directly. Independently versioned autonomy/1, config, local
runtime and commit-manifest formats retain their own versions.

Public prepare/run/Loop construction/iteration/general append remain disabled
until M006-S03. Maintained integration tests use native bound scratch evidence,
real private operations, actual ownership, grants, attempts, verification,
review checkpoints and commit recovery. Neither these tests nor the immutable
case inventory establish public or installed-pair qualification.

Current implementation references use named functions rather than historical
line numbers:

| Contract | Maintained source | Qualification |
| --- | --- | --- |
| Native input and schema-neutral grammar | `roadmap.py:Roadmap/_validate`; `ledger.py:_parse_event/_validate_base/validate_v2/fold` | legacy/mixed/unknown refusal; shared actor/field/order/round invariants |
| No executable lifecycle fallback | `receipt.py:run/write/_execute`; `render.py:coding_envelope/coding_v2/review_v2/holistic_v2`; `verdict.py:parse` | native receipt and bound presentation only |
| Public refusal before job effects | `_preflight.py:admission`; `loop.py:Loop.__init__/iteration/append`; `ledger.py:append`; `ownership.py:writer_lock` | `tests/test_native_retirement.py` |
| Native private lifecycle | `loop.py:_coding_slice/_verifying_slice/_checkpoint_review/_holistic_v2/_approval` | scoped private integration checks; architect full gate pending |
| Durable usage authority | `cli.py:_usage` | native attempt finishes; local result files are not fallback authority |
| Baseline node and corpus accounting | `docs/native_retirement_coverage.json` | individual candidate accounting; no count-only safety claim |

The issued eight-module focused command passed 491 tests during S08 r3,
including the explicit 1,317-node accounting check. The seven incident tests
also passed, including isolated archive installation and genuinely fresh-process
native recovery. CLI, loop-evidence and restoration selections passed 136 tests;
the retained checkpoint/milestone/commit/evidence/findings/backlog/verification
selection passed 199 tests. These are scoped results, not full-package qualification. The architect alone owns the unchanged
uncapped `full_gate.py --execute` full run on the final candidate. Its saved
execution must retain stdout head/tail, raw phase journal, all-module discovery,
terminal child disposition and a stable witness. Default `full_gate.py` validates
saved evidence; its receipt must describe that validation command. The finite
600-second metadata allowance does not cap the actual full run. Independent
review and full-package qualification remain pending.

The complete 150-case pinned development inventory remains below as historical
S01 accounting. Current inherited native execution uses the corresponding
pinned native corpus in `test_protocol_v2.py` and `test_autonomy.py`, with the
two former legacy-positive cases explicitly refused. The six new idle/history
cases remain assessed M008-S03 obligations, not silently skipped runner passes.
Selector/frozen issuance-field qualification remains M008-S02 work. The three
template-policy targets remain policy classifications, not runner passes.

## Finding references and historical qualification limits

This refresh addresses the stale-reference concerns in **M008-S06-01**, original
report `05_governance/reviews/m008/M008-S06_r1_review.md`, and
**M008-S01-R2-01**, original report
`05_governance/reviews/m008/M008-S01_r2_review.md`, for independent review.
Only the reviewer can close these findings. The source excerpts below remain
labelled provenance at the original S01 base; removed source text and its old
line numbers do not describe current code.

S01 completion was accepted as two-session evidence under D068: its first full
invocation timed out and retains `ok=false`; a fresh continuation recollected
1,257 nodes and completed the 203 nodes without complete prior phases. That
historical timeout is not rewritten as a successful uninterrupted run. The first
session lacks terminal session-level skip/exit evidence, and the final 203 ran
in a fresh process, so uninterrupted accumulated-process behavior is unproved.
The old 2,400-second cap and pending-gate wording below quote the superseded S01
plan. They do not impose a cap on S08 or imply S01 remains unaccepted. Minimum
Python, POSIX, native symlinks, sleep/resume, installed/public and live behavior
remain unqualified here.

## Historical S01 inventory (preserved provenance)

The following original inventory describes the S01 intake and its then-current
implementation/verification plan. All headings, source locations, future owners
and pending statements in this historical section refer to that snapshot unless
explicitly superseded by the current section above.

M008-S01 intake of the complete 83-member development source tree at
`bc4e9d1290119f54ec62d9e231826a68c2eea34c`. This is **fixture integrity and
coverage accounting only**. No current-runner native parity, installed pair,
production dispatch, live qualification or final release export is claimed.
All dispositions below are future implementation obligations, not passing tests.
Existing maintained tests and historical fixtures remain unchanged in meaning.

## Source and authority

D066 assessed the inputs; D067 and the r2 handoff admit S01. D065 parks M009
incomplete and permits native feature work after M005 closure. D040 alone is
planning authority. D037-D039 bind TD D013-D016/source009 policy; DEC03-I is
still pending. The historical register remains frozen under answered Q025.

Controller evidence is relative to the repository root under
`02_analysis/m008_s01_admission/`. Input hashes use raw bytes; evidence hashes
normalize CRLF only for non-NUL files. Never substitute these hash domains.
The r2 preflight binds 205 inputs. Development source members are listed in
[`v5_development.manifest.json`](../tests/fixtures/v5_development.manifest.json),
with raw SHA-256, size and Git mode, separately from old and final-export pins.
The archive identity is `609491befcf046e1ee006fc7652918a4a161a97c64cf76be68ce65a9f564ebde`
(1,136,640 bytes); all 83 members are regular mode `100644`.

| Admission input | Raw SHA-256 |
| --- | --- |
| `r1/source_manifest.json` | `4f1664abf830a98ee7ff33a6f2bac7be30afa1adbc74e3f09bd5add34aba8251` |
| `r1/case_inventory.json` | `37a1207c35cf335cb7145d15a010fa2466cf0b84162359f5a7251341f9a02216` |
| `r1/D023_test_inventory.json` | `f805414edb9b2018338dd36737432bd4c293c07933fb24747028d8ea50620e21` |
| `r1/runtime_inventory.json` | `6a260d0ffc841ec0c8918db1c6a0e211422911c13775e536979af2c116174523` |
| `r1/contract_bindings.json` | `36ca6e462f6fb4daf7a40f0ecaa24ef29cc4d017de9dd0094c4998c2c61ef8ee` |
| `r1/policy_bindings.json` | `e70562bbd0f597948635f2b4bc8bfea5816d27bd87cbb07b5c7ca2643be19a2b` |

The manifest preserves the complete admitted case inventory, including exact
non-event setup/oracles and a canonical hash of each whole case (sorted JSON
keys, compact separators, UTF-8). The immutable embedded corpora contain the
actual events. Its `coverage.cases` entries are keyed by corpus/name; no missing
oracle is replaced with a default. `coverage.D023_cases` preserves predecessor
mapping. Source and coverage pins are independently asserted by maintained tests.

## Accepted development packets and immutable evidence

These are four separate source commits, not a mixed source tree. Only S02 is
materialized here. The bound r1 `contract_bindings.json` records the review,
receipt and checkpoint identities; those records are existing external evidence,
not new F executions or independent review of this intake. Later admissions must
consume the actual packets immutably and assess deltas, without a final-freeze
prerequisite. The r1 assessment supersedes the dated r3/r4 statements that the
selector/Git/presentation packets were still future work.

| Contract | Accepted product commit | Accepted pin raw SHA-256 | Meaning |
| --- | --- | --- | --- |
| M002-S02 | `bc4e9d1290119f54ec62d9e231826a68c2eea34c` | `ae8d9c11169e3f0c6c3dd94d26aaf62aa0d45faee52bbe1ccd6f53154e9bd819` | development contract, not final export |
| M002-S03 | `9fff0cd06048adb864b119cf8e7d24d54916074f` | `c3bb755cf2e636c7d0f7f1120e7e6c6c1b60db5969ffc8e381a720c241fa8384` | development contract, not final export |
| M002-S04 | `4632a8915eb03859381ed2a97fc8e408c3814c39` | `90fe25018ba3764c27bb29de1eb5756fbee9e284dbdc90b760fc291b3851d415` | development contract, not final export |
| M003-S01 | `de35316c1f7db81c4ee255ca546f1f3291b41776` | `d8f202418df271793dc8b6440f3581456b0815215bd9dd5a111ea19beb8770e1` | development contract, not final export |

| Bound TD evidence path | Raw SHA-256 |
| --- | --- |
| `04_delivery/maintenance/convergence/contracts/M002-S02/accepted_pin_r1.json` | `ae8d9c11169e3f0c6c3dd94d26aaf62aa0d45faee52bbe1ccd6f53154e9bd819` |
| `04_delivery/maintenance/convergence/contracts/M002-S02/packet_r1.md` | `a018c465a035e223fe254de9751b971d08b30324c09569e98189bca1446005f6` |
| `05_governance/reviews/m002/M002-S02_r1_review.md` | `c141569f75404adaf563b12fcccc03b5a573e07f3bfd01b32a798aa50a4c7825` |
| `04_delivery/maintenance/convergence/contracts/M002-S02/native_case_dispositions_r1.json` | `d06553d570ba3fa8bbb824d932c2f8e246e269eea366ef271a36b4e7656ce672` |
| `04_delivery/maintenance/m002/M002-S02/accepted_checkpoint.json` | `1e8ad8d496a6be0bd889801100bfb81793271188dab34090bb4ee587332d968e` |
| `04_delivery/maintenance/m002/M002-S02/regression_matrix.md` | `b8d9163f2fab92a7413beb07d0670caae0ec8d60eab318e99bf889815afe183e` |
| `04_delivery/maintenance/m002/M002-S02/case_delta.json` | `f28bb2c2a1122dfc69fe542cfc76a7c2bc2410309f64a09f528aa5d76eb7fcd2` |
| `04_delivery/maintenance/m002/M002-S02/command_effects.json` | `8dcb360e5def505c7aef788568de2b13d6ec495dc552fab86d3bbfe676094172` |
| `04_delivery/maintenance/m002/M002-S02/runner_behavior_delta.md` | `f3ad313d8c0b7205c6fbe3ad0754d1f2c4931d236553c1f0596c4c41d23c2e66` |
| `05_governance/reviews/m002/M002-S02_r1_verification.json` | `8ccf8547d8d726c37e4e2c7309e8ece0d89f6077ab9a4250a34f6a8b9d2f88d1` |
| `04_delivery/maintenance/convergence/contracts/M002-S03/accepted_pin_r1.json` | `c3bb755cf2e636c7d0f7f1120e7e6c6c1b60db5969ffc8e381a720c241fa8384` |
| `04_delivery/maintenance/convergence/contracts/M002-S03/packet_r1.md` | `df250b26e7575ba215e8fd4ccc9ecb5c1e6be4077749e1031731d7e923e9a57f` |
| `05_governance/reviews/m002/M002-S03_r1_review.md` | `043206cb834999de77d576adbc6803a5eda8ab0c544a17549e7d314d022732c5` |
| `05_governance/reviews/m002/M002-S03_r1_verification.json` | `53668f61d72a8892ff33aeb6f6eb4859a5e12456472b2f8dd3f49216e0c2f901` |
| `04_delivery/maintenance/m002/M002-S03/accepted_checkpoint.json` | `9267884094c14f4e73a0060e892df73e977e1f1fc42f17116f712c1523a4e181` |
| `04_delivery/maintenance/m002/M002-S03/shared_issuance_cases.json` | `193a8230cfbaef2a5082c706e3027ffcb2e42ea25abb6189ee926e2323826f8c` |
| `04_delivery/maintenance/m002/M002-S03/field_consumer_map.json` | `91e211c66a3523b5cbf761c8fcb4d3a2e576568868a18b63d46c77d2bdb9be37` |
| `04_delivery/maintenance/convergence/contracts/M002-S04/accepted_pin_r1.json` | `90fe25018ba3764c27bb29de1eb5756fbee9e284dbdc90b760fc291b3851d415` |
| `04_delivery/maintenance/convergence/contracts/M002-S04/packet_r1.md` | `c8e32db4e65f13767fa4cfcc5d02b30ebfeb2042deb5006127e9e8013ffbaa32` |
| `05_governance/reviews/m002/M002-S04_r1_review.md` | `87f8b8cbb51ac27f3d7ba8524a5ae7a9bce19d63a203763a17a122abf4f2f407` |
| `05_governance/reviews/m002/M002-S04_r1_verification.json` | `a1157c0c53af1b30c00cdc99686053a2bd718ef1dadc62c61eb517cbd023b0f4` |
| `04_delivery/maintenance/m002/M002-S04/accepted_checkpoint.json` | `baa2d0ab75fb0e40b038a5c6a97767a82423313c3abd79adde60642fbec1b85b` |
| `04_delivery/maintenance/m002/M002-S04/git_interface_cases.json` | `f983e529f1b79165dd3a54bb406d4ad37569358616e45613670a54467292f171` |
| `04_delivery/maintenance/convergence/contracts/M003-S01/accepted_pin_r1.json` | `d8f202418df271793dc8b6440f3581456b0815215bd9dd5a111ea19beb8770e1` |
| `04_delivery/maintenance/convergence/contracts/M003-S01/packet_r1.md` | `325de1868e8b98456a633f73d05435e35265fe7f5618ad921365f221814e443e` |
| `05_governance/reviews/m003/M003-S01_r1_review.md` | `472630b6d49da4d5a362c900a973c87a2b2bd18c00e558868f3faf015f47bcd8` |
| `05_governance/reviews/m003/M003-S01_r1_verification.json` | `14310401b4d512405d37bc0c4d562518e1a9d00f604d00d950b9c54ad44adbee` |
| `04_delivery/maintenance/m003/M003-S01/accepted_checkpoint.json` | `97e93f85ef54d17ee81243c2379a3e141d5c80c75aa39921e2ae9772e80f7104` |

Policy snapshots are pinned at TD `b00e80fd4795b84fb6329f64bc6971f01bd04f7a`:

| TD policy path | Raw SHA-256 |
| --- | --- |
| `00_brief/decisions.md` | `de5a90e6269def8ae29e7fea550e7337014b02dff8820936ba01dc525b831c05` |
| `00_brief/intake_material/009_convergence_policy_and_git_interface.md` | `83e15bcea643bb09dd4cb2ee46968e1d72193f8e4f686241dfa37aadbdfd1c36` |
| `02_analysis/DEC01-DEC03_decision_synthesis.md` | `f4a683d278b9ac3c2c4f07faf74f5d2cfb63124131a3b551b1fa6718cd6a2fed` |

## Consumer obligations and latest gates

| Obligation | Owner and latest gate | Required evidence still pending |
| --- | --- | --- |
| Native grammar and legacy retirement | M008-S06, before consumer qualification | Native reader/fold independent of legacy schema coercion; explicit policy refusals; preserve every invariant below. Assess missing ownership.py, cli.py and verdict.py scope before issuance. |
| Selector and all 14 issuance fields | M008-S02 admission consumes accepted T M002-S03 | Actual rendering, runtime selection, write fence, frozen command and review consumers; no implicit activation of planned work. |
| Idle/completion and six additions | M008-S03 consumes accepted T M002-S02 | All 15 rows of the bound regression matrix; exact diagnostic/state and raw files/ledger/HEAD/staged-entry oracles; then installed public proof in M006-S02. |
| Configured Git/checker and handoff | M008-S04 consumes accepted T M002-S04; F proof before T M004-S01 export admission | Actual selected Python/Git, canonical ledger, explicit cwd/environment, finite checker timeout, both helpers, argv bounds, shadow/invalid-selection refusal, held ownership and recovery before new work. |
| Presentation | Accepted T M003-S01 bound at affected admissions | Preserve accepted M005 producer/rendering work; integrate native labels/custom rendering and complete evidence; requalify material deltas. |
| Historical eligibility DEC03-I | Joint design review before M008-S05 or any earlier affected validator edit; implemented proof before T M004-S01 | Exact receipt/envelope/resolution/authority binding, prospective scope/cap and single-use accounting; shared amendment if existing structure is insufficient. Keep current refusal. |
| Final distribution | M008-S07 after T M004-S01 | Separate export/corpus pin, deliberate development-to-final delta and clean materialization. |
| Public /2 integration | Separately assessed M006-S03 | Construction, prepare, run, iteration and append guards remain enabled until actual integrated candidate qualification. No bypass or disconnected executor. |
| Installed pair and live use | M006-S02 after reviewed integration and wheel/export freeze; M007 separately authorized live smoke | Same exact artifacts, actual opposite-tool consumption, fresh-process recovery, both operator directions, exact charges and zero-effect refusals. |

Profile-free manual/read/check/status stays supported; autonomous dispatch needs
a declared profile and a valid scoped grant. Manual work neither charges nor
refunds runner allowance. Preserve reservations, unknown attempts and stop rules.
Independent `frutlups/1`, `frutlups.local/1`, `template.local/1`,
`frutlups.autonomy/1` and `frutlups.commit-manifest/1` remain versioned as-is.
The three historically authorized deadline exceptions retain their exact
identities; D039 does not authorize hard-coded exceptions or acceptance bypass.
No existing finding is closed, waived or relabelled by this document.

## Complete current case map

| Corpus | Schema | Cases | Shared native | Template policy |
| --- | --- | ---: | ---: | ---: |
| protocol_v2.json | template.conformance/3 | 16 | 14 | 2 |
| autonomy_v1.json | template.autonomy-conformance/2 | 134 | 133 | 1 |

All 144 original names survive. Against F D023, 127 autonomy cases preserve
semantic contents apart from target labels; the legacy-prefix case changes.
The upstream packet's unchanged-128 statement compares its earlier T native
baseline, not F D023. The six new cases belong to M008-S03. Five base cases
change event payloads. Two legacy positives retire in favor of native refusal;
`illegal-version-reversal` retains refusal with the new exact diagnostic.
All three template-policy cases require native reader and later public
no-effect refusal; none is a shared-native runner pass.

Each row's exact invariant is its `coverage.cases` oracle plus all source events
under `tests/fixtures/v5_development/tests/fixtures/<corpus>` and the named case.
The canonical case hash binds both. Future consumers must assert each expected
state field, head, membership and diagnostic, and apply every declared setup
field. In particular, the old autonomy adapter hardcodes the second milestone
active: it must honor `second_status` during S03, not count setup failure as the
intended negative result. No consumer adapter is changed in S01.

All rows are **pending consumer execution**. S06/S03 is the component owner;
M006-S02 is the installed owner for every row. Integrity checks are separate.

| Corpus / case (manifest oracle key) | Target | Disposition | Consumer | Changed semantic fields | Whole-case SHA-256 |
| --- | --- | --- | --- | --- | --- |
| `protocol_v2.json / legacy-prefix-new-suffix` | template_policy | new_native_refusal | M008-S06 | error, expected, valid | `ab29764e3aa77428c1b39e0a48b46b4fa2c7468475f6ed8fe70cebecc48fd882` |
| `protocol_v2.json / blocked-authority-resolution` | shared_native | retained_native | M008-S06 | none | `352dd0850e8e49bbd2ad03d9a5f19d6978b926d1319ed27afd55c4523c59ce1a` |
| `protocol_v2.json / completed-seat-unknown-usage` | shared_native | retained_native | M008-S06 | none | `432301ba31350e130611d63e6cbfb868867850bae07e6ee08c993f5a621de964` |
| `protocol_v2.json / holistic-blocked` | shared_native | converted_native | M008-S06 | events | `aafa5ad46a97ade769710d8dcd6caf0032b37196fc2d9bcb05a366babb713f06` |
| `protocol_v2.json / holistic-needswork` | shared_native | converted_native | M008-S06 | events | `098f3da7b86a305450b8a56818cad34de430a68e9738e6612fc48c6f51fe3bf9` |
| `protocol_v2.json / holistic-pass-explicit-close` | shared_native | converted_native | M008-S06 | events | `df130f8057584e105ce35028489c36aafdda94f6f38157df519737b2c2af8833` |
| `protocol_v2.json / optional-approval-intent` | shared_native | converted_native | M008-S06 | events | `01595964bd945b227b7fd7eeceef9a5e18d6ca5947d3780ff7afcee424b1b4ec` |
| `protocol_v2.json / illegal-version-reversal` | template_policy | new_native_refusal | M008-S06 | error | `0f369adc18e28b2931e3d61bfdd05968c356618601e3173dd0e657b42a407126` |
| `protocol_v2.json / unknown-output-contract` | shared_native | converted_native | M008-S06 | error | `cc35135bffb542c4e0710922566b46f5b942d8f2045e97efa4bdf7e401a125dd` |
| `protocol_v2.json / unresolved-invocation-cannot-redispatch` | shared_native | converted_native | M008-S06 | error | `c7664758a889c546ae422118c4a2d966d31032281b322f264ddbfa63126feb8c` |
| `protocol_v2.json / checkpoint-cannot-change-round` | shared_native | converted_native | M008-S06 | error | `adf182ad8e352c14f0ff063052caca8dd80a36857bb4fc5c5cd228a00b821bf9` |
| `protocol_v2.json / nonhuman-retains-acceptance` | shared_native | converted_native | M008-S06 | error | `b13acd14114801b755d56c3d2665878355baba85137cd0ad16ed168b3e7f98fd` |
| `protocol_v2.json / holistic-disabled-reader-refusal` | shared_native | converted_native | M008-S06 | error, events | `3b988ce7d90ca1484c209dada659ba4f4d9e7af9e72397296dd4ef82f0a4bdad` |
| `protocol_v2.json / v2-unbound-unblocked-refusal` | shared_native | converted_native | M008-S06 | error | `0cba2db6197604574dd33a8c41f675cbc195db077843d3f12915fa6ddcdcd5f4` |
| `protocol_v2.json / unknown-ledger-v3` | shared_native | converted_native | M008-S06 | error | `3d258e6652c7b4d6c601c2afcb3251dbaf3dde888650eceacef65a0cf04885b4` |
| `protocol_v2.json / noncanonical-v2-extension-timestamp` | shared_native | converted_native | M008-S06 | error | `e1f7dfa69a99aa66311a8caf5c871c827c01d63020e9915e73a6e6e40422fdf1` |
| `autonomy_v1.json / admission-is-the-only-initial-allowance` | shared_native | retained_native | M008-S06 | none | `5ba9a444ac4c2a6a6bbe90edf442e65867e9e4aad497c477c617ff2c8aab0506` |
| `autonomy_v1.json / no-admission-no-start` | shared_native | retained_native | M008-S06 | none | `d2b1f71d03fba4920d611fce1024229d6e8f741ae74fc95dc9f2ed7c8de1dede` |
| `autonomy_v1.json / completed-duration-with-partial-provider-telemetry` | shared_native | retained_native | M008-S06 | none | `d37b25674f174e345bdae826dddf1beb4fc962ab75a5e85486c75104ee0da629` |
| `autonomy_v1.json / completed-missing-duration-keeps-reservation` | shared_native | retained_native | M008-S06 | none | `f13be667201a0fbd727a80e44c3608f3ddbadb1451a964e2ab2d8f268393d79d` |
| `autonomy_v1.json / failed-short-duration-does-not-refund` | shared_native | retained_native | M008-S06 | none | `902b3d0e7f426bfcc2a18b3ec9184dfdab553f2243bfcb211b55cea744216dcf` |
| `autonomy_v1.json / resolved-unknown-keeps-reservation` | shared_native | retained_native | M008-S06 | none | `6a5b277833307315b9757b64c359e215c901747586653e0b308b4693e9380af7` |
| `autonomy_v1.json / duration-rounds-up-deterministically` | shared_native | retained_native | M008-S06 | none | `708ff668e0ca8a1481f350ac0c7793212d5b321cfe7da656cd6d426c3c89f8aa` |
| `autonomy_v1.json / unfinished-start-debits-before-launch` | shared_native | retained_native | M008-S06 | none | `6db9ee13cef65de1e36821067c20210e377836ba4362cec4b68e09d383b0612f` |
| `autonomy_v1.json / truthful-overrun-finish-closes-run` | shared_native | retained_native | M008-S06 | none | `79912935891aad4bb2200a8f6f268da597afbe47926fd261f238e067eb3f49e2` |
| `autonomy_v1.json / failed-overrun-is-also-visible` | shared_native | retained_native | M008-S06 | none | `581c77cb108e945cebd6ce0bd16d42d3be41c9c4aeb5261b4c09a0cb0bc4c336` |
| `autonomy_v1.json / overrun-forbids-extension` | shared_native | retained_native | M008-S06 | none | `e1f3721ddde3fb053a084b425884e877560bf76984a65307bf2fa72707235b7f` |
| `autonomy_v1.json / reservation-cannot-exceed-remaining` | shared_native | retained_native | M008-S06 | none | `fe6ab0d01e6519edba48d6c1d9afb27fc2ea6b0e70dc3672c48beeb681a792a9` |
| `autonomy_v1.json / clip-final-reservation-to-three-seconds` | shared_native | retained_native | M008-S06 | none | `1f19cca3dabb623288277faeb59f3fce72b8facb0e9fce082f562ce5f182996e` |
| `autonomy_v1.json / exhausted-jobs-no-new-start` | shared_native | retained_native | M008-S06 | none | `9d9395e4e5e7aa8f98f917f59aa8de21ad3c793d114845151c098422639f2516` |
| `autonomy_v1.json / exhausted-time-no-new-start` | shared_native | retained_native | M008-S06 | none | `ce3c0814473331a7064a265ff6820be536ef1f358ea6f17d56e7331035278310` |
| `autonomy_v1.json / valid-extension-preserves-charge` | shared_native | retained_native | M008-S06 | none | `872a2691806864168606d28f56dfabbdf4547d986a7017e3cfe9b65bdd81f477` |
| `autonomy_v1.json / duplicate-extension-revision` | shared_native | retained_native | M008-S06 | none | `b9c1f070d912c8c44c1649bead57bafa49f81c134567f19d60ebdcbbb1e676d9` |
| `autonomy_v1.json / skipped-extension-revision` | shared_native | retained_native | M008-S06 | none | `39a2a6246116f4281df4119435133878a034506847c2b37a754a7d52ea3e7302` |
| `autonomy_v1.json / frutlups-cannot-grant-extension` | shared_native | retained_native | M008-S06 | none | `24ed46bb13d725b4e441cbedd6bb9f15b83e8ea92b1ae892878efb9c6f9c76a9` |
| `autonomy_v1.json / undelegated-architect-cannot-extend` | shared_native | retained_native | M008-S06 | none | `c4f61b3ae2fd010cd6cbadbb8da86b59912769d4cb0173d7965c7f9e002d6ed0` |
| `autonomy_v1.json / delegated-architect-can-extend` | shared_native | retained_native | M008-S06 | none | `5aa249a1bfce4f9481b515f58bc352d221b7467f5ee1ed358073e3755103ccb0` |
| `autonomy_v1.json / frutlups-cannot-admit` | shared_native | retained_native | M008-S06 | none | `ac873d03ae0a3c270d73b547bd2985ba973b09794286944c4974435a0da1cac0` |
| `autonomy_v1.json / empty-authority-cannot-grant` | shared_native | retained_native | M008-S06 | none | `12aa7ed0c0652655b0eeaaffd77e4922401c0d259a1dbea34b71e3b1f7ffedce` |
| `autonomy_v1.json / duplicate-authority-refs-refuse` | shared_native | retained_native | M008-S06 | none | `e74755c312a9015de750c3b044e9047344643e374840264291cac2061ff71b16` |
| `autonomy_v1.json / same-authority-set-cannot-grant-again` | shared_native | retained_native | M008-S06 | none | `8c70a9ab67c40e5ad8cfb43f37c5dc2c3a33a7f88c0b0696bec679836935a195` |
| `autonomy_v1.json / reordered-authority-set-cannot-grant-again` | shared_native | retained_native | M008-S06 | none | `7b32ddc0cffa2f4371a008b9a6cd2b1fb8e0c604ec8704dcad41bff361ee33ed` |
| `autonomy_v1.json / duplicate-run-id` | shared_native | retained_native | M008-S06 | none | `a71c9c696b4f99a00ff3851c05a5d861763e19db269fcc89da1d364ce49f87d4` |
| `autonomy_v1.json / unknown-extension-run` | shared_native | retained_native | M008-S06 | none | `89477b409661051b8210d3f53c521c57a5857063edc177b0b9098ddd2d4a1323` |
| `autonomy_v1.json / extension-milestone-must-match` | shared_native | retained_native | M008-S06 | none | `2a117b3a8a67574255ad2953081ba29f9ffeca16c55da0329233b52855684f24` |
| `autonomy_v1.json / successor-head-must-match` | shared_native | retained_native | M008-S06 | none | `3475c3aa84b7599f231ab050d5754fb31bac08b44dedbd077a014e6769c80ed2` |
| `autonomy_v1.json / successor-explicitly-supersedes` | shared_native | retained_native | M008-S06 | none | `d6858b5aa8545819ed258d2374c739d436ec7e49ec5b3639fe24f8f5c13a1fe4` |
| `autonomy_v1.json / superseded-run-cannot-extend` | shared_native | retained_native | M008-S06 | none | `a7b611bd1e5cf8c591524701efb8e73ca0a0c6b8ed1761156bd46765ed153001` |
| `autonomy_v1.json / new-run-cannot-bypass-unfinished-operation` | shared_native | retained_native | M008-S06 | none | `d4fc656c315c505d0285503dfec5871002c5e50ca33e55c04dcf4384281ff6b2` |
| `autonomy_v1.json / extension-cannot-overlap-unfinished-operation` | shared_native | retained_native | M008-S06 | none | `d2c6f68dfa3c370fed8a92057cb12b56c9b25ba2fcea86ef6598a56e4ccd92ab` |
| `autonomy_v1.json / verification-reserves-seconds-not-model-jobs` | shared_native | retained_native | M008-S06 | none | `3328bc99be886026de821ee4be151a6a0c05696d0e3e34d6156016b340b32627` |
| `autonomy_v1.json / verification-completion-settles-time` | shared_native | retained_native | M008-S06 | none | `34e385c48604bdd6acec5a07bc161e86efacad2e494344c4e5581852aa175ad3` |
| `autonomy_v1.json / verification-requires-verifying-state` | shared_native | retained_native | M008-S06 | none | `fbd78c82a34ab9bef97975bdd656746a86239cf646dd2f30b59193b2cfded9f1` |
| `autonomy_v1.json / verification-requires-current-round` | shared_native | retained_native | M008-S06 | none | `55185887924097b1918e02ee3bb853d4a0d2f0dbe920c08e46dc8bd89cc9a799` |
| `autonomy_v1.json / verification-global-overlap-blocks-model` | shared_native | retained_native | M008-S06 | none | `b56a5f2dd3e37bf7b9e9580802fee204b449c135e432e6fa771b3b2a57acf6de` |
| `autonomy_v1.json / model-global-overlap-blocks-verification` | shared_native | retained_native | M008-S06 | none | `898016da389c4a006af58f5d32f49867b17f3f95ceedbc2a999a495bd0b0de58` |
| `autonomy_v1.json / outside-run-milestone-refuses-during-invocation` | shared_native | retained_native | M008-S06 | none | `d7a88d0612d1143d85284f6e81b50bf793b0bf0b9b2c77e33bbf02a4a682b35d` |
| `autonomy_v1.json / verification-role-needs-profile` | shared_native | retained_native | M008-S06 | none | `8c0b56d98f31c263d94cb31485a6324cf91ae53448beb2fec5366a1dabb5bd32` |
| `autonomy_v1.json / profile-removal-does-not-erase-run` | shared_native | retained_native | M008-S06 | none | `6a6baf3b8fb61df8a64e9832c16f69fbfa1c52dfcc8f0496257f97613026b5a6` |
| `autonomy_v1.json / pre-admission-attempt-history-migration-refuses` | shared_native | retained_native | M008-S06 | none | `7a58ee30d1413dcf0d9bae62b1f4be7be782fb04a3a89ec822638649e3dcda48` |
| `autonomy_v1.json / base-attempt-history-remains-valid` | shared_native | retained_native | M008-S06 | none | `1cc940faf819b373038db9428b794f1e0b71f7669c1ceef1762c6cf38e723e53` |
| `autonomy_v1.json / legacy-nonattempt-prefix-is-preserved` | template_policy | new_native_refusal | M008-S06 | error, expected, valid | `6aff53633d9eb353b3fce5d75208ba433de1116e30d5895c13b5f6fa812c7194` |
| `autonomy_v1.json / v2-nonattempt-prefix-is-preserved` | shared_native | retained_native | M008-S06 | none | `fe768a6d13ac1cec474970763c2007d804f9a5b39f530e9dfd9b4abed3048e1a` |
| `autonomy_v1.json / transport-retry-of-retry-refuses` | shared_native | retained_native | M008-S06 | none | `d57b20ff79aa127cb55b989f60bcc9af052abe5040701c10f89afb0ef2c8bcc2` |
| `autonomy_v1.json / format-retry-of-retry-refuses` | shared_native | retained_native | M008-S06 | none | `652449fc6fdb410c663b674661ccfab3b49f37bdbdf20e104ebfe4606be75313` |
| `autonomy_v1.json / distinct-retry-units-charge-independently` | shared_native | retained_native | M008-S06 | none | `a404c3c41a72c05614f4c1af592c967daf02b349f50a02fb904d5575a8ee3638` |
| `autonomy_v1.json / retry-after-extension-keeps-contract` | shared_native | retained_native | M008-S06 | none | `efe689b1bee18a1999adb2197ff55197daf19cf038d947e5c63b9e80b4621089` |
| `autonomy_v1.json / retry-cannot-cross-run-membership` | shared_native | retained_native | M008-S06 | none | `eb4f1a3e350902d7a442fcfa92f0ada2f4080c16426c70a6009df68c5e11da99` |
| `autonomy_v1.json / initial-relabel-cannot-reset-model-context` | shared_native | retained_native | M008-S06 | none | `ace3192f24aa73450f60dd803d650f0ff1a80a412e5f6f18671f0e1e7bfc9436` |
| `autonomy_v1.json / out-of-selected-slice-refuses` | shared_native | retained_native | M008-S06 | none | `ee47550a987335be8e5823388cb377d86757d1995ab554ed43e06a26456b0a4c` |
| `autonomy_v1.json / selection-must-follow-roadmap-order` | shared_native | retained_native | M008-S06 | none | `c047e2a3e260eb6f0b0f13fa5dcdb6a0f8f1a11f256bdbfb8eff2fee30d28c0b` |
| `autonomy_v1.json / selection-must-not-repeat-slice` | shared_native | retained_native | M008-S06 | none | `dc141440073ba0632687655be6a1f3fca0791b2f7553010e95a85a40667cdc63` |
| `autonomy_v1.json / stop-must-be-last-selected-slice` | shared_native | retained_native | M008-S06 | none | `a2d6505d1ae1524955a28e2a3eef26cff8bb333a970a115420b9a817978ef48b` |
| `autonomy_v1.json / nonholistic-milestone-needs-slice-stop` | shared_native | retained_native | M008-S06 | none | `b2117a3f302340aef88ceeb6d641e08b03f8129d0eb41fc1ca4f5d4fbc432e25` |
| `autonomy_v1.json / nonholistic-final-acceptance-closes-run` | shared_native | retained_native | M008-S06 | none | `337422d651bfa98f4e39dbe6c487478f92dbdc5eb81aea660d66bb9c99a86f0b` |
| `autonomy_v1.json / manual-reopen-never-revives-run` | shared_native | retained_native | M008-S06 | none | `191de9c03ef334e80bf7a71700ae863e011d67145167f2582dd2ee0b07b84041` |
| `autonomy_v1.json / reopened-terminal-run-cannot-dispatch` | shared_native | retained_native | M008-S06 | none | `2a3012752016bcad3f7a87f61373d6ff51cd71293abc563950d8b735659c7c62` |
| `autonomy_v1.json / terminal-run-cannot-extend` | shared_native | retained_native | M008-S06 | none | `9f3485197d66a4e8c671b310f134fd74ef792de0921c953f8984848c1e07bc95` |
| `autonomy_v1.json / already-accepted-stop-cannot-be-admitted` | shared_native | retained_native | M008-S06 | none | `38a47b750caef210411fa15672d58eaed6be292a1fc0e4a2144fa41a798c645a` |
| `autonomy_v1.json / unrelated-manual-acceptance-is-not-run-stop` | shared_native | retained_native | M008-S06 | none | `c994ad3d16f5ee71ebd0d35b586d2a93d880d18ced43c1a88f5c5e24f62a0152` |
| `autonomy_v1.json / holistic-close-is-explicit-stop` | shared_native | retained_native | M008-S06 | none | `fab1571fa6029f6d87235715a66c52b5b78d6af6e346a113484e4ac12245ce00` |
| `autonomy_v1.json / slice-acceptance-does-not-close-holistic-boundary` | shared_native | retained_native | M008-S06 | none | `f08dfabcd4e14243cc7d22f1ce253674bd5d2c690e7505e0f55266309b6b5242` |
| `autonomy_v1.json / corrective-ceiling-persists-in-fold` | shared_native | retained_native | M008-S06 | none | `c81c7e614132780792946fd9ba38aabe81767859754b35f1a21df93ff84a1161` |
| `autonomy_v1.json / new-run-does-not-reset-corrective-consumption` | shared_native | retained_native | M008-S06 | none | `22b6171ad243ec02720e9acbe5365e5a07e82ea45d93b7fa2cb94034285b994d` |
| `autonomy_v1.json / invalid-admission-allowance` | shared_native | retained_native | M008-S06 | none | `931c99e799420594d4e45f922a94a37d556a5d0be967064ebadc2f19c8df0df9` |
| `autonomy_v1.json / invalid-admission-max_corrective_rounds` | shared_native | retained_native | M008-S06 | none | `f01e35da3ad292e20bce2182c61411f5180d3bc0c7e652e7786a2ad68f857183` |
| `autonomy_v1.json / invalid-admission-grantors` | shared_native | retained_native | M008-S06 | none | `02aa3a4e5c6287b55107b7dd75c28b51037171e777d032074069648920fe62d9` |
| `autonomy_v1.json / invalid-admission-previous` | shared_native | retained_native | M008-S06 | none | `0704c74ac088900c8b0d1b1f16b62b1659030d11e0649b1bc52462359fbe13ea` |
| `autonomy_v1.json / invalid-admission-unexpected` | shared_native | retained_native | M008-S06 | none | `2d8a082bde7d2590100cdd054271ea34193d160c5aebd34b91c0e39b9794e84e` |
| `autonomy_v1.json / negative-extension-refuses` | shared_native | retained_native | M008-S06 | none | `2499b8ef4a246c057ab7054b9aed1541d80f8c914f224a55ce6b6020de2daaec` |
| `autonomy_v1.json / zero-extension-refuses` | shared_native | retained_native | M008-S06 | none | `071a60a4fbb5a20840550479d51bf61d0fd388bcb1d1cff3b938e5e68a0b4cc4` |
| `autonomy_v1.json / unknown-allowance-unit-refuses` | shared_native | retained_native | M008-S06 | none | `baf2e9f9de04c7f1df665180a0c04fad0fdadc324e9af5a9f29b1d465a84ebc6` |
| `autonomy_v1.json / slice-stop-does-not-authorize-holistic` | shared_native | retained_native | M008-S06 | none | `b24779493018b78f0795e361ede80df3c7162eb44b37c43ab1c86e052350783e` |
| `autonomy_v1.json / slice-stop-allows-preflight-milestone-probe` | shared_native | retained_native | M008-S06 | none | `2d3ba36626d5f78d397c5fd087ad2f41295bc9cf75f6dc5a28a3a30e959cd863` |
| `autonomy_v1.json / completed-finish-cannot-settle-twice` | shared_native | retained_native | M008-S06 | none | `212eeabf31a3be4c4310073d922a6e99076aa3a01252902ba154354c658eab6c` |
| `autonomy_v1.json / holistic-blocker-stops-selected-slice-dispatch` | shared_native | retained_native | M008-S06 | none | `28352b45ed6205a718f20ceaf2fd53d88a64210fbc1472b0bc4b3fc823879cee` |
| `autonomy_v1.json / initial-coder-requires-issued-prompt` | shared_native | retained_native | M008-S06 | none | `a8ffc1518f013e5dce8bf0b72b8452416f3a066d6da303d5e51c8810a78e7aff` |
| `autonomy_v1.json / issued-coder-round-reserves-valid-job` | shared_native | retained_native | M008-S06 | none | `23ee02f71135ad0ee3f9a42caca7201b040729412298eb6b0719b9e4bb13a4b0` |
| `autonomy_v1.json / coder-future-round-refuses` | shared_native | retained_native | M008-S06 | none | `d5a373ef0fd6ca1535a660ad2f0e744653c34104a72c4141b5b6b0a9e8554cc6` |
| `autonomy_v1.json / coder-verifying-state-refuses` | shared_native | retained_native | M008-S06 | none | `92d520dcda3ce3495bde841f4bc4f1508fc1f35bd5742a6957732945be8d82a7` |
| `autonomy_v1.json / coder-reviewing-state-refuses` | shared_native | retained_native | M008-S06 | none | `5fc3be5417142057ccf233d18f3e97dd7fc4bcff4ce2be93c6ecbeb798beebd1` |
| `autonomy_v1.json / coder-accept-pending-state-refuses` | shared_native | retained_native | M008-S06 | none | `b99db036654c5cb221a272bccd9c9fe7171d510860ddeccd488e40f6f5dfe201` |
| `autonomy_v1.json / coder-accepted-state-refuses-before-holistic-close` | shared_native | retained_native | M008-S06 | none | `c164030263503c8fffec83a3c9cf70a5072e5a715dc99db6c14702d83c271e2c` |
| `autonomy_v1.json / coder-before-corrective-prompt-cannot-bypass-zero-ceiling` | shared_native | retained_native | M008-S06 | none | `c69343797adca921342abe7b13bf05489220b354bf19fbf376536fd1fa94a4a1` |
| `autonomy_v1.json / coder-after-corrective-prompt-still-obeys-zero-ceiling` | shared_native | retained_native | M008-S06 | none | `313aa5cdd6ebd82ac382a53b4d0ad64aaff6d73ea3fb8917d0262135f4cc8ec1` |
| `autonomy_v1.json / coder-stale-round-refuses` | shared_native | retained_native | M008-S06 | none | `ffd6c7ca850be64bd427571fcb6dd65e317c8879041918e46f93dabd7fcc1df2` |
| `autonomy_v1.json / issued-corrective-coder-can-use-existing-ceiling` | shared_native | retained_native | M008-S06 | none | `ae36cc620ce5ac0f0c41dbd6466d203e2e4779ec402546b636fbd3bbebae2543` |
| `autonomy_v1.json / coder-blocked-state-needs-resolution` | shared_native | retained_native | M008-S06 | none | `2074c5e190a99de3fff72967663e1e130988a0f820934eaf482e312599e36768` |
| `autonomy_v1.json / resolved-coder-still-needs-fresh-prompt` | shared_native | retained_native | M008-S06 | none | `71bfadd03c008994d14eaf54b1b28e1d77f3a9abaebfe932bca002a53b6f88a4` |
| `autonomy_v1.json / new-milestone-admission-cannot-bypass-global-unfinished` | shared_native | retained_native | M008-S06 | none | `48b28071d4ebaba428f38d2d59e6bf9b45c7c8f268f3fbe14507e025b6abed61` |
| `autonomy_v1.json / planned-milestone-cannot-be-admitted` | shared_native | retained_native | M008-S06 | none | `95882e5138402e07fc65bd1b4404fe42e6e089c5efae467435079993049df77e` |
| `autonomy_v1.json / done-label-alone-cannot-admit-run` | shared_native | retained_native | M008-S06 | none | `156e6d7d0532f9d6c26a4e9fdddfc9c74f7c1c4f3e078317d5ae4e63822905c3` |
| `autonomy_v1.json / completed-holistic-history-survives-done-roadmap` | shared_native | retained_native | M008-S06 | none | `7b6f7f57cb2c1dd5a94c76bcf098d08ff1ebeefa3725d895ba61c956d8304d62` |
| `autonomy_v1.json / completed-nonholistic-history-survives-done-roadmap` | shared_native | retained_native | M008-S06 | none | `7ccf00c605736e9a90291b01f5aed2f34861829208a6385e358a94133ea104c2` |
| `autonomy_v1.json / done-history-with-unsettled-attempt-refuses` | shared_native | retained_native | M008-S06 | none | `12b23403d3db580db7738142d640dc3b32173a3f4fde690e16984c4d403c7e6a` |
| `autonomy_v1.json / done-history-must-complete-whole-milestone` | shared_native | retained_native | M008-S06 | none | `8d04cb3f846d531ea4182429abe2620183fa0677d5ed87bf0112bbb62adedf1e` |
| `autonomy_v1.json / reviewer-unstarted-state-refuses` | shared_native | retained_native | M008-S06 | none | `701b6b62c72ad64be7132752c2d842933e383d8285a2e9743dd2adf22f28d26c` |
| `autonomy_v1.json / reviewer-coding-state-refuses` | shared_native | retained_native | M008-S06 | none | `5ae740d3761a8b6fc50ab64786801145ce280b9969615d7aa6c753aa703577a0` |
| `autonomy_v1.json / reviewer-verifying-state-refuses` | shared_native | retained_native | M008-S06 | none | `8115e0bebfcc937b7e9144b4988a5fe08bb84f82cd6b3054d5d1105362f8b7ce` |
| `autonomy_v1.json / reviewer-accept-pending-state-refuses` | shared_native | retained_native | M008-S06 | none | `fe1cf6af900ded284e8229b24ae111edceeae71e5cde9da1b1edb0a1c2e4491b` |
| `autonomy_v1.json / reviewer-accepted-state-refuses` | shared_native | retained_native | M008-S06 | none | `3db6fea5ed0d4946c69c60d58a03280fa296653d137494881ada1c39c1ed6f4b` |
| `autonomy_v1.json / reviewer-future-round-2-refuses` | shared_native | retained_native | M008-S06 | none | `cb03782d8596006d1ef36bda92ba5265864810a4a8cd1c42d7e0651faa31a25e` |
| `autonomy_v1.json / reviewer-future-round-3-refuses` | shared_native | retained_native | M008-S06 | none | `792eeaec403af0095fafef02ab2769012e9270a6810a0ca9ba303442256feead` |
| `autonomy_v1.json / reviewer-current-round-and-completion` | shared_native | retained_native | M008-S06 | none | `679655e626b7d7922a72f14818e0086c399bc061fcae3874112aa20ccb6578f4` |
| `autonomy_v1.json / reviewer-valid-transport-chain` | shared_native | retained_native | M008-S06 | none | `02030d9e0d0a1eda7d9eb7442e85b9db9e9b9c205daeb4fe6979566fa7877383` |
| `autonomy_v1.json / reviewer-valid-format-chain` | shared_native | retained_native | M008-S06 | none | `e2203ab0f1f69468d48b82ba2e07d0dc694695daee3860952ca0920129d4de70` |
| `autonomy_v1.json / reviewer-stale-round-refuses` | shared_native | retained_native | M008-S06 | none | `4adcf368630e30dd2f0038c0706370e977289e4afbedb8500b3637c9b0282651` |
| `autonomy_v1.json / reviewer-after-correction-current-round` | shared_native | retained_native | M008-S06 | none | `784583aacb5c2f81ce756144c50d1beb840a48ad2a82a841160370d40cf72edb` |
| `autonomy_v1.json / holistic-unstarted-state-refuses` | shared_native | retained_native | M008-S06 | none | `f02e713b4a05b78089f3eda35a65ecd4398ac04c9687d448acda241f50b39c3a` |
| `autonomy_v1.json / holistic-reviewing-state-refuses` | shared_native | retained_native | M008-S06 | none | `be95dd9ac14535d0f0d483f635868b03ac19499ea6b9e92cfc71bcd7181d8eef` |
| `autonomy_v1.json / holistic-accept-pending-state-refuses` | shared_native | retained_native | M008-S06 | none | `3f655ba909a38065dc676d7fb23b9d985553a4b55bb35f081c514b6893eaf02e` |
| `autonomy_v1.json / holistic-close-pending-state-refuses` | shared_native | retained_native | M008-S06 | none | `9284f7999288023e15e790c26e8e67d8ac196068fb65a60cb15de08aedebe3a2` |
| `autonomy_v1.json / holistic-cannot-review-only-selected-slices` | shared_native | retained_native | M008-S06 | none | `9ec629510e943cd893420fad8e32129a346ef806f8599b67799979102cee42fd` |
| `autonomy_v1.json / holistic-slice-scope-refuses` | shared_native | retained_native | M008-S06 | none | `98233a5b8503c53f62b8cec28081b2ea97dc9ca97e3883b2f8adcb8417efff80` |
| `autonomy_v1.json / holistic-numeric-round-refuses` | shared_native | retained_native | M008-S06 | none | `b6bb582634bf59e237e837b21e00287803ea21fcab1b4218a2d84985f1049953` |
| `autonomy_v1.json / holistic-accepted-milestone-and-completion` | shared_native | retained_native | M008-S06 | none | `7219659456d318d11da3bfee46f747b4c925462fedd70a0abf79c8784ad5dbf6` |
| `autonomy_v1.json / holistic-valid-transport-chain` | shared_native | retained_native | M008-S06 | none | `a5b541af68d72075a5b53bb38d85b526031f8b99afe5a6b3cb1a03f6a0c380c8` |
| `autonomy_v1.json / holistic-valid-format-chain` | shared_native | retained_native | M008-S06 | none | `4ebfdcfcdbb82f795a4b5ce6fd8651a11f8a4f75a95be53043733e1019430029` |
| `autonomy_v1.json / holistic-blocked-reservation-refuses` | shared_native | retained_native | M008-S06 | none | `8b641c670f71208c095ede04cab4f300f8fa7ff76499563154ad69834713646a` |
| `autonomy_v1.json / holistic-resolved-decision-can-review` | shared_native | retained_native | M008-S06 | none | `02c0abef75f0d32e60e0b6ec1b27aa99daca342b6651980c3c20b90466afb9f4` |
| `autonomy_v1.json / holistic-reopened-slice-refuses` | shared_native | retained_native | M008-S06 | none | `b347d1aadd9d887e375bdfbedad74377fa2c669682e1154942acc496ed19f117` |
| `autonomy_v1.json / holistic-after-reopened-slice-acceptance` | shared_native | retained_native | M008-S06 | none | `8684758f024aa667696e55963047581aeb7c25e0cfbaa6474a116921c26b070e` |
| `autonomy_v1.json / extension-cannot-reset-crashed-transport-chain` | shared_native | retained_native | M008-S06 | none | `9a0c3c6304b17d46050dbf2ccbf5e5504e23dc754b213a8f7a08b3565d134fc3` |
| `autonomy_v1.json / done-history-does-not-block-another-active-milestone` | shared_native | retained_native | M008-S06 | none | `8dc540f6743dbeddae1973246f2020496d1f26063e8facdac90af7232cc0d457` |
| `autonomy_v1.json / no-active-planned-admitted-history-refuses` | shared_native | new_native_refusal | M008-S03 | declared, error, events, name, roadmap, valid | `3e5f9edacca33ebfcd348ff9ed00fbdc126e024125f3e7fc63ac183a0ded4835` |
| `autonomy_v1.json / completed-run-cannot-become-planned` | shared_native | new_native_refusal | M008-S03 | declared, error, events, name, roadmap, valid | `fb1f8c3cf3180eddd6597ca47da88af62909f04073b8409373a4f5421e9248e6` |
| `autonomy_v1.json / overrun-run-cannot-become-planned` | shared_native | new_native_refusal | M008-S03 | declared, error, events, name, roadmap, valid | `4f87061fdbd6f9b512864199016238cf7742a4cd1559f169f54c0b35569c2fd0` |
| `autonomy_v1.json / superseded-run-cannot-become-planned` | shared_native | new_native_refusal | M008-S03 | declared, error, events, name, roadmap, valid | `01483b018f9d0ec4e7cb311d88eb5fcfe5ba541e17164660552854edbee02c58` |
| `autonomy_v1.json / done-history-requires-holistic-closure` | shared_native | new_native_refusal | M008-S03 | declared, error, events, name, roadmap, valid | `6a99601f66559b30138252827215f007ea0531e24a50cedda5818a0cbf208e63` |
| `autonomy_v1.json / completed-done-plus-never-admitted-planned` | shared_native | new_native_positive | M008-S03 | declared, events, name, roadmap, valid | `ed0c0e728879b88b280e17024273c787a8c0499927957ea1d9e57e91f1efa768` |

## Every original D023 case

The original corpora remain byte-exact. Retained means an invariant is to be
preserved at the native boundary; retired legacy-positive behavior is not a
pass. The identically named current case above supplies the new exact oracle.

| Original corpus / case | Old valid | Disposition | Current case | Owner |
| --- | --- | --- | --- | --- |
| `protocol_v2.json / legacy-prefix-new-suffix` | true | retired_legacy_positive_replaced_by_native_refusal | `legacy-prefix-new-suffix` | M008-S06 |
| `protocol_v2.json / blocked-authority-resolution` | true | retained_native | `blocked-authority-resolution` | M008-S06 |
| `protocol_v2.json / completed-seat-unknown-usage` | true | retained_native | `completed-seat-unknown-usage` | M008-S06 |
| `protocol_v2.json / holistic-blocked` | true | converted_native | `holistic-blocked` | M008-S06 |
| `protocol_v2.json / holistic-needswork` | true | converted_native | `holistic-needswork` | M008-S06 |
| `protocol_v2.json / holistic-pass-explicit-close` | true | converted_native | `holistic-pass-explicit-close` | M008-S06 |
| `protocol_v2.json / optional-approval-intent` | true | converted_native | `optional-approval-intent` | M008-S06 |
| `protocol_v2.json / illegal-version-reversal` | false | new_native_refusal | `illegal-version-reversal` | M008-S06 |
| `protocol_v2.json / unknown-output-contract` | false | converted_native | `unknown-output-contract` | M008-S06 |
| `protocol_v2.json / unresolved-invocation-cannot-redispatch` | false | converted_native | `unresolved-invocation-cannot-redispatch` | M008-S06 |
| `protocol_v2.json / checkpoint-cannot-change-round` | false | converted_native | `checkpoint-cannot-change-round` | M008-S06 |
| `protocol_v2.json / nonhuman-retains-acceptance` | false | converted_native | `nonhuman-retains-acceptance` | M008-S06 |
| `protocol_v2.json / holistic-disabled-reader-refusal` | false | converted_native | `holistic-disabled-reader-refusal` | M008-S06 |
| `protocol_v2.json / v2-unbound-unblocked-refusal` | false | converted_native | `v2-unbound-unblocked-refusal` | M008-S06 |
| `protocol_v2.json / unknown-ledger-v3` | false | converted_native | `unknown-ledger-v3` | M008-S06 |
| `protocol_v2.json / noncanonical-v2-extension-timestamp` | false | converted_native | `noncanonical-v2-extension-timestamp` | M008-S06 |
| `autonomy_v1.json / admission-is-the-only-initial-allowance` | true | retained_native | `admission-is-the-only-initial-allowance` | M008-S06 |
| `autonomy_v1.json / no-admission-no-start` | false | retained_native | `no-admission-no-start` | M008-S06 |
| `autonomy_v1.json / completed-duration-with-partial-provider-telemetry` | true | retained_native | `completed-duration-with-partial-provider-telemetry` | M008-S06 |
| `autonomy_v1.json / completed-missing-duration-keeps-reservation` | true | retained_native | `completed-missing-duration-keeps-reservation` | M008-S06 |
| `autonomy_v1.json / failed-short-duration-does-not-refund` | true | retained_native | `failed-short-duration-does-not-refund` | M008-S06 |
| `autonomy_v1.json / resolved-unknown-keeps-reservation` | true | retained_native | `resolved-unknown-keeps-reservation` | M008-S06 |
| `autonomy_v1.json / duration-rounds-up-deterministically` | true | retained_native | `duration-rounds-up-deterministically` | M008-S06 |
| `autonomy_v1.json / unfinished-start-debits-before-launch` | true | retained_native | `unfinished-start-debits-before-launch` | M008-S06 |
| `autonomy_v1.json / truthful-overrun-finish-closes-run` | true | retained_native | `truthful-overrun-finish-closes-run` | M008-S06 |
| `autonomy_v1.json / failed-overrun-is-also-visible` | true | retained_native | `failed-overrun-is-also-visible` | M008-S06 |
| `autonomy_v1.json / overrun-forbids-extension` | false | retained_native | `overrun-forbids-extension` | M008-S06 |
| `autonomy_v1.json / reservation-cannot-exceed-remaining` | false | retained_native | `reservation-cannot-exceed-remaining` | M008-S06 |
| `autonomy_v1.json / clip-final-reservation-to-three-seconds` | true | retained_native | `clip-final-reservation-to-three-seconds` | M008-S06 |
| `autonomy_v1.json / exhausted-jobs-no-new-start` | false | retained_native | `exhausted-jobs-no-new-start` | M008-S06 |
| `autonomy_v1.json / exhausted-time-no-new-start` | false | retained_native | `exhausted-time-no-new-start` | M008-S06 |
| `autonomy_v1.json / valid-extension-preserves-charge` | true | retained_native | `valid-extension-preserves-charge` | M008-S06 |
| `autonomy_v1.json / duplicate-extension-revision` | false | retained_native | `duplicate-extension-revision` | M008-S06 |
| `autonomy_v1.json / skipped-extension-revision` | false | retained_native | `skipped-extension-revision` | M008-S06 |
| `autonomy_v1.json / frutlups-cannot-grant-extension` | false | retained_native | `frutlups-cannot-grant-extension` | M008-S06 |
| `autonomy_v1.json / undelegated-architect-cannot-extend` | false | retained_native | `undelegated-architect-cannot-extend` | M008-S06 |
| `autonomy_v1.json / delegated-architect-can-extend` | true | retained_native | `delegated-architect-can-extend` | M008-S06 |
| `autonomy_v1.json / frutlups-cannot-admit` | false | retained_native | `frutlups-cannot-admit` | M008-S06 |
| `autonomy_v1.json / empty-authority-cannot-grant` | false | retained_native | `empty-authority-cannot-grant` | M008-S06 |
| `autonomy_v1.json / duplicate-authority-refs-refuse` | false | retained_native | `duplicate-authority-refs-refuse` | M008-S06 |
| `autonomy_v1.json / same-authority-set-cannot-grant-again` | false | retained_native | `same-authority-set-cannot-grant-again` | M008-S06 |
| `autonomy_v1.json / reordered-authority-set-cannot-grant-again` | false | retained_native | `reordered-authority-set-cannot-grant-again` | M008-S06 |
| `autonomy_v1.json / duplicate-run-id` | false | retained_native | `duplicate-run-id` | M008-S06 |
| `autonomy_v1.json / unknown-extension-run` | false | retained_native | `unknown-extension-run` | M008-S06 |
| `autonomy_v1.json / extension-milestone-must-match` | false | retained_native | `extension-milestone-must-match` | M008-S06 |
| `autonomy_v1.json / successor-head-must-match` | false | retained_native | `successor-head-must-match` | M008-S06 |
| `autonomy_v1.json / successor-explicitly-supersedes` | true | retained_native | `successor-explicitly-supersedes` | M008-S06 |
| `autonomy_v1.json / superseded-run-cannot-extend` | false | retained_native | `superseded-run-cannot-extend` | M008-S06 |
| `autonomy_v1.json / new-run-cannot-bypass-unfinished-operation` | false | retained_native | `new-run-cannot-bypass-unfinished-operation` | M008-S06 |
| `autonomy_v1.json / extension-cannot-overlap-unfinished-operation` | false | retained_native | `extension-cannot-overlap-unfinished-operation` | M008-S06 |
| `autonomy_v1.json / verification-reserves-seconds-not-model-jobs` | true | retained_native | `verification-reserves-seconds-not-model-jobs` | M008-S06 |
| `autonomy_v1.json / verification-completion-settles-time` | true | retained_native | `verification-completion-settles-time` | M008-S06 |
| `autonomy_v1.json / verification-requires-verifying-state` | false | retained_native | `verification-requires-verifying-state` | M008-S06 |
| `autonomy_v1.json / verification-requires-current-round` | false | retained_native | `verification-requires-current-round` | M008-S06 |
| `autonomy_v1.json / verification-global-overlap-blocks-model` | false | retained_native | `verification-global-overlap-blocks-model` | M008-S06 |
| `autonomy_v1.json / model-global-overlap-blocks-verification` | false | retained_native | `model-global-overlap-blocks-verification` | M008-S06 |
| `autonomy_v1.json / outside-run-milestone-refuses-during-invocation` | false | retained_native | `outside-run-milestone-refuses-during-invocation` | M008-S06 |
| `autonomy_v1.json / verification-role-needs-profile` | false | retained_native | `verification-role-needs-profile` | M008-S06 |
| `autonomy_v1.json / profile-removal-does-not-erase-run` | false | retained_native | `profile-removal-does-not-erase-run` | M008-S06 |
| `autonomy_v1.json / pre-admission-attempt-history-migration-refuses` | false | retained_native | `pre-admission-attempt-history-migration-refuses` | M008-S06 |
| `autonomy_v1.json / base-attempt-history-remains-valid` | true | retained_native | `base-attempt-history-remains-valid` | M008-S06 |
| `autonomy_v1.json / legacy-nonattempt-prefix-is-preserved` | true | retired_legacy_positive_replaced_by_native_refusal | `legacy-nonattempt-prefix-is-preserved` | M008-S06 |
| `autonomy_v1.json / v2-nonattempt-prefix-is-preserved` | true | retained_native | `v2-nonattempt-prefix-is-preserved` | M008-S06 |
| `autonomy_v1.json / transport-retry-of-retry-refuses` | false | retained_native | `transport-retry-of-retry-refuses` | M008-S06 |
| `autonomy_v1.json / format-retry-of-retry-refuses` | false | retained_native | `format-retry-of-retry-refuses` | M008-S06 |
| `autonomy_v1.json / distinct-retry-units-charge-independently` | true | retained_native | `distinct-retry-units-charge-independently` | M008-S06 |
| `autonomy_v1.json / retry-after-extension-keeps-contract` | true | retained_native | `retry-after-extension-keeps-contract` | M008-S06 |
| `autonomy_v1.json / retry-cannot-cross-run-membership` | false | retained_native | `retry-cannot-cross-run-membership` | M008-S06 |
| `autonomy_v1.json / initial-relabel-cannot-reset-model-context` | false | retained_native | `initial-relabel-cannot-reset-model-context` | M008-S06 |
| `autonomy_v1.json / out-of-selected-slice-refuses` | false | retained_native | `out-of-selected-slice-refuses` | M008-S06 |
| `autonomy_v1.json / selection-must-follow-roadmap-order` | false | retained_native | `selection-must-follow-roadmap-order` | M008-S06 |
| `autonomy_v1.json / selection-must-not-repeat-slice` | false | retained_native | `selection-must-not-repeat-slice` | M008-S06 |
| `autonomy_v1.json / stop-must-be-last-selected-slice` | false | retained_native | `stop-must-be-last-selected-slice` | M008-S06 |
| `autonomy_v1.json / nonholistic-milestone-needs-slice-stop` | false | retained_native | `nonholistic-milestone-needs-slice-stop` | M008-S06 |
| `autonomy_v1.json / nonholistic-final-acceptance-closes-run` | true | retained_native | `nonholistic-final-acceptance-closes-run` | M008-S06 |
| `autonomy_v1.json / manual-reopen-never-revives-run` | true | retained_native | `manual-reopen-never-revives-run` | M008-S06 |
| `autonomy_v1.json / reopened-terminal-run-cannot-dispatch` | false | retained_native | `reopened-terminal-run-cannot-dispatch` | M008-S06 |
| `autonomy_v1.json / terminal-run-cannot-extend` | false | retained_native | `terminal-run-cannot-extend` | M008-S06 |
| `autonomy_v1.json / already-accepted-stop-cannot-be-admitted` | false | retained_native | `already-accepted-stop-cannot-be-admitted` | M008-S06 |
| `autonomy_v1.json / unrelated-manual-acceptance-is-not-run-stop` | true | retained_native | `unrelated-manual-acceptance-is-not-run-stop` | M008-S06 |
| `autonomy_v1.json / holistic-close-is-explicit-stop` | true | retained_native | `holistic-close-is-explicit-stop` | M008-S06 |
| `autonomy_v1.json / slice-acceptance-does-not-close-holistic-boundary` | true | retained_native | `slice-acceptance-does-not-close-holistic-boundary` | M008-S06 |
| `autonomy_v1.json / corrective-ceiling-persists-in-fold` | false | retained_native | `corrective-ceiling-persists-in-fold` | M008-S06 |
| `autonomy_v1.json / new-run-does-not-reset-corrective-consumption` | false | retained_native | `new-run-does-not-reset-corrective-consumption` | M008-S06 |
| `autonomy_v1.json / invalid-admission-allowance` | false | retained_native | `invalid-admission-allowance` | M008-S06 |
| `autonomy_v1.json / invalid-admission-max_corrective_rounds` | false | retained_native | `invalid-admission-max_corrective_rounds` | M008-S06 |
| `autonomy_v1.json / invalid-admission-grantors` | false | retained_native | `invalid-admission-grantors` | M008-S06 |
| `autonomy_v1.json / invalid-admission-previous` | false | retained_native | `invalid-admission-previous` | M008-S06 |
| `autonomy_v1.json / invalid-admission-unexpected` | false | retained_native | `invalid-admission-unexpected` | M008-S06 |
| `autonomy_v1.json / negative-extension-refuses` | false | retained_native | `negative-extension-refuses` | M008-S06 |
| `autonomy_v1.json / zero-extension-refuses` | false | retained_native | `zero-extension-refuses` | M008-S06 |
| `autonomy_v1.json / unknown-allowance-unit-refuses` | false | retained_native | `unknown-allowance-unit-refuses` | M008-S06 |
| `autonomy_v1.json / slice-stop-does-not-authorize-holistic` | false | retained_native | `slice-stop-does-not-authorize-holistic` | M008-S06 |
| `autonomy_v1.json / slice-stop-allows-preflight-milestone-probe` | true | retained_native | `slice-stop-allows-preflight-milestone-probe` | M008-S06 |
| `autonomy_v1.json / completed-finish-cannot-settle-twice` | false | retained_native | `completed-finish-cannot-settle-twice` | M008-S06 |
| `autonomy_v1.json / holistic-blocker-stops-selected-slice-dispatch` | false | retained_native | `holistic-blocker-stops-selected-slice-dispatch` | M008-S06 |
| `autonomy_v1.json / initial-coder-requires-issued-prompt` | false | retained_native | `initial-coder-requires-issued-prompt` | M008-S06 |
| `autonomy_v1.json / issued-coder-round-reserves-valid-job` | true | retained_native | `issued-coder-round-reserves-valid-job` | M008-S06 |
| `autonomy_v1.json / coder-future-round-refuses` | false | retained_native | `coder-future-round-refuses` | M008-S06 |
| `autonomy_v1.json / coder-verifying-state-refuses` | false | retained_native | `coder-verifying-state-refuses` | M008-S06 |
| `autonomy_v1.json / coder-reviewing-state-refuses` | false | retained_native | `coder-reviewing-state-refuses` | M008-S06 |
| `autonomy_v1.json / coder-accept-pending-state-refuses` | false | retained_native | `coder-accept-pending-state-refuses` | M008-S06 |
| `autonomy_v1.json / coder-accepted-state-refuses-before-holistic-close` | false | retained_native | `coder-accepted-state-refuses-before-holistic-close` | M008-S06 |
| `autonomy_v1.json / coder-before-corrective-prompt-cannot-bypass-zero-ceiling` | false | retained_native | `coder-before-corrective-prompt-cannot-bypass-zero-ceiling` | M008-S06 |
| `autonomy_v1.json / coder-after-corrective-prompt-still-obeys-zero-ceiling` | false | retained_native | `coder-after-corrective-prompt-still-obeys-zero-ceiling` | M008-S06 |
| `autonomy_v1.json / coder-stale-round-refuses` | false | retained_native | `coder-stale-round-refuses` | M008-S06 |
| `autonomy_v1.json / issued-corrective-coder-can-use-existing-ceiling` | true | retained_native | `issued-corrective-coder-can-use-existing-ceiling` | M008-S06 |
| `autonomy_v1.json / coder-blocked-state-needs-resolution` | false | retained_native | `coder-blocked-state-needs-resolution` | M008-S06 |
| `autonomy_v1.json / resolved-coder-still-needs-fresh-prompt` | false | retained_native | `resolved-coder-still-needs-fresh-prompt` | M008-S06 |
| `autonomy_v1.json / new-milestone-admission-cannot-bypass-global-unfinished` | false | retained_native | `new-milestone-admission-cannot-bypass-global-unfinished` | M008-S06 |
| `autonomy_v1.json / planned-milestone-cannot-be-admitted` | false | retained_native | `planned-milestone-cannot-be-admitted` | M008-S06 |
| `autonomy_v1.json / done-label-alone-cannot-admit-run` | false | retained_native | `done-label-alone-cannot-admit-run` | M008-S06 |
| `autonomy_v1.json / completed-holistic-history-survives-done-roadmap` | true | retained_native | `completed-holistic-history-survives-done-roadmap` | M008-S06 |
| `autonomy_v1.json / completed-nonholistic-history-survives-done-roadmap` | true | retained_native | `completed-nonholistic-history-survives-done-roadmap` | M008-S06 |
| `autonomy_v1.json / done-history-with-unsettled-attempt-refuses` | false | retained_native | `done-history-with-unsettled-attempt-refuses` | M008-S06 |
| `autonomy_v1.json / done-history-must-complete-whole-milestone` | false | retained_native | `done-history-must-complete-whole-milestone` | M008-S06 |
| `autonomy_v1.json / reviewer-unstarted-state-refuses` | false | retained_native | `reviewer-unstarted-state-refuses` | M008-S06 |
| `autonomy_v1.json / reviewer-coding-state-refuses` | false | retained_native | `reviewer-coding-state-refuses` | M008-S06 |
| `autonomy_v1.json / reviewer-verifying-state-refuses` | false | retained_native | `reviewer-verifying-state-refuses` | M008-S06 |
| `autonomy_v1.json / reviewer-accept-pending-state-refuses` | false | retained_native | `reviewer-accept-pending-state-refuses` | M008-S06 |
| `autonomy_v1.json / reviewer-accepted-state-refuses` | false | retained_native | `reviewer-accepted-state-refuses` | M008-S06 |
| `autonomy_v1.json / reviewer-future-round-2-refuses` | false | retained_native | `reviewer-future-round-2-refuses` | M008-S06 |
| `autonomy_v1.json / reviewer-future-round-3-refuses` | false | retained_native | `reviewer-future-round-3-refuses` | M008-S06 |
| `autonomy_v1.json / reviewer-current-round-and-completion` | true | retained_native | `reviewer-current-round-and-completion` | M008-S06 |
| `autonomy_v1.json / reviewer-valid-transport-chain` | true | retained_native | `reviewer-valid-transport-chain` | M008-S06 |
| `autonomy_v1.json / reviewer-valid-format-chain` | true | retained_native | `reviewer-valid-format-chain` | M008-S06 |
| `autonomy_v1.json / reviewer-stale-round-refuses` | false | retained_native | `reviewer-stale-round-refuses` | M008-S06 |
| `autonomy_v1.json / reviewer-after-correction-current-round` | true | retained_native | `reviewer-after-correction-current-round` | M008-S06 |
| `autonomy_v1.json / holistic-unstarted-state-refuses` | false | retained_native | `holistic-unstarted-state-refuses` | M008-S06 |
| `autonomy_v1.json / holistic-reviewing-state-refuses` | false | retained_native | `holistic-reviewing-state-refuses` | M008-S06 |
| `autonomy_v1.json / holistic-accept-pending-state-refuses` | false | retained_native | `holistic-accept-pending-state-refuses` | M008-S06 |
| `autonomy_v1.json / holistic-close-pending-state-refuses` | false | retained_native | `holistic-close-pending-state-refuses` | M008-S06 |
| `autonomy_v1.json / holistic-cannot-review-only-selected-slices` | false | retained_native | `holistic-cannot-review-only-selected-slices` | M008-S06 |
| `autonomy_v1.json / holistic-slice-scope-refuses` | false | retained_native | `holistic-slice-scope-refuses` | M008-S06 |
| `autonomy_v1.json / holistic-numeric-round-refuses` | false | retained_native | `holistic-numeric-round-refuses` | M008-S06 |
| `autonomy_v1.json / holistic-accepted-milestone-and-completion` | true | retained_native | `holistic-accepted-milestone-and-completion` | M008-S06 |
| `autonomy_v1.json / holistic-valid-transport-chain` | true | retained_native | `holistic-valid-transport-chain` | M008-S06 |
| `autonomy_v1.json / holistic-valid-format-chain` | true | retained_native | `holistic-valid-format-chain` | M008-S06 |
| `autonomy_v1.json / holistic-blocked-reservation-refuses` | false | retained_native | `holistic-blocked-reservation-refuses` | M008-S06 |
| `autonomy_v1.json / holistic-resolved-decision-can-review` | true | retained_native | `holistic-resolved-decision-can-review` | M008-S06 |
| `autonomy_v1.json / holistic-reopened-slice-refuses` | false | retained_native | `holistic-reopened-slice-refuses` | M008-S06 |
| `autonomy_v1.json / holistic-after-reopened-slice-acceptance` | true | retained_native | `holistic-after-reopened-slice-acceptance` | M008-S06 |
| `autonomy_v1.json / extension-cannot-reset-crashed-transport-chain` | false | retained_native | `extension-cannot-reset-crashed-transport-chain` | M008-S06 |
| `autonomy_v1.json / done-history-does-not-block-another-active-milestone` | true | retained_native | `done-history-does-not-block-another-active-milestone` | M008-S06 |

## Every original D023 test and invariant

Baseline `3331c7702915adb777773a46d1b3809fbcb176fb`: six modules, 64 functions,
14 introduced by D023. This is a function/parameter inventory, not a count of
collected nodes or executed cases. Complete source copies under r1 `legacy_tests`
bind dynamic parameter tables, helper setup, exceptions and full assertions.
No assertion below authorizes continued legacy-positive runtime execution;
preserve its nonlegacy invariant through the mapped conversion or refusal.
No original test is removed by S01. Empty direct-assert lists mean the bound
source contains the exception/context oracle, not that the test has no oracle.

| Complete original source | Raw SHA-256 |
| --- | --- |
| `02_analysis/m008_s01_admission/r1/legacy_tests/test_protocol_v2.py` | `8b3a08ff169276fe7459c1ab3a65e0b0037c46cf25e3f9b80fe2c7dc77de1b02` |
| `02_analysis/m008_s01_admission/r1/legacy_tests/test_autonomy.py` | `cf31bb4eeafa1b32e03c1f28565180ff4dbab65a8380df9e929a566b747640d6` |
| `02_analysis/m008_s01_admission/r1/legacy_tests/test_roadmap.py` | `13dd81e06c5ef62e7365623ee4ff1c5216bf49a96f4889c09f063481d4e07f7d` |
| `02_analysis/m008_s01_admission/r1/legacy_tests/test_ledger.py` | `ab62cc19116dfda94e02a4a79303f02c7ca0baf05206e8a46c5928c7e4ca29c5` |
| `02_analysis/m008_s01_admission/r1/legacy_tests/test_fixture_integrity.py` | `7625e2c8493b9e78e73aa0b3d7a27167faf47fc8fd0df70f23c4bf6d9ebf2770` |
| `02_analysis/m008_s01_admission/r1/legacy_tests/test_cli.py` | `cdb8fa8d853300d270ec36d52aeca30df297ddb08e5cb1fb818ddcdfa5cb68d1` |

| Test identity | New in D023 | Disposition / owner | Function source SHA-256 | Parameter declaration and retained assertions |
| --- | --- | --- | --- | --- |
| `test_protocol_v2.py::test_base_event_corpus` | true | retained_native_with_contract_adapter_update / M008-S06 | `ac590bccf98c56279f2ce35ad964a3eafb5acd5b57cad5b47dbb92165beaa140` | pytest.mark.parametrize("case", CORPUS["cases"], ids=lambda case: case["name"]); assert path.read_bytes() == before; assert actual == value, key |
| `test_protocol_v2.py::test_base_corpus_has_all_sixteen_event_cases` | true | retained_native_with_contract_adapter_update / M008-S06 | `44fdd9229ce3cd5630729a901d861250a9dd6bb53dcf2ee102e4af97eae23f19` | assert len(CORPUS["cases"]) == 16; assert len({case["name"] for case in CORPUS["cases"]}) == 16 |
| `test_protocol_v2.py::test_legacy_prefix_and_writer_preserve_crlf` | true | retired_legacy_positive_replaced_by_native_refusal / M008-S06 | `e245b445d8f2cc70bc31020a42b27ea9eadc9bd60de4b8df1c12b98e2e280de5` | assert path.read_bytes().startswith(prefix); assert [e.schema for e in events] == [ledger.SCHEMA, ledger.SCHEMA, ledger.SCHEMA_V2]; assert path.read_bytes() == before |
| `test_protocol_v2.py::test_oversized_record_uses_bounded_read` | true | retained_native_with_contract_adapter_update / M008-S06 | `bf934b0cb7bb7301a87f81cc88bc56e0109632ebdd7ae91b79bdd960338ad10d` | pytest.mark.parametrize("count", [ledger.EVENT_LIMIT + 1, 16 * 1024 * 1024]); assert reads == [ledger.EVENT_LIMIT + 1] |
| `test_protocol_v2.py::test_v2_strict_timestamp_fields_and_references` | true | retained_native_with_contract_adapter_update / M008-S06 | `66ac73697aa5fa0cdf47b84658a371fd924b97b0224184fd62a370820c512154` | pytest.mark.parametrize("changes,message", [<br>    ({"t": "2026-02-30T00:00:00Z"}, "day"),<br>    ({"t": "2026-09-14T00:00:00+00:00"}, "canonical UTC"),<br>    ({"envelope": {"path": "../outside", "sha": "a" * 64}}, "unsafe"),<br>    ({"envelope": {"path": "safe", "sha": "A" * 64}}, "reference hash"),<br>    ({"envelope": {"path": "safe", "sha": "a" * 64, "extra": True}}, "reference"),<br>    ({"extra": True}, "unknown"),<br>]) |
| `test_protocol_v2.py::test_ledger_requires_utf8` | true | retained_native_with_contract_adapter_update / M008-S06 | `2803e6ea1e5264e449c0ed264636161d535d88d0257506b2052267ce7af9f10f` | Exception/context oracle in bound source |
| `test_autonomy.py::test_corpus_identity_and_case_counts` | true | retained_old_identity_assertions_plus_separate_new_native_identity_assertions / M008-S06 | `f7b233cca4c705b61eb7d237aea32f4b0c26cb7a02909c30ab29340697b8a79e` | assert CORPUS["schema"] == "template.autonomy-conformance/1"; assert len(CORPUS["cases"]) == 128; assert len({case["name"] for case in CORPUS["cases"]}) == 128; assert sum(case["valid"] for case in CORPUS["cases"]) == 40 |
| `test_autonomy.py::test_profile_case` | true | retained_native_with_contract_adapter_update / M008-S06 | `40b1ad39f0a3ca5ed85e8c8bd9e333ad9642179b7940060a5190c72b28079585` | pytest.mark.parametrize("case", CORPUS["cases"], ids=lambda case: case["name"]); assert case["error"]; assert case["error"] in str(caught.value); assert ledger._thaw(state.run_head) == case["head"]; assert ledger._thaw(state.run_membership) == case["membership"]; assert ledger._thaw(actual[key]) == value, (run_id, key) |
| `test_roadmap.py::test_load_refuses_each_template_validation_error` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `43ecd4988d955724a57d5d64ba572df8a44820ecdeb20ec3249d7a0117146fe2` | pytest.mark.parametrize(("mutate", "message"), INVALID_CASES); assert message in str(error.value) |
| `test_roadmap.py::test_loads_fixture_roadmap_as_frozen_model` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `53dce8274e9c9a97c9bf33534d09d376e562997726f665e1284214389fda7172` | assert roadmap.project == "replace-with-project-slug"; assert roadmap.verification_full == ("python", "scripts/hermetic_verification.py"); assert roadmap.review_routing["release"] == ("reviewer", "claude_reviewer"); assert slice_by_id(roadmap, "M001-S01").milestone_id == "M001"; assert effective_prefixes(roadmap, slice_by_id(roadmap, "M001-S01")) == ("07_app/",) |
| `test_roadmap.py::test_milestone_status_rules_match_template` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `0dd572cde982133362137da9b517aac9abd2c6ac2272e8cc6082ab7991d0278c` | pytest.mark.parametrize(<br>    ("statuses", "errors"),<br>    [<br>        (("active", "planned", "done"), ()),<br>        (("done", "done"), ()),<br>        (("active", "bad"), ("milestones[1].status must be planned, active, or done",)),<br>        (("planned",), ("at least one milestone must be active unless all milestones are done",)),<br>        (("done", "planned"),<br>         ("at least one milestone must be active unless all milestones are done",)),<br>    ],<br>    ids=("all-statuses", "all-done", "invalid-status", "planned-only", "done-and-planned"),<br>); assert result.returncode == (2 if errors else 0); assert result.stdout == ("" if errors else "roadmap: ok\n"); assert result.stderr == "".join(f"error: {message}\n" for message in errors); assert caught.value.errors == errors; assert result.stderr == "".join(f"error: {message}\n" for message in caught.value.errors); assert tuple(m.status for m in load(path).milestones) == statuses |
| `test_roadmap.py::test_next_slice_prioritizes_sticky_reopens_then_active_order_then_none` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `94defc5469738293d957ef0564899e5243cb90da10813c798ed43d1ff363fccf` | assert next_slice(roadmap, state) == first; assert next_slice(roadmap, state) == third; assert next_slice(roadmap, state) == second; assert next_slice(roadmap, state) is None |
| `test_roadmap.py::test_render_markdown_matches_template_script` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `da325e5ac78a01ca67cb85789600a440b26397532bea69545f6db38fd4186de3` | assert result.returncode == 0, result.stderr; assert render_markdown(load(ROADMAP)) == (project / "docs" / "roadmap.md").read_text(<br>        encoding="utf-8"<br>    ) |
| `test_roadmap.py::test_slice_size_warning_does_not_refuse_load` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `d4dd346a88161980cf281fa684c3586a87e8b047bf20ee3d0344a42b06b30390` | assert load(path).milestones[0].slices[0].notes == "x" * 3000 |
| `test_roadmap.py::test_v2_retains_runtime_profile_and_verification_declarations` | true | retained_native_with_contract_adapter_update / M008-S06 | `77f8f39b9195e2327605117ad8c45b8a3836be65b31638d00d051bc71463395c` | assert model.schema == data["schema"]; assert model.runtime == data["runtime"]; assert model.autonomy == data["autonomy"]; assert model.verification_git_boundary == ("python", "check.py"); assert model.verification_observation == "clean_checkout"; assert model.verification_timeout_seconds == 900; assert legacy.schema == "frutlups.roadmap/1"; assert legacy.runtime == {}; assert legacy.autonomy is None; assert legacy.verification_git_boundary is None; assert legacy.verification_observation == "process"; assert legacy.verification_timeout_seconds == 600 |
| `test_roadmap.py::test_strict_v2_declarations` | true | retained_native_with_contract_adapter_update / M008-S06 | `ac91d5a4952121b1c18ac1ac5361cddbb1036e926a02592e0a8b7e0ac0cd7970` | pytest.mark.parametrize("schema,key,value", [<br>    ("frutlups.roadmap/1", "autonomy", {"schema": "frutlups.autonomy/1"}),<br>    ("frutlups.roadmap/1", "runtime", {"python": "3.14"}),<br>    *[("frutlups.roadmap/2", "autonomy", value) for value in<br>      (None, {}, {"schema": "frutlups.autonomy/2"},<br>       {"schema": "frutlups.autonomy/1", "reset": True})],<br>    ("frutlups.roadmap/2", "runtime", {"python": "2.7"}),<br>    ("frutlups.roadmap/2", "runtime", {"python": "3.14", "extra": True}),<br>]) |
| `test_ledger.py::test_fold_table_rows` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `d32d66bf62ba5df19e9d6c461cbd02d75b43800ec098b96226c697596f343560` | pytest.mark.parametrize(("events", "step", "round_no"), FOLD_CASES); assert current.step == step; assert current.round == round_no; assert current.open_findings == ("F1",); assert current.unblock_reason == "approved"; assert current.reopened is True |
| `test_ledger.py::test_legacy_and_v2_event_types_are_declared` | true | converted_native_enum_and_explicit_legacy_refusal / M008-S06 | `8e029a89615cc09c984bb810ca6d7733c547f73424f54bee13cea678fee5b769` | assert {event.value for event in Ev} == {<br>        "prompt", "artifact", "coded", "verified", "reviewed", "accepted",<br>        "reopened", "unblocked", "milestone_done", "note", "stop",<br>        "resolved", "commit_cancelled", "holistic_reviewed", "review_checkpoint",<br>        "attempt_started", "attempt_finished", "run_admitted", "run_extended",<br>    } |
| `test_ledger.py::test_optional_cost_preserves_numeric_value` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `4114fc0dea0864f5d4b0fd142cf45a344b28e43b0d50312c6f04842d0cb83f60` | pytest.mark.parametrize("kind", ["coded", "reviewed"]); pytest.mark.parametrize("cost", [0, 1, 0.0, 0.25]); assert parsed[-1].data["cost_usd"] == cost; assert type(parsed[-1].data["cost_usd"]) is type(cost); assert current.step == ("verifying" if kind == "coded" else "accept_pending") |
| `test_ledger.py::test_cost_refuses_invalid_numeric_values` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `67325526f0cd9fd3d14122ee82ef0db6acbfcabdb1be1be0a52e08b05a0c8a01` | pytest.mark.parametrize("kind", ["coded", "reviewed"]); pytest.mark.parametrize("cost", [-1, -0.25, True, False, "1", None]); assert str(caught.value) == "line 1: invalid text, usage, or actor field" |
| `test_ledger.py::test_next_slice_matches_template_on_same_ledger` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `e6ea435c907eecccb4c5bb6573be273ec458e4e941c192a3fb181880aeaeafd2` | pytest.mark.parametrize(<br>    ("statuses", "accepted", "reopened", "done", "expected"),<br>    [<br>        (("active", "active"), (1,), (), (), "M002-S01"),<br>        (("active", "planned"), (1,), (), (), None),<br>        (("active", "done"), (1,), (), (), None),<br>        (("active", "done", "done"), (2, 3), (3, 2), (), "M002-S01"),<br>        (("active", "active"), (1, 2), (), (1, 2), None),<br>        (("active", "active"), (1,), (), (1,), "M002-S01"),<br>    ],<br>    ids=("skip-accepted-active", "skip-planned", "skip-done", "reopen-roadmap-order",<br>         "all-accepted", "milestone-done-does-not-gate"),<br>); assert (following.id if following else None) == expected; assert next_slice(model, replace(state, milestones_done=all_done)) == following; assert result.returncode == 0, result.stderr; assert result.stderr == ""; assert result.stdout == output |
| `test_ledger.py::test_read_names_line_for_strict_failures` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `e7bd0b7a495ba5076c1cb74e65628c579f1797f4b99d47c588f2b284d3eca5dc` | pytest.mark.parametrize(<br>    ("line", "message"),<br>    [<br>        ("{", "line 1: malformed JSON"),<br>        (json.dumps(_event("note", text="ok", extra=True)), "line 1: unknown"),<br>        (json.dumps({**_event("note", text="ok"), "schema": "bad"}), "line 1: invalid schema"),<br>    ],<br>    ids=("malformed", "unknown-field", "bad-schema"),<br>) |
| `test_ledger.py::test_append_folds_before_write_and_preserves_bytes_on_refusal` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `73410d49aaf85256ca3aadf1c832d33634660cbe21f097e5901de0bdbd2fae5d` | assert path.read_bytes() == before |
| `test_ledger.py::test_append_writes_lf_and_fsyncs` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `dc6ff20f15d1d3e3e8db50b4c636394a0f4be339567c559a56c163ccd2a22a7c` | assert calls; assert path.read_bytes().endswith(b"\n"); assert fold(read(path), roadmap).slices["M001-S01"].step == "coding" |
| `test_ledger.py::test_append_loads_adjacent_roadmap_by_default` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `899c7cbae523c7581e43957057908c2a26ada5d0ddb30bd3ce85fd77a573e4f2` | assert read(path) == (candidate,) |
| `test_ledger.py::test_fold_rejects_invalid_scope_timing_transition_and_round` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `451c860b5ed2117b693c66102bf394c793cebea1be19da100cd64dc3645c983d` | pytest.mark.parametrize(<br>    ("events", "message"),<br>    [<br>        (<br>            [_event(<br>                "artifact", scope="M001-S01", round=1, role="review_prompt",<br>                path="review-prompt.md", sha=DIGEST,<br>            )],<br>            "review prompt artifact out of order",<br>        ),<br>        (<br>            [_event(<br>                "artifact", scope="M999", round="holistic", role="holistic_prompt",<br>                path="holistic.md", sha=DIGEST,<br>            )],<br>            "unknown milestone",<br>        ),<br>        (<br>            [_event("milestone_done", milestone="M001")],<br>            "premature milestone_done",<br>        ),<br>        ([_coded()], "coded out of order"),<br>        ([_event("prompt", slice="M999-S01", round=1, path="p", sha=DIGEST)],<br>         "unknown slice"),<br>        ([_prompt(2)], "prompt out of order"),<br>    ],<br>    ids=("review-artifact-timing", "artifact-scope", "milestone-timing",<br>         "transition", "slice-id", "round"),<br>) |
| `test_ledger.py::test_holistic_artifacts_and_milestone_done_after_acceptance` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `7acafae58433a3517c2b7aa70597d821ac1dcddab2d9ee02f61a0ad7a157d76d` | assert state.milestones_done == frozenset({"M001"}); assert state.slices["M001-S01"].step == "accepted" |
| `test_ledger.py::test_corrective_prompt_count_and_reopen_flag_clear_on_accept` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `8a6b5d64a5ee7dd16ed1b5cbe75c45436ee6e837ea93b58bcd19812a520eb970` | assert current.corrective_rounds_used == 1; assert current.reopened is False |
| `test_ledger.py::test_evidence_sha_normalizes_text_but_not_nul_bytes` | false | retained_contract_invariant / M008-S06 | `a3f451794c7dad5dc1e1b3be7f6cfb233875c8152bc5faa542b17b83b9ea034f` | assert evidence_sha(crlf.read_bytes()) == evidence_sha(lf.read_bytes()); assert evidence_sha(binary) == hashlib.sha256(binary).hexdigest() |
| `test_ledger.py::test_evidence_sha_matches_template_script` | false | retained_contract_invariant / M008-S06 | `a91a932fa28d248bd25a70ca3451b72f750aef72ffefd4ef602c69d027114186` | pytest.mark.parametrize(<br>    ("data", "normalized"),<br>    [<br>        pytest.param(b"one\r\ntwo\r\n", b"one\ntwo\n", id="crlf"),<br>        pytest.param(b"one\ntwo\n", b"one\ntwo\n", id="lf-twin"),<br>        pytest.param(b"one\0\r\ntwo", b"one\0\r\ntwo", id="nul-with-crlf"),<br>        pytest.param(b"one\rtwo\r", b"one\rtwo\r", id="lone-cr"),<br>    ],<br>); assert digest == hashlib.sha256(normalized).hexdigest(); assert template_evidence.c.evidence_sha_bytes(data) == digest; assert template_evidence.c.sha(sample) == digest; assert sample.read_bytes() == data; assert spec is not None; assert spec.loader is not None |
| `test_ledger.py::test_evidence_sha_hashes_git_blob_bytes` | false | retained_contract_invariant / M008-S06 | `235a8c0d4f15e3ff3d0df2190460fcb05f0f1607f542f47b4cdba1e20f175b3a` | assert evidence_sha(blob) == evidence_sha(b"base\n") |
| `test_ledger.py::test_check_reports_immutable_artifact_and_notes_drift` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `1937b0e2ced27ed7d235a69fb4ee5b0bbc9c27354818f61e66cc535813825170` | assert {str(error) for error in check(path, root)} == {<br>        "drift: prompt.md", "missing: notes.md",<br>    } |
| `test_ledger.py::test_check_accepts_products_restored_to_head_or_absent_from_head` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `de08d6e356e3b0583d7c0a779fabb13fd50e790e7f07b132e94b03231703cabb` | assert check(path, root) == () |
| `test_ledger.py::test_check_accepts_crlf_working_copy_of_lf_blob_like_template` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `e67d36972c5ef5ef4283f19cea56f6ce8c8a52fdfafae6146588b0daf210ec1f` | pytest.mark.parametrize(<br>    "recorded", [b"base\n", b"latest\n"], ids=("matches-latest", "matches-head"),<br>); assert _git(root, "config", "--get", "core.autocrlf").stdout == b"false\n"; assert _git(root, "show", "HEAD:product.txt").stdout == b"base\n"; assert check(path, root) == (); assert result.returncode == 0, result.stderr; assert result.stdout == "ledger: ok\n"; assert result.stderr == ""; assert product.read_bytes() == b"base\r\n" |
| `test_ledger.py::test_check_reports_product_drift_from_latest_and_head` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `99855761b8f23ec3aa41167d9fa6e2325728c90308b28dd8d1976138d2424a6f` | assert tuple(map(str, check(path, root))) == ("drift: product.txt",) |
| `test_ledger.py::test_status_text_matches_template_script` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `12c1583407383f92b2b0df5f67b3ad05c67d9f62e45c8dde7289c3e225118903` | pytest.mark.parametrize("events", STATUS_CASES); assert result.returncode == 0, result.stderr; assert status_text(roadmap, fold(read(ledger_path), roadmap)) + "\n" == result.stdout |
| `test_fixture_integrity.py::test_v4_project_fixture_matches_manifest` | false | retained_archival_integrity / M008-S06 | `979d559cd3ca33cb7f0154c93e757c0bacb572f067cb13ab775ba9f019a9d414` | assert manifest["schema"] == "frutlups.fixture-manifest/1"; assert manifest["root"] == FIXTURE.name; assert sorted(actual) == sorted(expected); assert actual == expected |
| `test_fixture_integrity.py::test_v2_export_and_plural_corpora_match_raw_manifest` | true | retained_archival_integrity / M008-S06 | `4c9667b787b2af9c084759e263f12a4fc354152b0f62d63d991ebf5cf4a84d05` | assert manifest["commit"] == "169a74c66140743008bef374f2c7d0389ee81cb9"; assert len(rows) == 67; assert {row["path"] for row in manifest["corpora"]} == {"protocol_v2.json", "autonomy_v1.json"}; assert len(data) == row["bytes"], row["path"]; assert hashlib.sha256(data).hexdigest() == row["sha256_raw"], row["path"]; assert len(data) == row["bytes"]; assert hashlib.sha256(data).hexdigest() == row["sha256_raw"] |
| `test_fixture_integrity.py::test_every_v2_member_survives_clean_git_materialization` | true | retained_archival_integrity / M008-S06 | `0dc4e97c8c46b953c856fc7760f08293f7c46f27bc78a4d086ff733b1f8c3fde` | assert all(path.as_posix() in tracked for path in paths); assert (clean / path).read_bytes() == (source / path).read_bytes(), path |
| `test_cli.py::test_once_outputs_one_transition_and_resumes` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `646b2d154cf923b281e34b96c24f45cc495e00a11b1adf9c80b2efb33415d812` | pytest.mark.parametrize("json_mode", [False, True]); assert code == 0; assert state(root, cfg, model).slices["M001-S01"].step == "coding"; assert len(output.splitlines()) == 1; assert code == 0; assert state(root, cfg, model).slices["M001-S01"].step == "accepted"; assert row["action"] == "prompt"; assert row["slice"] == "M001-S01"; assert row["round"] == 1; assert row["path"] == events_of(root, cfg, "prompt")[0].data["path"]; assert output.startswith("M001-S01 r1 prompt -> prompts/for_coding_agent/"); assert [r["action"] for r in rows] == [<br>            "coded",<br>            "verified",<br>            "artifact",<br>            "reviewed",<br>            "accepted",<br>            "stop",<br>        ]; assert rows[0]["tokens_in"] == 17; assert rows[0]["tokens_out"] == 3; assert rows[0]["cost_usd"] == 0.01; assert rows[0]["seats"] == [<br>            {"name": "coder", "adapter": "pi", "model": "fake", "effort": "medium"}<br>        ]; assert rows[1]["ok"] is True; assert rows[-1]["reason"] == "done"; assert "M001-S01 r1 coder pi/fake/medium" in output; assert "M001-S01 r1 verify ok" in output; assert "M001-S01 r1 review claude/fake/high pass open=0" in output; assert "M001-S01 accepted" in output; assert "done:" in output |
| `test_cli.py::test_until_overrides_configuration` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `6573e9402e469c17bfa8a8c5c4abd4033f7ece84f94cc8eb64a8d09494370ae2` | pytest.mark.parametrize("until", ["slice", "milestone", "roadmap"]); assert code == 0; assert rows[-1]["reason"] == ("done" if until == "roadmap" else "boundary"); assert len(events_of(root, cfg, "accepted")) == 1 |
| `test_cli.py::test_status_and_usage_conform_to_template` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `65be441aacfb81fcaad0fa2ded2e4f8ef7169e596c7847067aa08ac299d675da` | pytest.mark.parametrize("json_mode", [False, True]); assert loop.run(root, cfg, model) == loop.StopReason.done; assert code == 0; assert output == expected; assert code == 0; assert rows[0] == {"action": "status", "slice": "M001-S01", "round": 1, "step": "accepted"}; assert rows[1] == {"action": "next", "slice": None}; assert output.startswith(expected); assert "tokens_in=30 tokens_out=7 cost_usd=0.03" in output; assert "M001 usage " in output; assert row["tokens_in"] == 30; assert row["tokens_out"] == 7; assert row["cost_usd"] == pytest.approx(0.03); assert row["secs"] == sum(e.data.get("secs", 0) for e in ledger.read(root / cfg.ledger)) |
| `test_cli.py::test_usage_combines_coded_event_cost_and_legacy_result_fallback` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `11c87b881496e67e41de74ac986b861b704052fce1164308f56c549d40fcbb16` | pytest.mark.parametrize("json_mode", [False, True]); pytest.mark.parametrize(("cost", "local_copy"), [(0, True), (0.5, True), (0.5, False)]); assert code == 0, output; assert len(usage) == 2; assert usage[0]["slice"] == "M001-S01"; assert usage[1]["milestone"] == "M001"; assert usage[0]["cost_usd"] == cost + 0.25; assert usage[1]["cost_usd"] == cost + 0.25; assert f"{scope} usage secs=? tokens_in=? tokens_out=? cost_usd={cost + 0.25}" in output |
| `test_cli.py::test_status_usage_includes_only_matching_holistic_jobs` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `7a4ba202414726bcb8ba6a04f2645ba413795394837fcabb20f475ec1605bfa1` | pytest.mark.parametrize("json_mode", [False, True]); assert loop.run(root, cfg, model) == loop.StopReason.done; assert code == 0, output; assert [row["action"] for row in rows] == ["status", "next", "usage", "usage"]; assert rows[2]["slice"] == "M001-S01"; assert rows[2]["secs"] == slice_secs; assert rows[2]["tokens_in"] == 30; assert rows[2]["tokens_out"] == 7; assert rows[2]["cost_usd"] == pytest.approx(0.03); assert rows[3]["milestone"] == "M001"; assert rows[3]["secs"] == pytest.approx(milestone_secs); assert rows[3]["tokens_in"] == 54; assert rows[3]["tokens_out"] == 24; assert rows[3]["cost_usd"] == pytest.approx(0.55); assert float(fields["secs"]) == pytest.approx(milestone_secs); assert fields["tokens_in"] == "54"; assert fields["tokens_out"] == "24"; assert float(fields["cost_usd"]) == pytest.approx(0.55); assert "usage note" not in output |
| `test_cli.py::test_status_usage_counts_unreadable_results_after_milestones` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `6efd0233f2e842441627b7ad39afb364bf73c7c5c19017f0998693949cbe0053` | pytest.mark.parametrize("json_mode", [False, True]); assert code == 0; assert rows[-2]["milestone"] == "M001"; assert rows[-1] == {"action": "usage", "note": "unreadable", "count": 7}; assert sum("note" in row for row in rows) == 1; assert lines[-2].startswith("M001 usage "); assert lines[-1] == "usage note unreadable=7"; assert output.count("usage note") == 1 |
| `test_cli.py::test_usage_matches_holistic_artifacts_to_each_milestone` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `d848bcf6bbd801afdcab6a80a2499ff8515de323ac25b6f5c23c86f6e7526ef4` | assert [row["milestone"] for row in rows[:-1]] == ["M001", "M002", "M003"]; assert rows[2]["secs"] is None; assert rows[2]["tokens_in"] is None; assert rows[2]["tokens_out"] is None; assert rows[2]["cost_usd"] is None; assert rows[3] == {"action": "usage", "note": "unreadable", "count": 1}; assert row["secs"] == amount; assert row["tokens_in"] == amount * 10; assert row["tokens_out"] == amount * 100; assert row["cost_usd"] == amount / 10 |
| `test_cli.py::test_main_drops_rows_on_broken_stdout` | false | retained_contract_invariant / M008-S06 | `75fa1b852bcda903b539b52f214b676dd64fd66ee1c19d6b97a9bdb505faa600` | pytest.mark.parametrize("json_mode", [False, True]); pytest.mark.parametrize(<br>    ("verb", "expected"), [("preflight", 2), ("status", 0), ("run", 0), ("internal", 1)],<br>); assert caught.value.code == expected; assert attempts; assert output.out == ""; assert output.err == ""; assert state(root, cfg, model).slices["M001-S01"].step == "coding" |
| `test_cli.py::test_stopped_run_names_reason_and_human_step` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `b18152a3d748600957969e5228d11f1d3ef750421c6aa4703523d039a41af34f` | pytest.mark.parametrize("json_mode", [False, True]); assert code == 3; assert "seat_auth" in output; assert "resolve seat access and resume" in output; assert json.loads(output.splitlines()[-1])["reason"] == "seat_auth" |
| `test_cli.py::test_internal_error_is_exit_one_json` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `9a909ce749d4fdddeda4db51f4dca00d338c54c0bdb7c6b1d3d1afec2ce4539b` | assert code == 1; assert json.loads(output)["action"] == "internal"; assert "owner must inspect" in output |
| `test_cli.py::test_preflight_failure_classes_exit_two` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `df839ae3bb00bac5ec49a05c6a7652c190341726f395ac7f7cfdae1974a1c4bc` | pytest.mark.parametrize(<br>    "failure",<br>    ["STOP", "agents", "dirty", "ledger", "roadmap", "config", "git", "pi", "verification", "seat"],<br>); assert code == 2; assert expected in output; assert all(line.startswith("preflight: ") for line in output.splitlines()); assert (root / cfg.ledger).read_bytes() == before |
| `test_cli.py::test_preflight_names_each_missing_windows_variable` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `b9dbea2c8b8a4ec2f85460d89ca6be014e28d06d8282057aafc8b751f42566f7` | pytest.mark.skipif(os.name != "nt", reason="Windows launcher contract"); pytest.mark.parametrize("name", ["SYSTEMROOT", "COMSPEC", "PATHEXT"]); assert code == 2; assert f"env_passthrough missing required Windows variable: {name}" in output |
| `test_cli.py::test_preflight_only_probes_active_routes` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `8fc88134d7ad5117cf947276145c696fbb9157db867b75da3f315db1a1bfda5f` | assert loop.preflight(root, cfg, model) == (); assert calls == ["coder", "holistic"] |
| `test_cli.py::test_preflight_probes_each_distinct_claude_model` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `9d171aaf24a9f71ce84931edd2deb30f3ef9e5328a6cbb6bf6cdc314b403ae8e` | pytest.mark.parametrize("same_model", [False, True]); assert loop.preflight(root, cfg, model) == (); assert [job.seat_name for job in calls] == (<br>        ["holistic"] if same_model else ["holistic", "reviewer"]<br>    ); assert list((root / "local_state/frutlups/jobs").iterdir()) == []; assert (root / "local_state/probe-count.txt").read_text() == ("2" if same_model else "3"); assert job.role is Role.reviewer; assert job.adapter == seat.adapter; assert job.provider == seat.provider; assert job.model == seat.model; assert job.effort == seat.effort; assert job.tools == _preflight.claude.READ_TOOLS; assert job.timeout == 60; assert job.expected_notes_path is None; assert job.cwd == root; assert job.prompt_path.is_relative_to(root / "local_state/frutlups"); assert job.prompt_path.read_bytes() == b"Reply with OK only.\n"; assert job.prompt_sha == hashlib.sha256(job.prompt_path.read_bytes()).hexdigest() |
| `test_cli.py::test_preflight_probe_plan_preserves_roles_and_keeps_only_failed_job` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `41ed0f0a5673ac749992ef0898716816a5dea5c0a88b7d5d77d40cc677e5f5bb` | pytest.mark.parametrize("adapter", ["pi", "claude"]); pytest.mark.parametrize("failure", [None, "auth", "capacity", "transport", "output", "timeout"]); assert len(captured) == 1; assert (root / "local_state/probe-count.txt").read_text() == "2"; assert not (root / "local_state/coder-count.txt").exists(); assert not (root / "local_state/reviewer-count.txt").exists(); assert (root / "local_state/plan.json").read_bytes() == plan_before; assert (root / cfg.ledger).read_bytes() == before; assert job.role is Role.reviewer; assert job.tools == module.READ_TOOLS; assert job.timeout == 60; assert result.status == ("timed_out" if failure == "timeout" else "failed"); assert result.failure_class == failure; assert directories == [result.stdout_path.parent]; assert result.stdout_path.is_file(); assert result.stderr_path.is_file(); assert (directories[0] / "prompt.md").read_bytes() == b"Reply with OK only.\n"; assert errors == (<br>            f"model probe failed: {name}: adapter={adapter} provider={seat.provider} "<br>            f"model=probe-model; failure={failure}; diagnostic: {result.diagnostic}; "<br>            f"stderr: (empty); streams: {directories[0].relative_to(root).as_posix()}",<br>        ); assert errors == (); assert result.status == "completed"; assert result.final_text == "OK"; assert directories == []; assert timeout == 60 |
| `test_cli.py::test_auth_refusal_retains_streams_and_scrubs_tail` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `438a12e5f930cc8a2f9ae1db5d28c1de8380bae2027c8d243b997f202a7012e7` | pytest.mark.parametrize("timed_out", [False, True]); assert code == 2; assert "synthetic-auth-secret" not in output; assert str(root) not in output; assert "<redacted>" in output; assert "auth denied" in output; assert len(output) < 10000; assert len(rows) == 2; assert (root / cfg.ledger).read_bytes() == before; assert "failure=timeout" in output; assert stdout.is_relative_to(root / "local_state/frutlups/jobs"); assert stdout.read_text() == "raw output"; assert "synthetic-auth-secret" in stderr.read_text() |
| `test_cli.py::test_unsupported_options_rejected` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `7e5ce59930be2e4b613de97d4710cb44917f5f5cca3ee62bff0fdb8d07330fc7` | pytest.mark.parametrize("verb", ["preflight", "status"]); assert code == 2 |
| `test_cli.py::test_preflight_success_and_configuration_loaded_once` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `5c65e8fa9affb171776de7497518dea2f2bbd068415dd619820e8e88658c0f01` | assert code == 0; assert output == "preflight: ok\n"; assert loads == [root]; assert code == 0; assert loads == [root] |
| `test_cli.py::test_preflight_collects_independent_refusals` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `bea3fe4f2574b6f87032eebc416b51e8c3dac0d0ad3a3faf5bb92a240ba7122f` | assert code == 2; assert "config: synthetic refusal" in output; assert "STOP exists" in output; assert "AGENTS.md must exist" in output; assert "schema must be" in output; assert len(output.splitlines()) == len(set(output.splitlines())) |
| `test_cli.py::test_claude_missing_auth_verb_uses_read_only_probe` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `701f03fe05a4a500d6e1bea57a8fb3fffedf1630923fe29dc460550de9c23310` | pytest.mark.parametrize("outcome", ["ok", "auth_error", "timeout", "malformed"]); assert (error is None) == (outcome == "ok"); assert len(calls) == 1; assert all(stdout.exists() == (outcome != "ok") for _, stdout, _ in calls); assert all(stderr.exists() == (outcome != "ok") for _, _, stderr in calls); assert "auth" not in argv; assert argv[argv.index("--tools") + 1] == "Read,Grep,Glob"; assert argv[argv.index("--model") + 1] == cfg.seats["holistic"].model; assert argv[argv.index("--effort") + 1] == cfg.seats["holistic"].effort; assert argv[argv.index("--permission-mode") + 1] == "dontAsk"; assert argv[argv.index("--mcp-config") + 1] == '{"mcpServers":{}}'; assert stdin_bytes == b"Reply with OK only.\n"; assert timeout == 60; assert env["FRUTLUPS_SEAT"] == "reviewer" |
| `test_cli.py::test_commit_failure_reports_slice_and_internal_exit` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `9c63182a7260e7824eb05d2fe757a3d7c006862e020dc6b682a93e7a124fd5b4` | assert code == 1; assert rows[-1]["reason"] == "internal"; assert "M001-S01" in rows[-1]["detail"]; assert "synthetic commit failure" in rows[-1]["detail"]; assert "owner must inspect" in rows[-1]["detail"]; assert len(events_of(root, cfg, "accepted")) == 1; assert not any(row["action"] == "accepted" for row in rows) |
| `test_cli.py::test_default_root_and_unknown_usage` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `428cda5e7bae6718b3637d55fa4fe93c676b33f09d476ad634674a74700a6a41` | assert code == 0; assert rows[0]["step"] == "unstarted"; assert rows[1]["slice"] == "M001-S01"; assert row["secs"] is None; assert row["tokens_in"] is None; assert row["tokens_out"] is None; assert row["cost_usd"] is None |
| `test_cli.py::test_prompt_and_stop_rows_survive_cp1252_stdout` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `bdbded7bc242da41f6243f5625efba6ed3a7bd5e0eb4a396df958d92a8f97965` | assert caught.value.code == 3; assert stop.data["reason"] == "seat_output"; assert stop.data["detail"] == "inspect \u2603 and resume"; assert stdout.encoding == "cp1252"; assert stdout.errors == "backslashreplace"; assert buffer.getvalue() == (<br>            b"M001-S01 r1 prompt -> prompts/for_coding_agent/001_M001-S01_r1.md\n"<br>            b"seat_output: inspect \\u2603 and resume\n"<br>        ) |
| `test_cli.py::test_holistic_artifact_rows_label_the_round` | false | converted_native_fixture_preserving_nonlegacy_assertions / M008-S06 | `e75d3575858a35819b262ab331ced7ae9b2332acc2dad10ce973993e6bda9a18` | pytest.mark.parametrize("role", ["holistic_prompt", "holistic_report"]); assert capsys.readouterr().out == f"M001 holistic {role} -> prompts/001_holistic.md\n" |
| `test_cli.py::test_v2_public_runner_and_append_refuse_before_effects` | true | retained_native_with_contract_adapter_update / M008-S06 | `b1fb3497f95ad916c7798e903c582bd56fcbf70f94d636d57236e2429372e80c` | pytest.mark.parametrize("declared", [False, True], ids=["ordinary-v2", "profile-v2"]); assert not (root / "local_state/frutlups").exists(); assert _preflight.prepare(root, cfg, model)[2] == (refusal,); assert loop.preflight(root, cfg, model) == (refusal,); assert loop.run(root, cfg, model) == loop.StopReason.preflight_failed; assert code == 0; assert "M001-S01 r1 unstarted" in output; assert calls == []; assert (root / cfg.ledger).read_bytes() == ledger_before; assert gitws.workspace_witness(root, executable=cfg.git) == before; assert not (root / "local_state/frutlups").exists(); assert code == 2; assert refusal in output |

Preserve independent raw/evidence hashes, bounded reads, UTF-8 refusal, fsync,
CRLF/prefix preservation, fold-before-write, refusal-before-effects and exact
stdout/usage. Legacy enum recognition becomes native enum plus explicit legacy
refusal. Legacy result fallback is an obligation to remove/audit, not retained
native support merely because its old positive test exists.

## Runtime producer/consumer and fallback inventory

This is the r1 static inventory at F `8bc95463dee5e391b41e439bcec44fdfdd11ca0d`,
not a reachability or execution claim. The 20 source hashes and all 129 locations
are preserved below. The semantic surface map includes stop/error writers and
nonmatching infrastructure; a `/1` string alone is not a deletion criterion.


`runtime_inventory.json` binds all 20 top-level runtime modules and lists 129
schema/lifecycle/legacy locations with source owner and line. This is a static
location inventory, not a claim of exercised reachability. The semantic plan is:

| Surface | Obligation / owner |
| --- | --- |
| roadmap._validate, Roadmap defaults, load | S06 removes /1 admission/default; S03 qualifies zero-active/selection/status. Keep independent profile/config formats. |
| ledger._parse_event/read/fold/Event | S06 removes mixed/legacy paths and version-reversal upgrade machinery; native classification/state/limits remain strict. |
| ledger.validate_v2/apply_v2 | S06 detaches schema impersonation (`base.schema=/1`) and legacy callback/state reuse before deleting legacy validation. |
| ledger.append and Loop.append | Remove old lifecycle writers, including stop/error paths; public /2 stays refused until M006-S03. Internal native writers remain bound. |
| Loop._slice, holistic paths, iteration and constructor | Retain actual private native coding/review/verification/approval/recovery; remove obsolete legacy execution branches, not native guards. Public enablement is later. |
| receipt.run and native producer/validator | Retire receipt/1 producer/consumer dependence; preserve accepted receipt/2 frozen-command, witness, deadline and recovery contracts. |
| render.coding/review/holistic versus native renderers | Retire legacy lifecycle templates; retain native custom rendering, bare hash label compatibility, bounds and complete references. |
| verdict.parse(native=False), native wrappers | S06 removes legacy report grammar dependence; do not conflate native custom formatting with legacy lifecycle support. |
| ownership.writer_lock | S06 removes Git-failure escape for /1 standalone append. Native Git-common-directory ownership/fresh checks remain. This path is absent from the old S06 boundary. |
| cli._usage/_status | Audit result-file fallback against native durable accounting; no legacy history reader or fallback may survive by being labelled read-only. M006-S01 owns final usage accounting; S06 must remove legacy lifecycle dependencies. cli.py is absent from old S06 scope. |
| _preflight.admission/prepare, runtime.resolve | S04 binds selected Python/Git and real template checker; S06 removes legacy admission without enabling /2. Recovery precedes new effects. |
| _loop_evidence.workspace_errors, receipts, usage | S06 eliminates schema-dependent legacy validation escape; retain native evidence and conservative unknown accounting. |
| _attempts, _autonomy | Preserve profile/grants, lifetime charges, reservation/settlement and retry contracts. S03 idle additions, S04 handoff, M006-S01 integrated accounting. |
| commit, findings, gitws | Already native consumers shared by recovery/approval/projection. Preserve current witnesses, historical evidence and source-bound findings. DEC03-I edits require prior joint review. |
| config, runtime, commit-manifest, autonomy | /1 here is independently versioned: retain frutlups/1, frutlups.local/1, template.local/1, frutlups.commit-manifest/1, frutlups.autonomy/1. No cosmetic renumbering. |
| _paths, procs, adapters and entry point | Infrastructure, not alternate lifecycle formats. Preserve process ownership and redaction; 'legacy prefix' in a stream helper does not authorize deleting process behavior. |

S06 also omits `verdict.py` despite needing removal of legacy parsing. Do not
silently widen that already broad implementation. Its next assessment must own
these three missing source paths and their real tests or split native grammar
separation from consumer retirement. S01 needs none of these production edits.


| Runtime source | Raw SHA-256 |
| --- | --- |
| `08_pkg/frutlups/src/frutlups/__init__.py` | `7138004bb298e0642f8d804c1e60e95884e90b392e469f20585b825392d6a746` |
| `08_pkg/frutlups/src/frutlups/_attempts.py` | `b0fd403d0f5ef74f69196ee26bbb305e5519149d8689597a9dd0a1c741ef689f` |
| `08_pkg/frutlups/src/frutlups/_autonomy.py` | `e895e14930466b0021a42c263de1e5913dbbc70af39ae60505d477bde47069d7` |
| `08_pkg/frutlups/src/frutlups/_loop_evidence.py` | `68810dcbbf9ad6921879fc6b5c75d8e5a1046f3ac239f929768e75282060b20f` |
| `08_pkg/frutlups/src/frutlups/_paths.py` | `359578b5b947f1397e579f8a92cc4c3a18fb9459b64fd0b20cb4467ec2180e43` |
| `08_pkg/frutlups/src/frutlups/_preflight.py` | `9258f02d708ee0651a34111d5529b69021d6ed3f006bf659d72095c50b48d51b` |
| `08_pkg/frutlups/src/frutlups/cli.py` | `621482859411b82c880447d482e8b9a04d58d1abb188dd45896049c19e9c115e` |
| `08_pkg/frutlups/src/frutlups/commit.py` | `dbff96eb2c520877724be1e949f95450da22ba39e3158e8ad5a65f220bff4165` |
| `08_pkg/frutlups/src/frutlups/config.py` | `cc8c9650b06760dff259f6366e20a8847bae9e91a73bfc15a344c7bb0132d4a3` |
| `08_pkg/frutlups/src/frutlups/findings.py` | `14810fe55b4b65b03e1aeefe9819565b55934695588c06e6fa2a1aa5650a1b79` |
| `08_pkg/frutlups/src/frutlups/gitws.py` | `ad61f0fedef52a8b174d9b81385548d93e49ad3453c1a867fcd6dcf6c849b6e6` |
| `08_pkg/frutlups/src/frutlups/ledger.py` | `16555b1c9b0e87ee4b69def57d0e063e566d7d3911be9e0196ec7d08bbb0006c` |
| `08_pkg/frutlups/src/frutlups/loop.py` | `477f33156aab7cbf71e1fe07469d01cc8f63987e7d74ea56c37ec4d746882c2e` |
| `08_pkg/frutlups/src/frutlups/ownership.py` | `1c6e094d0e800a752ecdead85cf7a40426de00177729d93fbe189bbeb0172c82` |
| `08_pkg/frutlups/src/frutlups/procs.py` | `374a3de27837668446855ca4bc8e207864e499e32ee70b038e30dbc36acda05e` |
| `08_pkg/frutlups/src/frutlups/receipt.py` | `cae1ace063e306b3d669e2a92c46d3ecc8166198ac8c0db65b6b75b4f74a76a5` |
| `08_pkg/frutlups/src/frutlups/render.py` | `2ab5438698ae9ae4529910d7e4b974caa138375ec33e8838739670106170b26e` |
| `08_pkg/frutlups/src/frutlups/roadmap.py` | `37235c6c13c0a4dd0d724966557ff12e1f028dd6894a53d70f89e9c6900d6b05` |
| `08_pkg/frutlups/src/frutlups/runtime.py` | `ba046dfa3b2c5d30053943a03c1570e7256b4023b0314bd78f99eac29bfacd8c` |
| `08_pkg/frutlups/src/frutlups/verdict.py` | `3bccbb4bc8b5d2375433d6c505e631f871fd397e3bc9d16e9001ad63a00cdd37` |

| Source / line | Function or class | Exact inventoried expression |
| --- | --- | --- |
| `08_pkg/frutlups/src/frutlups/_attempts.py:36` | `event` | return dict(schema=ledger.SCHEMA_V2, t=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), |
| `08_pkg/frutlups/src/frutlups/_attempts.py:116` | `Operation` | ledger._need(not review or model.schema == ledger.ROADMAP_V2 and role in ("reviewer", "holistic"), |
| `08_pkg/frutlups/src/frutlups/_attempts.py:125` | `Operation` | if self.model.schema == ledger.ROADMAP_V2: |
| `08_pkg/frutlups/src/frutlups/_autonomy.py:7` | `module` | SCHEMA = "frutlups.autonomy/1" |
| `08_pkg/frutlups/src/frutlups/_autonomy.py:27` | `validate` | def validate(event): |
| `08_pkg/frutlups/src/frutlups/_autonomy.py:31` | `validate` | p._need(set(event) - {"schema", "t", "ev", "by"} == FIELDS[ev], f"invalid {ev} fields") |
| `08_pkg/frutlups/src/frutlups/_autonomy.py:248` | `apply` | def apply(event, rm, states, done, extras): |
| `08_pkg/frutlups/src/frutlups/_autonomy.py:253` | `apply` | enabled = rm.get("autonomy") == {"schema": SCHEMA} |
| `08_pkg/frutlups/src/frutlups/_autonomy.py:266` | `apply` | p._need(event.get("schema") == p.SCHEMA_V2, "run grants require /2 ledger") |
| `08_pkg/frutlups/src/frutlups/_loop_evidence.py:27` | `evidence_paths` | if any(event.schema == ledger.SCHEMA_V2 for event in events): |
| `08_pkg/frutlups/src/frutlups/_loop_evidence.py:51` | `workspace_errors` | if any(e.schema == ledger.SCHEMA_V2 for e in events): |
| `08_pkg/frutlups/src/frutlups/_loop_evidence.py:119` | `review_receipt` | fields = {"schema", "slice", "round", "t", "base_commit", "tree_dirty_before", "commands", |
| `08_pkg/frutlups/src/frutlups/_loop_evidence.py:121` | `review_receipt` | ledger._need(set(value) == fields and value["schema"] == "frutlups.receipt/2" |
| `08_pkg/frutlups/src/frutlups/_loop_evidence.py:336` | `no_progress` | fields = {"schema", "slice", "round", "t", "base_commit", "tree_dirty_before", "commands", |
| `08_pkg/frutlups/src/frutlups/_loop_evidence.py:564` | `usage` | def usage(results, *, coder=False): |
| `08_pkg/frutlups/src/frutlups/_preflight.py:121` | `_static` | if roadmap.schema == ledger.ROADMAP_V2 and state.intents: |
| `08_pkg/frutlups/src/frutlups/_preflight.py:271` | `admission` | def admission(root, cfg=None, roadmap=None): |
| `08_pkg/frutlups/src/frutlups/_preflight.py:284` | `admission` | if roadmap is not None and roadmap.schema != "frutlups.roadmap/1": |
| `08_pkg/frutlups/src/frutlups/_preflight.py:315` | `prepare` | def prepare(root, cfg=None, roadmap=None): |
| `08_pkg/frutlups/src/frutlups/cli.py:142` | `_status` | commits = commit.status(root, events, model, executable=cfg.git) if model.schema == ledger.ROADMAP_V2 and state.intents else [] |
| `08_pkg/frutlups/src/frutlups/commit.py:19` | `module` | SCHEMA = "frutlups.commit-manifest/1" |
| `08_pkg/frutlups/src/frutlups/commit.py:153` | `_request_intent` | if rm.schema != "frutlups.roadmap/2": |
| `08_pkg/frutlups/src/frutlups/commit.py:211` | `_request_intent` | "schema": SCHEMA, |
| `08_pkg/frutlups/src/frutlups/commit.py:254` | `_manifest` | keys = {"schema", "id", "scope", "parent", "index_tree", "entries", "ledger", "git_boundary"} |
| `08_pkg/frutlups/src/frutlups/commit.py:257` | `_manifest` | if (manifest["schema"], manifest["id"], manifest["scope"], manifest["parent"]) != ( |
| `08_pkg/frutlups/src/frutlups/commit.py:258` | `_manifest` | SCHEMA, |
| `08_pkg/frutlups/src/frutlups/commit.py:758` | `approve` | return None  # Legacy/ledger-only approvals never acquire an inferred intent. |
| `08_pkg/frutlups/src/frutlups/config.py:12` | `module` | "schema", "until", "max_corrective_rounds", "max_jobs", "max_wall_minutes", |
| `08_pkg/frutlups/src/frutlups/config.py:16` | `module` | LOCAL_KEYS = {"schema", "pi", "claude", "git", "llloom", "path_dirs", "env_passthrough"} |
| `08_pkg/frutlups/src/frutlups/config.py:22` | `ConfigError` | """A configuration file is missing or violates its schema.""" |
| `08_pkg/frutlups/src/frutlups/config.py:43` | `Config` | schema: str |
| `08_pkg/frutlups/src/frutlups/config.py:70` | `Config` | if main["schema"] != "frutlups/1": |
| `08_pkg/frutlups/src/frutlups/config.py:71` | `Config` | raise ConfigError("schema: must be frutlups/1") |
| `08_pkg/frutlups/src/frutlups/config.py:72` | `Config` | if local["schema"] != "frutlups.local/1": |
| `08_pkg/frutlups/src/frutlups/config.py:73` | `Config` | raise ConfigError("schema: must be frutlups.local/1") |
| `08_pkg/frutlups/src/frutlups/config.py:106` | `Config` | main["schema"], until, **integers, commit_on_accept=main["commit_on_accept"], |
| `08_pkg/frutlups/src/frutlups/findings.py:28` | `report_events` | event.get("schema") == ledger.SCHEMA_V2, |
| `08_pkg/frutlups/src/frutlups/findings.py:29` | `report_events` | "unsupported legacy history; use pinned v4", |
| `08_pkg/frutlups/src/frutlups/findings.py:90` | `Projection` | event.get("schema") == ledger.SCHEMA_V2, |
| `08_pkg/frutlups/src/frutlups/findings.py:91` | `Projection` | "unsupported legacy history; use pinned v4", |
| `08_pkg/frutlups/src/frutlups/findings.py:169` | `validate_record` | event = {"ev": "reviewed", "schema": ledger.SCHEMA_V2, "by": by} |
| `08_pkg/frutlups/src/frutlups/ledger.py:21` | `module` | SCHEMA = "frutlups.ledger/1" |
| `08_pkg/frutlups/src/frutlups/ledger.py:78` | `module` | COMMON = {"schema", "t", "ev", "by"} |
| `08_pkg/frutlups/src/frutlups/ledger.py:94` | `Event` | schema: str = SCHEMA |
| `08_pkg/frutlups/src/frutlups/ledger.py:170` | `_parse_event` | if value.get("schema") == SCHEMA_V2: |
| `08_pkg/frutlups/src/frutlups/ledger.py:178` | `_parse_event` | value.get("milestone"), value.get("round"), _freeze(data), SCHEMA_V2) |
| `08_pkg/frutlups/src/frutlups/ledger.py:182` | `_parse_event` | raise LedgerError(f"{line}: invalid schema or event") from exc |
| `08_pkg/frutlups/src/frutlups/ledger.py:183` | `_parse_event` | _need(value.get("schema") == SCHEMA and ev in FIELDS, f"{line}: invalid schema or event") |
| `08_pkg/frutlups/src/frutlups/ledger.py:259` | `read` | def read(path: Path) -> tuple[Event, ...]: |
| `08_pkg/frutlups/src/frutlups/ledger.py:275` | `read` | _need(not upgraded or event.schema == SCHEMA_V2, "legacy event after /2 history") |
| `08_pkg/frutlups/src/frutlups/ledger.py:276` | `read` | upgraded = upgraded or event.schema == SCHEMA_V2 |
| `08_pkg/frutlups/src/frutlups/ledger.py:296` | `fold` | def fold(events: Sequence[Event], roadmap: Roadmap) -> ProjectState: |
| `08_pkg/frutlups/src/frutlups/ledger.py:301` | `fold` | rm = {"schema": roadmap.schema, "milestones": [ |
| `08_pkg/frutlups/src/frutlups/ledger.py:311` | `fold` | is_v2 = event.schema == SCHEMA_V2 |
| `08_pkg/frutlups/src/frutlups/ledger.py:312` | `fold` | _need(not is_v2 or roadmap.schema == ROADMAP_V2, "/2 history requires /2 roadmap") |
| `08_pkg/frutlups/src/frutlups/ledger.py:313` | `fold` | _need(not upgraded or is_v2, "legacy event after /2 history") |
| `08_pkg/frutlups/src/frutlups/ledger.py:435` | `_event_dict` | output = {"schema": event.schema, "t": event.t, "ev": event.ev.value, "by": event.by} |
| `08_pkg/frutlups/src/frutlups/ledger.py:457` | `append` | _need(roadmap.schema == "frutlups.roadmap/1", "/2 ledger append is not enabled") |
| `08_pkg/frutlups/src/frutlups/ledger.py:459` | `append` | _need(raw.get("schema") == SCHEMA, "/2 ledger append is not enabled") |
| `08_pkg/frutlups/src/frutlups/ledger.py:465` | `append` | _need(roadmap.schema == "frutlups.roadmap/1", "/2 ledger append is not enabled") |
| `08_pkg/frutlups/src/frutlups/ledger.py:491` | `_append_operation` | _need(roadmap.schema == ROADMAP_V2 and roadmap.autonomy is not None, "profile required") |
| `08_pkg/frutlups/src/frutlups/ledger.py:492` | `_append_operation` | _need(raw.get("schema") == SCHEMA_V2 and raw.get("by") == "frutlups" |
| `08_pkg/frutlups/src/frutlups/ledger.py:546` | `_append_review` | _need(roadmap.schema == ROADMAP_V2 and raw.get("schema") == SCHEMA_V2 |
| `08_pkg/frutlups/src/frutlups/ledger.py:667` | `_append_coding` | _need(roadmap.schema == ROADMAP_V2 and raw.get("schema") == SCHEMA_V2 |
| `08_pkg/frutlups/src/frutlups/ledger.py:712` | `_append_approval` | _need(roadmap.schema == ROADMAP_V2 and raw.get("schema") == SCHEMA_V2, |
| `08_pkg/frutlups/src/frutlups/ledger.py:794` | `status_text` | def status_text(roadmap: Roadmap, state: ProjectState) -> str: |
| `08_pkg/frutlups/src/frutlups/ledger.py:806` | `module` | SCHEMA_V2 = "frutlups.ledger/2" |
| `08_pkg/frutlups/src/frutlups/ledger.py:861` | `validate_v2` | def validate_v2(event, legacy_validate): |
| `08_pkg/frutlups/src/frutlups/ledger.py:862` | `validate_v2` | _need(event.get("schema") == SCHEMA_V2, "unknown ledger schema") |
| `08_pkg/frutlups/src/frutlups/ledger.py:877` | `validate_v2` | keys = set(event) - {"schema", "t", "ev", "by"} |
| `08_pkg/frutlups/src/frutlups/ledger.py:882` | `validate_v2` | base["schema"] = "frutlups.ledger/1" |
| `08_pkg/frutlups/src/frutlups/ledger.py:885` | `validate_v2` | set(base) == {"schema", "t", "ev", "by", "scope", "round", "role", "path", "sha"}, |
| `08_pkg/frutlups/src/frutlups/ledger.py:896` | `validate_v2` | legacy_validate(base) |
| `08_pkg/frutlups/src/frutlups/ledger.py:1015` | `apply_v2` | """Apply only new transitions; return whether the legacy fold should skip it.""" |
| `08_pkg/frutlups/src/frutlups/ledger.py:1018` | `apply_v2` | event.get("schema") != SCHEMA_V2 or ev != "unblocked", |
| `08_pkg/frutlups/src/frutlups/ledger.py:1174` | `module` | "schema", |
| `08_pkg/frutlups/src/frutlups/ledger.py:1210` | `envelope` | value["schema"] == "frutlups.envelope/2" |
| `08_pkg/frutlups/src/frutlups/ledger.py:1251` | `validate_outcome` | "schema", |
| `08_pkg/frutlups/src/frutlups/ledger.py:1266` | `validate_outcome` | value["schema"] == "frutlups.outcome/2" |
| `08_pkg/frutlups/src/frutlups/ledger.py:1297` | `manifest` | isinstance(value, dict) and set(value) == {"schema", "slice", "round", "changed"}, |
| `08_pkg/frutlups/src/frutlups/ledger.py:1301` | `manifest` | value["schema"] == "frutlups.manifest/2" |
| `08_pkg/frutlups/src/frutlups/ledger.py:1414` | `validate_history` | if event.ev == Ev.verified and event.schema == SCHEMA_V2: |
| `08_pkg/frutlups/src/frutlups/ledger.py:1416` | `validate_history` | _need(isinstance(value, dict) and value.get("schema") == "frutlups.receipt/2" |
| `08_pkg/frutlups/src/frutlups/ledger.py:1421` | `validate_history` | if event.schema == SCHEMA_V2 and event.ev in ( |
| `08_pkg/frutlups/src/frutlups/ledger.py:1453` | `validate_history` | elif event.schema == SCHEMA_V2 and event.ev == Ev.artifact and row.get("role") == "holistic_report": |
| `08_pkg/frutlups/src/frutlups/ledger.py:1472` | `validate_history` | _need(isinstance(value, dict) and value.get("schema") == "frutlups.commit-manifest/1" |
| `08_pkg/frutlups/src/frutlups/loop.py:49` | `Loop` | if roadmap.schema != "frutlups.roadmap/1": |
| `08_pkg/frutlups/src/frutlups/loop.py:68` | `Loop` | ledger.append( |
| `08_pkg/frutlups/src/frutlups/loop.py:71` | `Loop` | "schema": ledger.SCHEMA, |
| `08_pkg/frutlups/src/frutlups/loop.py:101` | `Loop` | if self.roadmap.schema == ledger.ROADMAP_V2: |
| `08_pkg/frutlups/src/frutlups/loop.py:115` | `Loop` | if self.roadmap.schema == ledger.ROADMAP_V2: |
| `08_pkg/frutlups/src/frutlups/loop.py:146` | `Loop` | if self.roadmap.schema == ledger.ROADMAP_V2: |
| `08_pkg/frutlups/src/frutlups/loop.py:455` | `Loop` | if self.roadmap.schema == ledger.ROADMAP_V2: |
| `08_pkg/frutlups/src/frutlups/loop.py:458` | `Loop` | if self.roadmap.schema == ledger.ROADMAP_V2 and current.step == "verifying": |
| `08_pkg/frutlups/src/frutlups/loop.py:464` | `Loop` | if self.roadmap.schema == ledger.ROADMAP_V2 and current.step == "reviewing": |
| `08_pkg/frutlups/src/frutlups/loop.py:467` | `Loop` | if self.roadmap.schema == ledger.ROADMAP_V2 and current.step in ("unstarted", "fix", "coding", "blocked"): |
| `08_pkg/frutlups/src/frutlups/loop.py:473` | `Loop` | if self.roadmap.schema == ledger.ROADMAP_V2 and current.step in ("accept_pending", "accepted"): |
| `08_pkg/frutlups/src/frutlups/loop.py:570` | `Loop` | result = receipt.run(self.root, item, current.round, self.roadmap, self.cfg, False, |
| `08_pkg/frutlups/src/frutlups/loop.py:712` | `Loop` | result = receipt.run(self.root, item, current.round, self.roadmap, self.cfg, False, |
| `08_pkg/frutlups/src/frutlups/loop.py:1089` | `Loop` | json.dumps(dict(schema="frutlups.manifest/2", **scope, |
| `08_pkg/frutlups/src/frutlups/loop.py:1112` | `Loop` | if self.roadmap.schema == ledger.ROADMAP_V2: |
| `08_pkg/frutlups/src/frutlups/loop.py:1115` | `Loop` | if self.roadmap.schema == ledger.ROADMAP_V2 and ( |
| `08_pkg/frutlups/src/frutlups/loop.py:1122` | `Loop` | if self.roadmap.schema == ledger.ROADMAP_V2: |
| `08_pkg/frutlups/src/frutlups/loop.py:1259` | `Loop` | if self.roadmap.schema == ledger.ROADMAP_V2: |
| `08_pkg/frutlups/src/frutlups/loop.py:1291` | `Loop` | if self.roadmap.schema != "frutlups.roadmap/1": |
| `08_pkg/frutlups/src/frutlups/loop.py:1312` | `Loop` | if self.roadmap.schema == ledger.ROADMAP_V2 and any("commit_intent" in e.data for e in events): |
| `08_pkg/frutlups/src/frutlups/loop.py:1368` | `run` | def run(root, cfg=None, roadmap=None, until=None, clock=time.monotonic, *, once=False, emit=None): |
| `08_pkg/frutlups/src/frutlups/ownership.py:28` | `writer_lock` | # Preserve the explicitly supported legacy standalone append API. |
| `08_pkg/frutlups/src/frutlups/ownership.py:29` | `writer_lock` | if roadmap is not None and roadmap.schema == "frutlups.roadmap/1": |
| `08_pkg/frutlups/src/frutlups/procs.py:189` | `run_process` | """Drain owned children; retain the legacy prefix or a bounded diagnostic tail. |
| `08_pkg/frutlups/src/frutlups/receipt.py:27` | `NativeReceipt` | schema: str |
| `08_pkg/frutlups/src/frutlups/receipt.py:79` | `validate_native` | ledger._need(value["schema"] == "frutlups.receipt/2" and value["slice"] == current.id |
| `08_pkg/frutlups/src/frutlups/receipt.py:181` | `Receipt` | schema: str |
| `08_pkg/frutlups/src/frutlups/receipt.py:243` | `_public_argv` | # Keep the legacy public spelling for the executable outside the project. |
| `08_pkg/frutlups/src/frutlups/receipt.py:299` | `run` | def run( |
| `08_pkg/frutlups/src/frutlups/receipt.py:309` | `run` | if roadmap.schema == "frutlups.roadmap/2": |
| `08_pkg/frutlups/src/frutlups/render.py:177` | `_findings_context` | def read(relative): |
| `08_pkg/frutlups/src/frutlups/render.py:232` | `coding_envelope` | value = dict(schema="frutlups.envelope/2", slice=item.id, round=state.round, |
| `08_pkg/frutlups/src/frutlups/render.py:277` | `outcome_contract` | value = dict(schema="frutlups.outcome/2", slice=sid, round=round_no, |
| `08_pkg/frutlups/src/frutlups/roadmap.py:15` | `module` | "schema", "project", "brief", "verification", "allowed_prefixes", "forbidden", |
| `08_pkg/frutlups/src/frutlups/roadmap.py:64` | `Roadmap` | schema: str = "frutlups.roadmap/1" |
| `08_pkg/frutlups/src/frutlups/roadmap.py:122` | `_validate` | v2 = data.get("schema") == "frutlups.roadmap/2" |
| `08_pkg/frutlups/src/frutlups/roadmap.py:123` | `_validate` | if data.get("schema") not in ("frutlups.roadmap/1", "frutlups.roadmap/2"): |
| `08_pkg/frutlups/src/frutlups/roadmap.py:124` | `_validate` | errors.append("schema must be frutlups.roadmap/1 or frutlups.roadmap/2") |
| `08_pkg/frutlups/src/frutlups/roadmap.py:126` | `_validate` | if not v2 or data["autonomy"] != {"schema": "frutlups.autonomy/1"}: |
| `08_pkg/frutlups/src/frutlups/roadmap.py:127` | `_validate` | errors.append("autonomy requires /2 and exactly {schema: frutlups.autonomy/1}") |
| `08_pkg/frutlups/src/frutlups/roadmap.py:328` | `load` | schema=data["schema"], runtime=MappingProxyType(dict(data.get("runtime", {}))), |
| `08_pkg/frutlups/src/frutlups/runtime.py:41` | `resolve_runtime` | if set(data) != {"schema", "python"} or data.get("schema") != "template.local/1": |
| `08_pkg/frutlups/src/frutlups/runtime.py:42` | `resolve_runtime` | raise ValueError("project.local.toml requires schema template.local/1 and python") |

## Integrity and discovery boundary

`tests/conftest.py` excludes exactly `fixtures/v5_development/tests` from F
collection. Every upstream file stays in the 83-member tree; the maintained
fixture definitions and previous test nodes stay present. New maintained checks
verify exact membership/raw hashes, safe member identities, mode metadata,
complete case/setup/oracle accounting, all predecessors and clean Git staging
and checkout. Only `local_state/README.md` receives a narrow ignore exception;
arbitrary local state and cache files remain ignored. No copied test is run as
F qualification and no skip/xfail substitutes for a missing consumer.

The coder uses only the issued three-module focused command, with a 600-second
cap clipped to its remaining 90 active minutes. Collection before/after is
only diagnostic. The architect owns one unchanged final full verifier on final
bytes, capped at 2400 seconds including recording and shared with independent
review. Require full discovery, zero unexpected skips, exit 0, no timeout and a
stable witness. Failure, unknown child disposition, exhausted allowance or
post-full edits requires reassessment, not an automatic rerun. The 600-second
development target is aspirational under D065. Prior usage remains charged.

The final full gate and independent review are pending. Minimum Python, POSIX,
native symlinks, sleep/resume, installed/public and live qualification are not
established by this intake. No new dependency, runtime import, migration,
workspace commit, publication or external write is part of S01.
