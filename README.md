# frutlups

`frutlups` is a deterministic, artifact-first project-state and artifact-writing
tool for governed coding loops. It reads a repository's roadmap and governance
artifacts, tells you (or an automated runner) exactly what the next loop step is,
and — on request — performs bounded, single-artifact writes such as a coding
prompt, a review prompt, or a verdict record.

It is **not** an autonomous agent: `frutlups` never launches, schedules, or pays
for model calls, and it makes no network, provider, or credential access. A
separate runner may consume the versioned status surface below and drive real
agents; `frutlups` supplies the deterministic state and the governed artifact
writes, nothing more.

## What it does

- **Reads** the active roadmap, prompts, self-reports, review reports, and
  verdict records to infer, from artifacts alone, where a governed loop stands.
- **Computes** the next actionable slice and a versioned planning frontier that a
  runner can act on, fully resumable without chat history or a database.
- **Writes**, only when asked, exactly one governed artifact per invocation
  (coding prompt, review prompt, or verdict record), each with a `--dry-run`
  preview.
- **Stops** cleanly on blocked, invalid, unsafe, or ambiguous states rather than
  guessing.

Legacy, v2, and template-v3 repository layouts are all supported; marker-free
legacy Markdown remains first-class.

A committed layout config may additionally opt into two closed layout modes
(each defaults to the historical behavior when absent):

- `reports.discovery: recursive_contained` — the acceptance-evidence scan
  accepts milestone subdirectories beneath the configured reviews root. The
  inventory is deterministic, stays inside the resolved root, treats ordinary
  files only (link-like entries are never evidence), and fails closed on
  escaped, duplicate, or contradictory authority. `flat` keeps the exact
  historical single-directory behavior.
- `prompts.numbering: global_flat_sequence` with
  `prompts.pairing: workflow_metadata` — coding and review prompts share one
  global number sequence and pair through their validated workflow metadata
  (canonical milestone/slice identity and explicit prompt references), never
  through filename parity, slugs, or proximity; ambiguity fails closed.
  `same_sequence` keeps the historical per-kind equal-sequence pairing.

## Runner integration surface

An automated runner consumes three read-only governed surfaces and may ask
`frutlups` to perform its bounded safe writes:

- `planning_frontier` — a versioned (`frutlups.planning_frontier`) outcome that
  maps the repository's durable state to exactly one behavior; and
- `loop_resume` — the concrete next loop step plus the artifact paths involved;
  and
- `memory_mode` — the versioned (`frutlups.memory_mode`) declared memory mode,
  independent of backend availability, with the safe repository-relative memory
  root when the declared mode is `llloom`.

All are available together from `status --json`. A runner never generates
governance artifacts itself: it observes the status, and when a write is due it
invokes the matching `frutlups` verb, then re-reads the status.

## Installation

`frutlups` targets Python 3.11+ and declares a single runtime dependency, PyYAML.
From the package root:

```powershell
python -m pip install .
# or, with the optional dev tools (mypy, ruff):
python -m pip install ".[dev]"
```

PyYAML (`>=6.0.3,<7`) is a **mandatory** runtime dependency and the sole accepted
YAML semantic engine; there is no custom parser or fallback. Installing without it
fails clearly rather than degrading behavior.

## CLI

`frutlups` exposes eight commands. Run any command with `--help` for its options;
all accept `--json`.

```powershell
# discover the commands and the loop they support
python -m frutlups --help

# read-only: where the loop stands and what to do next
python -m frutlups status <project>
python -m frutlups next <project>

# read-only runner-facing planning surfaces
python -m frutlups orchestrator-plan <project> --json
python -m frutlups orchestrator-handoff <project> --json

# governed single-artifact writes (preview with --dry-run first)
python -m frutlups make-coding-prompt <project> --dry-run
python -m frutlups make-coding-prompt <project>
python -m frutlups make-review-prompt <project>
python -m frutlups record-verdict <project> --review-report <path-to-review-report>

# advance one governed step (bounded, single artifact; --dry-run to preview)
python -m frutlups orchestrator-run <project> --once --dry-run
```

Commands:

- `status` — read-only project and loop status.
- `next` — the artifact-inferred next slice (read-only).
- `orchestrator-plan` — the read-only versioned planning frontier and resume plan.
- `orchestrator-run` — perform the next governed step (one bounded artifact write).
- `orchestrator-handoff` — a read-only coder/reviewer handoff snapshot.
- `make-coding-prompt` — write a coding prompt for the current frontier.
- `make-review-prompt` — write a review prompt for the latest unmatched coding prompt.
- `record-verdict` — parse a review report verdict and write a governance record.

`status`, `next`, `orchestrator-plan`, and `orchestrator-handoff` never write. The
writing verbs produce a single repository artifact and accept `--dry-run` to
preview without writing.

Here `<project>` is the repository whose loop you are governing. When you run the
commands from inside a package workspace that is itself a subdirectory of the
governed repository, `<project>` is the parent directory (for example `..`).

## Optional OKF/profile observation

The package ships one optional, read-only observation surface,
`frutlups.observe_okf_profile_path(path)`, which reports an artifact's OKF-concept
and framework-profile status for the pinned candidate profile
(`framework_profile: "0.1-rc.1"`; see `okf_profile_v0_1.md`). It observes exactly
one supplied path, changes no file, and — importantly — carries **no** routing,
acceptance, gate, runner, or write authority: an OKF or profile result never
decides prompt validity, acceptance, the frontier, a gate, or execution
eligibility. It is a supplementary reader, off by default, that nothing in the
loop calls automatically.

## Documentation

- [`QUICKSTART.md`](QUICKSTART.md) — the shortest path from a fresh checkout to a
  working artifact-first loop.
- [`ARTIFACT_TEMPLATE_GUIDE.md`](ARTIFACT_TEMPLATE_GUIDE.md) — how the artifact
  templates the loop reads and writes fit together.
- [`MIGRATION_GUIDE.md`](MIGRATION_GUIDE.md) — adopting the loop in an existing
  project.
- [`LLLOOM_INTEGRATION_GUIDE.md`](LLLOOM_INTEGRATION_GUIDE.md) — the optional,
  disabled-by-default `llloom` memory backend.
- [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md) — the local-first checklist before
  a package release.
- [`public_api_contract.md`](public_api_contract.md) — the stable public surface.
- [`okf_profile_v0_1.md`](okf_profile_v0_1.md) — the OKF/framework-profile
  candidate the optional observation implements against.

## Public surface and compatibility

The stable public surface — the distribution/import name, version, the eight CLI
verbs, the package `__all__` export set, documented dataclass and JSON shapes, and
`py.typed` — is described in [`public_api_contract.md`](public_api_contract.md).
Changes to it require reviewed compatibility evidence.

## Testing

With the package installed (or `src` on `PYTHONPATH`), run the product test suite
from the package root:

```powershell
python -m pip install ".[dev]"
python -m unittest discover -s tests
```

The suite runs offline and deterministically. See `tests/README.md` for what it
covers.

## Consumer integration status

Connecting `frutlups` to an autonomous runner (`frutlups-drive`) is the **next**
consumer integration, not a capability this repository ships. `frutlups` provides
the accepted control/state surface described above; the runner-side adapters,
live transport, and agent wiring live in that separate project and are not part of
this package.

## License and publication

This package's metadata declares a `Proprietary` license. Choosing a license,
initializing any public remote, tagging, publishing to a package registry, or
making a release are **human-owner decisions** and are not performed by this
repository or its tooling. Resolve the license before any public distribution.
