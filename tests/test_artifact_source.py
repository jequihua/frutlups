"""Tests for M012-S04: ArtifactSource protocol and collection request/result.

Covers:
- ArtifactSource is structural (runtime_checkable): an object with
  collect_artifacts satisfies it; one without does not
- request/result dataclasses are immutable (frozen)
- request/result to_dict() is JSON-serializable plain Python
- source role serializes as a string; optional AgentProfile via its schema
- validation reports missing collection locator and wrong-typed
  role/profile/metadata/result fields deterministically
- malformed expected refs/metadata/notes/artifact refs/previews serialize as
  JSON-safe placeholders without side effects
- a fake in-memory source returns a deterministic result with no side effects
- preview_artifact_collection is a read-only no-op
- contract is data only: no provider SDK / adapter / dispatch / fs / memory
"""

from __future__ import annotations

import json
import unittest

from frutlups.agents import AgentProfile, AgentRole
from frutlups.delivery import (
    ArtifactCollectionRequest,
    ArtifactCollectionResult,
    ArtifactSource,
    preview_artifact_collection,
    validate_artifact_collection_request,
    validate_artifact_collection_result,
)


def _request(**overrides: object) -> ArtifactCollectionRequest:
    defaults: dict[str, object] = dict(
        source_role=AgentRole.CODER,
        prompt_path="prompts/for_coding_agent/059_x.md",
        expected_artifacts=("05_governance/reviews/x_self_report.md",),
        profile=AgentProfile(label="anthropic coder", family="anthropic"),
        source_name="memory",
        source_kind="manual",
        metadata=("k=v",),
        notes=("example",),
    )
    defaults.update(overrides)
    return ArtifactCollectionRequest(**defaults)  # type: ignore[arg-type]


class _FakeSource:
    def collect_artifacts(
        self, request: ArtifactCollectionRequest
    ) -> ArtifactCollectionResult:
        return ArtifactCollectionResult(
            available=True,
            collected=True,
            message=f"collected for {request.source_role.value}",
            artifact_refs=("ref-1",),
            previews=("preview text",),
            source_name="fake",
        )


class _NotASource:
    pass


# ---------------------------------------------------------------------------
# Protocol shape
# ---------------------------------------------------------------------------

class ProtocolShapeTests(unittest.TestCase):
    def test_fake_source_satisfies_protocol(self) -> None:
        self.assertIsInstance(_FakeSource(), ArtifactSource)

    def test_object_without_method_does_not_satisfy(self) -> None:
        self.assertNotIsInstance(_NotASource(), ArtifactSource)

    def test_fake_source_returns_deterministic_result(self) -> None:
        src = _FakeSource()
        r1 = src.collect_artifacts(_request())
        r2 = src.collect_artifacts(_request())
        self.assertEqual(r1, r2)
        self.assertTrue(r1.collected)
        self.assertEqual(r1.message, "collected for coder")


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------

class ImmutabilityTests(unittest.TestCase):
    def test_request_is_frozen(self) -> None:
        req = _request()
        with self.assertRaises((AttributeError, TypeError)):
            req.prompt_path = "other"  # type: ignore[misc]

    def test_result_is_frozen(self) -> None:
        res = ArtifactCollectionResult(available=True)
        with self.assertRaises((AttributeError, TypeError)):
            res.available = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

class SerializationTests(unittest.TestCase):
    def test_request_to_dict_json_safe(self) -> None:
        d = _request().to_dict()
        json.dumps(d)
        self.assertEqual(d["source_role"], "coder")
        self.assertEqual(d["profile"]["family"], "anthropic")
        self.assertEqual(d["expected_artifacts"], ["05_governance/reviews/x_self_report.md"])

    def test_source_role_serializes_as_string(self) -> None:
        self.assertIsInstance(_request().to_dict()["source_role"], str)

    def test_profile_none_serializes_none(self) -> None:
        self.assertIsNone(_request(profile=None).to_dict()["profile"])

    def test_result_to_dict_json_safe(self) -> None:
        res = ArtifactCollectionResult(
            available=True, collected=False, message="ok",
            artifact_refs=("a",), previews=("p",), source_name="s",
        )
        d = res.to_dict()
        json.dumps(d)
        self.assertEqual(d["available"], True)
        self.assertEqual(d["artifact_refs"], ["a"])
        self.assertEqual(d["previews"], ["p"])

    def test_malformed_expected_artifacts_serialize_json_safe(self) -> None:
        d = _request(expected_artifacts=(object(),)).to_dict()  # type: ignore[arg-type]
        json.dumps(d)
        self.assertIsInstance(d["expected_artifacts"][0], str)

    def test_malformed_expected_artifacts_field_serializes_json_safe(self) -> None:
        d = _request(expected_artifacts=object()).to_dict()  # type: ignore[arg-type]
        json.dumps(d)

    def test_malformed_previews_serialize_json_safe(self) -> None:
        d = ArtifactCollectionResult(
            available=True, previews=(object(),)  # type: ignore[arg-type]
        ).to_dict()
        json.dumps(d)

    def test_malformed_role_and_profile_serialize_json_safe(self) -> None:
        d = ArtifactCollectionRequest(
            source_role="coder",  # type: ignore[arg-type]
            expected_artifacts=("a.md",),
            profile="not-profile",  # type: ignore[arg-type]
        ).to_dict()
        json.dumps(d)


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------

class RequestValidationTests(unittest.TestCase):
    def test_valid_request_no_errors(self) -> None:
        self.assertEqual(validate_artifact_collection_request(_request()), ())

    def test_expected_artifact_only_is_valid(self) -> None:
        req = ArtifactCollectionRequest(
            source_role=AgentRole.CODER, expected_artifacts=("a.md",)
        )
        self.assertEqual(validate_artifact_collection_request(req), ())

    def test_prompt_locator_with_source_name_is_valid(self) -> None:
        req = ArtifactCollectionRequest(
            source_role=AgentRole.CODER, prompt_path="p.md", source_name="memory"
        )
        self.assertEqual(validate_artifact_collection_request(req), ())

    def test_prompt_locator_without_source_name_is_invalid(self) -> None:
        req = ArtifactCollectionRequest(
            source_role=AgentRole.CODER, prompt_path="p.md"
        )
        errs = validate_artifact_collection_request(req)
        self.assertTrue(any("collection locator" in e for e in errs))

    def test_no_locator_at_all(self) -> None:
        req = ArtifactCollectionRequest(source_role=AgentRole.CODER)
        errs = validate_artifact_collection_request(req)
        self.assertTrue(any("collection locator" in e for e in errs))

    def test_wrong_typed_role(self) -> None:
        req = ArtifactCollectionRequest(
            source_role="coder", expected_artifacts=("a.md",)  # type: ignore[arg-type]
        )
        errs = validate_artifact_collection_request(req)
        self.assertTrue(any("source_role" in e for e in errs))

    def test_wrong_typed_profile(self) -> None:
        req = ArtifactCollectionRequest(
            source_role=AgentRole.CODER, expected_artifacts=("a.md",), profile="x"  # type: ignore[arg-type]
        )
        errs = validate_artifact_collection_request(req)
        self.assertTrue(any("profile must be an AgentProfile" in e for e in errs))

    def test_invalid_profile_surfaced_with_prefix(self) -> None:
        req = ArtifactCollectionRequest(
            source_role=AgentRole.CODER,
            expected_artifacts=("a.md",),
            profile=AgentProfile(label="x", mode="rpc"),
        )
        errs = validate_artifact_collection_request(req)
        self.assertTrue(any("profile: mode" in e for e in errs))

    def test_malformed_metadata_reported(self) -> None:
        req = _request(metadata=(object(),))  # type: ignore[arg-type]
        errs = validate_artifact_collection_request(req)
        self.assertTrue(any("metadata" in e for e in errs))

    def test_non_request_input(self) -> None:
        self.assertEqual(
            validate_artifact_collection_request("nope"),  # type: ignore[arg-type]
            ("request must be an ArtifactCollectionRequest instance",),
        )


# ---------------------------------------------------------------------------
# Result validation
# ---------------------------------------------------------------------------

class ResultValidationTests(unittest.TestCase):
    def test_valid_result_no_errors(self) -> None:
        res = ArtifactCollectionResult(available=True, collected=True, message="ok")
        self.assertEqual(validate_artifact_collection_result(res), ())

    def test_wrong_typed_available(self) -> None:
        res = ArtifactCollectionResult(available="yes")  # type: ignore[arg-type]
        errs = validate_artifact_collection_result(res)
        self.assertTrue(any("available must be a bool" in e for e in errs))

    def test_wrong_typed_message(self) -> None:
        res = ArtifactCollectionResult(available=True, message=123)  # type: ignore[arg-type]
        errs = validate_artifact_collection_result(res)
        self.assertTrue(any("message must be a string" in e for e in errs))

    def test_malformed_artifact_refs_reported(self) -> None:
        res = ArtifactCollectionResult(available=True, artifact_refs=(object(),))  # type: ignore[arg-type]
        errs = validate_artifact_collection_result(res)
        self.assertTrue(any("artifact_refs" in e for e in errs))

    def test_malformed_previews_reported(self) -> None:
        res = ArtifactCollectionResult(available=True, previews=(object(),))  # type: ignore[arg-type]
        errs = validate_artifact_collection_result(res)
        self.assertTrue(any("previews" in e for e in errs))

    def test_non_result_input(self) -> None:
        self.assertEqual(
            validate_artifact_collection_result("nope"),  # type: ignore[arg-type]
            ("result must be an ArtifactCollectionResult instance",),
        )


# ---------------------------------------------------------------------------
# Preview no-op
# ---------------------------------------------------------------------------

class PreviewTests(unittest.TestCase):
    def test_preview_is_no_op(self) -> None:
        res = preview_artifact_collection(_request(source_name="memory"))
        self.assertFalse(res.available)
        self.assertFalse(res.collected)
        self.assertEqual(res.source_name, "memory")
        self.assertEqual(validate_artifact_collection_result(res), ())

    def test_preview_deterministic(self) -> None:
        self.assertEqual(
            preview_artifact_collection(_request()).to_dict(),
            preview_artifact_collection(_request()).to_dict(),
        )


# ---------------------------------------------------------------------------
# End-to-end with the fake source (no side effects)
# ---------------------------------------------------------------------------

class FakeSourceFlowTests(unittest.TestCase):
    def test_request_validate_serialize_collect(self) -> None:
        req = _request()
        self.assertEqual(validate_artifact_collection_request(req), ())
        json.dumps(req.to_dict())
        result = _FakeSource().collect_artifacts(req)
        self.assertEqual(validate_artifact_collection_result(result), ())
        json.dumps(result.to_dict())
        self.assertTrue(result.collected)


if __name__ == "__main__":
    unittest.main()
