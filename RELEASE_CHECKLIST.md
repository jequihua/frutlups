# frutlups Release Checklist

A practical, local-first checklist a maintainer works through before tagging or
publishing a `frutlups` release. It is provider-neutral and standard-library
first: nothing here publishes, tags, or uploads automatically. Publishing steps
are called out explicitly as manual post-checks.

All commands run from the `08_pkg/` package workspace using the project Python
3.11 virtual environment at `08_pkg/.venv` (Python 3.11 is the compatibility
floor: `requires-python = ">=3.11"` and mypy/ruff target `py311`). Invoke the
interpreter explicitly as `.\.venv\Scripts\python.exe`, or activate the venv with
`.\.venv\Scripts\Activate.ps1`. Do not use the machine-global interpreter.
The only exception is the editable install in step 1, which runs from the
repository root: the composed project validator (`tools/run_project_validation.py`)
also needs the root `artifact-first-project-template` distribution installed
into that same environment.

## 1. Environment setup / dev install

```powershell
# create the 3.11 venv if it does not exist (use a real Python 3.11 launcher)
py -3.11 -m venv .venv

.\.venv\Scripts\python.exe --version          # expect: Python 3.11.x

# from the repository root: install BOTH repository distributions into the one
# venv — the root template project and the product with its dev extra — plus
# the build frontend. `build` provisions pyproject.toml's declared build-system
# requirements in an isolated environment; wheel remains explicit release
# tooling for local inspection and fallback diagnostics.
08_pkg\.venv\Scripts\python.exe -m pip install -e . -e ".\08_pkg[dev]" build wheel
```

Installing only `08_pkg[dev]` leaves the composed validator's root
installed-metadata control skipped; installing both repository distributions
makes that control run. Never borrow packages through the user site or
`PYTHONPATH`: every check in this checklist runs inside this one venv.

The `dev` extra installs the type checker and linter (`mypy`, `ruff`). The
separately installed `build` frontend creates an isolated build environment and
provisions the exact `[build-system].requires` declared by `pyproject.toml`.

## 2. Clean working tree

```powershell
git status --short
```

- Expect no unintended modifications or stray build byproducts.
- **Preserve intentional governance artifacts.** Untracked files under
  `05_governance/reviews/` (self-reports, review reports, verdict records) and
  `prompts/` are durable project evidence, not clutter — do not delete them to
  "clean" the tree.
- `.venv/`, `build/`, `dist/`, and `*.egg-info/` are local/ignored build
  artifacts and may be removed safely.

## 3. Type check

```powershell
.\.venv\Scripts\python.exe -m mypy
```

- Expect the recorded baseline, **not** a clean result. As of 0.1.8 the accepted source
  reports 27 errors in 5 files: `union-attr` 7, `arg-type` 7, `attr-defined` 6,
  `assignment` 5, `no-redef` 1, `import-untyped` 1.
- The gate is that a release introduces **no new finding**, not that the count is zero.
  Compare against the prior release's recorded baseline and investigate any increase.
- Counts vary with checker version; record the version used alongside the result.
- The baseline is configured under `[tool.mypy]` and scoped to `src/frutlups`.

## 4. Lint and format check

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

- Expect the recorded baseline, **not** a clean result. As of 0.1.8 the accepted source
  reports 31 findings, and the formatter would reformat 11 files.
- The gate is that a release introduces **no new finding**, not that the count is zero.
  Compare against the prior release's recorded baseline and investigate any increase.
- Counts vary with checker version; record the version used alongside the result.
- Ruff is scoped to `src/frutlups` via `[tool.ruff].include`; `tests/` linting is
  intentionally deferred and is not part of the enforced baseline.
- Both static lanes carried this debt before 0.1.6. The mypy counts and messages
  reproduce exactly against the pre-Q008 released source at `73f6132`; the Ruff figures
  were recorded at 0.1.6 and have not been measured against an earlier tree. Reducing
  either is separate, unscheduled work.

### Same-tool baseline comparison (mypy and Ruff)

"Compare against the prior release's recorded baseline" is a mechanical step,
not a judgment call. Materialize the exact prior-release `08_pkg` bytes beneath
the ignored local-state root, then run the **same** venv interpreter and the
**same** mypy/Ruff versions against the baseline tree and the candidate tree,
normalize repository-relative paths (and line references embedded in finding
messages), and compare the finding `(path, code, message)` multisets plus the
`ruff format --check` would-reformat path sets. The candidate sets must equal
the accepted baseline sets; any addition is release-blocking debt to close or
explicitly route, never to suppress.

```powershell
# from the repository root, with the prior release commit available:
git archive <prior-release-commit> -- 08_pkg > local_state\<baseline>.tar
# extract beneath local_state\, then from each tree root run:
#   <venv>\Scripts\python.exe -m mypy --no-error-summary
#   <venv>\Scripts\python.exe -m ruff check . --output-format concise
#   <venv>\Scripts\python.exe -m ruff format --check .
```

Record the exact Python, mypy, and Ruff versions beside every result; counts
vary with checker version, so a comparison across tool versions is not
evidence.

## 5. Full test suite

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

- Expect: `OK` with all tests passing.
- **Known ambient instability:** on Windows the shared temp directory can produce
  intermittent `etilqs_*` / `__PSScriptPolicyTest_*` failures unrelated to the
  code. If discovery fails this way, rerun once with isolated temp directories
  and record both results:

  ```powershell
  $env:TEMP = "$PWD\.tmp_test"; $env:TMP = "$PWD\.tmp_test"
  New-Item -ItemType Directory -Force .tmp_test | Out-Null
  .\.venv\Scripts\python.exe -m unittest discover -s tests
  Remove-Item -Recurse -Force .tmp_test
  ```

## 6. CLI help / status smoke checks

```powershell
.\.venv\Scripts\python.exe -m frutlups --help
.\.venv\Scripts\python.exe -m frutlups status ..
.\.venv\Scripts\python.exe -m frutlups status .. --json
.\.venv\Scripts\python.exe -m frutlups next ..
.\.venv\Scripts\python.exe -m frutlups next .. --json
.\.venv\Scripts\python.exe -m compileall -q src
```

- `--help` exits 0 and shows the loop description and examples.
- `status`/`next` run read-only; `--json` emits valid JSON.
- A prompt-health warning for an unmatched coding prompt is expected while a
  slice is mid-flight; it clears once the matching review prompt exists.

## 7. Package build (source distribution and wheel)

Build into a bounded local directory so nothing is published. The `build`
frontend installed in step 1 creates an isolated environment and provisions the
declared `setuptools>=77` and `wheel` build requirements; do not import whatever
`setuptools` version happens to be present in the release venv as the backend.

```powershell
.\.venv\Scripts\python.exe -m build --outdir .dist_check
```

Keep `.dist_check` through step 8 so the exact artifacts just built are the ones
inspected. Remove all local build byproducts after that inspection.

## 8. `py.typed` is included in built artifacts

`frutlups` ships PEP 561 typing metadata via `src/frutlups/py.typed`, declared in
`[tool.setuptools.package-data]`. Confirm it is packaged in both artifacts:

```powershell
.\.venv\Scripts\python.exe -c @"
from pathlib import Path
import tarfile, zipfile
out = Path('.dist_check')
sdists = list(out.glob('*.tar.gz'))
wheels = list(out.glob('*.whl'))
assert len(sdists) == 1, f'expected one sdist, found {sdists}'
assert len(wheels) == 1, f'expected one wheel, found {wheels}'
with tarfile.open(sdists[0]) as archive:
    assert any(n.endswith('py.typed') for n in archive.getnames()), 'py.typed missing from sdist'
with zipfile.ZipFile(wheels[0]) as archive:
    assert any(n.endswith('py.typed') for n in archive.namelist()), 'py.typed missing from wheel'
print('py.typed present in sdist and wheel')
"@

Remove-Item -Recurse -Force build, dist, .dist_check, src\frutlups.egg-info -ErrorAction SilentlyContinue
```

The test suite also guards this invariant
(`tests/test_packaging.py::test_py_typed_packaged_in_sdist`).

## 9. Metadata sanity checks

Confirm the release-facing metadata in `pyproject.toml`:

- `version` is correct for the intended release.
- `requires-python = ">=3.11"` matches the supported floor.
- `[project].dependencies` declares exactly one unconditional runtime
  dependency, `PyYAML>=6.0.3,<7`. Confirm the built wheel and sdist both carry
  it in `Requires-Dist`, and that a base install resolves a version inside the
  range. Type checker, linter, and build tools live only in
  `[project.optional-dependencies].dev` and the build-system requires, and the
  `dev` extra is never required for a base install.

  Through the accepted M001 baseline `frutlups` genuinely had **no runtime
  dependencies**; that dated fact is preserved rather than rewritten. Accepted
  M002 deliberately retired that posture so one bounded `SafeLoader` boundary
  owns YAML semantics.

  The dependency propagation is guarded by
  `tests/test_packaging.py::DependencyDeclarationTests`,
  `::BuiltMetadataTests`, and `::IsolatedBaseInstallTests`.
- `[project.scripts]` exposes the `frutlups` console entry point.
- `readme`, `authors`, and license are present and correct. The source declares
  the SPDX expression `MIT`, lists `LICENSE` under `license-files`, and requires
  setuptools `>=77` so wheel and sdist metadata carry `License-Expression: MIT`
  and the complete license file. Confirm both archives contain it.

## 10. Governance loop closure

A release slice, like any slice, is only complete when the artifact-first loop is
closed in `05_governance/reviews/`:

- [ ] coder self-report written
- [ ] matching review prompt created under `prompts/for_review_agent/`
- [ ] reviewer review report written
- [ ] verdict recorded with `frutlups record-verdict` (verdict record present)

Confirm the loop state reflects this:

```powershell
.\.venv\Scripts\python.exe -m frutlups status ..
```

For a release that is projected into a flatter public repository, repeat the
complete source suite from a fresh projection or extracted sdist after installing
that candidate in an isolated environment. This target-topology run is mandatory:
byte equality with the development tree does not prove that test fixture paths
are portable. The public suite must not read the parent development repository,
an ignored checkout, or any machine-local authority.

## 11. Manual publish (explicit post-check — not run by this checklist)

Publishing is a deliberate, manual action performed only after every step above
passes and a human has approved the release. None of it runs by default here.

```powershell
# example only — do not run unless intentionally publishing:
#   .\.venv\Scripts\python.exe -m twine check dist/*
#   .\.venv\Scripts\python.exe -m twine upload dist/*
```

Frutlups is distributed under the MIT License, including commercial use. Confirm
the built metadata and bundled `LICENSE` file before publishing anywhere.
