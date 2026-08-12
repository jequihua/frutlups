"""Roadmap and loop state helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from frutlups.review_report import ReviewVerdict

MILESTONE_RE = re.compile(r"^###\s+(?P<id>M\d+):\s+(?P<title>.+?)\s*$")
STATUS_RE = re.compile(r"^Status:\s*(?P<status>[\w_-]+)\s*$", re.IGNORECASE)
DONE_WHEN_RE = re.compile(r"^Done when:\s*$", re.IGNORECASE)
SLICES_RE = re.compile(r"^Slices:\s*$", re.IGNORECASE)
BULLET_RE = re.compile(r"^\s*[-*]\s+(?P<text>.+?)\s*$")
SLICE_BULLET_RE = re.compile(r"^\s*[-*]\s+(?P<id>M\d+-S\d+):\s*(?P<title>.+?)\s*$")
SLICE_ID_RE = re.compile(r"^M\d+-S\d+$")


class MilestoneStatus(StrEnum):
    """Known milestone states.

    The five canonical statuses are ``planned``, ``active``, ``completed``,
    ``blocked``, and ``needs_review``. ``unknown`` is a fallback used for any
    value the parser cannot recognise; it is not a roadmap-author state.
    """

    PLANNED = "planned"
    ACTIVE = "active"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    NEEDS_REVIEW = "needs_review"
    UNKNOWN = "unknown"

    @classmethod
    def canonical(cls) -> tuple[MilestoneStatus, ...]:
        """Return the ordered canonical statuses, excluding ``UNKNOWN``."""

        return (
            cls.PLANNED,
            cls.ACTIVE,
            cls.COMPLETED,
            cls.BLOCKED,
            cls.NEEDS_REVIEW,
        )

    @classmethod
    def coerce(cls, value: str | None) -> MilestoneStatus:
        """Coerce a free-form roadmap status token to a ``MilestoneStatus``.

        Recognises canonical values case-insensitively and treats ``-`` as
        ``_`` so the common ``needs-review`` spelling resolves to
        ``NEEDS_REVIEW``. Returns ``UNKNOWN`` for ``None``, empty/whitespace,
        or unrecognised tokens. Never raises.
        """

        if value is None:
            return cls.UNKNOWN
        normalized = value.strip().lower().replace("-", "_")
        if not normalized:
            return cls.UNKNOWN
        try:
            return cls(normalized)
        except ValueError:
            return cls.UNKNOWN


@dataclass(frozen=True)
class RoadmapMilestone:
    """A milestone parsed from a markdown roadmap."""

    milestone_id: str
    title: str
    status: MilestoneStatus
    done_criteria: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.milestone_id,
            "title": self.title,
            "status": self.status.value,
            "done_criteria": list(self.done_criteria),
        }


def parse_milestones(path: Path) -> tuple[RoadmapMilestone, ...]:
    """Parse milestone headings, status lines, and done criteria."""

    milestones: list[RoadmapMilestone] = []
    current_id: str | None = None
    current_title: str | None = None
    current_status = MilestoneStatus.UNKNOWN
    current_criteria: list[str] = []
    in_done_section = False
    current_bullet: str | None = None

    def finalize_bullet() -> None:
        nonlocal current_bullet
        if current_bullet is not None:
            text = current_bullet.strip()
            if text:
                current_criteria.append(text)
            current_bullet = None

    def finalize_milestone() -> None:
        nonlocal current_id, current_title, current_status
        nonlocal current_criteria, in_done_section
        finalize_bullet()
        if current_id is not None and current_title is not None:
            milestones.append(
                RoadmapMilestone(
                    milestone_id=current_id,
                    title=current_title,
                    status=current_status,
                    done_criteria=tuple(current_criteria),
                )
            )
        current_id = None
        current_title = None
        current_status = MilestoneStatus.UNKNOWN
        current_criteria = []
        in_done_section = False

    for line in path.read_text(encoding="utf-8").splitlines():
        milestone_match = MILESTONE_RE.match(line)
        if milestone_match:
            finalize_milestone()
            current_id = milestone_match.group("id")
            current_title = milestone_match.group("title")
            continue

        if current_id is None:
            continue

        if in_done_section:
            stripped = line.strip()
            if not stripped:
                finalize_bullet()
                continue
            bullet_match = BULLET_RE.match(line)
            if bullet_match:
                finalize_bullet()
                current_bullet = bullet_match.group("text")
                continue
            if current_bullet is not None and line.startswith((" ", "\t")):
                current_bullet = f"{current_bullet} {stripped}"
                continue
            finalize_bullet()
            in_done_section = False
            # fall through so this line gets normal processing below

        status_match = STATUS_RE.match(line)
        if status_match:
            current_status = MilestoneStatus.coerce(status_match.group("status"))
            continue

        if DONE_WHEN_RE.match(line.strip()):
            in_done_section = True
            current_bullet = None
            continue

    finalize_milestone()
    return tuple(milestones)


def next_actionable_milestone(
    milestones: tuple[RoadmapMilestone, ...],
) -> RoadmapMilestone | None:
    """Return review-needed, active, or first planned milestone."""

    for milestone in milestones:
        if milestone.status == MilestoneStatus.NEEDS_REVIEW:
            return milestone
    for milestone in milestones:
        if milestone.status == MilestoneStatus.ACTIVE:
            return milestone
    for milestone in milestones:
        if milestone.status == MilestoneStatus.PLANNED:
            return milestone
    return None


class DiagnosticSeverity(StrEnum):
    """Local severity for a roadmap diagnostic.

    Severities are intentionally simple. ``error`` indicates the loop cannot
    advance from artifacts alone. ``warning`` indicates the loop can still
    advance but the user should fix something. ``info`` indicates an
    actionable but non-blocking observation.
    """

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class Diagnostic:
    """A typed diagnostic about roadmap or loop state.

    The ``code`` is a stable string identifier (snake_case) intended for
    tests and tooling. The ``message`` is a human-readable explanation
    that should be actionable enough for a local user to know which
    artifact is missing or ambiguous.
    """

    code: str
    severity: DiagnosticSeverity
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
        }


@dataclass(frozen=True)
class RoadmapSlice:
    """A slice parsed from a detailed roadmap's ``Slices:`` bullet list."""

    slice_id: str
    milestone_id: str
    title: str

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.slice_id,
            "milestone_id": self.milestone_id,
            "title": self.title,
        }


class SliceKind(StrEnum):
    """Work classification for a roadmap slice.

    ``NORMAL`` covers all standard coding/review/governance work.
    ``MEMORY_UPDATE`` marks slices that explicitly mutate a ``llloom``
    workspace; these require separate review evidence and are segregated
    from normal read-only loop work.

    The enum members are retained for compatibility, but no milestone
    identifier, title, substring, directory name, or memory-root presence
    grants ``MEMORY_UPDATE`` by inference (M011-S01). Until a separately
    decided explicit slice-kind grammar exists, mutation permission comes
    only from a human-owned coding prompt/slice assignment, never from an
    identifier.
    """

    NORMAL = "normal"
    MEMORY_UPDATE = "memory_update"


def classify_slice_kind(milestone_id: str) -> SliceKind:
    """Return the work classification for a slice based on its milestone.

    Always ``NORMAL``. Milestone identity — including ``M010`` and any case
    variant — no longer confers memory-update authority (M011-S01 removed the
    repository-history leak that classified every ``M010`` milestone as a
    memory-mutation slice). No replacement heuristic is introduced: an explicit
    slice-kind grammar is a separate future template/frutlups contract decision.
    Until then, a slice is memory-update only when a human-owned prompt says so.
    """

    return SliceKind.NORMAL


def parse_slices(path: Path) -> tuple[RoadmapSlice, ...]:
    """Parse slices from a detailed roadmap's ``Slices:`` bullet sections.

    Slices are expected as bullet items of the form
    ``- M002-S03: title text``. Wrapped continuation lines (indented and
    bullet-less) are joined onto the prior slice title. The slice section
    ends at the next non-bullet, non-indented line (for example
    ``Acceptance:``) or at the next milestone heading.
    """

    slices: list[RoadmapSlice] = []
    current_milestone_id: str | None = None
    in_slices_section = False
    current_slice_id: str | None = None
    current_title: str | None = None

    def finalize_slice() -> None:
        nonlocal current_slice_id, current_title
        if (
            current_slice_id is not None
            and current_title is not None
            and current_milestone_id is not None
        ):
            slices.append(
                RoadmapSlice(
                    slice_id=current_slice_id,
                    milestone_id=current_milestone_id,
                    title=current_title.strip(),
                )
            )
        current_slice_id = None
        current_title = None

    for line in path.read_text(encoding="utf-8").splitlines():
        milestone_match = MILESTONE_RE.match(line)
        if milestone_match:
            finalize_slice()
            current_milestone_id = milestone_match.group("id")
            in_slices_section = False
            continue

        if current_milestone_id is None:
            continue

        if in_slices_section:
            stripped = line.strip()
            if not stripped:
                finalize_slice()
                continue
            slice_match = SLICE_BULLET_RE.match(line)
            if slice_match:
                finalize_slice()
                current_slice_id = slice_match.group("id")
                current_title = slice_match.group("title")
                continue
            if BULLET_RE.match(line):
                # Non-slice bullet inside Slices section: skip it but stay
                # in the section so a following slice bullet is still picked
                # up.
                finalize_slice()
                continue
            if current_slice_id is not None and line.startswith((" ", "\t")):
                current_title = f"{current_title} {stripped}" if current_title else stripped
                continue
            finalize_slice()
            in_slices_section = False
            # fall through so this line gets normal processing below

        if SLICES_RE.match(line.strip()):
            in_slices_section = True
            current_slice_id = None
            current_title = None
            continue

    finalize_slice()
    return tuple(slices)


def next_actionable_slice(
    slices: tuple[RoadmapSlice, ...],
    milestone_id: str,
    accepted_slice_ids: tuple[str, ...] = (),
) -> RoadmapSlice | None:
    """Return the first slice for ``milestone_id`` not yet accepted.

    Both the requested ``milestone_id`` and each candidate slice's
    ``milestone_id`` are compared case-insensitively, so callers may pass
    ``"M002"`` or ``"m002"`` interchangeably. A slice is considered accepted
    when its ID (case-insensitive) appears in ``accepted_slice_ids``. The
    first unaccepted slice in document order is returned. Returns ``None``
    if the milestone has no slices, or if every slice for the milestone is
    already accepted.
    """

    target_milestone = milestone_id.upper()
    accepted = {sid.upper() for sid in accepted_slice_ids}
    for candidate in slices:
        if candidate.milestone_id.upper() != target_milestone:
            continue
        if candidate.slice_id.upper() in accepted:
            continue
        return candidate
    return None


# ---------------------------------------------------------------------------
# M007-S03: Next-action computation from verdict and roadmap state
# ---------------------------------------------------------------------------


class NextActionKind(StrEnum):
    """Typed recommendation kind from :func:`compute_next_action_from_verdict`.

    ``ADVANCE_TO_NEXT_SLICE``: pass verdict; a following unaccepted slice
    exists in the same milestone.
    ``MILESTONE_COMPLETE``: pass verdict; no remaining unaccepted slice in
    the same milestone.
    ``RECODE_SAME_SLICE``: needs_work verdict; recommend another coding pass.
    ``UNBLOCK_SAME_SLICE``: blocked verdict; recommend human/external unblock.
    ``HUMAN_OVERRIDE_REQUIRED``: override verdict; advancement is deferred to
    M007-S04.
    ``INVALID``: malformed inputs; decision cannot be computed.
    """

    ADVANCE_TO_NEXT_SLICE = "advance_to_next_slice"
    MILESTONE_COMPLETE = "milestone_complete"
    RECODE_SAME_SLICE = "recode_same_slice"
    UNBLOCK_SAME_SLICE = "unblock_same_slice"
    HUMAN_OVERRIDE_REQUIRED = "human_override_required"
    INVALID = "invalid"


@dataclass(frozen=True)
class NextActionDecision:
    """Typed recommendation produced by :func:`compute_next_action_from_verdict`.

    ``kind`` is the action category. ``verdict`` is the input verdict or
    ``None`` for invalid decisions. ``current_slice_id`` is the reviewed
    slice. ``next_slice_id`` is populated only for
    ``ADVANCE_TO_NEXT_SLICE``; it is ``None`` otherwise. ``message`` is a
    human-readable summary. ``errors`` is empty on success.

    ``to_dict()`` returns only plain Python values; enum members are
    serialized as their ``.value`` strings.
    """

    kind: NextActionKind
    verdict: ReviewVerdict | None
    current_slice_id: str
    next_slice_id: str | None
    message: str
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "verdict": self.verdict.value if self.verdict is not None else None,
            "current_slice_id": self.current_slice_id,
            "next_slice_id": self.next_slice_id,
            "message": self.message,
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class NextActionCommand:
    """Input for :func:`compute_next_action_from_verdict`.

    ``verdict`` is the parsed review verdict. ``current_slice`` is the
    reviewed slice. ``slices`` contains all roadmap slices in document
    order. ``accepted_slice_ids`` contains slice IDs accepted before this
    verdict is applied; the current slice need not be present.
    """

    verdict: ReviewVerdict
    current_slice: RoadmapSlice
    slices: tuple[RoadmapSlice, ...]
    accepted_slice_ids: tuple[str, ...] = ()


def compute_next_action_from_verdict(
    command: NextActionCommand,
) -> NextActionDecision:
    """Compute a typed next-action recommendation from a review verdict.

    Pure, deterministic, and never raises for constructible malformed
    inputs. No filesystem access, no prompt scanning, no memory access,
    and no hidden state.

    Decision rules:

    - ``pass``: treat the current slice as accepted, then return
      ``ADVANCE_TO_NEXT_SLICE`` if the next unaccepted slice in the
      same milestone exists, or ``MILESTONE_COMPLETE`` if none remain.
    - ``needs_work``: return ``RECODE_SAME_SLICE``.
    - ``blocked``: return ``UNBLOCK_SAME_SLICE``.
    - ``override``: return ``HUMAN_OVERRIDE_REQUIRED``; advancement
      is deferred to M007-S04.
    - malformed inputs: return ``INVALID`` with deterministic errors.
    """

    def _invalid(*msgs: str, current_id: str = "") -> NextActionDecision:
        return NextActionDecision(
            kind=NextActionKind.INVALID,
            verdict=None,
            current_slice_id=current_id,
            next_slice_id=None,
            message="invalid inputs: cannot compute next action",
            errors=tuple(msgs),
        )

    errors: list[str] = []

    if not isinstance(command.verdict, ReviewVerdict):
        errors.append("verdict must be a ReviewVerdict instance")

    if not isinstance(command.current_slice, RoadmapSlice):
        errors.append("current_slice must be a RoadmapSlice instance")

    slices_valid = isinstance(command.slices, (tuple, list))
    if not slices_valid:
        errors.append("slices must be a tuple or list of RoadmapSlice instances")
    else:
        for idx, slc in enumerate(command.slices):
            if not isinstance(slc, RoadmapSlice):
                errors.append(f"slices[{idx}] must be a RoadmapSlice instance")
                slices_valid = False

    accepted_valid = isinstance(command.accepted_slice_ids, (tuple, list))
    if not accepted_valid:
        errors.append("accepted_slice_ids must be a tuple or list of strings")
    else:
        for idx, sid in enumerate(command.accepted_slice_ids):
            if not isinstance(sid, str):
                errors.append(f"accepted_slice_ids[{idx}] must be a string")
                accepted_valid = False

    if errors:
        return _invalid(*errors)

    current_id = command.current_slice.slice_id
    current_upper = current_id.upper()
    if not any(slc.slice_id.upper() == current_upper for slc in command.slices):
        return _invalid(
            f"current slice {current_id!r} is not present in the supplied slices",
            current_id=current_id,
        )

    verdict = command.verdict

    if verdict == ReviewVerdict.NEEDS_WORK:
        return NextActionDecision(
            kind=NextActionKind.RECODE_SAME_SLICE,
            verdict=verdict,
            current_slice_id=current_id,
            next_slice_id=None,
            message=(f"slice {current_id} needs more work; recommend another coding pass"),
            errors=(),
        )

    if verdict == ReviewVerdict.BLOCKED:
        return NextActionDecision(
            kind=NextActionKind.UNBLOCK_SAME_SLICE,
            verdict=verdict,
            current_slice_id=current_id,
            next_slice_id=None,
            message=(
                f"slice {current_id} is blocked;"
                " recommend human input or external dependency resolution"
            ),
            errors=(),
        )

    if verdict == ReviewVerdict.OVERRIDE:
        return NextActionDecision(
            kind=NextActionKind.HUMAN_OVERRIDE_REQUIRED,
            verdict=verdict,
            current_slice_id=current_id,
            next_slice_id=None,
            message=(
                f"slice {current_id} has an override verdict;"
                " human override advancement is deferred to M007-S04"
            ),
            errors=(),
        )

    # PASS: treat current slice as accepted when searching for next
    effective_accepted = tuple(set(command.accepted_slice_ids) | {current_id})
    next_slice = next_actionable_slice(
        tuple(command.slices),
        command.current_slice.milestone_id,
        effective_accepted,
    )

    if next_slice is not None:
        return NextActionDecision(
            kind=NextActionKind.ADVANCE_TO_NEXT_SLICE,
            verdict=verdict,
            current_slice_id=current_id,
            next_slice_id=next_slice.slice_id,
            message=(f"slice {current_id} accepted; advance to {next_slice.slice_id}"),
            errors=(),
        )

    return NextActionDecision(
        kind=NextActionKind.MILESTONE_COMPLETE,
        verdict=verdict,
        current_slice_id=current_id,
        next_slice_id=None,
        message=(
            f"slice {current_id} accepted;"
            f" milestone {command.current_slice.milestone_id} appears complete"
        ),
        errors=(),
    )


# ---------------------------------------------------------------------------
# M007-S04: Explicit human override authorization
# ---------------------------------------------------------------------------


class HumanOverrideTarget(StrEnum):
    """The explicit action the human chooses when authorizing an override.

    ``RECODE_SAME_SLICE``: recode the same slice again.
    ``UNBLOCK_SAME_SLICE``: mark the same slice blocked / needs human unblock.
    ``ADVANCE_TO_SLICE``: advance to an explicitly supplied next slice ID.
    ``MILESTONE_COMPLETE``: mark the current milestone complete with no next
    slice.

    ``ADVANCE_TO_SLICE`` requires a non-empty ``target_slice_id`` in the
    command. The other targets must have ``target_slice_id = None``.
    """

    RECODE_SAME_SLICE = "recode_same_slice"
    UNBLOCK_SAME_SLICE = "unblock_same_slice"
    ADVANCE_TO_SLICE = "advance_to_slice"
    MILESTONE_COMPLETE = "milestone_complete"


_HUMAN_OVERRIDE_TARGETS_NO_SLICE: frozenset[HumanOverrideTarget] = frozenset(
    {
        HumanOverrideTarget.RECODE_SAME_SLICE,
        HumanOverrideTarget.UNBLOCK_SAME_SLICE,
        HumanOverrideTarget.MILESTONE_COMPLETE,
    }
)


@dataclass(frozen=True)
class HumanOverrideDecision:
    """Result of :func:`authorize_human_override`.

    ``valid`` is ``True`` only when all authorization rules pass. ``target``
    is the authorized :class:`HumanOverrideTarget` or ``None`` on failure.
    ``source_kind`` is the prior :class:`NextActionKind` when available.
    ``next_slice_id`` is populated only for ``ADVANCE_TO_SLICE``.
    ``rationale`` preserves the human-supplied string (raw, may be empty on
    failure). ``errors`` is empty on success.

    ``to_dict()`` returns only plain Python values.
    """

    valid: bool
    target: HumanOverrideTarget | None
    source_kind: NextActionKind | None
    current_slice_id: str
    next_slice_id: str | None
    rationale: str
    message: str
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "target": self.target.value if self.target is not None else None,
            "source_kind": (self.source_kind.value if self.source_kind is not None else None),
            "current_slice_id": self.current_slice_id,
            "next_slice_id": self.next_slice_id,
            "rationale": self.rationale,
            "message": self.message,
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class HumanOverrideCommand:
    """Input for :func:`authorize_human_override`.

    ``prior_decision`` must be a :class:`NextActionDecision` with
    ``kind=HUMAN_OVERRIDE_REQUIRED`` and ``verdict=ReviewVerdict.OVERRIDE``.
    ``target`` is the human's explicit chosen action. ``rationale`` must be
    a non-empty string after trimming. ``target_slice_id`` is required (and
    must be non-empty) when ``target`` is ``ADVANCE_TO_SLICE``; it must be
    ``None`` for all other targets. ``actor`` is optional audit text.
    """

    prior_decision: NextActionDecision
    target: HumanOverrideTarget
    rationale: str
    target_slice_id: str | None = None
    actor: str | None = None


def authorize_human_override(
    command: HumanOverrideCommand,
) -> HumanOverrideDecision:
    """Authorize a human override of the loop's next-action decision.

    Pure, deterministic, and never raises for constructible malformed inputs.
    No filesystem access, no memory access, no hidden state. The result
    represents the human's authorization intent; it does not record the
    override or mutate roadmap state.

    Authorization rules:

    - ``prior_decision`` must be a :class:`NextActionDecision` with
      ``kind=HUMAN_OVERRIDE_REQUIRED`` and ``verdict=ReviewVerdict.OVERRIDE``.
    - ``target`` must be a :class:`HumanOverrideTarget` instance.
    - ``rationale`` must be a non-empty string after whitespace trimming.
    - ``ADVANCE_TO_SLICE`` requires a non-empty ``target_slice_id``.
    - All other targets require ``target_slice_id = None``.
    """

    if not isinstance(command, HumanOverrideCommand):
        return HumanOverrideDecision(
            valid=False,
            target=None,
            source_kind=None,
            current_slice_id="",
            next_slice_id=None,
            rationale="",
            message="override authorization failed",
            errors=("command must be a HumanOverrideCommand instance",),
        )

    errors: list[str] = []
    current_id = ""
    source_kind: NextActionKind | None = None
    raw_rationale = command.rationale if isinstance(command.rationale, str) else ""

    prior = command.prior_decision
    if not isinstance(prior, NextActionDecision):
        errors.append("prior_decision must be a NextActionDecision instance")
    else:
        if isinstance(prior.current_slice_id, str):
            current_id = prior.current_slice_id
        if isinstance(prior.kind, NextActionKind):
            source_kind = prior.kind
        if prior.kind != NextActionKind.HUMAN_OVERRIDE_REQUIRED:
            errors.append("prior decision kind must be HUMAN_OVERRIDE_REQUIRED")
        if prior.verdict != ReviewVerdict.OVERRIDE:
            errors.append("prior decision verdict must be override")

    if not isinstance(command.target, HumanOverrideTarget):
        errors.append("target must be a HumanOverrideTarget instance")

    if not isinstance(command.rationale, str):
        errors.append("rationale must be a non-empty string")
    elif not command.rationale.strip():
        errors.append("rationale must not be empty or whitespace-only")

    if isinstance(command.target, HumanOverrideTarget):
        if command.target == HumanOverrideTarget.ADVANCE_TO_SLICE:
            tsid = command.target_slice_id
            if not isinstance(tsid, str) or not tsid.strip():
                errors.append(
                    "target_slice_id must be a non-empty string when target is advance_to_slice"
                )
        elif command.target in _HUMAN_OVERRIDE_TARGETS_NO_SLICE:
            if command.target_slice_id is not None:
                errors.append(f"target_slice_id must be None when target is {command.target.value}")

    if errors:
        return HumanOverrideDecision(
            valid=False,
            target=None,
            source_kind=source_kind,
            current_slice_id=current_id,
            next_slice_id=None,
            rationale=raw_rationale,
            message="override authorization failed",
            errors=tuple(errors),
        )

    target = command.target
    next_slice_id = (
        command.target_slice_id if target == HumanOverrideTarget.ADVANCE_TO_SLICE else None
    )
    msg = f"human override authorized for slice {current_id}: {target.value}"
    if next_slice_id:
        msg = f"{msg} -> {next_slice_id}"

    return HumanOverrideDecision(
        valid=True,
        target=target,
        source_kind=prior.kind,
        current_slice_id=current_id,
        next_slice_id=next_slice_id,
        rationale=command.rationale,
        message=msg,
        errors=(),
    )
