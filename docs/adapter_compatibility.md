# M010-S02 adapter compatibility evidence

This candidate implements the accepted S04 design and the S02 record-limit
amendment. One main invocation remains accountable for required helper work.
There is no helper ledger, attestation, scheduler, replacement approval, model
catalogue, or helper count/depth limit. Public execution remains refused.
This note records offline development evidence; it does not accept the slice
or close S04-008 or any historical finding.

## Consuming-project setting and limits

```toml
[limits]
pi_record_mib = 32
```

Omitting the table or using an empty table selects 2 MiB. The value must be a
positive TOML integer. Boolean, zero, negative, fractional, string, unknown-key
and invalid-table inputs refuse before dispatch. MiB is converted once using
1024 * 1024; converted bytes must not exceed JavaScript's
Number.MAX_SAFE_INTEGER. The greatest representable whole-MiB setting is
8,589,934,591. This is a representation boundary, not an allocation suggestion.
32 MiB is an example, not a measured recommendation or a new default.

The job freezes the effective byte limit before reservation. Pi's binding
includes that limit, shared dispatch framing, adapter/parser bytes and packaged
extension bytes. The adapter-owned FRUTLUPS_PI_RECORD_BYTES transport overrides
ambient passthrough. The extension freezes it on initialization and forwards it
at every nested launch; task arguments cannot change cwd, tools or the limit.
Missing transport uses the default; invalid transport refuses. Changing the
cap or framing refuses a contract-equal retry; historical reservations retain
their recorded allowances and bindings.

Both readers measure encoded JSONL bytes excluding LF. A record at the limit
is allowed; a larger record causes sticky output failure through drain and EOF,
including after later valid settlement. No discarded body supplies terminal
state. Diagnostics identify the effective MiB/byte limit and limits.pi_record_mib.
Malformed input remains a failure. Known spend survives failures as partial;
absence of known spend is unknown. A failed helper returns a failed summary,
which the main agent may recover within its authority; it does not independently
veto a subsequently successful main invocation.

The separate 512 KiB encoded delegation-return budget, 1 MiB final-text cap,
8 MiB retained-output cap and prompt limits remain in force. Readers allocate
incrementally rather than preallocating the selected cap. Memory scales with
active readers, retained helper results and decoded JSON; decoding can exceed
encoded size. The setting is not a whole-seat memory budget or a scaling or
two-hour qualification.

## Adapter behavior

Pi explicitly loads the fresh packaged pi_delegate/index.ts with -e while
implicit extensions, skills and prompt templates stay disabled. Ordinary tasks
forward their selected/current model, thinking level, current role tools and
parent cwd. Optional context thinking falls back to Pi's current-thinking API.
The registered tool awaits child close, drains output, returns bounded labelled
summaries with usage, and responds to abort with termination and a five-second
best-effort escalation. Unexpected launch rejection waits for already launched
siblings. Existing outer process ownership remains the final cleanup boundary.

Pi requires agent_settled and a valid latest final assistant result, tracks
pending assistant work and failed automatic retries, and accounts assistant and
delegation tool-result usage without counting aggregate/event copies twice.
Claude enables Agent in both role tool sets. Its reserved environment sets
CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0 and derives BASH_DEFAULT_TIMEOUT_MS,
BASH_MAX_TIMEOUT_MS and CLAUDE_ASYNC_AGENT_STALL_TIMEOUT_MS from reserved
milliseconds. Finite outer timeouts and existing permission/MCP controls remain.
Claude sums complete modelUsage token entries and uses total_cost_usd once;
missing or invalid aggregate coverage falls back to known top-level usage as
partial. A failed main result is never labelled complete.

render.py owns the shared main-agent framing. Both adapters send those exact
bytes, job/render bounds count them, and reservations bind them. Probes now use
tools_for(Role.reviewer). Fixed inconsistent jobs retain three exact distinct
guards: reservation 'job tools differ from role', Pi 'Pi job tools do not match
its role', and Claude 'Claude job tools do not match its role'. Only verification
reservations apply the envelope timeout cap; role, probe and remaining-run
bounds and historical allowance replay remain separately enforced.

## Acceptance-to-test map

| Acceptance | Offline evidence |
| --- | --- |
| C01 | test_config.py tables: omitted/empty/default/custom, maximum whole-MiB representation, invalid values/keys/table/overflow; compatible defaulted Config field. |
| C02 | test_project_limit_reaches_frozen_real_seat_and_file_reader exercises one consuming config through Loop._job, actual PiSeat.run with controlled transport and explicit file inspection; nested actual handler checks 4 MiB; binding/default equivalence, stale retry and ambient override tests. |
| C03 | Shared generated cases feed Python and the actual TypeScript reader and handler: below/at/above, chunks 1/7/65536, multibyte, EOF without LF, malformed/BOM/NaN/invalid UTF-8/truncation, oversized retry/aggregate/late/EOF, failed and recovered retry/helper, missing settlement and later start. A real aggregate over 2 MiB fails at default and passes at 4 MiB. Existing final-text, usage, bounded-state and after-capture tests remain. |
| C04 | test_seats.py argv/env/results; actual registered-handler forwarding, nesting, current-thinking fallback, helper-error recovery, partial numeric overflow and awaited launch rejection; Claude aggregate/fallback tables and reserved wait settings. |
| C05 | test_attempts_v2.py probe construction, exact distinct guards, cap/framing retry refusal, verification-only timeout table, receipt settlement and historical replay; test_render.py plus exact Loop._job UTF-8 boundaries for both roles/adapters. |
| C06 | test_actual_typescript_handler_with_owned_children invokes the packaged TypeScript factory/registered execute through Node and owned controlled processes; resource and package-data check; single offline Pi help/load attempt below. |
| C07 | Before/after collection and named hash comparisons below; no baseline IDs lost, protected files and installed source pins unchanged. Synthetic adaptations and qualification gaps are explicit below. |
| C08 | Coder focused subsets only, each bounded at 600 seconds; actual-handler process deadline 45 seconds, main timeout checks 2 seconds. Architect alone owns the final combined scoped gate and shares its receipt with review. |
| C09 | No provider invocation, installation/update, new runtime dependency, public enablement, commit, publication, full-package execution or two-hour experiment. Installed/live qualification remains M006/M007. |

## Observed commands and results

All pytest invocations used the existing Python 3.14.3 runtime, -B,
-p no:cacheprovider and an external disposable --basetemp. No dependency was
installed. Below, TEST means 08_pkg/frutlups/tests and SCRATCH means the external
short scratch directory; these aliases avoid tracked machine-local paths.
Commands were bounded with subprocess.run(timeout=600). The installed Pi
launcher is PI_SHIM and the extension resource is EXTENSION.

| Command suffix after `python -B -m pytest` | Observed result |
| --- | --- |
| `--collect-only -q` with the sorted 38 root `TEST/test_*.py` modules, `-p no:cacheprovider --basetemp SCRATCH/collection-before` | Exit 0; 1,647 IDs; two existing iterable-parametrization deprecation warnings. No copied-template discovery or tests executed. |
| `-q TEST/test_config.py TEST/test_seats.py -p no:cacheprovider --basetemp SCRATCH/focused-2` | 80 passed, 1 failed: old Fable top-level expectation. Corrected to aggregate modelUsage below. |
| `-q TEST/test_adapter_delegation.py -p no:cacheprovider --basetemp SCRATCH/delegation-1` | 76 passed in 13.92 s before later edge/boundary additions. |
| `-q TEST/test_config.py TEST/test_seats.py -p no:cacheprovider --basetemp SCRATCH/focused-3` | 94 passed in 12.79 s. |
| `-q TEST/test_attempts_v2.py TEST/test_render.py TEST/test_incidents.py::test_pi_terminal_after_cap_preserves_final_text_and_usage -p no:cacheprovider --basetemp SCRATCH/binding-1` | 69 passed, 3 failed in 105.79 s. Fixture failures: old Claude fake probe could not recognize framing; two new verification cases omitted required receipts. Fixed by controlled real-adapter transport and real scratch receipts, preserving the guards. |
| `-q TEST/test_attempts_v2.py::test_probe_construction_uses_normal_reviewer_tools TEST/test_attempts_v2.py::test_verification_only_envelope_cap_and_role_run_bounds TEST/test_adapter_delegation.py::test_loop_job_counts_exact_sent_framing TEST/test_adapter_delegation.py::test_actual_typescript_handler_with_owned_children -s -p no:cacheprovider --basetemp SCRATCH/corrections-1` | 21 passed in 20.35 s. |
| `-q TEST/test_seats.py TEST/test_adapter_delegation.py -s -p no:cacheprovider --basetemp SCRATCH/edge-corrections` | 169 passed in 25.75 s, including strict shared malformed cases, usage overflow and awaited launch rejection. |
| `-q TEST/test_adapter_delegation.py::test_actual_typescript_handler_with_owned_children -s -p no:cacheprovider --basetemp SCRATCH/thinking-fallback` | 1 passed in 6.42 s on the final extension/handler bytes, including current-thinking API fallback. |
| `--collect-only -q` with the sorted 39 root `TEST/test_*.py` modules, `-p no:cacheprovider --basetemp SCRATCH/collection-after` | Exit 0; 1,768 IDs; all 1,647 baseline IDs retained, 121 added. Same two deprecation warnings. The first comparison script used the wrong node prefix; reparsing the saved output corrected it without repeating collection. |

An earlier sandboxed focused attempt failed on pytest scratch-directory access;
the focused process commands subsequently used the approved escalation. An
initial Python syntax check caught a quoting error in the new synthetic CLI
wrapper; it was corrected. These failures are not final-gate results.

The final handler observation on win32 reported 22 shared cases, chunks
1/7/65536, a 4,194,304-byte nested cap, and 523,179 encoded return bytes within
524,288. Sixty helpers each generated exactly 50 KiB of escape-heavy text;
input=60, cacheRead=120, cacheWrite=180, output=240, total cost=7.5 were asserted
individually despite labelled omission of text and per-helper details. Two
children with one/two-second waits took 2,067.9251 ms; abort returned only after
the controlled child closed and its PID was no longer live. Both real adapters'
short owned-process tests waited for process exit after a valid result and
cleaned an owned child on the actual outer deadline.

The single permitted loader attempt at 2026-10-05 18:41:46 UTC used exact argv
`PI_SHIM --offline -ne -e EXTENSION --help` through existing run_process with a
30-second deadline. Observed exit 0, no timeout/exception, 1.213 s, 10,325 stdout
bytes, and a 151-byte settings-lock EPERM warning naming the local settings
file. No extension-load error was emitted. The warning's machine-local path is
intentionally omitted here. This help attempt preceded the final edge
corrections; the actual final registered handler was subsequently exercised
under Node. Help success is not provider, installed delegated execution, or a
proof of all jiti/runtime behavior. The permitted loader attempt was not repeated.

## Preserved tests and qualification limits

Pinned Pi/Claude samples, fake CLI scripts and historical corpora were not
edited. Synthetic Pi transports/terminal records in admitted tests append
agent_settled because agent_end alone no longer settles. The attempts fixture
wraps the existing fake Pi CLI, preserving its bytes and lifecycle assertions.
The Claude probe fixture supplies a pinned successful result at actual adapter
transport because its old fake CLI recognizes only unframed text.

The existing assistant-usage test still asserts the same exact totals. Its
synthetic non-model toolResult no longer copies assistant usage (real delegated
usage is now counted); message_start is before the completed final, and explicit
settlement is added. Pi missing-end/empty diagnostic assertions now require
'missing settled final message' in place of 'missing terminal agent_end';
explicit pytest IDs retain their original collection identities. Missing-text
uses settlement without assistant text so its separate refusal still executes.

Role-tool assertions add subagent/Agent while retaining read restrictions,
permission assertions and both mismatch guards. Dispatch assertions include
the packaged extension, reserved settings and exact shared framing. Render
boundaries count those bytes. The pinned Fable result changes the asserted
token expectation from 4,693/4 to aggregate 5,219/16, retaining its original
parameter ID; cost remains exactly 0.005488. Opus remains 4,585/4 and 0.0024015.
The after-capture incident retains its final-text and exact usage assertions.
Only current candidate archive membership adds the extension; its frozen
115-path inventory is unchanged. No test deletion, skip, xfail or reduced
parametrization was introduced.

Starting checks matched all 36 input bindings, all 396 protected entries,
17 installed Pi source pins and the bound 1,064,585-byte ledger prefix. The
final handoff checks matched the 22 unchanged bindings, all 396 protected
entries, all 17 installed pins and that exact ledger prefix. Changed Python
syntax and git diff --check passed; Git emitted only CRLF-to-LF conversion
notices for the touched loop/preflight files. Existing architect-owned roadmap/generated-view
changes and issuance append are outside the coder change set. The index remains
untouched. Changed raw SHA-256 values are returned in the coder handoff.

Windows controlled direct-child abort and existing outer owned-tree timeout
cleanup were exercised. POSIX graceful-to-force escalation was not observed on
this host; neither direct abort nor the harness proves every nested cancellation
race. Package-data metadata and source resource access were checked; archive/
wheel installation, installed CLI delegated tasks, real Claude waiting, live
provider accounting and large-scale/two-hour behavior were not exercised.
The settings warning and help-only loader scope remain explicit limitations.

The final combined scoped pytest command in prompt 142 remains unrun by the
coder. The architect must run it once on delivered final bytes with a 600-second
receipt, then independent review uses that receipt. It is not a full-package
qualification. Historical S01/S03 disposition and S04-008 closure remain with
the architect and independent reviewer respectively; this candidate changes no
accepted reports or history.

## Round-2 usage correction addendum (M010-S02-001/002)

Earlier sections remain the round-1 record; they are not restated as new proof.
This addendum covers only the two usage corrections from checkpoint 02abc48.

001: the extension now aggregates all pinned Pi Usage fields (input, output,
cacheRead, cacheWrite, totalTokens and the five cost fields input, output,
cacheRead, cacheWrite, total; optional cacheWrite1h/reasoning are not
aggregated). Native result.usage is returned only when the aggregate is
complete: every child completed, every observed usage record supplied every
required field, and no sum exceeded the numeric limit. Otherwise native usage is
omitted, because Pi Usage cannot mark a field unknown, and the known values go
once into details.usage beside usage_completeness. Missing fields stay absent,
never zero; this survives compaction of per-helper details. Both readers (child
RecordReader and the root seats.pi stream) use details.usage only for a subagent
tool result with no native usage, and always label that coverage partial. A
child record now counts as complete only when all ten required fields are valid;
the root Python stream's completeness criteria and totals are unchanged.

002: a synchronous child-launch rejection becomes a failed summary
(failure_class transport, usage unknown, bounded error text). Promise.allSettled
still awaits every started sibling, whose observed tokens and cost are retained.
The tool result is an ordinary error result the parent can recover from; there
is no relaunch, root veto or change to abort/deadline handling.

Test adaptations: the synthetic USAGE fixture in test_adapter_delegation.py and
the harness fake-child default now carry the complete pinned shape with the same
token values; cost components sum exactly to the previous 0.125 total, so all
previous count/total expectations are unchanged. Harness shared-case assertions
add totalTokens and each cost component. The prior overflow case reads its
unchanged expected values from details.usage, since partial results no longer
carry native usage. The NUL-model case changes from expecting rejection to
asserting the waited sibling, failed summary and exact retained usage.
New handler cases: complete native shape (single, 60-way compacted, nested),
missing-field, mixed, invalid cost component, per-child overflow, nested partial,
launch rejection with sibling, all launches failed (unknown), nested rejection,
plus root reader recovery. The Python test feeds seven returned partial results
through seats.pi._Stream with a recovering parent final and asserts exact totals.
No node ID was renamed or removed; cases.json and pinned samples are unchanged.

Negative controls (scratch only): the new harness fails against the round-1
extension at the totalTokens assertion, and fails with the NUL-byte TypeError
when only the round-1 rejection throw is restored. The installed Pi runtime,
live providers and real session totals for omitted native usage were not
exercised; Pi session statistics exclude partial delegation spend, which
frutlups accounting retains through details.usage.
