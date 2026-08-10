"""Typed prompt inventory model for coding and review prompts.

This module models prompt files on disk under the project's
``prompts/for_coding_agent/`` and ``prompts/for_review_agent/``
directories. It provides:

- typed inventory artifacts (M003-S01)
- a pure numbering analyser (M003-S02)
- recognition of known inherited illustrative examples from the
  upstream template (M003-S03)

The module does not generate prompts, classify arbitrary stale prompts
beyond the exact known inherited list, or surface findings into
``frutlups status`` output; surfacing belongs to M003-S04.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from frutlups.artifacts import PromptDirectories

SEQUENCE_RE = re.compile(r"^(?P<n>\d+)_")


class PromptKind(StrEnum):
    """Logical kind of a prompt artifact on disk."""

    CODING = "coding"
    REVIEW = "review"


@dataclass(frozen=True)
class PromptArtifact:
    """A markdown prompt file inside one of the prompt directories.

    ``sequence`` is the zero-padded sequence number parsed from the
    filename when it matches the project convention (for example,
    ``001_some_slice.md`` → ``1``). It is ``None`` for filenames that do
    not start with a numeric prefix. This slice does not judge whether
    a sequence is valid; gap and duplicate detection belong to a later
    M003 slice.
    """

    kind: PromptKind
    path: Path
    filename: str
    sequence: int | None

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "path": str(self.path),
            "filename": self.filename,
            "sequence": self.sequence,
        }


def parse_sequence(filename: str) -> int | None:
    """Return the leading numeric prefix of ``filename`` as an ``int``.

    Returns ``None`` when the filename does not start with one or more
    digits followed by ``_``. Never raises.
    """

    match = SEQUENCE_RE.match(filename)
    return int(match.group("n")) if match else None


def inventory_prompts_in_dir(directory: Path, kind: PromptKind) -> tuple[PromptArtifact, ...]:
    """Inventory markdown prompt files directly inside ``directory``.

    The result is sorted deterministically by filename. Missing
    directories yield an empty tuple. Non-file entries and non-markdown
    files are skipped. This function does not validate filenames; any
    ``*.md`` file is included regardless of whether it follows the
    project's zero-padded numbering convention.
    """

    if not directory.is_dir():
        return ()
    artifacts: list[PromptArtifact] = []
    for path in sorted(directory.glob("*.md"), key=lambda p: p.name):
        if not path.is_file():
            continue
        artifacts.append(
            PromptArtifact(
                kind=kind,
                path=path,
                filename=path.name,
                sequence=parse_sequence(path.name),
            )
        )
    return tuple(artifacts)


def inventory_prompts(
    prompt_dirs: PromptDirectories,
) -> tuple[PromptArtifact, ...]:
    """Inventory coding and review prompts under ``prompt_dirs``.

    Coding prompts appear first (sorted by filename), followed by review
    prompts (sorted by filename). Either group may be empty if its
    directory does not exist.
    """

    coding = inventory_prompts_in_dir(prompt_dirs.coding, PromptKind.CODING)
    review = inventory_prompts_in_dir(prompt_dirs.review, PromptKind.REVIEW)
    return coding + review


@dataclass(frozen=True)
class PromptInventoryFinding:
    """A typed observation about prompt inventory health.

    ``code`` is a stable snake_case identifier intended for tests and
    tooling. ``kind`` and ``sequence`` are optional because future
    finding codes may not be tied to a single prompt kind or sequence
    number; for the codes emitted by ``analyze_prompt_inventory`` both
    fields are always populated. ``filenames`` lists the affected
    prompt files (sorted by filename) and ``message`` is a
    human-readable explanation actionable for a local user.
    """

    code: str
    kind: PromptKind | None
    sequence: int | None
    filenames: tuple[str, ...]
    message: str

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "kind": self.kind.value if self.kind is not None else None,
            "sequence": self.sequence,
            "filenames": list(self.filenames),
            "message": self.message,
        }


def analyze_prompt_inventory(
    artifacts: Iterable[PromptArtifact],
    *,
    numbering: str = "per_kind_sequence",
    pairing: str = "same_sequence",
) -> tuple[PromptInventoryFinding, ...]:
    """Analyse ``artifacts`` for numbering gaps, duplicates, and unmatched pairs.

    This helper is pure: it inspects the supplied ``PromptArtifact``
    iterable and returns a deterministic tuple of findings. It does not
    touch the filesystem and is not coupled to ``ProjectStatus``.

    With the default ``per_kind_sequence`` numbering and ``same_sequence``
    pairing, findings are returned in this order:

    1. ``missing_prompt_sequence`` for coding prompts (ascending)
    2. ``missing_prompt_sequence`` for review prompts (ascending)
    3. ``duplicate_prompt_sequence`` for coding prompts (ascending)
    4. ``duplicate_prompt_sequence`` for review prompts (ascending)
    5. ``unmatched_coding_prompt`` (ascending)
    6. ``unmatched_review_prompt`` (ascending)

    With ``numbering="global_flat_sequence"`` (M003-S02, owner note 008),
    gaps and duplicates are computed over the one global sequence shared by
    both prompt kinds and reported with ``kind=None``. With
    ``pairing="workflow_metadata"``, no unmatched-pair findings are
    reported: pairing is decided by validated workflow metadata elsewhere,
    so equal-sequence absence is ordinary loop state, not a defect.

    Within each finding, ``filenames`` is sorted by filename. Artifacts
    with ``sequence is None`` are ignored by every check.
    """

    by_kind_sequence: dict[PromptKind, dict[int, list[str]]] = {
        PromptKind.CODING: defaultdict(list),
        PromptKind.REVIEW: defaultdict(list),
    }
    for artifact in artifacts:
        if artifact.sequence is None:
            continue
        by_kind_sequence[artifact.kind][artifact.sequence].append(artifact.filename)

    findings: list[PromptInventoryFinding] = []
    if numbering == "global_flat_sequence":
        global_sequence: dict[int, list[str]] = defaultdict(list)
        for kind_map in by_kind_sequence.values():
            for sequence, filenames in kind_map.items():
                global_sequence[sequence].extend(filenames)
        findings.extend(_missing_findings(global_sequence, None))
        findings.extend(_duplicate_findings(global_sequence, None))
    else:
        findings.extend(
            _missing_findings(by_kind_sequence[PromptKind.CODING], PromptKind.CODING)
        )
        findings.extend(
            _missing_findings(by_kind_sequence[PromptKind.REVIEW], PromptKind.REVIEW)
        )
        findings.extend(
            _duplicate_findings(by_kind_sequence[PromptKind.CODING], PromptKind.CODING)
        )
        findings.extend(
            _duplicate_findings(by_kind_sequence[PromptKind.REVIEW], PromptKind.REVIEW)
        )

    if pairing == "same_sequence":
        coding_sequences = set(by_kind_sequence[PromptKind.CODING])
        review_sequences = set(by_kind_sequence[PromptKind.REVIEW])
        findings.extend(
            _unmatched_findings(
                sorted(coding_sequences - review_sequences),
                by_kind_sequence[PromptKind.CODING],
                kind=PromptKind.CODING,
                code="unmatched_coding_prompt",
                partner_label="review",
            )
        )
        findings.extend(
            _unmatched_findings(
                sorted(review_sequences - coding_sequences),
                by_kind_sequence[PromptKind.REVIEW],
                kind=PromptKind.REVIEW,
                code="unmatched_review_prompt",
                partner_label="coding",
            )
        )
    return tuple(findings)


def _kind_label(kind: PromptKind | None) -> str:
    return kind.value if kind is not None else "global"


def _missing_findings(
    sequence_to_filenames: dict[int, list[str]],
    kind: PromptKind | None,
) -> list[PromptInventoryFinding]:
    if not sequence_to_filenames:
        return []
    highest = max(sequence_to_filenames)
    present = set(sequence_to_filenames)
    missing = sorted(set(range(1, highest + 1)) - present)
    findings: list[PromptInventoryFinding] = []
    for sequence in missing:
        findings.append(
            PromptInventoryFinding(
                code="missing_prompt_sequence",
                kind=kind,
                sequence=sequence,
                filenames=(),
                message=(
                    f"{_kind_label(kind)} prompt sequence {sequence:03d} is missing "
                    f"between {min(present):03d} and {highest:03d}."
                ),
            )
        )
    return findings


def _duplicate_findings(
    sequence_to_filenames: dict[int, list[str]],
    kind: PromptKind | None,
) -> list[PromptInventoryFinding]:
    findings: list[PromptInventoryFinding] = []
    for sequence in sorted(sequence_to_filenames):
        filenames = sequence_to_filenames[sequence]
        if len(filenames) <= 1:
            continue
        sorted_filenames = tuple(sorted(filenames))
        findings.append(
            PromptInventoryFinding(
                code="duplicate_prompt_sequence",
                kind=kind,
                sequence=sequence,
                filenames=sorted_filenames,
                message=(
                    f"{_kind_label(kind)} prompt sequence {sequence:03d} has "
                    f"duplicate filenames: {', '.join(sorted_filenames)}."
                ),
            )
        )
    return findings


def _unmatched_findings(
    sequences: list[int],
    sequence_to_filenames: dict[int, list[str]],
    *,
    kind: PromptKind,
    code: str,
    partner_label: str,
) -> list[PromptInventoryFinding]:
    findings: list[PromptInventoryFinding] = []
    for sequence in sequences:
        filenames = tuple(sorted(sequence_to_filenames[sequence]))
        findings.append(
            PromptInventoryFinding(
                code=code,
                kind=kind,
                sequence=sequence,
                filenames=filenames,
                message=(
                    f"{kind.value} prompt sequence {sequence:03d} has no "
                    f"matching {partner_label} prompt."
                ),
            )
        )
    return findings


KNOWN_INHERITED_PROMPTS: frozenset[tuple[PromptKind, str]] = frozenset(
    {
        (PromptKind.CODING, "001_geecomposer_core_foundations.md"),
        (PromptKind.CODING, "002_geecomposer_core_foundations_closure.md"),
        (PromptKind.CODING, "014_geecomposer_milestone_006_cleanup.md"),
        (PromptKind.REVIEW, "001_review_core_foundations.md"),
        (PromptKind.REVIEW, "002_review_core_foundations_corrective.md"),
        (PromptKind.REVIEW, "014_review_observation_count_cleanup.md"),
    }
)
"""Exact ``(kind, filename)`` pairs recognised as inherited template examples.

These are illustrative prompts copied from the upstream artifact-first
template. Recognition is intentionally exact: arbitrary filenames
containing ``geecomposer``, ``core_foundations``, or similar substrings
are not classified as inherited unless they match this set.
"""


class PromptClassification(StrEnum):
    """Stable classification for a prompt artifact."""

    PROJECT_PROMPT = "project_prompt"
    INHERITED_EXAMPLE = "inherited_example"


@dataclass(frozen=True)
class PromptArtifactClassification:
    """A typed classification result for a single ``PromptArtifact``.

    ``ignored_for_analysis`` is the load-bearing field: when ``True``
    the artifact should be excluded from M003-S02 prompt numbering
    analysis (and from any future analysis that should ignore
    inherited examples). ``reason`` is a short human-readable
    explanation suitable for a local user.
    """

    kind: PromptKind
    filename: str
    classification: PromptClassification
    ignored_for_analysis: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "filename": self.filename,
            "classification": self.classification.value,
            "ignored_for_analysis": self.ignored_for_analysis,
            "reason": self.reason,
        }


def classify_prompt_artifact(
    artifact: PromptArtifact,
) -> PromptArtifactClassification:
    """Classify a single prompt artifact.

    Returns ``inherited_example`` (and ``ignored_for_analysis=True``)
    when ``(artifact.kind, artifact.filename)`` is in
    :data:`KNOWN_INHERITED_PROMPTS`. Otherwise returns
    ``project_prompt`` (and ``ignored_for_analysis=False``). Matching
    is exact and case-sensitive; arbitrary unknown filenames remain
    project prompts.
    """

    if (artifact.kind, artifact.filename) in KNOWN_INHERITED_PROMPTS:
        return PromptArtifactClassification(
            kind=artifact.kind,
            filename=artifact.filename,
            classification=PromptClassification.INHERITED_EXAMPLE,
            ignored_for_analysis=True,
            reason=(
                f"Known inherited illustrative example from the upstream "
                f"artifact-first template ({artifact.filename})."
            ),
        )
    return PromptArtifactClassification(
        kind=artifact.kind,
        filename=artifact.filename,
        classification=PromptClassification.PROJECT_PROMPT,
        ignored_for_analysis=False,
        reason="frutlups project prompt.",
    )


def classify_prompt_inventory(
    artifacts: Iterable[PromptArtifact],
) -> tuple[PromptArtifactClassification, ...]:
    """Classify every artifact in ``artifacts`` in input order."""

    return tuple(classify_prompt_artifact(artifact) for artifact in artifacts)


def filter_prompt_artifacts_for_analysis(
    artifacts: Iterable[PromptArtifact],
) -> tuple[PromptArtifact, ...]:
    """Return artifacts that should participate in numbering analysis.

    Known inherited illustrative examples are filtered out; the
    relative order of remaining artifacts is preserved.
    """

    return tuple(
        artifact
        for artifact in artifacts
        if (artifact.kind, artifact.filename) not in KNOWN_INHERITED_PROMPTS
    )


class PromptHealthSeverity(StrEnum):
    """Severity attached to a prompt-health finding for presentation."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


_PROMPT_FINDING_SEVERITY: dict[str, PromptHealthSeverity] = {
    "missing_prompt_sequence": PromptHealthSeverity.WARNING,
    "duplicate_prompt_sequence": PromptHealthSeverity.WARNING,
    "unmatched_coding_prompt": PromptHealthSeverity.WARNING,
    "unmatched_review_prompt": PromptHealthSeverity.WARNING,
}


@dataclass(frozen=True)
class PromptHealthFinding:
    """A prompt-numbering finding plus a presentation severity.

    This wraps a :class:`PromptInventoryFinding` with a severity drawn
    from a small fixed mapping so callers can present findings as
    warnings or errors without re-parsing the code.
    """

    severity: PromptHealthSeverity
    code: str
    kind: PromptKind | None
    sequence: int | None
    filenames: tuple[str, ...]
    message: str

    def to_dict(self) -> dict[str, object]:
        return {
            "severity": self.severity.value,
            "code": self.code,
            "kind": self.kind.value if self.kind is not None else None,
            "sequence": self.sequence,
            "filenames": list(self.filenames),
            "message": self.message,
        }


@dataclass(frozen=True)
class PromptHealth:
    """Aggregated prompt-inventory health for a project.

    ``ok`` is derived from ``findings``: it is ``True`` only when every
    finding has ``info`` severity (and is therefore always ``True`` for
    an empty findings list). ``inherited`` counts artifacts classified
    as inherited examples; ``ignored`` counts artifacts that are
    excluded from analysis for any reason (currently only inherited
    examples, but the count is reported separately to leave room for
    future classifications). ``analyzed`` is ``total - ignored``.
    """

    ok: bool
    total: int
    inherited: int
    ignored: int
    analyzed: int
    findings: tuple[PromptHealthFinding, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "total": self.total,
            "inherited": self.inherited,
            "ignored": self.ignored,
            "analyzed": self.analyzed,
            "findings": [finding.to_dict() for finding in self.findings],
        }


def compute_prompt_health(
    artifacts: Iterable[PromptArtifact],
    *,
    numbering: str = "per_kind_sequence",
    pairing: str = "same_sequence",
) -> PromptHealth:
    """Compute prompt health from the existing M003 helper pipeline.

    The pipeline is:

    1. classify every artifact (M003-S03)
    2. filter inherited examples out of the analysis input (M003-S03)
    3. run the pure numbering analyser on the filtered artifacts
       (M003-S02)
    4. wrap each finding with the documented severity mapping

    Under the default modes, pairing is by parsed sequence number only and
    any unmatched coding or review prompt sequence, including the highest
    one, is a warning that flips ``ok`` to ``False``. The configured
    ``numbering``/``pairing`` modes (M003-S02, owner note 008) change only
    what :func:`analyze_prompt_inventory` reports; the health surface
    describes the configured convention instead of false per-kind gaps or
    unmatched pairs.
    """

    artifacts_tuple = tuple(artifacts)
    classifications = classify_prompt_inventory(artifacts_tuple)
    inherited = sum(
        1
        for classification in classifications
        if classification.classification == PromptClassification.INHERITED_EXAMPLE
    )
    ignored = sum(1 for classification in classifications if classification.ignored_for_analysis)
    total = len(artifacts_tuple)
    analyzed = total - ignored

    filtered = filter_prompt_artifacts_for_analysis(artifacts_tuple)
    raw_findings = analyze_prompt_inventory(
        filtered, numbering=numbering, pairing=pairing
    )
    findings = tuple(
        PromptHealthFinding(
            severity=_PROMPT_FINDING_SEVERITY.get(raw.code, PromptHealthSeverity.WARNING),
            code=raw.code,
            kind=raw.kind,
            sequence=raw.sequence,
            filenames=raw.filenames,
            message=raw.message,
        )
        for raw in raw_findings
    )
    ok = all(finding.severity == PromptHealthSeverity.INFO for finding in findings)

    return PromptHealth(
        ok=ok,
        total=total,
        inherited=inherited,
        ignored=ignored,
        analyzed=analyzed,
        findings=findings,
    )
