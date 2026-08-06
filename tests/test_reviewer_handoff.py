"""Tests for M011-S02: reviewer handoff generator.

Covers:
- a ready-for-review state renders the frontier, loop step, pairing paths
  (coding prompt, self-report, review prompt, expected review report, expected
  verdict record), workspace, and verification commands
- self-report-derived expected changed files and verification commands are
  visible in stable order
- prompt-health warnings are visible (not silently hidden)
- missing self-report and missing review prompt states are represented as
  artifact gaps, not ready-to-review states
- an existing review report is made visible
- role/provider neutrality is preserved (no required model family)
- memory is marked optional and read-only for normal review slices
- rendering is read-only and creates no files
- repeated calls produce identical content when date_str is omitted
- ReviewerHandoff.to_dict() is JSON-serializable
- package exports include the reviewer handoff API without breaking the coder API
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from frutlups.handoff import ReviewerHandoff, build_reviewer_handoff
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
        "### M001: Scaffold\n\nStatus: active\n\n",
        encoding="utf-8",
    )


def _write_detailed_roadmap(root: Path) -> None:
    (root / "03_experiments" / "development_roadmap_frutlups.md").write_text(
        "# Detailed Roadmap\n\n"
        "### M001: Scaffold\n\nSlices:\n\n- M001-S01: initial scaffold\n\n",
        encoding="utf-8",
    )


def _write_coding_prompt(root: Path) -> None:
    (root / "prompts" / "for_coding_agent"
     / "001_frutlups_m001_s01_initial_scaffold.md").write_text(
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


_CHANGED_FILES = (
    "08_pkg/src/frutlups/handoff.py",
    "08_pkg/tests/test_reviewer_handoff.py",
)
_VERIF_CMDS = (
    "python -m unittest discover -s tests",
    "python -m frutlups status ..",
)


def _write_valid_self_report(root: Path) -> None:
    lines = [
        "# Self-Report: M001-S01 initial scaffold",
        "",
        "## Files Changed",
        "",
    ]
    for f in _CHANGED_FILES:
        lines.append(f"- `{f}`")
    lines += [
        "",
        "## Behavior Implemented",
        "",
        "Implemented the initial scaffold.",
        "",
        "## Tests Added or Updated",
        "",
        "Added focused tests.",
        "",
        "## Verification Commands and Results",
        "",
        "```powershell",
    ]
    lines.extend(_VERIF_CMDS)
    lines += [
        "```",
        "",
        "Result: pass.",
        "",
        "## Live Status Summary",
        "",
        "Memory disabled.",
        "",
        "## Known Limits and Intentional Deferrals",
        "",
        "None.",
        "",
        "## Memory Usage Statement",
        "",
        "No memory backend was read or mutated.",
        "",
        "## Matching Review Prompt Path Created by the Coder",
        "",
        "`prompts/for_review_agent/001_review_frutlups_m001_s01_initial_scaffold.md`",
        "",
        "## Blockers or Open Questions",
        "",
        "None.",
        "",
    ]
    (root / "05_governance" / "reviews"
     / "m001_s01_initial_scaffold_self_report.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )


def _write_review_prompt(root: Path) -> None:
    (root / "prompts" / "for_review_agent"
     / "001_review_frutlups_m001_s01_initial_scaffold.md").write_text(
        "# Review Prompt 001: frutlups M001-S01 initial scaffold\n", encoding="utf-8"
    )


def _write_review_report(root: Path, verdict: str = "pass") -> None:
    (root / "05_governance" / "reviews"
     / "m001_s01_initial_scaffold_review_report.md").write_text(
        f"# Review\n\n## Verdict\n\n{verdict}\n", encoding="utf-8"
    )


def _ready_for_review(root: Path) -> None:
    """Coding prompt + valid self-report + matching review prompt, no report."""
    _make_template(root)
    _write_active_roadmap(root)
    _write_detailed_roadmap(root)
    _write_coding_prompt(root)
    _write_valid_self_report(root)
    _write_review_prompt(root)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class ConstructionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_roadmap(self.root)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_returns_reviewer_handoff(self) -> None:
        self.assertIsInstance(build_reviewer_handoff(self.root), ReviewerHandoff)

    def test_content_is_non_empty_string(self) -> None:
        h = build_reviewer_handoff(self.root)
        self.assertIsInstance(h.content, str)
        self.assertTrue(h.content.strip())

    def test_to_dict_is_json_serializable(self) -> None:
        json.dumps(build_reviewer_handoff(self.root).to_dict())

    def test_to_dict_has_content_key(self) -> None:
        self.assertIn("content", build_reviewer_handoff(self.root).to_dict())

    def test_accepts_project_status_directly(self) -> None:
        status = build_status(self.root)
        self.assertIsInstance(build_reviewer_handoff(status), ReviewerHandoff)

    def test_date_str_appears_in_title(self) -> None:
        h = build_reviewer_handoff(self.root, date_str="2026-05-29")
        self.assertIn("2026-05-29", h.content)

    def test_no_date_str_title_still_present(self) -> None:
        self.assertIn("Handoff", build_reviewer_handoff(self.root).content)

    def test_title_targets_reviewer(self) -> None:
        self.assertIn("Reviewer", build_reviewer_handoff(self.root).content)


# ---------------------------------------------------------------------------
# Ready-for-review content
# ---------------------------------------------------------------------------

class ReadyForReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _ready_for_review(self.root)
        self.handoff = build_reviewer_handoff(self.root)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_loop_step_is_execute_review_prompt(self) -> None:
        self.assertIn("execute_review_prompt", self.handoff.content)

    def test_review_readiness_marks_ready(self) -> None:
        self.assertIn("Ready for review", self.handoff.content)

    def test_frontier_slice_id(self) -> None:
        self.assertIn("M001-S01", self.handoff.content)

    def test_frontier_milestone_id(self) -> None:
        self.assertIn("M001", self.handoff.content)

    def test_coding_prompt_path(self) -> None:
        self.assertIn(
            "001_frutlups_m001_s01_initial_scaffold.md", self.handoff.content
        )

    def test_self_report_path(self) -> None:
        self.assertIn(
            "m001_s01_initial_scaffold_self_report.md", self.handoff.content
        )

    def test_review_prompt_path(self) -> None:
        self.assertIn(
            "001_review_frutlups_m001_s01_initial_scaffold.md",
            self.handoff.content,
        )

    def test_expected_review_report_path(self) -> None:
        self.assertIn(
            "m001_s01_initial_scaffold_review_report.md", self.handoff.content
        )

    def test_expected_verdict_record_path(self) -> None:
        self.assertIn(
            "m001_s01_initial_scaffold_verdict_record.md", self.handoff.content
        )

    def test_workspace_guidance(self) -> None:
        self.assertIn("08_pkg", self.handoff.content)

    def test_verification_baseline(self) -> None:
        self.assertIn("python -m unittest discover", self.handoff.content)

    def test_verdict_labels_present(self) -> None:
        for label in ("pass", "needs_work", "blocked", "override"):
            self.assertIn(label, self.handoff.content)

    def test_severity_guidance_present(self) -> None:
        self.assertIn("blocker", self.handoff.content)
        self.assertIn("major", self.handoff.content)


# ---------------------------------------------------------------------------
# Self-report-derived evidence
# ---------------------------------------------------------------------------

class EvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _ready_for_review(self.root)
        self.content = build_reviewer_handoff(self.root).content

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_expected_changed_files_visible(self) -> None:
        for f in _CHANGED_FILES:
            self.assertIn(f, self.content)

    def test_changed_files_in_stable_order(self) -> None:
        i0 = self.content.index(_CHANGED_FILES[0])
        i1 = self.content.index(_CHANGED_FILES[1])
        self.assertLess(i0, i1)

    def test_verification_commands_visible(self) -> None:
        for cmd in _VERIF_CMDS:
            self.assertIn(cmd, self.content)

    def test_verification_commands_in_stable_order(self) -> None:
        i0 = self.content.index(_VERIF_CMDS[0])
        i1 = self.content.index(_VERIF_CMDS[1])
        self.assertLess(i0, i1)


# ---------------------------------------------------------------------------
# Artifact-gap states
# ---------------------------------------------------------------------------

class MissingSelfReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_roadmap(self.root)
        _write_detailed_roadmap(self.root)
        _write_coding_prompt(self.root)
        # No self-report written.
        self.content = build_reviewer_handoff(self.root).content

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_not_ready_for_review(self) -> None:
        self.assertIn("Not ready", self.content)

    def test_loop_step_execute_coding_prompt(self) -> None:
        self.assertIn("execute_coding_prompt", self.content)

    def test_evidence_marked_unavailable(self) -> None:
        self.assertIn("not available yet", self.content.lower())

    def test_does_not_claim_ready(self) -> None:
        self.assertNotIn("Ready for review", self.content)


class MissingReviewPromptTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_roadmap(self.root)
        _write_detailed_roadmap(self.root)
        _write_coding_prompt(self.root)
        _write_valid_self_report(self.root)
        # No matching review prompt written.
        self.content = build_reviewer_handoff(self.root).content

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_loop_step_make_review_prompt(self) -> None:
        self.assertIn("make_review_prompt", self.content)

    def test_marked_artifact_gap(self) -> None:
        self.assertIn("Artifact gap", self.content)

    def test_mentions_review_prompt_gap(self) -> None:
        self.assertIn("review prompt", self.content.lower())


class ExistingReviewReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _ready_for_review(self.root)
        _write_review_report(self.root, "pass")  # report exists, no verdict record
        self.content = build_reviewer_handoff(self.root).content

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_existing_review_report_visible(self) -> None:
        self.assertIn("existing review report", self.content.lower())

    def test_warns_not_to_duplicate(self) -> None:
        self.assertIn("duplicate", self.content.lower())


# ---------------------------------------------------------------------------
# Prompt-health visibility
# ---------------------------------------------------------------------------

class PromptHealthVisibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_roadmap(self.root)
        # Unmatched review prompt (no coding counterpart).
        (self.root / "prompts" / "for_review_agent"
         / "001_review_frutlups_m001_s01_foo.md").write_text(
            "# Review Prompt 001\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_unmatched_warning_visible(self) -> None:
        self.assertIn("unmatched", build_reviewer_handoff(self.root).content.lower())


# ---------------------------------------------------------------------------
# Role / provider neutrality
# ---------------------------------------------------------------------------

class NeutralityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_roadmap(self.root)
        self.content = build_reviewer_handoff(self.root).content

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_reviewer_role_is_logical(self) -> None:
        self.assertIn("logical: `reviewer`", self.content)

    def test_no_required_model_family(self) -> None:
        low = self.content.lower()
        self.assertIn("no provider or model family is required", low)

    def test_no_mandatory_provider_claim(self) -> None:
        low = self.content.lower()
        self.assertNotIn("must use claude", low)
        self.assertNotIn("must use gpt", low)
        self.assertNotIn("only claude", low)
        self.assertNotIn("only gpt", low)

    def test_mentions_swapped_roles(self) -> None:
        self.assertIn("swapped roles", self.content)


# ---------------------------------------------------------------------------
# Memory posture
# ---------------------------------------------------------------------------

class MemoryPostureTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_roadmap(self.root)
        self.content = build_reviewer_handoff(self.root).content

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_optional(self) -> None:
        self.assertIn("optional", self.content.lower())

    def test_read_only(self) -> None:
        self.assertIn("read-only", self.content.lower())

    def test_must_not_mutate(self) -> None:
        self.assertIn("must not mutate memory", self.content.lower())

    def test_no_affirmative_mutation(self) -> None:
        low = self.content.lower()
        self.assertNotIn("you may mutate memory", low)
        self.assertNotIn("mutate memory now", low)


# ---------------------------------------------------------------------------
# Read-only / determinism
# ---------------------------------------------------------------------------

class ReadOnlyTests(unittest.TestCase):
    def test_does_not_create_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _ready_for_review(root)
            before = set(root.rglob("*"))
            build_reviewer_handoff(root)
            after = set(root.rglob("*"))
        self.assertEqual(before, after)

    def test_repeated_calls_same_content(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _ready_for_review(root)
            h1 = build_reviewer_handoff(root)
            h2 = build_reviewer_handoff(root)
        self.assertEqual(h1.content, h2.content)

    def test_does_not_raise_on_minimal_project(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            _write_active_roadmap(root)
            try:
                build_reviewer_handoff(root)
            except Exception as exc:  # pragma: no cover - failure path
                self.fail(f"build_reviewer_handoff raised: {exc}")


# ---------------------------------------------------------------------------
# Package exports
# ---------------------------------------------------------------------------

class ExportTests(unittest.TestCase):
    def test_reviewer_api_exported(self) -> None:
        import frutlups

        self.assertTrue(hasattr(frutlups, "build_reviewer_handoff"))
        self.assertTrue(hasattr(frutlups, "ReviewerHandoff"))

    def test_coder_api_still_exported(self) -> None:
        import frutlups

        self.assertTrue(hasattr(frutlups, "build_coder_handoff"))
        self.assertTrue(hasattr(frutlups, "CoderHandoff"))


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
        from_path = build_reviewer_handoff(self.root).content
        from_string = build_reviewer_handoff(str(self.root)).content
        from_status = build_reviewer_handoff(build_status(self.root)).content
        self.assertEqual(from_path, from_string)
        self.assertEqual(from_path, from_status)

    def test_repeated_calls_render_identical_bytes(self) -> None:
        self.assertEqual(
            build_reviewer_handoff(self.root).content,
            build_reviewer_handoff(self.root).content,
        )


if __name__ == "__main__":
    unittest.main()
