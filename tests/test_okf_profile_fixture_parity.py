"""Pinned-corpus parity and the complete limit matrix (M004-S04).

The 25-file corpus under ``tests/fixtures/okf_profile`` is a byte-exact copy of
the template candidate's pinned fixture corpus, taken from committed blobs at
the declared template baseline. This suite proves:

* corpus integrity — every copied file matches its pinned SHA-256 (the digest
  table below restates
  ``04_delivery/frutlups_okf_kickoff_handoff/template_candidate_pin_manifest.json``
  so the check is self-contained in the product tree);
* full 24-fixture parity — every fixture reproduces its ``full_parser``
  OKF/profile outcome, identified separately, and the corpus covers all pinned
  oracle rows except row 6, which has no committed fixture and is constructed
  here per the canonical decision matrix;
* every declared resource limit at its maximum and at maximum-plus-one through
  the public observation, with the internal refusal category attributed
  white-box through the accepted boundary; and
* refusal determinism, hostile-input hygiene, filesystem purity, and read-only
  exactness (no discovery of adjacent files, no environment or directory use).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from frutlups._yaml import (
    DEFAULT_YAML_LIMITS,
    YamlBoundaryError,
    YamlFailure,
    load_yaml_bytes,
)
from frutlups.okf_profile import OKFProfileObservation, observe_okf_profile_path

_CORPUS = Path(__file__).resolve().parent / "fixtures" / "okf_profile"

# The pinned OKF/profile reference checker ships as a test-only, package-relative
# fixture (never product code, a public export, a runtime dependency, or a wheel
# member). It is loaded explicitly from that file with standard-library import
# machinery, without permanently mutating global ``sys.path``, so this parity check
# runs from the flattened front-facing checkout. See
# ``fixtures/front_repo_contract/manifest.json``.
_CHECKER_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "front_repo_contract" / "okf_yaml_profile.py"
)


def _load_reference_checker():
    spec = importlib.util.spec_from_file_location(
        "front_repo_contract_okf_yaml_profile", _CHECKER_FIXTURE
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

# Pinned committed-blob digests for the copied corpus (template baseline
# 6ab4f212818ad30a214ac72edbca6ca487e35c18; restated from the machine-readable
# pin manifest named in the module docstring).
_PINNED_CORPUS_SHA256 = {
    "accepted_full.md": "8ab5d9da183209bef7646c4e988147eef0b3c799736051acd34f7e9c2cc90f07",
    "accepted_minimal.md": "7a04d05f405a61b54e7c3a8f5c708f5e14842fd44ab534aac3a134cbf86deb28",
    "accepted_no_profile_field.md": "7ccc3f038ba8302b0e0a74fff9d1a607e95c709ab73d62f8d77c6a8a987c5b33",
    "accepted_quoted_numeric.md": "944ac4e268a7689153a1601c70a0cb46589a8c3035f4eea7871f8dc6d25a0aad",
    "accepted_unknown_extension.md": "1f64b3b806714eaef2fc7b9dd7a3be7efc7d2a3bc5cf84322a1cbd0e42e23de0",
    "anchors_aliases.md": "339f00535c3bd8a9c9f0b1611e8e47fa96a946d27daf959d4faa3f08636a1ec8",
    "duplicate_key.md": "156c89451dafc7e9b48d13929e98a630ef82b8c42348e3ba2e0c3f646cd5f79f",
    "flow_collection.md": "167a7ad406215432469f9f4f30e3d06ae1c89701324497020c7b0bae279d7148",
    "frontmatter_unterminated.md": "3e5132781878b4636de7a48c2992c7467e3732a3ed3edc2285bc034a762f7117",
    "legacy_no_frontmatter.md": "3478428621d6ba90151f0986b851c3b942fded3b90887b8ef67485907d65e7a2",
    "manifest.json": "2a12a9c1c0aeda1a7a9127dc21b421bfbd4eb7234bd5b56fba4e68cdf2f38b1e",
    "merge_keys.md": "98e25b28f5880975c77237514a0c241b8237821cbe152737be093248325e756e",
    "moved/original.md": "77a368f468e781b6e14aba93e3cff59e4a0ea961e62e87c5a6cc1b063cfb0a78",
    "moved/relocated.md": "c5867519ff1912e43c7a3af4921ce96c138f02abe246d2774ee71519bdd16e53",
    "scalar_leading_zero.md": "6cc872045a302e92c92936b9045554ddee08e522dde477a98645594520eabc9a",
    "scalar_native_timestamp.md": "ea0ec47bb549dd358317bb0d25a0d1440ef3973cbaab60baeef887f5912c1c29",
    "scalar_unquoted_bool.md": "ef0c9ad61ea464d3eec4fcdbff94677a0147cbcfee5d775cb6671e1c250dd764",
    "sem_equiv_key_order_a.md": "47d6d36a9e480db2b7a732af91bfd0dc6b79ae84f47fd87ad7e48a3008601636",
    "sem_equiv_key_order_b.md": "fdeebd84a9ee791212f9507d2d0b9817cb1476a35388cc836195f16d4646222b",
    "tool_namespace.md": "993601aefb3a94059280863a478d19eccba0d6db6107508f64e66861a4c7fb56",
    "type_empty.md": "9eba7d37aa4affab58b72cdc78a36456512caeede574f9d7c67a772be60b795e",
    "type_missing.md": "aa685f68d61bf87a3664f4180c52e4349f15e0ad918837db1b2057b9a627e331",
    "type_unknown.md": "b945ad416b37098eb703e8dc670ac825072d3e12987fc7207afd5c56d7d9a8fb",
    "version_mixed_newer_rc.md": "a593eb0de4a570b7a0889c52a081ae74ff231c343f02bb0b454790cc19040cab",
    "version_unknown.md": "1099e25de4d91d275237c9c69524eb57d4c274e0458b91d8d148ba6f1f63cc1c",
}

# Fixture id -> pinned oracle row number (consumer contract 09 section 4).
_FIXTURE_ROWS = {
    "legacy_no_frontmatter": 1,
    "accepted_no_profile_field": 2,
    "accepted_full": 3,
    "accepted_minimal": 3,
    "accepted_quoted_numeric": 3,
    "accepted_unknown_extension": 3,
    "moved_original": 3,
    "moved_relocated": 3,
    "sem_equiv_key_order_a": 3,
    "sem_equiv_key_order_b": 3,
    "tool_namespace": 3,
    "anchors_aliases": 4,
    "flow_collection": 4,
    "merge_keys": 4,
    "scalar_leading_zero": 4,
    "scalar_native_timestamp": 4,
    "scalar_unquoted_bool": 4,
    "duplicate_key": 5,
    "frontmatter_unterminated": 7,
    "type_empty": 8,
    "type_missing": 8,
    "type_unknown": 9,
    "version_mixed_newer_rc": 10,
    "version_unknown": 10,
}

ROW_LIMIT = ("unverified", "OKF_PARSE_LIMIT_EXCEEDED", "fail", "PROFILE_YAML_OUT_OF_SUBSET")
ROW_OUT_OF_SUBSET = ("pass", None, "fail", "PROFILE_YAML_OUT_OF_SUBSET")
ROW_NO_PROFILE_FIELD = ("pass", None, "not_applicable", None)
ROW_PROFILE_PASS = ("pass", None, "pass", None)

_LIMITS = DEFAULT_YAML_LIMITS
_TOTAL_LIMIT = 1_048_576


def _layers(observation: OKFProfileObservation) -> tuple[str, str | None, str, str | None]:
    return (
        observation.okf_concept.result,
        observation.okf_concept.reason,
        observation.framework_profile.result,
        observation.framework_profile.reason,
    )


def _manifest() -> dict:
    return json.loads((_CORPUS / "manifest.json").read_text(encoding="utf-8"))


class CorpusIntegrityTests(unittest.TestCase):
    def test_corpus_is_exactly_the_25_pinned_files_byte_exact(self) -> None:
        on_disk = {
            path.relative_to(_CORPUS).as_posix(): path
            for path in sorted(_CORPUS.rglob("*"))
            if path.is_file()
        }
        self.assertEqual(sorted(on_disk), sorted(_PINNED_CORPUS_SHA256))
        for rel, path in on_disk.items():
            with self.subTest(fixture=rel):
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                self.assertEqual(digest, _PINNED_CORPUS_SHA256[rel])

    def test_manifest_declares_24_fixtures_and_the_pinned_vocabulary(self) -> None:
        manifest = _manifest()
        self.assertEqual(manifest["manifest_schema"], "okf_profile_fixture_manifest")
        self.assertEqual(manifest["manifest_version"], "1")
        self.assertEqual(manifest["profile_candidate"], "0.1-rc.1")
        self.assertEqual(len(manifest["fixtures"]), 24)
        self.assertEqual(
            sorted(fixture["id"] for fixture in manifest["fixtures"]),
            sorted(_FIXTURE_ROWS),
        )


class FixtureParityTests(unittest.TestCase):
    """Every committed fixture reproduces its pinned ``full_parser`` outcome."""

    def test_all_24_fixtures_reproduce_their_full_parser_outcomes(self) -> None:
        manifest = _manifest()
        for fixture in manifest["fixtures"]:
            with self.subTest(fixture=fixture["id"]):
                rel = fixture["path"].removeprefix("tests/fixtures/okf_profile/")
                observation = observe_okf_profile_path(_CORPUS / rel)
                expected = fixture["expected"]["full_parser"]
                self.assertEqual(
                    {
                        "result": observation.okf_concept.result,
                        "reason": observation.okf_concept.reason,
                    },
                    expected["okf_concept"],
                )
                self.assertEqual(
                    {
                        "result": observation.framework_profile.result,
                        "reason": observation.framework_profile.reason,
                    },
                    expected["framework_profile"],
                )
                self.assertEqual(
                    observation.execution_eligibility,
                    fixture["expected"]["execution_eligibility"],
                )
                self.assertEqual(observation.diagnostics, ())
                self.assertEqual(observation.contract_version, 1)

    def test_corpus_covers_every_pinned_row_except_the_fixtureless_row_6(self) -> None:
        self.assertEqual(
            sorted(set(_FIXTURE_ROWS.values())), [1, 2, 3, 4, 5, 7, 8, 9, 10]
        )
        counts = {row: 0 for row in range(1, 11)}
        for row in _FIXTURE_ROWS.values():
            counts[row] += 1
        self.assertEqual(
            counts, {1: 1, 2: 1, 3: 9, 4: 6, 5: 1, 6: 0, 7: 1, 8: 2, 9: 1, 10: 2}
        )

    def test_row_6_is_constructed_because_it_has_no_committed_fixture(self) -> None:
        # The canonical decision matrix pins bounded resource refusal as
        # unverified/OKF_PARSE_LIMIT_EXCEEDED + fail/PROFILE_YAML_OUT_OF_SUBSET.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "row6.md"
            path.write_bytes(
                b'---\ntype: analysis\nframework_profile: "0.1-rc.1"\na: &x\n  b: *x\n---\n'
            )
            self.assertEqual(_layers(observe_okf_profile_path(path)), ROW_LIMIT)

    def test_fixture_outcomes_are_deterministic_across_repeated_calls(self) -> None:
        for fixture in _manifest()["fixtures"]:
            rel = fixture["path"].removeprefix("tests/fixtures/okf_profile/")
            path = _CORPUS / rel
            self.assertEqual(
                observe_okf_profile_path(path), observe_okf_profile_path(path)
            )

    def test_observation_leaves_every_fixture_byte_and_mtime_untouched(self) -> None:
        for rel in _PINNED_CORPUS_SHA256:
            path = _CORPUS / rel
            before = path.stat()
            observe_okf_profile_path(path)
            after = path.stat()
            self.assertEqual(before.st_size, after.st_size)
            self.assertEqual(before.st_mtime_ns, after.st_mtime_ns)
        # Byte identity is re-proven by the digest test running in the same
        # process order-independently.


class _LimitCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def observe_bytes(self, content: bytes) -> OKFProfileObservation:
        path = self.tmp / "limit.md"
        path.write_bytes(content)
        return observe_okf_profile_path(path)

    def assert_layers(self, content: bytes, row) -> None:
        self.assertEqual(_layers(self.observe_bytes(content)), row)

    def assert_boundary_category(self, frontmatter: bytes, category: YamlFailure) -> None:
        with self.assertRaises(YamlBoundaryError) as caught:
            load_yaml_bytes(frontmatter, limits=DEFAULT_YAML_LIMITS)
        self.assertEqual(caught.exception.category, category)


class TotalArtifactLimitTests(_LimitCase):
    def test_at_and_over_the_total_artifact_byte_ceiling(self) -> None:
        head = b'---\ntype: analysis\nframework_profile: "0.1-rc.1"\n---\n'
        at_limit = head + b"a" * (_TOTAL_LIMIT - len(head))
        self.assertEqual(len(at_limit), _TOTAL_LIMIT)
        self.assert_layers(at_limit, ROW_PROFILE_PASS)
        self.assert_layers(at_limit + b"b", ROW_LIMIT)


class FrontmatterLimitMatrixTests(_LimitCase):
    """At-limit and maximum-plus-one for every declared frontmatter limit.

    The at-limit input must be observed (its ordinary oracle row); the
    plus-one input must land in the bounded-refusal row, with the internal
    category attributed through the accepted boundary on the same framed
    bytes.
    """

    @staticmethod
    def _framed(frontmatter: str) -> bytes:
        return ("---\n" + frontmatter + "\n---\n").encode("utf-8")

    def _frontmatter_of_exact_bytes(self, total: int) -> str:
        # Double-quoted string lines (ASCII, so bytes equal characters) padded
        # to exactly ``total`` encoded bytes; every line stays far inside the
        # line-length limit and every other dimension inside its own.
        lines = ["type: analysis"]
        index = 0
        while True:
            joined = sum(len(line) + 1 for line in lines)
            remaining = total - joined  # budget for one final line, no newline
            assert remaining > 10
            prefix = f'k{index}: "'
            if remaining <= 8_000:
                lines.append(prefix + "a" * (remaining - len(prefix) - 1) + '"')
                break
            lines.append(prefix + "a" * (6_000 - len(prefix) - 1) + '"')
            index += 1
        text = "\n".join(lines)
        assert len(text.encode("utf-8")) == total
        assert max(len(line) for line in text.splitlines()) <= _LIMITS.max_line_length
        return text

    def test_frontmatter_bytes_at_and_over_limit(self) -> None:
        at_limit = self._frontmatter_of_exact_bytes(_LIMITS.max_bytes)
        self.assert_layers(self._framed(at_limit), ROW_NO_PROFILE_FIELD)
        over = self._frontmatter_of_exact_bytes(_LIMITS.max_bytes + 1)
        self.assert_boundary_category(over.encode("utf-8"), YamlFailure.INPUT_TOO_LARGE)
        self.assert_layers(self._framed(over), ROW_LIMIT)

    def test_line_count_at_and_over_limit(self) -> None:
        at_limit = "type: analysis\n" + "\n".join("# pad" for _ in range(_LIMITS.max_lines - 1))
        self.assertEqual(len(at_limit.splitlines()), _LIMITS.max_lines)
        self.assert_layers(self._framed(at_limit), ROW_NO_PROFILE_FIELD)
        over = at_limit + "\n# one more"
        self.assert_boundary_category(over.encode("utf-8"), YamlFailure.TOO_MANY_LINES)
        self.assert_layers(self._framed(over), ROW_LIMIT)

    def test_line_length_at_and_over_limit(self) -> None:
        prefix = 'type: analysis\nk: "'
        payload = _LIMITS.max_line_length - len('k: ""')
        at_limit = prefix + "a" * payload + '"'
        longest = max(len(line) for line in at_limit.splitlines())
        self.assertEqual(longest, _LIMITS.max_line_length)
        self.assert_layers(self._framed(at_limit), ROW_NO_PROFILE_FIELD)
        over = prefix + "a" * (payload + 1) + '"'
        self.assert_boundary_category(over.encode("utf-8"), YamlFailure.LINE_TOO_LONG)
        self.assert_layers(self._framed(over), ROW_LIMIT)

    def test_alias_count_at_and_over_limit(self) -> None:
        def document(references: int) -> str:
            return "type: analysis\na: &x v\nk: [" + ", ".join(["*x"] * references) + "]"

        # Aliases and the flow sequence are out of subset, so the at-limit
        # outcome is the ordinary row-4 observation, not a refusal.
        self.assert_layers(self._framed(document(_LIMITS.max_aliases)), ROW_OUT_OF_SUBSET)
        over = document(_LIMITS.max_aliases + 1)
        self.assert_boundary_category(over.encode("utf-8"), YamlFailure.TOO_MANY_ALIASES)
        self.assert_layers(self._framed(over), ROW_LIMIT)

    def test_nesting_depth_at_and_over_limit(self) -> None:
        def nested(depth: int) -> str:
            lines = ["type: analysis"]
            for level in range(1, depth):
                lines.append("  " * (level - 1) + f"m{level}:")
            lines.append("  " * (depth - 1) + "leaf: v")
            return "\n".join(lines)

        # depth collections total: the root mapping plus (depth - 1) nested.
        self.assert_layers(self._framed(nested(_LIMITS.max_depth)), ROW_NO_PROFILE_FIELD)
        over = nested(_LIMITS.max_depth + 1)
        self.assert_boundary_category(over.encode("utf-8"), YamlFailure.TOO_DEEP)
        self.assert_layers(self._framed(over), ROW_LIMIT)

    def test_node_count_at_and_over_limit(self) -> None:
        def flow_pair_document(items_a: int, items_b: int) -> str:
            return (
                "type: analysis\ns1: ["
                + ", ".join(["a"] * items_a)
                + "]\ns2: ["
                + ", ".join(["b"] * items_b)
                + "]"
            )

        # Nodes: root + type key/value + 2 sequence keys + 2 sequence nodes +
        # items -> 7 + items_a + items_b.
        overhead = 7
        budget = _LIMITS.max_nodes - overhead
        at_limit = flow_pair_document(budget - 996, 996)
        document = load_yaml_bytes(at_limit.encode("utf-8"), limits=DEFAULT_YAML_LIMITS)
        self.assertEqual(document.node_count, _LIMITS.max_nodes)
        self.assert_layers(self._framed(at_limit), ROW_OUT_OF_SUBSET)
        over = flow_pair_document(budget - 996, 997)
        self.assert_boundary_category(over.encode("utf-8"), YamlFailure.TOO_MANY_NODES)
        self.assert_layers(self._framed(over), ROW_LIMIT)

    def test_scalar_length_at_and_over_limit(self) -> None:
        # A plain scalar folded over three lines: the lexeme joins the lines
        # with single spaces, so its length is controlled exactly while every
        # line stays inside the line limit.
        def folded(total: int) -> str:
            first = 6_000
            second = 6_000
            third = total - first - second - 2  # two joining spaces
            return (
                "type: analysis\nk: " + "a" * first + "\n  " + "b" * second + "\n  " + "c" * third
            )

        at_limit = folded(_LIMITS.max_scalar_length)
        document = load_yaml_bytes(at_limit.encode("utf-8"), limits=DEFAULT_YAML_LIMITS)
        longest = max(len(evidence.lexeme) for evidence in document.scalars)
        self.assertEqual(longest, _LIMITS.max_scalar_length)
        self.assert_layers(self._framed(at_limit), ROW_NO_PROFILE_FIELD)
        over = folded(_LIMITS.max_scalar_length + 1)
        self.assert_boundary_category(over.encode("utf-8"), YamlFailure.SCALAR_TOO_LONG)
        self.assert_layers(self._framed(over), ROW_LIMIT)

    def test_mapping_pairs_at_and_over_limit(self) -> None:
        def flow_mapping(pairs: int) -> str:
            return "k: {" + ", ".join(f"a{index}: 1" for index in range(pairs)) + "}"

        at_limit = "type: analysis\n" + flow_mapping(_LIMITS.max_mapping_pairs)
        self.assert_layers(self._framed(at_limit), ROW_OUT_OF_SUBSET)
        over = "type: analysis\n" + flow_mapping(_LIMITS.max_mapping_pairs + 1)
        self.assert_boundary_category(over.encode("utf-8"), YamlFailure.MAPPING_TOO_LARGE)
        self.assert_layers(self._framed(over), ROW_LIMIT)

    def test_block_mapping_at_the_shared_line_and_pair_limit(self) -> None:
        # 500 top-level block pairs occupy exactly the 500-line limit too, so
        # this case pins both dimensions at their maxima simultaneously.
        pairs = [f"k{index}: v" for index in range(_LIMITS.max_mapping_pairs - 1)]
        at_limit = "type: analysis\n" + "\n".join(pairs)
        self.assertEqual(len(at_limit.splitlines()), _LIMITS.max_lines)
        self.assert_layers(self._framed(at_limit), ROW_NO_PROFILE_FIELD)

    def test_sequence_items_at_and_over_limit(self) -> None:
        def flow_sequence(items: int) -> str:
            return "type: analysis\nk: [" + ", ".join(["a"] * items) + "]"

        self.assert_layers(
            self._framed(flow_sequence(_LIMITS.max_sequence_items)), ROW_OUT_OF_SUBSET
        )
        over = flow_sequence(_LIMITS.max_sequence_items + 1)
        self.assert_boundary_category(over.encode("utf-8"), YamlFailure.SEQUENCE_TOO_LARGE)
        self.assert_layers(self._framed(over), ROW_LIMIT)

    @staticmethod
    def _token_dense_frontmatter(aliases: int, doc_end: bool) -> str:
        # Anchored, explicitly tagged empty flow sequences carry five tokens
        # for a single walked node, aliases add two tokens with no marginal
        # node, and a document-end marker adds exactly one token — so the
        # token dimension can be tuned independently of the node dimension.
        unique_items = 1_993  # nodes: root + 3 keys + 3 sequences + items
        thirds = [unique_items // 3] * 3
        thirds[2] += unique_items - sum(thirds)
        start = 0
        parts = []
        for index, count in enumerate(thirds):
            items = [f"&z{start + i} !!seq []" for i in range(count)]
            start += count
            if index == 0:
                items += ["*z0"] * aliases
            chunks = [
                ", ".join(items[k : k + 110]) for k in range(0, len(items), 110)
            ]
            parts.append(f"s{index}: [\n " + ",\n ".join(chunks) + "\n]")
        text = "\n".join(parts) + "\n"
        if doc_end:
            text += "...\n"
        return text

    def test_token_ceiling_at_and_over_limit(self) -> None:
        # At-limit success: exactly 10,000 tokens and exactly 2,000 nodes,
        # nothing refused; the ordinary oracle row (missing `type`) is
        # observed. (An earlier draft of this suite claimed an at-limit token
        # success was unreachable under the node ceiling; this construction
        # refutes that, so the claim was removed.)
        at_limit = self._token_dense_frontmatter(aliases=9, doc_end=True)
        document = load_yaml_bytes(at_limit.encode("utf-8"), limits=DEFAULT_YAML_LIMITS)
        self.assertEqual(document.token_count, _LIMITS.max_tokens)
        self.assertEqual(document.node_count, _LIMITS.max_nodes)
        self.assert_layers(
            self._framed(at_limit.rstrip("\n")),
            ("fail", "OKF_TYPE_MISSING", "not_applicable", None),
        )
        # Maximum-plus-one: one more alias reference adds two tokens, and the
        # scanner's token guard fires before any node work.
        over = self._token_dense_frontmatter(aliases=10, doc_end=False)
        with_doc_end = self._token_dense_frontmatter(aliases=10, doc_end=True)
        for candidate in (over, with_doc_end):
            with self.assertRaises(YamlBoundaryError) as caught:
                load_yaml_bytes(candidate.encode("utf-8"), limits=DEFAULT_YAML_LIMITS)
            self.assertEqual(caught.exception.category, YamlFailure.TOO_MANY_TOKENS)
        self.assert_layers(self._framed(over.rstrip("\n")), ROW_LIMIT)

    def test_alias_sharing_counts_the_shared_target_once(self) -> None:
        shared = "type: analysis\nb: &s [x, y]\nc: *s"
        document = load_yaml_bytes(shared.encode("utf-8"), limits=DEFAULT_YAML_LIMITS)
        # root + three key scalars + one value scalar + one sequence + two
        # items = 8 unique nodes; the aliased sequence is not counted again.
        self.assertEqual(document.node_count, 8)
        self.assertEqual(document.features.alias_references, 1)
        self.assert_layers(self._framed(shared), ROW_OUT_OF_SUBSET)

    def test_alias_cycle_and_recursion_before_the_depth_guard(self) -> None:
        cycle = "type: analysis\na: &x\n  b: *x"
        self.assert_boundary_category(cycle.encode("utf-8"), YamlFailure.ALIAS_CYCLE)
        self.assert_layers(self._framed(cycle), ROW_LIMIT)
        deep_flow = "[" * 3_000 + "]" * 3_000
        self.assert_boundary_category(deep_flow.encode("utf-8"), YamlFailure.TOO_DEEP)
        self.assert_layers(self._framed(deep_flow), ROW_LIMIT)


class RefusalHygieneTests(_LimitCase):
    HOSTILE_CASES = (
        ("duplicate key", b"---\ntype: a\ntype: b\n---\n"),
        ("malformed yaml", b'---\ntype: "bad" junk"\n---\n'),
        ("forbidden tag observed", b"---\nk: !!python/object/apply:os.system ['x']\n---\n"),
        ("deep flow recursion", b"---\n" + b"[" * 3_000 + b"]" * 3_000 + b"\n---\n"),
        ("alias cycle", b"---\na: &x\n  b: *x\n---\n"),
        ("invalid utf8", b"---\nk: \xff\n---\n"),
        ("over frontmatter bytes", ("---\n" + "\n".join('k%d: "%s"' % (i, "a" * 7000) for i in range(10)) + "\n---\n").encode("utf-8")),
    )

    def test_every_refusal_is_deterministic_bounded_and_echo_free(self) -> None:
        marker = "REFUSAL_MARKER_77ab"
        for label, content in self.HOSTILE_CASES:
            with self.subTest(case=label):
                path = self.tmp / (label.replace(" ", "_") + ".md")
                path.write_bytes(content.replace(b"junk", marker.encode("utf-8")))
                first = observe_okf_profile_path(path)
                second = observe_okf_profile_path(path)
                self.assertEqual(first, second)
                rendered = repr(first)
                self.assertNotIn(marker, rendered)
                self.assertNotIn(str(self.tmp), rendered)
                for diagnostic in first.diagnostics:
                    self.assertLessEqual(len(diagnostic), 240)

    def test_no_refusal_writes_journals_or_leaves_residue(self) -> None:
        for label, content in self.HOSTILE_CASES:
            path = self.tmp / (label.replace(" ", "_") + "_pure.md")
            path.write_bytes(content)
        before = sorted(entry.name for entry in self.tmp.iterdir())
        stats = {
            name: (self.tmp / name).stat().st_mtime_ns for name in before
        }
        for name in before:
            observe_okf_profile_path(self.tmp / name)
        self.assertEqual(sorted(entry.name for entry in self.tmp.iterdir()), before)
        for name in before:
            self.assertEqual((self.tmp / name).stat().st_mtime_ns, stats[name])


class CheckerEquivalenceTests(_LimitCase):
    """Exact-tuple parity with the pinned pre-construction checker.

    The expected tuples below were produced by running the pinned read-only
    checker adapter over the identical framed bytes (Prompt 034 Phase A/B);
    they pin the complete-domain semantics the 24-fixture corpus does not
    reach: aliases at occurrence paths, merge non-injection, aliased keys,
    complex keys, unknown/forbidden/constructor-incompatible tags, direct and
    aliased routing values, collection routing values, both key orders, and
    compound subset/type/version precedence.
    """

    MATRIX = (
        ("alias to explicit null as type", "x: &a null\ntype: *a", ROW_OUT_OF_SUBSET),
        ("alias to tilde as type", "x: &a ~\ntype: *a", ROW_OUT_OF_SUBSET),
        ("alias to empty scalar as type", 'x: &a ""\ntype: *a', ("fail", "OKF_TYPE_MISSING", "not_applicable", None)),
        ("aliased routing value", "x: &a analysis\ntype: *a", ROW_OUT_OF_SUBSET),
        ("aliased key naming type", "x: &a type\n*a : analysis", ROW_OUT_OF_SUBSET),
        ("aliased version value", 'v: &v "0.1-rc.1"\ntype: analysis\nframework_profile: *v', ROW_OUT_OF_SUBSET),
        ("merge-injected type is not top-level", "base: &b\n  type: analysis\n<<: *b", ("fail", "OKF_TYPE_MISSING", "not_applicable", None)),
        ("merge plus literal type", "base: &b\n  x: 1\n<<: *b\ntype: analysis", ROW_OUT_OF_SUBSET),
        ("complex sequence key with literal type", "type: analysis\n? [a, b]\n: v", ROW_OUT_OF_SUBSET),
        ("complex mapping key with literal type", "type: analysis\n? {a: 1}\n: v", ROW_OUT_OF_SUBSET),
        # Block form: no flow feature masks the complex-key policy itself.
        ("block complex key with literal type", "type: analysis\n? - a\n: v", ROW_OUT_OF_SUBSET),
        ("failed int construction", "type: analysis\nk: !!int abc", ROW_OUT_OF_SUBSET),
        ("failed bool construction", "type: analysis\nk: !!bool abc", ROW_OUT_OF_SUBSET),
        ("failed timestamp construction", "type: analysis\nk: !!timestamp 2026-13-40", ROW_OUT_OF_SUBSET),
        ("implicit out-of-range date", "type: analysis\nk: 2026-13-40", ROW_OUT_OF_SUBSET),
        ("unknown local tag", "type: analysis\nk: !frobnicate 1", ROW_OUT_OF_SUBSET),
        ("unknown global tag", "type: analysis\nk: !<tag:example.com,2026:thing> 1", ROW_OUT_OF_SUBSET),
        ("forbidden object tag", "type: analysis\nk: !!python/object/apply:os.system ['x']", ROW_OUT_OF_SUBSET),
        ("collection routing value", "type: [a]", ("fail", "OKF_TYPE_MISSING", "not_applicable", None)),
        ("subset beats unknown type", "type: made_up\nk: !frobnicate 1", ROW_OUT_OF_SUBSET),
        ("subset beats unknown version", 'type: analysis\nframework_profile: "9.9"\nk: !frobnicate 1', ROW_OUT_OF_SUBSET),
        ("subset beats both unknown type and version", 'type: made_up\nframework_profile: "9.9"\n? [a]\n: v', ROW_OUT_OF_SUBSET),
        ("type missing beats subset", "k: !frobnicate 1", ("fail", "OKF_TYPE_MISSING", "not_applicable", None)),
        ("type empty beats subset", "type:\nk: !frobnicate 1", ("fail", "OKF_TYPE_MISSING", "not_applicable", None)),
        ("tagged type value keeps lexeme", "type: !frobnicate analysis", ROW_OUT_OF_SUBSET),
        ("failed construction on type keeps lexeme", "type: !!int abc", ROW_OUT_OF_SUBSET),
        ("key order type first", 'type: analysis\nframework_profile: "0.1-rc.1"', ROW_PROFILE_PASS),
        ("key order version first", 'framework_profile: "0.1-rc.1"\ntype: analysis', ROW_PROFILE_PASS),
    )

    def test_checker_equivalence_matrix_exact_tuples(self) -> None:
        for label, frontmatter, expected in self.MATRIX:
            with self.subTest(case=label):
                observation = self.observe_bytes(
                    ("---\n" + frontmatter + "\n---\n").encode("utf-8")
                )
                self.assertEqual(_layers(observation), expected)
                self.assertEqual(observation.execution_eligibility, "not_evaluated")
                self.assertEqual(observation.diagnostics, ())

    def test_matrix_outcomes_are_deterministic_and_echo_free(self) -> None:
        for label, frontmatter, _ in self.MATRIX[:6]:
            with self.subTest(case=label):
                content = ("---\n" + frontmatter + "\n---\n").encode("utf-8")
                path = self.tmp / "eq.md"
                path.write_bytes(content)
                first = observe_okf_profile_path(path)
                self.assertEqual(first, observe_okf_profile_path(path))
                self.assertNotIn("frobnicate", repr(first))

    # The checker-exception domain: bounded inputs on which the pinned checker
    # itself raises rather than returning its four-field result. Parity is not
    # defined here; the M004 contract owns a deterministic total result (Prompt
    # 035 architect decision), and the observation must never raise.
    CHECKER_EXCEPTION_DOMAIN = (
        ("invalid bool key, type first", "type: analysis\n? !!bool abc\n: v", ROW_OUT_OF_SUBSET),
        ("invalid bool key, key first", "? !!bool abc\n: v\ntype: analysis", ROW_OUT_OF_SUBSET),
        ("malformed timestamp key, type first", "type: analysis\n? !!timestamp abc\n: v", ROW_OUT_OF_SUBSET),
        ("malformed timestamp key, key first", "? !!timestamp abc\n: v\ntype: analysis", ROW_OUT_OF_SUBSET),
        ("empty int key, type first", 'type: analysis\n? !!int ""\n: v', ROW_OUT_OF_SUBSET),
        ("sign-only int key, key first", '? !!int "-"\n: v\ntype: analysis', ROW_OUT_OF_SUBSET),
        ("empty float key, type first", 'type: analysis\n? !!float ""\n: v', ROW_OUT_OF_SUBSET),
        ("invalid bool key, missing type", "? !!bool abc\n: v\nk: 1", ("fail", "OKF_TYPE_MISSING", "not_applicable", None)),
        ("invalid bool key, empty type", "type:\n? !!bool abc\n: v", ("fail", "OKF_TYPE_MISSING", "not_applicable", None)),
    )

    def test_checker_exception_domain_is_owned_and_observation_never_raises(self) -> None:
        checker = _load_reference_checker()

        for label, frontmatter, expected in self.CHECKER_EXCEPTION_DOMAIN:
            with self.subTest(case=label):
                doc = "---\n" + frontmatter + "\n---\n"
                # The pinned checker genuinely raises on this bounded input.
                with self.assertRaises((KeyError, AttributeError, IndexError)):
                    checker.evaluate_profile(doc)
                # The observation owns a total result and never raises.
                observation = self.observe_bytes(doc.encode("utf-8"))
                self.assertEqual(_layers(observation), expected)
                self.assertEqual(observation.execution_eligibility, "not_evaluated")
                self.assertEqual(observation.diagnostics, ())
                self.assertNotIn("abc", repr(observation))


class ReadOnlyExactnessTests(_LimitCase):
    def test_glob_metacharacters_are_never_expanded(self) -> None:
        (self.tmp / "a.md").write_bytes(b"---\ntype: analysis\n---\n")
        (self.tmp / "b.md").write_bytes(b"---\ntype: analysis\n---\n")
        observation = observe_okf_profile_path(self.tmp / "*.md")
        self.assertEqual(observation.diagnostics, ("artifact read failed",))
        observation = observe_okf_profile_path(str(self.tmp / "?.md"))
        self.assertEqual(observation.diagnostics, ("artifact read failed",))

    def test_adjacent_files_never_affect_the_named_artifact(self) -> None:
        clean = self.tmp / "clean.md"
        clean.write_bytes(b'---\ntype: analysis\nframework_profile: "0.1-rc.1"\n---\n')
        isolated = observe_okf_profile_path(clean)
        hostile = self.tmp / "hostile.md"
        hostile.write_bytes(b"---\ntype: a\ntype: b\n---\n" + b"\xff")
        (self.tmp / "PROJECT_STATE.md").write_bytes(b"# not read\n")
        (self.tmp / "frutlups.layout.yaml").write_bytes(b"not: [valid\n")
        with_neighbors = observe_okf_profile_path(clean)
        self.assertEqual(isolated, with_neighbors)
        self.assertEqual(_layers(with_neighbors), ROW_PROFILE_PASS)

    def test_module_source_uses_no_discovery_or_environment_api(self) -> None:
        import frutlups.okf_profile as okf_profile_module

        source = Path(okf_profile_module.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "environ",
            "getenv",
            "glob(",
            "iglob",
            "walk(",
            "scandir",
            "listdir",
            "iterdir",
            "rglob",
            "resolve(",
            "readlink",
            "cwd(",
            "chdir",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
