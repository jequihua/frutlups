"""Tests for M012-S06: in-memory mock adapters.

Covers the deterministic mock `PromptSink` / `ArtifactSource` test doubles:
protocol conformance, immutable request traces, configurable JSON-safe canned
results, caller-mutation isolation, invalid-request handling (failure result,
not recorded), reset(), and the absence of any filesystem IO.
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from frutlups.adapters import MockArtifactSource, MockPromptSink
from frutlups.agents import AgentRole
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
        target_role=AgentRole.CODER, prompt_path="p.md"
    )
    defaults.update(overrides)
    return PromptDeliveryRequest(**defaults)  # type: ignore[arg-type]


def _collect_request(**overrides: object) -> ArtifactCollectionRequest:
    defaults: dict[str, object] = dict(
        source_role=AgentRole.CODER, expected_artifacts=("a.md",)
    )
    defaults.update(overrides)
    return ArtifactCollectionRequest(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

class ProtocolTests(unittest.TestCase):
    def test_mock_prompt_sink_satisfies_protocol(self) -> None:
        self.assertIsInstance(MockPromptSink(), PromptSink)

    def test_mock_artifact_source_satisfies_protocol(self) -> None:
        self.assertIsInstance(MockArtifactSource(), ArtifactSource)


# ---------------------------------------------------------------------------
# Deterministic canned results
# ---------------------------------------------------------------------------

class CannedResultTests(unittest.TestCase):
    def test_prompt_result_deterministic_and_valid(self) -> None:
        sink = MockPromptSink(
            delivered=True, accepted=False, message="m",
            artifact_refs=("ref-1",), sink_name="s",
        )
        r1 = sink.deliver_prompt(_prompt_request())
        r2 = sink.deliver_prompt(_prompt_request())
        self.assertEqual(r1, r2)
        self.assertTrue(r1.delivered)
        self.assertFalse(r1.accepted)
        self.assertEqual(r1.artifact_refs, ("ref-1",))
        self.assertEqual(validate_prompt_delivery_result(r1), ())
        json.dumps(r1.to_dict())

    def test_artifact_result_deterministic_and_valid(self) -> None:
        src = MockArtifactSource(
            available=True, collected=True, message="m",
            artifact_refs=("a.md",), previews=("body",), source_name="s",
        )
        r1 = src.collect_artifacts(_collect_request())
        r2 = src.collect_artifacts(_collect_request())
        self.assertEqual(r1, r2)
        self.assertEqual(r1.artifact_refs, ("a.md",))
        self.assertEqual(r1.previews, ("body",))
        self.assertEqual(validate_artifact_collection_result(r1), ())
        json.dumps(r1.to_dict())


# ---------------------------------------------------------------------------
# Request traces (immutable tuples, ordered)
# ---------------------------------------------------------------------------

class TraceTests(unittest.TestCase):
    def test_prompt_requests_recorded_in_order_as_tuple(self) -> None:
        sink = MockPromptSink()
        a = _prompt_request(prompt_id="1")
        b = _prompt_request(prompt_id="2")
        sink.deliver_prompt(a)
        sink.deliver_prompt(b)
        self.assertIsInstance(sink.requests, tuple)
        self.assertEqual(sink.requests, (a, b))

    def test_artifact_requests_recorded_in_order_as_tuple(self) -> None:
        src = MockArtifactSource()
        a = _collect_request(expected_artifacts=("a.md",))
        b = _collect_request(expected_artifacts=("b.md",))
        src.collect_artifacts(a)
        src.collect_artifacts(b)
        self.assertIsInstance(src.requests, tuple)
        self.assertEqual(src.requests, (a, b))

    def test_trace_snapshot_is_independent(self) -> None:
        sink = MockPromptSink()
        sink.deliver_prompt(_prompt_request(prompt_id="1"))
        snapshot = sink.requests
        sink.deliver_prompt(_prompt_request(prompt_id="2"))
        # earlier snapshot is unchanged by later deliveries
        self.assertEqual(len(snapshot), 1)
        self.assertEqual(len(sink.requests), 2)

    def test_reset_clears_trace(self) -> None:
        sink = MockPromptSink()
        sink.deliver_prompt(_prompt_request())
        self.assertEqual(len(sink.requests), 1)
        sink.reset()
        self.assertEqual(sink.requests, ())
        src = MockArtifactSource()
        src.collect_artifacts(_collect_request())
        src.reset()
        self.assertEqual(src.requests, ())


# ---------------------------------------------------------------------------
# Caller-mutation isolation
# ---------------------------------------------------------------------------

class MutationIsolationTests(unittest.TestCase):
    def test_prompt_refs_isolated_from_caller_list(self) -> None:
        refs = ["ref-1"]
        sink = MockPromptSink(artifact_refs=refs)
        refs.append("ref-2")  # mutate after construction
        result = sink.deliver_prompt(_prompt_request())
        self.assertEqual(result.artifact_refs, ("ref-1",))

    def test_artifact_refs_and_previews_isolated_from_caller_list(self) -> None:
        refs = ["a.md"]
        previews = ["body"]
        src = MockArtifactSource(artifact_refs=refs, previews=previews)
        refs.append("b.md")
        previews.append("extra")
        result = src.collect_artifacts(_collect_request())
        self.assertEqual(result.artifact_refs, ("a.md",))
        self.assertEqual(result.previews, ("body",))


# ---------------------------------------------------------------------------
# Invalid requests
# ---------------------------------------------------------------------------

class InvalidRequestTests(unittest.TestCase):
    def test_invalid_prompt_request_not_recorded(self) -> None:
        sink = MockPromptSink()
        res = sink.deliver_prompt(
            PromptDeliveryRequest(target_role=AgentRole.CODER)  # no locator
        )
        self.assertFalse(res.delivered)
        self.assertIn("invalid request", res.message)
        self.assertEqual(sink.requests, ())

    def test_invalid_artifact_request_not_recorded(self) -> None:
        src = MockArtifactSource()
        res = src.collect_artifacts(
            ArtifactCollectionRequest(source_role=AgentRole.CODER)  # no locator
        )
        self.assertFalse(res.collected)
        self.assertIn("invalid request", res.message)
        self.assertEqual(src.requests, ())


# ---------------------------------------------------------------------------
# No filesystem IO
# ---------------------------------------------------------------------------

class NoFilesystemTests(unittest.TestCase):
    def test_mock_adapters_create_no_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = set(root.rglob("*"))
            MockPromptSink(artifact_refs=("r",)).deliver_prompt(_prompt_request())
            MockArtifactSource(previews=("p",)).collect_artifacts(_collect_request())
            self.assertEqual(set(root.rglob("*")), before)


if __name__ == "__main__":
    unittest.main()
