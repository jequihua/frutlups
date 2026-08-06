# Review Prompt Template

Workflow metadata (fenced Markdown content, **not** top-of-file OKF/profile
frontmatter):

```yaml
milestone: TBD
slice: TBD
role: reviewer
mode: normal implementation
strictness: Level 3
status: draft
```

## Review Objective

TBD

## Read First

- `PROJECT_STATE.md`
- TBD

## Review Checks

- correctness;
- scope discipline;
- verification evidence;
- documentation honesty;
- governance updates;
- non-goals respected;
- minimality/scope: no unrequested abstractions, broad rewrites, or speculative
  scaffolding, and no new dependency where reuse/stdlib/native code suffices
  (deletion or reuse considered where appropriate); check both speculative
  overbuilding and silent complexity accretion in code touched by repeated
  smallest-diff corrections, distinguishing a bounded in-scope simplification
  needed to make the current change safe, an out-of-scope named evidence-backed
  simplification candidate (recording a candidate does not authorize it), and an
  unauthorized refactor or roadmap expansion;
- local state hygiene: if the slice ran tests, builds, data jobs, memory/sync
  tooling, or legacy migration, confirm generated local state is ignored,
  cleaned, or documented.
- artifact integrity: when the bundle cites repository paths or `test_*`
  identifiers, run `python scripts/artifact_integrity_preflight.py` against the
  exact artifacts before semantic review; treat errors as findings and assess
  warnings in context;
- live-state discipline: durable artifacts link to `PROJECT_STATE.md` or indexes
  instead of copying volatile prompt numbers, row counts, workspace lists,
  worktree contents, or next actions as continuing truth.
- OKF authoring (only when the coding prompt opted artifacts into the profile):
  check the exact path/type assignment against the registry; the required two-field
  **minimum** (`type` and `framework_profile`) is present; any additional
  profile-permitted fields are justified by a documented need and conform to the
  accepted profile (`framework_id` stays recommended-only for a movable or
  cross-referenced concept, never mandatory) — do not reject a profile-valid enriched
  artifact for carrying justified optional fields; read-only profile-check evidence; an
  unchanged Markdown body; preserved legacy compatibility; and the absence of authority
  inflation or any unrequested/implicit conversion. See
  `docs/template_framework/okf_authoring_and_migration.md`.

## Verification

- TBD

## Output

Write findings first, then closure decision and recommended next move. When an
accepted verdict and passing validation justify it, you may mark the slice or
milestone commit-ready (see `docs/template_framework/method.md` Commit
Discipline); marking commit-ready does not create a commit. Mark a milestone
commit-ready only after the Milestone Commit Closure checklist is satisfied or
explicitly deferred. On a positive milestone verdict, when local git actions are
allowed, the architect/reviewer performs that checklist and creates the milestone
commit by default; otherwise leave it commit-ready for a human or authorized
workflow. At a completed roadmap or work-package boundary you may instead note
pull-request-ready; opening a PR remains a human decision.

## Non-Goals

- TBD

## Definition Of Done

- TBD
