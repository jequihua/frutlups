"""Second-pass prompt context model and renderer (M013-S03).

A small, pure, deterministic model plus markdown renderer that ties together a
:class:`~frutlups.second_pass.PassFrontier` (M013-S01) and the
:class:`~frutlups.second_pass_evidence.SecondPassEvidence` bundle (M013-S02) into
text suitable to insert into a *future* coding prompt.

This is a foundation slice only. It makes second-pass prompt context renderable
and testable; it does **not** wire the renderer into ``make-coding-prompt``,
write any coding prompt, change ``frutlups status`` / ``frutlups next`` frontier
inference, add a CLI command, implement first-pass evidence preservation, touch
memory, or mutate any governance artifact. The renderer operates from supplied
model objects; the optional collector helper is read-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from frutlups.second_pass import (
    Frontier,
    PassFrontier,
    PassIdentity,
    validate_pass_frontier,
)
from frutlups.second_pass_evidence import (
    SecondPassEvidence,
    validate_second_pass_evidence,
)

# Short, stable note recorded with every rendered context so a downstream reader
# never mistakes collected evidence (or optional memory) for authoritative state.
AUTHORITY_NOTE = (
    "Repository artifacts remain authoritative. Memory is optional and "
    "read-only during normal coding and review slices; evidence below is "
    "supporting context, not a substitute for the source artifacts."
)


def _plain(value: object) -> object:
    """Return a JSON-safe plain representation of a scalar field.

    Mirrors :func:`frutlups.second_pass._plain`: enum values become their
    string value; ``None``/``str``/``int``/``float``/``bool`` pass through;
    anything else is converted to ``str`` so payloads survive ``json.dumps``.
    """

    if isinstance(value, StrEnum):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _model_dict(value: object) -> object:
    """Serialize a model exposing ``to_dict()`` safely, else fall back to ``_plain``."""

    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return to_dict()
        except Exception:
            pass
    return _plain(value)


@dataclass(frozen=True)
class SecondPassContext:
    """Second-pass prompt context: a frontier plus its supporting evidence.

    ``pass_frontier`` is the M013-S01 pass/frontier decision; ``evidence`` is the
    M013-S02 accepted-follow-up / known-divergence bundle. ``authority_note`` is
    a stable posture string (defaults to :data:`AUTHORITY_NOTE`). The object is
    pure data; rendering and validation are separate pure functions.
    """

    pass_frontier: PassFrontier
    evidence: SecondPassEvidence
    authority_note: str = AUTHORITY_NOTE

    def to_dict(self) -> dict[str, object]:
        return {
            "pass_frontier": _model_dict(self.pass_frontier),
            "evidence": _model_dict(self.evidence),
            "authority_note": _plain(self.authority_note),
        }


@dataclass(frozen=True)
class RenderOptions:
    """Deterministic bounds for rendering second-pass context.

    ``max_follow_ups`` / ``max_divergences`` cap how many items are rendered
    (``None`` means no cap). ``max_text_chars`` truncates each item body
    (``None`` means no truncation). Truncation and omission are always made
    visible in the output so a reader can tell context was bounded.
    """

    max_follow_ups: int | None = None
    max_divergences: int | None = None
    max_text_chars: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "max_follow_ups": _plain(self.max_follow_ups),
            "max_divergences": _plain(self.max_divergences),
            "max_text_chars": _plain(self.max_text_chars),
        }


def build_second_pass_context(
    pass_frontier: PassFrontier,
    evidence: SecondPassEvidence,
    *,
    authority_note: str = AUTHORITY_NOTE,
) -> SecondPassContext:
    """Construct a :class:`SecondPassContext`. Pure; performs no IO and does not validate."""

    return SecondPassContext(
        pass_frontier=pass_frontier,
        evidence=evidence,
        authority_note=authority_note,
    )


def validate_second_pass_context(context: SecondPassContext) -> tuple[str, ...]:
    """Return deterministic validation errors for ``context`` (empty if valid).

    Composes :func:`validate_pass_frontier` and
    :func:`validate_second_pass_evidence` (prefixed) and checks the authority
    note is a non-empty string. Never raises for constructible inputs.
    """

    if not isinstance(context, SecondPassContext):
        return ("context must be a SecondPassContext instance",)

    errors: list[str] = []
    if not isinstance(context.pass_frontier, PassFrontier):
        errors.append("pass_frontier must be a PassFrontier instance")
    else:
        for err in validate_pass_frontier(context.pass_frontier):
            errors.append(f"pass_frontier: {err}")

    if not isinstance(context.evidence, SecondPassEvidence):
        errors.append("evidence must be a SecondPassEvidence instance")
    else:
        for err in validate_second_pass_evidence(context.evidence):
            errors.append(f"evidence: {err}")

    if not isinstance(context.authority_note, str) or not context.authority_note.strip():
        errors.append("authority_note must be a non-empty string")

    return tuple(errors)


# ---------------------------------------------------------------------------
# Rendering helpers (pure, defensive against malformed constructible values)
# ---------------------------------------------------------------------------


def _s(value: object) -> str:
    """Render any field as a stable display string (enum → value)."""

    if isinstance(value, StrEnum):
        return value.value
    if value is None:
        return ""
    return str(value)


TRUNCATION_MARKER = "[truncated]"
"""Visible marker appended whenever ``max_text_chars`` omits evidence text."""


def _truncate(text: str, limit: int | None) -> str:
    """Truncate ``text`` to ``limit`` chars, always marking omission visibly.

    ``limit`` is the number of characters of the *original* text to keep; the
    :data:`TRUNCATION_MARKER` is then appended whenever any text was omitted, so
    truncation stays visible even for very small positive caps. ``limit == 0``
    keeps no text and renders the marker alone (never a silent empty omission).
    ``limit is None`` (or a malformed/negative value) returns the text
    unbounded. Deterministic; never raises.
    """

    if limit is None or not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        return text
    if len(text) <= limit:
        return text
    kept = text[:limit]
    if kept:
        return f"{kept} {TRUNCATION_MARKER}"
    return TRUNCATION_MARKER


def _cap(items: list, limit: int | None) -> tuple[list, int]:
    """Return ``(kept, omitted_count)`` applying an optional non-negative cap."""

    if limit is None or not isinstance(limit, int) or limit < 0 or limit >= len(items):
        return items, 0
    return items[:limit], len(items) - limit


def render_second_pass_context(
    context: SecondPassContext,
    *,
    options: RenderOptions | None = None,
) -> str:
    """Render ``context`` as stable markdown for inclusion in a coding prompt.

    Deterministic and pure: no IO, no time, no randomness. Defensive against
    malformed constructible fields (rendered via ``str``). Includes pass
    identity, frontier, baseline slice IDs, evidence paths, accepted follow-ups,
    known divergences, evidence-collection diagnostics (clearly marked), and the
    repository-authority / read-only-memory posture note. ``options`` bounds the
    output; omissions and truncation are made visible. Never raises for
    constructible inputs.
    """

    opts = options if isinstance(options, RenderOptions) else RenderOptions()

    pf = context.pass_frontier
    identity = getattr(pf, "identity", None)
    frontier = getattr(pf, "frontier", None)
    baseline = getattr(pf, "accepted_baseline_slice_ids", ())
    evidence_paths = getattr(pf, "evidence_paths", ())
    evidence = context.evidence
    follow_ups = list(getattr(evidence, "accepted_follow_ups", ()) or ())
    divergences = list(getattr(evidence, "known_divergences", ()) or ())
    diagnostics = list(getattr(evidence, "diagnostics", ()) or ())

    lines: list[str] = ["# Second-Pass Prompt Context", ""]

    # Pass identity
    lines.append("## Pass")
    lines.append("")
    if isinstance(identity, PassIdentity):
        lines.append(f"- Number: {_s(identity.number)}")
        lines.append(f"- Label: {_s(identity.label)}")
        lines.append(f"- Kind: {_s(identity.kind)}")
    else:
        lines.append("- Pass identity unavailable.")
    lines.append("")

    # Frontier
    lines.append("## Frontier")
    lines.append("")
    if isinstance(frontier, Frontier):
        lines.append(f"- Milestone: {_s(frontier.milestone_id)}")
        lines.append(f"- Slice: {_s(frontier.slice_id)}")
        lines.append(f"- Title: {_s(frontier.title)}")
        lines.append(f"- Selection: {_s(frontier.selection_kind)}")
    else:
        lines.append("- Frontier unavailable.")
    lines.append("")

    # Baseline slice IDs
    lines.append("## Accepted Baseline Slice IDs")
    lines.append("")
    baseline_list = list(baseline) if isinstance(baseline, (tuple, list)) else [baseline]
    if baseline_list:
        for entry in baseline_list:
            lines.append(f"- {_s(entry)}")
    else:
        lines.append("- None.")
    lines.append("")

    # Evidence paths
    lines.append("## Evidence Paths")
    lines.append("")
    ep_list = (
        list(evidence_paths) if isinstance(evidence_paths, (tuple, list)) else [evidence_paths]
    )
    if ep_list:
        for entry in ep_list:
            lines.append(f"- {_s(entry)}")
    else:
        lines.append("- None.")
    lines.append("")

    # Accepted follow-ups
    lines.append("## Accepted Follow-Ups")
    lines.append("")
    kept_fu, omitted_fu = _cap(follow_ups, opts.max_follow_ups)
    if not follow_ups:
        lines.append("- None.")
    else:
        for item in kept_fu:
            kind = _s(getattr(item, "kind", ""))
            slice_id = _s(getattr(item, "source_slice_id", ""))
            source = _s(getattr(item, "source_path", ""))
            text = _truncate(_s(getattr(item, "text", "")), opts.max_text_chars)
            slice_part = f" [{slice_id}]" if slice_id else ""
            lines.append(f"- ({kind}){slice_part} {source}")
            if text:
                lines.append(f"  - {text}")
        if omitted_fu:
            lines.append(f"- ... {omitted_fu} more follow-up(s) omitted.")
    lines.append("")

    # Known divergences
    lines.append("## Known Divergences")
    lines.append("")
    kept_div, omitted_div = _cap(divergences, opts.max_divergences)
    if not divergences:
        lines.append("- None.")
    else:
        for div in kept_div:
            identifier = _s(getattr(div, "identifier", "")) or _s(getattr(div, "title", ""))
            source = _s(getattr(div, "source_path", ""))
            body = _truncate(_s(getattr(div, "body", "")), opts.max_text_chars)
            lines.append(f"- {identifier} ({source})")
            if body:
                lines.append(f"  - {body}")
        if omitted_div:
            lines.append(f"- ... {omitted_div} more divergence(s) omitted.")
    lines.append("")

    # Diagnostics (clearly marked as non-authoritative)
    lines.append("## Evidence Diagnostics")
    lines.append("")
    lines.append("These are collection diagnostics, not authoritative facts.")
    lines.append("")
    if diagnostics:
        for diag in diagnostics:
            lines.append(f"- {_s(diag)}")
    else:
        lines.append("- None.")
    lines.append("")

    # Authority posture
    lines.append("## Authority")
    lines.append("")
    note = (
        context.authority_note
        if isinstance(context.authority_note, str) and context.authority_note.strip()
        else AUTHORITY_NOTE
    )
    lines.append(note)
    lines.append("")

    return "\n".join(lines)


def collect_second_pass_context(
    root,
    *,
    pass_number: int = 2,
    label: str = "second pass",
    slice_id: str = "",
):
    """Build a :class:`SecondPassContext` from live repository artifacts (read-only).

    Convenience helper for probes/tests: reads the inferred frontier and the
    M013-S02 evidence collectors, wraps them as a second-pass
    :class:`PassFrontier`, and returns a context. Imports the live builders
    lazily so this module stays import-light. Read-only; never writes, never
    mutates memory, and never changes frontier inference. Never raises for
    constructible inputs.
    """

    from frutlups.project import build_next_frontier
    from frutlups.second_pass import (
        FrontierSelectionKind,
        PassKind,
        pass_frontier_from_loop_frontier,
    )
    from frutlups.second_pass_evidence import collect_second_pass_evidence

    frontier = build_next_frontier(root)
    pass_frontier = pass_frontier_from_loop_frontier(
        frontier,
        pass_number=pass_number,
        label=label,
        kind=PassKind.SECOND_PASS,
        selection_kind=FrontierSelectionKind.HUMAN_SELECTED,
    )
    effective_slice = slice_id or pass_frontier.frontier.slice_id
    evidence = collect_second_pass_evidence(root, slice_id=effective_slice)
    return build_second_pass_context(pass_frontier, evidence)
