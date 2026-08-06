# Coding Prompt Template

Workflow metadata (fenced Markdown content, **not** top-of-file OKF/profile
frontmatter):

```yaml
milestone: TBD
slice: TBD
role: coder
mode: normal implementation
strictness: Level 3
status: draft
```

## Current State

Read `PROJECT_STATE.md`.

Do not restate volatile live fields here unless the task requires a dated
snapshot. Link to `PROJECT_STATE.md` or `prompts/INDEX.md` for the active
workspace set, next action, and current prompt/review frontier.

## Active Workspaces

- TBD

## Read First

- TBD

## Task

TBD

## Implementation Discipline

Follow YAGNI as defined in `CLAUDE.md` Minimal Implementation Discipline, the
canonical doctrine. Prefer reuse, stdlib/native features, and the smallest
correct useful change, not mechanically the smallest diff. Use a one-liner or
small local change when it fully solves the task, and avoid speculative
abstractions, new dependencies, or scaffolding for later. Extraction supported by
repeated concrete duplication or a shared invariant that must change together is
not speculative, but is justified only when the smallest shared helper reduces
total complexity and preserves local clarity. Do not trade away correctness,
security, or needed tests for brevity.

## OKF Authoring

Default: legacy/no-frontmatter. Only opt an artifact into the OKF profile by listing
every **exact new artifact path** and its assigned registry `type` here; the minimum
block is `type` plus `framework_profile: "0.1-rc.1"`. Do not convert historical
artifacts and do not opt in a directory, neighbouring file, or file class implicitly.
See `docs/template_framework/okf_authoring_and_migration.md`.

## Non-Goals

- TBD

## Verification

- TBD
- When cases share setup and assertion shape, prefer table-driven tests or
  `subTest`; keep tests separate when behavior, setup, or the failure story
  differs, and assert exact contract values individually.
- When changed artifacts cite repository paths or `test_*` identifiers, run:
  `python scripts/artifact_integrity_preflight.py <artifact> [<artifact> ...]`.
  Resolve hard errors before handoff; report advisory warnings with context.

## Self-Report

Write a self-report at:

`TBD`

Use the canonical schema in `prompts/templates/self_report.md`.

In `Known Limits / Follow-Up`, mention any substantial local-only artifacts this
slice produced (caches, virtual environments, generated outputs, archives, copied
repositories, memory roots, or run folders) and whether they were cleaned,
ignored, retained, or need reviewer/human attention.

Do not create a commit unless this prompt explicitly instructs it (see
`docs/template_framework/method.md` Commit Discipline).

## Definition Of Done

- TBD
