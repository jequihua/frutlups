"""Tests for M009-S01: MemoryBackend protocol and disabled backend.

Covers:
- MemoryStatus.to_dict() emits only JSON-safe plain values
- MemoryStatus is immutable
- DisabledMemoryBackend satisfies the disabled contract
- DisabledMemoryBackend satisfies MemoryBackend protocol structurally
- detect_memory() returns disabled status when no memory root exists
- detect_memory() detects an existing memory root without running commands
- build_status() includes disabled memory in ProjectStatus for projects without memory
- CLI human output includes "Memory: disabled"
"""

from __future__ import annotations

import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from frutlups.cli import main
from frutlups.memory import (
    DisabledMemoryBackend,
    MemoryBackend,
    MemoryStatus,
    detect_memory,
)
from frutlups.project import build_status


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_template(root: Path) -> None:
    for name in (
        "00_brief",
        "03_experiments",
        "05_governance",
        "06_infra",
        "08_pkg",
        "prompts/for_coding_agent",
        "prompts/for_review_agent",
    ):
        (root / name).mkdir(parents=True, exist_ok=True)


def _write_active_roadmap(root: Path) -> None:
    (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
        "# Active Roadmap\n\n### M001: Scaffold\n\nStatus: active\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# MemoryStatus.to_dict() emits JSON-safe values
# ---------------------------------------------------------------------------

class MemoryStatusToDictTests(unittest.TestCase):
    def _disabled_dict(self) -> dict:
        return DisabledMemoryBackend().status().to_dict()

    def test_enabled_is_bool(self) -> None:
        self.assertIsInstance(self._disabled_dict()["enabled"], bool)

    def test_backend_is_str(self) -> None:
        self.assertIsInstance(self._disabled_dict()["backend"], str)

    def test_message_is_str(self) -> None:
        self.assertIsInstance(self._disabled_dict()["message"], str)

    def test_root_none_maps_to_none(self) -> None:
        self.assertIsNone(self._disabled_dict()["root"])

    def test_root_path_maps_to_str(self) -> None:
        status = MemoryStatus(
            enabled=True,
            backend="llloom",
            root=Path("/some/path"),
            message="present",
        )
        d = status.to_dict()
        self.assertIsInstance(d["root"], str)
        self.assertNotIsInstance(d["root"], Path)

    def test_no_path_objects_in_values(self) -> None:
        status = MemoryStatus(
            enabled=True,
            backend="llloom",
            root=Path("/some/path"),
            message="present",
        )
        for v in status.to_dict().values():
            self.assertNotIsInstance(v, Path)

    def test_to_dict_is_json_serializable(self) -> None:
        d = DisabledMemoryBackend().status().to_dict()
        serialized = json.dumps(d)
        self.assertIsInstance(serialized, str)


# ---------------------------------------------------------------------------
# MemoryStatus is immutable
# ---------------------------------------------------------------------------

class MemoryStatusFrozenTests(unittest.TestCase):
    def test_is_frozen(self) -> None:
        status = MemoryStatus(enabled=False, backend="disabled")
        with self.assertRaises((AttributeError, TypeError)):
            status.enabled = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# DisabledMemoryBackend
# ---------------------------------------------------------------------------

class DisabledMemoryBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = DisabledMemoryBackend()
        self.status = self.backend.status()

    def test_status_enabled_is_false(self) -> None:
        self.assertFalse(self.status.enabled)

    def test_status_backend_name_is_disabled(self) -> None:
        self.assertEqual(self.status.backend, "disabled")

    def test_status_root_is_none(self) -> None:
        self.assertIsNone(self.status.root)

    def test_status_message_is_non_empty(self) -> None:
        self.assertTrue(self.status.message)

    def test_backend_is_frozen(self) -> None:
        with self.assertRaises((AttributeError, TypeError)):
            self.backend.reason = "mutated"  # type: ignore[misc]

    def test_backend_satisfies_protocol(self) -> None:
        self.assertIsInstance(self.backend, MemoryBackend)

    def test_custom_reason_propagates_to_message(self) -> None:
        backend = DisabledMemoryBackend(reason="no workspace configured")
        self.assertEqual(backend.status().message, "no workspace configured")


# ---------------------------------------------------------------------------
# detect_memory()
# ---------------------------------------------------------------------------

class DetectMemoryNoRootTests(unittest.TestCase):
    """detect_memory() returns disabled status when no memory root exists."""

    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_returns_disabled_when_no_memory_dir(self) -> None:
        status = detect_memory(self.root)
        self.assertFalse(status.enabled)

    def test_disabled_backend_name_is_disabled(self) -> None:
        status = detect_memory(self.root)
        self.assertEqual(status.backend, "disabled")

    def test_disabled_root_is_none(self) -> None:
        status = detect_memory(self.root)
        self.assertIsNone(status.root)

    def test_does_not_create_files(self) -> None:
        before = set(self.root.iterdir())
        detect_memory(self.root)
        after = set(self.root.iterdir())
        self.assertEqual(before, after)

    def test_result_is_json_safe(self) -> None:
        d = detect_memory(self.root).to_dict()
        json.dumps(d)


class DetectMemoryWithRootTests(unittest.TestCase):
    """detect_memory() detects an existing memory root (presence only)."""

    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        self.memory_dir = self.root / "07_app" / "llloom_memory"
        self.memory_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_returns_enabled_when_memory_dir_present(self) -> None:
        status = detect_memory(self.root)
        self.assertTrue(status.enabled)

    def test_backend_name_is_llloom(self) -> None:
        status = detect_memory(self.root)
        self.assertEqual(status.backend, "llloom")

    def test_root_points_to_memory_dir(self) -> None:
        status = detect_memory(self.root)
        self.assertEqual(status.root, self.memory_dir)

    def test_does_not_mutate_memory_dir(self) -> None:
        before = set(self.memory_dir.iterdir())
        detect_memory(self.root)
        after = set(self.memory_dir.iterdir())
        self.assertEqual(before, after)


# ---------------------------------------------------------------------------
# ProjectStatus includes memory
# ---------------------------------------------------------------------------

class ProjectStatusMemoryTests(unittest.TestCase):
    """build_status() reports disabled memory for projects without memory root."""

    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_roadmap(self.root)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_memory_enabled_is_false(self) -> None:
        status = build_status(self.root)
        self.assertFalse(status.memory.enabled)

    def test_memory_backend_is_disabled(self) -> None:
        status = build_status(self.root)
        self.assertEqual(status.memory.backend, "disabled")

    def test_status_json_has_memory_key(self) -> None:
        status = build_status(self.root)
        d = status.to_dict()
        self.assertIn("memory", d)

    def test_status_json_memory_enabled_false(self) -> None:
        status = build_status(self.root)
        d = status.to_dict()
        self.assertFalse(d["memory"]["enabled"])

    def test_status_json_memory_backend_is_disabled_string(self) -> None:
        status = build_status(self.root)
        d = status.to_dict()
        self.assertEqual(d["memory"]["backend"], "disabled")

    def test_status_json_memory_is_json_serializable(self) -> None:
        status = build_status(self.root)
        d = status.to_dict()
        json.dumps(d["memory"])


# ---------------------------------------------------------------------------
# CLI human output contains Memory: disabled
# ---------------------------------------------------------------------------

class CliHumanMemoryOutputTests(unittest.TestCase):
    """CLI status and next human output includes 'Memory: disabled'."""

    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_roadmap(self.root)

    def tearDown(self) -> None:
        self._td.cleanup()

    def _run(self, args: list[str]) -> tuple[int, str]:
        buf = StringIO()
        with redirect_stdout(buf):
            code = main(args)
        return code, buf.getvalue()

    def test_status_output_contains_memory_disabled(self) -> None:
        _, output = self._run(["status", str(self.root)])
        self.assertIn("Memory: disabled", output)

    def test_status_json_memory_disabled(self) -> None:
        _, output = self._run(["status", str(self.root), "--json"])
        d = json.loads(output)
        self.assertEqual(d["memory"]["backend"], "disabled")
        self.assertFalse(d["memory"]["enabled"])

    def test_next_output_contains_memory(self) -> None:
        _, output = self._run(["next", str(self.root)])
        self.assertIn("Memory:", output)


if __name__ == "__main__":
    unittest.main()
