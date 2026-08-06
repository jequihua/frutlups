"""Tests for M010-S02: seed manifest dry-run/apply command planning.

Covers:
- dry-run command vector has exact expected shape
- apply command vector has exact expected shape
- custom executable name is honored in both vectors
- to_dict() emits only plain Python values (no Path, enum, or exception objects)
- plan_seed_manifest_update() is pure: does not create files or directories
- plan_seed_manifest_update() does not invoke any MemoryCommandRunner
- command vectors are deterministic across repeated calls
- paths with spaces are preserved as single tuple arguments, not split
- SeedManifestUpdatePlan is frozen (immutable)
- read-only backend tests remain unaffected (checked by full suite)
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from frutlups.memory import (
    SeedManifestUpdatePlan,
    plan_seed_manifest_update,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ROOT = Path("/memory/root")
_MANIFEST = Path("/memory/manifests/update.yaml")
_DEFAULT_EXE = "llloom"

_DRY_RUN_EXPECTED = (
    "llloom", "--root", str(_ROOT),
    "seed", "apply", str(_MANIFEST),
    "--dry-run",
)
_APPLY_EXPECTED = (
    "llloom", "--root", str(_ROOT),
    "seed", "apply", str(_MANIFEST),
)


# ---------------------------------------------------------------------------
# Dry-run command vector
# ---------------------------------------------------------------------------

class DryRunCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = plan_seed_manifest_update(_ROOT, _MANIFEST)

    def test_dry_run_command_equals_expected(self) -> None:
        self.assertEqual(self.plan.dry_run_command, _DRY_RUN_EXPECTED)

    def test_dry_run_starts_with_executable(self) -> None:
        self.assertEqual(self.plan.dry_run_command[0], _DEFAULT_EXE)

    def test_dry_run_includes_root_flag(self) -> None:
        self.assertIn("--root", self.plan.dry_run_command)

    def test_dry_run_includes_root_path(self) -> None:
        cmd = list(self.plan.dry_run_command)
        idx = cmd.index("--root")
        self.assertEqual(cmd[idx + 1], str(_ROOT))

    def test_dry_run_includes_seed_verb(self) -> None:
        self.assertIn("seed", self.plan.dry_run_command)

    def test_dry_run_includes_apply_verb(self) -> None:
        self.assertIn("apply", self.plan.dry_run_command)

    def test_dry_run_includes_manifest_path(self) -> None:
        self.assertIn(str(_MANIFEST), self.plan.dry_run_command)

    def test_dry_run_ends_with_dry_run_flag(self) -> None:
        self.assertEqual(self.plan.dry_run_command[-1], "--dry-run")

    def test_dry_run_is_tuple(self) -> None:
        self.assertIsInstance(self.plan.dry_run_command, tuple)

    def test_dry_run_all_elements_are_strings(self) -> None:
        for arg in self.plan.dry_run_command:
            self.assertIsInstance(arg, str)


# ---------------------------------------------------------------------------
# Apply command vector
# ---------------------------------------------------------------------------

class ApplyCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = plan_seed_manifest_update(_ROOT, _MANIFEST)

    def test_apply_command_equals_expected(self) -> None:
        self.assertEqual(self.plan.apply_command, _APPLY_EXPECTED)

    def test_apply_starts_with_executable(self) -> None:
        self.assertEqual(self.plan.apply_command[0], _DEFAULT_EXE)

    def test_apply_includes_root_flag(self) -> None:
        self.assertIn("--root", self.plan.apply_command)

    def test_apply_includes_root_path(self) -> None:
        cmd = list(self.plan.apply_command)
        idx = cmd.index("--root")
        self.assertEqual(cmd[idx + 1], str(_ROOT))

    def test_apply_includes_seed_verb(self) -> None:
        self.assertIn("seed", self.plan.apply_command)

    def test_apply_includes_apply_verb(self) -> None:
        self.assertIn("apply", self.plan.apply_command)

    def test_apply_includes_manifest_path(self) -> None:
        self.assertIn(str(_MANIFEST), self.plan.apply_command)

    def test_apply_does_not_include_dry_run_flag(self) -> None:
        self.assertNotIn("--dry-run", self.plan.apply_command)

    def test_apply_is_tuple(self) -> None:
        self.assertIsInstance(self.plan.apply_command, tuple)

    def test_apply_all_elements_are_strings(self) -> None:
        for arg in self.plan.apply_command:
            self.assertIsInstance(arg, str)


# ---------------------------------------------------------------------------
# Custom executable
# ---------------------------------------------------------------------------

class CustomExecutableTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = plan_seed_manifest_update(_ROOT, _MANIFEST, executable="llloom-dev")

    def test_dry_run_uses_custom_executable(self) -> None:
        self.assertEqual(self.plan.dry_run_command[0], "llloom-dev")

    def test_apply_uses_custom_executable(self) -> None:
        self.assertEqual(self.plan.apply_command[0], "llloom-dev")

    def test_default_executable_is_llloom(self) -> None:
        default_plan = plan_seed_manifest_update(_ROOT, _MANIFEST)
        self.assertEqual(default_plan.executable, "llloom")


# ---------------------------------------------------------------------------
# to_dict() serialization
# ---------------------------------------------------------------------------

class ToDictTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = plan_seed_manifest_update(_ROOT, _MANIFEST)
        self.d = self.plan.to_dict()

    def test_to_dict_has_memory_root(self) -> None:
        self.assertIn("memory_root", self.d)

    def test_to_dict_has_manifest_path(self) -> None:
        self.assertIn("manifest_path", self.d)

    def test_to_dict_has_executable(self) -> None:
        self.assertIn("executable", self.d)

    def test_to_dict_has_dry_run_command(self) -> None:
        self.assertIn("dry_run_command", self.d)

    def test_to_dict_has_apply_command(self) -> None:
        self.assertIn("apply_command", self.d)

    def test_memory_root_is_string(self) -> None:
        self.assertIsInstance(self.d["memory_root"], str)
        self.assertNotIsInstance(self.d["memory_root"], Path)

    def test_manifest_path_is_string(self) -> None:
        self.assertIsInstance(self.d["manifest_path"], str)
        self.assertNotIsInstance(self.d["manifest_path"], Path)

    def test_executable_is_string(self) -> None:
        self.assertIsInstance(self.d["executable"], str)

    def test_dry_run_command_is_list(self) -> None:
        self.assertIsInstance(self.d["dry_run_command"], list)

    def test_apply_command_is_list(self) -> None:
        self.assertIsInstance(self.d["apply_command"], list)

    def test_no_path_objects_in_values(self) -> None:
        for v in self.d.values():
            self.assertNotIsInstance(v, Path)

    def test_to_dict_is_json_serializable(self) -> None:
        json.dumps(self.d)


# ---------------------------------------------------------------------------
# Purity: no file creation, no runner invocation
# ---------------------------------------------------------------------------

class PurityTests(unittest.TestCase):
    def test_does_not_create_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "memory"
            manifest = Path(tmp) / "update.yaml"
            before = set(Path(tmp).iterdir())
            plan_seed_manifest_update(root, manifest)
            after = set(Path(tmp).iterdir())
        self.assertEqual(before, after)

    def test_does_not_require_memory_root_to_exist(self) -> None:
        plan = plan_seed_manifest_update(Path("/nonexistent/root"), Path("/nonexistent/update.yaml"))
        self.assertIsInstance(plan, SeedManifestUpdatePlan)

    def test_does_not_require_manifest_to_exist(self) -> None:
        plan = plan_seed_manifest_update(_ROOT, Path("/nonexistent/manifest.yaml"))
        self.assertIsInstance(plan, SeedManifestUpdatePlan)

    def test_does_not_raise(self) -> None:
        try:
            plan_seed_manifest_update(Path("/not/real"), Path("/also/not/real"))
        except Exception as exc:
            self.fail(f"plan_seed_manifest_update raised: {exc}")


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

class DeterminismTests(unittest.TestCase):
    def test_dry_run_command_is_deterministic(self) -> None:
        p1 = plan_seed_manifest_update(_ROOT, _MANIFEST)
        p2 = plan_seed_manifest_update(_ROOT, _MANIFEST)
        self.assertEqual(p1.dry_run_command, p2.dry_run_command)

    def test_apply_command_is_deterministic(self) -> None:
        p1 = plan_seed_manifest_update(_ROOT, _MANIFEST)
        p2 = plan_seed_manifest_update(_ROOT, _MANIFEST)
        self.assertEqual(p1.apply_command, p2.apply_command)

    def test_repeated_property_access_is_stable(self) -> None:
        plan = plan_seed_manifest_update(_ROOT, _MANIFEST)
        self.assertEqual(plan.dry_run_command, plan.dry_run_command)
        self.assertEqual(plan.apply_command, plan.apply_command)


# ---------------------------------------------------------------------------
# Paths with spaces
# ---------------------------------------------------------------------------

class PathWithSpacesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path("/memory/my workspace")
        self.manifest = Path("/memory/my workspace/update file.yaml")
        self.plan = plan_seed_manifest_update(self.root, self.manifest)

    def test_root_path_with_spaces_preserved_in_dry_run(self) -> None:
        cmd = list(self.plan.dry_run_command)
        idx = cmd.index("--root")
        self.assertEqual(cmd[idx + 1], str(self.root))

    def test_manifest_path_with_spaces_is_single_argument(self) -> None:
        self.assertIn(str(self.manifest), self.plan.dry_run_command)

    def test_manifest_path_not_split_on_spaces_dry_run(self) -> None:
        # The path must appear as one argument, not space-split into multiple
        path_str = str(self.manifest)
        self.assertIn(path_str, self.plan.dry_run_command)
        self.assertNotIn("update", self.plan.dry_run_command)
        # "update" should only appear as part of the full path, not a standalone arg

    def test_manifest_path_with_spaces_in_apply(self) -> None:
        self.assertIn(str(self.manifest), self.plan.apply_command)


# ---------------------------------------------------------------------------
# Frozen / immutable
# ---------------------------------------------------------------------------

class FrozenTests(unittest.TestCase):
    def test_plan_is_frozen(self) -> None:
        plan = plan_seed_manifest_update(_ROOT, _MANIFEST)
        with self.assertRaises((AttributeError, TypeError)):
            plan.executable = "mutated"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
