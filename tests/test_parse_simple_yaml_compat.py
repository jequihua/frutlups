"""Tests for M002-S05: the `parse_simple_yaml` compatibility wrapper.

The exported name is a thin documented wrapper over the one private bounded
YAML boundary plus the private layout schema. These tests pin the wrapper
contract: public surface, wrapper/file-loader parity, deliberate native
scalar semantics, deterministic bounded refusals, single boundary call, and
the source-level one-engine audit. Installed-wheel missing/restored
dependency evidence is produced by the verification lane, not durably here.
"""

from __future__ import annotations

import ast
import inspect
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import yaml

import frutlups
import frutlups.layout as layout_module
from frutlups.layout import (
    LayoutConfigError,
    load_config_file,
    parse_simple_yaml,
    profile_from_config,
)

_SRC_DIR = Path(__file__).resolve().parents[1] / "src" / "frutlups"
# The accepted template-v3 layout ships as an immutable, package-relative fixture so
# this parity check runs from the flattened front-facing checkout without reading a
# root layout above ``tests/``. See ``fixtures/front_repo_contract/manifest.json``.
_TARGET_V3_CONFIG = (
    Path(__file__).resolve().parent / "fixtures" / "front_repo_contract" / "frutlups.layout.yaml"
)

_V2_LIKE = """\
schema_version: frutlups_layout_config_v0
profile_id: artifact_first_template_v2
workspace_map:
  required_for_base_profile:
    - "00_brief"
    - "questions"
state:
  canonical_file: "PROJECT_STATE.md"
"""

_LEGACY_LIKE = """\
schema_version: frutlups_layout_config_v0
profile_id: artifact_first_template_legacy_root
prompts:
  required_coding_prompt_sections:
    - "Active Roadmap Item"
    - "Required Self-Report"
"""


class PublicSurfaceTests(unittest.TestCase):
    def test_exported_with_unchanged_signature_and_inventory(self) -> None:
        self.assertTrue(callable(frutlups.parse_simple_yaml))
        self.assertIs(frutlups.parse_simple_yaml, parse_simple_yaml)
        signature = inspect.signature(parse_simple_yaml)
        self.assertEqual(list(signature.parameters), ["text"])
        self.assertEqual(len(frutlups.__all__), 152)

    def test_old_engine_helpers_are_gone(self) -> None:
        for name in ("_strip_inline_comment", "_scalar", "parse_block"):
            self.assertFalse(hasattr(layout_module, name), name)


class WrapperLoaderParityTests(unittest.TestCase):
    def _assert_parity(self, text: str) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "frutlups.layout.yaml"
            path.write_text(text, encoding="utf-8")
            via_file = load_config_file(path)
        via_wrapper = parse_simple_yaml(text)
        self.assertEqual(via_wrapper, via_file)

    def test_mappings_sequences_quotes_comments_booleans_nulls(self) -> None:
        self._assert_parity(
            "# header\n"
            "a: 1\n"
            "b:\n"
            "  c: hello  # trailing\n"
            "  d:\n"
            '    - "quoted"\n'
            "    - plain\n"
            "  e: true\n"
            "  f: null\n"
        )

    def test_folded_and_literal_blocks(self) -> None:
        self._assert_parity("note: >\n  one\n  two\nlit: |\n  x\n  y\n")

    def test_unknown_block_form_fields(self) -> None:
        self._assert_parity(
            "schema_version: frutlups_layout_config_v0\n"
            "unknown:\n"
            "  nested:\n"
            "    flag: false\n"
            "    nothing: ~\n"
        )

    def test_accepted_v2_and_legacy_profiles_match_loader(self) -> None:
        for text in (_V2_LIKE, _LEGACY_LIKE):
            with TemporaryDirectory() as tmp:
                path = Path(tmp) / "frutlups.layout.yaml"
                path.write_text(text, encoding="utf-8")
                via_file, file_diags = profile_from_config(load_config_file(path))
            via_wrapper, wrapper_diags = profile_from_config(parse_simple_yaml(text))
            self.assertEqual(via_wrapper.to_dict(), via_file.to_dict())
            self.assertEqual(
                [d.to_dict() for d in wrapper_diags], [d.to_dict() for d in file_diags]
            )

    @unittest.skipUnless(_TARGET_V3_CONFIG.is_file(), "shipped target config not present")
    def test_shipped_template_v3_profile_matches_loader(self) -> None:
        text = _TARGET_V3_CONFIG.read_text(encoding="utf-8")
        via_wrapper, wrapper_diags = profile_from_config(parse_simple_yaml(text))
        via_file, file_diags = profile_from_config(load_config_file(_TARGET_V3_CONFIG))
        self.assertEqual(via_wrapper.to_dict(), via_file.to_dict())
        self.assertEqual([d.to_dict() for d in wrapper_diags], [d.to_dict() for d in file_diags])
        self.assertEqual(list(wrapper_diags), [])


class NativeSemanticsTests(unittest.TestCase):
    """Deliberate PyYAML-native behavior replacing legacy string coercions."""

    def test_plain_scalars_are_typed(self) -> None:
        data = parse_simple_yaml("i: 1\nf: 1.5\nb: true\nn: ~\ns: text\n")
        self.assertEqual(data, {"i": 1, "f": 1.5, "b": True, "n": None, "s": "text"})

    def test_quoted_scalars_stay_strings(self) -> None:
        data = parse_simple_yaml('a: "1"\nb: "true"\n')
        self.assertEqual(data, {"a": "1", "b": "true"})

    def test_folded_block_keeps_trailing_newline(self) -> None:
        data = parse_simple_yaml("note: >\n  one\n  two\n")
        self.assertEqual(data["note"], "one two\n")

    def test_literal_block_keeps_lines(self) -> None:
        data = parse_simple_yaml("note: |\n  one\n  two\n")
        self.assertEqual(data["note"], "one\ntwo\n")


class WrapperRefusalTests(unittest.TestCase):
    """Deterministic, bounded, hostile-echo-free refusals (H7 shapes)."""

    HOSTILE = "X43Q_HOSTILE <script> 'C:\\evil\\secret'"

    def _assert_refused(self, text: str, marker: str) -> str:
        with self.assertRaises(LayoutConfigError) as caught:
            parse_simple_yaml(text)
        message = str(caught.exception)
        self.assertIn(marker, message)
        self.assertLessEqual(len(message), 240)
        self.assertNotIn(self.HOSTILE, message)
        self.assertNotIn("Traceback", message)
        self.assertIsNone(caught.exception.__cause__)
        return message

    def test_invalid_yaml(self) -> None:
        self._assert_refused('a: "unterminated\n', "invalid_yaml")

    def test_hostile_invalid_yaml_echoes_nothing(self) -> None:
        self._assert_refused(f'"{self.HOSTILE}": "ok"\nbad: "unterminated\n', "invalid_yaml")

    def test_multiple_documents(self) -> None:
        self._assert_refused("a: 1\n---\nb: 2\n", "multiple_documents")

    def test_plain_duplicate_keys(self) -> None:
        self._assert_refused("a: 1\na: 2\n", "duplicate_key")

    def test_semantic_duplicate_spellings(self) -> None:
        self._assert_refused("1: a\n01: b\n", "duplicate_key")

    def test_unsupported_tag(self) -> None:
        self._assert_refused("a: !!python/object/new:os.system\n  args:\n    - x\n", "unsupported_tag")

    def test_merge_keys(self) -> None:
        self._assert_refused("base: &b\n  k: v\nmerged:\n  <<: *b\n", "merge keys are not approved")

    def test_anchors_and_aliases(self) -> None:
        self._assert_refused("a: &x 1\nb: *x\n", "anchors and aliases are not approved")

    def test_flow_collections(self) -> None:
        self._assert_refused('a: ["x", "y"]\n', "flow collections are not approved")

    def test_non_mapping_root(self) -> None:
        self._assert_refused("- a\n- b\n", "root must be exactly one mapping")

    def test_non_string_key(self) -> None:
        self._assert_refused("1: x\n", "mapping keys must be strings")

    def test_max_bytes_plus_one(self) -> None:
        self._assert_refused("a: " + "x" * 65_534 + "\n", "input_too_large")

    def test_max_lines_plus_one(self) -> None:
        self._assert_refused("k: v\n" + "# pad\n" * 500, "too_many_lines")

    def test_too_deep(self) -> None:
        text = "".join(f"{'  ' * i}k{i}:\n" for i in range(40))
        self._assert_refused(text, "too_deep")

    def test_unencodable_input_refused_without_codec_leak(self) -> None:
        with self.assertRaises(LayoutConfigError) as caught:
            parse_simple_yaml("a: '\udcff'\n")
        message = str(caught.exception)
        self.assertIn("not UTF-8 encodable", message)
        self.assertNotIn("codec", message)
        self.assertLessEqual(len(message), 240)


class SingleBoundaryCallTests(unittest.TestCase):
    def test_wrapper_calls_the_boundary_once_and_never_the_file_loader(self) -> None:
        real = layout_module.load_yaml_bytes
        with (
            mock.patch.object(
                layout_module, "load_yaml_bytes", side_effect=real
            ) as boundary,
            mock.patch.object(
                layout_module,
                "load_config_file",
                side_effect=AssertionError("file loader reached from wrapper"),
            ),
            mock.patch.object(
                layout_module,
                "load_yaml_path",
                side_effect=AssertionError("path boundary reached from wrapper"),
            ),
        ):
            result = parse_simple_yaml("a: 1\n")
        self.assertEqual(result, {"a": 1})
        boundary.assert_called_once()
        (call_bytes,) = boundary.call_args.args
        self.assertEqual(call_bytes, b"a: 1\n")

    def test_wrapper_reads_no_files_and_mutates_nothing(self) -> None:
        constructors_before = dict(yaml.SafeLoader.yaml_constructors)
        multi_before = dict(yaml.SafeLoader.yaml_multi_constructors)
        recursion_before = sys.getrecursionlimit()
        text = "a: 1\nb:\n  - x\n"
        first = parse_simple_yaml(text)
        second = parse_simple_yaml(text)
        self.assertEqual(first, second)
        self.assertEqual(text, "a: 1\nb:\n  - x\n")
        self.assertEqual(dict(yaml.SafeLoader.yaml_constructors), constructors_before)
        self.assertEqual(dict(yaml.SafeLoader.yaml_multi_constructors), multi_before)
        self.assertEqual(sys.getrecursionlimit(), recursion_before)


class OneEngineSourceAuditTests(unittest.TestCase):
    """`_yaml.py` is the only PyYAML import; no fallback route remains."""

    def _product_modules(self) -> list[Path]:
        return sorted(_SRC_DIR.glob("*.py"))

    def test_only_private_boundary_imports_yaml(self) -> None:
        importers = []
        for path in self._product_modules():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import) and any(
                    alias.name == "yaml" for alias in node.names
                ):
                    importers.append(path.name)
                elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "yaml":
                    importers.append(path.name)
        self.assertEqual(importers, ["_yaml.py"])

    def test_no_raw_safe_load_or_loader_use_outside_boundary(self) -> None:
        for path in self._product_modules():
            if path.name == "_yaml.py":
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("safe_load(", text, path.name)
            self.assertNotIn("SafeLoader(", text, path.name)
            self.assertNotIn("CSafeLoader", text, path.name)

    def test_no_import_error_fallback_anywhere(self) -> None:
        for path in self._product_modules():
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("except ImportError", text, path.name)
            self.assertNotIn("except ModuleNotFoundError", text, path.name)

    def test_no_legacy_engine_names_anywhere(self) -> None:
        for path in self._product_modules():
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("_strip_inline_comment", text, path.name)
            self.assertNotIn("parse_block", text, path.name)


if __name__ == "__main__":
    unittest.main()
