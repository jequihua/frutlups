"""Second-pass evidence model and collector (M013-S02).

Small, pure, JSON-safe models plus a conservative collector for the evidence a
future second pass needs to cite first-pass artifacts without overwriting them:

- accepted follow-ups / residual risks / known limits / deferrals extracted from
  *accepted* review reports (a parseable ``pass`` verdict backed by a matching
  verdict record), and
- known divergences parsed from ``05_governance/known_divergences.md``.

This is still a foundation slice. It makes follow-up/divergence evidence
*available*; it does not render second-pass prompt context (M013-S03), preserve
first-pass evidence workflows (M013-S04), change ``frutlups status`` /
``frutlups next`` frontier inference, add a CLI command, mutate any governance
artifact, or touch memory. Model parsing is testable from supplied strings; the
file-reading collectors only read repository artifacts and never write.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath

from frutlups.review_report import ReviewVerdict, parse_review_report_verdict_text
from frutlups.second_pass import SLICE_ID_RE


class FollowUpKind(StrEnum):
    """Category of an accepted follow-up evidence item."""

    RESIDUAL_RISK = "residual_risk"
    KNOWN_LIMIT = "known_limit"
    DEFERRAL = "deferral"
    FOLLOW_UP = "follow_up"
    CORRECTION = "correction"


# Normalized review-report section heading -> follow-up kind. Headings are
# normalized by :func:`_parse_sections` (lowercased, trailing punctuation
# stripped, interior whitespace collapsed) before lookup.
_STABLE_SECTION_KINDS: dict[str, FollowUpKind] = {
    "residual risk": FollowUpKind.RESIDUAL_RISK,
    "residual risks": FollowUpKind.RESIDUAL_RISK,
    "known limits": FollowUpKind.KNOWN_LIMIT,
    "known limits and intentional deferrals": FollowUpKind.KNOWN_LIMIT,
    "known limits and deferrals": FollowUpKind.KNOWN_LIMIT,
    "intentional deferrals": FollowUpKind.DEFERRAL,
    "deferrals": FollowUpKind.DEFERRAL,
    "follow-up suggestions": FollowUpKind.FOLLOW_UP,
    "follow up suggestions": FollowUpKind.FOLLOW_UP,
    "follow-ups": FollowUpKind.FOLLOW_UP,
    "accepted follow-ups": FollowUpKind.FOLLOW_UP,
}

# Bodies that are conventional "nothing to report" placeholders. Skipped so the
# collector stays conservative and does not surface empty evidence.
_NOOP_BODIES: frozenset[str] = frozenset(
    {"none", "none.", "n/a", "na", "no findings", "no findings.", "nil"}
)

_REVIEW_REPORT_RE = re.compile(
    r"^(?P<milestone>m\d+)_(?P<slice>s\d+)_.*_review_report\.md$",
    re.IGNORECASE,
)
_CITED_REPORT_RE = re.compile(r"`([^`]*_review_report\.md)`")
_H2_PREFIX = "## "
_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\s*:\s*(.*)$")

_REVIEW_REPORT_SUFFIX = "_review_report.md"
_VERDICT_RECORD_SUFFIX = "_verdict_record.md"


def _looks_absolute(path: str) -> bool:
    """Return ``True`` when ``path`` looks absolute on any platform."""

    if path.startswith(("/", "\\")):
        return True
    return PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute()


def _plain(value: object) -> object:
    """Return a JSON-safe plain representation of a scalar field.

    Mirrors :func:`frutlups.second_pass._plain`: enum values become their
    string value; ``None``/``str``/``int``/``float``/``bool`` pass through;
    anything else (a malformed-but-constructible field) is converted to its
    ``str`` representation so ``to_dict()`` payloads always survive
    ``json.dumps``.
    """

    if isinstance(value, StrEnum):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _plain_sequence(value: object) -> list[object]:
    """Return a JSON-safe list of plain values for a sequence field.

    Non-sequence inputs become a single-element list holding the plain value,
    so a malformed scalar where a tuple was expected still serializes.
    """

    if isinstance(value, (tuple, list)):
        return [_plain(entry) for entry in value]
    return [_plain(value)]


def _plain_items(value: object) -> list[object]:
    """Return a JSON-safe list for a sequence of model items.

    Each entry that exposes a ``to_dict()`` is serialized through it; any other
    (malformed) entry falls back to :func:`_plain`. Non-sequence inputs become a
    single-element list, mirroring :func:`_plain_sequence`.
    """

    if isinstance(value, (tuple, list)):
        entries = list(value)
    else:
        entries = [value]
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


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FollowUpItem:
    """One accepted follow-up / residual-risk / deferral evidence item.

    ``source_path`` is a relative repository path to the artifact the evidence
    came from; ``source_slice_id`` is the slice it belongs to (``""`` when not
    known); ``kind`` categorizes the evidence; ``accepted`` records whether the
    source evidence is backed by an accepted verdict.
    """

    source_path: str
    text: str
    kind: FollowUpKind = FollowUpKind.FOLLOW_UP
    source_slice_id: str = ""
    accepted: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "source_path": _plain(self.source_path),
            "text": _plain(self.text),
            "kind": _plain(self.kind),
            "source_slice_id": _plain(self.source_slice_id),
            "accepted": _plain(self.accepted),
        }


@dataclass(frozen=True)
class KnownDivergence:
    """One known-divergence entry parsed from a governance markdown section.

    ``identifier`` is the stable full heading text; ``title`` is the heading
    with a leading ``YYYY-MM-DD:`` date prefix removed when present; ``body`` is
    the section text (may be empty).
    """

    source_path: str
    identifier: str
    title: str
    body: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "source_path": _plain(self.source_path),
            "identifier": _plain(self.identifier),
            "title": _plain(self.title),
            "body": _plain(self.body),
        }


@dataclass(frozen=True)
class FollowUpCollectionResult:
    """Result of collecting accepted follow-ups from a reviews directory."""

    items: tuple[FollowUpItem, ...] = field(default=())
    diagnostics: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, object]:
        return {
            "items": _plain_items(self.items),
            "diagnostics": _plain_sequence(self.diagnostics),
        }


@dataclass(frozen=True)
class SecondPassEvidence:
    """Combined second-pass evidence bundle for a frontier slice.

    ``accepted_follow_ups`` and ``known_divergences`` preserve collection order.
    ``diagnostics`` records deterministic notes about missing files, malformed
    headings, or empty evidence.
    """

    slice_id: str = ""
    accepted_follow_ups: tuple[FollowUpItem, ...] = field(default=())
    known_divergences: tuple[KnownDivergence, ...] = field(default=())
    diagnostics: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, object]:
        return {
            "slice_id": _plain(self.slice_id),
            "accepted_follow_ups": _plain_items(self.accepted_follow_ups),
            "known_divergences": _plain_items(self.known_divergences),
            "diagnostics": _plain_sequence(self.diagnostics),
        }


# ---------------------------------------------------------------------------
# Validation (deterministic; never raises for constructible inputs)
# ---------------------------------------------------------------------------


def validate_follow_up_item(item: FollowUpItem) -> tuple[str, ...]:
    """Return deterministic validation errors for ``item`` (empty if valid)."""

    if not isinstance(item, FollowUpItem):
        return ("item must be a FollowUpItem instance",)
    errors: list[str] = []
    if not isinstance(item.source_path, str) or not item.source_path.strip():
        errors.append("source_path must be a non-empty string")
    elif _looks_absolute(item.source_path):
        errors.append("source_path must be a relative repository path")
    if not isinstance(item.text, str) or not item.text.strip():
        errors.append("text must be a non-empty string")
    if not isinstance(item.kind, FollowUpKind):
        errors.append("kind must be a FollowUpKind")
    if not isinstance(item.source_slice_id, str):
        errors.append("source_slice_id must be a string")
    elif item.source_slice_id and not SLICE_ID_RE.match(item.source_slice_id):
        errors.append("source_slice_id must be empty or look like 'M013-S01'")
    if not isinstance(item.accepted, bool):
        errors.append("accepted must be a bool")
    return tuple(errors)


def validate_known_divergence(divergence: KnownDivergence) -> tuple[str, ...]:
    """Return deterministic validation errors for ``divergence`` (empty if valid)."""

    if not isinstance(divergence, KnownDivergence):
        return ("divergence must be a KnownDivergence instance",)
    errors: list[str] = []
    if not isinstance(divergence.source_path, str) or not divergence.source_path.strip():
        errors.append("source_path must be a non-empty string")
    elif _looks_absolute(divergence.source_path):
        errors.append("source_path must be a relative repository path")
    if not isinstance(divergence.identifier, str) or not divergence.identifier.strip():
        errors.append("identifier must be a non-empty string")
    if not isinstance(divergence.title, str) or not divergence.title.strip():
        errors.append("title must be a non-empty string")
    if not isinstance(divergence.body, str):
        errors.append("body must be a string")
    return tuple(errors)


def validate_second_pass_evidence(evidence: SecondPassEvidence) -> tuple[str, ...]:
    """Return deterministic validation errors for ``evidence`` (empty if valid).

    Composes :func:`validate_follow_up_item` and
    :func:`validate_known_divergence` (prefixed) over the bundle's collections.
    Never raises for constructible inputs.
    """

    if not isinstance(evidence, SecondPassEvidence):
        return ("evidence must be a SecondPassEvidence instance",)
    errors: list[str] = []

    if not isinstance(evidence.slice_id, str):
        errors.append("slice_id must be a string")
    elif evidence.slice_id and not SLICE_ID_RE.match(evidence.slice_id):
        errors.append("slice_id must be empty or look like 'M013-S01'")

    if not isinstance(evidence.accepted_follow_ups, (tuple, list)):
        errors.append("accepted_follow_ups must be a tuple or list")
    else:
        for index, item in enumerate(evidence.accepted_follow_ups):
            for err in validate_follow_up_item(item):
                errors.append(f"accepted_follow_ups[{index}]: {err}")

    if not isinstance(evidence.known_divergences, (tuple, list)):
        errors.append("known_divergences must be a tuple or list")
    else:
        for index, divergence in enumerate(evidence.known_divergences):
            for err in validate_known_divergence(divergence):
                errors.append(f"known_divergences[{index}]: {err}")

    if not isinstance(evidence.diagnostics, (tuple, list)):
        errors.append("diagnostics must be a tuple or list")

    return tuple(errors)


def build_second_pass_evidence(
    *,
    slice_id: str = "",
    accepted_follow_ups: tuple[FollowUpItem, ...] = (),
    known_divergences: tuple[KnownDivergence, ...] = (),
    diagnostics: tuple[str, ...] = (),
) -> SecondPassEvidence:
    """Construct a :class:`SecondPassEvidence`, normalizing collections to tuples.

    Pure and deterministic; performs no IO and does not validate (call
    :func:`validate_second_pass_evidence`).
    """

    return SecondPassEvidence(
        slice_id=slice_id,
        accepted_follow_ups=tuple(accepted_follow_ups)
        if isinstance(accepted_follow_ups, (tuple, list))
        else (),
        known_divergences=tuple(known_divergences)
        if isinstance(known_divergences, (tuple, list))
        else (),
        diagnostics=tuple(diagnostics) if isinstance(diagnostics, (tuple, list)) else (),
    )


# ---------------------------------------------------------------------------
# Pure text parsers
# ---------------------------------------------------------------------------


def _parse_sections(content: str) -> tuple[dict[str, str], list[str]]:
    """Parse ATX-heading sections into a (normalized-heading -> body, order) pair.

    Normalization lowercases the heading, strips trailing ``:!?.;,``, and
    collapses interior whitespace. Text before the first heading is discarded.
    The last occurrence of a duplicate heading wins; order preserves first
    appearance.
    """

    sections: dict[str, str] = {}
    order: list[str] = []
    current: str | None = None
    body: list[str] = []
    for line in content.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            hash_count = len(stripped) - len(stripped.lstrip("#"))
            rest = stripped[hash_count:]
            if 1 <= hash_count <= 6 and rest.startswith(" "):
                if current is not None:
                    sections[current] = "\n".join(body).strip()
                norm = " ".join(rest.strip().lower().rstrip(":!?.;,").split())
                current = norm
                if norm not in order:
                    order.append(norm)
                body = []
                continue
        if current is not None:
            body.append(line)
    if current is not None:
        sections[current] = "\n".join(body).strip()
    return sections, order


def extract_follow_ups_from_review_text(
    content: object,
    source_path: str,
    *,
    source_slice_id: str = "",
    accepted: bool = False,
) -> tuple[FollowUpItem, ...]:
    """Extract follow-up items from stable review-report sections.

    Conservative: only sections in :data:`_STABLE_SECTION_KINDS` with a
    non-empty, non-placeholder body produce an item. One item per matched
    section, in section order. Never raises.
    """

    if not isinstance(content, str):
        return ()
    sections, order = _parse_sections(content)
    items: list[FollowUpItem] = []
    for norm in order:
        kind = _STABLE_SECTION_KINDS.get(norm)
        if kind is None:
            continue
        body = sections.get(norm, "").strip()
        if not body or body.lower() in _NOOP_BODIES:
            continue
        items.append(
            FollowUpItem(
                source_path=source_path,
                text=body,
                kind=kind,
                source_slice_id=source_slice_id,
                accepted=accepted,
            )
        )
    return tuple(items)


def parse_known_divergences_text(
    content: object,
    source_path: str,
) -> tuple[tuple[KnownDivergence, ...], tuple[str, ...]]:
    """Parse ``known_divergences.md`` text into entries plus diagnostics.

    Each level-2 ``## `` heading starts one divergence entry. Returns a
    ``(entries, diagnostics)`` pair. Empty content and heading-less text yield
    deterministic diagnostics and no entries. Never raises.
    """

    if not isinstance(content, str) or not content.strip():
        return (), ("known divergences content is empty",)

    entries: list[KnownDivergence] = []
    current_title: str | None = None
    body: list[str] = []
    found_heading = False
    for line in content.splitlines():
        if line.startswith(_H2_PREFIX):
            found_heading = True
            if current_title is not None:
                entries.append(_make_divergence(source_path, current_title, body))
            current_title = line[len(_H2_PREFIX) :].strip()
            body = []
            continue
        if current_title is not None:
            body.append(line)
    if current_title is not None:
        entries.append(_make_divergence(source_path, current_title, body))

    diagnostics: list[str] = []
    if not found_heading:
        diagnostics.append("no divergence sections found (expected level-2 '## ' headings)")
    return tuple(entries), tuple(diagnostics)


def _make_divergence(source_path: str, heading: str, body_lines: list[str]) -> KnownDivergence:
    title = heading
    match = _DATE_PREFIX_RE.match(heading)
    if match and match.group(1).strip():
        title = match.group(1).strip()
    return KnownDivergence(
        source_path=source_path,
        identifier=heading,
        title=title,
        body="\n".join(body_lines).strip(),
    )


def _slice_id_from_review_name(name: str) -> str:
    match = _REVIEW_REPORT_RE.match(name)
    if not match:
        return ""
    return f"{match.group('milestone').upper()}-{match.group('slice').upper()}"


def _extract_cited_reports(content: str, *, exclude: str = "") -> tuple[str, ...]:
    """Return distinct backtick-wrapped ``*_review_report.md`` paths in order.

    Paths whose final component equals ``exclude`` (the citing report itself)
    are skipped. Used to preserve corrective history when an accepted corrective
    report cites an earlier report path.
    """

    seen: set[str] = set()
    out: list[str] = []
    for match in _CITED_REPORT_RE.finditer(content):
        val = match.group(1).strip()
        if not val or val in seen:
            continue
        if exclude and Path(val).name == exclude:
            continue
        seen.add(val)
        out.append(val)
    return tuple(out)


# ---------------------------------------------------------------------------
# File-reading collectors (read-only)
# ---------------------------------------------------------------------------


def collect_accepted_follow_ups(
    reviews_dir: Path | str,
    *,
    path_prefix: str = "05_governance/reviews",
) -> FollowUpCollectionResult:
    """Collect accepted follow-ups from review reports in ``reviews_dir``.

    A review report is *accepted* when its verdict parses as ``pass`` and a
    matching ``*_verdict_record.md`` sidecar exists next to it. Only accepted
    reports contribute follow-up evidence; ``needs_work``/``blocked`` reports
    and pass reports lacking a verdict record do not (the latter produces a
    diagnostic). An accepted corrective report additionally contributes the
    earlier report paths it cites, as ``CORRECTION`` items, to preserve
    history. ``path_prefix`` is prepended to report filenames to form relative
    repository ``source_path`` values (pass ``""`` for bare names). Read-only;
    never writes and never raises.
    """

    try:
        reviews_path = reviews_dir if isinstance(reviews_dir, Path) else Path(reviews_dir)
    except Exception:
        return FollowUpCollectionResult((), ("reviews_dir is not a valid path",))

    if not reviews_path.is_dir():
        return FollowUpCollectionResult((), (f"reviews directory not found: {reviews_path}",))

    def _rel(name: str) -> str:
        return f"{path_prefix}/{name}" if path_prefix else name

    items: list[FollowUpItem] = []
    diagnostics: list[str] = []

    for report in sorted(reviews_path.glob(f"*{_REVIEW_REPORT_SUFFIX}")):
        name = report.name
        try:
            content = report.read_text(encoding="utf-8")
        except OSError as exc:
            diagnostics.append(f"could not read {name}: {exc}")
            continue

        verdict_result = parse_review_report_verdict_text(content)
        is_pass = verdict_result.valid and verdict_result.verdict == ReviewVerdict.PASS
        if not is_pass:
            continue

        record = report.with_name(name[: -len(_REVIEW_REPORT_SUFFIX)] + _VERDICT_RECORD_SUFFIX)
        if not record.is_file():
            diagnostics.append(f"pass report {name} has no verdict record; treated as unaccepted")
            continue

        slice_id = _slice_id_from_review_name(name)
        items.extend(
            extract_follow_ups_from_review_text(
                content,
                _rel(name),
                source_slice_id=slice_id,
                accepted=True,
            )
        )

        if "corrective" in name.lower():
            for cited in _extract_cited_reports(content, exclude=name):
                items.append(
                    FollowUpItem(
                        source_path=cited,
                        text=f"cited by accepted corrective report {name}",
                        kind=FollowUpKind.CORRECTION,
                        source_slice_id=_slice_id_from_review_name(Path(cited).name),
                        accepted=True,
                    )
                )

    if not items and not diagnostics:
        diagnostics.append("no accepted follow-up evidence found")

    return FollowUpCollectionResult(tuple(items), tuple(diagnostics))


def collect_known_divergences(
    root: Path | str,
) -> tuple[tuple[KnownDivergence, ...], tuple[str, ...]]:
    """Read ``05_governance/known_divergences.md`` under ``root`` and parse it.

    Returns ``(entries, diagnostics)``. A missing file yields a diagnostic and
    no entries. Read-only; never writes and never raises.
    """

    root_path = root if isinstance(root, Path) else Path(root)
    rel = "05_governance/known_divergences.md"
    kd_path = root_path / "05_governance" / "known_divergences.md"
    if not kd_path.is_file():
        return (), ("known_divergences.md not found",)
    try:
        content = kd_path.read_text(encoding="utf-8")
    except OSError as exc:
        return (), (f"could not read known_divergences.md: {exc}",)
    return parse_known_divergences_text(content, rel)


def collect_second_pass_evidence(
    root: Path | str,
    *,
    slice_id: str = "",
) -> SecondPassEvidence:
    """Collect combined second-pass evidence from repository artifacts.

    Combines :func:`collect_accepted_follow_ups` over
    ``05_governance/reviews`` with :func:`collect_known_divergences`. Read-only;
    never writes, never mutates memory, and never changes frontier inference.
    Never raises.
    """

    root_path = root if isinstance(root, Path) else Path(root)
    follow_ups = collect_accepted_follow_ups(root_path / "05_governance" / "reviews")
    divergences, kd_diagnostics = collect_known_divergences(root_path)
    diagnostics = tuple(follow_ups.diagnostics) + tuple(kd_diagnostics)
    return SecondPassEvidence(
        slice_id=slice_id,
        accepted_follow_ups=follow_ups.items,
        known_divergences=divergences,
        diagnostics=diagnostics,
    )
