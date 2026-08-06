"""The one private product YAML syntax and representation boundary (M002-S02).

This module is the single place the product is allowed to turn YAML bytes into a
Python value. It uses the pure-Python ``yaml.SafeLoader`` for *syntax and
representation only* and keeps every finite resource bound, the duplicate-key
policy, the tag policy, and diagnostic rendering in owned project code.

It returns the safely constructed value together with two kinds of descriptive
evidence: aggregate :class:`YamlFeatures` for the document as a whole, and one
immutable :class:`ScalarEvidence` record per unique scalar node carrying that
scalar's **original lexeme**, resolved tag, style, explicit-tag flag, structural
location, and safe line/column. The pinned consumer contract requires canonical
scalar and style decisions to be made from the original lexeme rather than the
resolved Python value, so the boundary retains that lexeme instead of forcing a
later consumer to reparse. No live loader or node graph is exposed.

What this module deliberately does **not** do:

* it assigns no OKF or framework-profile result, no reason code, no layout
  validity, no routing eligibility, and no write authority -- the feature and
  scalar evidence it records is *descriptive*, and a later typed consumer owns
  meaning;
* it applies no producer subset, no canonical-scalar policy, and no schema, so
  ``SafeLoader``'s own YAML resolution semantics are preserved exactly;
* it never mutates global loader tables, implicit resolvers, the process
  recursion limit, environment variables, its input bytes, or any file.

``yaml.SafeLoader`` is used directly rather than through a subclass because this
boundary registers no constructor, resolver, or representer: there is nothing to
customize, and an empty subclass would isolate nothing (PyYAML subclasses share
the base tables until a registration copies them). If a future slice ever needs a
constructor or resolver, it must register it on a private subclass then, with
hostile-input tests -- never on the shared loader class.

Public surface: none. This module is private (``frutlups._yaml``), is not
re-exported from ``frutlups``, and does not change the public export list.
"""

from __future__ import annotations

import errno as _errno
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml  # mandatory dependency; a missing install surfaces as ImportError

__all__ = [
    "DEFAULT_YAML_LIMITS",
    "ScalarEvidence",
    "ScalarRole",
    "YamlBoundaryError",
    "YamlDocument",
    "YamlFailure",
    "YamlFeatures",
    "YamlLimits",
    "YamlNodeEvidence",
    "YamlNodeKind",
    "load_yaml_bytes",
    "load_yaml_path",
]

_MERGE_TAG = "tag:yaml.org,2002:merge"


class YamlFailure(str, Enum):
    """Stable failure categories for this boundary.

    The values are owned by this module. They are not OKF reason codes, not
    profile reason codes, and carry no authority beyond "this input was refused".
    """

    READ_FAILED = "read_failed"
    INPUT_NOT_BYTES = "input_not_bytes"
    INPUT_TOO_LARGE = "input_too_large"
    INVALID_UTF8 = "invalid_utf8"
    TOO_MANY_LINES = "too_many_lines"
    LINE_TOO_LONG = "line_too_long"
    TOO_MANY_TOKENS = "too_many_tokens"
    TOO_MANY_ALIASES = "too_many_aliases"
    TOO_DEEP = "too_deep"
    TOO_MANY_NODES = "too_many_nodes"
    SCALAR_TOO_LONG = "scalar_too_long"
    MAPPING_TOO_LARGE = "mapping_too_large"
    SEQUENCE_TOO_LARGE = "sequence_too_large"
    ALIAS_CYCLE = "alias_cycle"
    DUPLICATE_KEY = "duplicate_key"
    MULTIPLE_DOCUMENTS = "multiple_documents"
    UNSUPPORTED_TAG = "unsupported_tag"
    INVALID_YAML = "invalid_yaml"


@dataclass(frozen=True)
class YamlLimits:
    """Finite bounds applied before any expensive work.

    Depth counts the root node as 0 and every mapping-key, mapping-value, or
    sequence-item edge as one further level. Nodes are counted by identity, so a
    shared alias target is counted once while each alias *reference* still counts
    against ``max_aliases``. Mapping pairs are counted at the source, before merge
    expansion.
    """

    max_bytes: int = 65_536
    max_lines: int = 500
    max_line_length: int = 8_192
    max_tokens: int = 10_000
    max_nodes: int = 2_000
    max_depth: int = 32
    max_scalar_length: int = 16_384
    max_mapping_pairs: int = 500
    max_sequence_items: int = 1_000
    max_aliases: int = 50
    max_diagnostic_length: int = 240


DEFAULT_YAML_LIMITS = YamlLimits()


@dataclass(frozen=True)
class YamlFeatures:
    """Descriptive YAML feature evidence collected from tokens and nodes.

    Recorded so a later typed consumer can distinguish aliases, merges, flow
    collections, explicit tags, and scalar styles without reparsing through a
    second semantic engine. Presence of a feature is an observation, never a
    verdict.
    """

    has_anchors: bool = False
    has_aliases: bool = False
    has_merge_keys: bool = False
    has_flow_collections: bool = False
    has_explicit_tags: bool = False
    alias_references: int = 0
    scalar_styles: frozenset[str] = field(default_factory=frozenset)


class ScalarRole(str, Enum):
    """Where a scalar sits in its parent collection."""

    ROOT = "root"
    MAPPING_KEY = "mapping_key"
    MAPPING_VALUE = "mapping_value"
    SEQUENCE_ITEM = "sequence_item"


@dataclass(frozen=True)
class ScalarEvidence:
    """Immutable per-scalar representation evidence.

    Retained so a later typed consumer can apply canonical scalar and style policy
    from the **original lexeme**, as the pinned consumer contract requires, instead
    of from the resolved Python value -- and without reparsing or holding a live
    PyYAML loader or node graph.

    ``path`` is the route from the document root to this scalar as a tuple of
    ``(kind, index)`` steps, where ``kind`` is ``"key"``, ``"value"``, or
    ``"item"`` and ``index`` is the position within the parent collection. It is
    total and deterministic, so nested keys, nested values, and sequence items are
    distinguishable rather than merely encounter-ordered. ``role`` restates the
    kind of the final step for direct use.

    ``explicit_tag`` records whether a tag was *written*, not merely resolved: an
    untagged scalar that resolves to a standard tag stays ``False``. It is derived
    by associating the scalar with the node-property group that precedes it, so it
    is correct for both legal property orders -- ``!!str &anchor value`` and
    ``&anchor !!str value`` -- instead of depending on which property happens to
    come first.

    A scalar reached through an alias is recorded once, at the occurrence that
    defines it; alias reference counts stay in :class:`YamlFeatures`.

    This record is descriptive only. It carries no schema, OKF, profile,
    layout-validity, routing, acceptance, or write-authority result, and it is
    never rendered into a diagnostic.
    """

    lexeme: str
    tag: str
    style: str
    explicit_tag: bool
    role: ScalarRole
    path: tuple[tuple[str, int], ...]
    line: int
    column: int


class YamlNodeKind(str, Enum):
    """The structural kind of one composed node occurrence."""

    SCALAR = "scalar"
    MAPPING = "mapping"
    SEQUENCE = "sequence"


@dataclass(frozen=True)
class YamlNodeEvidence:
    """One immutable node occurrence in representation-only mode.

    Recorded on every *first* traversal of a node. When a previously visited
    alias target is reached again, that target container or scalar is recorded
    once more at the alias occurrence's path, but the descendants of a repeated
    container are **not** replayed at descendant alias-relative paths (the
    unique-node resource count is unchanged either way). This is sufficient for
    the current M004 top-level routing and producer-subset observation; it is
    not a fully expanded alias tree. ``lexeme`` is the exact scalar node value
    and ``None`` for collections; ``style`` is the scalar style or ``""`` for
    collections. The evidence is descriptive representation only -- it carries
    no mark, filename, source path, loader or node object, constructor, schema,
    producer-profile, OKF, or authority meaning.
    """

    kind: YamlNodeKind
    tag: str
    lexeme: str | None
    style: str
    role: ScalarRole
    path: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class YamlDocument:
    """A safely constructed value plus the evidence gathered while bounding it.

    In the default constructed mode ``value`` is the safely constructed value,
    ``value_constructed`` is ``True``, and ``node_occurrences`` is empty. In
    representation-only mode ``value`` is ``None`` (no document/value is
    constructed), ``value_constructed`` is ``False``, and ``node_occurrences``
    carries the deterministic node evidence recorded on first traversal, with
    the bounded repeated-alias behavior documented on :class:`YamlNodeEvidence`.
    """

    value: Any
    features: YamlFeatures = field(default_factory=YamlFeatures)
    scalars: tuple[ScalarEvidence, ...] = ()
    token_count: int = 0
    node_count: int = 0
    max_depth: int = 0
    mapping_pairs: int = 0
    node_occurrences: tuple[YamlNodeEvidence, ...] = ()
    value_constructed: bool = True


class YamlBoundaryError(Exception):
    """A refusal from this boundary, carrying a category and a bounded message."""

    def __init__(self, category: YamlFailure, message: str) -> None:
        super().__init__(message)
        self.category = category
        self.message = message


def _diagnostic(
    category: YamlFailure,
    limits: YamlLimits,
    *,
    limit: int | None = None,
    observed: int | None = None,
    line: int | None = None,
    column: int | None = None,
    detail: str | None = None,
) -> str:
    """Render a deterministic, bounded, path-safe message.

    Only the owned category, declared/observed counts, safe 1-based line and
    column numbers, and a short owned detail may appear. No source path, no
    machine-local path, no hostile scalar or key text, no PyYAML exception text,
    and no traceback ever reaches this string.

    The configured cap is honored exactly, for every value including 0, 1, and 2:
    the ellipsis is a courtesy that is dropped whenever it would not fit, and it
    can never push the result past the cap. A **negative** cap is deterministically
    clamped to zero, yielding an empty message rather than raising: a refusal must
    not be replaced by an unrelated error while its own diagnostic is being
    rendered. The stable failure category stays available on the exception in every
    case, so an empty message never loses refusal semantics.
    """

    parts = [f"yaml boundary refused: {category.value}"]
    if limit is not None:
        parts.append(f"limit {limit}")
    if observed is not None:
        parts.append(f"observed {observed}")
    if line is not None:
        parts.append(f"line {line}")
    if column is not None:
        parts.append(f"column {column}")
    if detail is not None:
        parts.append(detail)
    message = "; ".join(parts)
    cap = max(0, limits.max_diagnostic_length)
    if len(message) <= cap:
        return message
    if cap <= 3:
        return message[:cap]
    return message[: cap - 3] + "..."


def _refuse(category: YamlFailure, limits: YamlLimits, **fields: Any) -> YamlBoundaryError:
    return YamlBoundaryError(category, _diagnostic(category, limits, **fields))


def _safe_mark(exc: Exception) -> tuple[int | None, int | None]:
    """1-based (line, column) from a PyYAML mark, or (None, None).

    Only the integers are taken; the mark's name and snippet are never read,
    because they can carry a path or hostile source text.
    """

    mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
    if mark is None:
        return None, None
    line = getattr(mark, "line", None)
    column = getattr(mark, "column", None)
    return (
        None if line is None else line + 1,
        None if column is None else column + 1,
    )


def _invalid_yaml(exc: Exception, limits: YamlLimits) -> YamlBoundaryError:
    line, column = _safe_mark(exc)
    return _refuse(YamlFailure.INVALID_YAML, limits, line=line, column=column)


def _tag_supported(tag: str) -> bool:
    """Whether ``SafeLoader`` already supports this tag safely.

    Read-only inspection of the loader's own tables plus the merge tag, which
    ``SafeConstructor`` handles inside ``flatten_mapping`` rather than through a
    registered constructor. Nothing is registered here.
    """

    if tag == _MERGE_TAG:
        return True
    if tag in yaml.SafeLoader.yaml_constructors:
        return True
    return any(
        prefix is not None and tag.startswith(prefix)
        for prefix in yaml.SafeLoader.yaml_multi_constructors
    )


# ---------------------------------------------------------------------------
# Stage 1: bytes, decoding, and line shape (before any YAML work)
# ---------------------------------------------------------------------------


def _check_line_shape(text: str, limits: YamlLimits) -> None:
    lines = text.splitlines()
    if len(lines) > limits.max_lines:
        raise _refuse(
            YamlFailure.TOO_MANY_LINES, limits, limit=limits.max_lines, observed=len(lines)
        )
    for index, line in enumerate(lines, 1):
        if len(line) > limits.max_line_length:
            raise _refuse(
                YamlFailure.LINE_TOO_LONG,
                limits,
                limit=limits.max_line_length,
                observed=len(line),
                line=index,
            )


# ---------------------------------------------------------------------------
# Stage 2: token scan -- token, alias, and pre-compose nesting bounds
# ---------------------------------------------------------------------------


@dataclass
class _ScanResult:
    tokens: int = 0
    aliases: int = 0
    max_nesting: int = 0
    has_anchors: bool = False
    has_flow: bool = False
    has_explicit_tags: bool = False
    styles: set[str] = field(default_factory=set)
    # Start positions of node-property groups that contained an explicit tag.
    #
    # YAML lets a node carry an anchor and a tag in either order, and the node's
    # own start mark is the start of whichever property token comes first. Keying
    # on tag-token positions alone therefore misses `&anchor !!str value`, where
    # the node begins at the anchor. Grouping the consecutive property tokens that
    # precede one node and recording the *group's* start position is correct for
    # both legal orders, and it stays inside the single existing scan.
    explicit_tag_starts: set[tuple[int, int]] = field(default_factory=set)


_OPEN_TOKENS = (
    yaml.BlockSequenceStartToken,
    yaml.BlockMappingStartToken,
    yaml.FlowSequenceStartToken,
    yaml.FlowMappingStartToken,
)
_CLOSE_TOKENS = (
    yaml.BlockEndToken,
    yaml.FlowSequenceEndToken,
    yaml.FlowMappingEndToken,
)


def _scan(text: str, limits: YamlLimits) -> _ScanResult:
    """Scan tokens, enforcing token, alias, and nesting bounds before composing.

    The nesting bound is the mandatory pre-compose depth guard: PyYAML's composer
    recurses once per collection level, so an over-deep document must be refused
    here, before composition can recurse and before the post-compose graph walk
    would otherwise get the chance to run.

    The same pass groups node properties. Anchor and tag tokens for one node are
    consecutive and precede that node's content, so a run of them is one property
    group; the group ends at the first token that is neither. Recording the start
    position of every group that contained a tag makes explicit-tag association
    independent of whether the anchor or the tag was written first.
    """

    result = _ScanResult()
    nesting = 0
    property_start: tuple[int, int] | None = None
    property_tagged = False
    try:
        for token in yaml.scan(text, Loader=yaml.SafeLoader):
            result.tokens += 1
            if result.tokens > limits.max_tokens:
                raise _refuse(
                    YamlFailure.TOO_MANY_TOKENS,
                    limits,
                    limit=limits.max_tokens,
                    observed=result.tokens,
                )

            if isinstance(token, (yaml.AnchorToken, yaml.TagToken)):
                if property_start is None:
                    property_start = (token.start_mark.line, token.start_mark.column)
                property_tagged = property_tagged or isinstance(token, yaml.TagToken)
            elif property_start is not None:
                if property_tagged:
                    result.explicit_tag_starts.add(property_start)
                property_start = None
                property_tagged = False

            if isinstance(token, _OPEN_TOKENS):
                nesting += 1
                result.max_nesting = max(result.max_nesting, nesting)
                if nesting > limits.max_depth:
                    raise _refuse(
                        YamlFailure.TOO_DEEP, limits, limit=limits.max_depth, observed=nesting
                    )
                if isinstance(token, (yaml.FlowSequenceStartToken, yaml.FlowMappingStartToken)):
                    result.has_flow = True
            elif isinstance(token, _CLOSE_TOKENS):
                nesting = max(0, nesting - 1)
            elif isinstance(token, yaml.AliasToken):
                result.aliases += 1
                if result.aliases > limits.max_aliases:
                    raise _refuse(
                        YamlFailure.TOO_MANY_ALIASES,
                        limits,
                        limit=limits.max_aliases,
                        observed=result.aliases,
                    )
            elif isinstance(token, yaml.AnchorToken):
                result.has_anchors = True
            elif isinstance(token, yaml.TagToken):
                result.has_explicit_tags = True
            elif isinstance(token, yaml.ScalarToken):
                result.styles.add(token.style or "")
    except RecursionError:
        raise _refuse(YamlFailure.TOO_DEEP, limits, limit=limits.max_depth) from None
    except yaml.YAMLError as exc:
        raise _invalid_yaml(exc, limits) from None
    if property_start is not None and property_tagged:
        result.explicit_tag_starts.add(property_start)
    return result


# ---------------------------------------------------------------------------
# Stage 3: compose exactly one document
# ---------------------------------------------------------------------------


def _compose_single(loader: yaml.SafeLoader, limits: YamlLimits) -> yaml.Node | None:
    """Compose exactly one document, refusing a second one in its own category.

    Implemented with the composer's own ``check_node``/``get_node`` API rather
    than ``get_single_node`` so a second document is distinguished from other
    composer failures without inspecting PyYAML's exception text.
    """

    try:
        if not loader.check_node():
            return None
        root = loader.get_node()
        if loader.check_node():
            raise _refuse(YamlFailure.MULTIPLE_DOCUMENTS, limits)
        return root
    except RecursionError:
        raise _refuse(YamlFailure.TOO_DEEP, limits, limit=limits.max_depth) from None
    except yaml.YAMLError as exc:
        raise _invalid_yaml(exc, limits) from None


# ---------------------------------------------------------------------------
# Stage 4: cycle-safe bounded graph walk (before constructing any value)
# ---------------------------------------------------------------------------


@dataclass
class _WalkResult:
    nodes: int = 0
    depth: int = 0
    mapping_pairs: int = 0
    has_merge_keys: bool = False
    has_flow: bool = False
    scalars: list[ScalarEvidence] = field(default_factory=list)
    occurrences: list[YamlNodeEvidence] = field(default_factory=list)


def _node_occurrence(
    node: yaml.Node, role: ScalarRole, path: tuple[tuple[str, int], ...]
) -> YamlNodeEvidence:
    """The representation-only occurrence record for one composed node."""

    if isinstance(node, yaml.ScalarNode):
        return YamlNodeEvidence(
            kind=YamlNodeKind.SCALAR,
            tag=node.tag,
            lexeme=node.value,
            style=node.style or "",
            role=role,
            path=path,
        )
    kind = YamlNodeKind.MAPPING if isinstance(node, yaml.MappingNode) else YamlNodeKind.SEQUENCE
    return YamlNodeEvidence(kind=kind, tag=node.tag, lexeme=None, style="", role=role, path=path)


def _key_identity(
    loader: yaml.SafeLoader, key_node: yaml.Node, representation_only: bool
) -> object | None:
    """Resolved identity of a scalar key, or ``None`` when it cannot be established.

    This is the one bounded place either mode normalizes a scalar mapping key for
    semantic-duplicate identity: it constructs *only that scalar node* through the
    accepted ``SafeLoader`` scalar constructors, never the document value. The
    identity is ``(resolved_tag, constructed_value)`` from PyYAML's own scalar
    resolution, so ``1``/``01``, YAML boolean synonyms, and ``null``/``~`` collapse
    to one key while a quoted string key stays distinct from a typed scalar.

    The accepted-since-M002 allowlist is ``yaml.YAMLError``, ``RecursionError``,
    ``ValueError`` (bad ``!!int``/``!!float`` digits), and ``TypeError`` (an
    unhashable resolved value); when one is raised the identity is unavailable and
    this returns ``None``. **Default constructed mode keeps exactly that
    allowlist**, so a data-induced ``KeyError`` (an ``!!bool`` word outside the
    boolean set) or ``AttributeError`` (a malformed ``!!timestamp``) still
    surfaces from default mode exactly as it did before -- this correction does
    not reopen M002 default-mode behavior.

    **Representation-only mode additionally contains the remaining data-induced
    exceptions** those safe scalar constructors raise -- ``KeyError`` (a ``!!bool``
    word outside the boolean set), ``AttributeError`` (a malformed ``!!timestamp``),
    and ``IndexError`` (an empty or sign-only ``!!int``/``!!float`` lexeme, which
    PyYAML indexes before parsing) -- as an unavailable identity, so the read-only
    observation over bounded artifact bytes never leaks a raw PyYAML scalar-key
    construction exception. That set is the complete audit of PyYAML ``6.0.3``
    safe scalar constructors: every other malformed spelling resolves to
    ``ValueError`` or ``yaml.YAMLError`` above.

    The allowlist is deliberately type-based: it cannot distinguish an
    artifact-induced ``KeyError`` / ``AttributeError`` / ``IndexError`` from a
    programming defect injected at the same ``construct_object`` call with the
    same type, so representation-only mode contains either as unavailable
    identity. Non-allowlisted programming and control-flow failures -- for
    example ``RuntimeError``, ``KeyboardInterrupt``, ``SystemExit``, and
    ``GeneratorExit`` -- stay visible. An unavailable identity cannot form a
    semantic duplicate; every other key's identity and the accepted duplicate
    precedence are unchanged.
    """

    if not isinstance(key_node, yaml.ScalarNode):
        return None
    data_exceptions: tuple[type[BaseException], ...] = (
        yaml.YAMLError,
        RecursionError,
        ValueError,
        TypeError,
    )
    if representation_only:
        data_exceptions += (KeyError, AttributeError, IndexError)
    try:
        value = loader.construct_object(key_node, deep=True)
    except data_exceptions:
        return None
    identity: object = (key_node.tag, value)
    try:
        hash(identity)
    except TypeError:
        identity = (key_node.tag, repr(value))
    return identity


def _walk(
    node: yaml.Node,
    depth: int,
    loader: yaml.SafeLoader,
    limits: YamlLimits,
    result: _WalkResult,
    visited: set[int],
    active: set[int],
    explicit_tag_starts: set[tuple[int, int]],
    path: tuple[tuple[str, int], ...],
    role: ScalarRole,
    representation_only: bool,
) -> None:
    if depth > limits.max_depth:
        raise _refuse(YamlFailure.TOO_DEEP, limits, limit=limits.max_depth, observed=depth)
    result.depth = max(result.depth, depth)

    node_id = id(node)
    if node_id in active:
        raise _refuse(YamlFailure.ALIAS_CYCLE, limits)
    if node_id in visited:
        # A shared alias target is counted and inspected exactly once, but in
        # representation-only mode the repeated occurrence itself is recorded
        # at the alias occurrence's path.
        if representation_only:
            result.occurrences.append(_node_occurrence(node, role, path))
        return
    visited.add(node_id)
    result.nodes = len(visited)
    if result.nodes > limits.max_nodes:
        raise _refuse(
            YamlFailure.TOO_MANY_NODES, limits, limit=limits.max_nodes, observed=result.nodes
        )

    if not representation_only and not _tag_supported(node.tag):
        # In representation-only mode an unknown, forbidden, or
        # constructor-incompatible tag is valid representation evidence, not a
        # refusal: nothing will be constructed from it.
        raise _refuse(YamlFailure.UNSUPPORTED_TAG, limits)

    if isinstance(node, yaml.ScalarNode):
        if len(node.value) > limits.max_scalar_length:
            raise _refuse(
                YamlFailure.SCALAR_TOO_LONG,
                limits,
                limit=limits.max_scalar_length,
                observed=len(node.value),
            )
        if representation_only:
            result.occurrences.append(_node_occurrence(node, role, path))
        mark = node.start_mark
        result.scalars.append(
            ScalarEvidence(
                lexeme=node.value,
                tag=node.tag,
                style=node.style or "",
                explicit_tag=(mark.line, mark.column) in explicit_tag_starts,
                role=role,
                path=path,
                line=mark.line + 1,
                column=mark.column + 1,
            )
        )
        return

    if representation_only:
        result.occurrences.append(_node_occurrence(node, role, path))

    active.add(node_id)
    if isinstance(node, yaml.MappingNode):
        if node.flow_style:
            result.has_flow = True
        pairs = len(node.value)
        if pairs > limits.max_mapping_pairs:
            raise _refuse(
                YamlFailure.MAPPING_TOO_LARGE,
                limits,
                limit=limits.max_mapping_pairs,
                observed=pairs,
            )
        result.mapping_pairs += pairs
        seen: set[object] = set()
        for index, (key_node, value_node) in enumerate(node.value):
            if isinstance(key_node, yaml.ScalarNode) and key_node.tag == _MERGE_TAG:
                # A merge key participates in duplicate detection under a fixed
                # sentinel identity, distinct from a quoted "<<" string key, so two
                # source merge keys in one mapping are a duplicate. A single merge
                # keeps ordinary SafeLoader semantics.
                result.has_merge_keys = True
                identity: object = (_MERGE_TAG, None)
            else:
                identity = _key_identity(loader, key_node, representation_only)
            if identity is not None:
                if identity in seen:
                    raise _refuse(YamlFailure.DUPLICATE_KEY, limits)
                seen.add(identity)
            _walk(
                key_node, depth + 1, loader, limits, result, visited, active,
                explicit_tag_starts, path + (("key", index),), ScalarRole.MAPPING_KEY,
                representation_only,
            )
            _walk(
                value_node, depth + 1, loader, limits, result, visited, active,
                explicit_tag_starts, path + (("value", index),), ScalarRole.MAPPING_VALUE,
                representation_only,
            )
    elif isinstance(node, yaml.SequenceNode):
        if node.flow_style:
            result.has_flow = True
        items = len(node.value)
        if items > limits.max_sequence_items:
            raise _refuse(
                YamlFailure.SEQUENCE_TOO_LARGE,
                limits,
                limit=limits.max_sequence_items,
                observed=items,
            )
        for index, item in enumerate(node.value):
            _walk(
                item, depth + 1, loader, limits, result, visited, active,
                explicit_tag_starts, path + (("item", index),), ScalarRole.SEQUENCE_ITEM,
                representation_only,
            )
    active.discard(node_id)


def _walk_graph(
    root: yaml.Node,
    loader: yaml.SafeLoader,
    limits: YamlLimits,
    explicit_tag_starts: set[tuple[int, int]],
    representation_only: bool,
) -> _WalkResult:
    result = _WalkResult()
    try:
        _walk(
            root, 0, loader, limits, result, set(), set(), explicit_tag_starts, (),
            ScalarRole.ROOT, representation_only,
        )
    except RecursionError:
        raise _refuse(YamlFailure.TOO_DEEP, limits, limit=limits.max_depth) from None
    return result


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def _load_text(text: str, limits: YamlLimits, representation_only: bool) -> YamlDocument:
    _check_line_shape(text, limits)
    scan = _scan(text, limits)

    loader = yaml.SafeLoader(text)
    try:
        root = _compose_single(loader, limits)
        if root is None:
            return YamlDocument(
                value=None,
                features=YamlFeatures(scalar_styles=frozenset(scan.styles)),
                token_count=scan.tokens,
                value_constructed=not representation_only,
            )
        walk = _walk_graph(root, loader, limits, scan.explicit_tag_starts, representation_only)
        if representation_only:
            # Representation-only mode never constructs a value: the composed,
            # bounded, already-walked node graph is the complete result.
            value = None
        else:
            try:
                value = loader.construct_document(root)
            except RecursionError:
                raise _refuse(YamlFailure.TOO_DEEP, limits, limit=limits.max_depth) from None
            except yaml.YAMLError as exc:
                raise _invalid_yaml(exc, limits) from None
        features = YamlFeatures(
            has_anchors=scan.has_anchors,
            has_aliases=scan.aliases > 0,
            has_merge_keys=walk.has_merge_keys,
            has_flow_collections=scan.has_flow or walk.has_flow,
            has_explicit_tags=scan.has_explicit_tags,
            alias_references=scan.aliases,
            scalar_styles=frozenset(scan.styles),
        )
        return YamlDocument(
            value=value,
            features=features,
            scalars=tuple(walk.scalars),
            token_count=scan.tokens,
            node_count=walk.nodes,
            max_depth=walk.depth,
            mapping_pairs=walk.mapping_pairs,
            node_occurrences=tuple(walk.occurrences),
            value_constructed=not representation_only,
        )
    finally:
        loader.dispose()


def load_yaml_bytes(
    data: bytes,
    *,
    limits: YamlLimits = DEFAULT_YAML_LIMITS,
    representation_only: bool = False,
) -> YamlDocument:
    """Load one YAML document from raw bytes under finite bounds.

    The raw byte ceiling is applied *before* decoding, line splitting, scanning,
    composing, or constructing. UTF-8 is decoded strictly, exactly once. The input
    bytes are never mutated.

    With the default ``representation_only=False`` the accepted constructed-value
    behavior is unchanged. With ``representation_only=True`` the same single
    ``SafeLoader`` pipeline performs every existing bound and semantic-duplicate
    check once, composes and bounded-walks the node graph, and returns
    ``value=None`` and ``value_constructed=False``: it constructs **no document
    or value**. It still performs the one bounded per-scalar-key construction of
    :func:`_key_identity` for semantic-duplicate identity (a data-induced
    constructor failure there just leaves that key's identity unavailable, never
    a boundary refusal or a raised exception). Unknown, forbidden, or
    constructor-incompatible tags are recorded as representation evidence rather
    than refused. ``node_occurrences`` is the deterministic first-traversal node
    evidence with the bounded repeated-alias behavior documented on
    :class:`YamlNodeEvidence`.
    """

    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise _refuse(YamlFailure.INPUT_NOT_BYTES, limits)
    raw = bytes(data)
    if len(raw) > limits.max_bytes:
        raise _refuse(
            YamlFailure.INPUT_TOO_LARGE, limits, limit=limits.max_bytes, observed=len(raw)
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _refuse(YamlFailure.INVALID_UTF8, limits, observed=exc.start) from None
    return _load_text(text, limits, representation_only)


def load_yaml_path(path: Path, *, limits: YamlLimits = DEFAULT_YAML_LIMITS) -> YamlDocument:
    """Load one YAML document from a file under the same finite bounds.

    Opens once, reads at most ``max_bytes + 1`` bytes, and never rewrites,
    normalizes, or creates a file. The path never appears in a diagnostic.
    """

    try:
        with open(path, "rb") as handle:
            data = handle.read(limits.max_bytes + 1)
    except OSError as exc:
        raise _refuse(
            YamlFailure.READ_FAILED,
            limits,
            detail=_errno.errorcode.get(exc.errno or 0, "unknown"),
        ) from None
    return load_yaml_bytes(data, limits=limits)
