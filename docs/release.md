# Install and run frutlups 0.4.1

Download the published [v0.4.1 release](https://github.com/jequihua/frutlups/releases/tag/v0.4.1).
Use it with the qualified [template 5.0.0 export](../tests/fixtures/v5_final/template-5.0.0.tar).
Follow the exported template's README to create your project and configure its
verification command before enabling autonomous work.

## Support and limitations

The qualified environment is Windows with 64-bit CPython 3.14.3, PyYAML 6.0.3 and
Git for Windows. The release supports Pi and Claude Code seats and switching
between manual template operation and the runner.

The package metadata declares Python >=3.11, but Python 3.11 is expected to fail
because some evidence checks use `Path.is_junction`. Other Python versions and
operating systems have not been qualified. Two-hour endurance, sleep/resume,
arbitrary-scale delegation and hostile-helper confinement are also unqualified.
The successful live workflow used both 0.4.0 and 0.4.1; it is not evidence that
every live action was repeated on 0.4.1.

Pi 1.0.2 (Node 24.14.0) and Claude Code 2.1.289 were the recorded live tool versions.
These are dated observations, not guarantees about later CLI versions or account
access. See [models and delegation](adapters.md) and [release evidence](release_evidence.md).

## Install the frozen wheel

Use a new working directory outside your project. Download
[frutlups-0.4.1-py3-none-any.whl](https://github.com/jequihua/frutlups/releases/download/v0.4.1/frutlups-0.4.1-py3-none-any.whl)
into it. The release also supplies
[SHA256SUMS](https://github.com/jequihua/frutlups/releases/download/v0.4.1/SHA256SUMS).

For the qualified Windows setup, use PowerShell and an installed 64-bit Python
3.14.3. The `py` launcher below must select that interpreter:

```powershell
py -3.14 -m venv .venv-frutlups
.\.venv-frutlups\Scripts\python.exe --version
.\.venv-frutlups\Scripts\python.exe -m pip download --only-binary=:all: --no-deps --dest . PyYAML==6.0.3
```

The download command contacts the configured package index for PyYAML. It does not
install or download frutlups from PyPI. Check both local files before installation:

```powershell
$expected = @{
    'frutlups-0.4.1-py3-none-any.whl' = '68ad38d5d16797eedf7527167ce236ec771f343f7f928edaec84e9fd848090ac'
    'pyyaml-6.0.3-cp314-cp314-win_amd64.whl' = '4a2e8cebe2ff6ab7d1050ecd59c25d4c8bd7e6f400f5f82b96557ac0abafd0ac'
}
foreach ($file in $expected.Keys) {
    $actual = (Get-FileHash -LiteralPath $file -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
    if ($actual -ne $expected[$file]) { throw "Checksum mismatch: $file" }
}
.\.venv-frutlups\Scripts\python.exe -m pip install --no-index --no-deps .\frutlups-0.4.1-py3-none-any.whl .\pyyaml-6.0.3-cp314-cp314-win_amd64.whl
.\.venv-frutlups\Scripts\python.exe -m pip check
.\.venv-frutlups\Scripts\Activate.ps1
```

The install itself uses only those two local wheels. An offline machine can receive
both files in advance and omit the download command. The commands above are
instructions, not a claim of a new installation test during documentation upkeep.

After activation, change to your project directory. If you prefer not to activate,
invoke the environment's `Scripts\frutlups.exe` by its full path and pass your
project directory as the command's root argument.

Installing with `python -m pip install .` builds a different wheel from source.
Use that route for development; it does not reproduce the frozen release artifact.

## Configure a project

Use the template's configuration examples and edit them for your project:

| File | Commit it? | Purpose |
| --- | --- | --- |
| `frutlups.toml` | Yes | Run boundary, model seats, timeouts and evidence paths |
| `frutlups.local.toml` | No | Absolute paths to Pi, Claude Code and Git; environment forwarding |
| `project.local.toml` | No | Optional Python interpreter selection for project verification |

Keep `schema = "frutlups/1"` in `frutlups.toml` and
`schema = "frutlups.local/1"` in `frutlups.local.toml`. These are configuration-format
identifiers; leave them unchanged. They are independent of the installed package
version. The optional interpreter file uses `schema = "template.local/1"`.

In `frutlups.toml`, choose the run boundary (`until`), job and wall-time limits,
corrective rounds, commit-on-accept policy, evidence paths, seats and timeouts.
The template's model names are examples; replace them with models available to
your accounts. See [models and delegation](adapters.md) for effort and record limits.

In `frutlups.local.toml`, supply absolute `pi`, `claude` and `git` executable paths.
All three fields are required even if you use only one model adapter. Configure
`path_dirs` for required child executables and `env_passthrough` for needed
environment variables. On Windows include `SYSTEMROOT`, `COMSPEC` and `PATHEXT`.
The optional `llloom` field is needed only for projects using that integration.

Keep secrets out of both files. Credential-like keys ending `_key` or `_token` are
refused. Authenticate using the provider CLIs' own login mechanisms. The filtered
child environment must still support those logins.

### Authorize work

Enable the template's autonomy profile and use its `docs/autonomy.md` instructions
to record an owner grant with `python scripts/ledger.py run admit`. The grant names
the work, its boundary, allowances and an immutable approval file. Choose allowances
for your actual task; probes also consume jobs. Commit the profile, grant and
approval changes before running.

The runner cannot grant itself more work. Grant commands refuse when `FRUTLUPS_SEAT`
is set, and acting as architect still requires owner authority. Do not bypass the
guard or edit ledger events manually.

## Safe checks before a run

Run these from the project root after activating the environment:

```text
python scripts/ledger.py status
python scripts/ledger.py check
frutlups status
frutlups status --usage
```

These commands make no provider calls. Runner status should agree with template
status. Usage totals come from the ledger; integrity validation can read referenced
evidence, including job evidence. Read-only does not mean evidence is never read.

Then, when ready to consume the admitted allowance:

```text
frutlups preflight
frutlups run --until slice
```

**Preflight may call models.** When fresh work is admissible, it runs the real
project checker and probes each distinct configured seat combination. Each probe
consumes a job and can consume quota or incur charges. Failed probes stay charged;
a failed probe requires a new owner grant before fresh work can proceed. With no
admissible fresh work, preflight performs static checks without probing.

## Operating rules

- **Boundaries:** `--until slice`, `milestone` or `roadmap` overrides the configured
  boundary within the owner grant. `--once` performs one loop iteration, not
  necessarily an entire coding task. Extensions require owner authority through
  the template's `run extend` command.
- **Stop:** create a file named `STOP` at the project root to refuse admission before
  checker, recovery or dispatch. Remove it when ready to resume. This is not a
  guaranteed interrupt of a seat already running.
- **Resume:** run `frutlups run` again in the same project location. An interrupted
  approved commit can be recovered once, without repeating completed seat work.
  Moving a stopped project to another directory is not a qualified recovery path.
- **Unfinished attempts:** a reservation with no durable finish blocks new work.
  First establish what happened to the external work, then have an authorized
  human or architect record the resolution and honest usage using the template's
  `scripts/ledger.py attempt` workflow. An absent PID or a clean tree is insufficient
  evidence. Do not delete the reservation or restart the same work blindly.
- **Commit cancellation:** use the template's `scripts/ledger.py cancel` workflow
  for an unfinishable commit intent. Cancellation does not authorize deleting
  evidence or resetting the index; retaining acceptance requires human authority.
- **Evidence:** keep ledger events, approvals, receipts and reports intact.
  Refusals caused by changed or missing evidence require inspection, not manual
  history repair.
- **Cost:** `status --usage` reports known totals and coverage, not a bill or a
  spending cap. See [usage reporting](usage.md).

Text output contains actions and stop reasons. `run --json` emits one object per
line; `status --json` provides structured status. The optional root argument selects
a project outside the current directory.

| Exit code | Meaning |
| ---: | --- |
| 0 | Completion, a boundary, or a successful single iteration |
| 1 | Internal stop; inspect the diagnostic |
| 2 | Admission refusal; resolve the stated cause |
| 3 | Stop requiring human action |

Advanced recovery can explicitly select an owner-adopted historical deadline
assessment. This is not needed for a normal new project and grants no permission
to execute. See [historical eligibility](historical_eligibility.md) for the paired
flags and validation rules, and [execution reference](public_execution.md) for
internal ordering.

## Upgrading

Stop active work before changing tools. Preserve the project and its history in
place, inspect unresolved attempts and recovery state, and use a separate
environment for the new wheel. The qualified 0.4.0-to-0.4.1 continuation reused
completed reviewer checkpoints. It does not establish arbitrary upgrades from
older template projects. Keep such projects on their pinned tools until a
separate migration is planned; do not change format markers to force compatibility.

## Qualification evidence

The [release evidence](release_evidence.md) records the exact wheel and template
identities, offline installed cases, 1,912-test source pass, composed live canary
and remaining limitations. [Release history](changelog.md) describes older versions.
These references are separate from the steps needed to use the current release.

## Known limitations

In addition to the platform and endurance limits above, very short values in
parent environment variables whose names contain KEY, TOKEN, SECRET or PASSWORD
can be over-redacted in captured evidence and cause a `seat_output` refusal.
Avoid such short values in the launch environment; never put credentials in
tracked configuration or reports.

The public repository contains current source and documentation. The v0.4.1
release tag and wheel remain fixed even when documentation on main is updated.
