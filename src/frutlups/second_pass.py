"""Pass / frontier data model for the future second-pass workflow (M013-S01).

A small, pure, JSON-safe model describing a development *pass* and the roadmap
*frontier* it runs against, so a future second pass can cite first-pass
artifacts without overwriting them.

This is a foundation slice only. It defines typed models and deterministic
validation; it does **not** generate second-pass prompt context, parse
follow-ups/divergences, change how ``frutlups next`` / ``frutlups status``
compute the live frontier, write files, or touch memory. Everything here is
local and pure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath

from frutlups.project import LoopFrontier

MILESTONE_ID_RE = re.compile(r"^M\d+$")
"""A milestone id looks like ``M013``."""

SLICE_ID_RE = re.compile(r"^M\d+-S\d+$")
"""A slice id looks like ``M013-S01``."""


class PassKind(StrEnum):
    """Which development pass is being run."""

    INITIAL = "initial"
    SECOND_PASS = "second_pass"


class FrontierSelectionKind(StrEnum):
    """How the frontier for a pass was chosen."""

    ARTIFACT_INFERRED = "artifact_inferred"
    HUMAN_SELECTED = "human_selected"


def _plain(value: object) -> object:
    """Return a JSON-safe plain representation of a scalar field."""

    if isinstance(value, StrEnum):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _plain_sequence(value: object) -> object:
    if isinstance(value, (tuple, list)):
        return [_plain(entry) for entry in value]
    return _plain(value)


def _is_absolute_path(path: str) -> bool:
    """Return ``True`` when ``path`` looks absolute on any platform."""

    if path.startswith(("/", "\\")):
        return True
    return PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute()


@dataclass(frozen=True)
class PassIdentity:
    """Identity of one development pass.

    ``number`` is a positive integer; ``label`` is a stable non-empty string;
    ``kind`` is a :class:`PassKind`.
    """

    number: int
    label: str
    kind: PassKind = PassKind.INITIAL

    def to_dict(self) -> dict[str, object]:
        return {
            "number": _plain(self.number),
            "label": _plain(self.label),
            "kind": _plain(self.kind),
        }


@dataclass(frozen=True)
class Frontier:
    """A roadmap frontier and how it was selected."""

    milestone_id: str
    slice_id: str
    title: str
    selection_kind: FrontierSelectionKind = FrontierSelectionKind.ARTIFACT_INFERRED

    def to_dict(self) -> dict[str, object]:
        return {
            "milestone_id": _plain(self.milestone_id),
            "slice_id": _plain(self.slice_id),
            "title": _plain(self.title),
            "selection_kind": _plain(self.selection_kind),
        }


@dataclass(frozen=True)
class PassFrontier:
    """A pass plus its chosen frontier, baseline, and justifying evidence.

    ``accepted_baseline_slice_ids`` are the earlier accepted slice ids this pass
    builds on; ``evidence_paths`` are repo-relative paths to artifacts that
    justify the pass/frontier decision.
    """

    identity: PassIdentity
    frontier: Frontier
    accepted_baseline_slice_ids: tuple[str, ...] = field(default=())
    evidence_paths: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, object]:
        return {
            "identity": (
                self.identity.to_dict()
                if isinstance(self.identity, PassIdentity)
                else _plain(self.identity)
            ),
            "frontier": (
                self.frontier.to_dict()
                if isinstance(self.frontier, Frontier)
                else _plain(self.frontier)
            ),
            "accepted_baseline_slice_ids": _plain_sequence(self.accepted_baseline_slice_ids),
            "evidence_paths": _plain_sequence(self.evidence_paths),
        }


def validate_pass_identity(identity: PassIdentity) -> tuple[str, ...]:
    """Return deterministic validation errors for ``identity`` (empty if valid)."""

    if not isinstance(identity, PassIdentity):
        return ("identity must be a PassIdentity instance",)
    errors: list[str] = []
    if isinstance(identity.number, bool) or not isinstance(identity.number, int):
        errors.append("number must be a positive integer")
    elif identity.number <= 0:
        errors.append("number must be a positive integer")
    if not isinstance(identity.label, str) or not identity.label.strip():
        errors.append("label must be a non-empty string")
    if not isinstance(identity.kind, PassKind):
        errors.append("kind must be a PassKind")
    return tuple(errors)


def validate_frontier(frontier: Frontier) -> tuple[str, ...]:
    """Return deterministic validation errors for ``frontier`` (empty if valid)."""

    if not isinstance(frontier, Frontier):
        return ("frontier must be a Frontier instance",)
    errors: list[str] = []

    milestone_ok = isinstance(frontier.milestone_id, str) and bool(
        MILESTONE_ID_RE.match(frontier.milestone_id)
    )
    if not milestone_ok:
        errors.append("milestone_id must look like 'M013'")

    slice_ok = isinstance(frontier.slice_id, str) and bool(SLICE_ID_RE.match(frontier.slice_id))
    if not slice_ok:
        errors.append("slice_id must look like 'M013-S01'")

    if not isinstance(frontier.title, str) or not frontier.title.strip():
        errors.append("title must be a non-empty string")

    if not isinstance(frontier.selection_kind, FrontierSelectionKind):
        errors.append("selection_kind must be a FrontierSelectionKind")

    if milestone_ok and slice_ok:
        if frontier.slice_id.split("-", 1)[0].upper() != frontier.milestone_id.upper():
            errors.append("slice_id must belong to milestone_id")

    return tuple(errors)


def validate_pass_frontier(model: PassFrontier) -> tuple[str, ...]:
    """Return deterministic validation errors for ``model`` (empty if valid).

    Composes :func:`validate_pass_identity` and :func:`validate_frontier`
    (prefixed), then validates the baseline slice ids (each shaped like a slice
    id, no duplicates) and evidence paths (non-empty, relative, not absolute).
    Never raises for constructible inputs.
    """

    if not isinstance(model, PassFrontier):
        return ("model must be a PassFrontier instance",)

    errors: list[str] = []
    for err in validate_pass_identity(model.identity):
        errors.append(f"identity: {err}")
    for err in validate_frontier(model.frontier):
        errors.append(f"frontier: {err}")

    baseline = model.accepted_baseline_slice_ids
    if not isinstance(baseline, (tuple, list)):
        errors.append("accepted_baseline_slice_ids must be a tuple or list")
    else:
        seen: set[str] = set()
        for index, entry in enumerate(baseline):
            if not isinstance(entry, str) or not SLICE_ID_RE.match(entry):
                errors.append(f"accepted_baseline_slice_ids[{index}] must look like 'M013-S01'")
                continue
            key = entry.upper()
            if key in seen:
                errors.append(f"accepted_baseline_slice_ids contains duplicate: {entry}")
            else:
                seen.add(key)

    evidence = model.evidence_paths
    if not isinstance(evidence, (tuple, list)):
        errors.append("evidence_paths must be a tuple or list")
    else:
        for index, entry in enumerate(evidence):
            if not isinstance(entry, str) or not entry.strip():
                errors.append(f"evidence_paths[{index}] must be a non-empty string")
            elif _is_absolute_path(entry):
                errors.append(f"evidence_paths[{index}] must be a relative repository path")

    return tuple(errors)


def _dedupe_slice_ids(ids: object) -> tuple[str, ...]:
    """Return ``ids`` with case-insensitive duplicates removed, order preserved.

    Non-iterable inputs yield an empty tuple; non-string entries are kept
    verbatim (validation reports them) but not deduplicated against strings.
    """

    if not isinstance(ids, (tuple, list)):
        return ()
    result: list[str] = []
    seen: set[str] = set()
    for entry in ids:
        if isinstance(entry, str):
            key = entry.upper()
            if key in seen:
                continue
            seen.add(key)
        result.append(entry)
    return tuple(result)


def build_pass_frontier(
    *,
    pass_number: int,
    label: str,
    frontier: Frontier,
    kind: PassKind = PassKind.INITIAL,
    accepted_baseline_slice_ids: tuple[str, ...] = (),
    evidence_paths: tuple[str, ...] = (),
) -> PassFrontier:
    """Construct a :class:`PassFrontier`, normalizing duplicate baseline ids.

    Duplicate accepted baseline slice ids are removed (case-insensitive,
    first occurrence kept) so the built model is canonical. Pure and
    deterministic; performs no IO and does not validate (call
    :func:`validate_pass_frontier`).
    """

    return PassFrontier(
        identity=PassIdentity(number=pass_number, label=label, kind=kind),
        frontier=frontier,
        accepted_baseline_slice_ids=_dedupe_slice_ids(accepted_baseline_slice_ids),
        evidence_paths=tuple(evidence_paths) if isinstance(evidence_paths, (tuple, list)) else (),
    )


def pass_frontier_from_loop_frontier(
    frontier: LoopFrontier,
    *,
    pass_number: int,
    label: str,
    kind: PassKind = PassKind.INITIAL,
    selection_kind: FrontierSelectionKind = FrontierSelectionKind.ARTIFACT_INFERRED,
    evidence_paths: tuple[str, ...] = (),
) -> PassFrontier:
    """Build a :class:`PassFrontier` from an existing :class:`LoopFrontier`.

    Pure and read-only: it only reads attributes of the supplied frontier. When
    the frontier has no inferred slice, the frontier fields are empty strings
    (the resulting model is deterministic and will fail
    :func:`validate_pass_frontier`, surfacing the gap). It never reads the
    filesystem, writes artifacts, invokes memory, or changes frontier inference.
    """

    inferred_slice = getattr(frontier, "inferred_slice", None)
    inferred_milestone = getattr(frontier, "inferred_milestone", None)

    if inferred_slice is not None:
        slice_id = getattr(inferred_slice, "slice_id", "") or ""
        title = getattr(inferred_slice, "title", "") or ""
        if inferred_milestone is not None:
            milestone_id = getattr(inferred_milestone, "milestone_id", "") or ""
        else:
            milestone_id = slice_id.split("-", 1)[0] if "-" in slice_id else ""
    else:
        slice_id = ""
        title = ""
        milestone_id = ""

    baseline = getattr(frontier, "accepted_slice_ids", ())

    return build_pass_frontier(
        pass_number=pass_number,
        label=label,
        kind=kind,
        frontier=Frontier(
            milestone_id=milestone_id,
            slice_id=slice_id,
            title=title,
            selection_kind=selection_kind,
        ),
        accepted_baseline_slice_ids=baseline,
        evidence_paths=evidence_paths,
    )
