"""Bounded exact-path read-only OKF/profile observation (M004).

This module gives an external consumer exactly one deterministic, read-only
library operation over one explicitly named Markdown path: it returns the pinned
template candidate's OKF-concept and framework-profile observation for that
artifact and nothing else. It observes; it never decides.

The observation reproduces the pinned ``full_parser`` oracle of the template
candidate consumer contract
(``04_delivery/frutlups_okf_kickoff_handoff/09_template_candidate_consumer_contract.md``
section 4) on top of the one accepted product YAML boundary
(:func:`frutlups._yaml.load_yaml_bytes`). Markdown framing, resource ceilings,
producer-subset policy from original lexemes, registry and version checks, and
the exact result vocabulary live here; YAML syntax and representation stay in
the single accepted engine. No second semantic parser exists and none of the
template control lane's scripts are imported.

What this operation never does:

* it never writes, creates, deletes, touches, normalizes, locks, or renames a
  file, never changes an mtime, never appends a journal entry, never initializes
  memory, never calls a provider, and never mutates caller input;
* it never searches, globs, lists, or walks a directory, never resolves a
  repository or layout, and reads only the one supplied path (reading the target
  of an explicitly supplied symlink is exact-path observation, not discovery);
* it never computes or returns a native-region class, native prompt validity,
  native report validity, acceptance, frontier, gate, runner, write, or handoff
  decision -- ``execution_eligibility`` is the literal string ``not_evaluated``
  on every result, and no combination of the two observed layers ever grants
  anything.

Public surface: exactly the two contract constants, the two frozen dataclasses,
and the one observation function re-exported from ``frutlups``.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from frutlups._yaml import (
    DEFAULT_YAML_LIMITS,
    ScalarRole,
    YamlBoundaryError,
    YamlDocument,
    YamlFailure,
    YamlNodeEvidence,
    YamlNodeKind,
    load_yaml_bytes,
)

__all__ = [
    "OKF_PROFILE_OBSERVATION_CONTRACT_ID",
    "OKF_PROFILE_OBSERVATION_CONTRACT_VERSION",
    "OKFProfileObservation",
    "ProfileLayerResult",
    "observe_okf_profile_path",
]

OKF_PROFILE_OBSERVATION_CONTRACT_ID = "frutlups.okf_profile_observation"
OKF_PROFILE_OBSERVATION_CONTRACT_VERSION = 1

# Total-artifact ceiling: enforced on raw bytes before UTF-8 decode, line
# materialization, framing, or any YAML work (pinned profile-mode ceiling).
_MAX_ARTIFACT_BYTES = 1_048_576

# One read-only descriptor open. O_BINARY exists only on Windows (binary-mode
# descriptor); O_NONBLOCK exists only on POSIX, where it prevents the open of a
# FIFO or device from blocking while the pre-open stat's answer is re-verified
# on the opened descriptor. A nonblocking flag has no effect on reading a
# regular file.
_OPEN_FLAGS = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)

# Pinned candidate profile version (canonical profile document, `0.1-rc.1`).
_PINNED_FRAMEWORK_PROFILE = "0.1-rc.1"

# Shared type registry (canonical profile section 5.2), including the
# tool-owned reserved types. Pinned; extended only through change control.
_PROFILE_TYPE_REGISTRY = frozenset(
    {
        "brief",
        "constraint",
        "decision",
        "analysis",
        "coding_prompt",
        "review_prompt",
        "self_report",
        "review_report",
        "verdict_record",
        "delivery_plan",
        "framework_doc",
        "source",
        "claim",
        "entity",
        "page",
        "milestone",
        "slice",
    }
)

# Canonical scalar spellings (canonical profile section 6.3), applied to the
# original lexeme -- never to a reconstructed Python value.
_INT_CANONICAL_RE = re.compile(r"^(?:0|-?[1-9][0-9]*)$")
# A plain lexeme that looks numeric (exponent, underscore, sexagesimal, or
# decimal groupings) but that the engine left as a string is a cross-parser
# hazard and is out of the producer subset.
_NUMERIC_LIKE_RE = re.compile(r"^[+-]?[0-9][0-9_]*([.:][0-9_]+)*([eE][+-]?[0-9]+)?$")

_STR_TAG = "tag:yaml.org,2002:str"
_INT_TAG = "tag:yaml.org,2002:int"
_BOOL_TAG = "tag:yaml.org,2002:bool"
_NULL_TAG = "tag:yaml.org,2002:null"

# The complete supplementary-diagnostic vocabulary. Diagnostics are
# non-authoritative, deterministic, path-safe, and hostile-input-free; ordinary
# oracle outcomes carry an empty tuple.
_READ_FAILED_DIAGNOSTIC = "artifact read failed"
_INVALID_UTF8_DIAGNOSTIC = "artifact is not valid UTF-8"
# The three legal diagnostics tuples: empty for every oracle outcome, or
# exactly one fixed message for the two pre-evaluation failures.
_LEGAL_DIAGNOSTICS = frozenset(
    {(), (_READ_FAILED_DIAGNOSTIC,), (_INVALID_UTF8_DIAGNOSTIC,)}
)
_MAX_DIAGNOSTIC_LENGTH = 240

# Boundary refusal categories that are bounded resource refusals (pinned oracle
# row 6). Every other category the framed-frontmatter call can raise is a
# conclusive load failure (pinned oracle row 5): INVALID_YAML,
# MULTIPLE_DOCUMENTS, and DUPLICATE_KEY. READ_FAILED, INPUT_NOT_BYTES, and
# INVALID_UTF8 are unreachable here (this module owns the read and passes the
# in-limit bytes of an already strictly decoded string), and UNSUPPORTED_TAG is
# unreachable in representation-only mode, where every tag is representation
# evidence rather than a refusal.
_RESOURCE_REFUSALS = frozenset(
    {
        YamlFailure.INPUT_TOO_LARGE,
        YamlFailure.TOO_MANY_LINES,
        YamlFailure.LINE_TOO_LONG,
        YamlFailure.TOO_MANY_TOKENS,
        YamlFailure.TOO_MANY_ALIASES,
        YamlFailure.TOO_DEEP,
        YamlFailure.TOO_MANY_NODES,
        YamlFailure.SCALAR_TOO_LONG,
        YamlFailure.MAPPING_TOO_LARGE,
        YamlFailure.SEQUENCE_TOO_LARGE,
        YamlFailure.ALIAS_CYCLE,
    }
)


@dataclass(frozen=True)
class ProfileLayerResult:
    """One observed layer: a pinned result word plus its reason code or ``None``."""

    result: str
    reason: str | None


@dataclass(frozen=True)
class OKFProfileObservation:
    """The versioned, frozen result of one exact-path observation.

    ``okf_concept`` and ``framework_profile`` are separate, causally independent
    layer results; ``execution_eligibility`` is always the literal string
    ``not_evaluated``; ``diagnostics`` is supplementary and carries no decision
    authority. There is no combined truth value and no custom truthiness.
    """

    contract_id: str
    contract_version: int
    okf_concept: ProfileLayerResult
    framework_profile: ProfileLayerResult
    execution_eligibility: str
    diagnostics: tuple[str, ...]


# The ten legal layer pairings: the pinned oracle rows of consumer contract 09
# section 4 (the neutral row also carries the pre-evaluation read/decode
# failures). Reasons are None unless the exact row requires one.
_LEGAL_LAYER_OUTCOMES = frozenset(
    {
        ("not_evaluated", None, "not_applicable", None),  # row 1 / pre-L1 failures
        ("pass", None, "not_applicable", None),  # row 2
        ("pass", None, "pass", None),  # row 3
        ("pass", None, "fail", "PROFILE_YAML_OUT_OF_SUBSET"),  # row 4
        ("fail", "OKF_YAML_INVALID", "fail", "PROFILE_YAML_OUT_OF_SUBSET"),  # row 5
        ("unverified", "OKF_PARSE_LIMIT_EXCEEDED", "fail", "PROFILE_YAML_OUT_OF_SUBSET"),  # row 6
        ("fail", "OKF_FRONTMATTER_MISSING", "not_applicable", None),  # row 7
        ("fail", "OKF_TYPE_MISSING", "not_applicable", None),  # row 8
        ("pass", None, "fail", "PROFILE_TYPE_UNSUPPORTED"),  # row 9
        ("pass", None, "fail", "PROFILE_VERSION_UNSUPPORTED"),  # row 10
    }
)

_NEUTRAL_OUTCOME = ("not_evaluated", None, "not_applicable", None)


def _observation(
    okf_result: str,
    okf_reason: str | None,
    profile_result: str,
    profile_reason: str | None,
    diagnostics: tuple[str, ...] = (),
) -> OKFProfileObservation:
    """The one constructor seam every observation passes through.

    It admits exactly the ten pinned layer pairings and the fixed diagnostic
    vocabulary; anything else is a defect in this module, not a caller error,
    and is refused before a value can exist.
    """

    outcome = (okf_result, okf_reason, profile_result, profile_reason)
    if outcome not in _LEGAL_LAYER_OUTCOMES:
        raise ValueError("okf profile observation outcome outside the pinned oracle")
    if not isinstance(diagnostics, tuple) or diagnostics not in _LEGAL_DIAGNOSTICS:
        raise ValueError("okf profile observation diagnostics outside the fixed vocabulary")
    for diagnostic in diagnostics:
        if len(diagnostic) > _MAX_DIAGNOSTIC_LENGTH:
            raise ValueError("okf profile observation diagnostic exceeds the bound")
    if diagnostics and outcome != _NEUTRAL_OUTCOME:
        raise ValueError("okf profile observation diagnostics require the neutral outcome")
    return OKFProfileObservation(
        contract_id=OKF_PROFILE_OBSERVATION_CONTRACT_ID,
        contract_version=OKF_PROFILE_OBSERVATION_CONTRACT_VERSION,
        okf_concept=ProfileLayerResult(result=okf_result, reason=okf_reason),
        framework_profile=ProfileLayerResult(result=profile_result, reason=profile_reason),
        execution_eligibility="not_evaluated",
        diagnostics=diagnostics,
    )


def _frame_frontmatter(text: str) -> tuple[str, str]:
    """Return ``(frontmatter_text, status)`` under the exact framing contract.

    ``status`` is ``"ok"``, ``"legacy"`` (no opening delimiter), or
    ``"unterminated"``. A frame opens only when the first line is exactly
    ``---``; a frame closes at the first later line exactly ``---``. Indented,
    padded, tabbed, or BOM-prefixed delimiters are content. LF and CRLF are both
    accepted (``splitlines`` removes either); the body after the closing line is
    inert.
    """

    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return "", "legacy"
    for index in range(1, len(lines)):
        if lines[index] == "---":
            return "\n".join(lines[1:index]), "ok"
    return "", "unterminated"


def _scalar_value_in_subset(occurrence: YamlNodeEvidence) -> bool:
    """Whether a scalar value obeys the canonical producer subset (profile 6.3),
    decided from the original lexeme, resolved tag, and written style."""

    if occurrence.style == '"':
        return True  # a double-quoted string is canonical
    if occurrence.style in ("'", "|", ">"):
        return False  # single-quoted and block scalars are out of subset
    lexeme = occurrence.lexeme or ""
    if occurrence.tag == _STR_TAG:
        # A plain string that resolves as str but looks numeric is out of subset.
        return not _NUMERIC_LIKE_RE.match(lexeme)
    if occurrence.tag == _INT_TAG:
        return bool(_INT_CANONICAL_RE.match(lexeme))
    if occurrence.tag == _BOOL_TAG:
        return lexeme in ("true", "false")
    if occurrence.tag == _NULL_TAG:
        return lexeme in ("null", "~", "")
    return False  # float, timestamp, unknown, forbidden, or any other tag


def _out_of_subset(document: YamlDocument) -> bool:
    """Whether the framed document uses anything outside the producer subset.

    Decided entirely from the boundary's representation evidence: aggregate
    features, scan-level scalar styles, and the per-occurrence node stream
    (which includes alias occurrences at their own paths) -- never from
    reconstructed Python values.
    """

    features = document.features
    if (
        features.has_anchors
        or features.has_aliases
        or features.has_merge_keys
        or features.has_flow_collections
        or features.has_explicit_tags
    ):
        return True
    styles = features.scalar_styles
    if "'" in styles or "|" in styles or ">" in styles:
        return True
    for occurrence in document.node_occurrences:
        if occurrence.role in (ScalarRole.MAPPING_VALUE, ScalarRole.SEQUENCE_ITEM):
            if occurrence.kind is YamlNodeKind.SCALAR and not _scalar_value_in_subset(occurrence):
                return True
        if occurrence.role is ScalarRole.MAPPING_KEY:
            if occurrence.kind is not YamlNodeKind.SCALAR:
                return True  # collection-valued (complex) mapping key
            if occurrence.tag != _STR_TAG:
                return True  # non-string mapping key
    return False


def _top_level_scalar(document: YamlDocument, key: str) -> tuple[bool, str | None]:
    """Return ``(present, lexeme)`` for a top-level string-keyed scalar field.

    Presence and the value lexeme come from the boundary's per-occurrence node
    evidence, matching the pinned checker's node semantics exactly: a
    merge-injected field is not a top-level field; an aliased key or value is
    seen at its occurrence path with the target's tag and lexeme; and a
    collection value is present with no lexeme.
    """

    index: int | None = None
    for occurrence in document.node_occurrences:
        if (
            occurrence.role is ScalarRole.MAPPING_KEY
            and occurrence.kind is YamlNodeKind.SCALAR
            and occurrence.tag == _STR_TAG
            and len(occurrence.path) == 1
            and occurrence.path[0][0] == "key"
            and occurrence.lexeme == key
        ):
            index = occurrence.path[0][1]
            break
    if index is None:
        return False, None
    for occurrence in document.node_occurrences:
        if occurrence.path == (("value", index),):
            if occurrence.kind is YamlNodeKind.SCALAR:
                return True, occurrence.lexeme
            return True, None  # a collection value: present, no lexeme
    return True, None  # unreachable defensively: every composed pair has a value


def _observe_text(text: str) -> OKFProfileObservation:
    """Map one decoded artifact to its pinned oracle row."""

    frontmatter, status = _frame_frontmatter(text)
    if status == "legacy":
        return _observation("not_evaluated", None, "not_applicable", None)
    if status == "unterminated":
        return _observation("fail", "OKF_FRONTMATTER_MISSING", "not_applicable", None)

    # Exactly one call into the one accepted product YAML engine, in
    # representation-only mode with the accepted profile limits: the pinned
    # checker's semantics are pre-construction, so no value is ever
    # constructed, no construction failure can occur, and every tag is
    # representation evidence. Legacy and unterminated inputs make zero calls.
    # Only the boundary's typed refusals are handled; a programming error is a
    # defect and must surface, never be absorbed into an oracle row.
    try:
        document = load_yaml_bytes(
            frontmatter.encode("utf-8"), limits=DEFAULT_YAML_LIMITS, representation_only=True
        )
    except YamlBoundaryError as refusal:
        if refusal.category in _RESOURCE_REFUSALS:
            return _observation(
                "unverified", "OKF_PARSE_LIMIT_EXCEEDED", "fail", "PROFILE_YAML_OUT_OF_SUBSET"
            )
        return _observation("fail", "OKF_YAML_INVALID", "fail", "PROFILE_YAML_OUT_OF_SUBSET")

    present, type_value = _top_level_scalar(document, "type")
    if not present or not type_value:
        return _observation("fail", "OKF_TYPE_MISSING", "not_applicable", None)

    if _out_of_subset(document):
        return _observation("pass", None, "fail", "PROFILE_YAML_OUT_OF_SUBSET")
    if type_value not in _PROFILE_TYPE_REGISTRY:
        return _observation("pass", None, "fail", "PROFILE_TYPE_UNSUPPORTED")
    fp_present, fp_value = _top_level_scalar(document, "framework_profile")
    if not fp_present or fp_value is None:
        return _observation("pass", None, "not_applicable", None)  # not opted in
    if fp_value != _PINNED_FRAMEWORK_PROFILE:
        return _observation("pass", None, "fail", "PROFILE_VERSION_UNSUPPORTED")
    return _observation("pass", None, "pass", None)


def observe_okf_profile_path(path: str | Path, /) -> OKFProfileObservation:
    """Observe one explicitly named Markdown artifact, read-only.

    Opens the artifact exactly once, reads at most the total-artifact ceiling
    plus one byte, and refuses an over-limit artifact before decoding. An
    unreadable path and invalid UTF-8 each return the neutral layers with their
    one fixed diagnostic; every other outcome is one of the pinned oracle rows
    with an empty diagnostics tuple. The supplied path, exception text, file
    content, and machine-local values never appear in any result.
    """

    if not isinstance(path, (str, Path)):
        return _observation(
            "not_evaluated", None, "not_applicable", None, diagnostics=(_READ_FAILED_DIAGNOSTIC,)
        )
    # Only a regular file is an artifact, and classification must be bounded
    # BEFORE anything can block: a FIFO with no writer blocks inside a plain
    # blocking open, and a character device such as the Windows console can
    # block a read. The sequence is: (1) a symlink-following pre-open stat
    # refuses anything not reported regular (preserving observation of the
    # regular target of an explicitly supplied symlink); (2) exactly one
    # descriptor is opened read-only/binary with the platform's nonblocking
    # flag, which closes the pre-stat replacement window on POSIX; (3) the
    # opened descriptor is re-verified as still regular before (4) one bounded
    # read on that same descriptor.
    try:
        if not stat.S_ISREG(os.stat(path).st_mode):
            return _observation(
                "not_evaluated",
                None,
                "not_applicable",
                None,
                diagnostics=(_READ_FAILED_DIAGNOSTIC,),
            )
        descriptor = os.open(path, _OPEN_FLAGS)
    except (OSError, ValueError):
        return _observation(
            "not_evaluated", None, "not_applicable", None, diagnostics=(_READ_FAILED_DIAGNOSTIC,)
        )
    handle = None
    try:
        if stat.S_ISREG(os.fstat(descriptor).st_mode):
            handle = os.fdopen(descriptor, "rb")
    except (OSError, ValueError):
        pass  # refused below; the descriptor is closed by the finally block
    finally:
        if handle is None:
            os.close(descriptor)  # exactly one close on every pre-wrapper exit
    if handle is None:
        return _observation(
            "not_evaluated", None, "not_applicable", None, diagnostics=(_READ_FAILED_DIAGNOSTIC,)
        )
    try:
        with handle:
            data = handle.read(_MAX_ARTIFACT_BYTES + 1)
    except (OSError, ValueError):
        return _observation(
            "not_evaluated", None, "not_applicable", None, diagnostics=(_READ_FAILED_DIAGNOSTIC,)
        )
    if len(data) > _MAX_ARTIFACT_BYTES:
        return _observation(
            "unverified", "OKF_PARSE_LIMIT_EXCEEDED", "fail", "PROFILE_YAML_OUT_OF_SUBSET"
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return _observation(
            "not_evaluated", None, "not_applicable", None, diagnostics=(_INVALID_UTF8_DIAGNOSTIC,)
        )
    return _observe_text(text)
