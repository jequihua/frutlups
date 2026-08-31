"""M002-S01: semantic + render health over the M001 typed model, and a
no-write guarded dispatch surface.

This module builds on M001's contract-v1 typed model (:mod:`frutlups.slice_prompt`)
and never reimplements its validation or renderer: semantic health is exactly the
parser's ``_validate_entry`` reason codes, and render health is computed over the
prompt the released renderer emits. The reference checker
(``scripts/slice_contract_check.py``) stays the authority both are tested against
and is never imported by this product module.

Two surfaces, one typed model:

- :func:`evaluate_health` reports a single entry's defects with stable contract
  reason codes (contract section 10). It combines the semantic diagnostics with
  the three render invariants a guarded writer must be able to trust before it
  emits a prompt a coder will read: the ``## Typed Entry`` carrier strict-loads
  equal to the attempt-resolved entry (losslessness is equality, section 8), every
  line-start ``status:`` line is a plain agreeing dispatch line (read line-based,
  section 5), and no sentinel survives into the rendered prompt.
- :func:`guarded_dispatch` is the dispatch surface. It refuses, **without any
  filesystem write**, four cases: a ``frozen`` entry (valid planning material,
  never current work — section 5), a ``ready`` entry whose ``dispatch_authority``
  record is absent or not admitted under the repository root, a ``ready`` entry
  with an unsatisfied opening gate, and any prompt write not backed by a ``ready``
  entry. It writes exactly the rendered prompt — one artifact at the caller's
  destination — only when the backing entry is dispatchable.

Only a semantically healthy, ready entry reaches the filesystem evidence seam: an
unhealthy entry short-circuits to ``entry_unhealthy`` before any authority or gate
stat/open/hash. Every local evidence reference is admitted in two phases before
any content read: pure-lexical rejection with no filesystem access
(:func:`_lexical_local_reference`), then strict canonical resolution of both the
repository root and the target (:func:`_admitted_local_file`). Only the admitted
resolved regular-file path — never the lexical one — reaches the review-read or
hash consumers, so an in-root alias whose target lies outside the resolved root is
refused before any content I/O. ``dispatch_authority`` grants dispatch only when
its referenced record is such an admitted file — a mere syntactic path is not
recorded authority.

Opening-gate satisfaction is evaluated locally and fails closed. A path-kind gate
(``accepted_review``, ``owner_note``, ``artifact_exists``, ``artifact_identity``)
is satisfied only when its referenced repository-relative evidence is an admitted
regular file; ``artifact_identity`` must also hash to the pinned digest; and an
``accepted_review`` gate must additionally be a contract-conforming review whose
closure record (contract section 9) carries an accepting verdict (``pass`` or
``override``) — file existence or a bare accepting footer never satisfies it. The
recognizer is finite and exact, reading the record section-local and line-based
exactly as the released checker does, and is frozen over every released
review-report fixture by the focused tests. A gate whose evidence is not a local
file (``pinned_external_release``, ``human_launch_word``, ``external_answer``)
cannot be confirmed by this local surface and is treated as unsatisfied;
confirming those remains the runner/human gate's job, and the dispatch surface
never grants them silently.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from frutlups._yaml import YamlBoundaryError, load_yaml_bytes
from frutlups.slice_prompt import (
    SLICE_YAML_LIMITS,
    ContractVocab,
    Diagnostic,
    SliceEntry,
    SlicePromptError,
    _normalized_relative,
    _record_path_ok,
    _validate_entry,
    render_prompt,
    resolve_entry,
)

# Render-health reason codes this module emits over a rendered prompt. Each is a
# contract content reason code (slice_prompt_contract.md section 10); the safety-
# critical render invariants a guarded writer must trust are checked here, while
# the structural section/manifest rules stay the reference checker's authority.
RENDER_HEALTH_CODES = (
    "typed_entry_missing",
    "typed_entry_ambiguous",
    "typed_entry_unparseable",
    "typed_entry_mismatch",
    "typed_entry_status_line",
    "rendered_status_disagreement",
    "rendered_sentinel_residue",
    "attempt_mismatch",
)

# Dispatch refusal reason codes, in the order the enumerated cases are checked.
# ``dispatch_authority_missing`` reuses the contract's semantic code; the others
# name the dispatch decisions this surface owns.
DISPATCH_REFUSAL_CODES = (
    "entry_frozen",
    "entry_not_ready",
    "dispatch_authority_missing",
    "opening_gate_unsatisfied",
    "entry_unhealthy",
)

# Gate kinds whose evidence is a local repository file this surface can confirm.
LOCAL_EVIDENCE_GATE_KINDS = frozenset(
    {"accepted_review", "owner_note", "artifact_exists", "artifact_identity"}
)

# Verdicts that accept a predecessor (contract section 9). An ``accepted_review``
# gate is satisfied only by a conforming review whose closure record carries one
# of these; ``needs_work`` and ``blocked`` do not accept.
ACCEPTING_VERDICTS = frozenset({"pass", "override"})
_VERDICT_FOOTER_RE = re.compile(r"^Verdict: (pass|needs_work|blocked|override) - next: \S.*$")
# A line-start ``## `` heading, counted wherever it stands (contract section 9:
# no fence parsing; a fenced example carrying a heading line is a heading line).
_HEADING_RE = re.compile(r"^## (.+?)\s*$")
# Bounded read ceiling for a referenced review report (matches the sidecar YAML
# byte ceiling); an oversized or unreadable reference fails closed.
_REVIEW_MAX_BYTES = 1_048_576


@dataclass(frozen=True)
class HealthReport:
    """One entry's health: semantic + render defects with stable reason codes."""

    entry_id: str
    defects: tuple[Diagnostic, ...]

    @property
    def ok(self) -> bool:
        return not self.defects

    def defect_codes(self) -> tuple[str, ...]:
        return tuple(d.code for d in self.defects)


@dataclass(frozen=True)
class DispatchDecision:
    """Whether an entry may be dispatched, and why not when it may not."""

    entry_id: str
    dispatchable: bool
    refusals: tuple[Diagnostic, ...]
    health: HealthReport

    def refusal_codes(self) -> tuple[str, ...]:
        return tuple(d.code for d in self.refusals)


@dataclass(frozen=True)
class DispatchResult:
    """The outcome of :func:`guarded_dispatch`: the decision and what was written."""

    decision: DispatchDecision
    written: bool
    path: str | None


# --- health evaluation ------------------------------------------------------


def evaluate_health(
    entry: SliceEntry, vocab: ContractVocab, *, attempt: str | None = None
) -> HealthReport:
    """Report the entry's semantic and render health with stable reason codes.

    Render health is evaluated only when the entry is semantically clean, because
    the renderer refuses an invalid entry (M001); an invalid entry's defects are
    already the semantic reason codes.
    """

    defects = list(_validate_entry(dict(entry.data), entry.slice_id, vocab))
    if not defects:
        defects.extend(_render_health(entry, vocab, attempt))
    return HealthReport(entry.slice_id, tuple(defects))


def _render_health(
    entry: SliceEntry, vocab: ContractVocab, attempt: str | None
) -> list[Diagnostic]:
    try:
        rendered = render_prompt(entry, vocab, attempt=attempt)
    except SlicePromptError as exc:
        # A semantically clean entry the renderer still refuses: the known cause is
        # an attempt that contradicts the entry's own attempt identity (section 4).
        return [Diagnostic("attempt_mismatch", entry.slice_id, str(exc))]
    resolved = resolve_entry(dict(entry.data), vocab.attempt_token, entry.attempt)
    return evaluate_render_health(rendered, resolved, vocab, location=entry.slice_id)


def evaluate_render_health(
    rendered: str,
    resolved_entry: Mapping[str, Any],
    vocab: ContractVocab,
    *,
    location: str = "",
) -> list[Diagnostic]:
    """Check a rendered prompt against the entry it should carry.

    The three invariants a guarded writer must trust: the ``## Typed Entry`` block
    strict-loads equal to ``resolved_entry``, the dispatch ``status:`` lines agree
    and are plain, and no sentinel survived. Takes the rendered text directly so a
    renderer regression is caught rather than assumed away.
    """

    defects: list[Diagnostic] = []
    defects.extend(_carrier_defects(rendered, resolved_entry, location))
    defects.extend(_status_rail_defects(rendered, resolved_entry, location))
    defects.extend(_sentinel_defects(rendered, vocab, location))
    return defects


def _carrier_defects(
    rendered: str, resolved_entry: Mapping[str, Any], loc: str
) -> list[Diagnostic]:
    blocks = _typed_entry_blocks(rendered)
    if blocks is None or not blocks:
        return [Diagnostic("typed_entry_missing", loc, "no ## Typed Entry yaml block")]
    if len(blocks) > 1:
        return [
            Diagnostic(
                "typed_entry_ambiguous", loc, "more than one yaml block in the Typed Entry section"
            )
        ]
    block = blocks[0]
    try:
        loaded = load_yaml_bytes(block.encode("utf-8"), limits=SLICE_YAML_LIMITS).value
    except YamlBoundaryError as exc:
        return [Diagnostic("typed_entry_unparseable", loc, exc.message)]
    defects: list[Diagnostic] = []
    if loaded != resolved_entry:
        defects.append(
            Diagnostic(
                "typed_entry_mismatch",
                loc,
                "Typed Entry block does not equal the attempt-resolved entry",
            )
        )
    expected_status = str(resolved_entry.get("status")) if isinstance(resolved_entry, dict) else ""
    block_status_lines = [ln for ln in block.splitlines() if ln.startswith("status:")]
    if block_status_lines != [f"status: {expected_status}"]:
        defects.append(
            Diagnostic(
                "typed_entry_status_line",
                loc,
                "Typed Entry status must be one plain line-start 'status: <value>' line",
            )
        )
    return defects


def _status_rail_defects(
    rendered: str, resolved_entry: Mapping[str, Any], loc: str
) -> list[Diagnostic]:
    status = str(resolved_entry.get("status")) if isinstance(resolved_entry, dict) else ""
    lines = [ln for ln in rendered.splitlines() if ln.startswith("status:")]
    if len(lines) < 2 or any(ln != f"status: {status}" for ln in lines):
        return [
            Diagnostic(
                "rendered_status_disagreement",
                loc,
                "every line-start status line must be a plain 'status: <value>' with the"
                " entry's value, and at least two must appear",
            )
        ]
    return []


def _sentinel_defects(rendered: str, vocab: ContractVocab, loc: str) -> list[Diagnostic]:
    hits = sorted({s for s in vocab.sentinels if s in rendered})
    if hits:
        return [
            Diagnostic(
                "rendered_sentinel_residue",
                loc,
                "unresolved sentinel in rendered prompt: " + ", ".join(hits),
            )
        ]
    return []


def _typed_entry_blocks(rendered: str) -> list[str] | None:
    """The fenced yaml block bodies inside the ``## Typed Entry`` section.

    ``None`` when the section is absent; an empty list when it carries no block.
    """

    lines = rendered.splitlines()
    try:
        heading = lines.index("## Typed Entry")
    except ValueError:
        return None
    body: list[str] = []
    for line in lines[heading + 1 :]:
        if line.startswith("## "):
            break
        body.append(line)
    blocks: list[str] = []
    index = 0
    while index < len(body):
        if body[index].strip() == "```yaml":
            buf: list[str] = []
            index += 1
            while index < len(body) and body[index].strip() != "```":
                buf.append(body[index])
                index += 1
            blocks.append("\n".join(buf))
        index += 1
    return blocks


# --- dispatch surface -------------------------------------------------------


def evaluate_dispatch(
    entry: SliceEntry,
    vocab: ContractVocab,
    *,
    repo_root: Path,
    attempt: str | None = None,
) -> DispatchDecision:
    """Decide whether ``entry`` may be dispatched, with the refusals if not.

    The four enumerated refusals: a frozen entry, a ready entry missing dispatch
    authority, a ready entry with an unsatisfied opening gate, and any prompt write
    not backed by a ready entry. An unhealthy entry is never dispatchable, and it
    short-circuits before any authority or gate filesystem access (F3).
    """

    health = evaluate_health(entry, vocab, attempt=attempt)
    refusals: list[Diagnostic] = []

    status = entry.status
    if status == "frozen":
        refusals.append(
            Diagnostic(
                "entry_frozen",
                entry.slice_id,
                "a frozen entry is valid planning material, not current work; refusing to dispatch",
            )
        )
    elif status != "ready":
        refusals.append(
            Diagnostic(
                "entry_not_ready",
                entry.slice_id,
                f"a prompt write requires a ready entry; status is {status!r}",
            )
        )
    elif health.ok:
        # Only a semantically healthy, ready entry reaches the filesystem evidence
        # seam; an unhealthy entry never stats/opens/hashes authority or gate paths.
        authority_refusal = _authority_refusal(entry, repo_root)
        if authority_refusal is not None:
            refusals.append(authority_refusal)
        refusals.extend(_unsatisfied_gate_defects(entry, vocab, repo_root))

    if not health.ok:
        refusals.append(
            Diagnostic(
                "entry_unhealthy",
                entry.slice_id,
                "entry has health defects: " + ", ".join(sorted(set(health.defect_codes()))),
            )
        )

    return DispatchDecision(entry.slice_id, not refusals, tuple(refusals), health)


def _authority_refusal(entry: SliceEntry, repo_root: Path) -> Diagnostic | None:
    """Refuse a ready entry whose dispatch authority is not an admitted record.

    A syntactically valid path is not recorded authority (F1): the granting record
    must exist as a regular file strictly resolved under the repository root (F3).
    Called only for a healthy ready entry, so the path syntax is already
    validated; the defensive re-check keeps the refusal correct for any caller.
    """

    authority = entry.dispatch_authority
    if not (isinstance(authority, str) and authority.strip() and _record_path_ok(authority)):
        return Diagnostic(
            "dispatch_authority_missing",
            entry.slice_id,
            "status: ready requires a valid dispatch_authority record path",
        )
    norm = _lexical_local_reference(authority)
    target = None if norm is None else _admitted_local_file(repo_root, norm)
    if target is None:
        return Diagnostic(
            "dispatch_authority_missing",
            entry.slice_id,
            f"dispatch_authority record is absent or not a regular file resolved under"
            f" the repository root: {authority}",
        )
    return None


def _unsatisfied_gate_defects(
    entry: SliceEntry, vocab: ContractVocab, repo_root: Path
) -> list[Diagnostic]:
    gates = entry.data.get("opening_gates")
    if gates == "none" or not isinstance(gates, list):
        return []
    defects: list[Diagnostic] = []
    for index, gate in enumerate(gates):
        gloc = f"{entry.slice_id}.opening_gates[{index}]"
        if not isinstance(gate, Mapping):
            continue
        kind = gate.get("kind")
        reference = gate.get("reference")
        if kind not in LOCAL_EVIDENCE_GATE_KINDS:
            defects.append(
                Diagnostic(
                    "opening_gate_unsatisfied",
                    gloc,
                    f"{kind} gate requires external evidence the local dispatch surface"
                    f" cannot confirm",
                )
            )
            continue
        # Two-phase admission before any read (F3): lexical rejection with no
        # I/O, then strict canonical resolution under the resolved root.
        norm = _lexical_local_reference(reference)
        if norm is None:
            defects.append(
                Diagnostic(
                    "opening_gate_unsatisfied",
                    gloc,
                    f"{kind} gate reference is not a locally contained record path: {reference!r}",
                )
            )
            continue
        target = _admitted_local_file(repo_root, norm)
        if target is None:
            defects.append(
                Diagnostic(
                    "opening_gate_unsatisfied",
                    gloc,
                    f"{kind} evidence is absent or not a regular file resolved under"
                    f" the repository root: {reference}",
                )
            )
        elif kind == "accepted_review" and not _accepting_review(target, vocab):
            defects.append(
                Diagnostic(
                    "opening_gate_unsatisfied",
                    gloc,
                    f"accepted_review evidence is not a conforming accepting review: {reference}",
                )
            )
        elif kind == "artifact_identity" and _sha256_file(target) != gate.get("sha256"):
            defects.append(
                Diagnostic(
                    "opening_gate_unsatisfied",
                    gloc,
                    f"artifact_identity digest does not match: {reference}",
                )
            )
    return defects


def _lexical_local_reference(reference: Any) -> str | None:
    """Phase one of evidence admission: pure-lexical rejection, no filesystem access.

    Returns the normalized repository-relative file reference, or ``None`` for a
    non-string, empty, absolute, escaping, or directory reference. ``None`` means
    phase two never runs, so no stat, open, or hash follows.
    """

    if not isinstance(reference, str):
        return None
    norm = _normalized_relative(reference)
    if norm is None or norm.endswith("/"):
        return None
    return norm


def _admitted_local_file(repo_root: Path, norm: str) -> Path | None:
    """Phase two: strict canonical admission of a lexically accepted reference.

    Both the repository root and the referenced target are resolved strictly —
    aliases (symlinks, junctions) followed, a missing component refused — and the
    target is admitted only when its resolved form lies strictly under the
    resolved root and is a regular file. Containment is decided on the resolved
    form before the regular-file check, so a target resolving outside the root
    receives no stat; the admitted resolved path is what every content read or
    hash consumer receives, never the lexical one.
    """

    try:
        root = repo_root.resolve(strict=True)
        resolved = (repo_root / norm).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if resolved == root or not resolved.is_relative_to(root):
        return None
    if not resolved.is_file():
        return None
    return resolved


def _accepting_review(path: Path, vocab: ContractVocab) -> bool:
    """Whether the admitted file at ``path`` is a conforming accepting review.

    Bounded read, then the finite exact closure-record shape of contract section
    9, read section-local and line-based exactly as the released checker reads it
    (no fence parsing; headings counted at line start wherever they stand): exactly
    one ``## Closure Decision`` heading immediately followed by exactly one
    ``## Verdict`` heading; the closure section holds exactly two non-empty lines,
    ``Objective status: <vocabulary value>`` then a non-empty ``Objective
    evidence:`` line; the first non-empty verdict line is the verdict footer and
    no objective-status line stands under ``## Verdict``. Accepted only when the
    footer's verdict is ``pass`` or ``override``. This is not a general closure
    parser: it yields one boolean and exposes no record.
    """

    try:
        with path.open("rb") as handle:
            raw = handle.read(_REVIEW_MAX_BYTES + 1)
    except OSError:
        return False
    if len(raw) > _REVIEW_MAX_BYTES:
        return False
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return False

    order: list[str] = []
    bodies: dict[str, list[str]] = {}
    current = ""
    for line in text.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            current = match.group(1)
            order.append(current)
        else:
            bodies.setdefault(current, []).append(line)
    if order.count("Closure Decision") != 1 or order.count("Verdict") != 1:
        return False
    if order.index("Verdict") != order.index("Closure Decision") + 1:
        return False
    closure = [ln for ln in bodies.get("Closure Decision", []) if ln.strip()]
    if len(closure) != 2:
        return False
    status_line, evidence_line = closure
    if not status_line.startswith("Objective status:"):
        return False
    if status_line.split(":", 1)[1].strip() not in vocab.objective_status_values:
        return False
    if (
        not evidence_line.startswith("Objective evidence:")
        or not evidence_line.split(":", 1)[1].strip()
    ):
        return False
    verdict_lines = [ln for ln in bodies.get("Verdict", []) if ln.strip()]
    if not verdict_lines or any(ln.startswith("Objective status:") for ln in verdict_lines):
        return False
    footer = _VERDICT_FOOTER_RE.match(verdict_lines[0])
    return footer is not None and footer.group(1) in ACCEPTING_VERDICTS


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def guarded_dispatch(
    entry: SliceEntry,
    vocab: ContractVocab,
    *,
    repo_root: Path,
    destination: str | Path,
    attempt: str | None = None,
) -> DispatchResult:
    """Write exactly the rendered prompt when dispatchable; otherwise refuse.

    On refusal this returns before touching the filesystem: nothing at
    ``destination`` is created, so the enumerated invalid cases write nothing.
    """

    decision = evaluate_dispatch(entry, vocab, repo_root=repo_root, attempt=attempt)
    if not decision.dispatchable:
        return DispatchResult(decision, False, None)
    text = render_prompt(entry, vocab, attempt=attempt)
    target = Path(destination)
    target.write_text(text, encoding="utf-8")
    return DispatchResult(decision, True, str(target))
