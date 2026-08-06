# Mature-Project Migration Guide

How to adopt the `frutlups` artifact-first loop in an **existing, mature
project** that was not built artifact-first from day one. This is "Mode B"
adoption — applying the framework to a repository that already has its own code,
history, and conventions — done incrementally, without disrupting what already
works.

This is a focused migration guide, not a full manual. For a first-run path see
[`QUICKSTART.md`](QUICKSTART.md); for how the templates fit together see
[`ARTIFACT_TEMPLATE_GUIDE.md`](ARTIFACT_TEMPLATE_GUIDE.md); for optional memory
see [`LLLOOM_INTEGRATION_GUIDE.md`](LLLOOM_INTEGRATION_GUIDE.md).

All `frutlups` commands below run from the directory where `frutlups` is
installed (here, `08_pkg/`) using the project Python 3.11 virtual environment,
invoked explicitly as `.\.venv\Scripts\python.exe`. The path argument (`..` in
these examples) points at the project you are migrating. See `QUICKSTART.md` for
venv setup.

## Mode B: adopt incrementally, not all at once

The guiding principle: **introduce the loop proportionally**. You do not need to
restructure the existing project, move its code, or create every template
workspace. You add just enough artifact structure for `frutlups status` /
`frutlups next` to give useful answers, then grow the loop one slice at a time.

Repository artifacts remain the source of truth, and the authority order is
unchanged: roadmaps, prompts, reviews, governance, and source files are primary;
generated prompt text and chat are never higher authority than the artifacts they
cite. Memory (`llloom`) stays optional and disabled unless configured.

## Minimum viable structure to start the loop

`frutlups` recognizes a project root by the presence of **`00_brief/`** and
**`prompts/`**. Beyond that, the loop becomes useful as soon as a roadmap with a
parseable slice exists. The smallest structure that lets `status` infer a real
next action is:

```text
<your-project>/
  00_brief/            # marks the project root (can start nearly empty)
  prompts/
    for_coding_agent/  # coding prompts live here
    for_review_agent/  # review prompts live here
  03_experiments/
    active_roadmap_frutlups.md       # authored milestones, e.g. "### M001: First Milestone"
    development_roadmap_frutlups.md   # detailed slices under a "Slices:" marker
  05_governance/
    reviews/           # self-reports, review reports, verdict records
```

The roadmap formats matter. In `active_roadmap_frutlups.md`, a milestone is a
heading with a status, for example:

```text
### M001: First Milestone

Status: active
```

In `development_roadmap_frutlups.md`, slices are **bullet items under a
`Slices:` marker** within the milestone — not bullets placed directly under the
milestone heading. The `Slices:` line is required; without it the parser does
not recognize the bullets and the milestone is treated as having no slices:

```text
### M001: First Milestone

Slices:

- M001-S01: first slice
```

`frutlups` treats five directories as **required** for full template health:
`00_brief/`, `03_experiments/`, `05_governance/`, `06_infra/`, and `08_pkg/`
(the project root is recognized specifically by `00_brief/` + `prompts/`). The
other template workspaces — `01_data/`, `02_analysis/`, `04_delivery/`,
`07_app/`, `09_ops/` — are genuinely optional and you can omit them. Missing
required directories only change the `Template health` line in the report; they
do not block the loop. `frutlups status` returns exit code 0 once it finds a
project root (`00_brief/` + `prompts/`) — even with required directories still
missing — and exits with a nonzero code only when no project root is found at
the given path.

## Run status/next on a partially-populated project

`frutlups status` is safe to run at any stage — it is read-only and does not
require a complete template. Run it early and let the diagnostics guide what to
backfill next:

```powershell
.\.venv\Scripts\python.exe -m frutlups status ..
.\.venv\Scripts\python.exe -m frutlups status .. --json
.\.venv\Scripts\python.exe -m frutlups next ..
```

How `status` responds as you add structure:

- **Only `00_brief/` + `prompts/`** — `status` runs and exits 0, but reports a
  `Template health: missing required directories` line and the diagnostics
  **`[error] no_active_roadmap`** (no roadmap found under `03_experiments/`) and
  **`[warning] no_detailed_roadmap`**. The loop step is `no_frontier`. This is the
  signal to add a roadmap next.
- **After adding the active roadmap** with a milestone but no parseable slice in
  the detailed roadmap — `status` reports `Next milestone: M001 (...)` plus a
  `[warning] next_milestone_has_no_slices`, and the loop step is still
  `no_frontier`. The most common cause is a missing `Slices:` marker: the parser
  only recognizes slice bullets that appear under a `Slices:` line within the
  milestone, so bullets placed directly under the milestone heading are not seen.
- **Once the milestone has a parseable slice** (a `- M001-S01: ...` bullet under
  a `Slices:` marker) — `status` advances to the `make_coding_prompt` loop step
  for that slice, and `frutlups next ..` infers it as the frontier. This whole
  repository is the worked example: run `frutlups status ..` here to see a
  populated loop in action.

The `Template health: missing required directories` line is a completeness note
about the full template, not a failure. A migrated project that intentionally
uses only a few workspaces is expected to show it, and that is fine.

## Backfill incrementally

Adopt the loop one slice at a time rather than authoring a whole roadmap up front:

1. **Mark the root and add a roadmap.** Create `00_brief/` and `prompts/` (the
   project-root markers); the other required directories (`03_experiments/`,
   `05_governance/`, `06_infra/`, `08_pkg/`) can be near-empty placeholders to
   clear the `Template health` line. Then add a minimal
   `03_experiments/active_roadmap_frutlups.md` with a single milestone and
   `development_roadmap_frutlups.md` with that milestone followed by a `Slices:`
   marker and a single bullet slice (e.g. `- M001-S01: first slice`) describing
   the first piece of work you want to run through the loop.
2. **Check state.** Run `frutlups status ..`; with a parseable slice present,
   expect the `make_coding_prompt` loop step for that slice. If you instead see
   `[warning] next_milestone_has_no_slices`, the `Slices:` marker is missing or
   the bullet is not under it.
3. **Generate the first coding prompt** (preview, then write):

   ```powershell
   .\.venv\Scripts\python.exe -m frutlups make-coding-prompt .. --dry-run
   .\.venv\Scripts\python.exe -m frutlups make-coding-prompt ..
   ```
4. **Run the slice through the loop.** Implement it, write the coder self-report
   under `05_governance/reviews/`, then create the matching review prompt
   (`make-review-prompt ..`), have the reviewer write the review report, and
   record the verdict. `--review-report` is resolved relative to your **current
   working directory**, so from `08_pkg/` the path must reach back to the project
   root (note the `..\` prefix), not `05_governance/...` directly:

   ```powershell
   .\.venv\Scripts\python.exe -m frutlups record-verdict .. `
       --review-report ..\05_governance\reviews\<slice>_review_report.md
   ```
5. **Grow from there.** Add the next slice (and milestone when needed) only when
   you are ready to run it. The roadmap grows with the work, not ahead of it.

This keeps the loop order intact —
coding prompt → coder self-report → review prompt → review report → verdict
record → next slice — while the existing project keeps building normally
alongside it.

## Don't force unused workspaces

Keep adoption proportional to the project:

- Create an *optional* workspace only when you will actually put artifacts in it.
  A web service might never need `01_data/`; a data project might never need
  `07_app/`. The optional set (`01_data/`, `02_analysis/`, `04_delivery/`,
  `07_app/`, `09_ops/`) is a menu, not a mandate.
- The five **required** directories (`00_brief/`, `03_experiments/`,
  `05_governance/`, `06_infra/`, `08_pkg/`) only affect the `Template health`
  line; create them (placeholders are fine) so `status` reports
  `Template health: ok` rather than listing them as missing. They do not change
  the `status` exit code.
- Leave the existing project's own structure (its `src/`, build files, CI, etc.)
  where it is. `frutlups` orchestrates the loop *around* the codebase; it does not
  require relocating it.
- If you do add a workspace, a one-line `CONTEXT.md` stating its purpose is
  enough to keep it oriented (see `ARTIFACT_TEMPLATE_GUIDE.md`).

## Roles and providers

Adoption is provider-neutral. The logical roles (`architect`, `reviewer`,
`coder`, `human`) describe *who does what* in the loop, not which vendor or model
is used; a common preset maps GPT to architect/reviewer and Claude to coder, but
any mapping — including manual/file handoff for any role — works. Nothing in
migration hard-codes a provider.

## Memory / llloom during migration

Memory stays **optional and disabled** unless a project configures it; a migrated
project runs the full loop without `llloom`. If and when you adopt memory, do it
as its own step and keep it read-only during normal slices (mutation belongs to
explicit memory-update slices). See
[`LLLOOM_INTEGRATION_GUIDE.md`](LLLOOM_INTEGRATION_GUIDE.md).

## Where to go next

- First-run path: [`QUICKSTART.md`](QUICKSTART.md).
- How the templates fit together: [`ARTIFACT_TEMPLATE_GUIDE.md`](ARTIFACT_TEMPLATE_GUIDE.md).
- Optional memory: [`LLLOOM_INTEGRATION_GUIDE.md`](LLLOOM_INTEGRATION_GUIDE.md).
- CLI reference: the **CLI Usage** section of [`README.md`](README.md), or
  `frutlups <command> --help`.
- Release preparation: [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md).
- The loop operating model: `05_governance/prompt_loop_operating_model.md` at the
  repository root.
