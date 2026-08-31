"""M003-S01: durable rework-context mapping and seat role purity.

Two surfaces over the M001 typed model (:mod:`frutlups.slice_prompt`) and the
M002 evidence-admission seam (:mod:`frutlups.prompt_health`); neither is
reimplemented here.

- :func:`resolve_rework_context` binds one corrective entry to its controlling
  findings, its prior evidence, and its controlling ruling **by identity**: every
  ``prior_evidence`` row must name an admitted regular file under the repository
  root whose bytes hash to the recorded digest, and the ruling must be an admitted
  record. Prior evidence is referenced by path and digest and is never rewritten;
  a forged or drifted digest, an absent record, or an escaping reference refuses
  the whole mapping so no partial context can be acted on.
- :func:`check_seat_write` / :func:`enforce_seat_writes` decide, before any
  publication, whether a seat (``coder``, ``reviewer``, ``architect_reviewer``,
  ``human_owner``, ``runner``) may write one artifact. The role/type matrix and
  the reserved-path classification are consumed from the layout declaration
  (``ContractVocab``), never restated: a write is admitted only when its path is
  exactly one manifest row owned by the declaring seat, the row labels the same
  artifact type, and the artifact's *effective* type — the reserved-path
  classification when the path carries one, else the label — lies in the seat's
  matrix row. Labelling a ``_review_report.md`` path ``governance_record`` does
  not bypass the matrix. The manifest is resolved only for the entry's one
  declared attempt identity (M001's ``_effective_attempt`` rule): an omitted
  attempt derives ``entry.attempt``, and a supplied attempt that differs refuses
  with ``attempt_mismatch`` before any manifest row is looked up, so a caller
  cannot mint authority for another attempt by changing the argument and the
  path together.

Both surfaces are pure decisions: they read recorded artifacts only through the
admitted-file seam and never write.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from frutlups.prompt_health import _admitted_local_file, _lexical_local_reference, _sha256_file
from frutlups.slice_prompt import (
    FINDING_REQUIRED,
    SHA256_RE,
    ContractVocab,
    Diagnostic,
    SliceEntry,
    SlicePromptError,
    WriteEntry,
    _classify_reserved,
    _effective_attempt,
    _path_problem,
)

# Reason codes this module emits, in stable order.
REWORK_CONTEXT_CODES = (
    "rework_context_not_corrective",
    "rework_finding_invalid",
    "rework_finding_duplicate",
    "rework_evidence_invalid",
    "rework_evidence_absent",
    "rework_evidence_digest_mismatch",
    "rework_ruling_absent",
)
SEAT_WRITE_REFUSAL_CODES = (
    "seat_role_invalid",
    "write_path_invalid",
    "reserved_artifact_mislabeled",
    "role_type_incompatible",
    "attempt_mismatch",
    "write_outside_manifest",
    "write_role_mismatch",
    "write_type_mismatch",
)


# --- rework context ----------------------------------------------------------


@dataclass(frozen=True)
class ControllingFinding:
    """One controlling finding of a corrective entry (contract section 6)."""

    id: str
    violated_invariant: str
    prior_disposition: str
    authority_action: str
    coder_obligation: str
    closure_proof: str

    def to_dict(self) -> dict[str, str]:
        return {key: getattr(self, key) for key in FINDING_REQUIRED}


@dataclass(frozen=True)
class EvidenceIdentity:
    """One prior-evidence record: its repository-relative path and digest."""

    path: str
    sha256: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "sha256": self.sha256}


@dataclass(frozen=True)
class ReworkContext:
    """The resolved binding of a corrective entry to its controlling artifacts."""

    slice_id: str
    attempt: str | None
    findings: tuple[ControllingFinding, ...]
    prior_evidence: tuple[EvidenceIdentity, ...]
    controlling_ruling: str
    ruling_disputed: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "slice": self.slice_id,
            "attempt": self.attempt,
            "findings": [f.to_dict() for f in self.findings],
            "prior_evidence": [p.to_dict() for p in self.prior_evidence],
            "controlling_ruling": self.controlling_ruling,
            "ruling_disputed": self.ruling_disputed,
        }


@dataclass(frozen=True)
class ReworkResolution:
    """The outcome of :func:`resolve_rework_context`: a context or refusals, never both."""

    entry_id: str
    context: ReworkContext | None
    diagnostics: tuple[Diagnostic, ...]

    @property
    def ok(self) -> bool:
        return self.context is not None

    def diagnostic_codes(self) -> tuple[str, ...]:
        return tuple(d.code for d in self.diagnostics)


def _admitted_record(repo_root: Path, reference: Any) -> Path | None:
    """Lexical rejection, then strict canonical admission; ``None`` refuses."""

    norm = _lexical_local_reference(reference)
    return None if norm is None else _admitted_local_file(repo_root, norm)


def resolve_rework_context(entry: SliceEntry, repo_root: Path) -> ReworkResolution:
    """Bind ``entry`` to its controlling findings, prior evidence, and ruling by identity.

    The mapping is produced only when every finding is complete and unique, every
    prior-evidence row is an admitted regular file under ``repo_root`` hashing to
    its recorded digest, and the controlling ruling (plain or disputed) is an
    admitted record. Any refusal yields no context.
    """

    loc = entry.slice_id
    diagnostics: list[Diagnostic] = []

    def err(code: str, message: str) -> None:
        diagnostics.append(Diagnostic(code, loc, message))

    correction = entry.data.get("correction")
    if not entry.corrective or not isinstance(correction, Mapping):
        err(
            "rework_context_not_corrective",
            "rework context exists only for a corrective entry with a correction block",
        )
        return ReworkResolution(loc, None, tuple(diagnostics))

    findings: list[ControllingFinding] = []
    seen_ids: set[str] = set()
    raw_findings = correction.get("findings")
    if not isinstance(raw_findings, list) or not raw_findings:
        err("rework_finding_invalid", "correction.findings must be a non-empty list")
    else:
        for index, raw in enumerate(raw_findings):
            if not isinstance(raw, Mapping) or not all(
                isinstance(raw.get(key), str) and raw[key].strip() for key in FINDING_REQUIRED
            ):
                err(
                    "rework_finding_invalid",
                    f"finding {index} requires " + ", ".join(FINDING_REQUIRED),
                )
                continue
            if raw["id"] in seen_ids:
                err(
                    "rework_finding_duplicate",
                    f"finding id {raw['id']!r} is declared more than once",
                )
                continue
            seen_ids.add(raw["id"])
            findings.append(ControllingFinding(**{key: raw[key] for key in FINDING_REQUIRED}))

    evidence: list[EvidenceIdentity] = []
    raw_evidence = correction.get("prior_evidence")
    if not isinstance(raw_evidence, list) or not raw_evidence:
        err("rework_evidence_invalid", "correction.prior_evidence must be a non-empty list")
    else:
        for index, raw in enumerate(raw_evidence):
            path_value = raw.get("path") if isinstance(raw, Mapping) else None
            digest = raw.get("sha256") if isinstance(raw, Mapping) else None
            if (
                not isinstance(path_value, str)
                or _path_problem(path_value) is not None
                or not isinstance(digest, str)
                or not SHA256_RE.match(digest)
            ):
                err(
                    "rework_evidence_invalid",
                    f"prior_evidence {index} requires an exact relative file path"
                    f" and a sha256 digest",
                )
                continue
            target = _admitted_record(repo_root, path_value)
            if target is None:
                err(
                    "rework_evidence_absent",
                    f"prior evidence is absent or not a regular file resolved under"
                    f" the repository root: {path_value}",
                )
                continue
            if _sha256_file(target) != digest:
                err(
                    "rework_evidence_digest_mismatch",
                    f"prior evidence bytes do not match the recorded digest: {path_value}",
                )
                continue
            evidence.append(EvidenceIdentity(path_value, digest))

    ruling = correction.get("controlling_ruling")
    disputed = False
    if isinstance(ruling, Mapping) and set(ruling) == {"disputed"}:
        disputed = True
        ruling = ruling.get("disputed")
    ruling_path = ruling if isinstance(ruling, str) else None
    if ruling_path is None or _admitted_record(repo_root, ruling_path) is None:
        err(
            "rework_ruling_absent",
            "controlling ruling is absent or not a regular file resolved under the repository root",
        )

    if diagnostics:
        return ReworkResolution(loc, None, tuple(diagnostics))
    assert ruling_path is not None  # a missing ruling already recorded a diagnostic
    context = ReworkContext(
        slice_id=loc,
        attempt=entry.attempt,
        findings=tuple(findings),
        prior_evidence=tuple(evidence),
        controlling_ruling=ruling_path,
        ruling_disputed=disputed,
    )
    return ReworkResolution(loc, context, ())


# --- seat role purity --------------------------------------------------------


def check_seat_write(
    entry: SliceEntry,
    vocab: ContractVocab,
    seat: str,
    path: str,
    artifact_type: str,
    *,
    attempt: str | None = None,
) -> tuple[Diagnostic, ...]:
    """Refusals for ``seat`` writing ``path`` labelled ``artifact_type``; empty admits.

    The write is admitted only when the seat is a declared role owner, the path
    is an exact repository-relative file path, the effective artifact type
    (reserved-path classification when present, else the label) is in the seat's
    layout matrix row, and the path is exactly one manifest row — resolved for
    the entry's declared attempt — owned by the seat with the same label. An
    omitted ``attempt`` derives the entry's; a supplied one must equal it, else
    the write refuses (``attempt_mismatch``) before any manifest lookup.
    """

    loc = f"{entry.slice_id}:{path}"
    refusals: list[Diagnostic] = []

    def err(code: str, message: str) -> None:
        refusals.append(Diagnostic(code, loc, message))

    if seat not in vocab.role_owners:
        err("seat_role_invalid", f"unknown seat role {seat!r}")
    problem = _path_problem(path)
    if problem is not None:
        err("write_path_invalid", f"invalid write path ({problem}): {path!r}")
        return tuple(refusals)

    reserved = _classify_reserved(path, vocab.reserved_path_classification)
    effective_type = reserved or artifact_type
    if reserved is not None and reserved != artifact_type:
        err(
            "reserved_artifact_mislabeled",
            f"path classifies as {reserved} but is labelled {artifact_type!r}",
        )
    if seat in vocab.role_owners and effective_type not in vocab.role_type_matrix.get(seat, ()):
        err("role_type_incompatible", f"{seat} may not own {effective_type}")

    try:
        effective = _effective_attempt(entry, attempt)
    except SlicePromptError as exc:
        err("attempt_mismatch", str(exc))
        return tuple(refusals)
    row = next(
        (
            w.resolved(vocab.attempt_token, effective)
            for w in entry.writes
            if w.resolved(vocab.attempt_token, effective).path == path
        ),
        None,
    )
    if row is None:
        err("write_outside_manifest", "path is not a write-manifest row of the entry")
    else:
        if row.role_owner != seat:
            err("write_role_mismatch", f"manifest row is owned by {row.role_owner}, not {seat}")
        if row.artifact_type != artifact_type:
            err(
                "write_type_mismatch",
                f"manifest row labels the artifact {row.artifact_type!r}, not {artifact_type!r}",
            )
    return tuple(refusals)


def enforce_seat_writes(
    entry: SliceEntry,
    vocab: ContractVocab,
    seat: str,
    writes: Sequence[WriteEntry],
    *,
    attempt: str | None = None,
) -> tuple[Diagnostic, ...]:
    """Every refusal for a seat's declared set of writes; empty means all admitted."""

    refusals: list[Diagnostic] = []
    for write in writes:
        refusals.extend(
            check_seat_write(entry, vocab, seat, write.path, write.artifact_type, attempt=attempt)
        )
    return tuple(refusals)
