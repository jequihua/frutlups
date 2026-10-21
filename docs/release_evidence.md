# Release evidence: 0.4.1

This reference preserves the artifact identities, qualification results and limitations
from the accepted release guide. For current installation and operation, use the
[release guide](release.md). Publication is recorded on the
[GitHub Release](https://github.com/jequihua/frutlups/releases/tag/v0.4.1).

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

## Qualification evidence

Four bodies of evidence support this release. They are distinct; do not read one as
another.

### 1. 0.4.0 installed exact-pair suite (historical)

The [paired qualification](paired_qualification.md) suite installs the frozen 0.4.0
wheel and the PyYAML wheel into fresh isolated environments. It drives fake providers
through rows P01â€“P14: lifecycle, both handoffs, boundaries, recovery, findings, usage,
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
