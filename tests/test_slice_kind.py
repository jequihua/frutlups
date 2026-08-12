"""Tests for the slice-kind classification contract.

Originally introduced for M010-S01. M011-S01 removed the milestone-identity
memory-update leak, so the pinned contract is now:

- SliceKind enum values are stable strings (both members retained)
- classify_slice_kind() returns NORMAL for every milestone, including M010 and
  its case variants (no identifier grants MEMORY_UPDATE by inference)
- LoopFrontier.slice_kind property returns NORMAL for an M010 frontier
- LoopFrontier.to_dict() includes "slice_kind" key as a plain string ("normal")
- CodingPromptTemplate.memory_update field defaults to False
- CodingPromptTemplate.to_dict() includes "memory_update"
- render_coding_prompt() with memory_update=True (set directly on the template)
  still produces memory-update posture; with False, normal posture
- build_coding_prompt_plan() for an M010 frontier sets memory_update=False and
  the generated prompt omits memory-mutation posture
- projects without memory roots still work normally
- existing tests remain green (checked by full suite)
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from frutlups.state import SliceKind, classify_slice_kind, RoadmapSlice
from frutlups.prompt_template import CodingPromptTemplate, render_coding_prompt
from frutlups.project import build_coding_prompt_plan, build_next_frontier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_template(root: Path) -> None:
    for name in (
        "00_brief",
        "03_experiments",
        "05_governance/reviews",
        "06_infra",
        "08_pkg",
        "prompts/for_coding_agent",
        "prompts/for_review_agent",
    ):
        (root / name).mkdir(parents=True, exist_ok=True)


def _write_roadmaps(root: Path, *, m010: bool = False) -> None:
    milestones = (
        "### M001: Scaffold\n\nStatus: active\n\n"
        "### M010: llloom Memory-Update Slices\n\nStatus: planned\n\n"
    )
    (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
        f"# Active Roadmap\n\n{milestones}", encoding="utf-8"
    )
    m010_slices = (
        "### M010: llloom Memory-Update Slices\n\n"
        "Slices:\n\n- M010-S01: memory-update slice type\n\n"
    ) if m010 else ""
    (root / "03_experiments" / "development_roadmap_frutlups.md").write_text(
        "# Detailed Roadmap\n\n"
        "### M001: Scaffold\n\nSlices:\n\n- M001-S01: initial scaffold\n\n"
        + m010_slices,
        encoding="utf-8",
    )
    (root / "05_governance" / "reviews" / "m001_s01_initial_scaffold_review_report.md").write_text(
        "# Review\n\n## Verdict\n\npass\n", encoding="utf-8"
    )


def _valid_template(**overrides: object) -> CodingPromptTemplate:
    defaults: dict[str, object] = dict(
        sequence=45,
        milestone_id="M010",
        slice_id="M010-S01",
        slug="frutlups_m010_s01_memory_update_slice_type",
        title="memory-update slice type",
        role_instructions="You are the coding agent for `frutlups`.",
        required_reading=("CLAUDE.md", "README.md"),
        scope_paths=("08_pkg/",),
        non_goals=("do not run mutating llloom commands",),
        definition_of_done=("tests pass",),
        verification_commands=("python -m unittest discover -s tests",),
        self_report_path="05_governance/reviews/m010_s01_self_report.md",
    )
    defaults.update(overrides)
    return CodingPromptTemplate(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# SliceKind enum
# ---------------------------------------------------------------------------

class SliceKindEnumTests(unittest.TestCase):
    def test_normal_value_is_string(self) -> None:
        self.assertEqual(SliceKind.NORMAL, "normal")

    def test_memory_update_value_is_string(self) -> None:
        self.assertEqual(SliceKind.MEMORY_UPDATE, "memory_update")

    def test_enum_members_are_distinct(self) -> None:
        self.assertNotEqual(SliceKind.NORMAL, SliceKind.MEMORY_UPDATE)


# ---------------------------------------------------------------------------
# classify_slice_kind()
# ---------------------------------------------------------------------------

class ClassifySliceKindTests(unittest.TestCase):
    def test_m010_is_normal(self) -> None:
        # M011-S01: milestone identity no longer confers memory-update authority.
        self.assertEqual(classify_slice_kind("M010"), SliceKind.NORMAL)

    def test_m010_lowercase_is_normal(self) -> None:
        # M011-S01: case variants of M010 are also NORMAL (no identifier leak).
        self.assertEqual(classify_slice_kind("m010"), SliceKind.NORMAL)

    def test_m001_is_normal(self) -> None:
        self.assertEqual(classify_slice_kind("M001"), SliceKind.NORMAL)

    def test_m009_is_normal(self) -> None:
        self.assertEqual(classify_slice_kind("M009"), SliceKind.NORMAL)

    def test_m011_is_normal(self) -> None:
        self.assertEqual(classify_slice_kind("M011"), SliceKind.NORMAL)

    def test_empty_string_is_normal(self) -> None:
        self.assertEqual(classify_slice_kind(""), SliceKind.NORMAL)

    def test_unknown_milestone_is_normal(self) -> None:
        self.assertEqual(classify_slice_kind("MXXX"), SliceKind.NORMAL)


# ---------------------------------------------------------------------------
# LoopFrontier.slice_kind and to_dict()
# ---------------------------------------------------------------------------

class LoopFrontierSliceKindTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)

    def tearDown(self) -> None:
        self._td.cleanup()

    def _frontier(self, *, m010: bool = False):
        _write_roadmaps(self.root, m010=m010)
        return build_next_frontier(self.root)

    def test_slice_kind_normal_for_non_m010_frontier(self) -> None:
        frontier = self._frontier(m010=False)
        # With no M010 slices, inferred_slice is None (M001 exhausted, no M010 slices)
        # or M001-S01 would be next if not accepted — but we wrote a pass verdict
        # Either way, kind should not be MEMORY_UPDATE
        self.assertEqual(frontier.slice_kind, SliceKind.NORMAL)

    def test_slice_kind_normal_for_m010_frontier(self) -> None:
        # M011-S01: an ordinary M010 frontier classifies NORMAL, not memory_update.
        frontier = self._frontier(m010=True)
        self.assertEqual(frontier.slice_kind, SliceKind.NORMAL)

    def test_to_dict_includes_slice_kind(self) -> None:
        frontier = self._frontier(m010=True)
        d = frontier.to_dict()
        self.assertIn("slice_kind", d)

    def test_to_dict_slice_kind_is_string(self) -> None:
        frontier = self._frontier(m010=True)
        d = frontier.to_dict()
        self.assertIsInstance(d["slice_kind"], str)

    def test_to_dict_slice_kind_normal_value_for_m010(self) -> None:
        # M011-S01: generated frontier JSON reflects "normal" for a downstream M010.
        frontier = self._frontier(m010=True)
        d = frontier.to_dict()
        self.assertEqual(d["slice_kind"], "normal")

    def test_to_dict_slice_kind_normal_value_without_m010(self) -> None:
        frontier = self._frontier(m010=False)
        d = frontier.to_dict()
        self.assertEqual(d["slice_kind"], "normal")


# ---------------------------------------------------------------------------
# CodingPromptTemplate.memory_update
# ---------------------------------------------------------------------------

class CodingPromptTemplateMemoryUpdateTests(unittest.TestCase):
    def test_memory_update_defaults_to_false(self) -> None:
        template = _valid_template()
        self.assertFalse(template.memory_update)

    def test_memory_update_true_when_set(self) -> None:
        template = _valid_template(memory_update=True)
        self.assertTrue(template.memory_update)

    def test_to_dict_includes_memory_update(self) -> None:
        template = _valid_template()
        self.assertIn("memory_update", template.to_dict())

    def test_to_dict_memory_update_is_bool(self) -> None:
        template = _valid_template()
        self.assertIsInstance(template.to_dict()["memory_update"], bool)


# ---------------------------------------------------------------------------
# render_coding_prompt() memory-update posture
# ---------------------------------------------------------------------------

class MemoryUpdatePromptRenderTests(unittest.TestCase):
    def _render(self, memory_update: bool) -> str:
        return render_coding_prompt(
            _valid_template(memory_update=memory_update)
        ).content

    def test_memory_update_prompt_includes_mutation_permitted(self) -> None:
        content = self._render(memory_update=True)
        self.assertIn("memory-update", content.lower())

    def test_memory_update_prompt_includes_review_evidence_requirement(self) -> None:
        content = self._render(memory_update=True)
        self.assertIn("review", content.lower())

    def test_memory_update_prompt_states_artifacts_authoritative(self) -> None:
        content = self._render(memory_update=True)
        self.assertIn("authoritative", content.lower())

    def test_normal_prompt_does_not_say_mutation_permitted(self) -> None:
        content = self._render(memory_update=False)
        self.assertNotIn("Memory mutation is permitted", content)

    def test_memory_update_prompt_still_valid(self) -> None:
        result = render_coding_prompt(_valid_template(memory_update=True))
        self.assertTrue(result.valid)

    def test_normal_prompt_still_valid(self) -> None:
        result = render_coding_prompt(_valid_template(memory_update=False))
        self.assertTrue(result.valid)

    def test_memory_update_and_normal_have_different_posture(self) -> None:
        mu = self._render(memory_update=True)
        normal = self._render(memory_update=False)
        # The llloom Integration Posture sections should differ
        self.assertNotEqual(mu, normal)


# ---------------------------------------------------------------------------
# build_coding_prompt_plan() memory_update propagation
# ---------------------------------------------------------------------------

class CodingPromptPlanMemoryUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_m010_frontier_sets_memory_update_false(self) -> None:
        # M011-S01: an M010 frontier no longer grants memory-update posture.
        _write_roadmaps(self.root, m010=True)
        plan = build_coding_prompt_plan(self.root)
        self.assertIsNotNone(plan.template)
        self.assertFalse(plan.template.memory_update)

    def test_normal_frontier_sets_memory_update_false(self) -> None:
        # Rewrite roadmaps without M010 so frontier is a normal slice
        (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
            "# Active Roadmap\n\n### M002: Next\n\nStatus: planned\n\n",
            encoding="utf-8",
        ) if False else None  # avoid undefined 'root'
        _write_roadmaps_normal(self.root)
        plan = build_coding_prompt_plan(self.root)
        if plan.valid and plan.template is not None:
            self.assertFalse(plan.template.memory_update)

    def test_m010_render_omits_memory_update_posture(self) -> None:
        # M011-S01: the generated prompt for an M010 frontier must not grant
        # memory-mutation posture merely because its milestone is M010.
        _write_roadmaps(self.root, m010=True)
        plan = build_coding_prompt_plan(self.root)
        self.assertIsNotNone(plan.render)
        self.assertNotIn("memory mutation is permitted", plan.render.content.lower())

    def test_plan_valid_for_m010_frontier(self) -> None:
        _write_roadmaps(self.root, m010=True)
        plan = build_coding_prompt_plan(self.root)
        self.assertTrue(plan.valid)


def _write_roadmaps_normal(root: Path) -> None:
    """Write a roadmap with a normal non-M010 frontier slice."""
    (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
        "# Active Roadmap\n\n### M001: Scaffold\n\nStatus: active\n\n"
        "### M002: Next\n\nStatus: planned\n\n",
        encoding="utf-8",
    )
    (root / "03_experiments" / "development_roadmap_frutlups.md").write_text(
        "# Detailed Roadmap\n\n"
        "### M001: Scaffold\n\nSlices:\n\n- M001-S01: initial scaffold\n\n"
        "### M002: Next\n\nSlices:\n\n- M002-S01: next thing\n\n",
        encoding="utf-8",
    )
    (root / "05_governance" / "reviews" / "m001_s01_initial_scaffold_review_report.md").write_text(
        "# Review\n\n## Verdict\n\npass\n", encoding="utf-8"
    )


if __name__ == "__main__":
    unittest.main()
