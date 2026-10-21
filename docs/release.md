# frutlups 0.4.1 release guide

This guide states what the frozen 0.4.1 wheel supports, how to install and configure
it, the rules that govern autonomous runs, the evidence behind those claims, and what
remains unqualified. The last section is the maintainer's publication handoff.

Status on 2026-10-21: the 0.4.1 artifact and its composed live qualification were
accepted in the development repository. Publishing this source projection and
distributing the wheel are separate human actions still to come. This document does
not record that either has happened. No package-index release or download URL is
asserted anywhere.

## Release identities

Hashes are raw SHA-256.

| Item | Identity |
| --- | --- |
| Qualified wheel | `frutlups-0.4.1-py3-none-any.whl`, 130946 bytes, `68ad38d5d16797eedf7527167ce236ec771f343f7f928edaec84e9fd848090ac` |
| Wheel source checkpoint | development commit `d53f71b766b912399b6cc432c57524865b8b4fd6` (407 committed package files) |
| Release-evidence acceptance | development commit `f1bbea7e49c7dfeec39ecc94c28fab2467ae0593` |
| Final template (T711bce0) | commit `711bce067eb0b3d05abf8cdec54f3cc812f62a5d` |
| Template archive | `tests/fixtures/v5_final/template-5.0.0.tar`, 460800 bytes, `a8d3764d87804e0ae9b880f02a4f53592e553dc53c2d24d51b22d6a7bf4ec04a`, 69 regular members |
| Base corpus | `tests/fixtures/v5_final/protocol_v2.json`, 16 cases, 25323 bytes, `59a19d634ccfbd8d8c37616196cb59974b06310b168810e6ba71fda3b8a39891` |
| Autonomy corpus | `tests/fixtures/v5_final/autonomy_v1.json`, 134 cases, 343766 bytes, `ca8d6a2b159bb3f468ce725ab0645cd6153338bdb70a19f58027ab16a1c63150` |
| Superseded 0.4.0 wheel | `frutlups-0.4.0-py3-none-any.whl`, 130893 bytes, `628c2eb95723d03e8353dce5b70f0ff24d41f38408ab97d5b83882c8deb3d2e4` |

**Wheel contents.** The wheel was built once, offline, directly from the committed
source checkpoint. Its ZIP and RECORD membership, sizes and digests reconcile. All 25
runtime and data payloads match the committed source. Exactly two payloads differ from
0.4.0:

- `frutlups/__init__.py` (the version);
- `frutlups/_loop_evidence.py` (the routed-review merger fix).

**Metadata.** Unchanged from 0.4.0 apart from `Version`:

- console entry point `frutlups = frutlups.cli:main`;
- dependency `PyYAML>=6.0.3,<7`;
- `Requires-Python >=3.11`;
- the packaged Pi extension `frutlups/seats/pi_delegate/index.ts`.

**Later source changes, including this guide, do not change the qualified wheel.** A
wheel rebuilt from any later source, documentation-only changes included, is a
different artifact. It does not inherit this identity or qualification. Never replace
a qualified wheel with a rebuild that keeps the same version.

## Support statement

| Topic | Supported claim |
| --- | --- |
| Project schema | Native `/2` projects from the T711bce0 export. Legacy roadmap schemas, ledger prefixes, mixed histories and receipts are refused before any effect. |
| Manual operation | Never requires frutlups. The template's scripts are self-sufficient. Switching between manual and runner operation is supported in both directions and was qualified offline and live. |
| Adapters | Pi (`adapter = "pi"`, with a `provider`) and Claude Code (`adapter = "claude"`). |
| Declared Python | `>=3.11`. The declaration is not a test claim. |
| Tested environment | Windows, 64-bit CPython 3.14.3, PyYAML 6.0.3, Git for Windows. |
| Not tested | Python 3.11–3.13, other implementations, POSIX hosts, symlinked launchers and sleep/resume. |

**Python 3.11 is expected to fail.** Evidence and commit checks call
`Path.is_junction`, which exists only from Python 3.12. Carried finding M003-S02-01
records that this fails on 3.11.

**Live tool versions are dated observations, not compatibility promises.** These were
recorded statically on 2026-10-20:

- Pi package 1.0.2 on Node 24.14.0;
- Claude Code CLI with static file version 2.1.289.0.

Account entitlement and provider behavior can change independently of frutlups.

## Install the frozen wheel

Use PowerShell. Run these commands from any working directory outside the consuming
project. The placeholders mean:

- `WHEEL_DIR`: a directory holding the supplied frutlups wheel and a PyYAML 6.x wheel
  for your interpreter;
- `VENV`: a new directory outside every repository;
- `PYYAML_WHEEL` and `PYYAML_SHA256`: the PyYAML wheel's file name and SHA-256.

The recorded installed qualifications used
`pyyaml-6.0.3-cp314-cp314-win_amd64.whl` (156429 bytes,
`4a2e8cebe2ff6ab7d1050ecd59c25d4c8bd7e6f400f5f82b96557ac0abafd0ac`).

1. Check the wheel hash. It must equal
   `68ad38d5d16797eedf7527167ce236ec771f343f7f928edaec84e9fd848090ac`:

   ```powershell
   (Get-FileHash WHEEL_DIR\frutlups-0.4.1-py3-none-any.whl -Algorithm SHA256).Hash.ToLower()
   ```

2. Create a requirements file, for example `WHEEL_DIR\requirements.txt`, with
   hash-pinned file URLs:

   ```text
   file:///WHEEL_DIR/frutlups-0.4.1-py3-none-any.whl --hash=sha256:68ad38d5d16797eedf7527167ce236ec771f343f7f928edaec84e9fd848090ac
   file:///WHEEL_DIR/PYYAML_WHEEL --hash=sha256:PYYAML_SHA256
   ```

3. Install offline into a fresh environment, then check it:

   ```powershell
   python -m venv VENV
   VENV\Scripts\python.exe -m pip install --no-index --no-deps --no-cache-dir --require-hashes -r WHEEL_DIR\requirements.txt
   VENV\Scripts\python.exe -m pip check
   VENV\Scripts\frutlups.exe status PROJECT_ROOT
   ```

This is the same offline, hash-checked, no-dependency-resolution shape the
qualification harness used. The commands were not re-executed when this guide was
written.

`python -m pip install .` from a source checkout builds a new, unqualified wheel. Use
it for development only.

## Configure a project

Start from a T711bce0 export and follow its README. Leave frutlups out until the manual
loop works. Then add the runner configuration at the project root:

- **`frutlups.toml`** (committed): schema `frutlups/1`. It sets `until`,
  `max_corrective_rounds`, `max_jobs`, `max_wall_minutes`, `commit_on_accept`, the
  prompt, review and ledger paths, `[seats.*]` and `[timeouts]`. Optional
  `[limits] pi_record_mib` sets the Pi record limit
  ([adapter compatibility](adapter_compatibility.md)). The template's example still
  carries pre-0.4 comments and example model names; choose your own seats.
- **`frutlups.local.toml`** (ignored): schema `frutlups.local/1`. It holds absolute
  `pi`, `claude` and `git` executables, `path_dirs`, `env_passthrough` and an optional
  absolute `llloom`.
  - All three executables are required even when only one adapter is used.
  - On Windows, `env_passthrough` must include `SYSTEMROOT`, `COMSPEC` and `PATHEXT`.
- **`project.local.toml`** (optional, ignored): selects the project's Python
  interpreter for verification.

**Keep secrets out of these files.** Keys ending `_key` or `_token` are refused. Adapters
authenticate through their own CLI login.

**Enable the autonomy profile and record an owner grant** with the template's commands
(`docs/autonomy.md` in the exported project). For example:

```text
python scripts/ledger.py run admit M001 --run-id R001 --slice M001-S01 --stop M001-S01 --jobs 4 --transport-retries 1 --format-retries 1 --seconds 100 --max-corrective-rounds 2 --authority 05_governance/approval-R001.md --reason "Owner approved this slice"
```

The grant binds the hash of an immutable approval file. Run commands refuse when
`FRUTLUPS_SEAT` is set, and `--by architect` requires owner authority. Commit the
profile, grant and approval changes before running.

### Safe checks before a run

Run these from the project root:

| Command | Effect |
| --- | --- |
| `python scripts/ledger.py status` and `python scripts/ledger.py check` | Template status and integrity. Read-only; no provider. |
| `frutlups status` and `frutlups status --usage` | Read-only; must agree with the template. No provider, job store read or ledger write. |
| `frutlups preflight` | Static checks always. When fresh work is admissible under the current grant, it also runs the real project checker and then probes each distinct seat. |

**Preflight is not provider-free.** Its probes are real provider calls, and each is
charged as a job against the grant. A failed probe stays charged. Readiness in that
run then needs a new owner grant. Run preflight without an open grant if you want only
the static result.

## Operating rules

Full mechanics are in [public execution](public_execution.md) and the template's
`docs/autonomy.md` and `docs/upgrading.md`.

- **Owner grants.** Dispatch happens only under the autonomy profile and an open grant
  naming the work. Jobs, seconds and retries are charged against that grant. Unused
  headroom confers no authority after its boundary. Extensions need their own
  approval through `run extend`.
- **Historical assessment.** There is no automatic discovery and no environment or
  configuration fallback. Pass the pair explicitly, with both flags or neither, on every
  invocation:
  `--historical-assessment PATH --historical-assessment-sha SHA256`. It selects one
  human-reviewed, owner-adopted assessment. It changes only the historical
  frozen-timeout comparison and grants no execution permission. See
  [historical eligibility](historical_eligibility.md).
- **STOP.**
  - A `STOP` file at the project root makes `run` and `preflight` refuse as
    `kill_switch`. The refusal comes before any checker or pending recovery, and STOP is
    rechecked immediately before recovery effects.
  - Remove the file to resume.
  - This guide does not claim that STOP interrupts a seat already in flight.
- **Recovery.** A fresh `run` recovers an interrupted approved commit exactly once
  before any new work. It never repeats a seat, verification, approval, commit or usage
  record.
- **Unfinished attempts.** An attempt with no durable finish freezes new work: `run`
  and `preflight` exit 2 and redispatch nothing. To resolve it:
  1. A human or architect first establishes that the external process ended.
  2. They record `attempt_finished` with `status: resolved`, a reason, bound evidence
     and honest usage, using `python scripts/ledger.py attempt <event.json>`.

  A clean tree, an absent PID or a reacquired lock does not prove a child ended.
  Unknown charges stay reserved.
- **Cancellation.** An unfinishable commit intent is cancelled with
  `python scripts/ledger.py cancel <operation> --reason ... --by human|architect`.
  - Only a human may add `--retain-acceptance`.
  - Cancellation preserves evidence, files and index, and grants no cleanup authority.
- **Custom verification and evidence.** The project owns its `verification.full`
  command. The template's hermetic entry point fails closed until it is customized.
  frutlups runs the frozen verification and binds its receipt and evidence. Never
  delete, rewrite or "repair" ledger lines, receipts, reports or bound evidence; broken
  evidence refuses and must be preserved for inspection.
- **Relocation is not supported.** Running 0.4.1 against a copy of a stopped project in
  another directory refused with
  `review candidate product/HEAD/index drift; owner must inspect`. That is a safe
  refusal, not a supported move. Continue a project in place.
- **Usage.** `status --usage` reports sums of durable evidence with `complete`,
  `partial` or `unknown` coverage ([usage](usage.md)). These are not bills, quotas or
  currency limits.

## Upgrading

- **From 0.3.x.** 0.3.x cannot run `/2`. Install 0.4.1 into a new environment; do not
  edit a `/2` schema to suit an older runner. Legacy projects remain on their pinned
  tools.
- **From 0.4.0.** Stop the runner first. Confirm that no unresolved attempt exists and
  that recovery state is understood (`frutlups status` or the template's
  `ledger.py status`). Install the 0.4.1 wheel into a fresh environment and resume
  with `frutlups run`. Do not replace tools underneath live work or unresolved recovery.
  The live canary showed that 0.4.1 reuses completed 0.4.0 reviewer checkpoints
  rather than repeating them.

## Qualification evidence

Four bodies of evidence support this release. They are distinct; do not read one as
another.

### 1. 0.4.0 installed exact-pair suite (historical)

The [paired qualification](paired_qualification.md) suite installs the frozen 0.4.0
wheel and the PyYAML wheel into fresh isolated environments. It drives fake providers
through rows P01–P14: lifecycle, both handoffs, boundaries, recovery, findings, usage,
integrity and grants. Its 70 installed-pair tests still exercise 0.4.0 bytes; they are
not 0.4.1 artifact evidence.

### 2. Eight installed 0.4.1 affected-path cases

The canonical 0.4.1 wheel was installed once into a fresh isolated, non-editable
environment, with no user-site or system-site leakage and a valid RECORD. Each case
used controlled fake providers:

| Case | Result |
| --- | --- |
| Missing grant | Public refusal before probe, coder, verifier or ledger effects |
| Exhausted grant | Probes exhaust the grant; later work refuses without effects |
| Lifecycle | Manual and runner handoffs, status agreement, acceptance and commits, holistic boundary, successor grant, holistic completion and idle restart |
| Checkpoint reuse | Completed reviewer reused; changed or missing checkpoint reports refuse |
| Pending recovery | An interrupted intent is completed exactly once on restart |
| Routed union | Differing related links merge into one sorted union; one acceptance and commit |
| Merger negatives | Conflicting dispositions and ambiguous references refuse, in both orders |
| STOP before recovery | `run` and `preflight` refuse while STOP exists; recovery completes once STOP is removed |

The merger-negative case invokes the installed merger directly; it is not a public
lifecycle run. Synthetic usage values in these cases are fixture data.

### 3. Full source execution

One hermetic full execution ran on the wheel's source checkpoint (development source,
not the installed wheel). It recorded:

- 1912 unique tests: 1900 baseline plus 12 for the merger fix;
- 5736 passing setup, call and teardown phases;
- zero skips and one exit-zero finish;
- 12027.173 enclosing seconds (pytest reports 11928.96 seconds);
- identical before and after workspace witnesses.

**Raw-index limitation.** The raw Git index was first captured about 218 seconds after
launch: launch at 18:44:16 UTC, capture at 18:47:54 UTC. Launch-time raw-index equality
is therefore not established. The semantic index, workspace witnesses and the later
raw-index observations agree.

### 4. Composed live canary: 0.4.0 coding and review, 0.4.1 recovery and completion

This is a composed upgrade and recovery qualification. It does not claim that every
canary action ran under 0.4.1.

**Under 0.4.0**, a disposable canary project went through these steps:

1. A genuine human-policy blocker stopped a public restart with no dispatch, and was
   resolved natively.
2. A read-only manual consultation preceded the human answer.
3. A faulty seed was explicitly attributed to manual fixture preparation.
4. Both routed reviewers found a genuine defect. An oracle amendment followed.
5. Exactly one Pi coding attempt changed one file.
6. Native verification passed.
7. Two reviewer passes then hit the 0.4.0 merger failure, which stopped the run. That
   failure is preserved, not relabelled as success.

**Under 0.4.1**, four public transitions ran once each in place, using the installed
console:

| Transition | Result | Seconds | New model starts |
| --- | --- | ---: | ---: |
| Review reuse | Both original reviewer checkpoints reused; merged report passes | 10.3665 | 0 |
| Acceptance | One native acceptance and scratch commit | 8.7267 | 0 |
| Holistic review and closure | Genuine holistic pass; milestone closed with a scratch commit | 95.9113 | 2 |
| Completed restart | `done`; before and after snapshots equal | 8.1193 | 0 |

All four exited zero, with no timeout and complete streams. The final frutlups and
template status outputs were byte-identical, both commit intents completed, and no
unresolved attempt remained.

### Models, starts and cost observations

| Role | Adapter and model | Effort | Access |
| --- | --- | --- | --- |
| Coder | Pi, provider `openai-codex`, `gpt-6.1-sol` | medium | existing subscription |
| Routed reviewers and holistic | Claude Code CLI, `claude-sonnet-5-5` | high | existing subscription |
| Manual consultation (outside frutlups) | Pi, `gpt-6-astra` | medium | existing subscription |

**Starts.** The canary recorded fourteen native attempts:

- 7 probes;
- 4 routed reviews;
- 1 coder;
- 1 holistic review;
- 1 non-model verification.

That makes thirteen native model starts. Adding the manual consultation gives fourteen
cumulative main-agent starts. There were no retries, grant revisions, extensions or
repeated coder attempts. Recorded native charges were 289 seconds. The grant ended at
its milestone boundary with one job and 4151 seconds unused, which grants nothing.

**Cost.** Seat CLIs reported API-equivalent cost estimates:

- 1.3366938 USD native cumulative;
- 1.4150958 USD including the manual consultation.

These are not subscription bills. Subscription quota or rate-limit consumption was not
measured. frutlups enforces no dollar cap. Aggregate completeness is `partial`, and
earlier unknown usage was not reset to zero. Helper (sub-agent) work is accounted only
inside each main attempt's aggregate.

## Not qualified

The following remain outside every claim above:

- ordinary helper attribution and helper write denial;
- two-hour endurance;
- arbitrary-scale delegation;
- resistance to hostile agents;
- sleep/resume and power loss;
- untested platforms: POSIX, Python other than 3.14.3, the declared minimum, native
  symlinks.

Open review findings against the parked helper-supervision design (for example,
required background shell work) are not addressed by 0.4.1.

Known carried limitations include:

- **M003-S02-01:** Python 3.11 `Path.is_junction`.
- **M002-S03-01:** receipt redaction treats any parent environment variable whose
  name contains KEY, TOKEN, SECRET or PASSWORD as a secret, even when its value is only
  one to three characters long. Such a value can be redacted everywhere, including
  inside a review report, and stop the loop with `seat_output`. Avoid short values in
  such variables.

## Historical records

These documents keep their dated statements:

- [release_candidate.md](release_candidate.md) is the 2026-10-17 0.4.0 preparation
  record. Its "no wheel exists yet" and pending statements describe that date. The
  0.4.0 wheel was later built and qualified and has since been superseded by 0.4.1.
- [paired_qualification.md](paired_qualification.md) and
  [native_conformance.md](native_conformance.md) are development qualification records
  with their own dated candidate wording.
- [qualification.md](qualification.md) is the 0.3.0 real-seat canary.
- The [fixture notes](../tests/fixtures/README.md) separate current conformance inputs
  from historical fixtures.

## Publication handoff (maintainer)

This section is for the human maintainer of the private development repository. Run
every command from that repository's root, the checkout that contains
`front_repo.toml`. `FRONT_REPO` is the separate front-facing repository, normally the
sibling `../frutlups`. Agents never run `apply` or `bootstrap`; the tool refuses both
when `FRUTLUPS_SEAT` is set.

### Three things are published separately

- **Source projection.** `front_repo.toml` maps the package's README, metadata, `src/`,
  `tests/` and `docs/` into `FRONT_REPO`. This publishes source and documentation only.
- **Frozen-wheel distribution.** The qualified wheel above is not part of the
  projection. Distribute that exact file, with its hash, by a channel the maintainer
  chooses. None is assumed here.
- **Later rebuilds.** Any wheel built from projected or later source is a new artifact
  that needs its own identity and qualification. It must not be presented as the
  qualified 0.4.1 wheel.

### Stages

1. **Before any write:** independent review and acceptance of this documentation slice,
   its source commit, and the M007 holistic review.
2. **Read-only inspection** (allowed now):

   ```text
   python scripts/front_repo.py check --target-repo FRONT_REPO
   python scripts/front_repo.py status --target-repo FRONT_REPO
   ```

   `--allow-dirty-source` is a check-only convenience for inspecting uncommitted
   documentation. It is not permission to apply.
3. **Resolve the `.gitignore` divergence** (see below). Apply stays blocked until then.
4. **Human apply**, after stages 1–3, with a clean source and a clean target and no
   overrides:

   ```text
   python scripts/front_repo.py apply --target-repo FRONT_REPO
   ```

5. **Inspect the target:**

   ```text
   git -C FRONT_REPO status
   git -C FRONT_REPO diff --stat
   ```

   Review every change, including `.front_repo_sync.json`, which records the source
   commit and file hashes as provenance.
6. **Human commit and push** of `FRONT_REPO`.

Do not use `--overwrite-diverged`, `--allow-sensitive`, `--allow-dirty-target` or
`--allow-dirty-source` for apply. Do not edit or reset `.front_repo_sync.json`.

### The `.gitignore` divergence

A read-only check of the accepted checkpoint reported:

- 291 new copies, 37 updates and 78 unchanged files;
- exactly one diverged path, `.gitignore`;
- an unchanged target.

The documentation changes alter these counts.

`check` exits zero even with divergence, so a zero exit is not apply readiness. `apply`
refuses while the divergence exists.

**Why it diverges.** The target's `.gitignore` predates the mapping. It is absent from
the recorded sync state (last recorded source `0e244f2`) and differs from the source
file. The source file adds explicit exceptions under the `local_state/` rule, so the
fixture scaffold files `tests/fixtures/{v4_project,v2_project,v5_development,v5_final/project}/local_state/README.md`
stay visible to Git. Without them, those projected fixture members would be ignored in
the front repository.

**Resolving it is a deliberate human decision.** For example, the maintainer can review
and commit a front `.gitignore` byte-identical to the source file, after which `check`
reports it unchanged. Alternatively, the mapping or source policy can be changed
through the development process. Do not force it with `--overwrite-diverged`.
