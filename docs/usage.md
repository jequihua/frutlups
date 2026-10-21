# Usage and cost reporting

Run `frutlups status --usage` from your project, adding `--json` for structured
output. It reports durable totals, not an invoice, a provider bill or a record
of individual helper agents.

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

## Restart and validation

Status is read-only and rebuilds totals from durable history after a restart.
The accounting calculation does not use local job-result totals, but the status
command's integrity validation can read bound evidence from job storage. Preserve
that evidence; missing or changed bindings can cause refusal before totals print.

## Costs and coverage

Subscription accounts may report API-equivalent estimates. These are not bills,
remaining subscription quota or an enforced dollar cap. Unknown cost is not free
usage. Check the provider's own account controls before starting work.

Pi's session statistics can omit partial delegation spend that frutlups retains.
Claude aggregate token selection is independent of cost completeness and main-result
success. See [adapter accounting](adapters.md#usage).

frutlups records one aggregate per main attempt; it does not promise complete
helper-level accounting. For the installed and live observations and platform
limits, see [release evidence](release_evidence.md).
