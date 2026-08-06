# Artifact-Template Integration Guide

How the repository's artifact templates fit together when you adopt or maintain
the `frutlups` artifact-first loop. This is a practical integration guide, not a
full manual — for a first-run path see [`QUICKSTART.md`](QUICKSTART.md); for CLI
details see the **CLI Usage** section of [`README.md`](README.md).

The governing principle: **the unit of progress is an artifact advancing from one
reviewable state to another**, not an agent finishing a task. Repository files
are the source of truth. `frutlups status` and `frutlups next` only *read* that
state back to you — they never invent it.

All commands below run from the `08_pkg/` package workspace using the project
Python 3.11 virtual environment, invoked explicitly as
`.\.venv\Scripts\python.exe`. See `QUICKSTART.md` for venv setup.

## The template at a glance

The repository is organized into numbered workspaces, each a context boundary
with its own one-line `CONTEXT.md` purpose file:

```text
00_brief/       objective, scope, constraints, success criteria
01_data/        data sources, schema, quality, provenance, splits
02_analysis/    exploration turned into durable summaries
03_experiments/ plans, runs, comparisons — and the frutlups roadmaps
04_delivery/    stakeholder- or system-ready deliverables
05_governance/  decisions, costs, assumptions, risks, reviews, verdicts
06_infra/       how the system runs (local / HPC / cloud); architecture notes
07_app/         application interfaces (review apps, dashboards, APIs)
08_pkg/         the active Python package workspace (frutlups itself)
09_ops/         recurring or production-like operations
prompts/        coding/review prompts plus human handoff prompts
docs/           project notes and llloom observations
```

Not every workspace is used by every project. For this package, the load-bearing
workspaces are `03_experiments/` (roadmaps), `05_governance/` (reviews and
decisions), `06_infra/` (architecture), `08_pkg/` (the package), and `prompts/`
(the loop handoffs). The others hold placeholder `CONTEXT.md` files and are
available if a project needs them.

### Workspace `CONTEXT.md` files

Each workspace carries a `CONTEXT.md` that states its purpose in one or two
lines. Treat these as lightweight orientation and routing artifacts, not as the
live source of project state: they tell a new agent or maintainer *what a
workspace is for*, while the actual current state lives in the roadmap,
governance reviews, and prompts. When you adopt the template, keep each
`CONTEXT.md` short and accurate; when you extend a workspace, update its
`CONTEXT.md` so the orientation stays true.

## The durable loop state

The loop's state is not in chat history or a database — it is a chain of files
under `03_experiments/`, `prompts/`, and `05_governance/`. One slice produces
this chain:

```text
roadmap slice (03_experiments/active_roadmap_frutlups.md,
               03_experiments/development_roadmap_frutlups.md)
  -> coding prompt        prompts/for_coding_agent/NNN_..._<slug>.md
  -> coder implementation + self-report
                          05_governance/reviews/<slice>_<slug>_self_report.md
  -> matching review prompt
                          prompts/for_review_agent/NNN_review_..._<slug>.md
  -> review report + verdict
                          05_governance/reviews/<slice>_<slug>_review_report.md
  -> verdict record       05_governance/reviews/<slice>_<slug>_verdict_record.md
  -> next slice
```

Each artifact is a reviewable state. Because the whole chain is on disk,
`frutlups status` can reconstruct exactly where a slice is and what comes next —
its loop step walks `execute_coding_prompt` → `make_review_prompt` →
`execute_review_prompt` → `record_verdict` → next.

### Roadmap artifacts (`03_experiments/`)

- `active_roadmap_frutlups.md` — the authored milestone/slice plan.
- `development_roadmap_frutlups.md` — the detailed slice breakdown.

The roadmap defines the *frontier*: the next slice to work on. `frutlups next`
infers it from the roadmap plus recorded verdicts. **Do not advance roadmap
markdown by hand** to claim progress — let recorded verdicts move the frontier.

### Prompt artifacts (`prompts/`)

- `prompts/for_coding_agent/NNN_..._<slug>.md` — coding prompts. **The
  architect/reviewer creates these.** Each defines one narrow slice: role, scope,
  non-goals, verification commands, and the required self-report and
  review-prompt paths.
- `prompts/for_review_agent/NNN_review_..._<slug>.md` — review prompts. **The
  coder creates these — and only after the self-report exists.** They share the
  coding prompt's sequence number.
- Root-level `handoff_to_next_*_YYYY-MM-DD.md` — onboarding handoffs that
  activate a future coder or architect/reviewer session. They are not coding or
  review prompts.

Numbering is zero-padded and sequential. See `prompts/README.md` for the full
conventions.

### Governance artifacts (`05_governance/`)

Governance is the visible, durable record of *why*:

- `05_governance/reviews/` — the self-report / review-report / verdict-record
  triplet per slice, named by the slice and slug.
- `decision_log.md`, `assumptions_log.md`, `risks.md`, `review_log.md`,
  `cost_log.md` — running governance records.
- `known_divergences.md` — intentional deviations to surface rather than hide.
- `prompt_loop_operating_model.md`, `llloom_operating_model.md` — the operating
  models the loop follows.

A milestone or slice advances only after a verdict is recorded:
`pass`, `needs_work`, `blocked`, or `override` (override must carry rationale).
Human stop/go decisions stay visible here, not in chat.

## Roles are logical, not vendor-specific

The loop has four logical roles — `architect`, `reviewer`, `coder`, `human` —
defined in `05_governance/prompt_loop_operating_model.md`. They are *roles*, not
products: a common preset maps GPT to architect/reviewer and Claude to coder, but
a project may swap them, use one agent family for all roles, or run any role as a
manual/file handoff. Provider and model selection belong in configuration or
adapters, never hard-coded into the loop. Keep documentation and prompts
provider-neutral.

## Walking the loop (PowerShell, from `08_pkg/`)

```powershell
# 1. orient: where does the loop stand, and what is the next slice?
.\.venv\Scripts\python.exe -m frutlups status ..
.\.venv\Scripts\python.exe -m frutlups next ..

# 2. architect/reviewer: create the coding prompt for the frontier (preview first)
.\.venv\Scripts\python.exe -m frutlups make-coding-prompt .. --dry-run
.\.venv\Scripts\python.exe -m frutlups make-coding-prompt ..

# 3. coder: implement the slice, then write the self-report under
#    05_governance/reviews/<slice>_<slug>_self_report.md
#    (section headings must match the self-report schema exactly)

# 4. coder: only after the self-report exists, create the matching review prompt
.\.venv\Scripts\python.exe -m frutlups make-review-prompt .. --dry-run
.\.venv\Scripts\python.exe -m frutlups make-review-prompt ..

# 5. reviewer: execute the review prompt, write the review report ending in a
#    verdict (pass / needs_work / blocked / override)

# 6. record the verdict; this advances the frontier
#    (--review-report is resolved from the current directory; from 08_pkg use ..\)
.\.venv\Scripts\python.exe -m frutlups record-verdict .. `
    --review-report ..\05_governance\reviews\<slice>_review_report.md --dry-run
.\.venv\Scripts\python.exe -m frutlups record-verdict .. `
    --review-report ..\05_governance\reviews\<slice>_review_report.md
```

`status`/`next` never write. `make-coding-prompt`, `make-review-prompt`, and
`record-verdict` each write a single repository artifact and accept `--dry-run`
to preview without writing. All five accept `--json`.

## Verifying integration without hand-editing state

You can confirm the template is wired together correctly using only read-only
commands — never by editing the roadmap or inventing a verdict:

```powershell
.\.venv\Scripts\python.exe -m frutlups status ..        # loop step + next command
.\.venv\Scripts\python.exe -m frutlups status .. --json  # machine-readable state
.\.venv\Scripts\python.exe -m frutlups next ..           # inferred frontier
```

- If `status` reports `make_coding_prompt`, the frontier is ready for a prompt.
- If it reports `fix_self_report`, a self-report exists but a required section
  heading is missing or misnamed — fix the headings, do not edit loop state.
- A prompt-health warning for an `unmatched_coding_prompt` is the normal,
  expected state after a coding prompt is created but before its review prompt
  exists; it clears when the review prompt is written.

Let recorded verdicts — not manual roadmap edits — move the frontier. If chat
history and the artifacts disagree, trust the artifacts and re-run
`frutlups status ..`.

## Memory / llloom

Memory via `llloom` is **optional and read-only during normal coding and review
slices**, and disabled unless explicitly configured (`status` shows
`Memory: disabled` by default). The loop runs fully without it; do not require
`llloom`, provider credentials, CI, or any external service to integrate the
templates. Memory mutation, when used at all, belongs only to an explicit
memory-update slice.

## Where to go next

- First-run path: [`QUICKSTART.md`](QUICKSTART.md).
- CLI reference: the **CLI Usage** section of [`README.md`](README.md), or
  `frutlups <command> --help`.
- Release preparation: [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md).
- Loop operating model and architecture:
  `05_governance/prompt_loop_operating_model.md` and `06_infra/architecture.md`
  at the repository root.
- The `llloom` integration guide (M015-S03) and mature-project migration guide
  (M015-S04) are planned as separate documents.
