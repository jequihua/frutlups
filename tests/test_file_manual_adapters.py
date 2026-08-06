"""Tests for M012-S05: file/manual adapters.

Covers manual adapters (deterministic, side-effect free) and file adapters
(explicit, caller-directed local IO under a configured root with conservative
path safety). All file IO uses temporary directories; no repository files are
written.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from frutlups.adapters import (
    FileArtifactSource,
    FilePromptSink,
    ManualArtifactSource,
    ManualPromptSink,
)
from frutlups.agents import AgentProfile, AgentRole
from frutlups.delivery import (
    ArtifactCollectionRequest,
    ArtifactSource,
    PromptDeliveryRequest,
    PromptSink,
    validate_artifact_collection_result,
    validate_prompt_delivery_result,
)


def _prompt_request(**overrides: object) -> PromptDeliveryRequest:
    defaults: dict[str, object] = dict(
        target_role=AgentRole.CODER,
        prompt_path="prompts/for_coding_agent/060_x.md",
        prompt_content="# Coding Prompt 060\n\nbody",
        profile=AgentProfile(label="anthropic coder", family="anthropic"),
        sink_name="outbox",
        metadata=("k=v",),
        notes=("note",),
    )
    defaults.update(overrides)
    return PromptDeliveryRequest(**defaults)  # type: ignore[arg-type]


def _collect_request(**overrides: object) -> ArtifactCollectionRequest:
    defaults: dict[str, object] = dict(
        source_role=AgentRole.CODER,
        expected_artifacts=("out.md",),
        source_name="inbox",
    )
    defaults.update(overrides)
    return ArtifactCollectionRequest(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Manual adapters
# ---------------------------------------------------------------------------

class ManualAdapterTests(unittest.TestCase):
    def test_manual_prompt_sink_satisfies_protocol(self) -> None:
        self.assertIsInstance(ManualPromptSink(), PromptSink)

    def test_manual_artifact_source_satisfies_protocol(self) -> None:
        self.assertIsInstance(ManualArtifactSource(), ArtifactSource)

    def test_manual_prompt_sink_is_no_op(self) -> None:
        res = ManualPromptSink(sink_name="m").deliver_prompt(_prompt_request())
        self.assertFalse(res.delivered)
        self.assertFalse(res.accepted)
        self.assertEqual(res.sink_name, "m")
        self.assertEqual(res.artifact_refs, ())
        self.assertEqual(validate_prompt_delivery_result(res), ())

    def test_manual_artifact_source_is_no_op(self) -> None:
        res = ManualArtifactSource(source_name="m").collect_artifacts(_collect_request())
        self.assertFalse(res.available)
        self.assertFalse(res.collected)
        self.assertEqual(res.source_name, "m")
        self.assertEqual(res.previews, ())
        self.assertEqual(validate_artifact_collection_result(res), ())

    def test_manual_adapters_deterministic(self) -> None:
        a = ManualPromptSink().deliver_prompt(_prompt_request())
        b = ManualPromptSink().deliver_prompt(_prompt_request())
        self.assertEqual(a, b)

    def test_manual_prompt_sink_no_files_created(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = set(root.rglob("*"))
            ManualPromptSink().deliver_prompt(_prompt_request())
            self.assertEqual(set(root.rglob("*")), before)

    def test_manual_invalid_request_returns_failure(self) -> None:
        bad = PromptDeliveryRequest(target_role=AgentRole.CODER)  # no locator
        res = ManualPromptSink().deliver_prompt(bad)
        self.assertFalse(res.delivered)
        self.assertIn("invalid request", res.message)


# ---------------------------------------------------------------------------
# File prompt sink
# ---------------------------------------------------------------------------

class FilePromptSinkTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        self.outbox = self.root / "outbox"

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_satisfies_protocol(self) -> None:
        self.assertIsInstance(FilePromptSink(self.outbox), PromptSink)

    def test_writes_one_handoff_file(self) -> None:
        res = FilePromptSink(self.outbox).deliver_prompt(_prompt_request())
        self.assertTrue(res.delivered)
        self.assertEqual(validate_prompt_delivery_result(res), ())
        files = [p for p in self.outbox.rglob("*") if p.is_file()]
        self.assertEqual(len(files), 1)
        self.assertEqual(res.artifact_refs, (str(files[0]),))
        self.assertEqual(files[0].name, "060_x.md")

    def test_handoff_content_is_utf8_and_has_role(self) -> None:
        FilePromptSink(self.outbox).deliver_prompt(_prompt_request())
        text = (self.outbox / "060_x.md").read_text(encoding="utf-8")
        self.assertIn("Target role: coder", text)
        self.assertIn("Prompt Content", text)

    def test_deterministic_content(self) -> None:
        FilePromptSink(self.outbox).deliver_prompt(_prompt_request())
        first = (self.outbox / "060_x.md").read_text(encoding="utf-8")
        FilePromptSink(self.outbox).deliver_prompt(_prompt_request())
        second = (self.outbox / "060_x.md").read_text(encoding="utf-8")
        self.assertEqual(first, second)

    def test_invalid_request_does_not_write(self) -> None:
        bad = PromptDeliveryRequest(target_role=AgentRole.CODER)  # no locator
        res = FilePromptSink(self.outbox).deliver_prompt(bad)
        self.assertFalse(res.delivered)
        self.assertFalse(self.outbox.exists() and any(self.outbox.iterdir()))

    def test_traversal_prompt_id_does_not_write(self) -> None:
        req = _prompt_request(prompt_path="", prompt_id="../escape")
        res = FilePromptSink(self.outbox).deliver_prompt(req)
        self.assertFalse(res.delivered)
        self.assertIn("safe handoff filename", res.message)
        self.assertFalse(self.outbox.exists() and any(self.outbox.iterdir()))

    def test_unsafe_name_with_separator_does_not_write(self) -> None:
        req = _prompt_request(prompt_path="", prompt_id="a/b")
        res = FilePromptSink(self.outbox).deliver_prompt(req)
        self.assertFalse(res.delivered)
        self.assertFalse(self.outbox.exists() and any(self.outbox.iterdir()))


# ---------------------------------------------------------------------------
# File artifact source
# ---------------------------------------------------------------------------

class FileArtifactSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        (self.root / "out.md").write_text("artifact body content", encoding="utf-8")

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_satisfies_protocol(self) -> None:
        self.assertIsInstance(FileArtifactSource(self.root), ArtifactSource)

    def test_reads_only_requested_existing_file(self) -> None:
        res = FileArtifactSource(self.root).collect_artifacts(_collect_request())
        self.assertTrue(res.available)
        self.assertTrue(res.collected)
        self.assertEqual(res.artifact_refs, ("out.md",))
        self.assertEqual(validate_artifact_collection_result(res), ())

    def test_no_previews_by_default(self) -> None:
        res = FileArtifactSource(self.root).collect_artifacts(_collect_request())
        self.assertEqual(res.previews, ())

    def test_bounded_previews_when_configured(self) -> None:
        res = FileArtifactSource(self.root, preview_chars=8).collect_artifacts(
            _collect_request()
        )
        self.assertEqual(res.previews, ("artifact",))
        self.assertEqual(validate_artifact_collection_result(res), ())

    def test_missing_artifact_reported(self) -> None:
        res = FileArtifactSource(self.root).collect_artifacts(
            _collect_request(expected_artifacts=("nope.md",))
        )
        self.assertFalse(res.available)
        self.assertFalse(res.collected)
        self.assertIn("missing", res.message)

    def test_partial_collection_available_not_collected(self) -> None:
        res = FileArtifactSource(self.root).collect_artifacts(
            _collect_request(expected_artifacts=("out.md", "missing.md"))
        )
        self.assertTrue(res.available)
        self.assertFalse(res.collected)
        self.assertEqual(res.artifact_refs, ("out.md",))

    def test_traversal_outside_root_rejected(self) -> None:
        res = FileArtifactSource(self.root).collect_artifacts(
            _collect_request(expected_artifacts=("../escape.md",))
        )
        self.assertFalse(res.collected)
        self.assertNotIn("../escape.md", res.artifact_refs)
        self.assertIn("rejected (outside root)", res.message)

    def test_invalid_request_returns_failure(self) -> None:
        bad = ArtifactCollectionRequest(source_role=AgentRole.CODER)  # no locator
        res = FileArtifactSource(self.root).collect_artifacts(bad)
        self.assertFalse(res.collected)
        self.assertIn("invalid request", res.message)

    def test_does_not_write_files(self) -> None:
        before = set(self.root.rglob("*"))
        FileArtifactSource(self.root, preview_chars=4).collect_artifacts(
            _collect_request()
        )
        self.assertEqual(set(self.root.rglob("*")), before)


# ---------------------------------------------------------------------------
# End-to-end: deliver then collect within a temp dir
# ---------------------------------------------------------------------------

class EmptyRootTests(unittest.TestCase):
    """Corrective (review 060): empty/whitespace roots must be rejected before
    any IO and must not silently default to the current working directory."""

    def test_file_prompt_sink_empty_root_does_not_write(self) -> None:
        orig = os.getcwd()
        with TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                res = FilePromptSink("").deliver_prompt(_prompt_request())
                self.assertFalse(res.delivered)
                self.assertIn("root", res.message)
                self.assertEqual(list(Path(tmp).rglob("*")), [])
            finally:
                os.chdir(orig)

    def test_file_prompt_sink_whitespace_root_does_not_write(self) -> None:
        orig = os.getcwd()
        with TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                res = FilePromptSink("   ").deliver_prompt(_prompt_request())
                self.assertFalse(res.delivered)
                self.assertIn("root", res.message)
                self.assertEqual(list(Path(tmp).rglob("*")), [])
            finally:
                os.chdir(orig)

    def test_file_artifact_source_empty_root_does_not_read(self) -> None:
        orig = os.getcwd()
        with TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                Path("sentinel.md").write_text("secret", encoding="utf-8")
                res = FileArtifactSource("", preview_chars=10).collect_artifacts(
                    _collect_request(expected_artifacts=("sentinel.md",))
                )
                self.assertFalse(res.available)
                self.assertFalse(res.collected)
                self.assertIn("root", res.message)
                self.assertEqual(res.artifact_refs, ())
                self.assertEqual(res.previews, ())
            finally:
                os.chdir(orig)

    def test_file_artifact_source_whitespace_root_does_not_read(self) -> None:
        orig = os.getcwd()
        with TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                Path("sentinel.md").write_text("secret", encoding="utf-8")
                res = FileArtifactSource("   ").collect_artifacts(
                    _collect_request(expected_artifacts=("sentinel.md",))
                )
                self.assertFalse(res.available)
                self.assertFalse(res.collected)
                self.assertIn("root", res.message)
            finally:
                os.chdir(orig)


class RoundTripTests(unittest.TestCase):
    def test_deliver_then_collect(self) -> None:
        with TemporaryDirectory() as tmp:
            outbox = Path(tmp) / "outbox"
            delivery = FilePromptSink(outbox).deliver_prompt(_prompt_request())
            self.assertTrue(delivery.delivered)
            written = delivery.artifact_refs[0]

            source = FileArtifactSource(outbox, preview_chars=16)
            collection = source.collect_artifacts(
                ArtifactCollectionRequest(
                    source_role=AgentRole.CODER,
                    expected_artifacts=(written,),
                    source_name="inbox",
                )
            )
            self.assertTrue(collection.available)
            self.assertTrue(collection.collected)
            json.dumps(delivery.to_dict())
            json.dumps(collection.to_dict())
            self.assertEqual(validate_prompt_delivery_result(delivery), ())
            self.assertEqual(validate_artifact_collection_result(collection), ())


if __name__ == "__main__":
    unittest.main()
