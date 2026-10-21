# frutlups 0.4.0 release candidate

Status on 2026-10-17: source preparation for M006-S04. This is a candidate
record, not a release. No 0.4.0 wheel exists yet and S04 is not accepted.
Detailed gates are in `02_analysis/m006_s04_admission/r1/assessment.md`.

## Current status

- Version: the owner selected 0.4.0
  (`questions/answered/M006_S04_version_040.md`). `frutlups.__version__` and
  `pyproject.toml` `project.version` are both 0.4.0. Dependencies, build
  backend, the `frutlups` console entry point, the `pi_delegate/index.ts`
  package data and `requires-python` are unchanged from 0.3.2.
- Accepted source evidence: M006-S03 was accepted at
  `0749f7e01625686d26b10c5f993a67402c1aa4d4`, with 1,830 passing nodes and
  zero skips in its full run. That evidence belongs to that accepted source,
  not to this candidate or to any wheel.
- Candidate source evidence: only the focused metadata selection,
  `tests/test_package.py` plus `test_candidate_git_archive_install`. That
  incident installs a copied source subset into disposable scratch; it is a
  diagnostic, not the canonical wheel or an installed-wheel qualification.
- Not yet existing: a reviewed source checkpoint commit for this candidate, the
  canonical wheel and its hash, the installed-wheel smoke record and the final
  full execution on candidate bytes.
- A development editable installation may still report 0.3.2 distribution
  metadata. It is not evidence about the 0.4.0 wheel.

## Final template pins

Reused unchanged from the accepted final T export; this is not new T
qualification. Hashes are raw SHA-256. The T commit, archive and corpora are
also recorded in `tests/fixtures/v5_final.manifest.json`, listed last.

| Input | Bytes | SHA-256 |
| --- | --- | --- |
| T commit | - | `711bce067eb0b3d05abf8cdec54f3cc812f62a5d` |
| `tests/fixtures/v5_final/template-5.0.0.tar` | 460800 | `a8d3764d87804e0ae9b880f02a4f53592e553dc53c2d24d51b22d6a7bf4ec04a` |
| `tests/fixtures/v5_final/protocol_v2.json` (16 cases) | 25323 | `59a19d634ccfbd8d8c37616196cb59974b06310b168810e6ba71fda3b8a39891` |
| `tests/fixtures/v5_final/autonomy_v1.json` (134 cases) | 343766 | `ca8d6a2b159bb3f468ce725ab0645cd6153338bdb70a19f58027ab16a1c63150` |
| `tests/fixtures/v5_final.manifest.json` | 141317 | `c533c82f3e3d66f35ed6140d76554adbee0a9c2eb1cad55e2e5c2c292acf9c0c` |

## Candidate freeze sequence

Each step needs its own authority; none is granted by an earlier step.

1. Architect records the complete coded manifest and obtains independent source
   inspection. This is preliminary review, not S04 acceptance.
2. With exact owner authority, the reviewed source is committed as a candidate
   checkpoint. S04 stays unaccepted.
3. With separate build/install authority, the package is materialized from that
   clean commit into owned scratch, every exported source byte is compared, and
   one canonical wheel is built offline with the pinned backend, no build
   isolation and no dependency resolution. A PyYAML 6.0.3 wheel for the
   selected runtime is a prerequisite whose availability is not yet
   established.
4. That exact wheel and its PyYAML wheel are installed into a fresh isolated
   scratch environment. Checks: import and distribution version 0.4.0, console
   entry point, wheel metadata and RECORD, byte-exact Pi extension payload, no
   editable or `PYTHONPATH` leakage. The installed entry point runs from outside
   the checkout on a fresh final-template project: status parity, and refusal
   without a grant before any probe, job, Git or ledger change.
5. One separately owner-authorized architect full execution of the unchanged hermetic
   verifier on final candidate bytes: all 1,830 baseline nodes retained, zero
   skips, exit 0, stable witnesses.
6. Independent final review of the source review, manifest, wheel and install
   smoke, and full receipt. Only then may S04 be accepted.

The wheel hash, source commit, toolchain and dependency identities are to be
recorded outside the package in the architect-owned
`02_analysis/m006_s04_admission/r1/wheel_freeze.json`, which is pending and is
not evidence yet. Recording them there does not change the source candidate, so
the wheel never has to be rebuilt to contain its own identity. A qualified wheel
is never replaced by a version-only rebuild.

## Pending qualification

- S04: steps 1-6 above.
- M006-S02: positive installed native pair lifecycle and recovery matrix,
  using the frozen S04 wheel.
- M007: live provider, delegation and release work, using the same wheel.

Any changed executable, dependency, metadata or mandatory framing needs a new
reviewed identity and the affected requalification.

## Not claimed

No public release, installed or live support, installed Pi loading, arbitrary
helper scale, two-hour reliability, minimum Python, POSIX, native symlink or
sleep/resume qualification is claimed. The declared `requires-python` range does not imply
that every version or platform has been tested.
