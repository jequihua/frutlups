# frutlups Quickstart

The shortest safe path from a fresh checkout to a working artifact-first loop.
This is a first-run guide, not a full manual — it links to deeper docs rather
than repeating them.

`frutlups` orchestrates an artifact-first coder/reviewer development loop.
Repository files are the source of truth; the CLI reads and advances loop state
that lives entirely in artifacts. Agent roles are logical (`architect`,
`reviewer`, `coder`, `human`) and provider-neutral — no specific model or vendor
is required.

## Prerequisites and workspace assumptions

- Python 3.11 available (e.g. via `py -3.11`). 3.11 is the supported floor:
  `requires-python = ">=3.11"`, and mypy/ruff target `py311`.
- A POSIX-ish shell or PowerShell. Examples below use **PowerShell** and run from
  the `08_pkg/` package workspace.
- All commands invoke the project virtual environment explicitly as
  `.\.venv\Scripts\python.exe`. Do not use a machine-global interpreter as the
  default development interpreter.

## 1. Create / activate the venv

```powershell
# from 08_pkg/ — create the 3.11 venv if it does not exist yet
py -3.11 -m venv .venv

.\.venv\Scripts\python.exe --version          # expect: Python 3.11.x
```

You can also activate it for the session with `.\.venv\Scripts\Activate.ps1`;
the examples here invoke the interpreter explicitly so they work either way.
`.venv/` is local and gitignored — never commit it.

## 2. Install dev dependencies

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

The `dev` extra installs the type checker and linter (`mypy`, `ruff`). The
package declares exactly one runtime dependency, `PyYAML>=6.0.3,<7`, which a
base install resolves automatically; the `dev` extra is never required for a
base install. Building distributions additionally needs the `wheel` package
(see `RELEASE_CHECKLIST.md`).

## 3. Run the core health checks

```powershell
.\.venv\Scripts\python.exe -m ruff check .          # lint: "All checks passed!"
.\.venv\Scripts\python.exe -m ruff format --check .  # formatting is stable
.\.venv\Scripts\python.exe -m mypy                   # "Success: no issues found ..."
.\.venv\Scripts\python.exe -m unittest discover -s tests   # full suite: OK
.\.venv\Scripts\python.exe -m frutlups status ..
.\.venv\Scripts\python.exe -m frutlups next ..
```

Lint/format/type checks are scoped to `src/frutlups`. If the full test suite hits
the known ambient Windows shared-temp-dir instability, rerun once with isolated
temp directories (see `RELEASE_CHECKLIST.md`, step 5).

## 4. Read the current loop state

`status` is your orientation command. It reports template health, the active
roadmap, prompt inventory and health, memory state, and — most useful — the
current **loop step** with the suggested next command:

```powershell
.\.venv\Scripts\python.exe -m frutlups status ..
.\.venv\Scripts\python.exe -m frutlups status .. --json   # machine-readable
.\.venv\Scripts\python.exe -m frutlups next ..            # the inferred next slice
```

Whenever you lose track of where things stand, run `frutlups status ..` — it is
the source of truth for "what do I do next."

## The loop, in order

Work advances one reviewable slice at a time, in this fixed order:

```text
roadmap slice
  -> coding prompt            (architect/reviewer creates)
  -> coder implementation + self-report   (coder)
  -> matching review prompt   (coder, after the self-report exists)
  -> review report + verdict  (reviewer)
  -> verdict record           (record-verdict)
  -> next slice
```

The `status` loop step walks you through these: `execute_coding_prompt` ->
`make_review_prompt` -> `execute_review_prompt` -> `record_verdict` -> next.

## 5. Generate a coding prompt (preview first)

Always `--dry-run` before writing, to confirm the inferred slice, sequence, and
target path:

```powershell
.\.venv\Scripts\python.exe -m frutlups make-coding-prompt .. --dry-run
.\.venv\Scripts\python.exe -m frutlups make-coding-prompt ..
```

Coding prompts are written under `prompts/for_coding_agent/` with zero-padded
sequential numbering. A prompt defines the slice's scope, non-goals, verification
commands, and the required self-report and review-prompt paths.

## 6. Where the coder self-report belongs

After implementing the slice, the coder writes a self-report under
`05_governance/reviews/`, named to match the slice
(`<milestone>_<slice>_<slug>_self_report.md`). The required path is stated in the
coding prompt. The self-report is durable project evidence: files changed,
behavior implemented, tests, verification results, known limits, and the matching
review-prompt path.

Section headings must match the self-report schema exactly; if `status` reports
the loop step `fix_self_report`, a required heading is missing or misnamed.

## 7. Create the matching review prompt (only after the self-report exists)

The coder — not the architect — creates the review prompt, and only once the
self-report is in place:

```powershell
.\.venv\Scripts\python.exe -m frutlups make-review-prompt .. --dry-run
.\.venv\Scripts\python.exe -m frutlups make-review-prompt ..
```

This writes a review prompt under `prompts/for_review_agent/` with the same
sequence number as the coding prompt. Until it exists, `status` shows the loop
step `make_review_prompt` (and prompt health notes the unmatched coding prompt).

## 8. Record a verdict (after the review report exists)

The reviewer executes the review prompt and writes a review report under
`05_governance/reviews/`, ending in a verdict: `pass`, `needs_work`, `blocked`,
or `override`. Record it. `--review-report` is resolved from your current
directory, so from `08_pkg/` prefix it with `..\` to reach the project root:

```powershell
.\.venv\Scripts\python.exe -m frutlups record-verdict .. `
    --review-report ..\05_governance\reviews\<slice>_review_report.md --dry-run

.\.venv\Scripts\python.exe -m frutlups record-verdict .. `
    --review-report ..\05_governance\reviews\<slice>_review_report.md
```

This writes a verdict record next to the review report and computes the next
action (advance to the next slice, recode the same slice, or mark the milestone
complete). A `pass` advances the frontier; `needs_work` calls for a corrective
slice.

## Recovering orientation

If you are unsure what state the loop is in or what to do next, run:

```powershell
.\.venv\Scripts\python.exe -m frutlups status ..
```

The loop step and its suggested next command tell you exactly where you are in
the cycle above — no need to reconstruct state from chat history.

## Memory / llloom

Memory via `llloom` is **optional and disabled** unless explicitly configured;
`status` shows `Memory: disabled` by default. Normal coding and review slices
never mutate memory. You do not need `llloom`, provider credentials, CI, or any
external service to run the loop.

## Where to go next

- CLI reference and examples: the **CLI Usage** section of
  [`README.md`](README.md), or `frutlups <command> --help`.
- Preparing a release: [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md).
- Architecture and the loop operating model: `06_infra/architecture.md` and
  `05_governance/prompt_loop_operating_model.md` at the repository root.
