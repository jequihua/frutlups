# llloom Integration Guide

How the optional `llloom` memory backend fits into the `frutlups` artifact-first
loop. This is a focused integration guide, not an `llloom` manual — for a
first-run path see [`QUICKSTART.md`](QUICKSTART.md); for how the repository's
templates fit together see
[`ARTIFACT_TEMPLATE_GUIDE.md`](ARTIFACT_TEMPLATE_GUIDE.md).

The governing rules live in `05_governance/llloom_operating_model.md`; this guide
is the practical, package-local view of them. Because `llloom` is still in active
development, the command examples below are **current examples, not immutable
package assumptions** — read the current upstream `llloom` instructions before
relying on exact command details.

> Two kinds of command appear below. **`frutlups` commands** use the project
> Python 3.11 venv from `08_pkg/` (`.\.venv\Scripts\python.exe -m frutlups ...`).
> **`llloom` commands** are a *separate* external tool (`llloom --root ...`) that
> is **not** installed by this package and may not exist in a given project. Do
> not mix them up, and do not assume `llloom` is available.

## What llloom is, and what it is not

`llloom` is an **optional, source-grounded memory backend**. It can help agents
recall prior decisions, claims, sources, and project facts without treating chat
history as authoritative. It is **not** a hard dependency:

- **Memory is optional and disabled by default.** A project without `llloom`
  configured must keep working normally; the loop runs fully without it.
- **Repository artifacts stay above memory in the authority order.** Roadmaps,
  prompts, reviews, governance files, and source files remain the primary loop
  state. Memory provides source-grounded *context and evidence*, never
  authoritative loop state.

You do not need `llloom`, provider credentials, CI, or any external service to
run the loop.

### Authority order

When memory and project files disagree (from the operating model), trust, in
order: raw source bytes / source-registry hashes → claim YAML with locators and
`excerpt_hash` → operation journals and update reports → rendered pages →
rebuildable sidecars (search, graph, indexes). Generated prompt text and chat
transcripts are never higher authority than the repository artifacts they cite.

## How memory appears in `frutlups`

Two different memory facts surface read-only in `frutlups status`:

- `memory_mode` is the declaration-authoritative runner contract. It answers
  what the project permits: `none`, `lightweight`, or `llloom`.
- `memory` is a backend-health observation. Availability can never activate a
  mode or grant permission.

With mode `none` — the default when no declaration exists — backend health is
disabled and the loop proceeds normally:

```powershell
# frutlups command (venv), from 08_pkg/
.\.venv\Scripts\python.exe -m frutlups status ..
# ...
# Memory: disabled
```

The machine-readable form carries both facts under separate keys:

```powershell
.\.venv\Scripts\python.exe -m frutlups status .. --json
```

```json
{
  "memory_mode": {
    "contract_id": "frutlups.memory_mode",
    "contract_version": "1",
    "valid": true,
    "mode": "none",
    "memory_root": null,
    "diagnostics": []
  },
  "memory": {
    "enabled": false,
    "backend": "disabled",
    "root": null,
    "message": "memory backend disabled",
    "diagnostics": []
  }
}
```

An external runner must bind only contract id `frutlups.memory_mode`, version
`"1"`, and a valid canonical mode. A malformed or ambiguous declaration has
`valid: false` and must be refused rather than guessed. For `llloom`,
`memory_root` is the safe declared repository-relative reference; it does not
assert that the directory or executable is available.

When an authorized backend is available, `enabled` becomes `true`, `backend`
names the backend, `root` is the memory root, and `diagnostics` carries conservative,
read-only notes. An absent or broken backend is reported as health state, not
permission. A stale llloom directory under mode `none` remains disabled.

## Normal coding and review slices: read-only

Normal slices are **read-only** with respect to memory. Use memory to gather
context, cite claim IDs, and spot stale or conflicting facts — never to edit
claim YAML, rendered pages, indexes, or lock files by hand.

Current read-only `llloom` examples (external tool; `<memory-root>` is a
configured path such as `07_app/llloom_memory/`):

```powershell
# llloom commands (external tool, NOT a frutlups venv command)
llloom --root <memory-root> status
llloom --root <memory-root> doctor
llloom --root <memory-root> query "<question>" --status reviewed --verification-status verified
llloom --root <memory-root> claim-card <claim-id>
llloom --root <memory-root> verify
```

`doctor` is the central read-only, model-free health check. These commands are
the *current* recommended read-only set; treat them as examples to confirm
against upstream, not as fixed package behavior.

## Memory-update slices: explicit and guarded

Memory mutation belongs **only** to an explicit memory-update slice with its own
coding prompt and review evidence — never as a side effect of a normal coding or
review slice. The preferred guarded pattern is dry-run, then apply, then capture
evidence:

```powershell
# llloom commands — ONLY inside an explicit memory-update slice
llloom --root <memory-root> seed apply update.yaml --dry-run
llloom --root <memory-root> seed apply update.yaml
llloom --root <memory-root> doctor --last-op
```

Seed manifests use `seed_manifest_v1`. The update report under
`state/reports/updates/<op_id>.yaml` and the `doctor --last-op` update-review
bundle are part of the review evidence. Claim promotion follows
`draft -> reviewed -> validated`; replace a validated claim with
`supersede old --by new` rather than editing history.

If you are in a normal slice and about to change memory state, stop — that work
needs its own memory-update prompt.

## Accepted warnings stay visible and evidence-backed

`doctor` warnings that a project chooses to accept live in
`state/reports/health/accepted_warnings.yaml`. Each accepted warning must carry an
exact `warning_id`, a reason, and evidence. **Do not hide warnings behind vague
allowlist entries** — an accepted warning is a documented, evidence-backed
decision, not a silenced one. `frutlups` surfaces accepted-warning visibility
(read-only) through `read_accepted_warnings(memory_root)`.

## Recovery posture

Prefer read-only health and reconcile-style workflows before any manual repair:

```powershell
# llloom commands (external tool)
llloom --root <memory-root> status
llloom --root <memory-root> doctor
llloom --root <memory-root> reconcile
```

Do **not** delete locks manually. Use journal-only unlocks for human notation,
and `unlock --clear-stale --reason ...` only for timed-out, recoverable locks.
Rebuild derived state (`rebuild search` / `graph` / `index`) only when
instructed. When current upstream instructions differ from
`05_governance/llloom_operating_model.md`, pause and record the divergence before
changing behavior.

## How `frutlups` keeps llloom isolated and patchable

All assumptions about `llloom` live behind a small, patchable boundary in
`08_pkg/src/frutlups/memory.py`, so upstream changes are easy to absorb without
touching the rest of the package. The current high-level surface:

- `MemoryStatus` — the coarse, read-only summary the rest of the package consumes
  (`enabled`, `backend`, `root`, `message`, `diagnostics`); `MemoryBackend` is the
  protocol and `DisabledMemoryBackend` is the default.
- `LlloomCliBackend` — a **read-only** backend exposing `status`, `doctor`,
  `query`, `verify`, and `doctor_last_op`. Mutating commands (seed apply, ingest,
  render, supersede, unlock, reconcile, rebuild) are *intentionally absent* from
  it; mutation is not reachable through the normal-slice surface.
- `MemoryCommandRunner` / `SubprocessMemoryCommandRunner` — an injectable command
  runner that never raises (missing executable, timeout, and errors all return a
  `MemoryCommandResult`), so tests can substitute a fake without installing
  `llloom`.
- `detect_memory` — conservative detection; absence is a normal `disabled` state.
- `build_memory_prompt_snippet` — read-only prompt enrichment that degrades
  gracefully when memory is unavailable.
- `capture_doctor_last_op_evidence` — bounded, read-only evidence capture.
- `plan_seed_manifest_update` / `read_accepted_warnings` — planning and
  accepted-warning visibility helpers.

Treat these names and shapes as the *current* surface, not a frozen contract:
because `llloom` is still evolving, the package keeps this boundary thin and
patchable rather than promising stable internals. `frutlups` correctness does not
depend on unstable `llloom` internals.

## Roles and providers

Memory usage is provider-neutral. The logical roles (`architect`, `reviewer`,
`coder`, `human`) decide *when* memory is read or, in a dedicated slice, mutated —
not which vendor or model is in use. `llloom` is a memory tool, not a required
provider; nothing in the loop hard-codes it.

## Verifying without llloom or memory mutation

You can confirm the package's memory posture using only read-only `frutlups`
commands in the venv — no `llloom` install and no memory mutation:

```powershell
# frutlups commands (venv), from 08_pkg/
.\.venv\Scripts\python.exe -m frutlups status ..        # shows "Memory: disabled"
.\.venv\Scripts\python.exe -m frutlups status .. --json  # memory block, enabled=false
```

## Where to go next

- First-run path: [`QUICKSTART.md`](QUICKSTART.md).
- Template integration: [`ARTIFACT_TEMPLATE_GUIDE.md`](ARTIFACT_TEMPLATE_GUIDE.md).
- CLI reference: the **CLI Usage** section of [`README.md`](README.md), or
  `frutlups <command> --help`.
- Release preparation: [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md).
- The governing rules and the authoritative, up-to-date command set:
  `05_governance/llloom_operating_model.md` at the repository root.
- The mature-project migration guide (M015-S04) is planned as a separate
  document.
