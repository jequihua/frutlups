# frutlups

frutlups 0.4.1 is the optional autonomous runner for native `/2` projects made from
the agentic project template, final export T711bce0
(`711bce067eb0b3d05abf8cdec54f3cc812f62a5d`). You never need it for manual
operation: the template's own scripts run the whole loop without frutlups installed.
frutlups dispatches model seats only under the project's autonomy profile and a
current owner grant recorded with the template's `python scripts/ledger.py run admit`.

The [release guide](docs/release.md) covers the exact qualified artifact, installation,
configuration, operating rules, evidence, exclusions and upgrade notes.

## Compatibility

- **Native `/2` only.** Legacy roadmap schemas, ledger prefixes, mixed histories and
  receipts are refused before any effect. Do not downgrade or bypass the schema to make
  a runner proceed; continue legacy projects with their pinned tools.
- **frutlups 0.3.x does not run `/2` projects.** The T711bce0 export's own documents
  were written before 0.4.x and still say no compatible runner has been released.
  The qualified pair is recorded in the [release guide](docs/release.md).
- **Manual and runner operation interoperate.** You may hand a project from the runner
  to the manual scripts and back. Reservations, charges and unresolved-work guards
  persist across both.

## Python and platform

- **Declared:** `requires-python >=3.11`. PyYAML `>=6.0.3,<7` is the only runtime
  dependency.
- **Tested:** Windows with 64-bit CPython 3.14.3, PyYAML 6.0.3 and Git for Windows.
  No other Python version, operating system or POSIX host has been qualified.
- **Known gap:** some evidence checks call `Path.is_junction`, which exists only from
  Python 3.12. They are therefore expected to fail on 3.11 (carried finding M003-S02-01).

## Installation

There are two routes, and only the first installs the qualified artifact.

1. **The frozen wheel** `frutlups-0.4.1-py3-none-any.whl` (130946 bytes, SHA-256
   `68ad38d5d16797eedf7527167ce236ec771f343f7f928edaec84e9fd848090ac`). Obtain it
   from the maintainer; no package-index release or download URL is asserted.
   Check the hash, then install it into a fresh virtual environment as described in
   [installing the frozen wheel](docs/release.md#install-the-frozen-wheel).
2. **From source.** Run `python -m pip install .` in the package directory that contains
   `pyproject.toml`. This builds a new wheel. That wheel is not the qualified artifact
   and has not been qualified.

## Configuring a project

A consuming project needs two files at its root:

- **`frutlups.toml`**, committed, schema `frutlups/1`: run boundary, seats, timeouts
  and evidence paths. The template ships an example.
- **`frutlups.local.toml`**, ignored, schema `frutlups.local/1`: absolute `pi`, `claude`
  and `git` executables, `path_dirs`, `env_passthrough` and an optional `llloom`.

Credential-like keys (names ending `_key` or `_token`) are refused in both files.
On Windows, `env_passthrough` must include `SYSTEMROOT`, `COMSPEC` and `PATHEXT`.
Commit the profile, grant and approval changes before running. See
[configuring a project](docs/release.md#configure-a-project).

## Commands

Run these commands from the project root, or pass the root as the optional first argument:

```text
frutlups preflight [root] [--historical-assessment PATH --historical-assessment-sha SHA256]
frutlups run [root] [--until slice|milestone|roadmap] [--once] [--json]
             [--historical-assessment PATH --historical-assessment-sha SHA256]
frutlups status [root] [--usage] [--json]
```

### status

Status is read-only. Its text matches the template's `scripts/ledger.py status`.

`--usage` adds per-slice and per-milestone durable usage from the ledger, with
`completeness` and `unresolved_attempts` ([usage](docs/usage.md)). Unreported
quantities appear as `?` (JSON `null`). These are sums of available evidence, not
billing totals or currency limits.

### preflight

Preflight checks:

- canonical configuration and authority;
- project evidence;
- required executables;
- seat access, when fresh work is admissible under the current grant.

Seat access is checked by running the real project checker and then **probing each
distinct seat**. Probes are real provider calls, and each one is charged as a job
against the grant. With no admissible fresh work, preflight reports the static result
only, with no probe.

Preflight exits 0 when ready, or 2 with one line per refusal. Probe streams stay under
`local_state/frutlups/jobs/` when a probe fails.

### run

`run` holds the project's writer lock and resumes from the ledger:

- It recovers an interrupted approved commit exactly before starting new work.
- It refuses an unfinished attempt instead of redispatching it.
- A `STOP` file at the project root refuses before any checker, recovery or dispatch.

`--until` overrides the configured boundary, and `--once` performs one loop iteration.
The historical flags pass one explicit, owner-adopted selection; give both or neither
([historical eligibility](docs/historical_eligibility.md)).

Text output uses one line per action. `--json` emits one object per line, including
refusals and stop reasons. See [public execution](docs/public_execution.md).

| Exit code | Meaning |
| ---: | --- |
| 0 | Completion, a boundary, or a successful single iteration |
| 1 | An internal stop that the owner must inspect |
| 2 | An admission refusal |
| 3 | A stop that needs human action |

## Documentation

- [Release guide](docs/release.md): artifact, support, configuration, operations,
  evidence and publication handoff.
- [Public execution](docs/public_execution.md): admission order, STOP, recovery and
  refusals.
- [Historical eligibility](docs/historical_eligibility.md): explicit historical
  assessment selection.
- [Usage accounting](docs/usage.md): `status --usage` fields and coverage labels.
- [Adapter compatibility](docs/adapter_compatibility.md): the Pi record limit
  (`[limits] pi_record_mib`) and adapter delegation behavior. This is a dated
  development record; its statement that public execution is refused predates 0.4.0.
- [Fixtures](tests/fixtures/README.md): conformance inputs and historical fixtures.
- Historical records:
  - [Installed 0.4.0 pair qualification](docs/paired_qualification.md);
  - [0.4.0 preparation record](docs/release_candidate.md), dated 2026-10-17;
  - [0.3.0 real-seat qualification](docs/qualification.md).

## Release 0.4.1

- **Merger fix.** 0.4.0 stopped when two routed reviewers referred to the same finding
  with different related links. 0.4.1 merges those links into one sorted union when
  source, hash, finding ID and disposition agree. Genuine disposition conflicts and
  ambiguous references still refuse.
- **Version-only change otherwise.** Apart from the version, this is the only change
  from 0.4.0. Only `frutlups/__init__.py` and `frutlups/_loop_evidence.py` differ
  between the two wheels.
- **Qualification.** Qualified as the exact frozen wheel; see the
  [release guide](docs/release.md#qualification-evidence).

## Release 0.4.0 (superseded)

- 0.4.0 enabled public native `/2` execution through the owned checker, recovery and
  typed writers.
- Its installed exact-pair offline qualification is in
  [paired qualification](docs/paired_qualification.md).
- It ran the coding and review stages of the live canary before its merger failure
  stopped the run. 0.4.1 then completed that canary.

## Historical releases

The sections below describe each release as it was published. They are history, not
current claims.

### Release 0.3.2

- The conformance fixture and this project's scripts follow template v4.0.2 (D029).
- Milestone status accepts `done`, and `next_slice` scans every active milestone in
  roadmap order, skipping fully accepted milestones (D029).
- Finding summaries may contain literal or escaped pipes (D029).
- A coder job's `cost_usd` is recorded on its `coded` event when reported (D029).

None of these changes were requalified on real seats.

### Release 0.3.1

- The milestone-close commit is implemented and covered by a fake-seat test (D019,
  D027). It includes every artifact path recorded for the milestone (D028).
- Colliding new finding IDs from routed seats are suffixed with the seat name (D024,
  D027).
- Preflight probes every distinct adapter, provider and model combination with one
  read-only job, and keeps streams only on failure (D027).
- Usage rows include holistic jobs, and killed Pi jobs record drained usage in their
  job results (D019, D023, D027).
- The evidence hash and the multi-seat corrective round have end-to-end witnesses
  (D027).

None of these changes were requalified on real seats.

### Release 0.3.0

Version 0.3.0 is incompatible with 0.2.x projects (D002).

The [real-seat qualification](docs/qualification.md) ran on Windows only (N3). It
covered an ordinary slice through acceptance, forced coder timeouts and a forced path
violation (D020).

Pi usage summation (D022) and multi-seat report merging (D024) were fixed after the
canary. They are covered by tests only and were not requalified on real seats
(M004-H3).

The milestone-close commit (D019) remained unimplemented and unexercised. The owner
therefore committed the holistic prompt, report and ledger by hand after each
milestone close (M003-S01-H6, M004-H3). Version 0.3.1 implements the milestone-close
commit (D019, D027, D028).

Claude authentication and capacity, and Pi capacity, are text-defined and were not
exercised by the canary (D016).
