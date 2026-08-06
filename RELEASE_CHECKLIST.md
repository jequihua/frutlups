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

## 1. Environment setup / dev install

```powershell
# create the 3.11 venv if it does not exist (use a real Python 3.11 launcher)
py -3.11 -m venv .venv

.\.venv\Scripts\python.exe --version          # expect: Python 3.11.x
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

The `dev` extra installs the type checker and linter (`mypy`, `ruff`). Building
distributions additionally needs `build` or `wheel` (see step 8); install
`wheel` into the venv if a wheel build reports `invalid command 'bdist_wheel'`.

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

- Expect: `Success: no issues found in N source files`.
- The baseline is configured under `[tool.mypy]` and scoped to `src/frutlups`.

## 4. Lint and format check

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

- Expect: `All checks passed!` and all package files already formatted.
- Ruff is scoped to `src/frutlups` via `[tool.ruff].include`; `tests/` linting is
  intentionally deferred and is not part of the enforced baseline.

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

Build into a temporary directory so nothing is published and no byproducts are
left in the tree. If the `build` frontend is installed, prefer it; otherwise use
`setuptools.build_meta` directly (no broad tooling required):

```powershell
# preferred, if installed:
.\.venv\Scripts\python.exe -m build --outdir .dist_check
```

```powershell
# fallback probe with no extra frontend (wheel build needs the `wheel` package):
.\.venv\Scripts\python.exe -c @"
import tempfile, os, shutil
from setuptools import build_meta
out = tempfile.mkdtemp()
try:
    print('sdist:', build_meta.build_sdist(out))
    print('wheel:', build_meta.build_wheel(out))
finally:
    shutil.rmtree(out, ignore_errors=True)
"@
```

After building, remove any local byproducts:

```powershell
Remove-Item -Recurse -Force build, dist, .dist_check, src\frutlups.egg-info -ErrorAction SilentlyContinue
```

## 8. `py.typed` is included in built artifacts

`frutlups` ships PEP 561 typing metadata via `src/frutlups/py.typed`, declared in
`[tool.setuptools.package-data]`. Confirm it is packaged in both artifacts:

```powershell
.\.venv\Scripts\python.exe -c @"
import tempfile, os, tarfile, zipfile, shutil
from setuptools import build_meta
out = tempfile.mkdtemp()
try:
    s = build_meta.build_sdist(out)
    with tarfile.open(os.path.join(out, s)) as t:
        assert any(n.endswith('py.typed') for n in t.getnames()), 'py.typed missing from sdist'
    w = build_meta.build_wheel(out)
    with zipfile.ZipFile(os.path.join(out, w)) as z:
        assert any(n.endswith('py.typed') for n in z.namelist()), 'py.typed missing from wheel'
    print('py.typed present in sdist and wheel')
finally:
    shutil.rmtree(out, ignore_errors=True)
"@
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
  dependencies**; that dated fact is preserved rather than rewritten. The M002
  candidate retires the posture deliberately so a single bounded `SafeLoader`
  boundary can own YAML semantics. Until M002 is accepted as a whole, this
  declaration is part of an unaccepted candidate.

  The dependency propagation is guarded by
  `tests/test_packaging.py::DependencyDeclarationTests`,
  `::BuiltMetadataTests`, and `::IsolatedBaseInstallTests`.
- `[project.scripts]` exposes the `frutlups` console entry point.
- `readme`, `authors`, and `license` are present and correct.

**Known packaging cleanup before a public release (deferred):** the current
`license = { text = "Proprietary" }` TOML table form is deprecated by newer
setuptools (PEP 639); recent setuptools emits a `SetuptoolsDeprecationWarning`
noting the table form is scheduled for removal after 2027-02-18. This is **not
fixed in this slice** on purpose:

- the SPDX-string replacement (`license = "..."`) requires a valid SPDX license
  expression, and `"Proprietary"` is not a valid SPDX identifier;
- the SPDX-string form also requires setuptools `>= 77`, above the package's
  build floor; the pinned dev/build environment here uses setuptools 65.5.0,
  which does not emit the warning.

Before a public release, a maintainer should decide on the correct license
expression (a real SPDX identifier, or a license classifier / `license-files`)
and bump the setuptools floor accordingly, then re-run steps 7–8. Until then the
table form remains valid and functional.

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

## 11. Manual publish (explicit post-check — not run by this checklist)

Publishing is a deliberate, manual action performed only after every step above
passes and a human has approved the release. None of it runs by default here.

```powershell
# example only — do not run unless intentionally publishing:
#   .\.venv\Scripts\python.exe -m twine check dist/*
#   .\.venv\Scripts\python.exe -m twine upload dist/*
```

Note: `frutlups` is currently `license = "Proprietary"`; confirm distribution and
index policy before publishing anywhere.
