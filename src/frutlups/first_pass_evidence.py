"""First-pass evidence preservation model and collector (M013-S04).

A small, pure, deterministic model plus a read-only collector that lets a future
second pass *cite* first-pass artifacts without overwriting them. "Preserve"
means: keep first-pass evidence addressable through stable repository-relative
paths, capture enough metadata (byte size, SHA-256 digest) to detect drift, and
record diagnostics for missing/unreadable files — never copy, delete, rename,
rewrite, normalize, or otherwise mutate the first-pass artifacts.

This is a foundation slice. The collector only *reads* repository artifacts and
computes digests; it writes nothing, embeds no file bodies, does not change
``frutlups status`` / ``frutlups next`` frontier inference, adds no CLI command,
and never touches memory. ``to_dict()`` payloads are JSON-safe even for malformed
constructible inputs.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath

from frutlups.second_pass import SLICE_ID_RE


class PreservedArtifactKind(StrEnum):
    """Kind of a preserved first-pass artifact."""

    CODING_PROMPT = "coding_prompt"
    REVIEW_PROMPT = "review_prompt"
    SELF_REPORT = "self_report"
    REVIEW_REPORT = "review_report"
    VERDICT_RECORD = "verdict_record"
    KNOWN_DIVERGENCES = "known_divergences"
    OTHER = "other"


# Deterministic display/sort order for artifact kinds within a bundle.
_KIND_ORDER: dict[PreservedArtifactKind, int] = {
    kind: index for index, kind in enumerate(PreservedArtifactKind)
}

_CODING_PROMPT_DIR = "prompts/for_coding_agent"
_REVIEW_PROMPT_DIR = "prompts/for_review_agent"
_REVIEWS_DIR = "05_governance/reviews"
_KNOWN_DIVERGENCES_PATH = "05_governance/known_divergences.md"

# Governance review filename suffix -> artifact kind.
_REVIEWS_SUFFIX_KINDS: tuple[tuple[str, PreservedArtifactKind], ...] = (
    ("_self_report.md", PreservedArtifactKind.SELF_REPORT),
    ("_review_report.md", PreservedArtifactKind.REVIEW_REPORT),
    ("_verdict_record.md", PreservedArtifactKind.VERDICT_RECORD),
)

# Artifact kinds expected for every first-pass slice, in deterministic order.
# A second-pass reader uses these to tell "absent" from "not represented".
_EXPECTED_SLICE_KINDS: tuple[PreservedArtifactKind, ...] = (
    PreservedArtifactKind.CODING_PROMPT,
    PreservedArtifactKind.REVIEW_PROMPT,
    PreservedArtifactKind.SELF_REPORT,
    PreservedArtifactKind.REVIEW_REPORT,
    PreservedArtifactKind.VERDICT_RECORD,
)


def _looks_absolute(path: str) -> bool:
    """Return ``True`` when ``path`` looks absolute on any platform."""

    if path.startswith(("/", "\\")):
        return True
    return PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute()


def _plain(value: object) -> object:
    """Return a JSON-safe plain representation of a scalar field.

    Mirrors :func:`frutlups.second_pass._plain`: enum values become their string
    value; ``None``/``str``/``int``/``float``/``bool`` pass through; anything
    else is converted to ``str`` so payloads survive ``json.dumps``.
    """

    if isinstance(value, StrEnum):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _plain_sequence(value: object) -> list[object]:
    """Return a JSON-safe list of plain scalars for a sequence field."""

    if isinstance(value, (tuple, list)):
        return [_plain(entry) for entry in value]
    return [_plain(value)]


def _plain_items(value: object) -> list[object]:
    """Return a JSON-safe list for a sequence of model items.

    Each entry exposing a callable ``to_dict()`` is serialized through it
    (guarded), else falls back to :func:`_plain`. Non-sequence inputs become a
    one-element list.
    """

    entries = list(value) if isinstance(value, (tuple, list)) else [value]
    out: list[object] = []
    for entry in entries:
        to_dict = getattr(entry, "to_dict", None)
        if callable(to_dict):
            try:
                out.append(to_dict())
                continue
            except Exception:
                pass
        out.append(_plain(entry))
    return out


def _slice_token(slice_id: str) -> str:
    """Return the filename token for a slice id, e.g. ``M013-S01`` -> ``m013_s01``."""

    return slice_id.strip().lower().replace("-", "_")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreservedArtifact:
    """A reference to one preserved first-pass artifact (never its contents).

    ``path`` is a stable repository-relative path using forward slashes.
    ``size_bytes`` and ``sha256`` are populated when the file is readable, else
    ``None`` with a diagnostic. ``exists`` reflects whether the file was found
    and read.
    """

    kind: PreservedArtifactKind
    path: str
    source_slice_id: str = ""
    size_bytes: int | None = None
    sha256: str | None = None
    exists: bool = False
    diagnostics: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": _plain(self.kind),
            "path": _plain(self.path),
            "source_slice_id": _plain(self.source_slice_id),
            "size_bytes": _plain(self.size_bytes),
            "sha256": _plain(self.sha256),
            "exists": _plain(self.exists),
            "diagnostics": _plain_sequence(self.diagnostics),
        }


@dataclass(frozen=True)
class SlicePreservation:
    """Preserved first-pass artifacts grouped for one slice."""

    slice_id: str
    artifacts: tuple[PreservedArtifact, ...] = field(default=())
    diagnostics: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, object]:
        return {
            "slice_id": _plain(self.slice_id),
            "artifacts": _plain_items(self.artifacts),
            "diagnostics": _plain_sequence(self.diagnostics),
        }


@dataclass(frozen=True)
class FirstPassManifest:
    """Overall first-pass preservation manifest for a baseline slice set.

    ``baseline_slice_ids`` are the accepted slice IDs preserved; ``slices`` are
    the per-slice preservation bundles in baseline order; ``governance_artifacts``
    are shared governance files (e.g. ``known_divergences.md``).
    """

    baseline_slice_ids: tuple[str, ...] = field(default=())
    slices: tuple[SlicePreservation, ...] = field(default=())
    governance_artifacts: tuple[PreservedArtifact, ...] = field(default=())
    diagnostics: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, object]:
        return {
            "baseline_slice_ids": _plain_sequence(self.baseline_slice_ids),
            "slices": _plain_items(self.slices),
            "governance_artifacts": _plain_items(self.governance_artifacts),
            "diagnostics": _plain_sequence(self.diagnostics),
        }


# ---------------------------------------------------------------------------
# Validation (deterministic; never raises for constructible inputs)
# ---------------------------------------------------------------------------


def validate_preserved_artifact(artifact: PreservedArtifact) -> tuple[str, ...]:
    """Return deterministic validation errors for ``artifact`` (empty if valid)."""

    if not isinstance(artifact, PreservedArtifact):
        return ("artifact must be a PreservedArtifact instance",)
    errors: list[str] = []
    if not isinstance(artifact.kind, PreservedArtifactKind):
        errors.append("kind must be a PreservedArtifactKind")
    if not isinstance(artifact.path, str) or not artifact.path.strip():
        errors.append("path must be a non-empty string")
    elif _looks_absolute(artifact.path):
        errors.append("path must be a relative repository path")
    if not isinstance(artifact.source_slice_id, str):
        errors.append("source_slice_id must be a string")
    elif artifact.source_slice_id and not SLICE_ID_RE.match(artifact.source_slice_id):
        errors.append("source_slice_id must be empty or look like 'M013-S01'")
    if artifact.size_bytes is not None and (
        isinstance(artifact.size_bytes, bool)
        or not isinstance(artifact.size_bytes, int)
        or artifact.size_bytes < 0
    ):
        errors.append("size_bytes must be None or a non-negative integer")
    if artifact.sha256 is not None and (
        not isinstance(artifact.sha256, str) or not artifact.sha256.strip()
    ):
        errors.append("sha256 must be None or a non-empty string")
    if not isinstance(artifact.exists, bool):
        errors.append("exists must be a bool")
    if not isinstance(artifact.diagnostics, (tuple, list)):
        errors.append("diagnostics must be a tuple or list")
    return tuple(errors)


def validate_slice_preservation(preservation: SlicePreservation) -> tuple[str, ...]:
    """Return deterministic validation errors for ``preservation`` (empty if valid)."""

    if not isinstance(preservation, SlicePreservation):
        return ("preservation must be a SlicePreservation instance",)
    errors: list[str] = []
    if not isinstance(preservation.slice_id, str) or not SLICE_ID_RE.match(
        preservation.slice_id or ""
    ):
        errors.append("slice_id must look like 'M013-S01'")
    if not isinstance(preservation.artifacts, (tuple, list)):
        errors.append("artifacts must be a tuple or list")
    else:
        for index, artifact in enumerate(preservation.artifacts):
            for err in validate_preserved_artifact(artifact):
                errors.append(f"artifacts[{index}]: {err}")
    if not isinstance(preservation.diagnostics, (tuple, list)):
        errors.append("diagnostics must be a tuple or list")
    return tuple(errors)


def validate_first_pass_manifest(manifest: FirstPassManifest) -> tuple[str, ...]:
    """Return deterministic validation errors for ``manifest`` (empty if valid).

    Composes the per-slice and per-artifact validators (prefixed). Never raises
    for constructible inputs.
    """

    if not isinstance(manifest, FirstPassManifest):
        return ("manifest must be a FirstPassManifest instance",)
    errors: list[str] = []

    if not isinstance(manifest.baseline_slice_ids, (tuple, list)):
        errors.append("baseline_slice_ids must be a tuple or list")
    else:
        for index, entry in enumerate(manifest.baseline_slice_ids):
            if not isinstance(entry, str) or not SLICE_ID_RE.match(entry):
                errors.append(f"baseline_slice_ids[{index}] must look like 'M013-S01'")

    if not isinstance(manifest.slices, (tuple, list)):
        errors.append("slices must be a tuple or list")
    else:
        for index, preservation in enumerate(manifest.slices):
            for err in validate_slice_preservation(preservation):
                errors.append(f"slices[{index}]: {err}")

    if not isinstance(manifest.governance_artifacts, (tuple, list)):
        errors.append("governance_artifacts must be a tuple or list")
    else:
        for index, artifact in enumerate(manifest.governance_artifacts):
            for err in validate_preserved_artifact(artifact):
                errors.append(f"governance_artifacts[{index}]: {err}")

    if not isinstance(manifest.diagnostics, (tuple, list)):
        errors.append("diagnostics must be a tuple or list")

    return tuple(errors)


# ---------------------------------------------------------------------------
# Read-only collector
# ---------------------------------------------------------------------------


def _preserve_file(
    root: Path, rel_path: str, kind: PreservedArtifactKind, slice_id: str
) -> PreservedArtifact:
    """Build a :class:`PreservedArtifact` for ``rel_path`` under ``root`` (read-only).

    Computes byte size and SHA-256 from the file exactly as stored on disk. A
    missing or unreadable file yields ``exists=False`` with a diagnostic and no
    digest. Never writes; never raises.
    """

    target = root / rel_path
    if not target.is_file():
        return PreservedArtifact(
            kind=kind,
            path=rel_path,
            source_slice_id=slice_id,
            exists=False,
            diagnostics=(f"artifact not found: {rel_path}",),
        )
    try:
        data = target.read_bytes()
    except OSError as exc:
        return PreservedArtifact(
            kind=kind,
            path=rel_path,
            source_slice_id=slice_id,
            exists=False,
            diagnostics=(f"could not read {rel_path}: {exc}",),
        )
    return PreservedArtifact(
        kind=kind,
        path=rel_path,
        source_slice_id=slice_id,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        exists=True,
        diagnostics=(),
    )


def _collect_dir_matches(
    root: Path, rel_dir: str, token: str, kind: PreservedArtifactKind, slice_id: str
) -> list[PreservedArtifact]:
    """Preserve every ``*.md`` file in ``rel_dir`` whose name contains ``_token_``.

    The slice token is matched as ``_m013_s01_`` so ``s01`` does not match
    ``s010``. Results are sorted by path for determinism.
    """

    directory = root / rel_dir
    if not directory.is_dir():
        return []
    needle = f"_{token}_"
    out: list[PreservedArtifact] = []
    for candidate in sorted(directory.glob("*.md")):
        if needle in candidate.name:
            out.append(_preserve_file(root, f"{rel_dir}/{candidate.name}", kind, slice_id))
    return out


def _collect_reviews_matches(root: Path, token: str, slice_id: str) -> list[PreservedArtifact]:
    """Preserve self-report / review-report / verdict-record files for a slice token.

    Matches governance review files named ``<token>_*<suffix>``. Results are
    sorted by (kind order, path) for determinism.
    """

    directory = root / _REVIEWS_DIR
    if not directory.is_dir():
        return []
    prefix = f"{token}_"
    out: list[PreservedArtifact] = []
    for candidate in directory.glob("*.md"):
        name = candidate.name
        if not name.startswith(prefix):
            continue
        for suffix, kind in _REVIEWS_SUFFIX_KINDS:
            if name.endswith(suffix):
                out.append(_preserve_file(root, f"{_REVIEWS_DIR}/{name}", kind, slice_id))
                break
    out.sort(key=lambda a: (_KIND_ORDER.get(a.kind, 99), a.path))
    return out


def collect_slice_preservation(root: Path | str, slice_id: str) -> SlicePreservation:
    """Collect preserved first-pass artifacts for a single slice (read-only).

    Scans coding prompts, review prompts, and governance review files for the
    slice token. Missing artifacts produce diagnostics, not exceptions. Never
    writes; never raises for constructible inputs.
    """

    root_path = root if isinstance(root, Path) else Path(root)
    if not isinstance(slice_id, str) or not SLICE_ID_RE.match(slice_id or ""):
        return SlicePreservation(
            slice_id=slice_id if isinstance(slice_id, str) else "",
            diagnostics=("slice_id must look like 'M013-S01'",),
        )

    token = _slice_token(slice_id)
    artifacts: list[PreservedArtifact] = []
    artifacts.extend(
        _collect_dir_matches(
            root_path,
            _CODING_PROMPT_DIR,
            token,
            PreservedArtifactKind.CODING_PROMPT,
            slice_id,
        )
    )
    artifacts.extend(
        _collect_dir_matches(
            root_path,
            _REVIEW_PROMPT_DIR,
            token,
            PreservedArtifactKind.REVIEW_PROMPT,
            slice_id,
        )
    )
    artifacts.extend(_collect_reviews_matches(root_path, token, slice_id))

    artifacts.sort(key=lambda a: (_KIND_ORDER.get(a.kind, 99), a.path))

    # Surface every expected first-pass artifact kind that is absent, so a later
    # second-pass reader can distinguish "this slice has no review prompt" from
    # "the collector never represented that evidence". Order follows
    # ``_EXPECTED_SLICE_KINDS`` for determinism.
    present_kinds = {a.kind for a in artifacts}
    diagnostics: list[str] = []
    if not artifacts:
        diagnostics.append(f"no first-pass artifacts found for {slice_id}")
    for kind in _EXPECTED_SLICE_KINDS:
        if kind not in present_kinds:
            diagnostics.append(f"missing expected {kind.value} artifact for {slice_id}")
    return SlicePreservation(
        slice_id=slice_id,
        artifacts=tuple(artifacts),
        diagnostics=tuple(diagnostics),
    )


def collect_first_pass_evidence(
    root: Path | str,
    accepted_slice_ids: tuple[str, ...],
    *,
    include_known_divergences: bool = True,
) -> FirstPassManifest:
    """Collect a first-pass preservation manifest for ``accepted_slice_ids`` (read-only).

    Preserves coding prompts, review prompts, self-reports, review reports, and
    verdict records for each slice, plus ``known_divergences.md`` as a shared
    governance artifact. Order follows ``accepted_slice_ids``; malformed IDs are
    skipped with a diagnostic. Computes SHA-256 digests from bytes exactly as
    stored. Read-only: never writes, never mutates the first-pass artifacts, and
    never raises for constructible inputs.
    """

    root_path = root if isinstance(root, Path) else Path(root)
    ids = list(accepted_slice_ids) if isinstance(accepted_slice_ids, (tuple, list)) else []

    slices: list[SlicePreservation] = []
    diagnostics: list[str] = []
    seen: set[str] = set()
    for entry in ids:
        if not isinstance(entry, str) or not SLICE_ID_RE.match(entry):
            diagnostics.append(f"skipped malformed baseline slice id: {entry!r}")
            continue
        key = entry.upper()
        if key in seen:
            continue
        seen.add(key)
        slices.append(collect_slice_preservation(root_path, entry))

    governance: list[PreservedArtifact] = []
    if include_known_divergences:
        governance.append(
            _preserve_file(
                root_path,
                _KNOWN_DIVERGENCES_PATH,
                PreservedArtifactKind.KNOWN_DIVERGENCES,
                "",
            )
        )

    baseline = tuple(s.slice_id for s in slices)
    if not slices:
        diagnostics.append("no baseline slice ids supplied")
    return FirstPassManifest(
        baseline_slice_ids=baseline,
        slices=tuple(slices),
        governance_artifacts=tuple(governance),
        diagnostics=tuple(diagnostics),
    )


def collect_first_pass_evidence_for_project(
    root: Path | str = ".",
    *,
    include_known_divergences: bool = True,
) -> FirstPassManifest:
    """Collect a manifest using the project's accepted slice IDs (read-only).

    Reads the accepted baseline from ``build_status`` (the same artifact-inferred
    accepted set used by ``frutlups status``) and preserves it. Imports the live
    builder lazily. Read-only: never writes, never mutates memory, and never
    changes frontier inference. Never raises for constructible inputs.
    """

    from frutlups.project import build_status

    status = build_status(root)
    return collect_first_pass_evidence(
        root,
        tuple(status.accepted_slice_ids),
        include_known_divergences=include_known_divergences,
    )
