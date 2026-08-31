"""Packaging invariants documented by the release checklist (M014-S04).

Guards the release-checklist claim that ``frutlups`` ships PEP 561 typing
metadata: ``src/frutlups/py.typed`` must be packaged in a built source
distribution.

M002-S01 extends this module with dependency-propagation guards: the accepted
``PyYAML>=6.0.3,<7`` range declared in ``pyproject.toml`` must reach a built
wheel, a built sdist, and a fresh base installation, while the ``dev`` extra
stays optional and the existing packaging invariants are unchanged. Package
metadata is the single dependency source of truth: the accepted range is
asserted once against the source declaration, and every other check compares
built or installed metadata back to that same declaration, so no second
dependency constant exists.

Three properties of this module matter to a reviewer:

* **Builds honor ``[build-system].requires``.** Distributions are built by a
  throwaway PEP 517 build environment provisioned from the declared build
  requirements, never by importing whatever ``setuptools`` happens to sit in the
  test interpreter. A base editable product environment does not contain
  ``wheel`` and may carry a ``setuptools`` older than the declared floor, so an
  in-process build would either fail or silently emit metadata that omits the
  runtime requirement.
* **Nothing here skips.** A build, metadata, resolution, installation, import, or
  ``pip check`` failure fails its test with a bounded diagnostic. Evidence this
  module exists to produce is never allowed to disappear into a skip.
* **The requirement comparison is total.** Only specifier reordering and
  whitespace normalization compare equal. Extras, markers, direct-reference
  URLs, changed bounds, added exclusions, a different canonical distribution
  name, and any unconsumed syntax do not.

Environment requirement: the tests provision a build environment and a base
install environment with ``pip``, so an installation source (an index or a
populated cache) must be reachable. That requirement is stated rather than
handled by skipping.

Both build environments and every build byproduct are removed in teardown, so
the repository tree is left unchanged.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
import unittest
import venv
import zipfile
from email.message import Message
from email.parser import Parser
from pathlib import Path

# 08_pkg/ package workspace root (parent of this tests/ directory).
_PKG_ROOT = Path(__file__).resolve().parent.parent
_SRC_ROOT = _PKG_ROOT / "src"

# The accepted M002 range. Asserted once, against the source declaration only.
_ACCEPTED_RUNTIME_REQUIREMENT = "PyYAML>=6.0.3,<7"

_OPERATOR = r"===|==|!=|<=|>=|~=|<|>"
_VERSION = r"[A-Za-z0-9][A-Za-z0-9.*+!_-]*"
# A *total* pattern for the only requirement shape this product accepts: a
# distribution name plus an optional comma-separated specifier set, and nothing
# else. Anything the pattern cannot consume end to end -- an extra, a marker, a
# direct-reference URL, trailing text -- is rejected rather than ignored.
_REQUIREMENT_RE = re.compile(
    rf"^\s*(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*"
    rf"(?P<specifiers>(?:{_OPERATOR})\s*{_VERSION}"
    rf"(?:\s*,\s*(?:{_OPERATOR})\s*{_VERSION})*)?\s*$"
)
_SPECIFIER_RE = re.compile(rf"({_OPERATOR})\s*({_VERSION})")

_DIAGNOSTIC_LIMIT = 400

# Built once per module so every artifact check shares one isolated build.
_SCRATCH: Path | None = None
_WHEEL: Path | None = None
_SDIST: Path | None = None
_BUILD_FAILURE = ""


class BuildEnvironmentError(RuntimeError):
    """A build or provisioning step failed, with a bounded diagnostic."""


def _bounded(text: str) -> str:
    """Collapse and truncate captured output so diagnostics stay bounded."""

    collapsed = " ".join(text.split())
    if len(collapsed) <= _DIAGNOSTIC_LIMIT:
        return collapsed
    return collapsed[:_DIAGNOSTIC_LIMIT] + " [truncated]"


def _canonical_name(name: str) -> str:
    """PEP 503 canonical form, so ``PyYAML`` and ``pyyaml`` compare equal."""

    return re.sub(r"[-_.]+", "-", name).strip().lower()


def parse_requirement(requirement: str) -> tuple[str, frozenset[tuple[str, str]]]:
    """Reduce an accepted requirement to (canonical name, specifier set).

    Specifier ordering and surrounding whitespace are packaging syntax and are
    normalized away; the operator/version pairs are compared exactly. Any
    requirement carrying an extra, an environment marker, a direct-reference
    URL, a duplicated specifier clause, or any other unconsumed syntax raises
    :class:`ValueError` instead of silently comparing equal.
    """

    if not isinstance(requirement, str):
        raise ValueError(f"requirement must be a string, got {type(requirement).__name__}")
    match = _REQUIREMENT_RE.match(requirement)
    if match is None:
        raise ValueError(f"not an accepted plain name/specifier requirement: {requirement!r}")
    specifiers = match.group("specifiers") or ""
    found = _SPECIFIER_RE.findall(specifiers)
    clauses = [clause for clause in specifiers.split(",") if clause.strip()]
    if len(found) != len(clauses):
        raise ValueError(f"unconsumed specifier syntax in {requirement!r}")
    pairs = frozenset((operator, version) for operator, version in found)
    if len(pairs) != len(found):
        raise ValueError(f"duplicated specifier clause in {requirement!r}")
    return _canonical_name(match.group("name")), pairs


def _source_project() -> dict:
    with (_PKG_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]


def _source_build_system() -> dict:
    with (_PKG_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["build-system"]


def _venv_python(env_dir: Path) -> Path:
    return env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _run(command: list[str], what: str, *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
    )
    if result.returncode != 0:
        detail = _bounded(result.stderr or result.stdout)
        raise BuildEnvironmentError(f"{what} (exit {result.returncode}): {detail}")
    return result


_BUILD_SCRIPT = """\
import importlib
import json
import sys

backend_name, out_dir = sys.argv[1], sys.argv[2]
module_name, _, attribute = backend_name.partition(":")
backend = importlib.import_module(module_name)
if attribute:
    backend = getattr(backend, attribute)
print(json.dumps({
    "wheel": backend.build_wheel(out_dir),
    "sdist": backend.build_sdist(out_dir),
}))
"""


def _build_distributions(scratch: Path) -> tuple[Path, Path]:
    """Build a wheel and an sdist through the declared build requirements.

    The build environment is provisioned from ``[build-system].requires`` and the
    declared ``build-backend`` is invoked inside it, so the result does not
    depend on which build tooling the test interpreter happens to carry.
    """

    build_system = _source_build_system()
    requires = [str(item) for item in build_system["requires"]]
    backend = str(build_system["build-backend"])

    env_dir = scratch / "buildenv"
    try:
        venv.EnvBuilder(with_pip=True, clear=True).create(env_dir)
    except Exception as exc:  # pragma: no cover - venv creation is environmental
        raise BuildEnvironmentError(f"could not create a build environment: {_bounded(str(exc))}")
    python = _venv_python(env_dir)
    _run(
        [str(python), "-m", "pip", "install", "--quiet", *requires],
        f"could not provision the declared [build-system].requires {requires}",
    )

    out_dir = scratch / "dist"
    out_dir.mkdir(parents=True, exist_ok=True)
    script = scratch / "pep517_build.py"
    script.write_text(_BUILD_SCRIPT, encoding="utf-8")
    result = _run(
        [str(python), str(script), backend, str(out_dir)],
        f"the declared build backend {backend!r} failed",
        cwd=_PKG_ROOT,
    )
    try:
        names = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise BuildEnvironmentError(
            f"could not read built artifact names: {_bounded(result.stdout)} ({exc})"
        )
    return out_dir / names["wheel"], out_dir / names["sdist"]


def setUpModule() -> None:
    global _SCRATCH, _WHEEL, _SDIST, _BUILD_FAILURE
    _SCRATCH = Path(tempfile.mkdtemp(prefix="frutlups-packaging-"))
    try:
        _WHEEL, _SDIST = _build_distributions(_SCRATCH)
    except BuildEnvironmentError as exc:
        _BUILD_FAILURE = str(exc)


def tearDownModule() -> None:
    if _SCRATCH is not None:
        shutil.rmtree(_SCRATCH, ignore_errors=True)
    # Leave the source tree as it was found: both build byproducts are ignored
    # local state, never release inventory.
    shutil.rmtree(_SRC_ROOT / "frutlups.egg-info", ignore_errors=True)
    shutil.rmtree(_PKG_ROOT / "build", ignore_errors=True)


def _core_metadata(text: str) -> Message:
    return Parser().parsestr(text)


def _wheel_core_metadata() -> Message:
    assert _WHEEL is not None
    with zipfile.ZipFile(_WHEEL) as archive:
        name = min(
            (n for n in archive.namelist() if n.endswith(".dist-info/METADATA")),
            key=len,
        )
        return _core_metadata(archive.read(name).decode("utf-8"))


def _sdist_names() -> list[str]:
    assert _SDIST is not None
    with tarfile.open(_SDIST) as archive:
        return archive.getnames()


def _sdist_core_metadata() -> Message:
    assert _SDIST is not None
    with tarfile.open(_SDIST) as archive:
        member = min(
            (m for m in archive.getmembers() if m.name.endswith("/PKG-INFO")),
            key=lambda m: m.name.count("/"),
        )
        extracted = archive.extractfile(member)
        assert extracted is not None
        return _core_metadata(extracted.read().decode("utf-8"))


def _unconditional_requirements(values: list[str]) -> list[str]:
    """The requirement strings that carry no environment marker."""

    return [value for value in values if ";" not in value]


def _conditional_requirements(values: list[str]) -> list[str]:
    return [value for value in values if ";" in value]


class _BuiltArtifactTestCase(unittest.TestCase):
    """Base case: a build failure fails every dependent test, never skips it."""

    def setUp(self) -> None:
        if _BUILD_FAILURE:
            self.fail(_BUILD_FAILURE)


class PackagingTests(_BuiltArtifactTestCase):
    def test_py_typed_marker_exists_in_source_tree(self) -> None:
        self.assertTrue(
            (_SRC_ROOT / "frutlups" / "py.typed").is_file(),
            "src/frutlups/py.typed marker is missing",
        )

    def test_py_typed_packaged_in_sdist(self) -> None:
        names = _sdist_names()
        self.assertTrue(
            any(name.endswith("py.typed") for name in names),
            "py.typed was not included in the built sdist",
        )

    def test_source_declares_complete_mit_license(self) -> None:
        project = _source_project()
        self.assertEqual(project["license"], "MIT")
        self.assertEqual(project["license-files"], ["LICENSE"])
        license_text = (_PKG_ROOT / "LICENSE").read_text(encoding="utf-8")
        normalized = " ".join(license_text.split())
        self.assertTrue(license_text.startswith("MIT License\n\n"))
        self.assertIn("including without limitation the rights", normalized)
        self.assertIn("sell copies of the Software", normalized)


class DependencyDeclarationTests(unittest.TestCase):
    """The source declaration is the single dependency source of truth."""

    def test_source_declares_exactly_one_unconditional_runtime_dependency(self) -> None:
        declared = _source_project()["dependencies"]
        self.assertEqual(
            len(declared),
            1,
            f"expected exactly one runtime dependency, got {declared!r}",
        )
        try:
            observed = parse_requirement(declared[0])
        except ValueError as exc:
            self.fail(f"the declared runtime dependency is not an accepted shape: {exc}")
        self.assertEqual(
            observed,
            parse_requirement(_ACCEPTED_RUNTIME_REQUIREMENT),
            f"declared {declared[0]!r} is not semantically {_ACCEPTED_RUNTIME_REQUIREMENT!r}",
        )

    def test_dev_extra_is_unchanged_and_not_required_for_a_base_install(self) -> None:
        project = _source_project()
        extras = project["optional-dependencies"]
        self.assertEqual(set(extras), {"dev"}, "the dev extra is the only declared extra")
        self.assertEqual(
            [parse_requirement(item) for item in extras["dev"]],
            [parse_requirement("mypy>=1.8"), parse_requirement("ruff>=0.6")],
            "the dev extra contents changed",
        )
        runtime = {parse_requirement(item)[0] for item in project["dependencies"]}
        for item in extras["dev"]:
            self.assertNotIn(
                parse_requirement(item)[0],
                runtime,
                "a dev tool leaked into the unconditional runtime dependencies",
            )

    def test_requirement_comparison_rejects_everything_but_normalization(self) -> None:
        accepted = parse_requirement(_ACCEPTED_RUNTIME_REQUIREMENT)

        # Only specifier reordering and whitespace normalization may compare equal.
        for equivalent in (
            "PyYAML<7,>=6.0.3",
            "pyyaml>=6.0.3,<7",
            "  PyYAML >= 6.0.3 , < 7  ",
            "PyYAML <7 , >=6.0.3",
        ):
            with self.subTest(equivalent=equivalent):
                self.assertEqual(parse_requirement(equivalent), accepted)

        # Unparseable shapes are rejected outright rather than compared loosely.
        for rejected in (
            "PyYAML[unsafe]>=6.0.3,<7",
            "PyYAML[unsafe]",
            'PyYAML>=6.0.3,<7; extra == "dev"',
            'PyYAML>=6.0.3,<7; python_version < "3.12"',
            "PyYAML@https://example.invalid/PyYAML-6.0.3-py3-none-any.whl",
            "PyYAML @ file:///vendor/PyYAML-6.0.3.tar.gz",
            "PyYAML>=6.0.3,<7 trailing",
            "PyYAML>=6.0.3,,<7",
            "PyYAML>=",
            "PyYAML>=6.0.3,>=6.0.3",
            "",
            "   ",
        ):
            with self.subTest(rejected=rejected):
                with self.assertRaises(ValueError):
                    parse_requirement(rejected)

        # Parseable but semantically different requirements must not compare equal.
        for different in (
            "PyYAML>=6.0.3",
            "PyYAML<7",
            "PyYAML>=6.0.3,<7,!=6.0.4",
            "PyYAML>6.0.3,<7",
            "PyYAML>=6.0.2,<7",
            "PyYAML>=6.0.3,<8",
            "PyYAML",
            "PyYAML3>=6.0.3,<7",
            "ruamel.yaml>=6.0.3,<7",
        ):
            with self.subTest(different=different):
                self.assertNotEqual(parse_requirement(different), accepted)


class BuiltMetadataTests(_BuiltArtifactTestCase):
    """Built artifacts must reproduce the source declaration exactly."""

    def test_built_core_metadata_carries_the_declared_runtime_dependency(self) -> None:
        expected = {parse_requirement(item) for item in _source_project()["dependencies"]}
        for label, metadata in (("wheel", _wheel_core_metadata()), ("sdist", _sdist_core_metadata())):
            with self.subTest(artifact=label):
                values = metadata.get_all("Requires-Dist") or []
                unconditional = _unconditional_requirements(values)
                observed = set()
                for value in unconditional:
                    try:
                        observed.add(parse_requirement(value))
                    except ValueError as exc:
                        self.fail(f"{label} carries an unaccepted requirement shape: {exc}")
                self.assertEqual(observed, expected)

    def test_built_metadata_keeps_the_dev_extra_conditional(self) -> None:
        for label, metadata in (("wheel", _wheel_core_metadata()), ("sdist", _sdist_core_metadata())):
            with self.subTest(artifact=label):
                self.assertIn("dev", metadata.get_all("Provides-Extra") or [])
                values = metadata.get_all("Requires-Dist") or []
                conditional = {
                    parse_requirement(value.split(";", 1)[0])[0]
                    for value in _conditional_requirements(values)
                }
                self.assertEqual(conditional, {"mypy", "ruff"})

    def test_built_artifacts_carry_mit_expression_and_license_file(self) -> None:
        for label, metadata in (
            ("wheel", _wheel_core_metadata()),
            ("sdist", _sdist_core_metadata()),
        ):
            with self.subTest(artifact=label):
                self.assertEqual(metadata.get("License-Expression"), "MIT")
                self.assertIn("LICENSE", metadata.get_all("License-File") or [])

        assert _WHEEL is not None
        with zipfile.ZipFile(_WHEEL) as archive:
            license_names = [
                name for name in archive.namelist() if name.endswith("/licenses/LICENSE")
            ]
            self.assertEqual(len(license_names), 1)
            self.assertEqual(
                archive.read(license_names[0]).decode("utf-8"),
                (_PKG_ROOT / "LICENSE").read_text(encoding="utf-8"),
            )
        self.assertEqual(
            len([name for name in _sdist_names() if name.endswith("/LICENSE")]),
            1,
        )

    def test_wheel_contents_preserve_packaging_invariants(self) -> None:
        assert _WHEEL is not None
        with zipfile.ZipFile(_WHEEL) as archive:
            names = archive.namelist()
            entry_points = next(
                (n for n in names if n.endswith(".dist-info/entry_points.txt")), ""
            )
            entry_text = archive.read(entry_points).decode("utf-8") if entry_points else ""
        self.assertIn("frutlups/__init__.py", names)
        self.assertIn("frutlups/py.typed", names)
        # M004: the observation module ships as product code, while the pinned
        # OKF/profile fixture corpus is test evidence and never wheel content
        # (02_analysis/m004_okf_profile_observation_compatibility_record.md).
        self.assertIn("frutlups/okf_profile.py", names)
        self.assertFalse([n for n in names if "fixtures/okf_profile" in n])
        self.assertFalse(
            [n for n in names if n.startswith("template_pkg/")],
            "the scaffold placeholder package must never enter the product wheel",
        )
        self.assertIn("frutlups = frutlups.cli:main", entry_text.replace("\r\n", "\n"))


class IsolatedBaseInstallTests(_BuiltArtifactTestCase):
    """A fresh base install must resolve the declared range and stay consistent."""

    def test_isolated_base_install_resolves_pyyaml_and_passes_pip_check(self) -> None:
        assert _WHEEL is not None and _SCRATCH is not None
        env_dir = _SCRATCH / "baseenv"
        work_dir = _SCRATCH / "work"
        work_dir.mkdir(parents=True, exist_ok=True)

        venv.EnvBuilder(with_pip=True, clear=True).create(env_dir)
        python = _venv_python(env_dir)

        try:
            _run(
                [str(python), "-m", "pip", "install", "--quiet", str(_WHEEL)],
                "the built wheel could not be installed into a fresh base environment",
                cwd=work_dir,
            )
            probe = _run(
                [
                    str(python),
                    "-c",
                    "import json, frutlups, yaml\n"
                    "from importlib import metadata\n"
                    "print(json.dumps({\n"
                    "    'frutlups_version': metadata.version('frutlups'),\n"
                    "    'module_version': frutlups.__version__,\n"
                    "    'pyyaml_version': metadata.version('PyYAML'),\n"
                    "    'frutlups_file': frutlups.__file__,\n"
                    "    'yaml_file': yaml.__file__,\n"
                    "    'requires': metadata.requires('frutlups'),\n"
                    "}))",
                ],
                "the installed product could not import frutlups and yaml",
                cwd=work_dir,
            )
            observed = json.loads(probe.stdout)

            # The installed distribution reproduces the source declaration.
            expected = {parse_requirement(item) for item in _source_project()["dependencies"]}
            installed = {
                parse_requirement(value)
                for value in _unconditional_requirements(observed["requires"] or [])
            }
            self.assertEqual(installed, expected)

            # The resolved PyYAML version lies inside the declared range.
            version = tuple(
                int(part) for part in re.findall(r"\d+", observed["pyyaml_version"])[:3]
            )
            self.assertGreaterEqual(version, (6, 0, 3), observed["pyyaml_version"])
            self.assertLess(version, (7,), observed["pyyaml_version"])

            # One truthful release identity (M007-R1-F1): the source project
            # version, the installed distribution metadata, and the installed
            # public module attribute all equal 0.2.1 — no surface may diverge.
            self.assertEqual(_source_project()["version"], "0.2.1")
            self.assertEqual(observed["frutlups_version"], "0.2.1")
            self.assertEqual(observed["module_version"], "0.2.1")

            # Both imports resolve inside the isolated environment, never from the
            # package source tree, and the probe ran outside the package workspace.
            for key in ("frutlups_file", "yaml_file"):
                imported = Path(observed[key]).resolve()
                self.assertTrue(imported.is_relative_to(env_dir.resolve()), observed[key])
                self.assertFalse(imported.is_relative_to(_SRC_ROOT), observed[key])
            self.assertFalse(work_dir.resolve().is_relative_to(_PKG_ROOT))

            _run(
                [str(python), "-m", "pip", "check"],
                "pip check reported broken requirements after a fresh base install",
                cwd=work_dir,
            )

            # The base install did not pull the dev extra.
            listing = _run(
                [str(python), "-m", "pip", "list", "--format=freeze"],
                "could not list the fresh base environment",
                cwd=work_dir,
            )
            installed_names = {
                _canonical_name(line.split("==", 1)[0])
                for line in listing.stdout.splitlines()
                if "==" in line
            }
            self.assertNotIn("mypy", installed_names)
            self.assertNotIn("ruff", installed_names)
        except BuildEnvironmentError as exc:
            self.fail(str(exc))
        finally:
            shutil.rmtree(env_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
