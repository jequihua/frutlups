---
type: coding_prompt
framework_profile: "0.1-rc.1"
---

# Coding Prompt 013: Integrate Opt-In OKF Authoring And Migration

Workflow metadata:

```yaml
milestone: M005
slice: S01
role: coder
mode: normal implementation
strictness: Level 3
status: ready
```

## Current State

Read `PROJECT_STATE.md`. M001-M004 are accepted and committed. The candidate
profile, PyYAML-backed read-only checker, fixtures, and disposable navigation
view are established. M005 is active. This slice makes the profile usable for
new authoring and gradual adoption without changing the profile or converting
legacy documents. Implement the bounded authoring/migration surface below,
write its self-report, and do not commit.

This prompt itself is an opted-in `coding_prompt` example. Its top-of-file YAML
block is OKF/profile frontmatter. The later fenced `Workflow metadata` block is
ordinary Markdown content used by the project workflow; it is not document
frontmatter. Preserve that distinction in the implemented guidance.

## Active Workspaces

- `docs/template_framework`
- `08_pkg`
- `prompts`
- `tests`

## Read First

- `08_pkg/okf_profile_v0_1.md`, especially Sections 1, 4-6, and 8-11;
- `03_experiments/template_v3_implementation_roadmap.md`, the global rails and
  M005 only;
- `docs/template_framework/migration_and_adoption.md` and
  `docs/template_framework/prompt_style_guide.md`;
- `prompts/templates/coding_prompt.md`, `prompts/templates/review_prompt.md`, and
  `prompts/templates/self_report.md`;
- `08_pkg/public_api_contract.md` and `08_pkg/testing_strategy.md`.

Do not reopen the accepted profile, checker, fixture, or navigation design. If
the requested authoring behavior conflicts with an accepted contract, stop and
report the exact conflict instead of silently changing that contract.

## Task

Create one canonical, concise opt-in authoring and migration guide; route the
default coding, review, and self-report surfaces to it; and add focused tests
proving that new profiled artifacts and legacy no-frontmatter artifacts coexist.

The fixed new paths are:

- canonical guide:
  `docs/template_framework/okf_authoring_and_migration.md`;
- focused tests: `tests/test_okf_authoring_migration.py`;
- self-report:
  `prompts/for_coding_agent/013_integrate_opt_in_okf_authoring_and_migration_self_report.md`.

This is an authoring contract and compatibility slice, not a YAML writer or
repository migration utility.

## Required Work

### A. Canonical Opt-In Contract

Create `docs/template_framework/okf_authoring_and_migration.md` as the single
practical source for applying the accepted profile. Give the guide real
top-of-file profile frontmatter with exactly the two required fields:

```yaml
---
type: framework_doc
framework_profile: "0.1-rc.1"
---
```

The guide must define these operating rules clearly:

- legacy/no-frontmatter remains the default unless an active coding prompt or a
  human-approved adoption decision opts in exact **new artifact paths**;
- opt-in is per artifact, not a repository-wide mode and not inferred from a
  directory, neighboring file, active tool, or installed dependency;
- the minimum block contains only `type` and the pinned
  `framework_profile: "0.1-rc.1"`; `framework_id`, `title`, `description`, tags,
  timestamps, tool namespaces, and other fields are never made mandatory by
  this guide;
- `framework_id` is recommended only when the accepted profile says it is
  useful for a movable or cross-referenced concept;
- the block must start on the first line, precede the Markdown title, and follow
  the producer envelope; a fenced YAML example later in a document is not
  frontmatter;
- profile validity conveys no truth, approval, freshness, safety, current-state,
  or execution authority; `PROJECT_STATE.md` remains the only live-state source;
- authors validate exact opted-in paths with
  `python scripts/artifact_integrity_preflight.py --profile <path> [...]`;
- checking is read-only. No unknown-field preservation claim is made because
  this slice adds no read-then-rewrite path.

Provide a compact artifact-class/type mapping that covers every template-owned
type in the accepted registry: `brief`, `constraint`, `decision`, `analysis`,
`coding_prompt`, `review_prompt`, `self_report`, `review_report`,
`verdict_record`, `delivery_plan`, and `framework_doc`. Include copy-ready
minimum examples for the implementation-loop types and enough examples or an
unambiguous substitution rule for every other listed type. Keep examples
minimal; do not duplicate the profile's full field, YAML, namespace, or reason
code specification.

Explicitly describe how frontmatter coexists with each canonical body shape:

- prompt documents keep workflow routing metadata as fenced Markdown content;
- self-reports keep the canonical headings unchanged after the frontmatter;
- review reports and verdict records do not gain authority from metadata;
- ordinary framework/project documents retain their existing Markdown bodies.

### B. Additive Migration, Rollback, And Version Changes

Extend the canonical guide with a small, reviewable adoption sequence:

1. inventory or select candidate **new** artifacts without modifying them;
2. record human approval when an existing project is adopting the profile;
3. opt in exact output paths in a coding prompt;
4. add only the two minimum fields unless a documented use needs more;
5. run the read-only profile check and ordinary project validation;
6. inspect the diff and accept or roll back the change.

Define rollback as removing the entire newly added frontmatter block from the
explicitly opted-in artifact in a reviewed diff while leaving its Markdown body
unchanged, then re-running legacy and project validation. Warn that rollback
must first check whether a downstream consumer has begun relying on that
metadata. For historical artifacts, the default remains no edit at all; never
present bulk removal or history rewriting as rollback.

State that a profile-version change is change-controlled: do not silently
replace `0.1-rc.1`, do not declare stable `0.1`, and do not mass-update artifacts
until the new contract, compatibility behavior, migration/rollback plan, and
fixtures have been accepted.

Keep the existing `docs/template_framework/migration_and_adoption.md` as the
high-level adoption entry point. Replace its short Front Matter section with a
compact summary and link to the new canonical guide; do not duplicate the new
guide there. Add a discoverability link from `README.md` without adding another
copy of the rules.

Do not add an inventory script in this slice. The existing `rg --files` search
and explicit-path checker are sufficient for bounded/manual adoption. Record a
future inventory utility only as a possible evidence-driven follow-up if real
usage shows that manual selection is unsafe or too costly.

### C. Prompt, Review, And Self-Report Guidance

Update the three canonical files under `prompts/templates/` to link to the new
guide rather than restating its full rules.

In `coding_prompt.md`, add a short `OKF Authoring` section whose default is
legacy/no-frontmatter and which requires the prompt author to list every exact
new path and assigned registry type when opting in. It must forbid implicit
directory-wide or historical conversion.

In `review_prompt.md`, require the reviewer, only when the coding prompt opted in
artifacts, to check the exact path/type assignment, minimality, profile-check
evidence, unchanged body contract, legacy compatibility, and absence of
authority inflation or unrequested conversion.

In `self_report.md`, keep every canonical heading exactly unchanged. Add concise
instructions telling a coder with opted-in outputs to record the exact paths,
types, and profile-check result under the existing headings. Do not require the
self-report itself to carry frontmatter unless its own exact path was opted in.
Do not change the coder initialization skeleton merely to repeat this guidance.

The fenced workflow metadata used by prompt templates must be labelled or
explained so an author cannot mistake it for top-of-file OKF/profile
frontmatter. Preserve its existing workflow fields and semantics.

For this slice, opt in exactly the new canonical guide (`framework_doc`) and the
new self-report (`self_report`). Do not add frontmatter to modified legacy files.

### D. Focused Compatibility Tests

Add `tests/test_okf_authoring_migration.py` using `unittest` and the existing
checker surface. Do not create a second parser or duplicate accepted checker
logic. Cover at least:

- this coding prompt and the new canonical guide pass the existing profile
  checker;
- every copy-ready frontmatter example in the guide is accepted when placed
  before a minimal Markdown body, and the examples collectively cover all
  template-owned registry types named above;
- a temporary mixed set containing plain legacy Markdown, a profiled prompt,
  and a profiled self-report produces the accepted separate legacy/profile
  outcomes without mutating any input;
- the default prompt template is explicitly legacy/no-frontmatter, while exact
  path/type opt-in guidance is discoverable from coding, review, and self-report
  templates;
- self-report canonical headings and the onboarding-copy scaffold invariant
  remain intact;
- the migration entry point and root README link to the canonical guide instead
  of duplicating its detailed contract;
- no test assumes that profile conformance grants execution eligibility or
  other authority.

Prefer testing stable structural invariants, checker results, and links over
asserting long prose passages. If importing an accepted checker constant avoids
duplicating the type registry in test code, reuse it without turning that
internal import into a new public API claim.

### E. Honest Current Contracts And Self-Report

Update only the directly affected current package documents:

- `08_pkg/public_api_contract.md` to describe the implemented documentation/
  template authoring surface and explicitly say there is still no writer,
  converter, or inventory CLI;
- `08_pkg/testing_strategy.md` to describe the mixed legacy/profile compatibility
  coverage;
- `08_pkg/package_status.md` to describe the implemented M005 candidate surface
  without claiming review acceptance or milestone closure.

Write the self-report at the fixed path using the unchanged canonical headings
and actual top-of-file `type: self_report` profile frontmatter. Record:

- exact opted-in paths and their assigned types;
- the guide/template/test changes;
- mixed legacy/profile and input non-mutation evidence;
- profile-check results for this prompt, the guide, and the self-report;
- inherited validation and artifact-preflight commands with dated results;
- confirmation that no historical artifact conversion, parser/checker/profile/
  fixture/navigation change, new dependency, writer, converter, inventory CLI,
  source-repository edit, or commit occurred.

Do not edit architect-owned routing or closure surfaces: `PROJECT_STATE.md`,
`MILESTONES.md`, `prompts/INDEX.md`, review indexes, or the roadmap.

## Non-Goals

- No YAML serializer, writer, editor, formatter, converter, bulk migration,
  inventory CLI, repository scanner, generated metadata, or comment-preserving
  round-trip claim.
- No frontmatter retrofit to historical artifacts, existing templates, current
  state, accepted prompts/reports, fixtures, or generated navigation output.
- No default-on profile mode, directory inheritance, global toggle, mandatory
  metadata beyond `type` and `framework_profile`, or authoring burden disguised
  as a recommendation.
- No change to the accepted profile version, registry, producer envelope,
  reason codes, fixtures, PyYAML checker/adapter, limits, dependency declaration,
  or navigation view.
- No inference of truth, approval, freshness, safety, trust, current state, or
  execution eligibility from frontmatter or checker success.
- No stable `0.1`, M006 handoff, llloom, frutlups, Drift, model, service,
  credential, cloud, network, or live-cost work.
- No edit to another repository and no commit, verdict, review prompt, roadmap
  advance, or milestone closure.

## Verification

- Install/use the environment described by `ENVIRONMENT.md`, including the
  declared PyYAML dependency, then run
  `python -m unittest discover -s tests`.
- Run `python -m unittest tests.test_okf_authoring_migration` directly.
- Run the profile checker over this prompt, the canonical guide, and the
  self-report:
  `python scripts/artifact_integrity_preflight.py --profile <exact paths>`.
- Run `python scripts/artifact_integrity_preflight.py --tests-root tests` over
  the self-report and every changed Markdown file. Resolve hard errors and
  report advisory warnings with context.
- Prove the mixed temporary legacy/profile test leaves every input byte
  unchanged and keeps execution eligibility unevaluated.
- Run `git diff --check`.
- Run `git status --short` and `git status --short --ignored`; confirm only
  intended slice files are tracked and caches/local outputs are ignored or
  cleaned.
- Inspect the final diff for duplicated profile rules, accidental historical
  conversion, changed canonical self-report headings, and authority inflation.

## Definition Of Done

- One canonical guide makes minimum profile authoring copy-ready for every
  template-owned artifact class without duplicating the full profile contract.
- Opt-in requires exact new artifact paths and registry types; legacy remains
  the default and mixed repositories demonstrably work.
- Coding, review, and self-report templates provide concise, consistent routing
  to the guide, and workflow metadata cannot be mistaken for document
  frontmatter.
- Adoption, rollback, downstream-dependency caution, and profile-version change
  control are explicit, additive, and non-destructive.
- The existing checker validates all examples and opted-in artifacts read-only;
  no writer, converter, inventory CLI, parser duplication, or new dependency is
  introduced.
- Focused and inherited tests pass, canonical self-report headings remain
  stable, documentation is honest, and every non-goal is preserved.

Do not commit. The architect/reviewer owns the review prompt, verdict, governance
routing, and M005 milestone commit after a positive review.
