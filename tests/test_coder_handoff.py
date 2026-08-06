"""Tests for M011-S01: coder handoff generator.

Covers:
- normal active coding-prompt state renders frontier, loop step, coding
  prompt path, expected self-report path, workspace, and verification commands
- prompt-health warnings are visible in the handoff (not silently hidden)
- handoff text preserves role/provider neutrality
- handoff marks llloom / memory as optional and read-only for normal slices
- rendering is read-only and does not create any files
- handoff can be built from an existing ProjectStatus (no double read)
- date_str parameter is optional and keeps output deterministic when absent
- to_dict() is JSON-safe
"""

from __future__ import annotations

import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from frutlups.handoff import CoderHandoff, build_coder_handoff
from frutlups.project import build_status


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


def _write_active_roadmap(root: Path) -> None:
    (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
        "# Active Roadmap\n\n"
        "### M001: Scaffold\n\nStatus: active\n\n"
        "### M002: Next\n\nStatus: planned\n\n",
        encoding="utf-8",
    )


def _write_detailed_roadmap(root: Path) -> None:
    (root / "03_experiments" / "development_roadmap_frutlups.md").write_text(
        "# Detailed Roadmap\n\n"
        "### M001: Scaffold\n\nSlices:\n\n- M001-S01: initial scaffold\n\n"
        "### M002: Next\n\nSlices:\n\n- M002-S01: next thing\n\n",
        encoding="utf-8",
    )


def _write_coding_prompt(root: Path) -> None:
    (root / "prompts" / "for_coding_agent" / "001_frutlups_m001_s01_initial_scaffold.md").write_text(
        "# Coding Prompt 001: frutlups M001-S01 initial scaffold\n\n"
        "## Role\n\nYou are the coding agent.\n\n"
        "## Active Roadmap Item\n\n"
        "Active roadmap milestone: `M001`\n\n"
        "Detailed roadmap slice: `M001-S01: initial scaffold`\n\n"
        "## Required Self-Report\n\n"
        "Write a self-report at:\n\n"
        "`05_governance/reviews/m001_s01_initial_scaffold_self_report.md`\n",
        encoding="utf-8",
    )


def _write_pass_review_report(root: Path) -> None:
    (root / "05_governance" / "reviews" / "m001_s01_initial_scaffold_review_report.md").write_text(
        "# Review\n\n## Verdict\n\npass\n", encoding="utf-8"
    )


def _write_verdict_record(root: Path) -> None:
    # M003-S05: a valid receipt carries a live ``## Source`` citation of the
    # review report it records.
    (root / "05_governance" / "reviews" / "m001_s01_initial_scaffold_verdict_record.md").write_text(
        "# Verdict Record\n\n## Source\n\n"
        "Review report: `05_governance/reviews/m001_s01_initial_scaffold_review_report.md`\n",
        encoding="utf-8",
    )


def _simple_project_with_coding_prompt(root: Path) -> None:
    """Project with M001 active, M001-S01 fully recorded, M002-S01 as frontier."""
    _make_template(root)
    _write_active_roadmap(root)
    _write_detailed_roadmap(root)
    # M001-S01: pass review report + verdict record so pre-check doesn't block loop
    _write_pass_review_report(root)
    _write_verdict_record(root)
    # Write a coding prompt for M002-S01
    (root / "prompts" / "for_coding_agent" / "002_frutlups_m002_s01_next_thing.md").write_text(
        "# Coding Prompt 002: frutlups M002-S01 next thing\n\n"
        "## Role\n\nYou are the coding agent.\n\n"
        "## Active Roadmap Item\n\n"
        "Active roadmap milestone: `M002`\n\n"
        "Detailed roadmap slice: `M002-S01: next thing`\n\n"
        "## Required Self-Report\n\n"
        "Write a self-report at:\n\n"
        "`05_governance/reviews/m002_s01_next_thing_self_report.md`\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Basic construction
# ---------------------------------------------------------------------------

class HandoffConstructionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_roadmap(self.root)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_returns_coder_handoff(self) -> None:
        result = build_coder_handoff(self.root)
        self.assertIsInstance(result, CoderHandoff)

    def test_content_is_string(self) -> None:
        result = build_coder_handoff(self.root)
        self.assertIsInstance(result.content, str)

    def test_content_is_non_empty(self) -> None:
        result = build_coder_handoff(self.root)
        self.assertTrue(result.content.strip())

    def test_to_dict_is_json_serializable(self) -> None:
        result = build_coder_handoff(self.root)
        json.dumps(result.to_dict())

    def test_to_dict_has_content_key(self) -> None:
        result = build_coder_handoff(self.root)
        self.assertIn("content", result.to_dict())

    def test_accepts_project_status_directly(self) -> None:
        status = build_status(self.root)
        result = build_coder_handoff(status)
        self.assertIsInstance(result, CoderHandoff)

    def test_date_str_appears_in_title(self) -> None:
        result = build_coder_handoff(self.root, date_str="2026-05-29")
        self.assertIn("2026-05-29", result.content)

    def test_no_date_str_title_still_present(self) -> None:
        result = build_coder_handoff(self.root)
        self.assertIn("Handoff", result.content)


# ---------------------------------------------------------------------------
# Content: frontier, loop step, coding prompt, self-report
# ---------------------------------------------------------------------------

class HandoffContentTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _simple_project_with_coding_prompt(self.root)
        self.handoff = build_coder_handoff(self.root)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_frontier_slice_id_in_content(self) -> None:
        self.assertIn("M002-S01", self.handoff.content)

    def test_loop_step_in_content(self) -> None:
        # Step should be execute_coding_prompt since coding prompt exists but no self-report
        self.assertIn("execute_coding_prompt", self.handoff.content)

    def test_coding_prompt_path_in_content(self) -> None:
        self.assertIn("002_frutlups_m002_s01_next_thing.md", self.handoff.content)

    def test_self_report_path_in_content(self) -> None:
        self.assertIn("m002_s01_next_thing_self_report.md", self.handoff.content)

    def test_verification_commands_in_content(self) -> None:
        self.assertIn("python -m unittest discover", self.handoff.content)

    def test_workspace_guidance_in_content(self) -> None:
        self.assertIn("08_pkg", self.handoff.content)

    def test_required_reading_in_content(self) -> None:
        self.assertIn("CLAUDE.md", self.handoff.content)
        self.assertIn("README.md", self.handoff.content)


# ---------------------------------------------------------------------------
# Prompt-health warnings visible
# ---------------------------------------------------------------------------

class PromptHealthVisibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_roadmap(self.root)
        # Write a review prompt with no matching coding prompt (unmatched)
        (self.root / "prompts" / "for_review_agent" / "001_review_frutlups_m001_s01_foo.md").write_text(
            "# Review Prompt 001\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_unmatched_prompt_warning_visible(self) -> None:
        handoff = build_coder_handoff(self.root)
        # Prompt health warnings should appear in the handoff content
        self.assertIn("unmatched", handoff.content.lower())

    def test_prompt_health_section_present(self) -> None:
        handoff = build_coder_handoff(self.root)
        self.assertIn("Prompt", handoff.content)


# ---------------------------------------------------------------------------
# Role / provider neutrality
# ---------------------------------------------------------------------------

class ProviderNeutralityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_roadmap(self.root)
        self.handoff = build_coder_handoff(self.root)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_coder_role_is_logical(self) -> None:
        self.assertIn("logical: `coder`", self.handoff.content)

    def test_no_mandatory_provider_claim(self) -> None:
        # Should not say a specific provider IS the required coder
        content_lower = self.handoff.content.lower()
        # These phrases would indicate hard-coded provider requirements
        self.assertNotIn("must use anthropic", content_lower)
        self.assertNotIn("must use gpt", content_lower)
        self.assertNotIn("must use claude", content_lower)
        self.assertNotIn("only claude", content_lower)
        self.assertNotIn("only gpt", content_lower)

    def test_mentions_swappable_roles(self) -> None:
        self.assertIn("swapped roles", self.handoff.content)


# ---------------------------------------------------------------------------
# Memory / llloom posture
# ---------------------------------------------------------------------------

class MemoryPostureTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_roadmap(self.root)
        self.handoff = build_coder_handoff(self.root)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_llloom_marked_optional(self) -> None:
        self.assertIn("optional", self.handoff.content.lower())

    def test_llloom_read_only_for_normal_slices(self) -> None:
        self.assertIn("read-only", self.handoff.content.lower())

    def test_no_memory_mutation_instruction(self) -> None:
        content_lower = self.handoff.content.lower()
        # Should not give an affirmative instruction to mutate memory in a
        # normal slice. (We cannot assert the bare substring "mutate memory"
        # is absent, because the required prohibition "must not mutate memory"
        # legitimately contains it.)
        self.assertNotIn("you may mutate memory", content_lower)
        self.assertNotIn("mutate memory now", content_lower)
        # Should explicitly state memory must not be mutated.
        self.assertIn("must not mutate memory", content_lower)


# ---------------------------------------------------------------------------
# Read-only (no file creation)
# ---------------------------------------------------------------------------

class ReadOnlyTests(unittest.TestCase):
    def test_does_not_create_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            _write_active_roadmap(root)
            before = set(root.rglob("*"))
            build_coder_handoff(root)
            after = set(root.rglob("*"))
        self.assertEqual(before, after)

    def test_does_not_raise_on_minimal_project(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            _write_active_roadmap(root)
            try:
                build_coder_handoff(root)
            except Exception as exc:
                self.fail(f"build_coder_handoff raised: {exc}")

    def test_repeated_calls_produce_same_content(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            _write_active_roadmap(root)
            h1 = build_coder_handoff(root)
            h2 = build_coder_handoff(root)
        self.assertEqual(h1.content, h2.content)


class SingleSnapshotParityTests(unittest.TestCase):
    """Prompt 032: path, string, and prebuilt-status input render identical
    stable bytes from one selected snapshot."""

    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_roadmap(self.root)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_path_string_and_prebuilt_status_byte_parity(self) -> None:
        from_path = build_coder_handoff(self.root).content
        from_string = build_coder_handoff(str(self.root)).content
        from_status = build_coder_handoff(build_status(self.root)).content
        self.assertEqual(from_path, from_string)
        self.assertEqual(from_path, from_status)

    def test_repeated_calls_render_identical_bytes(self) -> None:
        self.assertEqual(
            build_coder_handoff(self.root).content,
            build_coder_handoff(self.root).content,
        )


if __name__ == "__main__":
    unittest.main()
