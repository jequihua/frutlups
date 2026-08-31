"""M004-S01: fresh corrective attempts and atomic governed publication.

The governed transaction contract v1 requires that a corrective proposal is not
dispatchable until a released governed operation *validates and records* its
sidecar entry, that the same operation may create the entry and render the prompt
**atomically**, and that retries never overwrite an accepted sidecar entry or a
historical prompt (``docs/template_framework/slice_prompt_contract.md`` section
6). This module is frutlups' implementation of that operation. It reuses the M001
typed model (:mod:`frutlups.slice_prompt`), the M002 health and evidence-admission
surface (:mod:`frutlups.prompt_health`), and the M003 role-purity and
rework-context surfaces (:mod:`frutlups.rework_context`); none is reimplemented.

The transaction performs publication authority itself (finding M004-R2-F1). It
loads the exact admitted ``frutlups.layout.yaml`` direct child of the supplied
repository root and accepts only
lexical repository-relative target references. Caller-created layout data cannot
authorize a target, and absolute references are never normalized into relative
ones.

- the **sidecar** — an existing regular file that is a direct child of the layout
  roadmaps directory and ends with the layout sidecar suffix, admitted lexically
  (no filesystem access) then canonically (strict resolution under the repository
  root, no symlink/junction alias); and
- the **coding prompt** — an absent direct child of the layout coding-prompt
  directory matching the layout filename pattern, whose parent directory is a
  canonically admitted, contained, non-alias directory.

A target that is detached, escaping, aliased, in the wrong directory, or of the
wrong shape refuses before target-content reads or any mutation seam.

Attempt freshness uses a total classifier over the bounded history domain
(finding M004-R2-F2). An absent current attempt means initial ``001``; a present
invalid attempt is unresolved. A filename-valid direct child with no raw exact
``## Typed Entry`` line is legacy, while every child with that marker is a valid
same-slice carrier, a valid other-slice carrier, or malformed. Malformed evidence
fails closed.

The current sidecar is indivisible authority: no entry participates unless its
complete parse is diagnostic-free, and post-splice confirmation repeats that
whole-sidecar proof. Recovery observations classify identity without following
aliases and cover the final sidecar, final prompt, and both deterministic sidecar
temporary siblings. Staging creates each temporary exclusively and writes through
the new descriptor. ``published`` requires the proposed final digests and absent
temporaries; ``refused`` requires a readable after-map equal to the readable
before-map and absent temporaries. Every other observation is
``recovery_required``.
"""

from __future__ import annotations

import hashlib
import os
import posixpath
import re
import stat
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias

from frutlups._yaml import YamlBoundaryError, load_yaml_bytes
from frutlups.exceptions import FrutlupsError
from frutlups.prompt_health import (
    _admitted_local_file,
    _lexical_local_reference,
    _typed_entry_blocks,
    evaluate_health,
)
from frutlups.rework_context import check_seat_write, resolve_rework_context
from frutlups.slice_prompt import (
    ATTEMPT_RE,
    SLICE_ID_RE,
    SLICE_YAML_LIMITS,
    ContractVocab,
    Diagnostic,
    SliceEntry,
    SlicePromptError,
    _emit_yaml_sequence,
    _is_junction,
    parse_sidecar,
    render_prompt,
)

# The three durable outcomes the transaction reports. ``published`` and
# ``refused`` are clean; ``recovery_required`` names durable partial state that a
# best-effort receipt must never hide.
PUBLISHED = "published"
REFUSED = "refused"
RECOVERY_REQUIRED = "recovery_required"
PUBLICATION_OUTCOMES = (PUBLISHED, REFUSED, RECOVERY_REQUIRED)

# Refusal reason codes this transaction owns, in the order they are checked. A
# refusal folds any reused-surface diagnostics (health, role purity, rework
# context) into one code and names the underlying codes in its message, so this
# tuple fully enumerates the publication refusal surface. Direct misuse of the
# standalone allocator raises :class:`PublicationError` instead.
PUBLICATION_REFUSAL_CODES = (
    "layout_unresolved",
    "not_corrective",
    "entry_not_ready",
    "entry_unhealthy",
    "role_impure",
    "rework_context_unresolved",
    "target_unbound",
    "slice_not_in_sidecar",
    "history_unresolved",
    "attempt_not_fresh",
    "prompt_collision",
    "sidecar_update_invalid",
    "publish_write_failed",
    "recovery_required",
)

# A block-sequence item that opens a slice entry: ``- slice: <id>`` at column 0,
# the house sidecar style (the released project and fixture sidecars both use it).
_ENTRY_START_RE = re.compile(r"^-\s+slice:\s*(.*\S)\s*$")
# The layout ``prompts.filename_pattern`` (`{sequence:03d}_{slug}.md`): a
# three-digit sequence, an underscore, a non-empty slug, and the .md extension.
_PROMPT_FILENAME_RE = re.compile(r"^[0-9]{3}_[^/\r\n]+\.md$")
_SUPPORTED_FILENAME_PATTERN = "{sequence:03d}_{slug}.md"
_TYPED_ENTRY_MARKER = b"## Typed Entry"

_CARRIER_MAX_BYTES = 1_048_576
_PUBLISH_TMP_SUFFIX = ".publish-tmp"
_ROLLBACK_TMP_SUFFIX = ".rollback-tmp"


class PublicationError(FrutlupsError):
    """Raised on programmer misuse of the allocator (invalid or exhausted attempt)."""


@dataclass(frozen=True)
class _LoadedLayout:
    """Validated publication data loaded internally from the repository layout."""

    roadmaps_directory: str
    sidecar_suffix: str
    coding_prompt_directory: str
    filename_pattern: str


@dataclass(frozen=True)
class AbsentObservation:
    """An owned path did not exist when observed."""

    @property
    def state(self) -> str:
        return "absent"


@dataclass(frozen=True)
class PresentObservation:
    """An owned path was readable and had the recorded SHA-256 digest."""

    sha256: str

    @property
    def state(self) -> str:
        return "present"


@dataclass(frozen=True)
class UnreadableObservation:
    """An owned path could not be classified as absent or read completely."""

    @property
    def state(self) -> str:
        return "unreadable"


@dataclass(frozen=True)
class UnsafeObservation:
    """An owned path has an identity that must not be followed or trusted."""

    identity: str

    @property
    def state(self) -> str:
        return "unsafe"


ArtifactObservation: TypeAlias = (
    AbsentObservation | PresentObservation | UnreadableObservation | UnsafeObservation
)

_ABSENT = AbsentObservation()
_UNREADABLE = UnreadableObservation()


class _UnsafeTemporaryError(OSError):
    """A temporary collision, identity defect, or cleanup failure is unsafe."""


@dataclass(frozen=True)
class PublicationResult:
    """The outcome of :func:`publish_corrective_attempt`.

    ``outcome`` is one of :data:`PUBLICATION_OUTCOMES`. ``refusals`` is non-empty
    for a pre-commit refusal and for the two commit failure receipts
    (``publish_write_failed`` under ``refused``, ``recovery_required`` under that
    outcome). ``before`` and ``after`` are complete typed maps over the frozen
    owned-path set when the transaction reached its mutation boundary; both are
    ``None`` for earlier precondition refusals.
    """

    slice_id: str
    attempt: str | None
    outcome: str
    sidecar_path: str | None
    prompt_path: str | None
    refusals: tuple[Diagnostic, ...]
    before: Mapping[str, ArtifactObservation] | None = None
    after: Mapping[str, ArtifactObservation] | None = None

    @property
    def published(self) -> bool:
        return self.outcome == PUBLISHED

    def refusal_codes(self) -> tuple[str, ...]:
        return tuple(d.code for d in self.refusals)


@dataclass(frozen=True)
class PreparedPublication:
    """A corrective attempt that passed every precondition and is ready to commit.

    Produced by :func:`prepare_corrective_attempt` after validation, target
    admission, history freshness, rendering, and splice confirmation, with no
    mutation performed. ``sidecar_key``/``prompt_key`` are the repository-relative
    POSIX norms; the targets are the canonically admitted paths. The M005-S02
    Drive seam uses this state for a zero-write dry-run receipt;
    :func:`commit_prepared_publication` is the only mutation seam.
    """

    slice_id: str
    attempt: str
    entry_data: Mapping[str, Any]
    sidecar_key: str
    sidecar_target: Path
    prompt_key: str
    prompt_target: Path
    original_sidecar_bytes: bytes
    sidecar_bytes: bytes
    prompt_bytes: bytes


# --- attempt allocation ------------------------------------------------------


def allocate_attempt(existing: Iterable[str]) -> str:
    """The smallest ``001``..``999`` attempt strictly greater than every existing one.

    ``existing`` is the complete set of attempt identities already used for a
    slice; each must be a valid attempt string. Empty history allocates ``001``.
    The result is fresh — never equal to any existing attempt — so a corrective
    retry names a new artifact instead of reusing accepted history. Raises
    :class:`PublicationError` on an invalid historical identity or when the space
    is exhausted at ``999``.
    """

    highest = 0
    for value in existing:
        if not (isinstance(value, str) and ATTEMPT_RE.match(value)):
            raise PublicationError(f"not a valid attempt identity: {value!r}")
        highest = max(highest, int(value))
    if highest >= 999:
        raise PublicationError("attempt identity space is exhausted at 999")
    return f"{highest + 1:03d}"


# --- the governed transaction ------------------------------------------------


def publish_corrective_attempt(
    *,
    entry: SliceEntry,
    repo_root: Path,
    sidecar_path: str | Path,
    prompt_path: str | Path,
) -> PublicationResult:
    """Validate a corrective entry and publish its sidecar update and prompt truthfully.

    The exact repository layout is admitted and loaded internally. Preconditions
    then reuse M001-M003 validation and admit the two lexical repository-relative
    targets before target content I/O. Freshness is decided by the total bounded
    history classifier. The commit freezes and observes the four-path owned set
    immediately before its first possible mutation and derives its receipt only
    from the typed before/after maps.
    """

    prepared = prepare_corrective_attempt(
        entry=entry, repo_root=repo_root, sidecar_path=sidecar_path, prompt_path=prompt_path
    )
    if isinstance(prepared, PublicationResult):
        return prepared
    return commit_prepared_publication(prepared)


def prepare_corrective_attempt(
    *,
    entry: SliceEntry,
    repo_root: Path,
    sidecar_path: str | Path,
    prompt_path: str | Path,
) -> PreparedPublication | PublicationResult:
    """Run every precondition of the transaction without reaching a mutation seam.

    Returns the refusal :class:`PublicationResult` (``before``/``after`` are
    ``None``: no mutation boundary was reached) or the :class:`PreparedPublication`
    holding both proposed artifacts. Nothing under the repository root is written.
    """

    slice_id = entry.slice_id
    refusals: list[Diagnostic] = []

    def refuse(code: str, message: str, location: str = slice_id) -> None:
        refusals.append(Diagnostic(code, location, message))

    # 1. Perform repository layout authority; it is never accepted from a caller.
    loaded = _load_layout_authority(repo_root)
    if isinstance(loaded, Diagnostic):
        return PublicationResult(slice_id, entry.attempt, REFUSED, None, None, (loaded,))
    root, layout, vocab = loaded

    # 2. Entry-level validation — pure, no target filesystem access.
    if not entry.corrective:
        refuse("not_corrective", "publication is governed only for a corrective entry")
    if entry.status != "ready":
        refuse(
            "entry_not_ready",
            f"a corrective entry is published only when ready; status is {entry.status!r}",
        )
    health = evaluate_health(entry, vocab)
    if not health.ok:
        refuse(
            "entry_unhealthy",
            "entry has health defects: " + ", ".join(sorted(set(health.defect_codes()))),
        )
    role_refusals = _role_purity_refusals(entry, vocab)
    if role_refusals:
        refuse(
            "role_impure",
            "manifest row is not role-pure: " + ", ".join(sorted({d.code for d in role_refusals})),
        )

    # 3. Target grammar and canonical admission (F1) — no target content read.
    sidecar_norm, sidecar_target, sidecar_refusal = _admit_sidecar_target(
        root, layout, sidecar_path
    )
    if sidecar_refusal is not None:
        refusals.append(sidecar_refusal)
    prompt_norm, prompt_target, prompt_refusal = _admit_prompt_target(root, layout, prompt_path)
    if prompt_refusal is not None:
        refusals.append(prompt_refusal)

    # Stop before target content I/O when the entry or either target is untrusted.
    if refusals:
        return PublicationResult(slice_id, entry.attempt, REFUSED, None, None, tuple(refusals))

    assert sidecar_norm is not None and sidecar_target is not None
    assert prompt_norm is not None and prompt_target is not None

    # 4. Rework-context binding reads declared evidence, never a publication target.
    rework = resolve_rework_context(entry, root)
    if not rework.ok:
        refuse(
            "rework_context_unresolved",
            "rework context does not resolve: " + ", ".join(sorted(set(rework.diagnostic_codes()))),
        )
        return PublicationResult(slice_id, entry.attempt, REFUSED, None, None, tuple(refusals))

    # 5. Total bounded history, freshness, and collision (F2).
    try:
        original_bytes = _read_bytes(sidecar_target)
    except OSError:
        refuse("history_unresolved", f"sidecar at {sidecar_norm} became unreadable")
        return PublicationResult(slice_id, entry.attempt, REFUSED, None, None, tuple(refusals))
    parsed = parse_sidecar(original_bytes, vocab, sidecar_path=sidecar_target)
    if parsed.diagnostics:
        refuse(
            "history_unresolved",
            "the complete current sidecar is invalid: " + ", ".join(parsed.diagnostic_codes()),
        )
        return PublicationResult(slice_id, entry.attempt, REFUSED, None, None, tuple(refusals))
    matches = tuple(candidate for candidate in parsed.entries if candidate.slice_id == slice_id)
    if not matches:
        refuse(
            "slice_not_in_sidecar",
            f"slice {slice_id} is not an entry of the sidecar at {sidecar_norm}",
        )
        return PublicationResult(slice_id, entry.attempt, REFUSED, None, None, tuple(refusals))
    if len(matches) != 1:
        refuse(
            "history_unresolved", f"slice {slice_id} does not have exactly one typed sidecar entry"
        )
        return PublicationResult(slice_id, entry.attempt, REFUSED, None, None, tuple(refusals))
    current = matches[0]

    history, history_refusal = _slice_history(root, layout, current, slice_id)
    if history_refusal is not None:
        return PublicationResult(slice_id, entry.attempt, REFUSED, None, None, (history_refusal,))
    assert history is not None
    try:
        allocated = allocate_attempt(history)
    except PublicationError as exc:
        refuse(
            "attempt_not_fresh",
            f"no fresh attempt over the bounded history {sorted(history)}: {exc}",
        )
        return PublicationResult(slice_id, entry.attempt, REFUSED, None, None, tuple(refusals))
    if entry.attempt != allocated:
        refuse(
            "attempt_not_fresh",
            f"attempt {entry.attempt!r} is not the next value {allocated!r} over history"
            f" {sorted(history)}",
        )
    if _target_exists(prompt_target):
        refuse(
            "prompt_collision",
            f"a prompt already exists at {prompt_norm}; refusing to overwrite accepted history",
        )
    if refusals:
        return PublicationResult(slice_id, entry.attempt, REFUSED, None, None, tuple(refusals))

    # 6. Build both artifacts; confirm the spliced sidecar records the entry verbatim.
    prompt_text = render_prompt(entry, vocab)
    new_text = _splice_entry(original_bytes.decode("utf-8"), slice_id, dict(entry.data))
    if new_text is None or not _reparse_confirms(
        new_text, vocab, sidecar_target, slice_id, dict(entry.data)
    ):
        refuse(
            "sidecar_update_invalid",
            "the spliced sidecar does not re-parse with the entry recorded verbatim",
        )
        return PublicationResult(slice_id, entry.attempt, REFUSED, None, None, tuple(refusals))

    return PreparedPublication(
        slice_id,
        allocated,
        dict(entry.data),
        sidecar_norm,
        sidecar_target,
        prompt_norm,
        prompt_target,
        original_bytes,
        new_text.encode("utf-8"),
        prompt_text.encode("utf-8"),
    )


def commit_prepared_publication(prepared: PreparedPublication) -> PublicationResult:
    """Commit a prepared attempt; the outcome follows only from typed state maps (F3)."""

    slice_id = prepared.slice_id
    receipt = _commit(
        prepared.sidecar_target,
        prepared.sidecar_bytes,
        prepared.original_sidecar_bytes,
        prepared.prompt_target,
        prepared.prompt_bytes,
        sidecar_key=prepared.sidecar_key,
        prompt_key=prepared.prompt_key,
    )
    if receipt.outcome == PUBLISHED:
        return PublicationResult(
            slice_id,
            prepared.attempt,
            PUBLISHED,
            str(prepared.sidecar_target),
            str(prepared.prompt_target),
            (),
            receipt.before,
            receipt.after,
        )
    if receipt.outcome == REFUSED:
        code = (
            "prompt_collision" if receipt.reason == "prompt_collision" else "publish_write_failed"
        )
        message = (
            f"a prompt appeared at {prepared.prompt_key} before mutation; no owned path changed"
            if code == "prompt_collision"
            else "publication did not complete; the readable owned state was restored"
        )
        return PublicationResult(
            slice_id,
            prepared.attempt,
            REFUSED,
            None,
            None,
            (Diagnostic(code, slice_id, message),),
            receipt.before,
            receipt.after,
        )
    return PublicationResult(
        slice_id,
        prepared.attempt,
        RECOVERY_REQUIRED,
        None,
        None,
        (
            Diagnostic(
                "recovery_required",
                slice_id,
                f"owned state is not a clean publication or refusal: {dict(receipt.after)}",
            ),
        ),
        receipt.before,
        receipt.after,
    )


def observe_owned_state(prepared: PreparedPublication) -> dict[str, ArtifactObservation]:
    """Observe the frozen four-path owned set of ``prepared`` without mutating it.

    The map has the same keys and typed observations a commit receipt carries, so a
    dry-run can report complete before/after state through the identical observer.
    """

    return _observe_owned(
        _owned_paths(
            prepared.sidecar_target,
            prepared.prompt_target,
            sidecar_key=prepared.sidecar_key,
            prompt_key=prepared.prompt_key,
        )
    )


# --- internally performed layout authority ----------------------------------


def _load_layout_authority(
    repo_root: Path,
) -> tuple[Path, _LoadedLayout, ContractVocab] | Diagnostic:
    """Admit and load only the repository root's ``frutlups.layout.yaml`` child.

    The layout file must be an alias-free regular file directly beneath an
    existing repository directory. YAML is parsed once through the house boundary;
    the target directories, sidecar suffix, filename pattern, and contract
    vocabulary all come from that same validated mapping.
    """

    try:
        root = Path(repo_root).resolve(strict=True)
    except (OSError, RuntimeError, TypeError):
        return _layout_refusal("repository root is absent or cannot be resolved")
    if not root.is_dir():
        return _layout_refusal("repository root is not a directory")

    candidate = root / "frutlups.layout.yaml"
    try:
        if candidate.is_symlink() or _is_junction(candidate):
            return _layout_refusal("frutlups.layout.yaml is an alias")
    except OSError:
        return _layout_refusal("frutlups.layout.yaml alias state is unreadable")
    admitted = _admitted_local_file(root, "frutlups.layout.yaml")
    if admitted is None or admitted.parent != root:
        return _layout_refusal("frutlups.layout.yaml is not an admitted direct-child regular file")

    try:
        raw = _read_bounded(admitted, SLICE_YAML_LIMITS.max_bytes)
        if len(raw) > SLICE_YAML_LIMITS.max_bytes:
            return _layout_refusal("frutlups.layout.yaml exceeds the house YAML byte bound")
        doc = load_yaml_bytes(raw, limits=SLICE_YAML_LIMITS).value
    except (OSError, YamlBoundaryError) as exc:
        return _layout_refusal(f"frutlups.layout.yaml is unreadable or invalid: {exc}")
    if not isinstance(doc, dict):
        return _layout_refusal("frutlups.layout.yaml is not a mapping")

    roadmaps = doc.get("roadmaps")
    prompts = doc.get("prompts")
    contract = doc.get("slice_prompt_contract")
    if (
        not isinstance(roadmaps, dict)
        or not isinstance(prompts, dict)
        or not isinstance(contract, dict)
    ):
        return _layout_refusal(
            "layout lacks the roadmaps, prompts, or slice_prompt_contract mapping"
        )
    roadmaps_directory = _layout_directory(roadmaps.get("directory"))
    coding_prompt_directory = _layout_directory(prompts.get("coding_prompt_dir"))
    sidecar_suffix = contract.get("sidecar_suffix")
    filename_pattern = prompts.get("filename_pattern")
    if roadmaps_directory is None or coding_prompt_directory is None:
        return _layout_refusal(
            "layout publication directories are not canonical repository-relative directories"
        )
    if (
        not isinstance(sidecar_suffix, str)
        or re.fullmatch(r"\.[A-Za-z0-9][A-Za-z0-9._-]*", sidecar_suffix) is None
    ):
        return _layout_refusal("layout sidecar suffix is unsupported")
    if filename_pattern != _SUPPORTED_FILENAME_PATTERN:
        return _layout_refusal(f"layout filename pattern is unsupported: {filename_pattern!r}")
    try:
        vocab = ContractVocab.from_block(contract)
    except (KeyError, TypeError, ValueError, SlicePromptError) as exc:
        return _layout_refusal(f"layout contract vocabulary is invalid: {exc}")
    if vocab.version != 1:
        return _layout_refusal(f"layout contract version is unsupported: {vocab.version!r}")
    return (
        root,
        _LoadedLayout(
            roadmaps_directory,
            sidecar_suffix,
            coding_prompt_directory,
            filename_pattern,
        ),
        vocab,
    )


def _layout_refusal(message: str) -> Diagnostic:
    return Diagnostic("layout_unresolved", "frutlups.layout.yaml", message)


def _layout_directory(value: Any) -> str | None:
    """Validate one layout directory without normalizing unsafe spellings."""

    if not isinstance(value, str) or not value or value != value.strip() or chr(92) in value:
        return None
    probe = _lexical_local_reference(value + "/_publication_path_probe")
    if probe is None or posixpath.dirname(probe) != value:
        return None
    return value


# --- precondition helpers ----------------------------------------------------


def _role_purity_refusals(entry: SliceEntry, vocab: ContractVocab) -> list[Diagnostic]:
    """Every M003 role-purity refusal across the entry's declared manifest rows."""

    refusals: list[Diagnostic] = []
    for write in entry.writes:
        resolved = write.resolved(vocab.attempt_token, entry.attempt)
        refusals.extend(
            check_seat_write(
                entry,
                vocab,
                write.role_owner,
                resolved.path,
                write.artifact_type,
                attempt=entry.attempt,
            )
        )
    return refusals


# --- target admission and layout binding (F1) --------------------------------


def _admit_sidecar_target(
    repo_root: Path, layout: _LoadedLayout, sidecar_path: str | Path
) -> tuple[str | None, Path | None, Diagnostic | None]:
    """Admit the sidecar reference and return its norm and canonical path.

    Lexical (no filesystem): a repository-relative file that is a direct child of
    the layout roadmaps directory and ends with the layout sidecar suffix.
    Canonical: not a symlink/junction alias, and an existing regular file strictly
    resolved under the repository root (M002 admission seam).
    """

    norm = _repo_relative_norm(sidecar_path)
    if norm is None:
        return None, None, _unbound("sidecar path is not a lexical repository-relative file path")
    if posixpath.dirname(norm) != layout.roadmaps_directory:
        return (
            None,
            None,
            _unbound(
                f"sidecar is not a direct child of the layout roadmaps directory"
                f" {layout.roadmaps_directory!r}"
            ),
        )
    basename = posixpath.basename(norm)
    if not basename.endswith(layout.sidecar_suffix) or basename == layout.sidecar_suffix:
        return (
            None,
            None,
            _unbound(
                f"sidecar does not end with the layout sidecar suffix {layout.sidecar_suffix!r}"
            ),
        )
    if not _alias_free_path(repo_root, norm):
        return None, None, _unbound("sidecar target or an intermediate component is an alias")
    admitted = _admitted_local_file(repo_root, norm)
    if admitted is None:
        return (
            None,
            None,
            _unbound("sidecar is absent or not a regular file resolved under the repository root"),
        )
    return norm, admitted, None


def _admit_prompt_target(
    repo_root: Path, layout: _LoadedLayout, prompt_path: str | Path
) -> tuple[str | None, Path | None, Diagnostic | None]:
    """Admit the coding-prompt reference and return its norm and canonical path.

    Lexical (no filesystem): a repository-relative file that is a direct child of
    the layout coding-prompt directory and matches the layout filename pattern.
    Canonical: the coding-prompt directory is a contained, non-alias directory and
    the target itself is not an alias. Existence of the target is a history
    collision, decided later, not an admission failure.
    """

    norm = _repo_relative_norm(prompt_path)
    if norm is None:
        return None, None, _unbound("prompt path is not a lexical repository-relative file path")
    if posixpath.dirname(norm) != layout.coding_prompt_directory:
        return (
            None,
            None,
            _unbound(
                f"prompt is not a direct child of the layout coding-prompt directory"
                f" {layout.coding_prompt_directory!r}"
            ),
        )
    if layout.filename_pattern != _SUPPORTED_FILENAME_PATTERN or not _PROMPT_FILENAME_RE.match(
        posixpath.basename(norm)
    ):
        return (
            None,
            None,
            _unbound(
                f"prompt filename does not match the loaded layout pattern"
                f" {layout.filename_pattern!r}"
            ),
        )
    if not _alias_free_path(repo_root, norm):
        return None, None, _unbound("prompt target or an intermediate component is an alias")
    directory = _admitted_local_dir(repo_root, layout.coding_prompt_directory)
    if directory is None:
        return (
            None,
            None,
            _unbound(
                "coding-prompt directory is absent or not contained under the repository root"
            ),
        )
    return norm, directory / posixpath.basename(norm), None


def _unbound(message: str) -> Diagnostic:
    return Diagnostic("target_unbound", "publication_target", message)


def _repo_relative_norm(target: str | Path) -> str | None:
    """The lexical repository-relative POSIX norm of ``target``, or ``None``.

    Absolute input is rejected before normalization, including an absolute target
    that happens to lie under the repository root. Pure lexical normalization is
    then delegated to the reused M002 reference grammar.
    """

    try:
        lexical = Path(target)
    except TypeError:
        return None
    if lexical.is_absolute():
        return None
    return _lexical_local_reference(str(target))


def _alias_free_path(repo_root: Path, norm: str) -> bool:
    """Whether every lexical component below ``repo_root`` is non-alias."""

    candidate = repo_root
    try:
        for part in norm.split("/"):
            candidate = candidate / part
            if candidate.is_symlink() or _is_junction(candidate):
                return False
    except OSError:
        return False
    return True


def _admitted_local_dir(repo_root: Path, norm: str) -> Path | None:
    """Strict canonical admission of a directory (companion to M002's file seam)."""

    try:
        root = repo_root.resolve(strict=True)
        resolved = (repo_root / norm).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if resolved == root or not resolved.is_relative_to(root):
        return None
    return resolved if resolved.is_dir() else None


# --- complete bounded history (F2) -------------------------------------------


def _slice_history(
    repo_root: Path, layout: _LoadedLayout, current: SliceEntry, slice_id: str
) -> tuple[set[str] | None, Diagnostic | None]:
    """The complete bounded attempt history for ``slice_id``, or a fail-closed refusal.

    The current sidecar attempt has three states: absent means initial ``001``;
    present-valid is included; present-invalid is unresolved. Every admitted,
    filename-valid direct child is classified by :func:`_carrier_attempt` into one
    of four total states. Only legacy and valid-other-slice evidence is excluded.
    """

    if "attempt" not in current.data:
        attempts: set[str] = {"001"}
    elif isinstance(current.data["attempt"], str) and ATTEMPT_RE.match(current.data["attempt"]):
        attempts = {current.data["attempt"]}
    else:
        return None, Diagnostic(
            "history_unresolved",
            slice_id,
            f"current sidecar attempt is present but invalid: {current.data.get('attempt')!r}",
        )
    directory = repo_root / layout.coding_prompt_directory
    try:
        children = sorted(directory.iterdir())
    except OSError:
        return None, Diagnostic(
            "history_unresolved", slice_id, "coding-prompt directory is not readable"
        )
    for child in children:
        if not _PROMPT_FILENAME_RE.match(child.name):
            continue
        try:
            if child.is_symlink() or _is_junction(child):
                continue
            is_file = child.is_file()
        except OSError:
            return None, Diagnostic(
                "history_unresolved", slice_id, f"cannot admit history child {child.name}"
            )
        if not is_file:
            continue
        kind, value = _carrier_attempt(child, slice_id)
        if kind == "malformed":
            return None, Diagnostic(
                "history_unresolved",
                slice_id,
                f"malformed contract-v1 carrier {child.name}: {value}",
            )
        if kind == "valid_same_slice":
            assert value is not None
            attempts.add(value)
    return attempts, None


def _carrier_attempt(path: Path, slice_id: str) -> tuple[str, str | None]:
    """Totally classify one admitted filename-valid history child.

    The four states are ``legacy_non_contract``, ``valid_same_slice``,
    ``valid_other_slice``, and ``malformed``. Only a complete bounded read with no
    exact line-start Typed Entry marker is legacy. Once the raw marker exists,
    every decode, fence, YAML, mapping, identity, or duplicate-marker defect is
    malformed and therefore fail-closed.
    """

    try:
        raw = _read_bounded(path, _CARRIER_MAX_BYTES)
    except OSError:
        return "malformed", "unreadable"
    if len(raw) > _CARRIER_MAX_BYTES:
        return "malformed", "oversized"
    marker_count = raw.splitlines().count(_TYPED_ENTRY_MARKER)
    if marker_count == 0:
        return "legacy_non_contract", None
    if marker_count != 1:
        return "malformed", "carrier marker disagreement"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return "malformed", "invalid UTF-8 after Typed Entry marker"
    fence_problem = _typed_entry_fence_problem(text)
    if fence_problem is not None:
        return "malformed", fence_problem
    blocks = _typed_entry_blocks(text)
    if blocks is None or len(blocks) == 0:
        return "malformed", "Typed Entry marker has no carrier fence"
    if len(blocks) != 1:
        return "malformed", "Typed Entry marker has duplicate carrier fences"
    try:
        loaded = load_yaml_bytes(blocks[0].encode("utf-8"), limits=SLICE_YAML_LIMITS).value
    except YamlBoundaryError as exc:
        return "malformed", f"unparseable Typed Entry block: {exc.message}"
    if not isinstance(loaded, dict):
        return "malformed", "Typed Entry is not a mapping"
    carrier_slice = loaded.get("slice")
    if not isinstance(carrier_slice, str) or not SLICE_ID_RE.match(carrier_slice):
        return "malformed", f"missing or invalid slice identity {carrier_slice!r}"
    attempt = loaded.get("attempt")
    if not isinstance(attempt, str) or not ATTEMPT_RE.match(attempt):
        return "malformed", f"missing or invalid attempt identity {attempt!r}"
    if carrier_slice == slice_id:
        return "valid_same_slice", attempt
    return "valid_other_slice", attempt


def _typed_entry_fence_problem(text: str) -> str | None:
    """Validate the one finite fenced carrier shape consumed by the M002 seam."""

    lines = text.splitlines()
    try:
        heading = lines.index("## Typed Entry")
    except ValueError:
        return "Typed Entry marker has no carrier section"
    body: list[str] = []
    for line in lines[heading + 1 :]:
        if line.startswith("## "):
            break
        body.append(line)
    openings = [index for index, line in enumerate(body) if line.strip() == "```yaml"]
    closings = [index for index, line in enumerate(body) if line.strip() == "```"]
    if not openings or not closings or closings[0] < openings[0]:
        return "Typed Entry marker has an incomplete carrier fence"
    if len(openings) != 1 or len(closings) != 1:
        return "Typed Entry marker has duplicate carrier fences"
    return None


# --- sidecar splice ----------------------------------------------------------


def _splice_entry(text: str, slice_id: str, entry_data: Mapping[str, Any]) -> str | None:
    """Return ``text`` with ``slice_id``'s entry replaced by ``entry_data`` verbatim.

    Only the target entry's lines change; every other byte — including other
    entries and their formatting — is preserved, so no accepted entry is rewritten.
    Returns ``None`` when the target entry cannot be located.
    """

    lines = text.split("\n")
    start = None
    for index, line in enumerate(lines):
        match = _ENTRY_START_RE.match(line)
        if match is not None and _unquote(match.group(1)) == slice_id:
            start = index
            break
    if start is None:
        return None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line and not line[0].isspace():  # the next block-sequence item or top-level key
            end = index
            break
    replacement = _emit_yaml_sequence([dict(entry_data)], 0)
    return "\n".join(lines[:start] + replacement + lines[end:])


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _reparse_confirms(
    text: str,
    vocab: ContractVocab,
    sidecar_path: Path,
    slice_id: str,
    entry_data: Mapping[str, Any],
) -> bool:
    """Confirm one exact entry inside a diagnostic-free complete sidecar parse."""

    parsed = parse_sidecar(text.encode("utf-8"), vocab, sidecar_path=sidecar_path)
    if parsed.diagnostics:
        return False
    matches = tuple(candidate for candidate in parsed.entries if candidate.slice_id == slice_id)
    return len(matches) == 1 and dict(matches[0].data) == dict(entry_data)


# --- typed complete recovery state (F3) --------------------------------------


@dataclass(frozen=True)
class _CommitReceipt:
    outcome: str
    reason: str
    before: Mapping[str, ArtifactObservation]
    after: Mapping[str, ArtifactObservation]


def _commit(
    sidecar: Path,
    new_sidecar_bytes: bytes,
    original_sidecar_bytes: bytes,
    prompt: Path,
    prompt_bytes: bytes,
    *,
    sidecar_key: str,
    prompt_key: str,
) -> _CommitReceipt:
    """Publish using a frozen four-path owned set and typed observations.

    The before-map is captured immediately before the first mutation. A
    pre-existing temporary or unreadable observation prevents mutation. Every
    return observes the same frozen paths again and uses :func:`_state_outcome`;
    recovery helpers never decide that their own work succeeded.
    """

    owned_paths = _owned_paths(sidecar, prompt, sidecar_key=sidecar_key, prompt_key=prompt_key)
    temporary_keys = (sidecar_key + _PUBLISH_TMP_SUFFIX, sidecar_key + _ROLLBACK_TMP_SUFFIX)
    before = _observe_owned(owned_paths)

    def finish(reason: str, *, force_recovery: bool = False) -> _CommitReceipt:
        after = _observe_owned(owned_paths)
        outcome = (
            RECOVERY_REQUIRED
            if force_recovery
            else _state_outcome(
                before,
                after,
                sidecar_key=sidecar_key,
                prompt_key=prompt_key,
                temporary_keys=temporary_keys,
                proposed_sidecar=new_sidecar_bytes,
                proposed_prompt=prompt_bytes,
            )
        )
        return _CommitReceipt(outcome, reason, before, after)

    # The mutation boundary is usable only when all observations are readable,
    # both deterministic temporaries are absent, the sidecar still matches the
    # bytes used to derive the proposal, and the final prompt is still absent.
    if any(
        isinstance(value, (UnreadableObservation, UnsafeObservation)) for value in before.values()
    ):
        return finish("recovery_required")
    if any(before[key] != _ABSENT for key in temporary_keys):
        return finish("recovery_required")
    if before[sidecar_key] != PresentObservation(_sha256(original_sidecar_bytes)):
        return finish("publish_write_failed")
    if before[prompt_key] != _ABSENT:
        return finish("prompt_collision")

    try:
        _stage_and_replace(sidecar, new_sidecar_bytes)
    except _UnsafeTemporaryError:
        return finish("recovery_required", force_recovery=True)
    except OSError:
        return finish("publish_write_failed")

    try:
        _create_exclusive(prompt, prompt_bytes)
    except FileExistsError:
        # A true post-before-state collision changes the prompt observation and
        # therefore cannot classify as a clean refusal.
        _try(_rollback, sidecar, original_sidecar_bytes)
        return finish("prompt_collision")
    except OSError:
        _try(_remove, prompt)  # a partial prompt may remain if this fails
        _try(_rollback, sidecar, original_sidecar_bytes)
        return finish("publish_write_failed")
    return finish("published")


def _try(func, *args) -> None:
    """Attempt a recovery step; its failure surfaces through :func:`_commit`'s observation."""

    try:
        func(*args)
    except OSError:
        pass


def _observe_path(path: Path) -> ArtifactObservation:
    """Observe identity without following it, then hash one stable regular file.

    Only ``FileNotFoundError`` from the initial ``lstat`` proves absence. Aliases,
    directories, and other non-regular nodes are unsafe without a content open.
    A regular path is opened and read through its descriptor, with descriptor and
    final path identity required to match the initial non-following observation.
    """

    try:
        initial = _lstat(path)
    except FileNotFoundError:
        return _ABSENT
    except OSError:
        return _UNREADABLE
    problem = _identity_problem(initial)
    if problem is not None:
        return UnsafeObservation(problem)

    try:
        descriptor = _open_for_observation(path)
    except FileNotFoundError:
        return UnsafeObservation("identity_changed")
    except OSError:
        return _UNREADABLE
    try:
        opened = _fstat(descriptor)
        if _identity_problem(opened) is not None or not _same_identity(initial, opened):
            observation: ArtifactObservation = UnsafeObservation("identity_changed")
        else:
            observation = PresentObservation(_sha256(_read_descriptor(descriptor)))
    except FileNotFoundError:
        observation = UnsafeObservation("identity_changed")
    except OSError:
        observation = _UNREADABLE
    try:
        _close_descriptor(descriptor)
    except OSError:
        return _UNREADABLE
    if not isinstance(observation, PresentObservation):
        return observation

    try:
        final = _lstat(path)
    except FileNotFoundError:
        return UnsafeObservation("identity_changed")
    except OSError:
        return _UNREADABLE
    if _identity_problem(final) is not None or not _same_identity(initial, final):
        return UnsafeObservation("identity_changed")
    return observation


def _owned_paths(
    sidecar: Path, prompt: Path, *, sidecar_key: str, prompt_key: str
) -> dict[str, Path]:
    """The frozen four-path owned set: both finals plus the two sidecar temporaries."""

    return {
        sidecar_key: sidecar,
        prompt_key: prompt,
        sidecar_key + _PUBLISH_TMP_SUFFIX: sidecar.with_name(sidecar.name + _PUBLISH_TMP_SUFFIX),
        sidecar_key + _ROLLBACK_TMP_SUFFIX: sidecar.with_name(sidecar.name + _ROLLBACK_TMP_SUFFIX),
    }


def _observe_owned(paths: Mapping[str, Path]) -> dict[str, ArtifactObservation]:
    return {key: _observe_path(path) for key, path in paths.items()}


def _identity_problem(identity: os.stat_result) -> str | None:
    """Return the unsafe identity class, or ``None`` for a regular file."""

    attributes = getattr(identity, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if stat.S_ISLNK(identity.st_mode) or attributes & reparse_flag:
        return "alias"
    if stat.S_ISDIR(identity.st_mode):
        return "directory"
    if not stat.S_ISREG(identity.st_mode):
        return "non_regular"
    return None


def _state_outcome(
    before: Mapping[str, ArtifactObservation],
    after: Mapping[str, ArtifactObservation],
    *,
    sidecar_key: str,
    prompt_key: str,
    temporary_keys: tuple[str, ...],
    proposed_sidecar: bytes,
    proposed_prompt: bytes,
) -> str:
    """Classify the bounded state maps as published, refused, or recovery-required."""

    temporaries_absent = all(after.get(key) == _ABSENT for key in temporary_keys)
    if (
        after.get(sidecar_key) == PresentObservation(_sha256(proposed_sidecar))
        and after.get(prompt_key) == PresentObservation(_sha256(proposed_prompt))
        and temporaries_absent
    ):
        return PUBLISHED
    readable = not any(
        isinstance(value, (UnreadableObservation, UnsafeObservation))
        for value in (*before.values(), *after.values())
    )
    if readable and before == after and temporaries_absent:
        return REFUSED
    return RECOVERY_REQUIRED


# --- operational seams (isolated so a rejected target reaches none of them) ---


def _read_bounded(path: Path, limit: int) -> bytes:
    """Read at most ``limit + 1`` bytes so oversize input is observable."""

    with path.open("rb") as handle:
        return handle.read(limit + 1)


def _lstat(path: Path) -> os.stat_result:
    return os.lstat(path)


def _open_for_observation(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    return os.open(path, flags)


def _open_exclusive(path: Path) -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    return os.open(path, flags, 0o600)


def _fstat(descriptor: int) -> os.stat_result:
    return os.fstat(descriptor)


def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return os.path.samestat(left, right)


def _read_descriptor(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 65_536)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _write_descriptor(descriptor: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("temporary descriptor write made no progress")
        remaining = remaining[written:]


def _close_descriptor(descriptor: int) -> None:
    os.close(descriptor)


def _read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def _target_exists(path: Path) -> bool:
    return path.exists()


def _stage_and_replace(
    target: Path, data: bytes, temporary_suffix: str = _PUBLISH_TMP_SUFFIX
) -> None:
    """Create one regular temporary exclusively, close it, then replace target."""

    tmp = target.with_name(target.name + temporary_suffix)
    descriptor: int | None = None
    created = False
    try:
        try:
            descriptor = _open_exclusive(tmp)
        except OSError as exc:
            raise _UnsafeTemporaryError("temporary exclusive creation failed") from exc
        created = True
        try:
            identity = _fstat(descriptor)
        except OSError as exc:
            raise _UnsafeTemporaryError("temporary descriptor identity is unreadable") from exc
        if _identity_problem(identity) is not None:
            raise _UnsafeTemporaryError("exclusive temporary descriptor is not regular")
        _write_descriptor(descriptor, data)
        try:
            _close_descriptor(descriptor)
        except OSError as exc:
            raise _UnsafeTemporaryError("temporary descriptor close failed") from exc
        descriptor = None
        os.replace(tmp, target)
    except OSError as exc:
        unsafe = isinstance(exc, _UnsafeTemporaryError) or not created
        if descriptor is not None:
            try:
                _close_descriptor(descriptor)
            except OSError:
                unsafe = True
        if created:
            try:
                _remove(tmp)
            except OSError:
                unsafe = True
        if unsafe:
            raise _UnsafeTemporaryError("temporary identity or cleanup is unsafe") from exc
        raise


def _create_exclusive(target: Path, data: bytes) -> None:
    with open(target, "xb") as handle:
        handle.write(data)


def _rollback(target: Path, data: bytes) -> None:
    _stage_and_replace(target, data, _ROLLBACK_TMP_SUFFIX)


def _remove(target: Path) -> None:
    target.unlink()


# --- misc --------------------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
