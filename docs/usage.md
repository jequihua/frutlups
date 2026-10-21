# Usage accounting and selected runtime

This note describes what `frutlups status --usage` reports and which runtime
behavior has been observed. It is not an invoice, a provider bill or a record
of helper agents.

## Fields

Each slice and each milestone gets one `usage` row, in text and JSON:

| Field | Meaning |
| --- | --- |
| `secs` | Sum of known measured attempt seconds |
| `tokens_in` | Sum of known input tokens, including cache reads and writes |
| `tokens_out` | Sum of known output tokens |
| `cost_usd` | Sum of known cost reported by the seat CLI |
| `completeness` | `complete`, `partial` or `unknown` |
| `unresolved_attempts` | Reserved attempts with no durable finish |

A missing value is `null` in JSON and `?` in text. It is never shown as 0.
A known 0 stays 0.

Example text row:

```text
M001-S01 usage secs=2 tokens_in=10 tokens_out=5 cost_usd=0.25 completeness=partial unresolved_attempts=1
```

## Coverage labels

- `unknown`: no known value at all, including rows that have only unfinished
  attempts.
- `complete`: every contributing observation is fully covered, all four values
  are known, no total was unrepresentable, and no attempt is unresolved.
- `partial`: anything in between. Known subtotals stay visible, but they never
  claim to be the whole amount.

A provider attempt counts as covered only when its seat recorded
`completeness: complete` and all four values. Verification attempts are not
provider calls: they contribute measured seconds only. Seconds alone therefore
give `partial`, never a known provider spend. Lifecycle-only `coded` or
`reviewed` totals, from histories without attempt events, carry no coverage
record, so they are always `partial`.

## Sources and precedence

Totals come only from the validated native ledger:

- Each `attempt_finished` counts once against the scope of its
  `attempt_started`. This includes failed and resolved attempts, transport and
  format retries, routed reviewers, holistic attempts and probes. Known spend
  on a failed attempt is kept; nothing is refunded.
- A lifecycle `coded`/`reviewed` copy is skipped when an attempt owns the same
  scope, round and role.
- An attempt with no finish adds 1 to `unresolved_attempts` and no values.
- A milestone row adds its own holistic and probe attempts to all of its slices
  once.
- A float total that overflows becomes `null` with `partial`. Integer token
  totals stay exact. Output never contains NaN or Infinity.

Local job files under `local_state/frutlups/jobs` are never read for totals.
Malformed history is refused before any usage row is printed.

## Restart

Status is read-only. A fresh process after a restart rebuilds the same rows
from durable history, even when local job results are missing or misleading.
`tests/test_usage_v2.py` checks this with real fresh-process `status` runs. It
also checks that the ledger, the workspace witness and the job directory stay
unchanged, that the job store is never opened or listed, and that a malformed
ledger line is refused.

## Selected runtime evidence

`tests/test_runtime_v2.py` runs a private native slice with a
`project.local.toml` selector. The selected runtime identity is bound to the
coder, reviewer and verification attempts and matches the verification
receipt. A literal `python` at the start of the verification command runs the
selected launcher, while the receipt keeps the declared argv. A non-Python
command (`git rev-parse`) runs unchanged.

Two launchers of one interpreter were observed: the project venv and its base
executable, both CPython 3.14.3 on Windows. They are not two Python versions.
The base launcher was observed at the verifier's argv-substitution boundary
only. On the qualification host it lacks the template checker's PyYAML, so a
full private run with it stops at the project checker. That path is excluded,
not inferred.

## Not qualified here

- Installed wheel, live providers and real CLI usage records. Public `run` and
  `preflight` now execute offline with fake seats
  ([public_execution.md](public_execution.md)); that is not installed or live proof.
- Other Python versions, the declared minimum, POSIX, symlinked launchers and
  sleep/resume.
- Pi's own session statistics can omit partial delegation spend that frutlups
  keeps through `details.usage`
  ([adapter_compatibility.md](adapter_compatibility.md)).
- Helper-level accounting: the seat's main agent owns its helpers; frutlups
  records one aggregate per attempt.
