# Package Tests

This directory holds the complete `frutlups` product test suite. It runs offline
and deterministically. With the package installed (or `src` on `PYTHONPATH`), run
it from the package root:

```powershell
python -m pip install ".[dev]"
python -m unittest discover -s tests
```

The suite covers the CLI verbs and their text/JSON contracts, project discovery
and roadmap parsing, the planning-frontier and loop-resume surfaces, the bounded
single-artifact write paths (coding prompt, review prompt, verdict record), the
private bounded YAML boundary, the layout and runner policy, the acceptance and
gate logic, and the optional read-only OKF/profile observation. Hostile-input,
resource-limit, mutation-boundary, packaging, and installed-package checks are
included.

Tests assert exact contract values and never pretend future behavior exists.
