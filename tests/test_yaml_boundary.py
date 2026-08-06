"""Focused probes for the private bounded YAML boundary (M002-S02).

These tests exercise ``frutlups._yaml`` as the one place the product may turn
YAML bytes into a Python value. They cover benign inputs, every declared resource
dimension at its limit and at limit-plus-one, the semantic duplicate policy, tag
and document policy, bounded diagnostics, filesystem purity, and the absence of
any global loader mutation or public re-export.

Hostile and limit inputs are generated in memory; no large fixture is committed.

Where a literal maximum cannot be reached at the default limits without tripping
another dimension first, the test says so and isolates the dimension with a
purpose-built :class:`~frutlups._yaml.YamlLimits`. The default limits are never
weakened to make a test convenient.
"""

from __future__ import annotations

import copy
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from frutlups import _yaml
from frutlups._yaml import (
    DEFAULT_YAML_LIMITS as LIMITS,
)
from frutlups._yaml import (
    ScalarEvidence,
    ScalarRole,
    YamlBoundaryError,
    YamlFailure,
    YamlLimits,
    load_yaml_bytes,
    load_yaml_path,
)

_SRC_ROOT = Path(_yaml.__file__).resolve().parent


# ---------------------------------------------------------------------------
# Input builders (in memory; nothing is committed)
# ---------------------------------------------------------------------------


def _document_of_exact_size(total_bytes: int) -> str:
    """A 500-line, 500-pair mapping padded to exactly ``total_bytes`` bytes.

    Every other dimension stays inside its default limit, so the byte dimension
    is the one under test.
    """

    lines = LIMITS.max_lines
    prefixes = [f"k{index:03d}: " for index in range(lines)]
    fixed = sum(len(prefix) for prefix in prefixes) + (lines - 1)
    base, extra = divmod(total_bytes - fixed, lines)
    rendered = [
        prefix + "a" * (base + (extra if index == lines - 1 else 0))
        for index, prefix in enumerate(prefixes)
    ]
    text = "\n".join(rendered)
    assert len(text.encode("utf-8")) == total_bytes
    return text


def _document_of_exact_nodes(total_nodes: int) -> str:
    """A flow sequence whose unique node count is exactly ``total_nodes``.

    ``nodes = 1 sequence + scalars + 3 per single-pair mapping``.
    """

    mappings = (total_nodes - 1) // 3
    scalars = (total_nodes - 1) - 3 * mappings
    items = ["{a: 1}"] * mappings + ["z"] * scalars
    return "[" + ", ".join(items) + "]"


def _document_of_exact_scalar(length: int) -> str:
    """A block scalar whose constructed value is exactly ``length`` characters.

    Split across lines so the per-line limit is not the dimension under test.
    """

    per_line = 5_000
    whole, rest = divmod(length, per_line + 1)
    chunks = ["a" * per_line] * whole
    if rest:
        chunks.append("a" * rest)
    text = "k: |-\n" + "\n".join("  " + chunk for chunk in chunks)
    return text


def _nested_sequences(levels: int) -> str:
    return "[" * levels + "z" + "]" * levels


def _aliases(count: int) -> str:
    return "anchor: &x 1\nrefs: [" + ", ".join(["*x"] * count) + "]"


def _load(text: str | bytes, limits: YamlLimits = LIMITS):
    data = text if isinstance(text, bytes) else text.encode("utf-8")
    return load_yaml_bytes(data, limits=limits)


class _BoundaryTestCase(unittest.TestCase):
    def assertRefused(self, text, category: YamlFailure, *, limits: YamlLimits = LIMITS):
        with self.assertRaises(YamlBoundaryError) as caught:
            _load(text, limits)
        self.assertEqual(caught.exception.category, category)
        self.assertLessEqual(len(caught.exception.message), limits.max_diagnostic_length)
        return caught.exception


# ---------------------------------------------------------------------------
# Benign inputs and descriptive feature evidence
# ---------------------------------------------------------------------------


class BenignInputTests(_BoundaryTestCase):
    def test_benign_documents_load_to_expected_values(self) -> None:
        cases = [
            ("mapping", "a: 1\nb: two\n", {"a": 1, "b": "two"}),
            ("sequence", "- 1\n- 2\n", [1, 2]),
            ("block scalar", "k: |\n  one\n  two\n", {"k": "one\ntwo\n"}),
            ("flow collections", "m: {a: 1}\ns: [1, 2]\n", {"m": {"a": 1}, "s": [1, 2]}),
            ("safe explicit tag", "k: !!str 7\n", {"k": "7"}),
            ("safe binary tag", "k: !!binary aGk=\n", {"k": b"hi"}),
            ("explicit single document", "---\na: 1\n", {"a": 1}),
            ("empty input", "", None),
            ("comment only", "# nothing\n", None),
        ]
        for label, text, expected in cases:
            with self.subTest(case=label):
                self.assertEqual(_load(text).value, expected)

    def test_single_merge_keeps_ordinary_safeloader_semantics(self) -> None:
        document = _load("base: &b {a: 1, b: 2}\nuse:\n  <<: *b\n  b: 3\n")
        self.assertEqual(document.value["use"], {"a": 1, "b": 3})
        self.assertTrue(document.features.has_merge_keys)

    def test_bounded_shared_aliases_are_recorded_as_evidence(self) -> None:
        document = _load("base: &b {a: 1}\nx: *b\ny: *b\n")
        self.assertEqual(document.value["x"], {"a": 1})
        self.assertEqual(document.value["y"], {"a": 1})
        self.assertTrue(document.features.has_anchors)
        self.assertTrue(document.features.has_aliases)
        self.assertEqual(document.features.alias_references, 2)
        # The shared target is one node, counted once: the root mapping, its three
        # keys, the shared mapping, and that mapping's key and value make seven.
        self.assertEqual(document.node_count, 7)

    def test_feature_evidence_distinguishes_styles_and_shapes(self) -> None:
        document = _load("flow: {a: 1}\nblock: |\n  x\nquoted: 'y'\ntagged: !!str 7\n")
        features = document.features
        self.assertTrue(features.has_flow_collections)
        self.assertTrue(features.has_explicit_tags)
        self.assertFalse(features.has_aliases)
        self.assertFalse(features.has_merge_keys)
        self.assertEqual(features.scalar_styles, frozenset({"", "'", "|"}))

    def test_evidence_carries_no_verdict_fields(self) -> None:
        # The boundary is descriptive only: it must expose nothing that could be
        # mistaken for an OKF, profile, layout, routing, or authority result.
        document = _load("a: 1\n")
        forbidden = ("okf", "profile", "eligib", "authority", "valid", "reason", "verdict")
        names = set(vars(document)) | set(vars(document.features))
        for scalar in document.scalars:
            names |= set(vars(scalar))
        for name in names:
            with self.subTest(attribute=name):
                self.assertFalse([token for token in forbidden if token in name.lower()])


# ---------------------------------------------------------------------------
# Retained per-scalar representation evidence
# ---------------------------------------------------------------------------


class ScalarEvidenceTests(_BoundaryTestCase):
    """The original lexeme must survive, because the resolved value cannot carry it."""

    def _by_path(self, text: str) -> dict[tuple[tuple[str, int], ...], ScalarEvidence]:
        return {scalar.path: scalar for scalar in _load(text).scalars}

    def test_representation_pairs_that_construct_identically_stay_distinguishable(self) -> None:
        # Each pair constructs to the same Python value, so only the retained
        # lexeme, tag, or style can tell the two documents apart.
        cases = [
            ("boolean spelling", "k: true\n", "k: yes\n", True, "true", "yes"),
            ("integer spelling", "k: 1\n", "k: 01\n", 1, "1", "01"),
        ]
        for label, left_text, right_text, expected, left_lexeme, right_lexeme in cases:
            with self.subTest(case=label):
                left, right = _load(left_text), _load(right_text)
                # Ordinary SafeLoader output is unchanged on both sides.
                self.assertEqual(left.value, {"k": expected})
                self.assertEqual(right.value, {"k": expected})
                # The retained evidence is not.
                left_value = left.scalars[1]
                right_value = right.scalars[1]
                self.assertEqual(left_value.role, ScalarRole.MAPPING_VALUE)
                self.assertEqual(left_value.lexeme, left_lexeme)
                self.assertEqual(right_value.lexeme, right_lexeme)
                self.assertNotEqual(left.scalars, right.scalars)
                self.assertEqual(left_value.tag, right_value.tag)
                self.assertEqual(left_value.style, right_value.style)

    def test_plain_and_quoted_timestamps_are_distinguished_by_the_same_mechanism(self) -> None:
        plain = _load("k: 2026-08-03\n").scalars[1]
        quoted = _load("k: '2026-08-03'\n").scalars[1]
        self.assertEqual(plain.lexeme, quoted.lexeme)
        self.assertEqual(plain.tag, "tag:yaml.org,2002:timestamp")
        self.assertEqual(quoted.tag, "tag:yaml.org,2002:str")
        self.assertEqual(plain.style, "")
        self.assertEqual(quoted.style, "'")

    def test_structural_location_distinguishes_keys_values_and_items(self) -> None:
        text = "outer:\n  inner: 1\n  list:\n    - a\n    - b\n"
        evidence = self._by_path(text)
        expected = {
            (("key", 0),): ("outer", ScalarRole.MAPPING_KEY),
            (("value", 0), ("key", 0)): ("inner", ScalarRole.MAPPING_KEY),
            (("value", 0), ("value", 0)): ("1", ScalarRole.MAPPING_VALUE),
            (("value", 0), ("key", 1)): ("list", ScalarRole.MAPPING_KEY),
            (("value", 0), ("value", 1), ("item", 0)): ("a", ScalarRole.SEQUENCE_ITEM),
            (("value", 0), ("value", 1), ("item", 1)): ("b", ScalarRole.SEQUENCE_ITEM),
        }
        self.assertEqual(set(evidence), set(expected))
        for path, (lexeme, role) in expected.items():
            with self.subTest(path=path):
                self.assertEqual(evidence[path].lexeme, lexeme)
                self.assertEqual(evidence[path].role, role)

    def test_repeated_lexemes_at_different_locations_are_separate_records(self) -> None:
        # Location must be structural, not an encounter-order guess: the same
        # lexeme appears three times in three different roles.
        text = "x:\n  x: x\n"
        evidence = self._by_path(text)
        self.assertEqual(
            {path: record.role for path, record in evidence.items()},
            {
                (("key", 0),): ScalarRole.MAPPING_KEY,
                (("value", 0), ("key", 0)): ScalarRole.MAPPING_KEY,
                (("value", 0), ("value", 0)): ScalarRole.MAPPING_VALUE,
            },
        )
        self.assertTrue(all(record.lexeme == "x" for record in evidence.values()))

    def test_explicit_tags_are_recorded_per_scalar(self) -> None:
        document = _load("plain: 7\ntagged: !!str 7\n")
        plain = document.scalars[1]
        tagged = document.scalars[3]
        self.assertFalse(plain.explicit_tag)
        self.assertTrue(tagged.explicit_tag)
        self.assertEqual(plain.tag, "tag:yaml.org,2002:int")
        self.assertEqual(tagged.tag, "tag:yaml.org,2002:str")
        self.assertTrue(document.features.has_explicit_tags)

    def test_a_root_scalar_is_recorded_with_the_root_role(self) -> None:
        document = _load("just-a-scalar\n")
        self.assertEqual(len(document.scalars), 1)
        self.assertEqual(document.scalars[0].role, ScalarRole.ROOT)
        self.assertEqual(document.scalars[0].path, ())
        self.assertEqual(document.scalars[0].lexeme, "just-a-scalar")

    def test_evidence_is_immutable_and_exposes_no_live_loader_or_node(self) -> None:
        document = _load("a: 1\n")
        self.assertIsInstance(document.scalars, tuple)
        record = document.scalars[0]
        with self.assertRaises(Exception):
            record.lexeme = "mutated"  # type: ignore[misc]
        with self.assertRaises(Exception):
            document.scalars = ()  # type: ignore[misc]
        for value in vars(record).values():
            with self.subTest(value=type(value).__name__):
                self.assertNotIsInstance(value, (yaml.Node, yaml.SafeLoader))
        self.assertIsInstance(record.path, tuple)

    def test_scalar_locations_are_safe_one_based_numbers(self) -> None:
        document = _load("a: 1\nb: 2\n")
        self.assertEqual(
            [(record.line, record.column) for record in document.scalars],
            [(1, 1), (1, 4), (2, 1), (2, 4)],
        )

    def test_retained_lexemes_never_reach_a_diagnostic(self) -> None:
        secret = "SUPERSECRET"
        with self.assertRaises(YamlBoundaryError) as caught:
            _load(f"a: {secret}\na: 2\n")
        self.assertNotIn(secret, caught.exception.message)


class ExplicitTagEvidenceTests(_BoundaryTestCase):
    """``explicit_tag`` must not depend on the order of a node's properties.

    YAML lets an anchor and a tag appear in either order, and the node's start
    mark is the start of whichever comes first. These cases pin the association
    to the property *group*, so both legal orders are recorded identically.
    """

    def _explicit_by_path(self, text: str) -> dict[tuple[tuple[str, int], ...], bool]:
        return {record.path: record.explicit_tag for record in _load(text).scalars}

    def test_explicit_tag_is_independent_of_anchor_and_tag_order(self) -> None:
        value = (("value", 0),)
        cases = [
            # The reviewer-shaped counterexample is the second row: before this
            # correction, an anchor written before the tag was recorded as
            # non-explicit because the node began at the anchor token.
            ("tag before anchor", "k: !!str &a value\n", True),
            ("anchor before tag", "k: &a !!str value\n", True),
            ("verbatim tag before anchor", "k: !<tag:yaml.org,2002:str> &a value\n", True),
            ("verbatim anchor before tag", "k: &a !<tag:yaml.org,2002:str> value\n", True),
            ("tag with no anchor", "k: !!str value\n", True),
            ("anchor with no tag", "k: &a value\n", False),
            ("no properties at all", "k: value\n", False),
            ("tagged quoted scalar", "k: !!str 'value'\n", True),
            ("tagged block scalar", "k: !!str |-\n  value\n", True),
        ]
        for label, text, expected in cases:
            with self.subTest(case=label):
                self.assertEqual(self._explicit_by_path(text)[value], expected)
                # The aggregate must agree, but it is not what is under test here.
                self.assertEqual(_load(text).features.has_explicit_tags, expected)

    def test_explicit_tag_is_recorded_in_every_collection_shape(self) -> None:
        cases = [
            (
                "tagged key, both orders",
                "!!str k: 1\n",
                {(("key", 0),): True, (("value", 0),): False},
            ),
            (
                "tagged key with anchor first",
                "&a !!str k: 1\n",
                {(("key", 0),): True, (("value", 0),): False},
            ),
            (
                "block sequence",
                "- !!str &x a\n- &y !!str b\n- c\n",
                {(("item", 0),): True, (("item", 1),): True, (("item", 2),): False},
            ),
            (
                "flow sequence",
                "k: [!!str &x a, &y !!str b, c]\n",
                {
                    (("value", 0), ("item", 0)): True,
                    (("value", 0), ("item", 1)): True,
                    (("value", 0), ("item", 2)): False,
                },
            ),
            (
                "flow mapping",
                "k: {a: !!str &x 1, b: &y !!str 2, c: 3}\n",
                {
                    (("value", 0), ("value", 0)): True,
                    (("value", 0), ("value", 1)): True,
                    (("value", 0), ("value", 2)): False,
                },
            ),
            (
                "nested block mapping",
                "outer:\n  inner: &a !!str 1\n  other: 1\n",
                {
                    (("value", 0), ("value", 0)): True,
                    (("value", 0), ("value", 1)): False,
                },
            ),
        ]
        for label, text, expected in cases:
            with self.subTest(case=label):
                observed = self._explicit_by_path(text)
                for path, flag in expected.items():
                    with self.subTest(path=path):
                        self.assertEqual(observed[path], flag)

    def test_siblings_resolving_to_the_same_tag_are_still_distinguished(self) -> None:
        # Explicitness must come from what was written, never from the resolved
        # tag: each pair below resolves identically.
        cases = [
            ("integer", "tagged: !!int 7\nplain: 7\n", "tag:yaml.org,2002:int"),
            ("string", "tagged: !!str hello\nplain: hello\n", "tag:yaml.org,2002:str"),
        ]
        for label, text, tag in cases:
            with self.subTest(case=label):
                records = {record.path: record for record in _load(text).scalars}
                tagged = records[(("value", 0),)]
                plain = records[(("value", 1),)]
                self.assertEqual(tagged.tag, tag)
                self.assertEqual(plain.tag, tag)
                self.assertEqual(tagged.lexeme, plain.lexeme)
                self.assertTrue(tagged.explicit_tag)
                self.assertFalse(plain.explicit_tag)

    def test_alias_to_an_anchored_tagged_scalar_keeps_the_record_once_rule(self) -> None:
        document = _load("anchor: &a !!str 7\nuse: *a\nplain: 7\n")
        self.assertEqual(document.value, {"anchor": "7", "use": "7", "plain": 7})
        self.assertEqual(document.features.alias_references, 1)
        records = {record.path: record for record in document.scalars}
        self.assertTrue(records[(("value", 0),)].explicit_tag)
        self.assertFalse(records[(("value", 2),)].explicit_tag)
        # The alias position is not a second record: the declared rule is that a
        # scalar reached through an alias is recorded once, where it is defined.
        self.assertNotIn((("value", 1),), records)


# ---------------------------------------------------------------------------
# Every declared resource dimension, at the limit and at limit-plus-one
# ---------------------------------------------------------------------------


class ResourceLimitTests(_BoundaryTestCase):
    def test_each_dimension_passes_at_limit_and_refuses_at_limit_plus_one(self) -> None:
        cases = [
            (
                "raw bytes",
                _document_of_exact_size(LIMITS.max_bytes),
                _document_of_exact_size(LIMITS.max_bytes + 1),
                YamlFailure.INPUT_TOO_LARGE,
            ),
            (
                "lines",
                "\n".join(f"k{i}: {i}" for i in range(LIMITS.max_lines)),
                "\n".join(f"k{i}: {i}" for i in range(LIMITS.max_lines + 1)),
                YamlFailure.TOO_MANY_LINES,
            ),
            (
                "line length",
                "k: " + "a" * (LIMITS.max_line_length - 3),
                "k: " + "a" * (LIMITS.max_line_length - 2),
                YamlFailure.LINE_TOO_LONG,
            ),
            (
                "nodes",
                _document_of_exact_nodes(LIMITS.max_nodes),
                _document_of_exact_nodes(LIMITS.max_nodes + 1),
                YamlFailure.TOO_MANY_NODES,
            ),
            (
                "depth",
                _nested_sequences(LIMITS.max_depth),
                _nested_sequences(LIMITS.max_depth + 1),
                YamlFailure.TOO_DEEP,
            ),
            (
                "scalar length",
                _document_of_exact_scalar(LIMITS.max_scalar_length),
                _document_of_exact_scalar(LIMITS.max_scalar_length + 1),
                YamlFailure.SCALAR_TOO_LONG,
            ),
            (
                "mapping pairs",
                "{" + ", ".join(f"k{i}: {i}" for i in range(LIMITS.max_mapping_pairs)) + "}",
                "{" + ", ".join(f"k{i}: {i}" for i in range(LIMITS.max_mapping_pairs + 1)) + "}",
                YamlFailure.MAPPING_TOO_LARGE,
            ),
            (
                "sequence items",
                "[" + ", ".join(str(i) for i in range(LIMITS.max_sequence_items)) + "]",
                "[" + ", ".join(str(i) for i in range(LIMITS.max_sequence_items + 1)) + "]",
                YamlFailure.SEQUENCE_TOO_LARGE,
            ),
            (
                "alias references",
                _aliases(LIMITS.max_aliases),
                _aliases(LIMITS.max_aliases + 1),
                YamlFailure.TOO_MANY_ALIASES,
            ),
        ]
        for label, at_limit, over_limit, category in cases:
            with self.subTest(dimension=label, case="at limit"):
                _load(at_limit)  # must not raise
            with self.subTest(dimension=label, case="limit + 1"):
                self.assertRefused(over_limit, category)

    def test_token_dimension_isolated_because_defaults_cannot_reach_it(self) -> None:
        # A document with 10,000 scanned tokens is unreachable under the default
        # node, mapping, and sequence ceilings, so the token dimension is isolated
        # with a purpose-built limit instead of weakening any default.
        text = "[" + ", ".join(str(i) for i in range(200)) + "]"
        observed = _load(text).token_count
        self.assertLess(observed, LIMITS.max_tokens)
        _load(text, YamlLimits(max_tokens=observed))  # exactly at the limit: passes
        self.assertRefused(
            text, YamlFailure.TOO_MANY_TOKENS, limits=YamlLimits(max_tokens=observed - 1)
        )

    def test_defaults_match_the_declared_limit_table(self) -> None:
        self.assertEqual(
            (
                LIMITS.max_bytes,
                LIMITS.max_lines,
                LIMITS.max_line_length,
                LIMITS.max_tokens,
                LIMITS.max_nodes,
                LIMITS.max_depth,
                LIMITS.max_scalar_length,
                LIMITS.max_mapping_pairs,
                LIMITS.max_sequence_items,
                LIMITS.max_aliases,
                LIMITS.max_diagnostic_length,
            ),
            (65_536, 500, 8_192, 10_000, 2_000, 32, 16_384, 500, 1_000, 50, 240),
        )


class RecursionAndCycleTests(_BoundaryTestCase):
    def test_deep_nesting_is_refused_before_composition_can_recurse(self) -> None:
        # Far beyond the interpreter recursion limit, and short enough per line
        # that the line-length guard is not what fires.
        levels = 2_000
        self.assertLess(len(_nested_sequences(levels)), LIMITS.max_line_length)
        self.assertGreater(levels, sys.getrecursionlimit())
        before = sys.getrecursionlimit()
        self.assertRefused(_nested_sequences(levels), YamlFailure.TOO_DEEP)
        self.assertEqual(sys.getrecursionlimit(), before)

    def test_alias_cycle_is_refused_deterministically(self) -> None:
        for label, text in (
            ("self-referential sequence", "a: &x [*x]\n"),
            ("self-referential mapping", "a: &x {k: *x}\n"),
        ):
            with self.subTest(case=label):
                self.assertRefused(text, YamlFailure.ALIAS_CYCLE)


# ---------------------------------------------------------------------------
# Duplicate-key policy, applied before dictionary collapse
# ---------------------------------------------------------------------------


class DuplicateKeyTests(_BoundaryTestCase):
    def test_textual_and_semantic_duplicates_are_refused(self) -> None:
        cases = [
            ("textual at root", "a: 1\na: 2\n"),
            ("textual nested", "outer:\n  b: 1\n  b: 2\n"),
            ("integer 1 versus 01", "1: x\n01: y\n"),
            ("boolean yes versus on", "yes: x\non: y\n"),
            ("boolean true versus True", "true: x\nTrue: y\n"),
            ("null versus tilde", "null: x\n~: y\n"),
            ("nested semantic duplicate", "outer:\n  1: x\n  01: y\n"),
        ]
        for label, text in cases:
            with self.subTest(case=label):
                self.assertRefused(text, YamlFailure.DUPLICATE_KEY)

    def test_two_source_merge_keys_are_duplicates(self) -> None:
        text = "b1: &b1 {a: 1}\nb2: &b2 {c: 2}\nuse:\n  <<: *b1\n  <<: *b2\n"
        self.assertRefused(text, YamlFailure.DUPLICATE_KEY)

    def test_quoted_keys_stay_distinct_from_typed_scalars(self) -> None:
        cases = [
            ("quoted 1 versus integer 1", "1: x\n'1': y\n", {1: "x", "1": "y"}),
            ("quoted null versus tilde", "~: x\n'null': y\n", {None: "x", "null": "y"}),
            ("quoted merge marker", "use:\n  '<<': 1\n  other: 2\n", {"use": {"<<": 1, "other": 2}}),
        ]
        for label, text, expected in cases:
            with self.subTest(case=label):
                self.assertEqual(_load(text).value, expected)


# ---------------------------------------------------------------------------
# Encoding, document, and tag policy
# ---------------------------------------------------------------------------


class InputPolicyTests(_BoundaryTestCase):
    def test_invalid_utf8_is_refused_before_any_yaml_work(self) -> None:
        self.assertRefused(b"k: \xff\xfe\n", YamlFailure.INVALID_UTF8)

    def test_malformed_and_multi_document_inputs_are_refused(self) -> None:
        cases = [
            ("truncated flow", "k: [1, 2\n", YamlFailure.INVALID_YAML),
            ("bad indentation", "a: 1\n  b: 2\n", YamlFailure.INVALID_YAML),
            ("tab indentation", "a:\n\tb: 1\n", YamlFailure.INVALID_YAML),
            ("undefined alias", "k: *missing\n", YamlFailure.INVALID_YAML),
            ("two documents", "a: 1\n---\nb: 2\n", YamlFailure.MULTIPLE_DOCUMENTS),
            ("three documents", "a: 1\n---\nb: 2\n---\nc: 3\n", YamlFailure.MULTIPLE_DOCUMENTS),
        ]
        for label, text, category in cases:
            with self.subTest(case=label):
                self.assertRefused(text, category)

    def test_unsupported_and_object_tags_are_refused(self) -> None:
        cases = [
            ("unknown local tag", "k: !frobnicate 1\n"),
            ("python object apply", "k: !!python/object/apply:os.system ['echo hi']\n"),
            ("python name", "k: !!python/name:os.system\n"),
            ("python module", "k: !!python/module:os\n"),
            ("unknown global tag", "k: !<tag:example.com,2026:thing> 1\n"),
        ]
        for label, text in cases:
            with self.subTest(case=label):
                self.assertRefused(text, YamlFailure.UNSUPPORTED_TAG)

    def test_non_bytes_input_is_refused_by_the_boundary(self) -> None:
        with self.assertRaises(YamlBoundaryError) as caught:
            load_yaml_bytes("a: 1")  # type: ignore[arg-type]
        self.assertEqual(caught.exception.category, YamlFailure.INPUT_NOT_BYTES)


# ---------------------------------------------------------------------------
# Bounded, path-safe, hostile-echo-free diagnostics
# ---------------------------------------------------------------------------


class DiagnosticTests(_BoundaryTestCase):
    def test_diagnostics_are_bounded_and_never_echo_hostile_content(self) -> None:
        secret = "SUPERSECRET"
        machine_path = "C:\\Users\\someone\\private\\notes.txt"
        cases = [
            ("long hostile scalar", "k: " + secret * 2_000 + "\n"),
            ("hostile duplicate key", secret * 20 + ": 1\n" + secret * 20 + ": 2\n"),
            ("machine-shaped path in a duplicate", f'a: "{machine_path}"\na: 2\n'),
            ("machine-shaped path in a bad tag", f'k: !frobnicate "{machine_path}"\n'),
            ("hostile scalar in malformed yaml", "k: [" + secret * 10 + "\n"),
        ]
        for label, text in cases:
            with self.subTest(case=label):
                with self.assertRaises(YamlBoundaryError) as caught:
                    _load(text)
                message = caught.exception.message
                self.assertLessEqual(len(message), LIMITS.max_diagnostic_length)
                self.assertNotIn(secret, message)
                self.assertNotIn("Users", message)
                self.assertNotIn("notes.txt", message)
                self.assertNotIn("Traceback", message)
                self.assertNotIn("\n", message)
                self.assertTrue(message.startswith("yaml boundary refused: "))

    def test_diagnostics_are_deterministic_for_the_same_input(self) -> None:
        text = "a: 1\na: 2\n"
        first = self.assertRefused(text, YamlFailure.DUPLICATE_KEY)
        second = self.assertRefused(text, YamlFailure.DUPLICATE_KEY)
        self.assertEqual(first.message, second.message)

    def test_every_configured_cap_is_honored_including_the_small_boundary(self) -> None:
        # The cap is a hard ceiling at every accepted value: an ellipsis is a
        # courtesy that is dropped whenever it would not fit.
        text = "k: " + "a" * 9_000
        for cap in (0, 1, 2, 3, 4, 5, 30, 240):
            with self.subTest(cap=cap):
                limits = YamlLimits(max_diagnostic_length=cap)
                with self.assertRaises(YamlBoundaryError) as caught:
                    _load(text, limits)
                error = caught.exception
                self.assertLessEqual(len(error.message), cap)
                # The stable category stays available whatever the message length.
                self.assertEqual(error.category, YamlFailure.LINE_TOO_LONG)

    def test_a_negative_cap_deterministically_yields_an_empty_message(self) -> None:
        # Declared behavior: a negative cap clamps to zero. A refusal must not be
        # replaced by an unrelated error while its own diagnostic is rendered.
        for cap in (-1, -240):
            with self.subTest(cap=cap):
                limits = YamlLimits(max_diagnostic_length=cap)
                with self.assertRaises(YamlBoundaryError) as caught:
                    _load("a: 1\na: 2\n", limits)
                self.assertEqual(caught.exception.message, "")
                self.assertEqual(caught.exception.category, YamlFailure.DUPLICATE_KEY)

    def test_a_cap_wide_enough_keeps_the_full_message(self) -> None:
        error = self.assertRefused("k: " + "a" * 9_000, YamlFailure.LINE_TOO_LONG)
        self.assertLess(len(error.message), LIMITS.max_diagnostic_length)
        self.assertNotIn("...", error.message)


# ---------------------------------------------------------------------------
# Filesystem and input purity
# ---------------------------------------------------------------------------


class PurityTests(_BoundaryTestCase):
    def test_input_bytes_are_not_mutated_and_no_file_is_written(self) -> None:
        payload = b"a: 1\nb: [1, 2]\n"
        snapshot = bytes(payload)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = sorted(entry.name for entry in root.iterdir())
            load_yaml_bytes(payload)
            with self.assertRaises(YamlBoundaryError):
                _load("a: 1\na: 2\n")
            self.assertEqual(payload, snapshot)
            self.assertEqual(sorted(entry.name for entry in root.iterdir()), before)

    def test_load_yaml_path_reads_once_and_changes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "config.yaml"
            original = b"a: 1\nb: two\n"
            target.write_bytes(original)
            before_listing = sorted(entry.name for entry in root.iterdir())

            self.assertEqual(load_yaml_path(target).value, {"a": 1, "b": "two"})

            self.assertEqual(target.read_bytes(), original)
            self.assertEqual(sorted(entry.name for entry in root.iterdir()), before_listing)

    def test_missing_and_unreadable_paths_refuse_without_creating_anything(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / "a_directory"
            directory.mkdir()
            before = sorted(entry.name for entry in root.iterdir())
            for label, candidate in (
                ("missing file", root / "absent.yaml"),
                ("directory", directory),
            ):
                with self.subTest(case=label):
                    with self.assertRaises(YamlBoundaryError) as caught:
                        load_yaml_path(candidate)
                    message = caught.exception.message
                    self.assertEqual(caught.exception.category, YamlFailure.READ_FAILED)
                    self.assertLessEqual(len(message), LIMITS.max_diagnostic_length)
                    self.assertNotIn(candidate.name, message)
                    self.assertNotIn(tmp, message)
            self.assertEqual(sorted(entry.name for entry in root.iterdir()), before)

    def test_oversize_file_is_refused_before_decoding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "big.yaml"
            target.write_bytes(b"k: " + b"a" * (LIMITS.max_bytes + 10))
            with self.assertRaises(YamlBoundaryError) as caught:
                load_yaml_path(target)
            self.assertEqual(caught.exception.category, YamlFailure.INPUT_TOO_LARGE)


# ---------------------------------------------------------------------------
# Global state, public surface, and single-loader boundary
# ---------------------------------------------------------------------------


class BoundaryIsolationTests(_BoundaryTestCase):
    def test_calls_do_not_mutate_global_loader_state_or_recursion_limit(self) -> None:
        before = (
            dict(yaml.SafeLoader.yaml_constructors),
            dict(yaml.SafeLoader.yaml_multi_constructors),
            copy.deepcopy(yaml.SafeLoader.yaml_implicit_resolvers),
            sys.getrecursionlimit(),
        )
        _load("a: 1\nb: &x [1]\nc: *x\n")
        with self.assertRaises(YamlBoundaryError):
            _load("k: !!python/object/apply:os.system ['x']\n")
        with self.assertRaises(YamlBoundaryError):
            _load(_nested_sequences(2_000))
        after = (
            dict(yaml.SafeLoader.yaml_constructors),
            dict(yaml.SafeLoader.yaml_multi_constructors),
            copy.deepcopy(yaml.SafeLoader.yaml_implicit_resolvers),
            sys.getrecursionlimit(),
        )
        self.assertEqual(before, after)

    def test_importing_the_boundary_does_not_mutate_global_loader_state(self) -> None:
        program = (
            "import copy, json, sys, yaml\n"
            "before = (sorted(map(str, yaml.SafeLoader.yaml_constructors)),"
            " sorted(map(str, yaml.SafeLoader.yaml_multi_constructors)),"
            " copy.deepcopy(yaml.SafeLoader.yaml_implicit_resolvers), sys.getrecursionlimit())\n"
            "import frutlups._yaml\n"
            "after = (sorted(map(str, yaml.SafeLoader.yaml_constructors)),"
            " sorted(map(str, yaml.SafeLoader.yaml_multi_constructors)),"
            " copy.deepcopy(yaml.SafeLoader.yaml_implicit_resolvers), sys.getrecursionlimit())\n"
            "print(json.dumps(before == after))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", program], capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "true")

    def test_the_boundary_is_private_and_not_re_exported(self) -> None:
        import frutlups

        self.assertEqual(len(frutlups.__all__), 147)  # 142 + 5 M004 okf-profile observation exports (02_analysis/m004_okf_profile_observation_compatibility_record.md)
        self.assertNotIn("_yaml", frutlups.__all__)
        for name in _yaml.__all__:
            with self.subTest(name=name):
                self.assertNotIn(name, frutlups.__all__)
        # Word-bounded so the unrelated public name ``parse_simple_yaml`` does not
        # look like an import of this private module.
        package_init = (_SRC_ROOT / "__init__.py").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\b_yaml\b", package_init))

    def test_no_product_module_uses_another_yaml_loader(self) -> None:
        forbidden = (
            "yaml.Loader",
            "yaml.UnsafeLoader",
            "yaml.FullLoader",
            "yaml.CLoader",
            "yaml.CSafeLoader",
            "yaml.load(",
            "yaml.unsafe_load",
            "yaml.full_load",
            "add_constructor",
            "add_multi_constructor",
            "add_implicit_resolver",
            "setrecursionlimit",
        )
        importers = []
        for module in sorted(_SRC_ROOT.glob("*.py")):
            source = module.read_text(encoding="utf-8")
            with self.subTest(module=module.name):
                for token in forbidden:
                    # assertFalse, not assertNotIn: a failure must name the module
                    # and token without dumping the whole module source.
                    self.assertFalse(token in source, f"{module.name} uses {token}")
            if "import yaml" in source:
                importers.append(module.name)
        self.assertEqual(importers, ["_yaml.py"])


# ---------------------------------------------------------------------------
# Representation-only mode (M004 correction: the private pre-construction seam)
# ---------------------------------------------------------------------------


class RepresentationOnlyModeTests(unittest.TestCase):
    """The narrow private representation-only mode of ``load_yaml_bytes``.

    Default constructed-mode behavior is pinned by every other test in this
    module; these tests prove the mode separation: representation-only
    constructs no document/value, tolerates every tag as representation
    evidence, contains data-induced scalar-key construction failures as an
    unavailable identity (while default mode keeps surfacing them), keeps every
    bound and refusal category, and records first-traversal node occurrences
    with a repeated alias target recorded once more at the alias path (its
    descendants are not replayed).
    """

    @staticmethod
    def _rep(text: str, **kwargs):
        return load_yaml_bytes(
            text.encode("utf-8"), representation_only=True, **kwargs
        )

    def test_default_mode_is_constructed_with_no_occurrences(self) -> None:
        document = load_yaml_bytes(b"a: 1")
        self.assertTrue(document.value_constructed)
        self.assertEqual(document.value, {"a": 1})
        self.assertEqual(document.node_occurrences, ())

    def test_representation_mode_returns_no_value_and_full_occurrences(self) -> None:
        from frutlups._yaml import YamlNodeEvidence, YamlNodeKind

        document = self._rep("a: 1\nb:\n  - x")
        self.assertFalse(document.value_constructed)
        self.assertIsNone(document.value)
        expected = (
            YamlNodeEvidence(
                kind=YamlNodeKind.MAPPING, tag="tag:yaml.org,2002:map", lexeme=None,
                style="", role=ScalarRole.ROOT, path=(),
            ),
            YamlNodeEvidence(
                kind=YamlNodeKind.SCALAR, tag="tag:yaml.org,2002:str", lexeme="a",
                style="", role=ScalarRole.MAPPING_KEY, path=(("key", 0),),
            ),
            YamlNodeEvidence(
                kind=YamlNodeKind.SCALAR, tag="tag:yaml.org,2002:int", lexeme="1",
                style="", role=ScalarRole.MAPPING_VALUE, path=(("value", 0),),
            ),
            YamlNodeEvidence(
                kind=YamlNodeKind.SCALAR, tag="tag:yaml.org,2002:str", lexeme="b",
                style="", role=ScalarRole.MAPPING_KEY, path=(("key", 1),),
            ),
            YamlNodeEvidence(
                kind=YamlNodeKind.SEQUENCE, tag="tag:yaml.org,2002:seq", lexeme=None,
                style="", role=ScalarRole.MAPPING_VALUE, path=(("value", 1),),
            ),
            YamlNodeEvidence(
                kind=YamlNodeKind.SCALAR, tag="tag:yaml.org,2002:str", lexeme="x",
                style="", role=ScalarRole.SEQUENCE_ITEM, path=(("value", 1), ("item", 0)),
            ),
        )
        self.assertEqual(document.node_occurrences, expected)

    def test_alias_occurrence_is_recorded_at_the_alias_path(self) -> None:
        from frutlups._yaml import YamlNodeKind

        document = self._rep("x: &a null\ntype: *a")
        occurrences = {occ.path: occ for occ in document.node_occurrences}
        original = occurrences[(("value", 0),)]
        aliased = occurrences[(("value", 1),)]
        self.assertEqual(original.lexeme, "null")
        self.assertEqual(aliased.lexeme, "null")
        self.assertEqual(aliased.kind, YamlNodeKind.SCALAR)
        self.assertEqual(aliased.role, ScalarRole.MAPPING_VALUE)
        # The shared target stays a single unique node for the resource count:
        # root + two key scalars + one shared value scalar = 4 unique nodes.
        self.assertEqual(document.node_count, 4)
        self.assertEqual(document.features.alias_references, 1)

    def test_repeated_alias_to_container_records_container_not_descendants(self) -> None:
        # The bounded repeated-alias behavior: a repeated alias to a mapping is
        # recorded once at the alias path, but the mapping's descendants are
        # NOT replayed at descendant alias-relative paths. This pins the actual
        # (limited) behavior so the "complete per-occurrence" overclaim cannot
        # silently return.
        from frutlups._yaml import YamlNodeKind

        document = self._rep("base: &a\n  inner: 1\ncopy: *a\n")
        alias_path = (("value", 1),)
        occ_by_path = {occ.path: occ for occ in document.node_occurrences}
        self.assertIn(alias_path, occ_by_path)
        self.assertEqual(occ_by_path[alias_path].kind, YamlNodeKind.MAPPING)
        # No descendant of the repeated container is replayed under the alias.
        descendant_replays = [
            occ
            for occ in document.node_occurrences
            if len(occ.path) > 1 and occ.path[0] == alias_path[0]
        ]
        self.assertEqual(descendant_replays, [])

    def test_unknown_and_forbidden_tags_are_evidence_not_refusals(self) -> None:
        cases = [
            ("unknown local tag", "k: !frobnicate 1", "!frobnicate"),
            ("unknown global tag", "k: !<tag:example.com,2026:thing> 1", "tag:example.com,2026:thing"),
            (
                "forbidden object tag",
                "k: !!python/object/apply:os.system ['x']",
                "tag:yaml.org,2002:python/object/apply:os.system",
            ),
            ("failed int construction", "k: !!int abc", "tag:yaml.org,2002:int"),
        ]
        for label, text, expected_tag in cases:
            with self.subTest(case=label):
                # Default constructed mode refuses or fails these inputs...
                with self.assertRaises(Exception):
                    load_yaml_bytes(text.encode("utf-8"))
                # ...representation-only mode records them as evidence.
                document = self._rep(text)
                tags = {occ.tag for occ in document.node_occurrences}
                self.assertIn(expected_tag, tags)

    def test_construction_is_never_called_in_representation_mode(self) -> None:
        with mock.patch.object(
            yaml.SafeLoader,
            "construct_document",
            side_effect=AssertionError("construct_document reached"),
        ):
            document = self._rep("a: 1")
        self.assertIsNone(document.value)
        with mock.patch.object(
            yaml.SafeLoader,
            "construct_document",
            side_effect=AssertionError("construct_document reached"),
        ):
            with self.assertRaises(AssertionError):
                load_yaml_bytes(b"a: 1")

    def test_refusal_categories_are_identical_in_representation_mode(self) -> None:
        cases = [
            ("malformed", 'k: "bad" junk"', YamlFailure.INVALID_YAML),
            ("multiple documents", "a: 1\n...\n--- {b: 2}", YamlFailure.MULTIPLE_DOCUMENTS),
            ("semantic duplicate", "1: x\n01: y", YamlFailure.DUPLICATE_KEY),
            ("alias cycle", "a: &x\n  b: *x", YamlFailure.ALIAS_CYCLE),
            ("scalar too long", "k: " + "a" * 6000 + "\n  " + "b" * 6000 + "\n  " + "c" * 6000, YamlFailure.SCALAR_TOO_LONG),
            ("too many aliases", "a: &x v\nk: [" + ", ".join(["*x"] * 51) + "]", YamlFailure.TOO_MANY_ALIASES),
        ]
        for label, text, category in cases:
            with self.subTest(case=label):
                with self.assertRaises(YamlBoundaryError) as caught:
                    self._rep(text)
                self.assertEqual(caught.exception.category, category)

    def test_representation_mode_is_deterministic_and_mutates_no_global(self) -> None:
        before = (
            dict(yaml.SafeLoader.yaml_constructors),
            dict(yaml.SafeLoader.yaml_multi_constructors),
            sys.getrecursionlimit(),
        )
        first = self._rep("k: !frobnicate 1\nx: &a v\ny: *a")
        second = self._rep("k: !frobnicate 1\nx: &a v\ny: *a")
        self.assertEqual(first, second)
        after = (
            dict(yaml.SafeLoader.yaml_constructors),
            dict(yaml.SafeLoader.yaml_multi_constructors),
            sys.getrecursionlimit(),
        )
        self.assertEqual(before, after)

    def test_existing_scalar_evidence_and_features_are_unchanged_by_mode(self) -> None:
        text = "a: &x 'v'\nb: *x\nc: [1, 2]"
        default_doc = load_yaml_bytes(text.encode("utf-8"))
        rep_doc = self._rep(text)
        self.assertEqual(default_doc.features, rep_doc.features)
        self.assertEqual(default_doc.scalars, rep_doc.scalars)
        self.assertEqual(default_doc.token_count, rep_doc.token_count)
        self.assertEqual(default_doc.node_count, rep_doc.node_count)

    # -- Data-induced scalar-key construction exceptions (M004 correction 035) --

    _INVALID_KEY_CASES = (
        ("invalid bool key", "? !!bool abc\n: value\n", "abc"),
        ("malformed timestamp key", "? !!timestamp abc\n: value\n", "abc"),
        ("empty int key", '? !!int ""\n: value\n', ""),
        ("sign-only int key", '? !!int "-"\n: value\n', "-"),
        ("empty float key", '? !!float ""\n: value\n', ""),
    )

    def test_invalid_scalar_key_is_contained_as_unavailable_identity(self) -> None:
        # Representation-only mode does not leak PyYAML's data-induced scalar-key
        # construction exception: it composes and records the key occurrence and
        # returns, leaving that key's semantic identity simply unavailable.
        for label, text, lexeme in self._INVALID_KEY_CASES:
            with self.subTest(case=label):
                document = self._rep(text)
                self.assertFalse(document.value_constructed)
                key_occurrences = [
                    occ
                    for occ in document.node_occurrences
                    if occ.role is ScalarRole.MAPPING_KEY and occ.lexeme == lexeme
                ]
                self.assertEqual(len(key_occurrences), 1)
                self.assertTrue(document.features.has_explicit_tags)

    def test_default_mode_still_surfaces_the_data_induced_key_exception(self) -> None:
        # Default constructed mode is unchanged by this correction: the same
        # invalid explicit-tag scalar key still raises the raw PyYAML exception,
        # exactly as accepted at M002 -- KeyError for a bad !!bool, AttributeError
        # for a bad !!timestamp, IndexError for an empty/sign-only !!int/!!float.
        expected = {
            "invalid bool key": KeyError,
            "malformed timestamp key": AttributeError,
            "empty int key": IndexError,
            "sign-only int key": IndexError,
            "empty float key": IndexError,
        }
        for label, text, _ in self._INVALID_KEY_CASES:
            with self.subTest(case=label):
                with self.assertRaises(expected[label]):
                    load_yaml_bytes(text.encode("utf-8"))

    def test_invalid_scalar_key_never_manufactures_a_duplicate(self) -> None:
        # Two distinct invalid bool keys have no available identity, so they are
        # not a semantic duplicate; representation mode returns without refusing.
        document = self._rep("? !!bool abc\n: v\n? !!bool def\n: w\n")
        self.assertFalse(document.value_constructed)

    def test_representation_mode_still_refuses_a_real_duplicate_beside_invalid_key(
        self,
    ) -> None:
        # An unavailable invalid-key identity does not disturb real duplicate
        # detection: a genuine semantic duplicate still refuses in representation
        # mode, in either relative order.
        for text in ("? !!bool abc\n: v\na: 1\na: 2\n", "a: 1\na: 2\n? !!bool abc\n: v\n"):
            with self.subTest(text=text):
                with self.assertRaises(YamlBoundaryError) as caught:
                    load_yaml_bytes(text.encode("utf-8"), representation_only=True)
                self.assertEqual(caught.exception.category, YamlFailure.DUPLICATE_KEY)

    def test_default_mode_duplicate_beside_invalid_key_is_unchanged(self) -> None:
        # Default mode is unchanged: whichever the walk reaches first governs.
        # An invalid explicit-tag key encountered first raises its raw
        # construction exception; a real duplicate reached first refuses.
        with self.assertRaises(KeyError):
            load_yaml_bytes(b"? !!bool abc\n: v\na: 1\na: 2\n")
        with self.assertRaises(YamlBoundaryError) as caught:
            load_yaml_bytes(b"a: 1\na: 2\n? !!bool abc\n: v\n")
        self.assertEqual(caught.exception.category, YamlFailure.DUPLICATE_KEY)

    def test_injected_key_identity_programming_error_propagates(self) -> None:
        # A non-allowlisted programming failure at the scalar-key identity seam
        # is never absorbed by the data-exception allowlist, in either mode.
        import frutlups._yaml as yaml_module

        def boom(loader, key_node, representation_only):  # noqa: ARG001
            raise RuntimeError("injected")

        with mock.patch.object(yaml_module, "_key_identity", boom):
            for mode in (False, True):
                with self.subTest(representation_only=mode):
                    with self.assertRaises(RuntimeError):
                        load_yaml_bytes(b"a: 1\nb: 2\n", representation_only=mode)

    def test_allowlisted_exception_types_are_origin_agnostic(self) -> None:
        # The private boundary deliberately classifies by exception type, not by
        # traceback or message. Representation mode therefore contains a
        # reviewer/programmer-originated exception of an allowlisted type at the
        # exact construct_object seam, while default mode still propagates it.
        for exception_type in (KeyError, AttributeError, IndexError):
            with self.subTest(exception_type=exception_type.__name__):
                with mock.patch.object(
                    yaml.SafeLoader,
                    "construct_object",
                    side_effect=exception_type("reviewer programming defect"),
                ):
                    document = load_yaml_bytes(b"a: 1\n", representation_only=True)
                self.assertFalse(document.value_constructed)
                with mock.patch.object(
                    yaml.SafeLoader,
                    "construct_object",
                    side_effect=exception_type("reviewer programming defect"),
                ):
                    with self.assertRaises(exception_type):
                        load_yaml_bytes(b"a: 1\n")

    def test_data_exception_allowlist_excludes_programming_errors(self) -> None:
        # The representation-mode allowlist adds exactly KeyError, AttributeError,
        # and IndexError to the accepted M002 exception types; it never widens to
        # Exception/BaseException, so non-allowlisted RuntimeError and control-flow
        # exits stay visible.
        import inspect

        import frutlups._yaml as yaml_module

        source = inspect.getsource(yaml_module._key_identity)
        self.assertNotIn("except Exception", source)
        self.assertNotIn("except BaseException", source)
        self.assertIn("KeyError", source)
        self.assertIn("AttributeError", source)
        self.assertIn("IndexError", source)
