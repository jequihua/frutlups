# Models, delegation and limits

frutlups supports Pi and Claude Code. You choose a model and effort for each seat
in the project's committed `frutlups.toml`; the CLI and your account determine
which choices are available. frutlups does not maintain a model catalogue.

## Choose seats

The following are seat fragments, not a complete configuration. The model IDs
were used in the recorded live canary; they are not a promise of present account
access or a requirement to use those models:

```toml
[seats.coder]
adapter = "pi"
provider = "openai-codex"
model = "gpt-6.1-sol"
effort = "medium"
corrective_effort = "high"

[seats.reviewer]
adapter = "claude"
model = "claude-sonnet-5-5"
effort = "high"
```

Define every seat referenced by your project's review routes, including holistic
review. Pi seats require a `provider`. Claude seats use the Claude Code CLI.
`corrective_effort`, when supplied, selects the effort for corrective work;
otherwise the seat retains its normal effort. Model and effort choices must be
accepted by the selected CLI and account. Preflight probes check access when work
is admitted; they are real model calls.

## Helpers and long-running work

The main agent is responsible for coordinating helpers and awaiting required work.
frutlups records the main invocation's outcome and aggregate usage. It does not
maintain a helper obligation ledger or require proof of every child action.

Pi loads the packaged `pi_delegate` extension explicitly. It accepts concurrent
tasks, optional model and thinking choices, and nested delegation, with bounded
returned summaries. Ordinary role tools and the working directory are forwarded.
Claude Code exposes its `Agent` tool to both coder and reviewer roles. There is no
frutlups helper-count, concurrency or nesting cap. Provider and CLI constraints,
process resources and the finite outer allowance still apply.

The main agent conveys task context and scope to its helpers. This is a behavioral
contract, not adversarial filesystem confinement. Reviewer helpers and arbitrary
scale have not been independently qualified by the release canary.

Claude's print-mode background wait ceiling is disabled, and its default/maximum
shell timeouts and async-agent stall timeout are set from the reserved allowance.
The outer process deadline and cleanup remain in force. Set appropriate
`coder_seconds`, `reviewer_seconds` and run allowances for the task; a large setting
does not establish two-hour reliability. Verification retains its own deadline.

## Pi record limit

A project can raise the maximum size of an individual Pi JSON output record:

```toml
[limits]
pi_record_mib = 32
```

The default is **2 MiB**. `32` is an example, not a measured recommendation. This is
a frutlups parser limit, not a native Pi limit or a cap on the number of helpers.
Repeated aggregate records can exceed it on large jobs. Above-limit or malformed
records refuse successful completion; known usage is retained as partial.

Use a positive integer. The value is bound to the invocation and forwarded to
nested Pi launches. Changing it cannot silently alter an existing reservation or
make a changed job eligible for an identical-contract retry.

Larger values permit larger allocations. They do not increase the separate 512 KiB
combined delegation-result budget, 1 MiB final-text limit or 8 MiB retained-output
limit, and they are not a whole-process memory budget.

## Usage

Pi adds assistant and delegation usage without counting repeated aggregates twice.
When delegation usage is incomplete, frutlups retains known amounts as partial;
Pi's own session statistics can omit those partial amounts.

Claude token totals use valid `modelUsage` aggregates when available, independently
of whether the main result failed or cost is missing. Only unavailable aggregate
totals fall back to known top-level usage. Cost comes from `total_cost_usd`.
Failure or incomplete metrics prevent complete coverage; no known metrics means
unknown coverage. See [usage reporting](usage.md).

## Evidence and boundaries

[Release evidence](release_evidence.md) states what was actually installed and run.
The [adapter implementation record](adapter_compatibility.md) preserves earlier
offline work, including its then-current public-execution refusal and pending
qualification statements. Those statements describe that stage, not current
release status. Hostile-agent resistance, ordinary helper write-denial evidence,
arbitrary scale and long-duration reliability remain outside release claims.
