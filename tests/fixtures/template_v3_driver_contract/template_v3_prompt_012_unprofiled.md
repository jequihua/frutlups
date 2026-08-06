# Coding Prompt 012: Add One Deterministic Disposable OKF Navigation View

Front matter:

```yaml
milestone: M004
slice: S01
role: coder
mode: normal implementation
strictness: Level 3
status: ready
```

## Current State

Read `PROJECT_STATE.md`. M003 is accepted and committed. M004 is now active, but
no generator or generated view exists yet. Preserve the accepted profile,
fixtures, and PyYAML-backed checker. Implement only the first bounded navigation
view described here, write its self-report, and do not commit.

## Active Workspaces

- `02_analysis`
- `03_experiments`
- `05_governance`
- `08_pkg`
- `scripts`
- `tests`
- `prompts`

## Read First

- `05_governance/reviews/m003_s02_correction3_review_report.md`;
- `03_experiments/template_v3_implementation_roadmap.md`, M004 and the global
  rails only;
- `02_analysis/hypotheses.md`, H2 only;
- `08_pkg/architecture_contract.md`, especially Preserved Authorities and the
  Generation Boundary;
- `08_pkg/public_api_contract.md`;
- `08_pkg/okf_profile_v0_1.md`, Section 7 only;
- `08_pkg/testing_strategy.md`, especially Deterministic Regeneration Tests;
- `08_pkg/README.md` and `scripts/README.md`;
- `prompts/templates/self_report.md`.

Do not reopen the profile, fixture, or checker design unless this slice exposes a
real conflict. If it does, stop and report the exact conflict instead of changing
an accepted M003 contract.

## Task

Implement one optional, standard-library-only generator that renders one
deterministic, disposable navigation view for the template's OKF backbone from a
small explicit manifest. The view must lower the cost of locating canonical
sources without copying their substantive content, copying live state, or
becoming an authority itself.

The fixed slice paths are:

- manifest: `08_pkg/okf_navigation_manifest.json`;
- generated view: `08_pkg/generated/okf_navigation.md`;
- generator: `scripts/generate_okf_navigation.py`;
- focused tests: `tests/test_okf_navigation_view.py`;
- measured evidence: `03_experiments/m004_navigation_view_evidence.md`;
- self-report:
  `prompts/for_coding_agent/012_add_deterministic_okf_navigation_views_self_report.md`.

Do not generalize this into a repository-wide indexing framework in this slice.

## Implementation Discipline

Follow YAGNI. Use only the Python standard library; PyYAML is installed for the
accepted profile checker but is neither needed nor permitted for this generator.
Prefer a small script with directly testable pure functions over a new package,
plugin system, template engine, or configuration framework.

The manifest is human-authored navigation configuration, not project-state or
content authority. The generated Markdown is a read model. Canonical facts remain
in the linked source artifacts, and current routing remains only in
`PROJECT_STATE.md`.

## Required Work

### A. One Explicit Bounded Manifest

Create `08_pkg/okf_navigation_manifest.json` with a compact versioned schema for
exactly one view. It must declare:

- a stable manifest schema identifier;
- the single fixed output path;
- a stable view identifier and title;
- an explicitly ordered, bounded set of groups;
- explicitly ordered source entries with repository-relative POSIX paths and
  short navigation labels.

Labels may identify what a reader will find, but must not restate status,
verdicts, limits, profile field semantics, or other facts owned by a source. The
manifest and rendered output must route at least to:

- `PROJECT_STATE.md`, explicitly as the only current-state source;
- `08_pkg/package_status.md`;
- `08_pkg/architecture_contract.md`;
- `08_pkg/okf_profile_v0_1.md`;
- `08_pkg/public_api_contract.md`;
- `08_pkg/testing_strategy.md`;
- `tests/fixtures/okf_profile/manifest.json`;
- `scripts/README.md`;
- `03_experiments/template_v3_implementation_roadmap.md`.

Use no glob, recursive scan, directory discovery, implicit source inclusion, or
machine-local path. Reject duplicate group identifiers, duplicate source paths,
unknown schema versions, malformed field types, empty required strings, and
unknown keys rather than guessing.

Keep the accepted input deliberately small. Enforce and document finite limits
for manifest bytes, group count, source count, and individual path/label length.
Choose modest values that comfortably cover this one manifest; do not build a
generic large-repository crawler.

### B. Safe Fixed-Surface Generator

Create `scripts/generate_okf_navigation.py` with exactly these repository-facing
commands:

```text
python scripts/generate_okf_navigation.py
python scripts/generate_okf_navigation.py --check
```

The CLI must use the fixed manifest and output paths above. Do not expose an
arbitrary root, manifest, source directory, or output path option. Internal
functions may accept paths so tests can exercise isolated temporary repositories.

Required behavior:

- resolve the repository root independently of the caller's current directory;
- validate every manifest source as a unique, contained, existing regular file;
- reject absolute paths, parent traversal, backslash spellings, path aliases,
  generated-output-as-input, containment escapes, and symlink-based escapes;
- validate that the fixed output remains beneath `08_pkg/generated/` and refuse
  unsafe output or parent symlinks;
- read no input files except the fixed manifest, its explicit source entries,
  and the existing fixed output when byte comparison is required;
- render source labels and portable links, not source body text;
- preserve manifest group/source order as the serialization order;
- emit UTF-8 with LF line endings, one trailing newline, and no timestamp,
  hostname, absolute path, environment value, random value, or run-specific data;
- avoid rewriting an already byte-identical output;
- write a changed output atomically in its destination directory and clean up
  any temporary file on failure;
- never modify a source or any file other than the fixed generated output.

The first output line must be a stable generated-file marker naming the manifest
and regeneration command. The visible opening notice must say that the view is
disposable and non-authoritative, that it intentionally does not reproduce live
project state, and that readers must follow links to canonical sources. Every
source entry must show both its short label and repository-relative path.

The default command renders the expected bytes and writes only when needed.
`--check` is strictly read-only:

- exit 0 when the output is byte-identical to the expected rendering;
- exit 1 when the output is missing or stale;
- exit 2 for invalid arguments, an invalid/unsafe manifest or source, unsafe
  output state, or another generation error.

Expected failures must produce concise diagnostics without a traceback. A failed
generation or check must leave any pre-existing output byte-identical.

### C. Generated View And Manual Fallback

Generate and track `08_pkg/generated/okf_navigation.md`. It must contain only the
marker, authority notice, stable headings, and links derived from the manifest.
Do not add OKF frontmatter in this milestone; frontmatter authoring is M005.

Update `08_pkg/README.md` so it:

- keeps its direct links to canonical package contracts;
- offers the generated view as optional navigation;
- states that deleting the generated file loses no canonical information;
- provides both generation and `--check` commands.

Manual navigation must remain possible when the generated view is deleted or the
generator is unavailable. Do not add the generated view to the mandatory
`CLAUDE.md` read order.

### D. Focused Tests

Add `tests/test_okf_navigation_view.py` using only the standard library. Cover at
least these scenarios without weakening or skipping inherited coverage:

- the committed generated file equals a fresh rendering byte for byte;
- two regenerations from identical inputs produce identical bytes;
- delete-and-regenerate restores the exact bytes;
- an already current generation does not rewrite the file;
- `--check` returns 0 for current, 1 for missing/stale, and performs no write;
- manifest order is preserved exactly in the output;
- the generated marker, non-authority notice, source paths, and regeneration
  command are present;
- source bodies and current-state values are not copied into the view;
- malformed JSON, wrong schema, unknown keys, duplicate groups/sources, type
  errors, exceeded limits, missing/non-regular sources, unsafe path forms,
  containment escapes, and output-as-input fail safely;
- a generation failure preserves any existing output bytes and leaves no
  temporary residue;
- source bytes remain unchanged across generation and check;
- invoking the real CLI from outside the repository still targets the repository
  correctly;
- symlink source/output/parent escapes are rejected where the platform permits
  symlink creation, with only a narrowly documented platform/privilege skip.

Use isolated temporary roots for destructive, malformed, stale, and symlink
scenarios. Do not alter the tracked generated file as part of a negative test.
Use scenario descriptions in documentation; do not invent test function names in
contracts before the implementation exists.

### E. Honest Discovery Evidence

Create `03_experiments/m004_navigation_view_evidence.md` as dated, reproducible
slice evidence, not live state. Define one "read" as opening one repository
artifact and define "concept reached" as identifying the canonical artifact that
owns the answer; the generated view itself never counts as the answer source.

Record at least three fixed discovery questions spanning the profile contract,
checker operation, and fixture outcomes. For each question record:

- the pre-slice route from the accepted M003 tree;
- the route using the generated view;
- the artifacts opened and total read count;
- the final canonical source;
- any limitation or judgment involved.

Use repository evidence such as the pre-slice Git tree for the baseline. Do not
invent timings, token counts, or agent-performance claims. The aggregate
generated-view route must use fewer reads, and no question may terminate at the
generated output. If the evidence does not show a real improvement, narrow or
redesign the view rather than claiming success.

### F. Documentation And Self-Report

Update only the directly affected current contracts:

- `scripts/README.md` with the fixed CLI, exit behavior, authority warning, and
  standard-library boundary;
- `08_pkg/architecture_contract.md`, `08_pkg/public_api_contract.md`,
  `08_pkg/testing_strategy.md`, and `08_pkg/package_status.md` to describe the
  implemented candidate surface without claiming M004 acceptance;
- `03_experiments/run_summary.md` with a concise dated pointer to the evidence.

Do not change `PROJECT_STATE.md`, `MILESTONES.md`, the prompt index, review index,
or roadmap routing. Those are architect/reviewer-owned closure surfaces.

Write
`prompts/for_coding_agent/012_add_deterministic_okf_navigation_views_self_report.md`
with the canonical headings from `prompts/templates/self_report.md`. Include:

- the manifest schema and every enforced bound;
- CLI and exit-code behavior;
- a source/output safety audit;
- delete/regenerate, stale-check, determinism, non-rewrite, non-mutation, and
  safe-failure evidence;
- the discovery measurement table and its limitations;
- all commands and dated results;
- confirmation that no source repository, M005/M006 surface, accepted M003
  behavior, or dependency declaration changed.

Do not claim review acceptance or milestone closure.

## Non-Goals

- No second generated view, repository-wide index, prompt/review index generator,
  or generic generation framework.
- No arbitrary manifest/output CLI, glob, recursive scan, auto-discovery, watcher,
  daemon, hook, background process, or network access.
- No copied source bodies, live status, latest verdict, next action, approval,
  freshness, trust, safety, or execution claim in the generated output.
- No generated-file edit-back, canonical-source edit, repair, migration, or
  deletion beyond atomic replacement of the fixed output.
- No OKF frontmatter rollout, YAML writer, profile/schema/fixture/checker change,
  or stable `0.1` declaration.
- No new dependency, package, console entry point, or `pyproject.toml` change.
- No mandatory generated-view read path or loss of manual/offline operation.
- No M005 authoring/migration work, M006 handoff work, llloom, frutlups, Drift,
  model, service, credential, cloud, or live-cost work.
- No edit to the v2 baseline, design source, or another repository.
- No verdict, review prompt, roadmap advance, milestone closure, or commit.

## Verification

- Run `python -m unittest discover -s tests` in an environment installed according
  to `ENVIRONMENT.md`; skips are not acceptance evidence for generator scenarios
  except a documented platform/privilege-only symlink case.
- Run the focused navigation-view tests directly.
- Run the generator twice and prove byte identity and no rewrite on the second
  run.
- Run `python scripts/generate_okf_navigation.py --check` from the repository and
  from a different current directory.
- Delete the generated file in a controlled verification step, regenerate it, and
  confirm its hash/bytes match the accepted pre-delete rendering.
- Hash every explicit source before and after generation/check and confirm no
  source mutation.
- Run `git diff --check`.
- Run `git status --short` and `git status --short --ignored`; confirm only
  intended slice files are tracked and any cache/temp output is ignored or
  cleaned.
- Run `python scripts/artifact_integrity_preflight.py --tests-root tests` over the
  self-report, evidence note, and every changed Markdown contract. Resolve hard
  errors and report advisory warnings with context.
- Inspect the generated Markdown manually for the exact marker, authority notice,
  stable ordering, portable links, lack of copied live state, and lack of
  machine-local/run-specific data.

## Definition Of Done

- One explicit bounded manifest drives exactly one fixed generated view.
- The generator is standard-library only, deterministic, path-contained,
  symlink-aware, atomic on change, and non-rewriting when current.
- `--check` distinguishes current, stale/missing, and invalid/unsafe states while
  remaining read-only.
- The tracked view is byte-identical to regeneration, visibly disposable and
  non-authoritative, and contains only stable navigation material.
- Negative tests prove malformed/hostile inputs and unsafe output state fail
  without source mutation, output corruption, traceback, or temp residue.
- Delete/regenerate, repeated-render, stale detection, outside-CWD invocation,
  manual fallback, and inherited regression scenarios pass.
- Dated evidence shows fewer aggregate reads for at least three fixed discovery
  questions, with every route ending at a canonical source.
- Directly affected documentation and the canonical self-report are complete and
  honest.
- Every non-goal is preserved.

Do not commit. The architect/reviewer owns the review prompt, verdict, governance
routing, and M004 milestone commit after a positive review.
