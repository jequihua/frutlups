"""Prompt-delivery and artifact-collection contracts (M012-S03, M012-S04).

Defines small, provider-neutral structural protocols — ``PromptSink``
(M012-S03) for delivering a rendered prompt to a logical role, and
``ArtifactSource`` (M012-S04) for collecting a logical agent's output
artifacts — plus the immutable request/result data shapes a future adapter
would use. This module is a *contract only*: nothing here delivers a prompt,
collects artifacts, dispatches an agent, calls a provider API, watches a
process, polls a service, touches the filesystem/network, or reads/mutates
memory. The core loop continues to work with prompt files, self-reports,
review reports, and verdict records as repository artifacts.

Serialization follows the placeholder posture accepted in reviews 055/056:
``to_dict()`` never raises for constructible-but-malformed fields, rendering
them as JSON-safe placeholders, while the ``validate_*`` helpers report those
fields as deterministic errors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from frutlups.agents import (
    AgentProfile,
    AgentRole,
    _coerce_plain,
    _coerce_plain_sequence,
    validate_agent_profile,
)


@dataclass(frozen=True)
class PromptDeliveryRequest:
    """Immutable description of a prompt to deliver to a logical role.

    A request must carry at least one clear prompt locator: ``prompt_path`` or
    ``prompt_id``. ``prompt_content`` / ``content_preview`` are optional (a
    path-only request is valid). ``profile`` is an optional :class:`AgentProfile`.
    ``metadata`` and ``notes`` are ordered descriptive tuples. Nothing here is
    executed; this is data for a future sink.
    """

    target_role: AgentRole
    prompt_path: str = ""
    prompt_id: str = ""
    prompt_content: str = ""
    content_preview: str = ""
    profile: AgentProfile | None = None
    sink_name: str = ""
    sink_kind: str = ""
    metadata: tuple[str, ...] = field(default=())
    notes: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, object]:
        return {
            "target_role": (
                self.target_role.value
                if isinstance(self.target_role, AgentRole)
                else _coerce_plain(self.target_role)
            ),
            "prompt_path": _coerce_plain(self.prompt_path),
            "prompt_id": _coerce_plain(self.prompt_id),
            "prompt_content": _coerce_plain(self.prompt_content),
            "content_preview": _coerce_plain(self.content_preview),
            "profile": _profile_to_plain(self.profile),
            "sink_name": _coerce_plain(self.sink_name),
            "sink_kind": _coerce_plain(self.sink_kind),
            "metadata": _coerce_plain_sequence(self.metadata),
            "notes": _coerce_plain_sequence(self.notes),
        }


@dataclass(frozen=True)
class PromptDeliveryResult:
    """Immutable result a sink returns describing a (would-be) delivery.

    ``delivered`` and ``accepted`` are booleans, ``message`` is a human-readable
    summary, ``artifact_refs`` lists any produced artifact references, and
    ``sink_name`` identifies the sink. This is data only; constructing it does
    not perform or imply any side effect.
    """

    delivered: bool
    accepted: bool = False
    message: str = ""
    artifact_refs: tuple[str, ...] = field(default=())
    sink_name: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "delivered": (
                self.delivered
                if isinstance(self.delivered, bool)
                else _coerce_plain(self.delivered)
            ),
            "accepted": (
                self.accepted if isinstance(self.accepted, bool) else _coerce_plain(self.accepted)
            ),
            "message": _coerce_plain(self.message),
            "artifact_refs": _coerce_plain_sequence(self.artifact_refs),
            "sink_name": _coerce_plain(self.sink_name),
        }


@runtime_checkable
class PromptSink(Protocol):
    """Structural contract for a future prompt-delivery target.

    An implementation accepts a :class:`PromptDeliveryRequest` and returns a
    :class:`PromptDeliveryResult`. This package defines the shape only; it ships
    no real sink. ``@runtime_checkable`` lets tests assert that an object
    structurally satisfies the protocol (it checks for ``deliver_prompt``; it
    does not verify the signature).
    """

    def deliver_prompt(
        self, request: PromptDeliveryRequest
    ) -> PromptDeliveryResult:  # pragma: no cover - protocol stub
        ...


def _profile_to_plain(profile: object) -> object:
    """Serialize an optional profile field JSON-safely.

    ``None`` stays ``None``; an :class:`AgentProfile` uses its own ``to_dict``;
    anything else becomes a plain-Python placeholder.
    """

    if profile is None:
        return None
    if isinstance(profile, AgentProfile):
        return profile.to_dict()
    return _coerce_plain(profile)


def _validate_string_sequence(field_name: str, value: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, (tuple, list)):
        errors.append(f"{field_name} must be a tuple or list of non-empty strings")
        return errors
    for index, entry in enumerate(value):
        if not isinstance(entry, str) or not entry.strip():
            errors.append(f"{field_name}[{index}] must be a non-empty string")
    return errors


def validate_prompt_delivery_request(
    request: PromptDeliveryRequest,
) -> tuple[str, ...]:
    """Return deterministic validation errors for ``request`` (empty when valid).

    Pure and read-only; never raises for constructible inputs. Requires a
    ``target_role`` that is an :class:`AgentRole` and at least one clear prompt
    locator (``prompt_path`` or ``prompt_id``). String fields must be strings;
    an optional ``profile`` must be ``None`` or a valid :class:`AgentProfile`;
    ``metadata``/``notes`` must be tuples/lists of non-empty strings.
    """

    if not isinstance(request, PromptDeliveryRequest):
        return ("request must be a PromptDeliveryRequest instance",)

    errors: list[str] = []
    if not isinstance(request.target_role, AgentRole):
        errors.append("target_role must be an AgentRole")

    for field_name in (
        "prompt_path",
        "prompt_id",
        "prompt_content",
        "content_preview",
        "sink_name",
        "sink_kind",
    ):
        if not isinstance(getattr(request, field_name), str):
            errors.append(f"{field_name} must be a string")

    path_ok = isinstance(request.prompt_path, str) and request.prompt_path.strip()
    id_ok = isinstance(request.prompt_id, str) and request.prompt_id.strip()
    if not (path_ok or id_ok):
        errors.append("request must include a prompt locator (prompt_path or prompt_id)")

    if request.profile is not None:
        if not isinstance(request.profile, AgentProfile):
            errors.append("profile must be an AgentProfile or None")
        else:
            for profile_error in validate_agent_profile(request.profile):
                errors.append(f"profile: {profile_error}")

    for field_name in ("metadata", "notes"):
        errors.extend(_validate_string_sequence(field_name, getattr(request, field_name)))

    return tuple(errors)


def validate_prompt_delivery_result(
    result: PromptDeliveryResult,
) -> tuple[str, ...]:
    """Return deterministic validation errors for ``result`` (empty when valid).

    ``delivered`` and ``accepted`` must be booleans, ``message`` and
    ``sink_name`` strings, and ``artifact_refs`` a tuple/list of non-empty
    strings. Never raises for constructible inputs.
    """

    if not isinstance(result, PromptDeliveryResult):
        return ("result must be a PromptDeliveryResult instance",)

    errors: list[str] = []
    for field_name in ("delivered", "accepted"):
        if not isinstance(getattr(result, field_name), bool):
            errors.append(f"{field_name} must be a bool")
    for field_name in ("message", "sink_name"):
        if not isinstance(getattr(result, field_name), str):
            errors.append(f"{field_name} must be a string")
    errors.extend(_validate_string_sequence("artifact_refs", result.artifact_refs))
    return tuple(errors)


def preview_prompt_delivery(
    request: PromptDeliveryRequest,
) -> PromptDeliveryResult:
    """Return a read-only, no-op preview result for ``request``.

    This performs **no** delivery: it always returns ``delivered=False`` and
    ``accepted=False``. It exists so callers can demonstrate a sink's expected
    result shape without any side effect. Never raises; never writes; never
    calls anything external.
    """

    sink_name = request.sink_name if isinstance(getattr(request, "sink_name", None), str) else ""
    return PromptDeliveryResult(
        delivered=False,
        accepted=False,
        message="preview only; no prompt was delivered",
        artifact_refs=(),
        sink_name=sink_name,
    )


# ---------------------------------------------------------------------------
# M012-S04: artifact-collection contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArtifactCollectionRequest:
    """Immutable description of artifacts to collect from a logical role.

    A request must carry at least one clear collection locator: an entry in
    ``expected_artifacts`` (paths/ids/refs the source should produce), or a
    prompt locator (``prompt_path`` / ``prompt_id``) paired with a non-empty
    ``source_name``. ``profile`` is an optional :class:`AgentProfile`.
    ``metadata`` and ``notes`` are ordered descriptive tuples. Nothing here is
    executed; this is data for a future source.
    """

    source_role: AgentRole
    prompt_path: str = ""
    prompt_id: str = ""
    expected_artifacts: tuple[str, ...] = field(default=())
    profile: AgentProfile | None = None
    source_name: str = ""
    source_kind: str = ""
    metadata: tuple[str, ...] = field(default=())
    notes: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, object]:
        return {
            "source_role": (
                self.source_role.value
                if isinstance(self.source_role, AgentRole)
                else _coerce_plain(self.source_role)
            ),
            "prompt_path": _coerce_plain(self.prompt_path),
            "prompt_id": _coerce_plain(self.prompt_id),
            "expected_artifacts": _coerce_plain_sequence(self.expected_artifacts),
            "profile": _profile_to_plain(self.profile),
            "source_name": _coerce_plain(self.source_name),
            "source_kind": _coerce_plain(self.source_kind),
            "metadata": _coerce_plain_sequence(self.metadata),
            "notes": _coerce_plain_sequence(self.notes),
        }


@dataclass(frozen=True)
class ArtifactCollectionResult:
    """Immutable result a source returns describing a (would-be) collection.

    ``available`` and ``collected`` are booleans, ``message`` is a
    human-readable summary, ``artifact_refs`` lists collected artifact
    references, ``previews`` holds optional plain-string previews/summaries, and
    ``source_name`` identifies the source. This is data only; constructing it
    does not perform or imply any side effect.
    """

    available: bool
    collected: bool = False
    message: str = ""
    artifact_refs: tuple[str, ...] = field(default=())
    previews: tuple[str, ...] = field(default=())
    source_name: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "available": (
                self.available
                if isinstance(self.available, bool)
                else _coerce_plain(self.available)
            ),
            "collected": (
                self.collected
                if isinstance(self.collected, bool)
                else _coerce_plain(self.collected)
            ),
            "message": _coerce_plain(self.message),
            "artifact_refs": _coerce_plain_sequence(self.artifact_refs),
            "previews": _coerce_plain_sequence(self.previews),
            "source_name": _coerce_plain(self.source_name),
        }


@runtime_checkable
class ArtifactSource(Protocol):
    """Structural contract for a future artifact-collection source.

    An implementation accepts an :class:`ArtifactCollectionRequest` and returns
    an :class:`ArtifactCollectionResult`. This package defines the shape only;
    it ships no real source. ``@runtime_checkable`` lets tests assert that an
    object structurally satisfies the protocol (it checks for
    ``collect_artifacts``; it does not verify the signature).
    """

    def collect_artifacts(
        self, request: ArtifactCollectionRequest
    ) -> ArtifactCollectionResult:  # pragma: no cover - protocol stub
        ...


def validate_artifact_collection_request(
    request: ArtifactCollectionRequest,
) -> tuple[str, ...]:
    """Return deterministic validation errors for ``request`` (empty when valid).

    Pure and read-only; never raises for constructible inputs. Requires a
    ``source_role`` that is an :class:`AgentRole` and at least one clear
    collection locator: a non-empty ``expected_artifacts`` entry, or a prompt
    locator (``prompt_path``/``prompt_id``) together with a non-empty
    ``source_name``. String fields must be strings; an optional ``profile`` must
    be ``None`` or a valid :class:`AgentProfile`; ``expected_artifacts`` /
    ``metadata`` / ``notes`` must be tuples/lists of non-empty strings.
    """

    if not isinstance(request, ArtifactCollectionRequest):
        return ("request must be an ArtifactCollectionRequest instance",)

    errors: list[str] = []
    if not isinstance(request.source_role, AgentRole):
        errors.append("source_role must be an AgentRole")

    for field_name in (
        "prompt_path",
        "prompt_id",
        "source_name",
        "source_kind",
    ):
        if not isinstance(getattr(request, field_name), str):
            errors.append(f"{field_name} must be a string")

    for field_name in ("expected_artifacts", "metadata", "notes"):
        errors.extend(_validate_string_sequence(field_name, getattr(request, field_name)))

    has_expected = isinstance(request.expected_artifacts, (tuple, list)) and any(
        isinstance(entry, str) and entry.strip() for entry in request.expected_artifacts
    )
    prompt_ok = (isinstance(request.prompt_path, str) and request.prompt_path.strip()) or (
        isinstance(request.prompt_id, str) and request.prompt_id.strip()
    )
    source_named = isinstance(request.source_name, str) and request.source_name.strip()
    if not (has_expected or (prompt_ok and source_named)):
        errors.append(
            "request must include a collection locator: an expected artifact "
            "reference, or a prompt locator with a source_name"
        )

    if request.profile is not None:
        if not isinstance(request.profile, AgentProfile):
            errors.append("profile must be an AgentProfile or None")
        else:
            for profile_error in validate_agent_profile(request.profile):
                errors.append(f"profile: {profile_error}")

    return tuple(errors)


def validate_artifact_collection_result(
    result: ArtifactCollectionResult,
) -> tuple[str, ...]:
    """Return deterministic validation errors for ``result`` (empty when valid).

    ``available`` and ``collected`` must be booleans, ``message`` and
    ``source_name`` strings, and ``artifact_refs`` / ``previews`` tuples/lists
    of non-empty strings. Never raises for constructible inputs.
    """

    if not isinstance(result, ArtifactCollectionResult):
        return ("result must be an ArtifactCollectionResult instance",)

    errors: list[str] = []
    for field_name in ("available", "collected"):
        if not isinstance(getattr(result, field_name), bool):
            errors.append(f"{field_name} must be a bool")
    for field_name in ("message", "source_name"):
        if not isinstance(getattr(result, field_name), str):
            errors.append(f"{field_name} must be a string")
    for field_name in ("artifact_refs", "previews"):
        errors.extend(_validate_string_sequence(field_name, getattr(result, field_name)))
    return tuple(errors)


def preview_artifact_collection(
    request: ArtifactCollectionRequest,
) -> ArtifactCollectionResult:
    """Return a read-only, no-op preview result for ``request``.

    This performs **no** collection: it always returns ``available=False`` and
    ``collected=False``. It exists so callers can demonstrate a source's
    expected result shape without any side effect. Never raises; never reads or
    writes; never calls anything external.
    """

    source_name = (
        request.source_name if isinstance(getattr(request, "source_name", None), str) else ""
    )
    return ArtifactCollectionResult(
        available=False,
        collected=False,
        message="preview only; no artifacts were collected",
        artifact_refs=(),
        previews=(),
        source_name=source_name,
    )
