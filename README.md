# frutlups

frutlups is an unattended runner for template v4 projects and is never used for manual operation.

With Python 3.11+ installed, run `python -m pip install .` from the cloned package directory
containing `pyproject.toml`; PyYAML is the only runtime package dependency (D010).

Run these commands from the project root, or pass the root as the optional first argument:

```text
frutlups preflight [root]
frutlups run [root] [--until slice|milestone|roadmap] [--once] [--json]
frutlups status [root] [--usage] [--json]
```

Preflight checks configuration, project evidence, required executables, and subscription access.
It exits 0 when ready or 2 with one line per refusal. Authentication probe streams remain under
`local_state/frutlups/jobs/`; refusal messages include their location and a scrubbed stderr tail.
On Windows, `env_passthrough` must include `SYSTEMROOT`, `COMSPEC`, and `PATHEXT`.

Run resumes from the ledger. `--until` overrides the configured boundary; `--once` performs one
loop iteration. Exit codes are 0 for completion, a boundary, or a successful single iteration;
2 for preflight refusal; 3 for a stop requiring human action; and 1 for an internal error.
Text output uses one line per action. `--json` emits one object per line, including refusals
and stop reasons.

Status text matches the template's `scripts/ledger.py status`. `--usage` adds per-slice and
per-milestone sums of recorded seat seconds, input/output tokens, and reported cost estimates.
Coder cost estimates come from local job results associated with recorded coding prompts;
reviewer estimates come from the ledger. Unreported quantities appear as `?` (JSON `null`).
These are sums of available evidence, not billing totals or currency limits.

## Release 0.3.0

Version 0.3.0 is incompatible with 0.2.x projects (D002).

The [real-seat qualification](docs/qualification.md) ran on Windows only (N3), covering an
ordinary slice through acceptance, forced coder timeouts, and a forced path violation (D020).

Pi usage summation (D022) and multi-seat report merging (D024)
were fixed after the canary and are covered by tests only; these fixes were not requalified
on real seats (M004-H3).

The milestone-close commit (D019) remains unimplemented and unexercised, so the owner commits
the holistic prompt, report, and ledger by hand after each milestone close (M003-S01-H6, M004-H3).

Claude authentication and capacity classification remains text-defined and was not exercised
by the canary (D016).
