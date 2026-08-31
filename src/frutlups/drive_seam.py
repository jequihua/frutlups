"""M005-S02: the explicit, bounded subprocess Drive seam.

The frutlups-drive consumer binds this product only through ``python -m
frutlups`` subprocess verbs and versioned JSON documents; it never imports
Python, parses a roadmap or sidecar, or guesses a contract. This module is the
producer-side bridge that serializes and admits three already accepted internal
behaviors without changing them:

- ``drive-payload`` wraps the unmodified M001 ``frutlups.slice_prompt_payload.v1``
  object (:func:`frutlups.slice_prompt.drive_payload`) in
  ``frutlups.drive_payload.v1`` with an adoption block of exact identities;
- ``drive-frontier`` emits ``frutlups.frontier.v2`` from the M005 frontier
  transition (:mod:`frutlups.frontier`) with the milestone and last-slice
  position derived producer-side from the admitted sidecar;
- ``corrective-publish`` reads one ``frutlups.corrective_publication_proposal.v1``
  document from stdin, allocates the fresh attempt over the complete bounded M004
  history, materializes the entry, and runs the unchanged M004 transaction
  (:mod:`frutlups.publication`) either as a zero-write dry-run or as a
  publication, returning ``frutlups.corrective_publication_receipt.v1``.

Every unsupported version and pre-mutation input or admission failure is one
``frutlups.drive_seam_refusal.v1`` document on exit 3. Exit 0 carries a valid
payload, frontier, validated dry-run receipt, or published receipt; exit 3 also
carries a publication receipt with outcome ``refused`` when the mutation boundary
was reached and the exact readable pre-state restored; exit 4 always carries a
publication receipt with outcome ``recovery_required``. Argparse usage is exit 2
and is the only non-JSON exit in the contract.

Wire discipline: stdout documents are UTF-8 JSON with sorted keys,
``ensure_ascii=False``, separators ``(',', ':')`` and exactly one final LF;
canonical digests hash those bytes without the LF. Every sidecar, prompt, review
report, and proposal read is bounded at 1 MiB. No emitted value comes from an
environment binding, an absolute path, or the current working directory.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from frutlups.closure import FrutlupsRoute
from frutlups.frontier import frontier_transition_from_report_text
from frutlups.prompt_health import _admitted_local_file
from frutlups.publication import (
    PUBLISHED,
    REFUSED,
    ArtifactObservation,
    PresentObservation,
    PublicationError,
    PublicationResult,
    UnsafeObservation,
    _admit_sidecar_target,
    _alias_free_path,
    _load_layout_authority,
    _read_bounded,
    _repo_relative_norm,
    _slice_history,
    allocate_attempt,
    commit_prepared_publication,
    observe_owned_state,
    prepare_corrective_attempt,
)
from frutlups.slice_prompt import (
    SLICE_ID_RE,
    SLICE_REQUIRED,
    ContractVocab,
    SliceEntry,
    _is_junction,
    drive_payload,
    parse_sidecar,
    resolve_attempt,
)

__all__ = [
    "DETAIL_MAX_BYTES",
    "EXIT_OK",
    "EXIT_RECOVERY_REQUIRED",
    "EXIT_REFUSED",
    "EXIT_USAGE",
    "FRONTIER_SCHEMA",
    "INPUT_MAX_BYTES",
    "PAYLOAD_WRAPPER_SCHEMA",
    "PROPOSAL_SCHEMA",
    "RECEIPT_SCHEMA",
    "REFUSAL_SCHEMA",
    "ROUTE_STEPS",
    "SEAM_REFUSAL_CODES",
    "STDOUT_MAX_BYTES",
    "SeamResult",
    "VERB_FRONTIER",
    "VERB_PAYLOAD",
    "VERB_PUBLISH",
    "canonical_json_bytes",
    "run_corrective_publish",
    "run_drive_frontier",
    "run_drive_payload",
    "serialize_document",
]

PAYLOAD_WRAPPER_SCHEMA = "frutlups.drive_payload.v1"
FRONTIER_SCHEMA = "frutlups.frontier.v2"
PROPOSAL_SCHEMA = "frutlups.corrective_publication_proposal.v1"
RECEIPT_SCHEMA = "frutlups.corrective_publication_receipt.v1"
REFUSAL_SCHEMA = "frutlups.drive_seam_refusal.v1"

VERB_PAYLOAD = "drive-payload"
VERB_FRONTIER = "drive-frontier"
VERB_PUBLISH = "corrective-publish"

PAYLOAD_VERSION = "1"
FRONTIER_VERSION = "2"
PUBLISH_VERSION = "1"

INPUT_MAX_BYTES = 1_048_576
STDOUT_MAX_BYTES = 2_097_152
DETAIL_MAX_BYTES = 1024
PATH_MAX_BYTES = 4096

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_REFUSED = 3
EXIT_RECOVERY_REQUIRED = 4

MODE_DRY_RUN = "dry_run"
MODE_PUBLISH = "publish"
OUTCOME_VALIDATED = "validated"

# The one-to-one route -> step derivation of frontier-v2 (route stays authoritative).
ROUTE_STEPS: Mapping[str, str] = {
    "advance_to_next_slice": "advance_slice",
    "milestone_complete": "complete_milestone",
    "recode_same_slice": "recode_slice",
    "unblock_same_slice": "unblock_slice",
    "human_override_required": "human_gate",
    "invalid": "stop_invalid",
}

# Seam-owned refusal codes. Every public M004 refusal code
# (:data:`frutlups.publication.PUBLICATION_REFUSAL_CODES`) is emitted unchanged
# beside these when the corrective transaction refuses before mutation.
SEAM_REFUSAL_CODES = (
    "unsupported_version",
    "malformed_json",
    "project_root_unavailable",
    "sidecar_absent",
    "sidecar_unreadable",
    "sidecar_oversized",
    "sidecar_invalid",
    "slice_invalid",
    "routing_status_invalid",
    "prompt_absent",
    "prompt_unreadable",
    "prompt_oversized",
    "review_report_absent",
    "review_report_unreadable",
    "review_report_oversized",
    "proposal_empty",
    "proposal_oversized",
    "proposal_invalid",
    "proposal_target_mismatch",
    "payload_oversized",
)

_PROPOSAL_KEYS = frozenset(
    {"schema", "version", "slice", "sidecar_path", "prompt_path", "entry_template"}
)


@dataclass(frozen=True)
class SeamResult:
    """One verb outcome: the exit code and the single stdout document."""

    exit_code: int
    document: Mapping[str, Any]


# --- wire encoding -----------------------------------------------------------


def canonical_json_bytes(document: Any) -> bytes:
    """The canonical digest bytes: sorted keys, compact separators, raw UTF-8, no LF."""

    return json.dumps(
        document, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def serialize_document(document: Any) -> bytes:
    """The stdout form: the canonical bytes plus exactly one final LF."""

    return canonical_json_bytes(document) + b"\n"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _bounded_detail(text: str) -> str:
    """Non-empty UTF-8 evidence of at most :data:`DETAIL_MAX_BYTES`."""

    raw = (text or "no further detail").encode("utf-8")
    if len(raw) <= DETAIL_MAX_BYTES:
        return raw.decode("utf-8")
    return raw[:DETAIL_MAX_BYTES].decode("utf-8", errors="ignore") or "detail truncated"


def _refusal(verb: str, code: str, detail: str) -> SeamResult:
    return SeamResult(
        EXIT_REFUSED,
        {
            "schema": REFUSAL_SCHEMA,
            "version": 1,
            "verb": verb,
            "code": code,
            "detail": _bounded_detail(detail),
        },
    )


def _observation_to_dict(observation: ArtifactObservation) -> dict[str, str]:
    typed: dict[str, str] = {"state": observation.state}
    if isinstance(observation, PresentObservation):
        typed["sha256"] = observation.sha256
    elif isinstance(observation, UnsafeObservation):
        typed["identity"] = observation.identity
    return typed


def _typed_map(observed: Mapping[str, ArtifactObservation]) -> dict[str, dict[str, str]]:
    return {key: _observation_to_dict(value) for key, value in observed.items()}


# --- shared admission --------------------------------------------------------


def _admit_root(verb: str, project_root: str) -> Path | SeamResult:
    """Admit PROJECT_ROOT as cwd-independent repository authority (M005-R1-F1).

    Only an absolute spelling is admitted; a relative, ``.``, or ``..`` value is
    refused before any resolution, so the process cwd never selects evidence or
    mutation authority.
    """

    try:
        if not Path(project_root).is_absolute():
            return _refusal(
                verb,
                "project_root_unavailable",
                "PROJECT_ROOT must be an absolute path; relative spellings are never"
                " resolved against cwd",
            )
        root = Path(project_root).resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError):
        return _refusal(
            verb, "project_root_unavailable", "PROJECT_ROOT is absent or cannot be resolved"
        )
    if not root.is_dir():
        return _refusal(verb, "project_root_unavailable", "PROJECT_ROOT is not a directory")
    return root


def _load_authority(verb: str, root: Path) -> tuple[Any, ContractVocab] | SeamResult:
    loaded = _load_layout_authority(root)
    if not isinstance(loaded, tuple):
        return _refusal(verb, loaded.code, loaded.message)
    _root, layout, vocab = loaded
    return layout, vocab


def _read_governed(
    verb: str, root: Path, reference: str, kind: str
) -> tuple[str, Path, bytes] | SeamResult:
    """Admit one repository-relative input and read it within the 1 MiB bound.

    ``kind`` names the ``<kind>_absent`` / ``<kind>_unreadable`` /
    ``<kind>_oversized`` codes; lexical, containment, and alias failures reuse the
    M004 ``target_unbound`` code. Admission order (M005-R1-F2): pure lexical
    validation, then component-wise alias rejection of every intermediate
    directory, and only then the first identity access of the final child — a
    non-following ``lstat`` through :func:`_lstat_target` that proves absence —
    followed by the final component's own alias check, canonical admission, and
    the bounded read. Outside-root state beneath an intermediate alias can never
    change the refusal because that child is never probed.
    """

    norm = _repo_relative_norm(reference)
    if norm is None:
        return _refusal(
            verb, "target_unbound", f"{kind} path is not a lexical repository-relative file path"
        )
    if _intermediate_alias(root, norm):
        return _refusal(
            verb, "target_unbound", f"{kind} has an intermediate alias component: {norm}"
        )
    lexical = root.joinpath(*norm.split("/"))
    try:
        _lstat_target(lexical)
    except FileNotFoundError:
        return _refusal(verb, f"{kind}_absent", f"{kind} is absent: {norm}")
    except OSError:
        return _refusal(verb, f"{kind}_unreadable", f"{kind} identity is unreadable: {norm}")
    if not _alias_free_path(root, norm):
        return _refusal(verb, "target_unbound", f"{kind} is an alias: {norm}")
    admitted = _admitted_local_file(root, norm)
    if admitted is None:
        return _refusal(
            verb,
            "target_unbound",
            f"{kind} is not a regular file resolved under PROJECT_ROOT: {norm}",
        )
    try:
        raw = _read_bounded(admitted, INPUT_MAX_BYTES)
    except OSError:
        return _refusal(verb, f"{kind}_unreadable", f"{kind} cannot be read: {norm}")
    if len(raw) > INPUT_MAX_BYTES:
        return _refusal(
            verb, f"{kind}_oversized", f"{kind} exceeds {INPUT_MAX_BYTES} bytes: {norm}"
        )
    return norm, admitted, raw


def _intermediate_alias(root: Path, norm: str) -> bool:
    """Whether any directory component strictly above the final child is an alias.

    Only the intermediate prefixes are observed; the final child is never
    touched here. An unreadable intermediate identity is treated as an alias
    (fail closed).
    """

    candidate = root
    try:
        for part in norm.split("/")[:-1]:
            candidate = candidate / part
            if candidate.is_symlink() or _is_junction(candidate):
                return True
    except OSError:
        return True
    return False


def _lstat_target(path: Path) -> os.stat_result:
    """The explicit first identity access of a governed final child (seam)."""

    return os.lstat(path)


def _select_entry(
    verb: str, vocab: ContractVocab, raw: bytes, sidecar: Path, slice_id: str
) -> tuple[tuple[SliceEntry, ...], SliceEntry] | SeamResult:
    """Parse the admitted sidecar as indivisible authority and select one entry."""

    if not SLICE_ID_RE.match(slice_id):
        return _refusal(verb, "slice_invalid", f"SLICE is not an Mnnn-Snn identity: {slice_id!r}")
    parsed = parse_sidecar(raw, vocab, sidecar_path=sidecar)
    if parsed.diagnostics:
        return _refusal(
            verb,
            "sidecar_invalid",
            "sidecar has diagnostics: " + ", ".join(parsed.diagnostic_codes()),
        )
    matches = tuple(entry for entry in parsed.entries if entry.slice_id == slice_id)
    if len(matches) != 1:
        return _refusal(
            verb,
            "slice_not_in_sidecar",
            f"slice {slice_id} does not have exactly one sidecar entry",
        )
    return parsed.entries, matches[0]


# --- drive-payload -----------------------------------------------------------


def run_drive_payload(
    *, project_root: str, sidecar: str, slice_id: str, prompt: str, version: str
) -> SeamResult:
    """Emit ``frutlups.drive_payload.v1`` or a seam refusal."""

    verb = VERB_PAYLOAD
    if version != PAYLOAD_VERSION:
        return _refusal(
            verb, "unsupported_version", f"drive-payload supports version {PAYLOAD_VERSION} only"
        )
    root = _admit_root(verb, project_root)
    if isinstance(root, SeamResult):
        return root
    authority = _load_authority(verb, root)
    if isinstance(authority, SeamResult):
        return authority
    _layout, vocab = authority
    sidecar_read = _read_governed(verb, root, sidecar, "sidecar")
    if isinstance(sidecar_read, SeamResult):
        return sidecar_read
    _sidecar_norm, sidecar_target, sidecar_bytes = sidecar_read
    selected = _select_entry(verb, vocab, sidecar_bytes, sidecar_target, slice_id)
    if isinstance(selected, SeamResult):
        return selected
    _entries, entry = selected
    prompt_read = _read_governed(verb, root, prompt, "prompt")
    if isinstance(prompt_read, SeamResult):
        return prompt_read
    prompt_norm, _prompt_target, prompt_bytes = prompt_read

    token = vocab.attempt_token
    attempt = entry.attempt
    self_report_path = entry.self_report_path(token, attempt)
    if self_report_path is None:
        return _refusal(verb, "sidecar_invalid", "entry declares no coder-owned self_report write")
    correction = entry.data.get("correction")
    prior_evidence = (
        [{"path": row["path"], "sha256": row["sha256"]} for row in correction["prior_evidence"]]
        if entry.corrective
        and isinstance(correction, Mapping)
        and isinstance(correction.get("prior_evidence"), list)
        else []
    )
    document = {
        "schema": PAYLOAD_WRAPPER_SCHEMA,
        "version": 1,
        "payload": drive_payload(entry, vocab),
        "adoption": {
            "slice": entry.slice_id,
            "attempt": attempt,
            "prompt_path": prompt_norm,
            "prompt_sha256": _sha256(prompt_bytes),
            "self_report_path": self_report_path,
            "evidence_paths": [
                write.resolved(token, attempt).path
                for write in entry.writes
                if write.artifact_type == "evidence"
            ],
            "prior_evidence": prior_evidence,
        },
    }
    if len(serialize_document(document)) > STDOUT_MAX_BYTES:
        return _refusal(
            verb, "payload_oversized", f"payload document exceeds {STDOUT_MAX_BYTES} bytes"
        )
    return SeamResult(EXIT_OK, document)


# --- drive-frontier ----------------------------------------------------------


def _slice_number(entry: SliceEntry) -> int:
    return int(entry.slice_id[-2:])


def run_drive_frontier(
    *,
    project_root: str,
    sidecar: str,
    slice_id: str,
    review_report: str,
    version: str,
    explicit_routing_status: str | None = None,
) -> SeamResult:
    """Emit ``frutlups.frontier.v2`` or a seam refusal."""

    verb = VERB_FRONTIER
    if version != FRONTIER_VERSION:
        return _refusal(
            verb, "unsupported_version", f"drive-frontier supports version {FRONTIER_VERSION} only"
        )
    if (
        explicit_routing_status is not None
        and explicit_routing_status not in FrutlupsRoute.__members__.values()
    ):
        return _refusal(
            verb,
            "routing_status_invalid",
            "explicit routing status is outside the route vocabulary",
        )
    root = _admit_root(verb, project_root)
    if isinstance(root, SeamResult):
        return root
    authority = _load_authority(verb, root)
    if isinstance(authority, SeamResult):
        return authority
    _layout, vocab = authority
    sidecar_read = _read_governed(verb, root, sidecar, "sidecar")
    if isinstance(sidecar_read, SeamResult):
        return sidecar_read
    _sidecar_norm, sidecar_target, sidecar_bytes = sidecar_read
    selected = _select_entry(verb, vocab, sidecar_bytes, sidecar_target, slice_id)
    if isinstance(selected, SeamResult):
        return selected
    entries, entry = selected
    report_read = _read_governed(verb, root, review_report, "review_report")
    if isinstance(report_read, SeamResult):
        return report_read
    _report_norm, _report_target, report_bytes = report_read
    try:
        report_text = report_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return _refusal(verb, "review_report_unreadable", "review report is not valid UTF-8")

    milestone = entry.milestone
    is_last_slice = not any(
        other.milestone == milestone and _slice_number(other) > _slice_number(entry)
        for other in entries
    )
    transition = frontier_transition_from_report_text(
        report_text, is_last_slice=is_last_slice, explicit_routing_status=explicit_routing_status
    )
    route = transition.route.value
    receipt = transition.receipt.to_dict() if transition.receipt is not None else None
    document = {
        "schema": FRONTIER_SCHEMA,
        "version": 2,
        "milestone": milestone,
        "slice": entry.slice_id,
        "step": ROUTE_STEPS[route],
        "outcome": route,
        "route": route,
        "milestone_complete": transition.milestone_complete,
        "reason": _bounded_detail(transition.reason),
        "receipt": receipt,
        "receipt_sha256": _sha256(canonical_json_bytes(receipt)) if receipt is not None else None,
    }
    return SeamResult(EXIT_OK, document)


# --- corrective-publish ------------------------------------------------------


class _ProposalDefect(Exception):
    """A JSON-level defect the proposal contract names ``proposal_invalid``."""


def _pairs_without_duplicates(items: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in items]
    if len(set(keys)) != len(keys):
        raise _ProposalDefect("duplicate JSON object key")
    return dict(items)


def _reject_constant(name: str) -> Any:
    raise _ProposalDefect(f"non-finite number {name}")


def _parse_proposal(verb: str, raw: bytes) -> dict[str, Any] | SeamResult:
    """Decode and shape-check one proposal; never raises for any byte string."""

    def invalid(detail: str) -> SeamResult:
        return _refusal(verb, "proposal_invalid", detail)

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return invalid("proposal is not valid UTF-8")
    try:
        value = json.loads(
            text, object_pairs_hook=_pairs_without_duplicates, parse_constant=_reject_constant
        )
    except _ProposalDefect as exc:
        return invalid(str(exc))
    except (ValueError, RecursionError):
        return _refusal(verb, "malformed_json", "proposal is not one JSON document")
    if not isinstance(value, dict):
        return invalid("proposal is not a JSON object")
    if set(value) != _PROPOSAL_KEYS:
        return invalid("proposal keys must be exactly " + ", ".join(sorted(_PROPOSAL_KEYS)))
    if value["schema"] != PROPOSAL_SCHEMA:
        return invalid(f"proposal schema must be {PROPOSAL_SCHEMA}")
    if type(value["version"]) is not int or value["version"] != 1:
        return invalid("proposal version must be integer 1")
    slice_id = value["slice"]
    if not isinstance(slice_id, str) or not SLICE_ID_RE.match(slice_id):
        return invalid("proposal slice must be an Mnnn-Snn string")
    for key in ("sidecar_path", "prompt_path"):
        path = value[key]
        if not isinstance(path, str) or not path or len(path.encode("utf-8")) > PATH_MAX_BYTES:
            return invalid(
                f"proposal {key} must be a non-empty string of at most {PATH_MAX_BYTES} bytes"
            )
        if _repo_relative_norm(path) != path:
            return invalid(f"proposal {key} is not a canonical repository-relative POSIX path")
    template = value["entry_template"]
    if not isinstance(template, dict):
        return invalid("proposal entry_template must be a JSON object")
    if "attempt" in template:
        return invalid("proposal entry_template must not carry attempt; frutlups allocates it")
    missing = [field for field in SLICE_REQUIRED if field not in template]
    if missing:
        return invalid("proposal entry_template lacks required fields: " + ", ".join(missing))
    if template["slice"] != slice_id:
        return invalid("proposal slice must equal entry_template.slice")
    return value


def _materialize_entry(template: Mapping[str, Any], attempt: str) -> dict[str, Any]:
    """Insert the allocated attempt beside the dispatch identity, preserving order."""

    anchor = "dispatch_authority" if "dispatch_authority" in template else "status"
    materialized: dict[str, Any] = {}
    for key, value in template.items():
        materialized[key] = value
        if key == anchor:
            materialized["attempt"] = attempt
    if "attempt" not in materialized:
        materialized["attempt"] = attempt
    return materialized


def run_corrective_publish(
    *,
    project_root: str,
    sidecar: str,
    prompt: str,
    version: str,
    proposal_bytes: bytes,
    dry_run: bool,
) -> SeamResult:
    """Run the corrective transaction as dry-run or publication; emit its receipt."""

    verb = VERB_PUBLISH
    if version != PUBLISH_VERSION:
        return _refusal(
            verb,
            "unsupported_version",
            f"corrective-publish supports version {PUBLISH_VERSION} only",
        )
    if not proposal_bytes:
        return _refusal(verb, "proposal_empty", "no proposal bytes were received on stdin")
    if len(proposal_bytes) > INPUT_MAX_BYTES:
        return _refusal(verb, "proposal_oversized", f"proposal exceeds {INPUT_MAX_BYTES} bytes")
    proposal = _parse_proposal(verb, proposal_bytes)
    if isinstance(proposal, SeamResult):
        return proposal
    if proposal["sidecar_path"] != sidecar or proposal["prompt_path"] != prompt:
        return _refusal(
            verb,
            "proposal_target_mismatch",
            "argv --sidecar/--prompt must equal proposal sidecar_path/prompt_path byte for byte",
        )
    slice_id: str = proposal["slice"]

    root = _admit_root(verb, project_root)
    if isinstance(root, SeamResult):
        return root
    authority = _load_authority(verb, root)
    if isinstance(authority, SeamResult):
        return authority
    layout, vocab = authority
    token = vocab.attempt_token
    if prompt.count(token) != 1:
        return _refusal(
            verb,
            "proposal_invalid",
            f"prompt_path must carry exactly one attempt placeholder {token}",
        )

    sidecar_read = _read_governed(verb, root, sidecar, "sidecar")
    if isinstance(sidecar_read, SeamResult):
        return sidecar_read
    _sidecar_norm, _sidecar_target, sidecar_bytes = sidecar_read
    _norm, sidecar_target, sidecar_refusal = _admit_sidecar_target(root, layout, sidecar)
    if sidecar_refusal is not None:
        return _refusal(verb, sidecar_refusal.code, sidecar_refusal.message)
    assert sidecar_target is not None

    # Complete bounded history and fresh allocation, exactly as the M004 transaction
    # decides them; the transaction repeats this proof over the materialized entry.
    parsed = parse_sidecar(sidecar_bytes, vocab, sidecar_path=sidecar_target)
    if parsed.diagnostics:
        return _refusal(
            verb,
            "history_unresolved",
            "the complete current sidecar is invalid: " + ", ".join(parsed.diagnostic_codes()),
        )
    matches = tuple(entry for entry in parsed.entries if entry.slice_id == slice_id)
    if not matches:
        return _refusal(
            verb, "slice_not_in_sidecar", f"slice {slice_id} is not an entry of the sidecar"
        )
    if len(matches) != 1:
        return _refusal(
            verb,
            "history_unresolved",
            f"slice {slice_id} does not have exactly one typed sidecar entry",
        )
    history, history_refusal = _slice_history(root, layout, matches[0], slice_id)
    if history_refusal is not None:
        return _refusal(verb, history_refusal.code, history_refusal.message)
    assert history is not None
    try:
        attempt = allocate_attempt(history)
    except PublicationError as exc:
        return _refusal(
            verb,
            "attempt_not_fresh",
            f"no fresh attempt over the bounded history {sorted(history)}: {exc}",
        )

    entry_data = _materialize_entry(proposal["entry_template"], attempt)
    prompt_norm = resolve_attempt(prompt, token, attempt)
    prepared = prepare_corrective_attempt(
        entry=SliceEntry(entry_data), repo_root=root, sidecar_path=sidecar, prompt_path=prompt_norm
    )
    if isinstance(prepared, PublicationResult):
        return _refusal(
            verb,
            prepared.refusals[0].code,
            "; ".join(f"{d.code}: {d.message}" for d in prepared.refusals),
        )

    if dry_run:
        before = _typed_map(observe_owned_state(prepared))
        after = _typed_map(observe_owned_state(prepared))
        outcome, refusal_codes, exit_code = OUTCOME_VALIDATED, [], EXIT_OK
    else:
        result = commit_prepared_publication(prepared)
        assert result.before is not None and result.after is not None
        before = _typed_map(result.before)
        after = _typed_map(result.after)
        outcome = result.outcome
        refusal_codes = list(result.refusal_codes())
        exit_code = (
            EXIT_OK
            if outcome == PUBLISHED
            else EXIT_REFUSED
            if outcome == REFUSED
            else EXIT_RECOVERY_REQUIRED
        )

    proposal_sha256 = _sha256(proposal_bytes)
    receipt: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "version": 1,
        "mode": MODE_DRY_RUN if dry_run else MODE_PUBLISH,
        "transaction_id": "cp." + proposal_sha256,
        "proposal_sha256": proposal_sha256,
        "slice": slice_id,
        "attempt": attempt,
        "outcome": outcome,
        "sidecar_entry": {
            "path": prepared.sidecar_key,
            "sha256": _sha256(canonical_json_bytes(entry_data)),
        },
        "rendered_prompt": {"path": prepared.prompt_key, "sha256": _sha256(prepared.prompt_bytes)},
        "refusal_codes": refusal_codes,
        "before": before,
        "after": after,
    }
    receipt["receipt_sha256"] = _sha256(canonical_json_bytes(receipt))
    return SeamResult(exit_code, receipt)
