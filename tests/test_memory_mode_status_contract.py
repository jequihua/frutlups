"""Released version-1 memory declaration status contract."""

from __future__ import annotations

import inspect
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import frutlups
import frutlups.project as project_module
from frutlups import (
    MEMORY_MODE_CONTRACT_ID,
    MEMORY_MODE_CONTRACT_VERSION,
    MEMORY_MODE_SUPPORTED_VERSIONS,
    MemoryModeStatus,
    build_memory_mode_status,
    build_status,
)
from tests.test_memory_lane_contract import (
    _RaisingRunner,
    _fs_snapshot,
    _make_legacy_project,
    _make_v2_project,
    _run_cli,
)


_CONTRACT_KEYS = {
    "contract_id",
    "contract_version",
    "valid",
    "mode",
    "memory_root",
    "diagnostics",
}


def _write_state(root: Path, body: str) -> None:
    (root / "PROJECT_STATE.md").write_text(body, encoding="utf-8", newline="\n")


class PublicContractTests(unittest.TestCase):
    def test_identity_and_version_are_frozen(self) -> None:
        self.assertEqual(MEMORY_MODE_CONTRACT_ID, "frutlups.memory_mode")
        self.assertEqual(MEMORY_MODE_CONTRACT_VERSION, "1")
        self.assertEqual(MEMORY_MODE_SUPPORTED_VERSIONS, ("1",))

    def test_five_contract_names_are_exported(self) -> None:
        names = {
            "MEMORY_MODE_CONTRACT_ID",
            "MEMORY_MODE_CONTRACT_VERSION",
            "MEMORY_MODE_SUPPORTED_VERSIONS",
            "MemoryModeStatus",
            "build_memory_mode_status",
        }
        self.assertTrue(names <= set(frutlups.__all__))
        self.assertEqual(len(frutlups.__all__), 152)
        self.assertEqual(len(set(frutlups.__all__)), 152)

    def test_release_version_is_0_1_2(self) -> None:
        self.assertEqual(frutlups.__version__, "0.1.5")

    def test_dataclass_shape_is_exact(self) -> None:
        value = MemoryModeStatus(
            contract_id="frutlups.memory_mode",
            contract_version="1",
            valid=True,
            mode="none",
            memory_root=None,
            diagnostics=(),
        )
        self.assertEqual(set(value.to_dict()), _CONTRACT_KEYS)
        json.dumps(value.to_dict())

    def test_project_status_constructor_keeps_memory_mode_optional(self) -> None:
        parameter = inspect.signature(project_module.ProjectStatus).parameters["memory_mode"]
        self.assertIsNot(parameter.default, inspect.Parameter.empty)


class DeclarationModeTests(unittest.TestCase):
    def _observe(
        self,
        mode: str | None,
        *,
        memory_root_config: str | None = None,
    ) -> dict[str, object]:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(
                root,
                mode,
                scaffolds=False,
                memory_root_config=memory_root_config,
            )
            return build_memory_mode_status(root).to_dict()

    def test_none_declaration(self) -> None:
        self.assertEqual(
            self._observe("none"),
            {
                "contract_id": "frutlups.memory_mode",
                "contract_version": "1",
                "valid": True,
                "mode": "none",
                "memory_root": None,
                "diagnostics": [],
            },
        )

    def test_lightweight_declaration(self) -> None:
        value = self._observe("lightweight")
        self.assertTrue(value["valid"])
        self.assertEqual(value["mode"], "lightweight")
        self.assertIsNone(value["memory_root"])

    def test_llloom_declaration_carries_safe_relative_root(self) -> None:
        value = self._observe("llloom", memory_root_config="memory/store")
        self.assertTrue(value["valid"])
        self.assertEqual(value["mode"], "llloom")
        self.assertEqual(value["memory_root"], "memory/store")

    def test_llloom_root_is_declaration_not_availability(self) -> None:
        value = self._observe("llloom")
        self.assertTrue(value["valid"])
        self.assertEqual(value["memory_root"], "llloom_memory")

    def test_missing_mode_line_defaults_to_none(self) -> None:
        self.assertEqual(self._observe(None)["mode"], "none")

    def test_missing_state_file_defaults_to_none(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "none", scaffolds=False)
            (root / "PROJECT_STATE.md").unlink()
            value = build_memory_mode_status(root)
        self.assertTrue(value.valid)
        self.assertEqual(value.mode, "none")

    def test_legacy_project_defaults_to_none_without_memory_probe(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_legacy_project(root, make_memory_root=True)
            with patch(
                "frutlups.project.observe_llloom_memory_root",
                side_effect=AssertionError("backend probe reached"),
            ):
                value = build_memory_mode_status(root)
        self.assertTrue(value.valid)
        self.assertEqual(value.mode, "none")

    def test_list_form_declaration(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, None, scaffolds=False)
            _write_state(root, "# Project State\n\nMemory mode:\n- lightweight\n")
            value = build_memory_mode_status(root)
        self.assertTrue(value.valid)
        self.assertEqual(value.mode, "lightweight")

    def test_layout_configured_memory_label_is_authoritative(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, None, scaffolds=False)
            (root / "frutlups.layout.yaml").write_text(
                "schema_version: frutlups_layout_config_v0\n"
                "profile_id: artifact_first_template_v2\n"
                "state:\n"
                "  canonical_file: PROJECT_STATE.md\n"
                "  mode_fields:\n"
                "    memory:\n"
                "      label: Recall mode\n"
                "      allowed_values:\n"
                "        - none\n"
                "        - lightweight\n"
                "        - llloom\n",
                encoding="utf-8",
                newline="\n",
            )
            _write_state(root, "Recall mode: llloom\n")
            value = build_memory_mode_status(root)
        self.assertTrue(value.valid)
        self.assertEqual(value.mode, "llloom")
        self.assertEqual(value.memory_root, "llloom_memory")


class FailClosedDeclarationTests(unittest.TestCase):
    def _project(self, root: Path, body: str) -> None:
        _make_v2_project(root, None, scaffolds=False)
        _write_state(root, body)

    def test_unknown_mode_is_invalid(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root, "Memory mode: enabled\n")
            value = build_memory_mode_status(root)
        self.assertEqual(value.to_dict()["diagnostics"], ["invalid_memory_mode"])
        self.assertFalse(value.valid)
        self.assertIsNone(value.mode)

    def test_empty_present_mode_is_invalid(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root, "Memory mode:\nFrutlups mode: manual\n")
            value = build_memory_mode_status(root)
        self.assertEqual(value.diagnostics, ("invalid_memory_mode",))

    def test_duplicate_different_modes_are_invalid(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, None, scaffolds=False, make_root=True)
            _write_state(root, "Memory mode: llloom\nMemory mode: llloom\n")
            status = build_status(root, memory_runner=_RaisingRunner())
        self.assertEqual(status.memory_mode.diagnostics, ("duplicate_memory_mode",))
        self.assertFalse(status.memory.enabled)
        self.assertIn("layout_state_mode_duplicate", {d.code for d in status.diagnostics})

    def test_duplicate_identical_modes_are_invalid(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root, "Memory mode: none\nMemory mode: none\n")
            value = build_memory_mode_status(root)
        self.assertEqual(value.diagnostics, ("duplicate_memory_mode",))

    def test_invalid_utf8_state_is_owned_invalid(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, None, scaffolds=False)
            (root / "PROJECT_STATE.md").write_bytes(b"Memory mode: \xff\n")
            value = build_memory_mode_status(root)
        self.assertEqual(value.diagnostics, ("state_unreadable",))

    def test_state_directory_is_owned_invalid(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, None, scaffolds=False)
            (root / "PROJECT_STATE.md").unlink()
            (root / "PROJECT_STATE.md").mkdir()
            value = build_memory_mode_status(root)
        self.assertEqual(value.diagnostics, ("state_unreadable",))

    def test_oversized_state_is_owned_invalid(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, None, scaffolds=False)
            (root / "PROJECT_STATE.md").write_bytes(b"x" * 262_145)
            value = build_memory_mode_status(root)
        self.assertEqual(value.diagnostics, ("state_unreadable",))

    def test_state_at_byte_limit_is_accepted(self) -> None:
        prefix = b"Memory mode: none\n"
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, None, scaffolds=False)
            (root / "PROJECT_STATE.md").write_bytes(
                prefix + (b" " * (262_144 - len(prefix)))
            )
            value = build_memory_mode_status(root)
        self.assertTrue(value.valid)
        self.assertEqual(value.mode, "none")

    def test_escaping_state_path_never_reads_external_declaration(self) -> None:
        secret = "llloom"
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "project"
            _make_v2_project(root, None, scaffolds=False)
            (base / "outside_state.md").write_text(
                f"Memory mode: {secret}\n", encoding="utf-8", newline="\n"
            )
            (root / "frutlups.layout.yaml").write_text(
                "schema_version: frutlups_layout_config_v0\n"
                "profile_id: artifact_first_template_v2\n"
                "state:\n"
                "  canonical_file: ../outside_state.md\n",
                encoding="utf-8",
                newline="\n",
            )
            payload = build_memory_mode_status(root).to_dict()
        self.assertFalse(payload["valid"])
        self.assertIsNone(payload["mode"])
        self.assertEqual(payload["diagnostics"], ["state_unreadable"])

    def test_unreadable_layout_is_owned_invalid(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "none", scaffolds=False)
            (root / "frutlups.layout.yaml").write_text("[", encoding="utf-8")
            value = build_memory_mode_status(root)
        self.assertEqual(value.diagnostics, ("layout_unreadable",))

    def test_unsafe_llloom_root_is_invalid(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(
                root,
                "llloom",
                scaffolds=False,
                memory_root_config="'../outside'",
            )
            value = build_memory_mode_status(root)
        self.assertEqual(value.diagnostics, ("invalid_memory_root",))

    def test_irrelevant_unsafe_root_does_not_invalidate_none(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(
                root,
                "none",
                scaffolds=False,
                memory_root_config="'../outside'",
            )
            value = build_memory_mode_status(root)
        self.assertTrue(value.valid)
        self.assertEqual(value.mode, "none")

    def test_hostile_value_is_never_echoed(self) -> None:
        hostile = "SECRET_TOKEN_VALUE"
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root, f"Memory mode: {hostile}\n")
            payload = json.dumps(build_memory_mode_status(root).to_dict())
        self.assertNotIn(hostile, payload)
        self.assertLessEqual(max(map(len, json.loads(payload)["diagnostics"])), 240)


class StatusCompositionTests(unittest.TestCase):
    def test_status_object_and_json_carry_same_fact(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "lightweight", scaffolds=False)
            status = build_status(root, memory_runner=_RaisingRunner())
            payload = status.to_dict()
        self.assertIsInstance(status.memory_mode, MemoryModeStatus)
        self.assertEqual(payload["memory_mode"], status.memory_mode.to_dict())

    def test_status_cli_json_adds_exact_sibling_without_changing_frontier(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "none", scaffolds=False)
            code, out, _err = _run_cli(["status", str(root), "--json"])
            payload = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(set(payload["memory_mode"]), _CONTRACT_KEYS)
        self.assertEqual(payload["planning_frontier"]["contract_id"], "frutlups.planning_frontier")
        self.assertEqual(payload["planning_frontier"]["contract_version"], "1")
        self.assertIn("loop_resume", payload)

    def test_cli_invalid_declaration_returns_parseable_refusal_fact(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "bogus", scaffolds=False)
            code, out, _err = _run_cli(["status", str(root), "--json"])
            payload = json.loads(out)
        self.assertEqual(code, 0)
        self.assertFalse(payload["memory_mode"]["valid"])
        self.assertIsNone(payload["memory_mode"]["mode"])

    def test_builder_never_queries_or_observes_backend(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "llloom", scaffolds=False, make_root=True)
            with (
                patch(
                    "frutlups.project.observe_llloom_memory_root",
                    side_effect=AssertionError("availability probe reached"),
                ),
                patch(
                    "frutlups.project.detect_memory",
                    side_effect=AssertionError("legacy probe reached"),
                ),
            ):
                value = build_memory_mode_status(root)
        self.assertTrue(value.valid)
        self.assertEqual(value.mode, "llloom")

    def test_status_reads_state_file_once_for_health_and_declaration(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "none", scaffolds=False)
            real = project_module._read_state_file_once
            calls = 0

            def counted(*args, **kwargs):
                nonlocal calls
                calls += 1
                return real(*args, **kwargs)

            with patch(
                "frutlups.project._read_state_file_once", side_effect=counted
            ):
                status = build_status(root, memory_runner=_RaisingRunner())
        self.assertEqual(calls, 1)
        self.assertEqual(status.memory_mode.mode, "none")

    def test_builder_is_read_only(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "lightweight", scaffolds=False)
            before = _fs_snapshot(root)
            first = build_memory_mode_status(root)
            second = build_memory_mode_status(root)
            after = _fs_snapshot(root)
        self.assertEqual(first, second)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
