# Offline incident baseline — M001-S01

This is an offline characterization of package 0.3.2, imported from
P:published-frutlups `152bc66e00b02546c4269e87eb9f7981ef817b39` under the
115-file inventory in `02_analysis/source_import.json`. It is not a repair or
release qualification. The only additions are `tests/test_incidents.py` and
this document. Production and frozen fixture bytes are unchanged.

The observed toolchain is Windows, selected repository `.venv` Python 3.14.3,
and Git 2.32.0.windows.1. Below, `<python>` means that selected interpreter,
`<workspace>` the development root, and `<scratch>` a test-owned directory
outside it. All paths are portable identities, not resolved host paths.

## Witnesses and measurements

The complete focused measurement run passed 7 tests, with zero skips, in
16.78 seconds. Per-test wall times include setup/teardown after the measurement
fixture starts; session-scoped baseline setup is outside those rows. Allocation
peaks use stdlib `tracemalloc` in the pytest parent. They are not RSS, child
process peaks, or aggregate process-tree memory. The Pi parser's allocation
peak exceeds its retained stream size because parsing creates Python objects.

| Witness | Observed result | Wall seconds | Parent Python allocation peak, bytes |
| --- | --- | ---: | ---: |
| Expanded Git staging | 700 paths, 42,776 command characters; Windows error 206; HEAD/index unchanged and files preserved | 0.959 | 774366 |
| Late Pi terminal event | 8,388,768 bytes emitted; 8,388,608 retained; terminal semantics lost | 0.243 | 19300245 |
| Identical no-change verification failures | Two coder completions with empty changes, two exit-1 receipts, one corrective round, then `rounds_exhausted`; no acceptance | 1.299 | 729886 |
| CRLF raw evidence | 33 raw bytes preserved in Git blob and archive under `*.raw -text`; normalized evidence hash deliberately differs | 0.229 | 297466 |
| Fresh-process legacy resume | Five distinct Python invocations reach coding, verifying, reviewing, accept_pending, accepted; one coder and one reviewer invocation total | 3.891 | 312679 |
| Candidate materialization/install | 117 tracked files and archive members; all 54 frozen manifest entries validate; candidate import and CLI parser in isolated processes | 3.770 | 1371630 |
| Current manual `/2` | 17 Git-listed runtime/template files copied; five lifecycle events recorded once; stable successful `/2` receipt and ledger-only acceptance | 5.540 | 325827 |

The first two rows assert the current faults, without skips or xfails. Later
repair slices must turn those assertions into success regressions. The no-change
row characterizes repeated identical work until the configured round budget,
rather than claiming a no-progress detector. CRLF and resume rows are successful
witnesses. The late-terminal stream consists of 8,192 valid 1,024-byte JSONL
records followed by a 160-byte assistant/agent-end suffix. Parsing the complete
owned stream succeeds, isolating retention as the reason the bounded output
fails. This scenario emits less than 16 MiB and no stderr.

The candidate is seeded from the admitted import inventory plus the two new
files, committed only inside `<scratch>/candidate`, and archived from that
candidate's HEAD. The development HEAD is never archived or committed. Exact
tracked and archived file inventories are asserted; the restored
`tests/fixtures/v4_project/local_state/README.md` is explicitly tracked there.
Development Git also lists that README with `--cached --others --exclude-standard`.
The archive is installed without dependency resolution, build isolation, cache,
index access, or global installation. Two `-I` fresh processes assert the import
comes from the target installation. Manifest hashes use the frozen CRLF policy.
CLI checks cover the current top-level usage response (exit 2 for `--help`) and
the installed console entry point's `status --help` parser (exit 0).

The manual fixture copies Git-listed `scripts/` and `prompts/templates/` from
the current workspace onto an owned disposable legacy scaffold, changes only
that copy to roadmap `/2`, and commits its baseline there. Each manual command
starts a fresh Python process; no production runner claims `/2` support. The
review is explicitly synthetic. Its recorded acceptance is ledger-only in the
fixture and grants no acceptance to this development slice.

## Exact command recipes

From `<workspace>`, the measured focused invocation was:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
.\.venv\Scripts\python.exe -B -m pytest -q 08_pkg/frutlups/tests/test_incidents.py -p no:cacheprovider -rP
```

`-rP` exposes captured measurements from passing tests. Diagnostic runs used
the same command, and the bounded selections `-k 'fresh_process'` and
`-k 'candidate or current_manual'`. The initial sandboxed command used `python`
instead of the explicit `.venv` path and failed with external-temp access denied
(4 failures, 7 setup/teardown errors). With authorized external-scratch access,
successive diagnostic results were 4 failed/3 passed, 2 failed/5 passed,
1 failed/1 passed/5 deselected, and 1 failed/1 passed/5 deselected. These exposed
test expectation/setup issues: CRLF output, lifecycle state names, review grammar,
and CLI configuration/help behavior. Complete focused runs then passed 7/7 in
16.56 seconds and, after the pip/entrypoint refinements, 16.78 seconds.
No production repairs were made.

Test subprocesses use argv arrays, `shell=False` behavior and a 90-second
timeout; the Pi stream process has a 30-second limit. The complete executable
driver and inline fresh-process assertions are retained in `test_incidents.py`.
The Git setup recipe for owned repositories is exactly:

```text
git -C <owned-repo> init -q
git -C <owned-repo> config user.name "Incident fixture"
git -C <owned-repo> config user.email fixture@example.invalid
git -C <owned-repo> config core.autocrlf false
git -C <owned-repo> add .
git -C <owned-repo> commit -qm "Owned offline baseline"
```

The 700-path test calls the existing `gitws.commit` and records its real
`git -C <owned-repo> add -A -- <700 paths>` invocation before Windows refuses
it. Each generated path is `p` + a three-digit index + `_` + 51 `x` characters
+ `.txt`, for indices 0 through 699. Resolved scratch length affects the total
command length; the test requires at least 40,000 characters, not one host path.

The materialization recipe, after the explicit inventory copy, is:

```text
git -C <scratch>/candidate ls-files -z
git -C <scratch>/candidate status --porcelain
git -C <scratch>/candidate archive --format=zip --output=<scratch>/candidate.zip HEAD
<python> -B -m pip install --no-index --no-deps --no-cache-dir --disable-pip-version-check --no-build-isolation --target <scratch>/installed <scratch>/exported
<python> -I -B -c <manifest-and-CLI-check-in-test_incidents.py> <scratch>/installed <scratch>/exported
<python> -I -B -c <installed-entrypoint-check-in-test_incidents.py> <scratch>/installed
```

ZIP extraction uses stdlib `zipfile`; the candidate archive is wholly test-owned.
The raw-CRLF witness separately executes `git show HEAD:evidence.raw` and
`git archive --format=zip --output=raw.zip HEAD` in its own repository.
Fresh legacy resumption executes the following once per transition, with
the existing offline fake Pi/Claude adapters and installed development import:

```text
<python> -B <workspace>/08_pkg/frutlups/tests/test_incidents.py <owned-legacy-repo>
```

The manual fixture runs these commands from `<scratch>/manual` in sequence:

```text
git -C <workspace> ls-files -z scripts prompts/templates
git -C <scratch>/manual add .
git -C <scratch>/manual commit -qm "Owned current manual runtime"
<python> -B scripts/roadmap.py check
<python> -B scripts/prompt.py M001-S01
<python> -B scripts/ledger.py coded M001-S01
<python> -B scripts/verify.py M001-S01
<python> -B scripts/prompt.py M001-S01 --review
<python> -B scripts/ledger.py record 05_governance/reviews/001_M001-S01_r1_review.md
<python> -B scripts/ledger.py accept M001-S01
<python> -B scripts/ledger.py check
<python> -B scripts/ledger.py status
```

The test writes a synthetic CRLF product between prompt/coded and a synthetic
review before record. Verification uses the inherited owned fixture's
`[<python>, "-c", "print('verified')"]`, not the development product verifier.
The actual development full command remains `python scripts/hermetic_verification.py`.

## Limits and remaining qualification

The single full-verifier run passed all 442 tests (16 modules including this
one), with zero skips, in 153.03 pytest seconds and 163.841 observed wall seconds.
Installed development imports and required reads passed the project integrity
gate; all 15 original modules were present. Two existing pytest deprecation
warnings name the combinations/permutations parametrizations in
`tests/test_loop_evidence.py`; that file was outside the write boundary.
This is command execution evidence, not an authoritative slice receipt or review.

The full command was observed with this 600-second wrapper from `<workspace>`:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
@'
import subprocess, time
started=time.perf_counter()
result=subprocess.run(['python', 'scripts/hermetic_verification.py'], timeout=600)
print(f'Full verifier observed wall seconds: {time.perf_counter()-started:.3f}', flush=True)
raise SystemExit(result.returncode)
'@ | python -B -
```

Only this measurement document was finalized after the full run. The bounded
materialization recheck for that final report uses the following command; it
does not repeat the full verifier or any provider work:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
.\.venv\Scripts\python.exe -B -m pytest -q 08_pkg/frutlups/tests/test_incidents.py -p no:cacheprovider -k candidate_git_archive_install
```

No 120-second performance promise is inferred from these measurements. The
full-verification limit is 600 seconds. Synthetic streams are capped at 16 MiB
each; only the Pi case approaches that bound. Tests add no dependencies and
make zero live provider calls, probes, canaries or scientific experiments.

This is one coder handoff within the admitted one-handoff/two-corrective-round,
12-seat-invocation and 120-active-minute limits. Focused test reruns are local
diagnostics, not additional development seats. Synthetic adapter invocations
inside tests are not paid model invocations. Development token/cost and
aggregate child-process peak memory remain unknown.

Other Python versions, POSIX, installed configured preflight/run/status/resume,
paired runner `/2` conformance, real provider transport and the final live user
path remain outside this baseline. The installed CLI checks prove import,
packaging metadata and command parsing, not that later integration gate.
