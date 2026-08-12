"""Tests for M003-S01: independent workflow-metadata regions.

The coding-prompt metadata reader observes the first-line ``---`` frame and
the first fenced YAML workflow block independently through the accepted
bounded YAML boundary. Concept frontmatter (``type``/``framework_profile``)
never routes; legacy-leading routing is preserved; dual workflow regions hit
the conservative S01 hold with no identity. The pinned fixtures are immutable
inputs; mutations happen on copies in temporary directories.
"""

from __future__ import annotations

import dataclasses
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import yaml

import frutlups
import frutlups.project as project_module
from frutlups.layout import legacy_profile, v2_default_profile
from frutlups.project import _parse_coding_prompt_meta
from frutlups.prompts import PromptArtifact, PromptKind

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "template_v3_driver_contract"

_CONFLICT_PREFIX = "dual workflow routing conflict:"
_CONFLICT_SUFFIX = (
    "differ between leading metadata frame and fenced workflow metadata block"
)


def _conflict_message(roles: str) -> str:
    return f"{_CONFLICT_PREFIX} {roles} {_CONFLICT_SUFFIX}"


def _meta_for(root: Path, filename: str, profile=None, sequence: int = 12):
    """Parse one prompt file under ``root`` with a flat coding-prompt dir."""

    if profile is None:
        profile = v2_default_profile()
    profile = dataclasses.replace(profile, coding_prompt_dir=".")
    artifact = PromptArtifact(
        kind=PromptKind.CODING, path=root / filename, filename=filename, sequence=sequence
    )
    return _parse_coding_prompt_meta(artifact, root, profile)


def _meta_text(text: str, profile=None, filename: str = "012_prompt.md", sequence: int = 12):
    tmp = TemporaryDirectory()
    root = Path(tmp.name)
    (root / filename).write_text(text, encoding="utf-8")
    meta = _meta_for(root, filename, profile, sequence)
    tmp.cleanup()
    return meta


def _fixture_meta(name: str):
    return _meta_for(_FIXTURES, name)


def _assert_conflict_refusal(test_case, meta, roles: str, values=()) -> None:
    """The exact M003-S02 conflict refusal: singleton diagnostic, no identity."""

    message = _conflict_message(roles)
    test_case.assertFalse(meta.valid)
    test_case.assertEqual((meta.milestone_id, meta.slice_id, meta.title), ("", "", ""))
    test_case.assertEqual(meta.self_report_path, "")
    test_case.assertEqual(meta.review_output_path, "")
    # Exact singleton-tuple equality: membership is insufficient (M003-S02
    # correction); no generic missing-field diagnostics may follow.
    test_case.assertEqual(meta.errors, (message,))
    test_case.assertLessEqual(len(message), 240)
    for value in values:
        for error in meta.errors:
            test_case.assertNotIn(value, error)


class PinnedFixtureRoutingTests(unittest.TestCase):
    def test_unprofiled_fixture_routes_from_fenced_region(self) -> None:
        meta = _fixture_meta("template_v3_prompt_012_unprofiled.md")
        self.assertTrue(meta.valid, meta.errors)
        self.assertEqual(meta.milestone_id, "M004")
        self.assertEqual(meta.slice_id, "M004-S01")

    def test_profiled_fixture_routes_from_fenced_region(self) -> None:
        meta = _fixture_meta("template_v3_prompt_013_profiled.md")
        self.assertTrue(meta.valid, meta.errors)
        self.assertEqual(meta.milestone_id, "M005")
        self.assertEqual(meta.slice_id, "M005-S01")

    def test_concept_key_mutations_change_no_native_identity(self) -> None:
        text = (_FIXTURES / "template_v3_prompt_013_profiled.md").read_text(encoding="utf-8")
        for mutated in (
            text.replace("type: coding_prompt", "type: review_prompt"),
            text.replace('framework_profile: "0.1-rc.1"', 'framework_profile: "9.9-zed"'),
            text.replace("type: coding_prompt", "type: coding_prompt\nmilestone: M999\nslice: S99"),
        ):
            meta = _meta_text(mutated)
            self.assertTrue(meta.valid, meta.errors)
            self.assertEqual((meta.milestone_id, meta.slice_id), ("M005", "M005-S01"))

    def test_legacy_leading_fixture_routes_unchanged(self) -> None:
        meta = _fixture_meta("legacy_leading_workflow_prompt.md")
        self.assertTrue(meta.valid, meta.errors)
        self.assertEqual((meta.milestone_id, meta.slice_id), ("M002", "M002-S01"))

    def test_profile_valid_native_invalid_fixture_has_no_identity(self) -> None:
        meta = _fixture_meta("profile_valid_native_invalid_prompt.md")
        self.assertFalse(meta.valid)
        self.assertEqual(meta.milestone_id, "")
        self.assertEqual(meta.slice_id, "")
        self.assertIn("could not parse milestone_id from coding prompt", meta.errors)
        self.assertFalse(any(_CONFLICT_PREFIX in e for e in meta.errors))

    def test_conflicting_dual_fixture_refuses_with_exact_diagnostic(self) -> None:
        meta = _fixture_meta("conflicting_dual_workflow_prompt.md")
        _assert_conflict_refusal(self, meta, "milestone, slice", values=("M003", "M007", "S01", "S04"))

    def test_identical_dual_routing_selects_leading(self) -> None:
        text = (
            "---\nmilestone: M002\nslice: S01\n---\n\n"
            "# Prompt\n\n```yaml\nmilestone: M002\nslice: S01\n```\n"
        )
        meta = _meta_text(text)
        self.assertTrue(meta.valid, meta.errors)
        self.assertEqual((meta.milestone_id, meta.slice_id), ("M002", "M002-S01"))
        self.assertEqual(
            meta.self_report_path, "05_governance/reviews/m002_s01_self_report.md"
        )


class MalformedRegionTests(unittest.TestCase):
    def test_unterminated_leading_frame_no_fallback_to_fenced(self) -> None:
        meta = _meta_text(
            "---\ntype: coding_prompt\n\n# Prompt\n\n```yaml\nmilestone: M004\nslice: S01\n```\n"
        )
        self.assertFalse(meta.valid)
        self.assertEqual(meta.milestone_id, "")
        self.assertTrue(any("unterminated" in e and "leading" in e for e in meta.errors))

    def test_unterminated_fenced_block_no_fallback_to_leading(self) -> None:
        meta = _meta_text(
            "---\nmilestone: M002\nslice: S01\n---\n\n# Prompt\n\n```yaml\nrole: coder\n"
        )
        self.assertFalse(meta.valid)
        self.assertEqual(meta.milestone_id, "")
        self.assertTrue(any("unterminated" in e and "fenced" in e for e in meta.errors))

    def test_malformed_leading_yaml_refuses_with_valid_fenced(self) -> None:
        meta = _meta_text(
            '---\nmilestone: "unterminated\n---\n\n# Prompt\n\n```yaml\nmilestone: M004\nslice: S01\n```\n'
        )
        self.assertFalse(meta.valid)
        self.assertEqual(meta.milestone_id, "")
        self.assertTrue(any("leading workflow metadata region refused" in e for e in meta.errors))

    def test_malformed_fenced_yaml_refuses_with_valid_leading(self) -> None:
        meta = _meta_text(
            '---\nmilestone: M002\nslice: S01\n---\n\n# Prompt\n\n```yaml\nrole: "unterminated\n```\n'
        )
        self.assertFalse(meta.valid)
        self.assertEqual(meta.milestone_id, "")
        self.assertTrue(any("fenced workflow metadata region refused" in e for e in meta.errors))

    def test_refusal_text_is_bounded_and_echo_free(self) -> None:
        hostile = "X43Q_HOSTILE <script>"
        meta = _meta_text(f'---\nmilestone: "{hostile}\n---\n\n# Prompt\n')
        self.assertFalse(meta.valid)
        for error in meta.errors:
            self.assertNotIn(hostile, error)
            self.assertNotIn("Traceback", error)
            self.assertLessEqual(len(error), 240)


class FramingAndDerivationTests(unittest.TestCase):
    def test_first_line_only_framing(self) -> None:
        # A blank first line means no leading region; the fenced block routes.
        meta = _meta_text("\n---\nmilestone: M009\nslice: S01\n---\n\n```yaml\nmilestone: M004\nslice: S01\n```\n")
        self.assertTrue(meta.valid, meta.errors)
        self.assertEqual((meta.milestone_id, meta.slice_id), ("M004", "M004-S01"))

    def test_absent_regions_missing_identity_errors(self) -> None:
        meta = _meta_text("# Prompt\n\nNo metadata regions here.\n")
        self.assertFalse(meta.valid)
        self.assertIn("could not parse milestone_id from coding prompt", meta.errors)
        self.assertIn("could not parse slice_id from coding prompt", meta.errors)

    def test_empty_leading_mapping_is_not_an_error(self) -> None:
        meta = _meta_text("---\n---\n\n# Prompt\n\n```yaml\nmilestone: M004\nslice: S01\n```\n")
        self.assertTrue(meta.valid, meta.errors)
        self.assertEqual(meta.milestone_id, "M004")

    def test_missing_slice_in_fenced_region(self) -> None:
        meta = _meta_text("# Prompt\n\n```yaml\nmilestone: M004\nrole: coder\n```\n")
        self.assertFalse(meta.valid)
        self.assertIn("could not parse slice_id from coding prompt", meta.errors)

    def test_configurable_routing_field_names(self) -> None:
        profile = dataclasses.replace(
            v2_default_profile(),
            front_matter_milestone_field="ms",
            front_matter_slice_field="sl",
            front_matter_title_field="name",
        )
        meta = _meta_text(
            "```yaml\nms: M005\nsl: S02\nname: custom slice\n```\n", profile=profile
        )
        self.assertTrue(meta.valid, meta.errors)
        self.assertEqual((meta.milestone_id, meta.slice_id, meta.title), ("M005", "M005-S02", "custom slice"))

    def test_title_falls_back_to_slug(self) -> None:
        meta = _meta_text(
            "```yaml\nmilestone: M004\nslice: S01\n```\n", filename="012_my_slice.md"
        )
        self.assertTrue(meta.valid, meta.errors)
        self.assertEqual(meta.title, "my slice")

    def test_self_report_and_review_paths_derive_from_routing(self) -> None:
        meta = _fixture_meta("template_v3_prompt_012_unprofiled.md")
        self.assertEqual(
            meta.self_report_path, "05_governance/reviews/m004_s01_self_report.md"
        )
        self.assertEqual(
            meta.review_output_path, "05_governance/reviews/m004_s01_review_report.md"
        )

    def test_legacy_body_section_fallback_preserved(self) -> None:
        meta = _meta_text(
            "# Prompt\n\n## Active Roadmap Item\n\n"
            "Active roadmap milestone: `M001`\n\n"
            "Detailed roadmap slice: `M001-S01: body title`\n",
            profile=legacy_profile(),
        )
        self.assertEqual(meta.milestone_id, "M001")
        self.assertEqual(meta.slice_id, "M001-S01")
        self.assertEqual(meta.title, "body title")


class RegionSchemaRefusalTests(unittest.TestCase):
    """Boundary and workflow-schema refusals stay deterministic (H7 shapes)."""

    def _assert_region_refusal(self, block: str, marker: str) -> None:
        meta = _meta_text(f"# Prompt\n\n```yaml\n{block}\n```\n")
        self.assertFalse(meta.valid)
        self.assertEqual(meta.milestone_id, "")
        self.assertTrue(any(marker in e for e in meta.errors), meta.errors)

    def test_semantic_duplicate_keys(self) -> None:
        self._assert_region_refusal("milestone: M001\nmilestone: M001", "duplicate_key")

    def test_multiple_documents(self) -> None:
        self._assert_region_refusal("milestone: M001\n---\nslice: S01", "multiple_documents")

    def test_unsupported_tag(self) -> None:
        self._assert_region_refusal(
            "milestone: !!python/object/new:os.system\n  args:\n    - x", "unsupported_tag"
        )

    def test_anchor_alias(self) -> None:
        self._assert_region_refusal(
            "milestone: &x M001\nslice: *x", "anchors or aliases"
        )

    def test_merge_key(self) -> None:
        self._assert_region_refusal(
            "base: &b\n  milestone: M001\nmerged:\n  <<: *b", "merge keys"
        )

    def test_flow_collection(self) -> None:
        self._assert_region_refusal('milestone: M001\nextra: ["a", "b"]', "flow collections")

    def test_explicit_tag(self) -> None:
        self._assert_region_refusal("milestone: !!str M001", "explicit tags")

    def test_non_mapping_region(self) -> None:
        self._assert_region_refusal("- milestone\n- slice", "single mapping")

    def test_non_string_key(self) -> None:
        self._assert_region_refusal("1: x\nmilestone: M001", "keys must be strings")

    def test_non_string_routing_value(self) -> None:
        self._assert_region_refusal("milestone: 4\nslice: S01", "routing values must be strings")

    def test_max_lines_plus_one(self) -> None:
        self._assert_region_refusal("milestone: M001\n" + "# pad\n" * 500, "too_many_lines")

    def test_too_deep(self) -> None:
        nested = "".join(f"{'  ' * i}k{i}:\n" for i in range(40))
        self._assert_region_refusal(nested, "too_deep")


class RegionPurityTests(unittest.TestCase):
    def test_each_present_region_parsed_exactly_once(self) -> None:
        real = project_module.load_yaml_bytes
        with mock.patch.object(project_module, "load_yaml_bytes", side_effect=real) as spy:
            _fixture_meta("template_v3_prompt_013_profiled.md")
            self.assertEqual(spy.call_count, 2)
            _fixture_meta("template_v3_prompt_012_unprofiled.md")
            self.assertEqual(spy.call_count, 3)
            _fixture_meta("legacy_leading_workflow_prompt.md")
            self.assertEqual(spy.call_count, 4)

    def test_repeated_reads_are_pure(self) -> None:
        constructors_before = dict(yaml.SafeLoader.yaml_constructors)
        recursion_before = sys.getrecursionlimit()
        fixture = _FIXTURES / "template_v3_prompt_013_profiled.md"
        bytes_before = fixture.read_bytes()
        first = _fixture_meta("template_v3_prompt_013_profiled.md")
        second = _fixture_meta("template_v3_prompt_013_profiled.md")
        self.assertEqual(first, second)
        self.assertEqual(fixture.read_bytes(), bytes_before)
        self.assertEqual(dict(yaml.SafeLoader.yaml_constructors), constructors_before)
        self.assertEqual(sys.getrecursionlimit(), recursion_before)


class PublicSurfaceTests(unittest.TestCase):
    def test_exports_and_private_reader(self) -> None:
        self.assertEqual(len(frutlups.__all__), 152)
        for name in (
            "_workflow_routing_mapping",
            "_parse_workflow_region",
            "_leading_frame_region",
            "_fenced_workflow_region",
        ):
            self.assertFalse(hasattr(frutlups, name), name)
        self.assertFalse(hasattr(project_module, "_parse_front_matter"))


# ---------------------------------------------------------------------------
# M003-S01 correction (prompt 015): exact framing, concept-before-schema
# authority, present-field string validation, and identity closure.
# ---------------------------------------------------------------------------


class ExactFramingTests(unittest.TestCase):
    def test_trailing_padded_opener_is_absent_and_fenced_routes(self) -> None:
        meta = _meta_text(
            "--- \nmilestone: M009\nslice: S09\n---\n\n# P\n\n```yaml\nmilestone: M004\nslice: S01\n```\n"
        )
        self.assertTrue(meta.valid, meta.errors)
        self.assertEqual((meta.milestone_id, meta.slice_id), ("M004", "M004-S01"))

    def test_indented_opener_is_absent_and_fenced_routes(self) -> None:
        meta = _meta_text(
            "  ---\nmilestone: M009\nslice: S09\n---\n\n# P\n\n```yaml\nmilestone: M004\nslice: S01\n```\n"
        )
        self.assertTrue(meta.valid, meta.errors)
        self.assertEqual((meta.milestone_id, meta.slice_id), ("M004", "M004-S01"))

    def test_exact_opener_with_padded_would_be_closer_is_unterminated(self) -> None:
        meta = _meta_text(
            "---\nmilestone: M009\nslice: S09\n--- \n\n# P\n\n```yaml\nmilestone: M004\nslice: S01\n```\n"
        )
        self.assertFalse(meta.valid)
        self.assertEqual((meta.milestone_id, meta.slice_id, meta.title), ("", "", ""))
        self.assertEqual(meta.self_report_path, "")
        self.assertEqual(meta.review_output_path, "")
        self.assertIn(
            "leading metadata frame is unterminated (no closing --- line)", meta.errors
        )

    def test_exact_opener_with_indented_would_be_closer_is_unterminated(self) -> None:
        meta = _meta_text(
            "---\nmilestone: M009\nslice: S09\n  ---\n\n# P\n\n```yaml\nmilestone: M004\nslice: S01\n```\n"
        )
        self.assertFalse(meta.valid)
        self.assertEqual(meta.milestone_id, "")
        self.assertTrue(any("unterminated" in e for e in meta.errors))


class ConceptAuthorityTests(unittest.TestCase):
    """Concept-only leading mappings cannot touch a valid fenced identity."""

    _FENCED = "\n# P\n\n```yaml\nmilestone: M004\nslice: S01\n```\n"

    def _with_leading(self, body: str):
        return _meta_text(f"---\n{body}\n---\n{self._FENCED}")

    def test_concept_mutations_preserve_fenced_identity(self) -> None:
        cases = {
            "unknown type string": "type: something_else",
            "unknown profile string": 'type: coding_prompt\nframework_profile: "9.9-zed"',
            "routing-shaped string": "type: coding_prompt\nmilestone: M999\nslice: S99",
            "routing-shaped null": "type: coding_prompt\nmilestone: null",
            "routing-shaped numeric": "type: coding_prompt\nmilestone: 999",
            "routing-shaped boolean": "type: coding_prompt\nmilestone: true",
            "routing-shaped sequence": "type: coding_prompt\nmilestone:\n  - M009",
            "routing-shaped mapping": "type: coding_prompt\ntitle:\n  x: 1",
            "flow collection field": "type: coding_prompt\nextra: [1, 2]",
            "anchor and alias fields": "type: &t coding_prompt\nother: *t",
        }
        for label, body in cases.items():
            with self.subTest(label=label):
                meta = self._with_leading(body)
                self.assertTrue(meta.valid, meta.errors)
                self.assertEqual(meta.milestone_id, "M004")
                self.assertEqual(meta.slice_id, "M004-S01")
                self.assertEqual(
                    meta.self_report_path, "05_governance/reviews/m004_s01_self_report.md"
                )

    def test_boundary_invalid_concept_region_still_fails_closed(self) -> None:
        meta = self._with_leading("type: a\ntype: b")
        self.assertFalse(meta.valid)
        self.assertEqual((meta.milestone_id, meta.slice_id), ("", ""))
        self.assertIn("leading workflow metadata region refused: duplicate_key", meta.errors)

    def test_concept_region_with_resource_overflow_fails_closed(self) -> None:
        meta = self._with_leading("type: coding_prompt\n" + "# pad\n" * 500)
        self.assertFalse(meta.valid)
        self.assertEqual(meta.milestone_id, "")
        self.assertTrue(any("too_many_lines" in e for e in meta.errors))


class PresentFieldValidationTests(unittest.TestCase):
    """Every present non-string configured routing value refuses (incl. null)."""

    def _assert_null_refusal(self, text: str, field: str) -> None:
        meta = _meta_text(text)
        self.assertFalse(meta.valid, field)
        self.assertEqual((meta.milestone_id, meta.slice_id, meta.title), ("", "", ""), field)
        self.assertEqual(meta.self_report_path, "", field)
        self.assertEqual(meta.review_output_path, "", field)
        self.assertTrue(any("routing values must be strings" in e for e in meta.errors), field)

    def test_present_null_in_each_field_fenced(self) -> None:
        for field in ("milestone", "slice", "title"):
            with self.subTest(field=field):
                others = {"milestone": "milestone: M004", "slice": "slice: S01", "title": "title: t"}
                lines = [f"{field}:" if k == field else v for k, v in
                         (("milestone", others["milestone"]), ("slice", others["slice"]), ("title", others["title"]))]
                self._assert_null_refusal("```yaml\n" + "\n".join(lines) + "\n```\n", field)

    def test_present_null_in_each_field_legacy_leading(self) -> None:
        for field in ("milestone", "slice"):
            with self.subTest(field=field):
                base = {"milestone": "milestone: M004", "slice": "slice: S01"}
                base[field] = f"{field}: ~"
                text = "---\n" + base["milestone"] + "\n" + base["slice"] + "\n---\n\n# P\n"
                self._assert_null_refusal(text, field)

    def test_representative_non_string_values_refuse(self) -> None:
        for label, value in (
            ("boolean", "true"),
            ("number", "4"),
            ("mapping", "{x: 1}"),
        ):
            with self.subTest(label=label):
                meta = _meta_text(f"```yaml\nmilestone: {value}\nslice: S01\n```\n")
                self.assertFalse(meta.valid)
                self.assertEqual((meta.milestone_id, meta.slice_id), ("", ""))
        meta = _meta_text("```yaml\nmilestone:\n  - M004\nslice: S01\n```\n")
        self.assertFalse(meta.valid)
        self.assertEqual((meta.milestone_id, meta.slice_id), ("", ""))
        self.assertTrue(any("routing values must be strings" in e for e in meta.errors))

    def test_absent_field_is_not_confused_with_present_null(self) -> None:
        meta = _meta_text("```yaml\nslice: S01\n```\n")
        self.assertFalse(meta.valid)
        self.assertEqual(meta.milestone_id, "")
        self.assertIn("could not parse milestone_id from coding prompt", meta.errors)
        self.assertFalse(any("routing values must be strings" in e for e in meta.errors))

    def test_custom_configured_names_get_same_presence_treatment(self) -> None:
        profile = dataclasses.replace(
            v2_default_profile(),
            front_matter_milestone_field="ms",
            front_matter_slice_field="sl",
            front_matter_title_field="ttl",
        )
        meta = _meta_text("```yaml\nms: null\nsl: S02\n```\n", profile=profile)
        self.assertFalse(meta.valid)
        self.assertEqual((meta.milestone_id, meta.slice_id), ("", ""))
        self.assertTrue(any("routing values must be strings" in e for e in meta.errors))
        meta = _meta_text("```yaml\nms: M005\nsl: S02\n```\n", profile=profile)
        self.assertTrue(meta.valid, meta.errors)
        self.assertEqual((meta.milestone_id, meta.slice_id), ("M005", "M005-S02"))


class BodyFallbackSuppressionTests(unittest.TestCase):
    """A region error is never repaired from the roadmap-item body fallback."""

    def _profile_with_body_fallback(self):
        return dataclasses.replace(
            v2_default_profile(), roadmap_item_section="active roadmap item"
        )

    _BODY = (
        "\n## Active Roadmap Item\n\n"
        "Active roadmap milestone: `M001`\n\n"
        "Detailed roadmap slice: `M001-S01: body title`\n"
    )

    def test_malformed_region_cannot_be_repaired_by_body(self) -> None:
        meta = _meta_text(
            "# P\n\n```yaml\nmilestone: null\nslice: S01\n```\n" + self._BODY,
            profile=self._profile_with_body_fallback(),
        )
        self.assertFalse(meta.valid)
        self.assertEqual((meta.milestone_id, meta.slice_id, meta.title), ("", "", ""))
        self.assertEqual(meta.self_report_path, "")
        self.assertEqual(meta.review_output_path, "")

    def test_dual_conflict_cannot_be_repaired_by_body(self) -> None:
        meta = _meta_text(
            "---\nmilestone: M003\nslice: S01\n---\n\n# P\n\n"
            "```yaml\nmilestone: M007\nslice: S04\n```\n" + self._BODY,
            profile=self._profile_with_body_fallback(),
        )
        _assert_conflict_refusal(self, meta, "milestone, slice")

    def test_body_fallback_intact_without_region_error(self) -> None:
        meta = _meta_text("# P\n" + self._BODY, profile=self._profile_with_body_fallback())
        self.assertEqual(meta.milestone_id, "M001")
        self.assertEqual(meta.slice_id, "M001-S01")
        self.assertEqual(meta.title, "body title")


# ---------------------------------------------------------------------------
# M003-S02 (prompt 017): the final deterministic dual-region conflict rule.
# ---------------------------------------------------------------------------


class DualRegionConflictTests(unittest.TestCase):
    """Differing shared roles refuse with the exact bounded diagnostic."""

    def _dual(self, leading_body: str, fenced_body: str):
        return _meta_text(
            f"---\n{leading_body}\n---\n\n# P\n\n```yaml\n{fenced_body}\n```\n"
        )

    def test_individual_role_conflicts(self) -> None:
        cases = {
            "milestone": ("milestone: M003\nslice: S01", "milestone: M007\nslice: S01", "milestone"),
            "slice": ("milestone: M003\nslice: S01", "milestone: M003\nslice: S04", "slice"),
            "title": (
                "milestone: M003\nslice: S01\ntitle: alpha",
                "milestone: M003\nslice: S01\ntitle: beta",
                "title",
            ),
        }
        for label, (leading, fenced, roles) in cases.items():
            with self.subTest(label=label):
                _assert_conflict_refusal(self, self._dual(leading, fenced), roles)

    def test_multi_field_combinations_and_ordering(self) -> None:
        cases = {
            "milestone+slice": ("milestone: M003\nslice: S01", "milestone: M007\nslice: S04", "milestone, slice"),
            "milestone+title": (
                "milestone: M003\nslice: S01\ntitle: a",
                "milestone: M007\nslice: S01\ntitle: b",
                "milestone, title",
            ),
            "slice+title": (
                "milestone: M003\nslice: S01\ntitle: a",
                "milestone: M003\nslice: S04\ntitle: b",
                "slice, title",
            ),
            "all three": (
                "milestone: M003\nslice: S01\ntitle: a",
                "milestone: M007\nslice: S04\ntitle: b",
                "milestone, slice, title",
            ),
        }
        for label, (leading, fenced, roles) in cases.items():
            with self.subTest(label=label):
                _assert_conflict_refusal(self, self._dual(leading, fenced), roles)

    def test_role_order_independent_of_yaml_entry_order(self) -> None:
        meta = self._dual("title: a\nslice: S01\nmilestone: M003", "milestone: M007\ntitle: b\nslice: S04")
        _assert_conflict_refusal(self, meta, "milestone, slice, title")

    def test_exact_equality_boundaries(self) -> None:
        # A case difference is a deliberate conflict; a backticked value is
        # invalid YAML (an indicator-led plain scalar) and refuses at the
        # boundary — both deterministic, both without identity.
        meta = self._dual("milestone: M003\nslice: S01", "milestone: m003\nslice: S01")
        _assert_conflict_refusal(self, meta, "milestone")
        meta = self._dual("milestone: M003\nslice: S01", "milestone: `M003`\nslice: S01")
        self.assertFalse(meta.valid)
        self.assertEqual((meta.milestone_id, meta.slice_id), ("", ""))
        self.assertTrue(any("invalid_yaml" in e for e in meta.errors))
        self.assertFalse(any(_CONFLICT_PREFIX in e for e in meta.errors))

    def test_outer_whitespace_stripping_is_not_a_conflict(self) -> None:
        meta = self._dual("milestone: M003\nslice: S01", "milestone:   M003  \nslice: S01")
        self.assertTrue(meta.valid, meta.errors)
        self.assertEqual((meta.milestone_id, meta.slice_id), ("M003", "M003-S01"))

    def test_custom_field_names_compare_and_report_canonical_roles(self) -> None:
        profile = dataclasses.replace(
            v2_default_profile(),
            front_matter_milestone_field="zqm",
            front_matter_slice_field="zqs",
            front_matter_title_field="zqt",
        )
        meta = _meta_text(
            "---\nzqm: M003\nzqs: S01\n---\n\n# P\n\n```yaml\nzqm: M007\nzqs: S04\n```\n",
            profile=profile,
        )
        # Canonical role labels only: no physical name or value leaks.
        _assert_conflict_refusal(self, meta, "milestone, slice", values=("zqm", "zqs", "M003", "M007", "S04"))

    def test_title_alone_does_not_create_a_dual_case(self) -> None:
        # Leading carries routing, fenced carries only a title: not dual.
        # The leading region routes; the fenced-only title is not merged, so
        # the slug fallback applies.
        meta = self._dual("milestone: M003\nslice: S01", "title: fenced title")
        self.assertTrue(meta.valid, meta.errors)
        self.assertEqual((meta.milestone_id, meta.slice_id), ("M003", "M003-S01"))
        self.assertEqual(meta.title, "prompt")
        self.assertFalse(any(_CONFLICT_PREFIX in e for e in meta.errors))

    def test_differing_shared_title_conflicts_once_both_qualify(self) -> None:
        meta = self._dual(
            "milestone: M003\nslice: S01\ntitle: alpha",
            "milestone: M003\nslice: S01\ntitle: beta",
        )
        _assert_conflict_refusal(self, meta, "title", values=("alpha", "beta"))


class DualRegionSelectionTests(unittest.TestCase):
    """Conflict-free dual cases select leading completely, never merging."""

    def _dual(self, leading_body: str, fenced_body: str):
        return _meta_text(
            f"---\n{leading_body}\n---\n\n# P\n\n```yaml\n{fenced_body}\n```\n"
        )

    def test_identical_shared_plus_fenced_only_field_does_not_merge(self) -> None:
        meta = self._dual(
            "milestone: M003\nslice: S01", "milestone: M003\nslice: S01\ntitle: fenced title"
        )
        self.assertTrue(meta.valid, meta.errors)
        self.assertEqual((meta.milestone_id, meta.slice_id), ("M003", "M003-S01"))
        # The fenced-only title does not supplement the leading selection;
        # the slug fallback applies instead.
        self.assertEqual(meta.title, "prompt")

    def test_disjoint_routing_fields_are_not_a_conflict(self) -> None:
        # Leading carries milestone, fenced carries slice: nothing shared.
        meta = self._dual("milestone: M003", "slice: S01")
        self.assertFalse(meta.valid)
        self.assertEqual(meta.milestone_id, "M003")
        self.assertEqual(meta.slice_id, "")
        self.assertIn("could not parse slice_id from coding prompt", meta.errors)
        self.assertFalse(any(_CONFLICT_PREFIX in e for e in meta.errors))

    def test_fenced_only_slice_does_not_fill_leading(self) -> None:
        meta = self._dual("milestone: M003\nslice: S02", "milestone: M003")
        # slice is present only in leading: no conflict; leading selected.
        self.assertTrue(meta.valid, meta.errors)
        self.assertEqual((meta.milestone_id, meta.slice_id), ("M003", "M003-S02"))

    def test_last_occurrence_within_region_before_cross_region_comparison(self) -> None:
        # Case-variant all-string spellings: the pinned last-occurrence value
        # (M003) is what the cross-region comparison sees.
        meta = self._dual(
            "milestone: M001\nMILESTONE: M003\nslice: S01", "milestone: M003\nslice: S01"
        )
        self.assertTrue(meta.valid, meta.errors)
        self.assertEqual((meta.milestone_id, meta.slice_id), ("M003", "M003-S01"))

    def test_last_occurrence_conflict_uses_winning_value(self) -> None:
        meta = self._dual(
            "milestone: M003\nMILESTONE: M009\nslice: S01", "milestone: M003\nslice: S01"
        )
        _assert_conflict_refusal(self, meta, "milestone")

    def test_concept_leading_never_enters_comparison(self) -> None:
        meta = _meta_text(
            "---\ntype: coding_prompt\nmilestone: M999\n---\n\n# P\n\n"
            "```yaml\nmilestone: M004\nslice: S01\n```\n"
        )
        self.assertTrue(meta.valid, meta.errors)
        self.assertEqual((meta.milestone_id, meta.slice_id), ("M004", "M004-S01"))

    def test_malformed_region_precedence_over_comparison(self) -> None:
        meta = _meta_text(
            "---\nmilestone: M003\nslice: S01\n---\n\n# P\n\n```yaml\nmilestone: null\nslice: S04\n```\n"
        )
        self.assertFalse(meta.valid)
        self.assertTrue(any("routing values must be strings" in e for e in meta.errors))
        self.assertFalse(any(_CONFLICT_PREFIX in e for e in meta.errors))


# ---------------------------------------------------------------------------
# M003-S02 correction (prompt 018): region-owned diagnostics are authoritative;
# conflicts return exactly one owned diagnostic.
# ---------------------------------------------------------------------------


class CausalDiagnosticControlTests(unittest.TestCase):
    """Singleton/two-error preservation and generic-behavior negative controls."""

    def test_boundary_refusal_is_exact_singleton(self) -> None:
        meta = _meta_text('# P\n\n```yaml\nmilestone: "unterminated\n```\n')
        self.assertFalse(meta.valid)
        self.assertEqual(
            meta.errors, ("fenced workflow metadata region refused: invalid_yaml",)
        )

    def test_schema_refusal_is_exact_singleton(self) -> None:
        meta = _meta_text("```yaml\nmilestone: null\nslice: S01\n```\n")
        self.assertFalse(meta.valid)
        self.assertEqual(
            meta.errors, ("fenced workflow routing values must be strings",)
        )

    def test_two_region_errors_preserved_in_deterministic_order(self) -> None:
        meta = _meta_text(
            '---\nmilestone: "unterminated\n---\n\n# P\n\n```yaml\nslice: "unterminated\n```\n'
        )
        self.assertFalse(meta.valid)
        self.assertEqual(
            meta.errors,
            (
                "leading workflow metadata region refused: invalid_yaml",
                "fenced workflow metadata region refused: invalid_yaml",
            ),
        )

    def test_no_region_keeps_generic_diagnostics(self) -> None:
        meta = _meta_text("# P\n\nNo metadata regions here.\n")
        self.assertFalse(meta.valid)
        self.assertEqual(
            meta.errors,
            (
                "could not parse milestone_id from coding prompt",
                "could not parse slice_id from coding prompt",
                "could not parse title from coding prompt",
                "could not parse self_report_path from coding prompt",
            ),
        )

    def test_incomplete_routing_keeps_generic_diagnostics(self) -> None:
        meta = _meta_text("# P\n\n```yaml\nmilestone: M004\nrole: coder\n```\n")
        self.assertFalse(meta.valid)
        self.assertEqual(
            meta.errors,
            (
                "could not parse slice_id from coding prompt",
                "could not parse title from coding prompt",
                "could not parse self_report_path from coding prompt",
            ),
        )

    def test_repeated_conflict_calls_return_identical_singleton(self) -> None:
        text = "---\nmilestone: M003\nslice: S01\n---\n\n# P\n\n```yaml\nmilestone: M007\nslice: S04\n```\n"
        first = _meta_text(text)
        second = _meta_text(text)
        self.assertEqual(first.errors, second.errors)
        self.assertEqual(first.errors, (_conflict_message("milestone, slice"),))

    def test_hostile_values_conflict_singleton_without_leak(self) -> None:
        hostile = "X43Q_HOSTILE <script>"
        meta = _meta_text(
            f"---\nmilestone: {hostile}\nslice: S01\n---\n\n# P\n\n"
            "```yaml\nmilestone: M007\nslice: S04\n```\n"
        )
        self.assertFalse(meta.valid)
        self.assertEqual(meta.errors, (_conflict_message("milestone, slice"),))
        self.assertNotIn(hostile, meta.errors[0])


# ---------------------------------------------------------------------------
# M003-S01 correction (prompt 016): literal concept-key classification and
# pre-normalization per-entry routing-value validation.
# ---------------------------------------------------------------------------


class LiteralConceptKeyTests(unittest.TestCase):
    """Concept classification depends only on the literal concept key."""

    _FENCED = "\n# P\n\n```yaml\nmilestone: M004\nslice: S01\n```\n"

    def _with_leading(self, body: str):
        return _meta_text(f"---\n{body}\n---\n{self._FENCED}")

    def test_concept_with_unrelated_non_string_keys_still_routes_fenced(self) -> None:
        cases = {
            "type plus integer key": "type: coding_prompt\norder: 3",
            "type plus boolean key": "type: coding_prompt\ntrue: x",
            "framework_profile plus integer key": 'framework_profile: "0.1-rc.1"\norder: 3',
            "framework_profile plus boolean key": 'framework_profile: "0.1-rc.1"\nfalse: x',
        }
        for label, body in cases.items():
            with self.subTest(label=label):
                meta = self._with_leading(body)
                self.assertTrue(meta.valid, meta.errors)
                self.assertEqual((meta.milestone_id, meta.slice_id), ("M004", "M004-S01"))
                self.assertEqual(
                    meta.self_report_path, "05_governance/reviews/m004_s01_self_report.md"
                )

    def test_concept_with_safe_flow_anchor_merge_features_routes_fenced(self) -> None:
        cases = {
            "flow collection": "type: coding_prompt\nextra: [1, 2]",
            "anchor and alias": "type: &t coding_prompt\nother: *t",
            "merge key": "base: &b\n  note: x\ntype: coding_prompt\nmerged:\n  <<: *b",
        }
        for label, body in cases.items():
            with self.subTest(label=label):
                meta = self._with_leading(body)
                self.assertTrue(meta.valid, meta.errors)
                self.assertEqual((meta.milestone_id, meta.slice_id), ("M004", "M004-S01"))

    def test_non_string_keys_without_concept_key_refuse_string_key_rule(self) -> None:
        meta = self._with_leading("milestone: M009\nslice: S09\norder: 3".replace("order", "3"))
        self.assertFalse(meta.valid)
        self.assertEqual((meta.milestone_id, meta.slice_id), ("", ""))
        self.assertTrue(any("keys must be strings" in e for e in meta.errors))

    def test_boundary_invalid_concept_mapping_fails_closed(self) -> None:
        meta = self._with_leading("type: a\ntype: b")
        self.assertFalse(meta.valid)
        self.assertEqual((meta.milestone_id, meta.slice_id), ("", ""))
        self.assertTrue(any("duplicate_key" in e for e in meta.errors))


class PreNormalizationValidationTests(unittest.TestCase):
    """No case/whitespace/order variant can hide a non-string routing value."""

    def _fenced(self, body: str):
        return _meta_text(f"# P\n\n```yaml\n{body}\n```\n")

    def _leading(self, body: str):
        return _meta_text(f"---\n{body}\n---\n\n# P\n")

    def _assert_hidden_variant_refused(self, meta, label: str) -> None:
        self.assertFalse(meta.valid, label)
        self.assertEqual((meta.milestone_id, meta.slice_id, meta.title), ("", "", ""), label)
        self.assertEqual(meta.self_report_path, "", label)
        self.assertEqual(meta.review_output_path, "", label)
        self.assertTrue(
            any("routing values must be strings" in e for e in meta.errors), (label, meta.errors)
        )

    def test_case_variant_cannot_hide_null_in_either_order(self) -> None:
        pairs = {
            "null first": "milestone:\nMilestone: M004\nslice: S01",
            "null last": "Milestone: M004\nmilestone:\nslice: S01",
        }
        for label, body in pairs.items():
            for region_name, build in (("fenced", self._fenced), ("leading", self._leading)):
                with self.subTest(label=label, region=region_name):
                    self._assert_hidden_variant_refused(build(body), label)

    def test_whitespace_padded_key_cannot_hide_null_in_either_order(self) -> None:
        pairs = {
            "null first": '" milestone ": null\nmilestone: M004\nslice: S01',
            "null last": 'milestone: M004\n" milestone ": null\nslice: S01',
        }
        for label, body in pairs.items():
            for region_name, build in (("fenced", self._fenced), ("leading", self._leading)):
                with self.subTest(label=label, region=region_name):
                    self._assert_hidden_variant_refused(build(body), label)

    def test_slice_and_title_variants_refuse_in_both_regions(self) -> None:
        cases = {
            "slice null hidden": "milestone: M004\nslice:\nSlice: S01",
            "slice null visible": "milestone: M004\nSlice: S01\nslice:",
            "title null hidden": "milestone: M004\nslice: S01\ntitle:\nTitle: ok",
            "title null visible": "milestone: M004\nslice: S01\nTitle: ok\ntitle:",
        }
        for label, body in cases.items():
            for region_name, build in (("fenced", self._fenced), ("leading", self._leading)):
                with self.subTest(label=label, region=region_name):
                    self._assert_hidden_variant_refused(build(body), label)

    def test_custom_configured_names_get_per_entry_validation(self) -> None:
        profile = dataclasses.replace(
            v2_default_profile(),
            front_matter_milestone_field="ms",
            front_matter_slice_field="sl",
            front_matter_title_field="ttl",
        )
        meta = _meta_text("```yaml\nms:\nMS: M005\nsl: S02\n```\n", profile=profile)
        self.assertFalse(meta.valid)
        self.assertEqual((meta.milestone_id, meta.slice_id), ("", ""))
        self.assertTrue(any("routing values must be strings" in e for e in meta.errors))

    def test_all_string_normalized_spellings_keep_existing_behavior(self) -> None:
        # Two case spellings, both strings: no refusal, and the normalized
        # mapping keeps its deterministic last-occurrence behavior. This is
        # not a duplicate policy; it pins pre-correction behavior.
        meta = self._fenced("milestone: M004\nMILESTONE: M009\nslice: S01")
        self.assertTrue(meta.valid, meta.errors)
        self.assertEqual((meta.milestone_id, meta.slice_id), ("M009", "M009-S01"))


if __name__ == "__main__":
    unittest.main()
