"""Tests for M001-S01: contract-v1 typed model, lossless renderer, Drive payload.

Conformance is proven two ways, both against byte-pinned released Template
V3.1.0 artifacts carried in the self-contained release-authority fixture bundle,
never against a private reimplementation:

- The parser is proven against the released fixture corpus: for every ``sidecar``
  and ``align`` fixture in ``tests/fixtures/slice_contract/manifest.json``,
  ``frutlups.slice_prompt`` reproduces exactly the reason codes the manifest
  declares.
- The renderer and the round-trip are proven against the released reference
  checker (``scripts/slice_contract_check.py``), the authority this module is
  tested against: each canonical entry is rendered from the typed model and the
  checker returns zero diagnostics, and the ``## Typed Entry`` block strict-loads
  equal to the attempt-resolved sidecar entry.

The reference checker, layout, and fixture corpus are release-authority copies
located relative to this test file and consumed read-only. Their complete bundle
is digest-pinned independently, so an extracted sdist and a flattened public
repository exercise the same bytes as the development layout.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

import yaml

from frutlups.slice_prompt import (
    PAYLOAD_SCHEMA,
    ContractVocab,
    SlicePromptError,
    align,
    drive_payload,
    parse_sidecar,
    render_prompt,
    resolve_entry,
)

TEST_ROOT = Path(__file__).resolve().parent
RELEASE_AUTHORITY = TEST_ROOT / "fixtures" / "release_v0_2_0"
LAYOUT_PATH = RELEASE_AUTHORITY / "frutlups.layout.yaml"
FIXTURES = RELEASE_AUTHORITY / "slice_contract"
CHECKER_PATH = RELEASE_AUTHORITY / "slice_contract_check.py"

# The canonical positive renderings the contract defines (section 8): each is one
# (sidecar, slice, attempt) the renderer must produce so the released checker
# passes and the typed entry equals the attempt-resolved sidecar entry.
CANONICAL_RENDERS = (
    ("all_fields.slices.yaml", "M001-S01", None),
    ("all_fields.slices.yaml", "M002-S02", "002"),
    ("all_fields_attempt_001.slices.yaml", "M002-S02", "001"),
    ("frozen_entry_valid.slices.yaml", "M001-S01", None),
)


def _load_reference_checker():
    spec = importlib.util.spec_from_file_location("frutlups_ref_slice_contract_check", CHECKER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SlicePromptFixtureConformanceTests(unittest.TestCase):
    """The parser reproduces the released corpus's reason codes."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.vocab = ContractVocab.from_layout(LAYOUT_PATH)
        cls.manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))

    def _parse(self, name: str):
        return parse_sidecar(FIXTURES / name, self.vocab, sidecar_path=FIXTURES / name)

    def test_sidecar_fixtures_reproduce_expected_codes(self) -> None:
        sidecar_fixtures = [f for f in self.manifest["fixtures"] if f["mode"] == "sidecar"]
        self.assertGreaterEqual(len(sidecar_fixtures), 60, "corpus should carry the full sidecar fixture set")
        for fx in sidecar_fixtures:
            with self.subTest(fixture=fx["id"]):
                parsed = self._parse(Path(fx["path"]).name)
                self.assertEqual(set(parsed.diagnostic_codes()), set(fx["expected"]["codes"]))

    def test_align_fixtures_reproduce_expected_codes(self) -> None:
        align_fixtures = [f for f in self.manifest["fixtures"] if f["mode"] == "align"]
        self.assertGreaterEqual(len(align_fixtures), 3, "corpus should carry the alignment fixtures")
        for fx in align_fixtures:
            with self.subTest(fixture=fx["id"]):
                names = fx["args"]["sidecars"]
                parsed_a = self._parse(names[0])
                parsed_b = self._parse(names[1])
                got = (
                    set(parsed_a.diagnostic_codes())
                    | set(parsed_b.diagnostic_codes())
                    | {d.code for d in align(parsed_a, parsed_b)}
                )
                self.assertEqual(got, set(fx["expected"]["codes"]))

    def test_positive_sidecars_parse_into_typed_entries(self) -> None:
        parsed = self._parse("all_fields.slices.yaml")
        self.assertTrue(parsed.is_valid)
        self.assertEqual([e.slice_id for e in parsed.entries], ["M001-S01", "M002-S02"])
        live = parsed.entry("M002-S02")
        self.assertIsNotNone(live)
        assert live is not None
        self.assertTrue(live.live)
        self.assertTrue(live.corrective)
        self.assertEqual(live.attempt, "002")
        self.assertEqual(live.milestone, "M002")
        self.assertEqual(len(live.writes), 4)


class SlicePromptRendererTests(unittest.TestCase):
    """The renderer's output conforms to the released checker; the carrier is lossless."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.vocab = ContractVocab.from_layout(LAYOUT_PATH)
        cls.checker = _load_reference_checker()
        layout, layout_diags = cls.checker.load_layout_contract(LAYOUT_PATH)
        assert not layout_diags, layout_diags
        cls.layout = layout

    def test_canonical_renders_pass_reference_checker(self) -> None:
        for name, slice_id, attempt in CANONICAL_RENDERS:
            with self.subTest(sidecar=name, slice=slice_id, attempt=attempt):
                parsed = parse_sidecar(FIXTURES / name, self.vocab, sidecar_path=FIXTURES / name)
                entry = parsed.entry(slice_id)
                self.assertIsNotNone(entry)
                assert entry is not None
                rendered = render_prompt(entry, self.vocab, attempt=attempt)
                doc = self.checker._load_yaml_file(FIXTURES / name)
                diagnostics = self.checker.check_rendered(doc, slice_id, attempt, rendered, name, self.layout)
                self.assertEqual([d.code for d in diagnostics], [], msg=[d.code for d in diagnostics])

    def test_typed_entry_block_loads_equal_to_resolved_entry(self) -> None:
        token = self.vocab.attempt_token
        for name, slice_id, attempt in CANONICAL_RENDERS:
            with self.subTest(sidecar=name, slice=slice_id, attempt=attempt):
                parsed = parse_sidecar(FIXTURES / name, self.vocab, sidecar_path=FIXTURES / name)
                entry = parsed.entry(slice_id)
                assert entry is not None
                rendered = render_prompt(entry, self.vocab, attempt=attempt)
                block = self._extract_typed_entry_block(rendered)
                loaded = yaml.safe_load(block)
                expected = resolve_entry(dict(entry.data), token, attempt)
                self.assertEqual(loaded, expected)
                # dispatch status is read line-based: exactly one plain status line
                status_lines = [ln for ln in block.splitlines() if ln.startswith("status:")]
                self.assertEqual(status_lines, [f"status: {entry.status}"])

    def test_renderer_refuses_invalid_entry(self) -> None:
        # Failure injection: an entry with a malformed write path must not render.
        from frutlups.slice_prompt import SliceEntry

        parsed = parse_sidecar(FIXTURES / "all_fields.slices.yaml", self.vocab, sidecar_path=FIXTURES / "all_fields.slices.yaml")
        good = parsed.entry("M001-S01")
        assert good is not None
        broken_data = dict(good.data)
        broken_writes = [dict(w) for w in broken_data["writes"]]
        broken_writes[0]["path"] = "08_pkg/src/routing/"  # a directory, not a file
        broken_data["writes"] = broken_writes
        with self.assertRaises(SlicePromptError):
            render_prompt(SliceEntry(broken_data), self.vocab)

    def test_renderer_refuses_mismatched_attempt(self) -> None:
        parsed = parse_sidecar(FIXTURES / "all_fields.slices.yaml", self.vocab, sidecar_path=FIXTURES / "all_fields.slices.yaml")
        entry = parsed.entry("M002-S02")
        assert entry is not None
        with self.assertRaises(SlicePromptError):
            render_prompt(entry, self.vocab, attempt="001")  # entry's attempt is 002

    @staticmethod
    def _extract_typed_entry_block(rendered: str) -> str:
        lines = rendered.splitlines()
        heading = lines.index("## Typed Entry")
        body = lines[heading + 1:]
        open_i = body.index("```yaml")
        close_i = body.index("```", open_i + 1)
        return "\n".join(body[open_i + 1:close_i])


class DrivePayloadTests(unittest.TestCase):
    """The versioned Drive payload is emitted, lossless, and JSON-safe."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.vocab = ContractVocab.from_layout(LAYOUT_PATH)

    def test_payload_is_versioned_lossless_and_json_safe(self) -> None:
        parsed = parse_sidecar(FIXTURES / "all_fields.slices.yaml", self.vocab, sidecar_path=FIXTURES / "all_fields.slices.yaml")
        entry = parsed.entry("M001-S01")
        assert entry is not None
        payload = drive_payload(entry, self.vocab)
        round_tripped = json.loads(json.dumps(payload, sort_keys=True))
        self.assertEqual(round_tripped["schema"], PAYLOAD_SCHEMA)
        self.assertEqual(round_tripped["contract_version"], self.vocab.version)
        self.assertEqual(round_tripped["slice"], "M001-S01")
        self.assertEqual(round_tripped["attempt"], None)
        self.assertIsNone(round_tripped["execution_envelope"])
        # the carrier equals the attempt-resolved sidecar entry
        self.assertEqual(round_tripped["entry"], resolve_entry(dict(entry.data), self.vocab.attempt_token, None))

    def test_live_payload_surfaces_runner_consumed_fields_attempt_resolved(self) -> None:
        parsed = parse_sidecar(FIXTURES / "all_fields.slices.yaml", self.vocab, sidecar_path=FIXTURES / "all_fields.slices.yaml")
        entry = parsed.entry("M002-S02")
        assert entry is not None
        payload = drive_payload(entry, self.vocab)
        self.assertEqual(payload["attempt"], "002")
        self.assertTrue(payload["live"])
        envelope = payload["execution_envelope"]
        self.assertIsInstance(envelope, dict)
        assert isinstance(envelope, dict)
        # runner-consumed fields reach the runner through the payload (contract section 7)
        self.assertEqual(envelope["agent_budget_seconds"], 1800)
        self.assertEqual(envelope["local_output_root"], "local_state/m002_s02_attempt_002/")
        # the write manifest is resolved, so no {attempt} token survives
        for write in payload["writes"]:
            self.assertNotIn(self.vocab.attempt_token, write["path"])
        self.assertEqual(payload["writes"][0]["path"], "01_data/evidence/m002_s02_attempt_002/joined_ledger.json")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
