"""Local, provider-neutral file/manual/mock adapters (M012-S05, M012-S06).

Concrete implementations of the M012-S03 ``PromptSink`` and M012-S04
``ArtifactSource`` contracts that are still *not* agent dispatchers:

- Manual adapters (:class:`ManualPromptSink`, :class:`ManualArtifactSource`)
  represent human-mediated work. They are deterministic and side-effect free:
  they return a result with ``delivered``/``collected`` false and a stable
  message, and never touch the filesystem.
- File adapters (:class:`FilePromptSink`, :class:`FileArtifactSource`) perform
  only explicit, caller-directed local filesystem operations under an explicit
  root. They never call a provider, start a process, watch for changes, poll a
  service, scan directories, mutate roadmap/memory state, or hide loop state.
- Mock adapters (:class:`MockPromptSink`, :class:`MockArtifactSource`) are
  deterministic in-memory test doubles. They keep an immutable request trace and
  return configurable canned results, with no filesystem/network/subprocess/
  memory access. They are for tests; they are never authoritative for repository
  state.

All adapters validate the request with the existing delivery/artifact validators
before doing any work; an invalid request yields a deterministic failure result,
never an exception. Paths are resolved and confined to the configured root;
traversal outside the root and unsafe output names are refused without IO.
"""

from __future__ import annotations

import re
from pathlib import Path

from frutlups.agents import AgentProfile, AgentRole
from frutlups.delivery import (
    ArtifactCollectionRequest,
    ArtifactCollectionResult,
    PromptDeliveryRequest,
    PromptDeliveryResult,
    validate_artifact_collection_request,
    validate_prompt_delivery_request,
)

_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _is_empty_root(root: object) -> bool:
    """Return ``True`` when ``root`` is an empty/whitespace-only string or None.

    File adapters require an explicit root. ``Path("")`` silently means the
    current working directory, so an empty/whitespace string must be rejected
    before any filesystem IO rather than defaulting to CWD.
    """

    if root is None:
        return True
    if isinstance(root, str) and not root.strip():
        return True
    return False


def _is_within(child: Path, parent: Path) -> bool:
    """Return ``True`` when ``child`` is at or under ``parent``."""

    try:
        return child.is_relative_to(parent)
    except (AttributeError, ValueError):
        try:
            child.relative_to(parent)
            return True
        except ValueError:
            return False


def _role_value(role: object) -> str:
    return role.value if isinstance(role, AgentRole) else str(role)


def _failed_delivery(sink_name: str, message: str) -> PromptDeliveryResult:
    return PromptDeliveryResult(
        delivered=False,
        accepted=False,
        message=message,
        artifact_refs=(),
        sink_name=sink_name,
    )


def _failed_collection(source_name: str, message: str) -> ArtifactCollectionResult:
    return ArtifactCollectionResult(
        available=False,
        collected=False,
        message=message,
        artifact_refs=(),
        previews=(),
        source_name=source_name,
    )


# ---------------------------------------------------------------------------
# Manual adapters (deterministic, side-effect free)
# ---------------------------------------------------------------------------


class ManualPromptSink:
    """A :class:`~frutlups.delivery.PromptSink` for human-mediated handoff.

    Performs no IO: it always returns ``delivered=False``/``accepted=False`` with
    a stable human-readable message instructing a person to deliver the prompt.
    """

    def __init__(self, sink_name: str = "manual", instructions: str = "") -> None:
        self.sink_name = sink_name
        self.instructions = instructions

    def deliver_prompt(self, request: PromptDeliveryRequest) -> PromptDeliveryResult:
        errors = validate_prompt_delivery_request(request)
        if errors:
            return _failed_delivery(self.sink_name, "invalid request: " + "; ".join(errors))
        message = (
            f"manual handoff: deliver this prompt to the "
            f"{_role_value(request.target_role)} role by hand"
        )
        if self.instructions:
            message += f" — {self.instructions}"
        return _failed_delivery(self.sink_name, message)


class ManualArtifactSource:
    """An :class:`~frutlups.delivery.ArtifactSource` for human-mediated collection.

    Performs no IO: it always returns ``available=False``/``collected=False``
    with a stable human-readable message instructing a person to gather the
    artifacts.
    """

    def __init__(self, source_name: str = "manual", instructions: str = "") -> None:
        self.source_name = source_name
        self.instructions = instructions

    def collect_artifacts(self, request: ArtifactCollectionRequest) -> ArtifactCollectionResult:
        errors = validate_artifact_collection_request(request)
        if errors:
            return _failed_collection(self.source_name, "invalid request: " + "; ".join(errors))
        message = (
            f"manual collection: gather artifacts for the "
            f"{_role_value(request.source_role)} role by hand"
        )
        if self.instructions:
            message += f" — {self.instructions}"
        return _failed_collection(self.source_name, message)


# ---------------------------------------------------------------------------
# File adapters (explicit, caller-directed local IO under a configured root)
# ---------------------------------------------------------------------------


def _safe_handoff_name(request: PromptDeliveryRequest) -> str:
    """Derive a safe, separator-free handoff filename, or ``""`` if unsafe.

    Prefers ``prompt_id``, else the basename of ``prompt_path``. Rejects empty
    names, names containing ``..`` or path separators, and names that do not
    match the conservative safe-name pattern. Appends ``.md`` when missing.
    """

    raw = ""
    if isinstance(request.prompt_id, str) and request.prompt_id.strip():
        raw = request.prompt_id.strip()
    elif isinstance(request.prompt_path, str) and request.prompt_path.strip():
        raw = Path(request.prompt_path).name.strip()
    if not raw:
        return ""
    if ".." in raw or "/" in raw or "\\" in raw:
        return ""
    if not _SAFE_NAME_RE.match(raw):
        return ""
    return raw if raw.endswith(".md") else f"{raw}.md"


def _render_handoff(request: PromptDeliveryRequest) -> str:
    """Render a deterministic UTF-8 prompt-handoff document for ``request``."""

    lines: list[str] = ["# Prompt Handoff", ""]
    lines.append(f"- Target role: {_role_value(request.target_role)}")
    if request.prompt_path:
        lines.append(f"- Prompt path: {request.prompt_path}")
    if request.prompt_id:
        lines.append(f"- Prompt id: {request.prompt_id}")
    if request.sink_name:
        lines.append(f"- Sink: {request.sink_name}")
    if request.sink_kind:
        lines.append(f"- Sink kind: {request.sink_kind}")
    if isinstance(request.profile, AgentProfile):
        profile = request.profile
        family = f" ({profile.family})" if profile.family else ""
        lines.append(f"- Profile: {profile.label}{family}")
    if request.metadata:
        lines.append("- Metadata:")
        for entry in request.metadata:
            lines.append(f"  - {entry}")
    if request.notes:
        lines.append("- Notes:")
        for entry in request.notes:
            lines.append(f"  - {entry}")
    lines.append("")
    lines.append("## Prompt Content")
    lines.append("")
    content = (
        request.prompt_content
        or request.content_preview
        or "(no content provided; see prompt path/id)"
    )
    lines.append(content)
    lines.append("")
    return "\n".join(lines)


class FilePromptSink:
    """A :class:`~frutlups.delivery.PromptSink` that writes a local handoff file.

    Writes one deterministic UTF-8 handoff file under the explicit
    ``outbox_root``. It never calls a provider or starts a process. Path safety
    is conservative: the filename is derived from the request, must be
    separator-free and traversal-free, and the resolved target must stay inside
    ``outbox_root``. On success it returns ``delivered=True`` with an artifact
    ref to the written file; otherwise a deterministic failure result.
    """

    def __init__(self, outbox_root: Path | str, sink_name: str = "file") -> None:
        self.outbox_root = outbox_root
        self.sink_name = sink_name

    def deliver_prompt(self, request: PromptDeliveryRequest) -> PromptDeliveryResult:
        if _is_empty_root(self.outbox_root):
            return _failed_delivery(
                self.sink_name,
                "file prompt sink requires an explicit non-empty outbox root",
            )

        errors = validate_prompt_delivery_request(request)
        if errors:
            return _failed_delivery(self.sink_name, "invalid request: " + "; ".join(errors))

        name = _safe_handoff_name(request)
        if not name:
            return _failed_delivery(
                self.sink_name,
                "could not derive a safe handoff filename from the request",
            )

        root = Path(self.outbox_root)
        target = root / name
        try:
            resolved_root = root.resolve(strict=False)
            resolved_target = target.resolve(strict=False)
        except OSError as exc:
            return _failed_delivery(self.sink_name, f"could not resolve handoff path: {exc}")
        if not _is_within(resolved_target, resolved_root):
            return _failed_delivery(self.sink_name, "handoff target escapes the outbox root")

        content = _render_handoff(request)
        try:
            root.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            return _failed_delivery(self.sink_name, f"could not write handoff file: {exc}")

        return PromptDeliveryResult(
            delivered=True,
            accepted=False,
            message=f"wrote prompt handoff to {target}",
            artifact_refs=(str(target),),
            sink_name=self.sink_name,
        )


class FileArtifactSource:
    """An :class:`~frutlups.delivery.ArtifactSource` reading explicit local files.

    Reads only the explicit ``expected_artifacts`` from the request, resolving
    relative entries under the configured ``root``. It does not scan
    directories, watch for changes, poll, infer paths, or write anything.

    Collection rule: ``available`` is ``True`` when at least one requested
    artifact exists inside the root; ``collected`` is ``True`` only when every
    requested artifact was found inside the root (none missing, none rejected for
    traversal). When ``preview_chars`` is positive, a bounded UTF-8 preview of
    each found, non-empty file is included.
    """

    def __init__(
        self,
        root: Path | str,
        source_name: str = "file",
        preview_chars: int = 0,
    ) -> None:
        self.root = root
        self.source_name = source_name
        self.preview_chars = preview_chars

    def collect_artifacts(self, request: ArtifactCollectionRequest) -> ArtifactCollectionResult:
        if _is_empty_root(self.root):
            return _failed_collection(
                self.source_name,
                "file artifact source requires an explicit non-empty root",
            )

        errors = validate_artifact_collection_request(request)
        if errors:
            return _failed_collection(self.source_name, "invalid request: " + "; ".join(errors))

        root = Path(self.root)
        try:
            resolved_root = root.resolve(strict=False)
        except OSError as exc:
            return _failed_collection(self.source_name, f"could not resolve root: {exc}")

        found: list[str] = []
        missing: list[str] = []
        rejected: list[str] = []
        previews: list[str] = []

        for ref in request.expected_artifacts:
            if not isinstance(ref, str):
                continue
            candidate = Path(ref)
            target = candidate if candidate.is_absolute() else root / candidate
            try:
                resolved = target.resolve(strict=False)
            except OSError:
                missing.append(ref)
                continue
            if not _is_within(resolved, resolved_root):
                rejected.append(ref)
                continue
            if resolved.is_file():
                found.append(ref)
                if self.preview_chars and self.preview_chars > 0:
                    try:
                        text = resolved.read_text(encoding="utf-8")
                    except OSError:
                        text = ""
                    if text:
                        previews.append(text[: self.preview_chars])
            else:
                missing.append(ref)

        available = len(found) > 0
        collected = available and not missing and not rejected

        parts = [f"collected {len(found)} of {len(request.expected_artifacts)} artifact(s)"]
        if missing:
            parts.append("missing: " + ", ".join(missing))
        if rejected:
            parts.append("rejected (outside root): " + ", ".join(rejected))

        return ArtifactCollectionResult(
            available=available,
            collected=collected,
            message="; ".join(parts),
            artifact_refs=tuple(found),
            previews=tuple(previews),
            source_name=self.source_name,
        )


# ---------------------------------------------------------------------------
# Mock adapters (deterministic, in-memory test doubles)
# ---------------------------------------------------------------------------


class MockPromptSink:
    """In-memory :class:`~frutlups.delivery.PromptSink` test double.

    Records each *valid* :class:`PromptDeliveryRequest` and returns a
    deterministic, configurable :class:`PromptDeliveryResult`. Invalid requests
    return a failure result and are **not** recorded in the success trace.
    Canned ``artifact_refs`` are copied into a tuple at construction, so later
    mutation of a caller-supplied list cannot alter results or recorded
    evidence. The request trace is exposed as an immutable tuple via
    :attr:`requests`. No filesystem/network/subprocess/memory access.
    """

    def __init__(
        self,
        *,
        delivered: bool = True,
        accepted: bool = True,
        message: str = "mock delivery",
        artifact_refs: tuple[str, ...] = (),
        sink_name: str = "mock",
    ) -> None:
        self.delivered = bool(delivered)
        self.accepted = bool(accepted)
        self.message = message
        self._artifact_refs = tuple(artifact_refs)
        self.sink_name = sink_name
        self._requests: list[PromptDeliveryRequest] = []

    @property
    def requests(self) -> tuple[PromptDeliveryRequest, ...]:
        """Immutable snapshot of recorded valid requests, in call order."""

        return tuple(self._requests)

    def deliver_prompt(self, request: PromptDeliveryRequest) -> PromptDeliveryResult:
        errors = validate_prompt_delivery_request(request)
        if errors:
            return _failed_delivery(self.sink_name, "invalid request: " + "; ".join(errors))
        self._requests.append(request)
        return PromptDeliveryResult(
            delivered=self.delivered,
            accepted=self.accepted,
            message=self.message,
            artifact_refs=self._artifact_refs,
            sink_name=self.sink_name,
        )

    def reset(self) -> None:
        """Clear the in-memory request trace only."""

        self._requests.clear()


class MockArtifactSource:
    """In-memory :class:`~frutlups.delivery.ArtifactSource` test double.

    Records each *valid* :class:`ArtifactCollectionRequest` and returns a
    deterministic, configurable :class:`ArtifactCollectionResult`. Invalid
    requests return a failure result and are **not** recorded in the success
    trace. Canned ``artifact_refs`` and ``previews`` are copied into tuples at
    construction, so later mutation of caller-supplied lists cannot alter
    results or recorded evidence. The request trace is exposed as an immutable
    tuple via :attr:`requests`. No filesystem/network/subprocess/memory access.
    """

    def __init__(
        self,
        *,
        available: bool = True,
        collected: bool = True,
        message: str = "mock collection",
        artifact_refs: tuple[str, ...] = (),
        previews: tuple[str, ...] = (),
        source_name: str = "mock",
    ) -> None:
        self.available = bool(available)
        self.collected = bool(collected)
        self.message = message
        self._artifact_refs = tuple(artifact_refs)
        self._previews = tuple(previews)
        self.source_name = source_name
        self._requests: list[ArtifactCollectionRequest] = []

    @property
    def requests(self) -> tuple[ArtifactCollectionRequest, ...]:
        """Immutable snapshot of recorded valid requests, in call order."""

        return tuple(self._requests)

    def collect_artifacts(self, request: ArtifactCollectionRequest) -> ArtifactCollectionResult:
        errors = validate_artifact_collection_request(request)
        if errors:
            return _failed_collection(self.source_name, "invalid request: " + "; ".join(errors))
        self._requests.append(request)
        return ArtifactCollectionResult(
            available=self.available,
            collected=self.collected,
            message=self.message,
            artifact_refs=self._artifact_refs,
            previews=self._previews,
            source_name=self.source_name,
        )

    def reset(self) -> None:
        """Clear the in-memory request trace only."""

        self._requests.clear()
