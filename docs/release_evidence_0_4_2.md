# Release evidence: 0.4.2

Use the [release guide](release.md) for installation and operation. This note
summarizes the accepted F qualification and F's 2026-10-26 assessment of T's final
return. The [0.4.1 evidence](release_evidence.md) remains a historical record.

## Exact artifacts

Hashes are raw SHA-256. Use the existing frozen wheel, without rebuilding it.

| Item | Identity |
| --- | --- |
| Wheel | `frutlups-0.4.2-py3-none-any.whl`, 131332 bytes |
| Wheel SHA-256 | `a2fd180d53918c641f0f3dc5b1e891909ccf65a6eaef6ffe6285b4e92275c787` |
| Wheel source checkpoint | development `1b1018756ddff13623ee4859f036faa358b4aee3` |
| Template 5.0.0 | `711bce067eb0b3d05abf8cdec54f3cc812f62a5d` |
| [Template archive](../tests/fixtures/v5_final/template-5.0.0.tar) | 460800 bytes, `a8d3764d87804e0ae9b880f02a4f53592e553dc53c2d24d51b22d6a7bf4ec04a` |

All 25 wheel runtime/data members match the frozen source Git blobs. Against
0.4.1, runtime changes are only the continuation guard in `frutlups/loop.py` and
the version literal in `frutlups/__init__.py`; metadata changes identify 0.4.2.
The tag includes later documentation. GitHub's source archives are not the
separately qualified wheel and a rebuild does not inherit its qualification.

## Correction

A legitimate manual blocker previously required a runner dispatch policy even
when the blocked round had never used the runner. Once an authorized human or
architect resolves that blocker, 0.4.2 can continue through the template or runner.
Real runner participation still requires its matching policy; a later manual
pause does not erase it. Frozen fields, charges, grants, write-fence checks and
integrity/recovery refusals remain. Resolution creates no grant or refund.

## Distinct qualification evidence

- **F installed cases:** eleven offline cases cover the expected old-wheel
  refusal, eight manual authority/environment and F/T handoff combinations, an
  actual write-fence refusal and a two-partial-product continuation. Fake seats
  were used. Detection/refusal of an out-of-scope write is not filesystem confinement.
- **F source suite:** the accepted replacement full execution passed 1,937 tests
  and 5,811 phases with zero skips and stable witnesses. The original run's
  hibernation-related checker-deadline failure is preserved separately.
- **T offline return:** 28 new observed 0.4.2 results accompany inherited checks
  with their original version attribution. Three preserved unsuccessful
  observations and the original missing historical snapshot remain disclosed.
  Older 0.4.0/0.4.1 checks are not fresh 0.4.2 execution.
- **T live exact pair:** manual blocking and resolution led to two probes, one
  coder, one verifier, two reviewers and acceptance. All eight protected CLI
  cases passed. Holistic output hit a correctly rejected duplicate finding ID;
  a formatting-only operator handoff through public template commands completed
  closure. A fresh runner restart observed completion with no new dispatch.
  This is operator-assisted completion, not uninterrupted autonomous holistic success.
- **T full gate and reviews:** 257 tests in fourteen modules, zero failures/errors
  and four declared platform/optional skips. Two release reviews, holistic review
  and the later administrative audit/reassessment retain their distinct scope.

The F controller's provenance is
`04_delivery/maintenance/m011/T_resume_r1/F_maintainer_handoff.md` with
`acceptance.json`, and `02_analysis/m011_t_final_return/r1/assessment.md`.
These are development evidence references, not files included in the public wheel.
This release operation reuses the saved qualification and makes no new test claim.

## Support and limits

Qualification covers the recorded Windows x64, CPython 3.14.3 and PyYAML 6.0.3
environment. The live toolchain was Pi 1.0.2 / Node 24.14.0 and Claude Code
2.1.289, using `openai-codex/gpt-6.1-sol` at medium effort and
`claude-sonnet-5-5` at high effort. Those dated observations do not establish
current CLI compatibility, serving builds or account access.

No full minimum-Python, POSIX, native-symlink privilege, sleep/resume, power-loss,
two-hour endurance, arbitrary-scale delegation or hostile-helper containment
claim follows. Python 3.11 retains the `Path.is_junction` limitation. Owner
grants, refusal handling and occasional operator intervention remain necessary.

The live native account charged 90 seconds: 88 provider seconds plus two
verification seconds. These are native seconds, not a dollar cost. Preflight
probes may consume quota. Actual billing, complete helper-inclusive usage and
successful probe raw streams remain unknown; see [usage reporting](usage.md).

The intended distribution is the [v0.4.2 GitHub Release](https://github.com/jequihua/frutlups/releases/tag/v0.4.2),
with the frozen wheel and `SHA256SUMS`. The operator's completed publication
report establishes the observed publication and downloaded-asset checks.
