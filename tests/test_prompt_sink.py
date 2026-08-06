"""Tests for M012-S03: PromptSink protocol and delivery request/result data.

Covers:
- PromptSink is structural (runtime_checkable): an object with deliver_prompt
  satisfies it; one without does not
- request/result dataclasses are immutable (frozen)
- request/result to_dict() is JSON-serializable plain Python
- target role serializes as a string; optional AgentProfile via its schema
- validation reports missing locator, wrong-typed role/profile/metadata, and
  wrong-typed result fields deterministically
- malformed metadata/notes/artifact refs serialize as JSON-safe placeholders
- a fake in-memory sink returns a deterministic result with no side effects
- preview_prompt_delivery is a read-only no-op
- contract is data only: no provider SDK / adapter / dispatch / memory
"""

from __future__ import annotations

import json
import unittest

from frutlups.agents import AgentProfile, AgentRole
from frutlups.delivery import (
    PromptDeliveryRequest,
    PromptDeliveryResult,
    PromptSink,
    preview_prompt_delivery,
    validate_prompt_delivery_request,
    validate_prompt_delivery_result,
)


def _request(**overrides: object) -> PromptDeliveryRequest:
    defaults: dict[str, object] = dict(
        target_role=AgentRole.CODER,
        prompt_path="prompts/for_coding_agent/058_x.md",
        prompt_content="# Coding Prompt 058",
        profile=AgentProfile(label="anthropic coder", family="anthropic"),
        sink_name="memory",
        sink_kind="manual",
        metadata=("k=v",),
        notes=("example",),
    )
    defaults.update(overrides)
    return PromptDeliveryRequest(**defaults)  # type: ignore[arg-type]


# A tiny local fake sink — no filesystem/network/subprocess/memory.
class _FakeSink:
    def deliver_prompt(
        self, request: PromptDeliveryRequest
    ) -> PromptDeliveryResult:
        return PromptDeliveryResult(
            delivered=True,
            accepted=True,
            message=f"delivered to {request.target_role.value}",
            artifact_refs=("ref-1",),
            sink_name="fake",
        )


class _NotASink:
    pass


# ---------------------------------------------------------------------------
# Protocol shape
# ---------------------------------------------------------------------------

class ProtocolShapeTests(unittest.TestCase):
    def test_fake_sink_satisfies_protocol(self) -> None:
        self.assertIsInstance(_FakeSink(), PromptSink)

    def test_object_without_method_does_not_satisfy(self) -> None:
        self.assertNotIsInstance(_NotASink(), PromptSink)

    def test_fake_sink_returns_deterministic_result(self) -> None:
        sink = _FakeSink()
        r1 = sink.deliver_prompt(_request())
        r2 = sink.deliver_prompt(_request())
        self.assertEqual(r1, r2)
        self.assertTrue(r1.delivered)
        self.assertEqual(r1.message, "delivered to coder")


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------

class ImmutabilityTests(unittest.TestCase):
    def test_request_is_frozen(self) -> None:
        req = _request()
        with self.assertRaises((AttributeError, TypeError)):
            req.prompt_path = "other"  # type: ignore[misc]

    def test_result_is_frozen(self) -> None:
        res = PromptDeliveryResult(delivered=True)
        with self.assertRaises((AttributeError, TypeError)):
            res.delivered = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

class SerializationTests(unittest.TestCase):
    def test_request_to_dict_json_safe(self) -> None:
        d = _request().to_dict()
        json.dumps(d)
        self.assertEqual(d["target_role"], "coder")
        self.assertEqual(d["profile"]["family"], "anthropic")
        self.assertEqual(d["metadata"], ["k=v"])

    def test_target_role_serializes_as_string(self) -> None:
        self.assertIsInstance(_request().to_dict()["target_role"], str)

    def test_profile_none_serializes_none(self) -> None:
        d = _request(profile=None).to_dict()
        self.assertIsNone(d["profile"])

    def test_result_to_dict_json_safe(self) -> None:
        res = PromptDeliveryResult(
            delivered=True, accepted=False, message="ok", artifact_refs=("a",), sink_name="s"
        )
        d = res.to_dict()
        json.dumps(d)
        self.assertEqual(d["delivered"], True)
        self.assertEqual(d["artifact_refs"], ["a"])

    def test_malformed_metadata_serializes_json_safe(self) -> None:
        d = _request(metadata=(object(),)).to_dict()  # type: ignore[arg-type]
        json.dumps(d)
        self.assertIsInstance(d["metadata"][0], str)

    def test_malformed_metadata_field_serializes_json_safe(self) -> None:
        d = _request(metadata=object()).to_dict()  # type: ignore[arg-type]
        json.dumps(d)

    def test_malformed_artifact_refs_serialize_json_safe(self) -> None:
        d = PromptDeliveryResult(
            delivered=True, artifact_refs=(object(),)  # type: ignore[arg-type]
        ).to_dict()
        json.dumps(d)

    def test_malformed_role_and_profile_serialize_json_safe(self) -> None:
        d = PromptDeliveryRequest(
            target_role="coder",  # type: ignore[arg-type]
            prompt_path="p.md",
            profile="not-profile",  # type: ignore[arg-type]
        ).to_dict()
        json.dumps(d)


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------

class RequestValidationTests(unittest.TestCase):
    def test_valid_request_no_errors(self) -> None:
        self.assertEqual(validate_prompt_delivery_request(_request()), ())

    def test_path_only_request_is_valid(self) -> None:
        req = PromptDeliveryRequest(target_role=AgentRole.CODER, prompt_path="p.md")
        self.assertEqual(validate_prompt_delivery_request(req), ())

    def test_id_only_request_is_valid(self) -> None:
        req = PromptDeliveryRequest(target_role=AgentRole.CODER, prompt_id="058")
        self.assertEqual(validate_prompt_delivery_request(req), ())

    def test_missing_locator(self) -> None:
        req = PromptDeliveryRequest(target_role=AgentRole.CODER)
        errs = validate_prompt_delivery_request(req)
        self.assertTrue(any("prompt locator" in e for e in errs))

    def test_wrong_typed_role(self) -> None:
        req = PromptDeliveryRequest(target_role="coder", prompt_path="p.md")  # type: ignore[arg-type]
        errs = validate_prompt_delivery_request(req)
        self.assertTrue(any("target_role" in e for e in errs))

    def test_wrong_typed_profile(self) -> None:
        req = PromptDeliveryRequest(
            target_role=AgentRole.CODER, prompt_path="p.md", profile="x"  # type: ignore[arg-type]
        )
        errs = validate_prompt_delivery_request(req)
        self.assertTrue(any("profile must be an AgentProfile" in e for e in errs))

    def test_invalid_profile_surfaced_with_prefix(self) -> None:
        req = PromptDeliveryRequest(
            target_role=AgentRole.CODER,
            prompt_path="p.md",
            profile=AgentProfile(label="x", mode="rpc"),
        )
        errs = validate_prompt_delivery_request(req)
        self.assertTrue(any("profile: mode" in e for e in errs))

    def test_malformed_metadata_reported(self) -> None:
        req = _request(metadata=(object(),))  # type: ignore[arg-type]
        errs = validate_prompt_delivery_request(req)
        self.assertTrue(any("metadata" in e for e in errs))

    def test_non_request_input(self) -> None:
        self.assertEqual(
            validate_prompt_delivery_request("nope"),  # type: ignore[arg-type]
            ("request must be a PromptDeliveryRequest instance",),
        )


# ---------------------------------------------------------------------------
# Result validation
# ---------------------------------------------------------------------------

class ResultValidationTests(unittest.TestCase):
    def test_valid_result_no_errors(self) -> None:
        res = PromptDeliveryResult(delivered=True, accepted=True, message="ok")
        self.assertEqual(validate_prompt_delivery_result(res), ())

    def test_wrong_typed_delivered(self) -> None:
        res = PromptDeliveryResult(delivered="yes")  # type: ignore[arg-type]
        errs = validate_prompt_delivery_result(res)
        self.assertTrue(any("delivered must be a bool" in e for e in errs))

    def test_wrong_typed_message(self) -> None:
        res = PromptDeliveryResult(delivered=True, message=123)  # type: ignore[arg-type]
        errs = validate_prompt_delivery_result(res)
        self.assertTrue(any("message must be a string" in e for e in errs))

    def test_malformed_artifact_refs_reported(self) -> None:
        res = PromptDeliveryResult(delivered=True, artifact_refs=(object(),))  # type: ignore[arg-type]
        errs = validate_prompt_delivery_result(res)
        self.assertTrue(any("artifact_refs" in e for e in errs))

    def test_non_result_input(self) -> None:
        self.assertEqual(
            validate_prompt_delivery_result("nope"),  # type: ignore[arg-type]
            ("result must be a PromptDeliveryResult instance",),
        )


# ---------------------------------------------------------------------------
# Preview no-op
# ---------------------------------------------------------------------------

class PreviewTests(unittest.TestCase):
    def test_preview_is_no_op(self) -> None:
        res = preview_prompt_delivery(_request(sink_name="memory"))
        self.assertFalse(res.delivered)
        self.assertFalse(res.accepted)
        self.assertEqual(res.sink_name, "memory")
        self.assertEqual(validate_prompt_delivery_result(res), ())

    def test_preview_deterministic(self) -> None:
        self.assertEqual(
            preview_prompt_delivery(_request()).to_dict(),
            preview_prompt_delivery(_request()).to_dict(),
        )


# ---------------------------------------------------------------------------
# End-to-end with the fake sink (no side effects)
# ---------------------------------------------------------------------------

class FakeSinkFlowTests(unittest.TestCase):
    def test_request_validate_serialize_deliver(self) -> None:
        req = _request()
        self.assertEqual(validate_prompt_delivery_request(req), ())
        json.dumps(req.to_dict())
        result = _FakeSink().deliver_prompt(req)
        self.assertEqual(validate_prompt_delivery_result(result), ())
        json.dumps(result.to_dict())
        self.assertTrue(result.delivered)


if __name__ == "__main__":
    unittest.main()
