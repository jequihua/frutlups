"""Tests for M003-S03: configured prompt templates and required sections.

Selected v2/template-v3 profiles render coding and review prompts through the
configured scaffold and the heading-aware ``TBD`` slot renderer; genuine
legacy/no-config profiles keep the hard-coded renderer byte-for-byte. The
accepted scaffolds shipped under ``fixtures/front_repo_contract/`` are the
inputs; mutations happen on copies in temporary directories.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import yaml

import frutlups
import frutlups._scaffold as scaffold_module
import frutlups.project as project_module
from frutlups.cli import main
from frutlups.layout import load_config_file, profile_from_config, v2_default_profile
from frutlups.project import (
    _render_review_from_scaffold,
    _review_read_first_values,
    build_coding_prompt_plan,
    build_review_prompt_plan,
    build_status,
    _parse_coding_prompt_meta,
)
from frutlups.prompt_template import render_coding_prompt
from frutlups.prompts import PromptArtifact, PromptKind
from frutlups.review_prompt_template import ReviewPromptTemplate

# The accepted coding/review scaffold bytes are shipped as immutable, package-relative
# fixtures so this suite runs from the flattened front-facing checkout without reading
# above its own ``tests/`` tree. See ``fixtures/front_repo_contract/manifest.json``.
_TEMPLATES = Path(__file__).resolve().parent / "fixtures" / "front_repo_contract"

_V2_STATE = "# Project State\n\nMemory mode:\n- none\n\nFrutlups mode:\n- manual\n"


def _write_scaffolds(root: Path, *, coding: str | None = None, review: str | None = None) -> None:
    target = root / "prompts" / "templates"
    target.mkdir(parents=True, exist_ok=True)
    (target / "coding_prompt.md").write_text(
        coding
        if coding is not None
        else (_TEMPLATES / "coding_prompt.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (target / "review_prompt.md").write_text(
        review
        if review is not None
        else (_TEMPLATES / "review_prompt.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )


def _make_v2_project(root: Path, *, scaffolds: bool = True) -> None:
    for name in (
        "00_brief",
        "03_experiments",
        "05_governance/reviews",
        "prompts/for_coding_agent",
        "prompts/for_review_agent",
        "questions",
    ):
        (root / name).mkdir(parents=True, exist_ok=True)
    if scaffolds:
        _write_scaffolds(root)
    (root / "frutlups.layout.yaml").write_text(
        "schema_version: frutlups_layout_config_v0\n"
        "profile_id: artifact_first_template_v2\n"
        "automation_boundary:\n"
        "  runner_implemented: true\n",
        encoding="utf-8",
    )
    (root / "PROJECT_STATE.md").write_text(_V2_STATE, encoding="utf-8")
    (root / "03_experiments" / "active_roadmap.md").write_text(
        "### M001: First\n\nStatus: active\n", encoding="utf-8"
    )
    (root / "03_experiments" / "development_roadmap.md").write_text(
        "### M001: First\n\nSlices:\n\n- M001-S01: first slice\n", encoding="utf-8"
    )


def _make_project_derived_v2_project(
    root: Path,
    *,
    coder_review: str = "only when the active prompt or project convention explicitly allows it",
) -> None:
    """A configured project with non-default paths and authored milestone fields."""

    _make_v2_project(root)
    (root / "state").mkdir()
    (root / "state" / "CURRENT.md").write_text(_V2_STATE, encoding="utf-8")
    (root / "planning").mkdir()
    (root / "planning" / "live_plan.md").write_text(
        "### M001: Acme Compiler\n\n"
        "Status: active\n\n"
        "Objective:\n"
        "Build the Acme compiler from the project's own contracts.\n\n"
        "Active workspaces:\n"
        "- `08_pkg`, `09_ops`.\n\n"
        "Non-goals:\n"
        "- Do not add a dashboard.\n"
        "- Do not use the network.\n\n"
        "Verification/evidence:\n"
        "- run the Acme product suite;\n"
        "- inspect `evidence/report.json`.\n\n"
        "Done when:\n"
        "- the Acme compiler behavior is implemented.\n",
        encoding="utf-8",
    )
    (root / "planning" / "design_plan.md").write_text(
        "### M001: Acme Compiler\n\n"
        "Slices:\n\n"
        "- M001-S01: ship the Acme compiler\n",
        encoding="utf-8",
    )
    for workspace in ("08_pkg", "09_ops"):
        (root / workspace).mkdir(exist_ok=True)
        (root / workspace / "GUIDE.md").write_text(
            f"# {workspace} orientation\n", encoding="utf-8"
        )
    (root / "frutlups.layout.yaml").write_text(
        "schema_version: frutlups_layout_config_v0\n"
        "profile_id: artifact_first_template_v3\n"
        "workspace_map:\n"
        "  context_filename: GUIDE.md\n"
        "state:\n"
        "  canonical_file: state/CURRENT.md\n"
        "roadmaps:\n"
        "  directory: planning\n"
        "  active_roadmap_glob: live*.md\n"
        "  development_roadmap_glob: design*.md\n"
        "prompts:\n"
        f"  coder_may_create_review_prompt: {coder_review}\n"
        "automation_boundary:\n"
        "  runner_implemented: true\n",
        encoding="utf-8",
    )


def _make_legacy_project(root: Path) -> None:
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
    (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
        "### M001: First\n\nStatus: active\n", encoding="utf-8"
    )
    (root / "03_experiments" / "development_roadmap_frutlups.md").write_text(
        "### M001: First\n\nSlices:\n\n- M001-S01: first slice\n", encoding="utf-8"
    )


def _headings(text: str) -> list[str]:
    headings = []
    in_fence = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence and line.startswith("## "):
            headings.append(line[3:].strip())
    return headings


def _review_template() -> ReviewPromptTemplate:
    return ReviewPromptTemplate(
        sequence=1,
        milestone_id="M001",
        slice_id="M001-S01",
        slug="first_slice",
        title="First Slice",
        role_instructions="You are the reviewer.",
        required_reading=("CLAUDE.md", "README.md"),
        coding_prompt_path="prompts/for_coding_agent/001_first_slice.md",
        self_report_path="05_governance/reviews/m001_s01_first_slice_self_report.md",
        review_output_path="05_governance/reviews/m001_s01_first_slice_review_report.md",
        expected_changed_files=("08_pkg/src/frutlups/x.py",),
        verification_commands=("python -m unittest discover -s tests",),
        severity_guidance=("blocker: correctness",),
        verdict_choices=("pass", "needs_work", "blocked", "override"),
        prior_review_paths=(),
        non_goals=("do not X",),
        notes=(),
    )


class ConfiguredSelectionTests(unittest.TestCase):
    def test_v2_plan_renders_through_configured_scaffold_once(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            real_read = scaffold_module._read_scaffold
            with (
                mock.patch.object(
                    scaffold_module, "_read_scaffold", side_effect=real_read
                ) as read_spy,
                mock.patch(
                    "frutlups.project.load_layout_profile",
                    side_effect=project_module.load_layout_profile,
                ) as layout_spy,
            ):
                plan = build_coding_prompt_plan(root)
            self.assertTrue(plan.valid, plan.errors)
            self.assertTrue(plan.render.content.startswith("# Coding Prompt Template"))
            self.assertEqual(read_spy.call_count, 1)
            self.assertEqual(layout_spy.call_count, 1)

    def test_missing_scaffold_fails_closed_without_writer(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, scaffolds=False)
            with mock.patch(
                "frutlups.cli.write_coding_prompt",
                side_effect=AssertionError("writer reached on scaffold failure"),
            ):
                plan = build_coding_prompt_plan(root)
            self.assertFalse(plan.valid)
            self.assertIn("configured coding template is missing or not a file", plan.errors)
            self.assertEqual(plan.render.content, "")

    def test_scaffold_change_after_first_read_revalidates_next_plan(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            first = build_coding_prompt_plan(root)
            self.assertTrue(first.valid, first.errors)
            (root / "prompts" / "templates" / "coding_prompt.md").write_text(
                "## Task\n\nTBD\n", encoding="utf-8"
            )
            second = build_coding_prompt_plan(root)
            self.assertFalse(second.valid)

    def test_legacy_profile_keeps_hardcoded_renderer_byte_for_byte(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_legacy_project(root)
            with mock.patch.object(
                scaffold_module,
                "_read_scaffold",
                side_effect=AssertionError("scaffold read on legacy profile"),
            ):
                plan = build_coding_prompt_plan(root)
            self.assertTrue(plan.valid, plan.errors)
            direct = render_coding_prompt(plan.template)
            self.assertTrue(direct.valid)
            # Q008 keeps the direct public call byte-stable while the composer
            # deliberately supplies the reader-derived report contract.
            self.assertNotEqual(plan.render.content, direct.content)
            for line in project_module.self_report_format_contract(
                project_module.self_report_schema_for_profile(build_status(root).layout.profile)
            ):
                self.assertIn(f"- {line}", plan.render.content)
                self.assertNotIn(f"- {line}", direct.content)
            self.assertTrue(plan.render.content.startswith("# Coding Prompt 001:"))


class HeadingContractTests(unittest.TestCase):
    _CODING_REQUIRED = (
        "Current State",
        "Active Workspaces",
        "Read First",
        "Task",
        "Non-Goals",
        "Verification",
        "Self-Report",
        "Definition Of Done",
    )
    _REVIEW_REQUIRED = (
        "Review Objective",
        "Read First",
        "Review Checks",
        "Verification",
        "Output",
        "Non-Goals",
        "Definition Of Done",
    )

    def test_shipped_scaffolds_satisfy_live_contracts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            coding_plan = build_coding_prompt_plan(root)
            self.assertTrue(coding_plan.valid, coding_plan.errors)
            coding_headings = _headings(coding_plan.render.content)
            self.assertEqual(
                [h for h in coding_headings if h in self._CODING_REQUIRED],
                list(self._CODING_REQUIRED),
            )
            for name in self._CODING_REQUIRED:
                self.assertEqual(coding_headings.count(name), 1, name)
            self.assertIn("Implementation Discipline", coding_headings)
            self.assertIn("OKF Authoring", coding_headings)

            status = build_status(root)
            review_render = _render_review_from_scaffold(
                status, status.layout.profile, _review_template()
            )
            self.assertTrue(review_render.valid, review_render.errors)
            review_headings = _headings(review_render.content)
            self.assertEqual(
                [h for h in review_headings if h in self._REVIEW_REQUIRED],
                list(self._REVIEW_REQUIRED),
            )
            for name in self._REVIEW_REQUIRED:
                self.assertEqual(review_headings.count(name), 1, name)

    def _assert_scaffold_refusal(self, coding_text: str, marker: str) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            (root / "prompts" / "templates" / "coding_prompt.md").write_text(
                coding_text, encoding="utf-8"
            )
            plan = build_coding_prompt_plan(root)
        self.assertFalse(plan.valid)
        self.assertEqual(plan.render.content, "")
        self.assertTrue(any(marker in e for e in plan.errors), plan.errors)

    def test_missing_required_section_refused(self) -> None:
        scaffold = (_TEMPLATES / "coding_prompt.md").read_text(encoding="utf-8")
        self._assert_scaffold_refusal(
            scaffold.replace("## Non-Goals", "## Things Not To Do"),
            "required section 5 is missing",
        )

    def test_duplicate_required_section_refused(self) -> None:
        scaffold = (_TEMPLATES / "coding_prompt.md").read_text(encoding="utf-8")
        self._assert_scaffold_refusal(
            scaffold + "\n## Task\n\nMore.\n", "required section 4 appears more than once"
        )

    def test_out_of_order_required_section_refused(self) -> None:
        scaffold = (_TEMPLATES / "coding_prompt.md").read_text(encoding="utf-8")
        task = scaffold.index("## Task")
        verification = scaffold.index("## Verification")
        block = scaffold[task:verification]
        rest = scaffold[verification:]
        moved = scaffold[:task] + rest.replace(
            "## Self-Report", block + "## Self-Report", 1
        )
        self._assert_scaffold_refusal(moved, "out of configured order")

    def test_heading_inside_fence_is_not_a_section(self) -> None:
        scaffold = (_TEMPLATES / "coding_prompt.md").read_text(encoding="utf-8")
        # Move the only Non-Goals heading inside a fenced block.
        scaffold = scaffold.replace("## Non-Goals", "```\n## Non-Goals\n```", 1)
        self._assert_scaffold_refusal(scaffold, "required section 5 is missing")

    def test_empty_required_section_config_fails_closed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            status = build_status(root)
            profile = dataclasses.replace(
                status.layout.profile, required_coding_prompt_sections=()
            )
            content, errors = scaffold_module.render_configured_scaffold(
                root=status.root,
                template_rel=profile.coding_template,
                required_sections=profile.required_coding_prompt_sections,
                workflow_values=(("milestone", "M001"), ("slice", "M001-S01")),
                section_slots={},
                owner="coding",
            )
        self.assertEqual(content, "")
        self.assertIn("required-section configuration is empty or malformed", errors)


class SlotSubstitutionTests(unittest.TestCase):
    def _coding_content(self) -> str:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            plan = build_coding_prompt_plan(root)
            return plan.render.content

    def test_workflow_identity_and_single_region(self) -> None:
        content = self._coding_content()
        self.assertIn("milestone: M001\n", content)
        self.assertIn("slice: M001-S01\n", content)
        self.assertEqual(content.count("```yaml"), 1)
        self.assertNotIn("TBD", content)

    def test_exact_list_and_prose_slots(self) -> None:
        content = self._coding_content()
        self.assertIn("## Active Workspaces\n\n- 08_pkg/\n", content)
        self.assertIn("- CLAUDE.md\n", content)
        self.assertIn(
            "## Task\n\nImplement M001-S01: first slice.\n\nYou are the coding agent for this project.",
            content,
        )
        self.assertIn(
            "- Do not implement future milestones or unrelated behavior.", content
        )
        self.assertIn("- python -m unittest discover -s tests\n", content)
        self.assertIn(
            "## Self-Report\n\nWrite a self-report at:\n\n"
            "`05_governance/reviews/m001_s01_first_slice_self_report.md`",
            content,
        )
        self.assertIn("- All required behavior is implemented and tested.", content)

    def test_static_scaffold_bytes_and_line_endings_preserved(self) -> None:
        content = self._coding_content()
        self.assertIn("Follow YAGNI as defined in `CLAUDE.md`", content)
        self.assertIn("Do not create a commit unless this prompt explicitly instructs it", content)
        self.assertNotIn("\r", content)
        self.assertTrue(content.endswith("\n") and not content.endswith("\n\n"))

    def test_round_trip_through_accepted_reader(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            plan = build_coding_prompt_plan(root)
            target = root / "prompts" / "for_coding_agent" / "001_first_slice.md"
            target.write_text(plan.render.content, encoding="utf-8")
            artifact = PromptArtifact(
                kind=PromptKind.CODING, path=target, filename="001_first_slice.md", sequence=1
            )
            profile = build_status(root).layout.profile
            meta = _parse_coding_prompt_meta(artifact, root, profile)
        self.assertTrue(meta.valid, meta.errors)
        self.assertEqual(meta.milestone_id, plan.template.milestone_id)
        self.assertEqual(meta.slice_id, plan.template.slice_id)
        self.assertEqual(meta.self_report_path, plan.template.self_report_path)


class ProjectDerivedPromptTests(unittest.TestCase):
    def test_configured_coding_prompt_uses_project_authorities(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project_derived_v2_project(root)
            plan = build_coding_prompt_plan(root)

        self.assertTrue(plan.valid, plan.errors)
        self.assertEqual(
            plan.template.required_reading,
            (
                "CLAUDE.md",
                "README.md",
                "state/CURRENT.md",
                "planning/live_plan.md",
                "planning/design_plan.md",
                "08_pkg/GUIDE.md",
                "09_ops/GUIDE.md",
            ),
        )
        self.assertEqual(plan.template.scope_paths, ("08_pkg", "09_ops"))
        self.assertEqual(
            plan.template.non_goals,
            ("Do not add a dashboard.", "Do not use the network."),
        )
        self.assertEqual(
            plan.template.verification_commands,
            ("run the Acme product suite;", "inspect `evidence/report.json`."),
        )
        self.assertIn("ship the Acme compiler", plan.render.content)
        self.assertIn(
            "Build the Acme compiler from the project's own contracts.",
            plan.render.content,
        )
        self.assertIn("You are the coding agent for this project.", plan.render.content)
        self.assertNotIn("coding agent for `frutlups`", plan.render.content)
        self.assertNotIn("local-first, artifact-first", plan.render.content)
        self.assertNotIn("python -m compileall", plan.render.content)
        self.assertNotIn("Matching review prompt is created.", plan.render.content)

    def test_explicit_boolean_policy_allows_review_prompt_dod(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project_derived_v2_project(root, coder_review="true")
            plan = build_coding_prompt_plan(root)

        self.assertTrue(plan.valid, plan.errors)
        self.assertIn("Matching review prompt is created.", plan.render.content)

class ReviewScaffoldTests(unittest.TestCase):
    def test_review_slots_and_single_region(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            status = build_status(root)
            render = _render_review_from_scaffold(
                status, status.layout.profile, _review_template()
            )
        self.assertTrue(render.valid, render.errors)
        content = render.content
        self.assertIn("milestone: M001\n", content)
        self.assertIn("slice: M001-S01\n", content)
        self.assertEqual(content.count("```yaml"), 1)
        self.assertIn(
            "## Review Objective\n\nReview M001-S01: First Slice.\n\nYou are the reviewer.\n",
            content,
        )
        self.assertIn(
            "- coding prompt under review: `prompts/for_coding_agent/001_first_slice.md`",
            content,
        )
        self.assertIn("- coder self-report: `05_governance/reviews/m001_s01_first_slice_self_report.md`", content)
        self.assertIn("- 08_pkg/src/frutlups/x.py", content)
        self.assertIn("- python -m unittest discover -s tests\n", content)
        self.assertIn("- do not X\n", content)
        self.assertIn(
            "- Write the review report at `05_governance/reviews/m001_s01_first_slice_review_report.md`.",
            content,
        )
        self.assertNotIn(
            "- Use exactly one verdict value from: pass, needs_work, blocked, override.",
            content,
        )
        for line in project_module.review_report_format_contract():
            self.assertIn(f"- {line}", content)
        self.assertNotIn("TBD", content)


class SlotRefusalTests(unittest.TestCase):
    def _assert_refusal(self, coding_text: str, marker: str) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            (root / "prompts" / "templates" / "coding_prompt.md").write_text(
                coding_text, encoding="utf-8"
            )
            plan = build_coding_prompt_plan(root)
        self.assertFalse(plan.valid)
        self.assertEqual(plan.render.content, "")
        self.assertTrue(any(marker in e for e in plan.errors), plan.errors)

    def test_missing_section_slot(self) -> None:
        scaffold = (_TEMPLATES / "coding_prompt.md").read_text(encoding="utf-8")
        self._assert_refusal(
            scaffold.replace("## Non-Goals\n\n- TBD", "## Non-Goals", 1),
            "expected slot missing in section 'non-goals'",
        )

    def test_duplicate_section_slot(self) -> None:
        scaffold = (_TEMPLATES / "coding_prompt.md").read_text(encoding="utf-8")
        self._assert_refusal(
            scaffold.replace("## Non-Goals\n\n- TBD", "## Non-Goals\n\n- TBD\n- TBD", 1),
            "duplicate slot in section 'non-goals'",
        )

    def test_unconsumed_tbd_slot(self) -> None:
        scaffold = (_TEMPLATES / "coding_prompt.md").read_text(encoding="utf-8")
        self._assert_refusal(
            scaffold.replace("## Current State", "## Current State\n\n- TBD", 1),
            "unconsumed TBD placeholder remains after rendering",
        )

    def test_missing_workflow_slot(self) -> None:
        scaffold = (_TEMPLATES / "coding_prompt.md").read_text(encoding="utf-8")
        self._assert_refusal(
            scaffold.replace("milestone: TBD", "milestone: M001", 1),
            "workflow metadata slot 'milestone' is missing",
        )

    def test_leading_frame_scaffold_refused(self) -> None:
        scaffold = (_TEMPLATES / "coding_prompt.md").read_text(encoding="utf-8")
        self._assert_refusal(
            "---\nmilestone: M001\nslice: S01\n---\n\n" + scaffold,
            "must not open a leading metadata frame",
        )

    def test_missing_fenced_block_refused(self) -> None:
        scaffold = (_TEMPLATES / "coding_prompt.md").read_text(encoding="utf-8")
        start = scaffold.index("```yaml")
        end = scaffold.index("```", start + 3) + 3
        self._assert_refusal(
            scaffold[:start] + scaffold[end:], "no fenced yaml workflow block"
        )


class PathSafetyTests(unittest.TestCase):
    def _plan_with_template_path(self, path: str) -> object:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            config = (
                "schema_version: frutlups_layout_config_v0\n"
                "profile_id: artifact_first_template_v2\n"
                "prompts:\n"
                f'  coding_template: "{path}"\n'
                "  required_coding_prompt_sections:\n"
                "    - Read First\n"
            )
            (root / "frutlups.layout.yaml").write_text(config, encoding="utf-8")
            return build_coding_prompt_plan(root)

    def test_absolute_template_path_refused(self) -> None:
        plan = self._plan_with_template_path("C:/elsewhere/template.md")
        self.assertFalse(plan.valid)
        self.assertTrue(any("not a safe repo-relative path" in e for e in plan.errors))

    def test_escaping_template_path_refused(self) -> None:
        plan = self._plan_with_template_path("../outside/template.md")
        self.assertFalse(plan.valid)
        self.assertTrue(any("not a safe repo-relative path" in e for e in plan.errors))

    def test_directory_as_template_refused(self) -> None:
        plan = self._plan_with_template_path("prompts/templates")
        self.assertFalse(plan.valid)
        self.assertTrue(any("missing or not a file" in e for e in plan.errors))

    def test_invalid_utf8_template_refused(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            (root / "prompts" / "templates" / "coding_prompt.md").write_bytes(b"# x\n\xff\xfe\n")
            plan = build_coding_prompt_plan(root)
        self.assertFalse(plan.valid)
        self.assertTrue(any("not valid UTF-8" in e for e in plan.errors))

    def test_symlink_escape_refused(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            _make_v2_project(root)
            outside = Path(tmp) / "outside_templates"
            outside.mkdir()
            (outside / "coding_prompt.md").write_text("# x\n", encoding="utf-8")
            templates_dir = root / "prompts" / "templates"
            for stale in templates_dir.iterdir():
                stale.unlink()
            templates_dir.rmdir()
            try:
                os.symlink(outside, templates_dir, target_is_directory=True)
            except OSError:
                # File symlinks need privilege on Windows; directory junctions
                # do not. Both exercise the same resolved-outside containment.
                result = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(templates_dir), str(outside)],
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            plan = build_coding_prompt_plan(root)
        self.assertFalse(plan.valid)
        self.assertTrue(any("resolves outside the project root" in e for e in plan.errors))

    def test_mutating_cli_refusal_leaves_filesystem_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, scaffolds=False)
            before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
            with mock.patch(
                "frutlups.cli.write_coding_prompt",
                side_effect=AssertionError("writer reached on scaffold failure"),
            ):
                from contextlib import redirect_stderr, redirect_stdout
                from io import StringIO

                out, err = StringIO(), StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    code = main(["make-coding-prompt", str(root)])
            after = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
        self.assertEqual(code, 1)
        self.assertIn("configured coding template is missing or not a file", err.getvalue())
        self.assertEqual(before, after)


class HostileDataTests(unittest.TestCase):
    def _plan_with_non_goals(self, non_goals: tuple[str, ...]) -> object:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            status = build_status(root)
            profile = status.layout.profile
            plan = build_coding_prompt_plan(root)
            template = dataclasses.replace(plan.template, non_goals=non_goals)
            return project_module._render_coding_from_scaffold(status, profile, template)

    def test_tbd_inside_inserted_value_is_inert(self) -> None:
        render = self._plan_with_non_goals(("Do not touch the TBD marker.",))
        self.assertTrue(render.valid, render.errors)
        self.assertIn("- Do not touch the TBD marker.", render.content)

    def test_heading_injection_refused(self) -> None:
        render = self._plan_with_non_goals(("line one\n## Non-Goals\nmore",))
        self.assertFalse(render.valid)
        self.assertIn("headings differ", render.errors[0])

    def test_unbalanced_fence_injection_fails_closed(self) -> None:
        # A value carrying a fence line is refused at value validation before
        # substitution; the contract scanner is never fooled.
        render = self._plan_with_non_goals(
            ("```yaml\nmilestone: M999\nslice: S99\n```",)
        )
        self.assertFalse(render.valid)
        self.assertIn("inserted value would alter document structure", render.errors[0])

    def test_repeated_render_is_pure(self) -> None:
        constructors_before = dict(yaml.SafeLoader.yaml_constructors)
        recursion_before = sys.getrecursionlimit()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            scaffold_bytes = (root / "prompts" / "templates" / "coding_prompt.md").read_bytes()
            first = build_coding_prompt_plan(root)
            second = build_coding_prompt_plan(root)
            self.assertEqual(first.render.content, second.render.content)
            self.assertEqual(
                (root / "prompts" / "templates" / "coding_prompt.md").read_bytes(),
                scaffold_bytes,
            )
        self.assertEqual(dict(yaml.SafeLoader.yaml_constructors), constructors_before)
        self.assertEqual(sys.getrecursionlimit(), recursion_before)


class PublicSurfaceTests(unittest.TestCase):
    def test_exports_and_current_verbs(self) -> None:
        self.assertEqual(len(frutlups.__all__), 152)
        self.assertFalse(hasattr(frutlups, "render_configured_scaffold"))
        import argparse

        from frutlups.cli import _build_parser

        subparsers = next(
            action
            for action in _build_parser()._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        self.assertEqual(len(subparsers.choices), 9)


# ---------------------------------------------------------------------------
# M003-S03 correction (prompt 020): F1-F6 durable regression coverage.
# ---------------------------------------------------------------------------

_SR_HEADINGS = (
    "Intent:", "Files Changed:", "Behavior Implemented:", "Tests Added Or Updated:",
    "Verification Run:", "Definition Of Done Audit:", "Non-Goals Confirmed:", "Memory Used:",
    "Memory Update Requested:", "Known Limits / Follow-Up:", "Recommended Next Move:",
)


def _make_v2_review_project(root: Path) -> None:
    """A v2 project whose loop step is make_review_prompt with a valid report."""

    _make_v2_project(root)
    plan = build_coding_prompt_plan(root)
    (root / "prompts" / "for_coding_agent" / "001_frutlups_m001_s01_first_slice.md").write_text(
        plan.render.content, encoding="utf-8"
    )
    report = root / plan.template.self_report_path
    report.parent.mkdir(parents=True, exist_ok=True)
    parts = [
        f"{heading}\n\n{'- 08_pkg/src/frutlups/x.py' if heading == 'Files Changed:' else 'x'}\n"
        for heading in _SR_HEADINGS
    ]
    report.write_text("# Coder Self-Report\n\n" + "\n".join(parts), encoding="utf-8")


class ProjectDerivedReviewPromptTests(unittest.TestCase):
    def test_make_review_prompt_keeps_neutral_identity_and_derived_reading(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project_derived_v2_project(root)
            coding = build_coding_prompt_plan(root)
            self.assertTrue(coding.valid, coding.errors)
            (root / coding.preview.target_path).write_text(
                coding.render.content, encoding="utf-8"
            )
            report = root / coding.template.self_report_path
            report.parent.mkdir(parents=True, exist_ok=True)
            parts = [
                f"{heading}\n\n"
                + (
                    "- 08_pkg/src/acme.py\n"
                    if heading == "Files Changed:"
                    else "run the Acme product suite\n"
                    if heading == "Verification Run:"
                    else "x\n"
                )
                for heading in _SR_HEADINGS
            ]
            report.write_text(
                "# Coder Self-Report\n\n" + "\n".join(parts), encoding="utf-8"
            )
            review = build_review_prompt_plan(root)

        self.assertTrue(review.valid, review.errors)
        self.assertIn("You are the reviewer for this project.", review.render.content)
        self.assertNotIn("reviewer for `frutlups`", review.render.content)
        for reading in (
            "state/CURRENT.md",
            "planning/live_plan.md",
            "planning/design_plan.md",
            "08_pkg/GUIDE.md",
            "09_ops/GUIDE.md",
        ):
            self.assertIn(f"`{reading}`", review.render.content)


def _snapshot(root: Path) -> dict[str, str]:
    import hashlib

    return {
        str(path.relative_to(root)): (
            "dir" if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest()
        )
        for path in sorted(root.rglob("*"))
    }


class ConfiguredWriterByteTests(unittest.TestCase):
    """F1: actual configured writes persist the validated plan bytes."""

    def test_cli_review_write_is_byte_exact_and_never_rerenders(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_review_project(root)
            plan = build_review_prompt_plan(root)
            real_read = scaffold_module._read_scaffold
            with (
                mock.patch(
                    "frutlups.review_prompt_template.render_review_prompt",
                    side_effect=AssertionError("hard-coded render after planning"),
                ),
                mock.patch.object(
                    scaffold_module, "_read_scaffold", side_effect=real_read
                ) as read_spy,
            ):
                from contextlib import redirect_stderr, redirect_stdout
                from io import StringIO

                out, err = StringIO(), StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    code = main(["make-review-prompt", str(root)])
            written = next((root / "prompts" / "for_review_agent").glob("*.md")).read_bytes()
        self.assertEqual(code, 0, err.getvalue())
        # Exactly one scaffold read in the invocation (planning); none in the write.
        self.assertEqual(read_spy.call_count, 1)
        self.assertEqual(written, plan.render.content.encode("utf-8"))
        self.assertTrue(written.startswith(b"# Review Prompt Template"))

    def test_orchestrator_review_dispatch_is_byte_exact(self) -> None:
        from frutlups.orchestrator import run_one_step

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_review_project(root)
            plan = build_review_prompt_plan(root)
            result = run_one_step(root, journal=False)
            written = next((root / "prompts" / "for_review_agent").glob("*.md")).read_bytes()
        self.assertTrue(result.wrote, result.refusal_reason)
        self.assertEqual(written, plan.render.content.encode("utf-8"))

    def test_public_review_writer_keeps_legacy_rendering_through_same_core(self) -> None:
        from frutlups.review_prompt_template import (
            ReviewPromptWriteCommand,
            render_review_prompt,
            write_review_prompt,
        )
        from test_layout import _review_template as legacy_template

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = ReviewPromptWriteCommand(project_root=root, template=legacy_template())
            result = write_review_prompt(command)
            written = next((root / "prompts" / "for_review_agent").glob("*.md")).read_bytes()
            self.assertTrue(result.wrote, result.errors)
            self.assertEqual(written, render_review_prompt(legacy_template()).content.encode("utf-8"))
            # Overwrite protection still refuses without overwrite=True.
            second = write_review_prompt(command)
            self.assertFalse(second.wrote)
            self.assertTrue(any("already exists" in e for e in second.errors))

    def test_cli_coding_write_is_byte_exact(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            plan = build_coding_prompt_plan(root)
            from contextlib import redirect_stderr, redirect_stdout
            from io import StringIO

            out, err = StringIO(), StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = main(["make-coding-prompt", str(root)])
            written = next((root / "prompts" / "for_coding_agent").glob("*.md")).read_bytes()
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual(written, plan.render.content.encode("utf-8"))


class InvalidPreviewTruthTests(unittest.TestCase):
    """F2: invalid configured-render previews tell the truth."""

    def test_coding_invalid_preview_shape_and_no_write(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, scaffolds=False)
            plan = build_coding_prompt_plan(root)
        self.assertFalse(plan.valid)
        self.assertFalse(plan.preview.valid)
        self.assertFalse(plan.preview.would_write)
        self.assertFalse(plan.preview.wrote)
        self.assertTrue(any("missing or not a file" in e for e in plan.preview.errors))
        self.assertEqual(plan.render.content, "")

    def test_review_invalid_preview_shape_and_no_write(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_review_project(root)
            (root / "prompts" / "templates" / "review_prompt.md").unlink()
            plan = build_review_prompt_plan(root)
        self.assertFalse(plan.valid)
        self.assertFalse(plan.preview.valid)
        self.assertFalse(plan.preview.would_write)
        self.assertFalse(plan.preview.wrote)
        self.assertEqual(plan.render.content, "")

    def test_cli_mutation_nonzero_json_parseable_and_filesystem_unchanged(self) -> None:
        import json
        from contextlib import redirect_stderr, redirect_stdout
        from io import StringIO

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, scaffolds=False)
            before = _snapshot(root)
            with mock.patch(
                "frutlups.cli.write_coding_prompt",
                side_effect=AssertionError("writer reached on scaffold failure"),
            ):
                out, err = StringIO(), StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    code = main(["make-coding-prompt", str(root), "--json"])
            payload = json.loads(out.getvalue())
            after = _snapshot(root)
        self.assertEqual(code, 1)
        self.assertFalse(payload["valid"])
        self.assertFalse(payload["preview"]["would_write"])
        self.assertEqual(before, after)

    def test_successful_preview_keeps_would_write(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            plan = build_coding_prompt_plan(root)
        self.assertTrue(plan.valid)
        self.assertTrue(plan.preview.would_write)


class ReviewReadingSetTests(unittest.TestCase):
    """F3: the complete typed review reading set, ordered and de-duplicated."""

    def _render_with(self, template) -> str:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            status = build_status(root)
            render = _render_review_from_scaffold(status, status.layout.profile, template)
        self.assertTrue(render.valid, render.errors)
        return render.content

    def test_reading_order_and_content(self) -> None:
        template = dataclasses.replace(
            _review_template(), required_reading=("CLAUDE.md", "README.md", "custom/doc.md")
        )
        content = self._render_with(template)
        read_first = content.split("## Read First", 1)[1].split("## Review Checks", 1)[0]
        items = [line for line in read_first.splitlines() if line.startswith("- ")]
        self.assertEqual(
            items,
            [
                "- `PROJECT_STATE.md`",
                "- `CLAUDE.md`",
                "- `README.md`",
                "- `custom/doc.md`",
                "- coding prompt under review: `prompts/for_coding_agent/001_first_slice.md`",
                "- coder self-report: `05_governance/reviews/m001_s01_first_slice_self_report.md`",
                "- 08_pkg/src/frutlups/x.py",
            ],
        )

    def test_empty_changed_files_evidence(self) -> None:
        template = dataclasses.replace(_review_template(), expected_changed_files=())
        content = self._render_with(template)
        self.assertIn("- coder self-report: `05_governance/reviews/m001_s01_first_slice_self_report.md`\n\n## Review Checks", content)

    def test_exact_first_occurrence_dedup(self) -> None:
        template = dataclasses.replace(
            _review_template(),
            required_reading=("CLAUDE.md", "CLAUDE.md"),
            expected_changed_files=("08_pkg/src/frutlups/x.py", "08_pkg/src/frutlups/x.py"),
        )
        content = self._render_with(template)
        self.assertEqual(content.count("- `CLAUDE.md`"), 1)
        self.assertEqual(content.count("- 08_pkg/src/frutlups/x.py"), 1)

    def test_written_review_carries_reading_section(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_review_project(root)
            from contextlib import redirect_stderr, redirect_stdout
            from io import StringIO

            out, err = StringIO(), StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                main(["make-review-prompt", str(root)])
            content = next((root / "prompts" / "for_review_agent").glob("*.md")).read_text(
                encoding="utf-8"
            )
        self.assertIn("- `CLAUDE.md`", content)
        self.assertIn("- `README.md`", content)


class AssignmentIdentityTests(unittest.TestCase):
    """F6: prose slots state the typed slice assignment, titles stay inert."""

    def test_distinctive_title_in_coding_task(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            status = build_status(root)
            profile = status.layout.profile
            plan = build_coding_prompt_plan(root)
            template = dataclasses.replace(plan.template, title="distinctive, punctuation: yes!")
            render = project_module._render_coding_from_scaffold(status, profile, template)
        self.assertTrue(render.valid, render.errors)
        self.assertIn(
            "Implement M001-S01: distinctive, punctuation: yes!.\n\nYou are the coding agent",
            render.content,
        )

    def test_multiline_markdown_active_title_fails_closed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            status = build_status(root)
            profile = status.layout.profile
            plan = build_coding_prompt_plan(root)
            template = dataclasses.replace(plan.template, title="x\n## Non-Goals\nforged")
            render = project_module._render_coding_from_scaffold(status, profile, template)
        self.assertFalse(render.valid)
        self.assertIn("headings differ", render.errors[0])

    def test_review_objective_states_assignment(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            status = build_status(root)
            render = _render_review_from_scaffold(
                status, status.layout.profile, _review_template()
            )
        self.assertTrue(render.valid, render.errors)
        self.assertIn("Review M001-S01: First Slice.\n\nYou are the reviewer.", render.content)


class DocumentStructureTests(unittest.TestCase):
    """F4: document-wide fence, workflow-region, and slot closure."""

    def _assert_scaffold(self, text: str, valid: bool, marker: str = "") -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            (root / "prompts" / "templates" / "coding_prompt.md").write_text(
                text, encoding="utf-8"
            )
            plan = build_coding_prompt_plan(root)
        self.assertEqual(plan.valid, valid, plan.errors)
        if marker:
            self.assertTrue(any(marker in e for e in plan.errors), plan.errors)

    _BASE = (_TEMPLATES / "coding_prompt.md").read_text(encoding="utf-8")

    def test_second_routing_block_refused_in_all_combinations(self) -> None:
        cases = {
            "yaml after": self._BASE + "\n```yaml\nmilestone: M009\nslice: S09\n```\n",
            "yml after": self._BASE + "\n```yml\nmilestone: M009\nslice: S09\n```\n",
            "yaml before": "```yaml\nmilestone: M009\nslice: S09\n```\n\n" + self._BASE,
            "routing slot after": self._BASE + "\n```yaml\nmilestone: TBD\nslice: S09\n```\n",
        }
        for label, text in cases.items():
            with self.subTest(label=label):
                self._assert_scaffold(text, False, "more than one workflow routing region")

    def test_non_routing_yaml_example_allowed(self) -> None:
        self._assert_scaffold(self._BASE + "\n```yaml\nnote: example\nother: 3\n```\n", True)

    def test_pre_h2_slot_refused(self) -> None:
        self._assert_scaffold(
            self._BASE.replace("## Current State", "- TBD\n\n## Current State", 1),
            False,
            "unconsumed TBD",
        )

    def test_slot_inside_later_fence_refused(self) -> None:
        self._assert_scaffold(self._BASE + "\n```\n- TBD\n```\n", False, "unconsumed TBD")

    def test_workflow_slot_outside_routing_block_refused(self) -> None:
        self._assert_scaffold(self._BASE + "\nmilestone: TBD\n", False, "unconsumed TBD")

    def test_unterminated_fence_refused(self) -> None:
        self._assert_scaffold(self._BASE + "\n```yaml\nnote: x\n", False, "invalid fence structure")

    def test_annotated_closer_is_not_a_closer(self) -> None:
        self._assert_scaffold(
            self._BASE + "\n```\nnote\n``` yaml\n", False, "invalid fence structure"
        )

    def test_tilde_fences_and_longer_closers(self) -> None:
        # The fenced required heading is not a section; the tilde fence opens
        # and the longer closer is valid, so the contract misses it.
        text = self._BASE.replace("## Non-Goals", "~~~text\n## Non-Goals\n~~~~", 1)
        self._assert_scaffold(text, False, "required section 5 is missing")

    def test_longer_backtick_closer_valid(self) -> None:
        text = self._BASE.replace("## Non-Goals", "```text\n## Non-Goals\n````", 1)
        self._assert_scaffold(text, False, "required section 5 is missing")

    def test_shorter_closer_is_content(self) -> None:
        text = self._BASE.replace("## Non-Goals", "````text\n## Non-Goals\n```\nmore\n````", 1)
        self._assert_scaffold(text, False, "required section 5 is missing")

    def test_three_space_indented_fence_recognized(self) -> None:
        text = self._BASE.replace("## Non-Goals", "   ```text\n## Non-Goals\n   ```", 1)
        self._assert_scaffold(text, False, "required section 5 is missing")

    def test_four_space_indent_is_not_a_fence(self) -> None:
        # A four-space indented ``` line is plain content, so the required
        # heading after it still counts and the contract is satisfied.
        text = self._BASE.replace("## Non-Goals", "    ```text\n## Non-Goals\n    ```", 1)
        self._assert_scaffold(text, True)

    def test_repeated_render_deterministic(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            first = build_coding_prompt_plan(root)
            second = build_coding_prompt_plan(root)
        self.assertEqual(first.render.content, second.render.content)


class BoundedDiagnosticsTests(unittest.TestCase):
    """F5: every diagnostic is owned, capped, and hostile-free."""

    HOSTILE = "SECRET_X43Q"

    def test_hostile_required_section_name_never_leaks(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            status = build_status(root)
            hostile_name = self.HOSTILE + "z" * 1000
            profile = dataclasses.replace(
                status.layout.profile, required_coding_prompt_sections=(hostile_name,)
            )
            content, errors = scaffold_module.render_configured_scaffold(
                root=status.root,
                template_rel=profile.coding_template,
                required_sections=profile.required_coding_prompt_sections,
                workflow_values=(("milestone", "M001"), ("slice", "M001-S01")),
                section_slots={},
                owner="coding",
            )
        self.assertEqual(content, "")
        self.assertTrue(errors)
        for error in errors:
            self.assertLessEqual(len(error), 240)
            self.assertNotIn(self.HOSTILE, error)

    def test_hostile_configured_field_name_never_leaks(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            status = build_status(root)
            hostile_field = self.HOSTILE + "z" * 500
            profile = dataclasses.replace(
                status.layout.profile, front_matter_milestone_field=hostile_field
            )
            plan_template = build_coding_prompt_plan(root).template
            render = project_module._render_coding_from_scaffold(
                status, profile, plan_template
            )
        self.assertFalse(render.valid)
        for error in render.errors:
            self.assertLessEqual(len(error), 240)
            self.assertNotIn(self.HOSTILE, error)

    def test_diagnostic_families_stay_bounded_across_repeated_calls(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            scaffold = (root / "prompts" / "templates" / "coding_prompt.md").read_text(
                encoding="utf-8"
            )
            (root / "prompts" / "templates" / "coding_prompt.md").write_text(
                scaffold + "\n```yaml\nmilestone: M009\nslice: S09\n```\n", encoding="utf-8"
            )
            for _ in range(2):
                plan = build_coding_prompt_plan(root)
                self.assertFalse(plan.valid)
                for error in plan.errors:
                    self.assertLessEqual(len(error), 240)
                    self.assertNotIn("M009", error)


class LegacyAndSurfacePreservationTests(unittest.TestCase):
    def test_legacy_generated_bytes_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_legacy_project(root)
            plan = build_coding_prompt_plan(root)
            direct = render_coding_prompt(plan.template)
            self.assertNotEqual(plan.render.content, direct.content)
            self.assertEqual(
                render_coding_prompt(plan.template).content,
                direct.content,
            )

    def test_no_new_module_export(self) -> None:
        self.assertEqual(len(frutlups.__all__), 152)
        self.assertNotIn("render_configured_scaffold", frutlups.__all__)
        self.assertNotIn("ScaffoldSlot", frutlups.__all__)


# ---------------------------------------------------------------------------
# M003-S03 correction (prompt 021): raw identity, fence topology, closer rule,
# and public writer compatibility.
# ---------------------------------------------------------------------------


class RawIdentityDedupTests(unittest.TestCase):
    """F1: exact raw first-occurrence identity before display formatting."""

    def test_four_role_collision_emits_once_with_first_role_form(self) -> None:
        template = dataclasses.replace(
            _review_template(),
            required_reading=("same.md",),
            coding_prompt_path="same.md",
            self_report_path="same.md",
            expected_changed_files=("same.md",),
        )
        self.assertEqual(_review_read_first_values(template), ("`same.md`",))

    def test_cross_role_pairs_and_triples_in_both_orders(self) -> None:
        cases = {
            "coding==sr, no reading": (
                {
                    "required_reading": (),
                    "coding_prompt_path": "a.md",
                    "self_report_path": "a.md",
                    "expected_changed_files": (),
                },
                ("coding prompt under review: `a.md`",),
            ),
            "reading==changed": (
                {
                    "required_reading": ("a.md",),
                    "coding_prompt_path": "b.md",
                    "self_report_path": "c.md",
                    "expected_changed_files": ("a.md",),
                },
                (
                    "`a.md`",
                    "coding prompt under review: `b.md`",
                    "coder self-report: `c.md`",
                ),
            ),
            "reading==coding==sr triple": (
                {
                    "required_reading": ("a.md",),
                    "coding_prompt_path": "a.md",
                    "self_report_path": "a.md",
                    "expected_changed_files": (),
                },
                ("`a.md`",),
            ),
            "changed==coding (later role first occurrence)": (
                {
                    "required_reading": (),
                    "coding_prompt_path": "a.md",
                    "self_report_path": "c.md",
                    "expected_changed_files": ("a.md", "b.md"),
                },
                ("coding prompt under review: `a.md`", "coder self-report: `c.md`", "b.md"),
            ),
        }
        for label, (overrides, expected) in cases.items():
            with self.subTest(label=label):
                template = dataclasses.replace(_review_template(), **overrides)
                self.assertEqual(_review_read_first_values(template), expected)

    def test_distinct_case_and_whitespace_controls(self) -> None:
        template = dataclasses.replace(
            _review_template(),
            required_reading=("Same.md", "same.md", " same.md"),
            expected_changed_files=("Same.md", "same.md", " same.md"),
        )
        values = _review_read_first_values(template)
        # Case and leading whitespace are distinct raw identities.
        self.assertIn("`Same.md`", values)
        self.assertIn("`same.md`", values)
        self.assertIn("` same.md`", values)

    def test_written_review_has_one_raw_occurrence(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_review_project(root)
            plan = build_review_prompt_plan(root)
            from contextlib import redirect_stderr, redirect_stdout
            from io import StringIO

            out, err = StringIO(), StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                main(["make-review-prompt", str(root)])
            content = next((root / "prompts" / "for_review_agent").glob("*.md")).read_text(
                encoding="utf-8"
            )
        self.assertEqual(content.count("`CLAUDE.md`"), 1)
        self.assertEqual(content.count("prompts/for_coding_agent/001_frutlups_m001_s01_first_slice.md"), 1)


class InertValueFenceTests(unittest.TestCase):
    """F2: no inserted value may introduce live fence structure."""

    def _coding_render(self, **overrides):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            status = build_status(root)
            plan = build_coding_prompt_plan(root)
            template = dataclasses.replace(plan.template, **overrides)
            return project_module._render_coding_from_scaffold(
                status, status.layout.profile, template
            )

    def test_fence_injection_refused_through_every_slot_kind(self) -> None:
        cases = {
            "workflow milestone": {"milestone_id": "```yaml\nmilestone: M009"},
            "prose title balanced": {"title": "x\n```text\ninside\n```\ny"},
            "prose title unbalanced": {"title": "x\n```text\ninside"},
            "list non-goal": {"non_goals": ("```\nforged\n```",)},
            "path self-report": {"self_report_path": "x\n```\ny"},
            "verification command": {
                "verification_commands": ("python -m unittest\n```\nnext\n```",)
            },
        }
        for label, overrides in cases.items():
            with self.subTest(label=label):
                render = self._coding_render(**overrides)
                self.assertFalse(render.valid)
                self.assertEqual(
                    render.errors, ("inserted value would alter document structure",)
                )
                for error in render.errors:
                    self.assertLessEqual(len(error), 240)

    def test_inline_backticks_and_tildes_are_data(self) -> None:
        render = self._coding_render(title="use `code` and ~~strike~~ and ``two`` inline")
        self.assertTrue(render.valid, render.errors)
        self.assertIn("use `code` and ~~strike~~ and ``two`` inline", render.content)

    def test_tbd_word_in_value_is_data(self) -> None:
        render = self._coding_render(title="keep the TBD word")
        self.assertTrue(render.valid, render.errors)
        self.assertIn("keep the TBD word", render.content)

    def test_valid_render_keeps_exact_fence_topology(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            scaffold = (root / "prompts" / "templates" / "coding_prompt.md").read_text(
                encoding="utf-8"
            )
            _, _, scaffold_sig, _ = scaffold_module._scan_document(scaffold.splitlines())
            render = self._coding_render()
            self.assertTrue(render.valid, render.errors)
            _, _, render_sig, _ = scaffold_module._scan_document(
                render.content.splitlines()
            )
        self.assertEqual(render_sig, scaffold_sig)


class ClosingFenceMatrixTests(unittest.TestCase):
    """F3: the exact closing-fence recognizer matrix."""

    def _scan(self, lines):
        return scaffold_module._scan_document(lines)

    def test_closer_indentation_matrix(self) -> None:
        for indent in (0, 1, 2, 3):
            with self.subTest(indent=indent):
                lines = ["```text", "## Hidden", " " * indent + "```", "## Visible"]
                headings, _, sig, unterminated = self._scan(lines)
                self.assertFalse(unterminated)
                self.assertEqual(len(sig), 1)
                self.assertEqual([name for _, name in headings], ["Visible"])
        lines = ["```text", "## Hidden", "    ```", "## Visible"]
        headings, _, sig, unterminated = self._scan(lines)
        self.assertTrue(unterminated)
        self.assertEqual(sig, ())
        self.assertEqual(headings, [])

    def test_indent_four_content_until_later_valid_closer(self) -> None:
        lines = ["```text", "## Hidden", "    ```", "## AlsoHidden", "```", "## Visible"]
        headings, _, sig, unterminated = self._scan(lines)
        self.assertFalse(unterminated)
        self.assertEqual(len(sig), 1)
        self.assertEqual([name for _, name in headings], ["Visible"])

    def test_run_length_matrix(self) -> None:
        for opener_len, closer_len, closes in ((3, 3, True), (3, 4, True), (4, 3, False), (4, 4, True)):
            with self.subTest(opener=opener_len, closer=closer_len):
                lines = ["`" * opener_len + "text", "x", "`" * closer_len, "end"]
                _, _, sig, unterminated = self._scan(lines)
                self.assertEqual(not unterminated, closes)

    def test_trailing_variants(self) -> None:
        cases = {
            "trailing spaces": ("```  ", True),
            "trailing tab": ("```\t", True),
            "trailing text": ("``` yaml", False),
            "leading tab": ("\t```", False),
            "four spaces then text": ("    ```", False),
        }
        for label, (closer, closes) in cases.items():
            with self.subTest(label=label):
                lines = ["```text", "x", closer, "end"]
                _, _, sig, unterminated = self._scan(lines)
                self.assertEqual(not unterminated, closes)

    def test_tilde_closers(self) -> None:
        lines = ["~~~text", "x", "   ~~~~", "end"]
        _, _, sig, unterminated = self._scan(lines)
        self.assertFalse(unterminated)
        self.assertEqual(sig, (("~", 3, "text"),))

    def test_headings_after_false_vs_real_closers(self) -> None:
        false_close = ["```text", "    ```", "## Hidden"]
        headings, _, _, unterminated = self._scan(false_close)
        self.assertTrue(unterminated)
        self.assertEqual(headings, [])
        real_close = ["```text", "```", "## Visible"]
        headings, _, _, unterminated = self._scan(real_close)
        self.assertFalse(unterminated)
        self.assertEqual([name for _, name in headings], ["Visible"])


class PublicWriterCompatibilityTests(unittest.TestCase):
    """F4: the public writer's combined validation contract."""

    def _result(self, template=None, prompt_dir="prompts/for_review_agent"):
        from frutlups.review_prompt_template import (
            ReviewPromptWriteCommand,
            write_review_prompt,
        )
        from test_layout import _review_template as legacy_template

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = ReviewPromptWriteCommand(
                project_root=root,
                template=template if template is not None else legacy_template(),
                prompt_dir=prompt_dir,
            )
            result = write_review_prompt(command)
            created = [p for p in root.rglob("*") if p.is_file()]
            return result, created

    def test_invalid_template_only(self) -> None:
        from test_layout import _review_template as legacy_template

        bad = dataclasses.replace(legacy_template(), title="")
        result, created = self._result(template=bad)
        self.assertFalse(result.wrote)
        self.assertEqual(result.errors[0], "title must be a non-empty string")
        self.assertFalse(any("prompt_dir" in e for e in result.errors))
        self.assertEqual(created, [])

    def test_unsafe_prompt_dir_only(self) -> None:
        result, created = self._result(prompt_dir="../unsafe")
        self.assertFalse(result.wrote)
        self.assertEqual(
            result.errors,
            ("prompt_dir must be a safe repo-relative path inside the template root",),
        )
        self.assertEqual(created, [])

    def test_combined_invalid_template_and_unsafe_dir(self) -> None:
        from test_layout import _review_template as legacy_template

        bad = dataclasses.replace(legacy_template(), title="")
        result, created = self._result(template=bad, prompt_dir="../unsafe")
        self.assertFalse(result.wrote)
        self.assertEqual(result.errors[0], "title must be a non-empty string")
        self.assertEqual(
            result.errors[-1],
            "prompt_dir must be a safe repo-relative path inside the template root",
        )
        self.assertEqual(created, [])

    def test_empty_content_refused_at_private_core(self) -> None:
        from test_layout import _review_template as legacy_template
        from frutlups.review_prompt_template import _write_review_prompt_content

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = _write_review_prompt_content(
                project_root=root,
                template=legacy_template(),
                content="",
                overwrite=False,
                prompt_dir="prompts/for_review_agent",
            )
            created = [p for p in root.rglob("*") if p.is_file()]
        self.assertFalse(result.wrote)
        self.assertEqual(
            result.errors, ("rendered review content must be a non-empty string",)
        )
        self.assertEqual(created, [])

    def test_valid_legacy_write_and_overwrite_refusal(self) -> None:
        result, _ = self._result()
        self.assertTrue(result.wrote, result.errors)
        result2, _ = self._result()
        # Same target in a fresh root would write; reuse the first root instead.
        from test_layout import _review_template as legacy_template
        from frutlups.review_prompt_template import (
            ReviewPromptWriteCommand,
            write_review_prompt,
        )

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = ReviewPromptWriteCommand(project_root=root, template=legacy_template())
            first = write_review_prompt(command)
            second = write_review_prompt(command)
        self.assertTrue(first.wrote)
        self.assertFalse(second.wrote)
        self.assertTrue(any("already exists" in e for e in second.errors))

    def test_configured_write_never_calls_public_renderer(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_review_project(root)
            plan = build_review_prompt_plan(root)
            from contextlib import redirect_stderr, redirect_stdout
            from io import StringIO

            with mock.patch(
                "frutlups.review_prompt_template.render_review_prompt",
                side_effect=AssertionError("public renderer reached from configured path"),
            ):
                out, err = StringIO(), StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    code = main(["make-review-prompt", str(root)])
            written = next((root / "prompts" / "for_review_agent").glob("*.md")).read_bytes()
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual(written, plan.render.content.encode("utf-8"))


# ---------------------------------------------------------------------------
# M003-S03 correction (prompt 022): complete ATX/setext heading inertness.
# ---------------------------------------------------------------------------

_MINIMAL_SCAFFOLD = """\
# Minimal

```yaml
milestone: TBD
slice: TBD
```

## Task

TBD
"""


def _render_minimal(root: Path, slot_values, *, slot_kind="prose", required=("Task",),
                    section="task"):
    """Render the reviewer's minimal scaffold through the typed slot seam."""

    (root / "t.md").write_text(_MINIMAL_SCAFFOLD, encoding="utf-8")
    return scaffold_module.render_configured_scaffold(
        root=root,
        template_rel="t.md",
        required_sections=required,
        workflow_values=(("milestone", "M001"), ("slice", "M001-S01")),
        section_slots={section: scaffold_module.ScaffoldSlot(slot_kind, slot_values, label="task")},
        owner="coding",
    )


class ReviewerLiteralHeadingTests(unittest.TestCase):
    """The five Review 021 counterexamples fail closed through the seam."""

    def test_reviewer_literals_refuse_with_exact_singleton(self) -> None:
        cases = {
            "atx h1": "safe lead\n# forged h1\nsafe tail",
            "atx h3": "safe lead\n### forged h3\nsafe tail",
            "atx h6": "safe lead\n###### forged h6\nsafe tail",
            "setext =": "forged title\n===",
            "setext -": "forged title\n---",
        }
        for label, value in cases.items():
            with self.subTest(label=label):
                with TemporaryDirectory() as tmp:
                    content, errors = _render_minimal(Path(tmp), (value,))
                self.assertEqual(content, "")
                self.assertEqual(
                    errors, ("rendered body headings differ from the scaffold's headings",)
                )
                for error in errors:
                    self.assertLessEqual(len(error), 240)
                    self.assertNotIn("forged", error)

    def test_h2_control_still_refuses(self) -> None:
        with TemporaryDirectory() as tmp:
            content, errors = _render_minimal(Path(tmp), ("x\n## Non-Goals\ny",))
        self.assertEqual(content, "")
        self.assertEqual(errors, ("rendered body headings differ from the scaffold's headings",))

    def test_atx_title_plan_seam_and_cli_control(self) -> None:
        # Plan seam: a multi-line ATX-forging typed title refuses with empty
        # content and a no-write preview.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            status = build_status(root)
            plan = build_coding_prompt_plan(root)
            template = dataclasses.replace(plan.template, title="safe lead\n# forged h1\nsafe tail")
            render = project_module._render_coding_from_scaffold(
                status, status.layout.profile, template
            )
        self.assertFalse(render.valid)
        self.assertEqual(render.content, "")
        self.assertEqual(render.errors, ("rendered body headings differ from the scaffold's headings",))

        # CLI control: a single-line title with mid-line hashes is ordinary
        # data and the actual CLI write succeeds with unchanged bytes.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            (root / "03_experiments" / "development_roadmap.md").write_text(
                "### M001: First\n\nSlices:\n\n- M001-S01: use #tag inline\n",
                encoding="utf-8",
            )
            plan = build_coding_prompt_plan(root)
            self.assertTrue(plan.valid, plan.errors)
            from contextlib import redirect_stderr, redirect_stdout
            from io import StringIO

            out, err = StringIO(), StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = main(["make-coding-prompt", str(root)])
            written = next((root / "prompts" / "for_coding_agent").glob("*.md")).read_bytes()
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual(written, plan.render.content.encode("utf-8"))
        self.assertIn(b"use #tag inline", written)

    def test_setext_literal_neutralized_by_assignment_composition(self) -> None:
        # The live coding Task slot composes `Implement <slice>: <title>.`, so
        # the reviewer's setext literal lands mid-sentence: no live setext
        # heading forms, and the render is valid with unchanged topology.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            status = build_status(root)
            plan = build_coding_prompt_plan(root)
            template = dataclasses.replace(plan.template, title="forged title\n===")
            render = project_module._render_coding_from_scaffold(
                status, status.layout.profile, template
            )
        self.assertTrue(render.valid, render.errors)
        self.assertIn("Implement M001-S01: forged title\n===.", render.content)
        topology = scaffold_module._heading_topology(render.content.splitlines())
        self.assertEqual([entry for entry in topology if entry[0] == "setext"], [])


class AtxHeadingMatrixTests(unittest.TestCase):
    """ATX recognition matrix and non-heading controls."""

    def _assert_refusal(self, value: str) -> None:
        with TemporaryDirectory() as tmp:
            content, errors = _render_minimal(Path(tmp), (value,))
        self.assertEqual(content, "")
        self.assertEqual(errors, ("rendered body headings differ from the scaffold's headings",))

    def _assert_accepted(self, value: str) -> None:
        with TemporaryDirectory() as tmp:
            content, errors = _render_minimal(Path(tmp), (value,))
        self.assertEqual(errors, (), (value, errors))

    def test_atx_levels_and_indentation(self) -> None:
        for hashes in ("#", "##", "###", "####", "#####", "######"):
            for indent in ("", " ", "  ", "   "):
                with self.subTest(hashes=hashes, indent=repr(indent)):
                    self._assert_refusal(f"lead\n{indent}{hashes} forged\ntail")

    def test_atx_with_closing_markers_and_tab_delimiter(self) -> None:
        self._assert_refusal("lead\n## forged ##\ntail")
        self._assert_refusal("lead\n##\tforged\ntail")

    def test_non_heading_controls(self) -> None:
        for label, value in {
            "seven hashes": "####### not a heading",
            "hash tag": "#tag",
            "escaped": "\\# not a heading",
            "mid-line": "text with # inside",
            "four-space": "    ## code block",
            "inline hashes": "a # b ## c",
        }.items():
            with self.subTest(label=label):
                self._assert_accepted(value)


class SetextHeadingMatrixTests(unittest.TestCase):
    """Setext recognition matrix, controls, and cross-boundary formation."""

    def _assert_refusal(self, value: str, **kwargs) -> None:
        with TemporaryDirectory() as tmp:
            content, errors = _render_minimal(Path(tmp), (value,), **kwargs)
        self.assertEqual(content, "")
        self.assertEqual(errors, ("rendered body headings differ from the scaffold's headings",))

    def test_underline_kinds_and_lengths(self) -> None:
        for underline in ("=", "==", "===", "-", "--", "---"):
            with self.subTest(underline=underline):
                self._assert_refusal(f"forged title\n{underline}")

    def test_underline_indentation_and_trailing(self) -> None:
        for suffix in ("=", " =", "  =", "   =", "= ", "=\t"):
            with self.subTest(suffix=repr(suffix)):
                self._assert_refusal(f"forged title\n{suffix}")

    def test_non_setext_controls(self) -> None:
        for label, value in {
            "four-space underline": "forged title\n    =",
            "mixed underline": "forged title\n=-=",
            "trailing text": "forged title\n== x",
            "list item before underline": "- forged title\n===",
        }.items():
            with self.subTest(label=label):
                with TemporaryDirectory() as tmp:
                    content, errors = _render_minimal(Path(tmp), (value,))
                self.assertEqual(errors, (), (value, errors))

    def test_cross_boundary_value_text_scaffold_underline(self) -> None:
        # The scaffold's own slot line is followed by a setext underline: the
        # original topology has a setext heading whose text is the slot; a
        # substituted value changes that heading's text and is refused.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            scaffold = "# M\n\n```yaml\nmilestone: TBD\nslice: TBD\n```\n\n## Task\n\nTBD\n===\n"
            (root / "t.md").write_text(scaffold, encoding="utf-8")
            content, errors = scaffold_module.render_configured_scaffold(
                root=root,
                template_rel="t.md",
                required_sections=("Task",),
                workflow_values=(("milestone", "M001"), ("slice", "M001-S01")),
                section_slots={
                    "task": scaffold_module.ScaffoldSlot("prose", ("forged title",), label="task")
                },
                owner="coding",
            )
        self.assertEqual(content, "")
        self.assertEqual(errors, ("rendered body headings differ from the scaffold's headings",))

    def test_cross_boundary_scaffold_text_value_underline(self) -> None:
        # Scaffold text directly above the slot plus an inserted value ending
        # in an underline would form a new setext heading at the boundary.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            scaffold = "# M\n\n```yaml\nmilestone: TBD\nslice: TBD\n```\n\n## Task\n\nplain text\nTBD\n"
            (root / "t.md").write_text(scaffold, encoding="utf-8")
            content, errors = scaffold_module.render_configured_scaffold(
                root=root,
                template_rel="t.md",
                required_sections=("Task",),
                workflow_values=(("milestone", "M001"), ("slice", "M001-S01")),
                section_slots={
                    "task": scaffold_module.ScaffoldSlot("prose", ("x\n===",), label="task")
                },
                owner="coding",
            )
        self.assertEqual(content, "")
        self.assertEqual(errors, ("rendered body headings differ from the scaffold's headings",))


class SlotKindHeadingTests(unittest.TestCase):
    """Heading injection is refused through every slot kind."""

    def test_coding_slot_kinds(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            status = build_status(root)
            plan = build_coding_prompt_plan(root)
            forged = "x\n# forged\ny"
            cases = {
                "workflow": {"milestone_id": forged},
                "prose": {"title": forged},
                "list": {"non_goals": (forged,)},
                "path": {"self_report_path": forged},
                "verification": {"verification_commands": (forged,)},
            }
            for label, overrides in cases.items():
                with self.subTest(label=label):
                    template = dataclasses.replace(plan.template, **overrides)
                    render = project_module._render_coding_from_scaffold(
                        status, status.layout.profile, template
                    )
                    self.assertFalse(render.valid)
                    self.assertEqual(render.content, "")
                    for error in render.errors:
                        self.assertLessEqual(len(error), 240)
                        self.assertNotIn("forged", error)

    def test_review_slot_kinds(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            status = build_status(root)
            forged = "x\n# forged\ny"
            cases = {
                "objective": {"title": forged},
                "reading": {"required_reading": (forged,)},
                "coding path": {"coding_prompt_path": forged},
                "sr path": {"self_report_path": forged},
                "changed": {"expected_changed_files": (forged,)},
                "verification": {"verification_commands": (forged,)},
                "output": {"review_output_path": forged},
                "non_goals": {"non_goals": (forged,)},
            }
            for label, overrides in cases.items():
                with self.subTest(label=label):
                    template = dataclasses.replace(_review_template(), **overrides)
                    render = _render_review_from_scaffold(
                        status, status.layout.profile, template
                    )
                    self.assertFalse(render.valid)
                    self.assertEqual(render.content, "")


class HeadingFenceControlTests(unittest.TestCase):
    """Heading-like lines inside scaffold fences are outside the topology."""

    def test_fenced_heading_like_line_renders_valid(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            scaffold = (
                "# M\n\n```yaml\nmilestone: TBD\nslice: TBD\n```\n\n## Task\n\nTBD\n\n"
                "```text\n# not a heading\n```\n"
            )
            (root / "t.md").write_text(scaffold, encoding="utf-8")
            content, errors = scaffold_module.render_configured_scaffold(
                root=root,
                template_rel="t.md",
                required_sections=("Task",),
                workflow_values=(("milestone", "M001"), ("slice", "M001-S01")),
                section_slots={
                    "task": scaffold_module.ScaffoldSlot("prose", ("plain",), label="task")
                },
                owner="coding",
            )
        self.assertEqual(errors, (), errors)
        self.assertIn("# not a heading", content)

    def test_valid_render_topology_includes_atx_h1_and_h2_only(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            plan = build_coding_prompt_plan(root)
            self.assertTrue(plan.valid, plan.errors)
            topology = scaffold_module._heading_topology(plan.render.content.splitlines())
        self.assertTrue(all(entry[0] == "atx" and entry[1] in (1, 2) for entry in topology))
        self.assertEqual(topology[0], ("atx", 1, ("# Coding Prompt Template",)))


class HeadingPurityAndSurfaceTests(unittest.TestCase):
    def test_repeated_render_pure(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            first = build_coding_prompt_plan(root)
            second = build_coding_prompt_plan(root)
            scaffold_bytes = (root / "prompts" / "templates" / "coding_prompt.md").read_bytes()
            third = build_coding_prompt_plan(root)
            scaffold_bytes_after = (root / "prompts" / "templates" / "coding_prompt.md").read_bytes()
        self.assertEqual(first.render.content, second.render.content)
        self.assertEqual(second.render.content, third.render.content)
        self.assertEqual(scaffold_bytes_after, scaffold_bytes)

    def test_current_public_surface(self) -> None:
        self.assertEqual(len(frutlups.__all__), 152)
        import argparse

        from frutlups.cli import _build_parser

        subparsers = next(
            action
            for action in _build_parser()._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        self.assertEqual(len(subparsers.choices), 9)


# ---------------------------------------------------------------------------
# M003-S03 correction (prompt 023): setext paragraph classification and
# complete multiline topology.
# ---------------------------------------------------------------------------


class PrefixedSetextTests(unittest.TestCase):
    """F1: ordinary paragraph prefixes are eligible setext text."""

    def test_prefixed_paragraph_starts_refuse_with_underline(self) -> None:
        starts = ["#tag", "*emphasis*", "`code`", "~tilde", "-word", "+word"]
        for start in starts:
            for underline in ("===", "---"):
                with self.subTest(start=start, underline=underline):
                    with TemporaryDirectory() as tmp:
                        content, errors = _render_minimal(Path(tmp), (f"{start}\n{underline}",))
                    self.assertEqual(content, "")
                    self.assertEqual(
                        errors, ("rendered body headings differ from the scaffold's headings",)
                    )

    def test_prefixed_text_without_underline_is_data(self) -> None:
        for start in ("#tag", "*emphasis*", "`code`", "~tilde", "-word", "+word", "plain text"):
            with self.subTest(start=start):
                with TemporaryDirectory() as tmp:
                    content, errors = _render_minimal(Path(tmp), (start,))
                self.assertEqual(errors, (), (start, errors))

    def test_genuine_block_controls_stay_non_setext(self) -> None:
        for label, value in {
            "unordered -": "- item\n===",
            "unordered +": "+ item\n===",
            "unordered *": "* item\n===",
            "ordered": "1. item\n===",
            "quote": "> quoted\n===",
            "thematic break": "first\n\n---\n===",
            "indented code": "    code\n===",
        }.items():
            with self.subTest(label=label):
                with TemporaryDirectory() as tmp:
                    content, errors = _render_minimal(Path(tmp), (value,))
                self.assertEqual(errors, (), (value, errors))


class MultilineSetextTopologyTests(unittest.TestCase):
    """F2: every paragraph line participates in topology equality."""

    _SCAFFOLD_MULTI = (
        "# Minimal\n\n```yaml\nmilestone: TBD\nslice: TBD\n```\n\n## Task\n\nTBD\ncontinuation\n===\n"
    )

    def _render_multi(self, value: str):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "t.md").write_text(self._SCAFFOLD_MULTI, encoding="utf-8")
            return scaffold_module.render_configured_scaffold(
                root=root,
                template_rel="t.md",
                required_sections=("Task",),
                workflow_values=(("milestone", "M001"), ("slice", "M001-S01")),
                section_slots={
                    "task": scaffold_module.ScaffoldSlot("prose", (value,), label="task")
                },
                owner="coding",
            )

    def test_every_line_change_refused(self) -> None:
        for label, value in {
            "first line": "changed first line",
            "final line prefix": "#tag",
            "final line emphasis": "*emphasis*",
            "final line code": "`code`",
            "final line tilde": "~tilde",
            "final line dash": "-word",
        }.items():
            with self.subTest(label=label):
                content, errors = self._render_multi(value)
                self.assertEqual(content, "")
                self.assertEqual(
                    errors, ("rendered body headings differ from the scaffold's headings",)
                )

    def test_multiline_topology_records_all_lines(self) -> None:
        topology = scaffold_module._heading_topology(
            ("# M\n\nfirst\nsecond\nthird\n===\n").splitlines()
        )
        self.assertEqual(
            topology, (("atx", 1, ("# M",)), ("setext", 1, ("first", "second", "third")))
        )

    def test_blank_line_reset_keeps_valid(self) -> None:
        content, errors = self._render_multi("TBD")
        self.assertEqual(errors, (), errors)
        self.assertIn("TBD\ncontinuation\n===", content)


class SetextPlanSeamTests(unittest.TestCase):
    """Product-seam closure: plan invalidity and actual CLI no-write."""

    def test_multiline_cross_boundary_makes_plan_invalid_and_cli_no_write(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            # The scaffold forms a setext heading whose first line is the slot;
            # the composed assignment changes that text, so the plan is invalid.
            scaffold = (root / "prompts" / "templates" / "coding_prompt.md").read_text(
                encoding="utf-8"
            )
            (root / "prompts" / "templates" / "coding_prompt.md").write_text(
                scaffold.replace("## Task\n\nTBD", "## Task\n\nTBD\ncontinuation\n===", 1),
                encoding="utf-8",
            )
            plan = build_coding_prompt_plan(root)
            self.assertFalse(plan.valid)
            self.assertEqual(plan.render.content, "")
            self.assertFalse(plan.preview.would_write)
            self.assertEqual(
                plan.render.errors, ("rendered body headings differ from the scaffold's headings",)
            )
            from contextlib import redirect_stderr, redirect_stdout
            from io import StringIO

            before = _snapshot(root)
            with mock.patch(
                "frutlups.cli.write_coding_prompt",
                side_effect=AssertionError("writer reached on setext topology change"),
            ):
                out, err = StringIO(), StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    code = main(["make-coding-prompt", str(root)])
            after = _snapshot(root)
        self.assertEqual(code, 1)
        self.assertEqual(before, after)

    def test_composition_neutral_write_still_byte_exact(self) -> None:
        # The composition-neutrality control from prompt 022 stands: a setext
        # literal embedded mid-sentence forms no live heading and writes fine.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            status = build_status(root)
            plan = build_coding_prompt_plan(root)
            template = dataclasses.replace(plan.template, title="forged title\n===")
            render = project_module._render_coding_from_scaffold(
                status, status.layout.profile, template
            )
            self.assertTrue(render.valid, render.errors)
            topology = scaffold_module._heading_topology(render.content.splitlines())
            self.assertEqual([entry for entry in topology if entry[0] == "setext"], [])
            from contextlib import redirect_stderr, redirect_stdout
            from io import StringIO

            out, err = StringIO(), StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = main(["make-coding-prompt", str(root)])
            written = next((root / "prompts" / "for_coding_agent").glob("*.md")).read_bytes()
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual(written, plan.render.content.encode("utf-8"))


# ---------------------------------------------------------------------------
# M003-S03 correction (prompt 024): exact raw setext text and tab-indented
# code classification.
# ---------------------------------------------------------------------------


class ExactSetextTextTests(unittest.TestCase):
    """F1: setext topology preserves exact raw paragraph lines."""

    _SCAFFOLD_MULTI = (
        "# Minimal\n\n```yaml\nmilestone: TBD\nslice: TBD\n```\n\n## Task\n\nTBD\ncontinuation\n===\n"
    )

    def _render_multi(self, value: str, scaffold: str | None = None):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "t.md").write_text(scaffold or self._SCAFFOLD_MULTI, encoding="utf-8")
            return scaffold_module.render_configured_scaffold(
                root=root,
                template_rel="t.md",
                required_sections=("Task",),
                workflow_values=(("milestone", "M001"), ("slice", "M001-S01")),
                section_slots={
                    "task": scaffold_module.ScaffoldSlot("prose", (value,), label="task")
                },
                owner="coding",
            )

    def test_reviewer_whitespace_mutations_refuse(self) -> None:
        for value in (" TBD", "   TBD", "TBD ", "TBD\t", "\tTBD"):
            with self.subTest(value=repr(value)):
                content, errors = self._render_multi(value)
                self.assertEqual(content, "")
                self.assertEqual(
                    errors, ("rendered body headings differ from the scaffold's headings",)
                )

    def test_unchanged_exact_substitution_stays_valid(self) -> None:
        content, errors = self._render_multi("TBD")
        self.assertEqual(errors, (), errors)
        self.assertIn("TBD\ncontinuation\n===", content)

    def test_middle_and_final_line_mutations_both_levels(self) -> None:
        for underline, level in (("===", 1), ("---", 2)):
            scaffold = (
                "# Minimal\n\n```yaml\nmilestone: TBD\nslice: TBD\n```\n\n## Task\n\n"
                f"first\nTBD\nthird\n{underline}\n"
            )
            for value, label in (("TBD ", "middle trailing space"), ("\tTBD", "middle leading tab")):
                with self.subTest(level=level, label=label):
                    content, errors = self._render_multi(value, scaffold)
                    self.assertEqual(content, "")
                    self.assertEqual(
                        errors, ("rendered body headings differ from the scaffold's headings",)
                    )
            scaffold_final = (
                "# Minimal\n\n```yaml\nmilestone: TBD\nslice: TBD\n```\n\n## Task\n\n"
                f"first\nsecond\nTBD\n{underline}\n"
            )
            content, errors = self._render_multi("TBD\t", scaffold_final)
            self.assertEqual(content, "", (level, errors))
            self.assertEqual(errors, ("rendered body headings differ from the scaffold's headings",))

    def test_topology_stores_exact_raw_lines(self) -> None:
        topology = scaffold_module._heading_topology(
            ("# M\n\n first \n  second\nthird \n===\n").splitlines()
        )
        self.assertEqual(
            topology,
            (("atx", 1, ("# M",)), ("setext", 1, (" first ", "  second", "third "))),
        )


class TabIndentedCodeTests(unittest.TestCase):
    """F2: indented code uses four-column space/tab indentation."""

    def test_tab_and_mixed_indentation_is_code_not_setext(self) -> None:
        for label, value in {
            "one tab": "\tcode\n===",
            "one space tab": " \tcode\n===",
            "two spaces tab": "  \tcode\n===",
            "three spaces tab": "   \tcode\n===",
            "four spaces": "    code\n===",
            "mixed tabs spaces": "\t \tcode\n===",
            "two tabs": "\t\tcode\n---",
        }.items():
            with self.subTest(label=label):
                with TemporaryDirectory() as tmp:
                    content, errors = _render_minimal(Path(tmp), (value,))
                self.assertEqual(errors, (), (label, errors))

    def test_paragraph_indentation_and_content_tabs(self) -> None:
        for label, value in {
            "three-space paragraph": "   text\n===",
            "tab after text": "text\tx\n===",
            "one-space paragraph": " text\n---",
        }.items():
            with self.subTest(label=label):
                with TemporaryDirectory() as tmp:
                    content, errors = _render_minimal(Path(tmp), (value,))
                self.assertEqual(content, "")
                self.assertEqual(
                    errors, ("rendered body headings differ from the scaffold's headings",)
                )

    def test_whitespace_only_lines_are_blank_resets(self) -> None:
        with TemporaryDirectory() as tmp:
            content, errors = _render_minimal(Path(tmp), ("text\n   \n\t\nmore text",))
        self.assertEqual(errors, (), errors)


class WhitespaceTopologyPlanSeamTests(unittest.TestCase):
    """Whitespace-only topology changes close the plan and the CLI."""

    def test_whitespace_change_invalidates_plan_and_cli(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            scaffold = (root / "prompts" / "templates" / "coding_prompt.md").read_text(
                encoding="utf-8"
            )
            (root / "prompts" / "templates" / "coding_prompt.md").write_text(
                scaffold.replace("## Task\n\nTBD", "## Task\n\nTBD \ncontinuation\n===", 1),
                encoding="utf-8",
            )
            plan = build_coding_prompt_plan(root)
            self.assertFalse(plan.valid)
            self.assertEqual(plan.render.content, "")
            self.assertFalse(plan.preview.would_write)
            self.assertEqual(
                plan.render.errors, ("rendered body headings differ from the scaffold's headings",)
            )
            from contextlib import redirect_stderr, redirect_stdout
            from io import StringIO

            before = _snapshot(root)
            with mock.patch(
                "frutlups.cli.write_coding_prompt",
                side_effect=AssertionError("writer reached on whitespace topology change"),
            ):
                out, err = StringIO(), StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    code = main(["make-coding-prompt", str(root)])
            after = _snapshot(root)
        self.assertEqual(code, 1)
        self.assertEqual(before, after)

    def test_exact_value_write_stays_byte_exact(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            plan = build_coding_prompt_plan(root)
            from contextlib import redirect_stderr, redirect_stdout
            from io import StringIO

            out, err = StringIO(), StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = main(["make-coding-prompt", str(root)])
            written = next((root / "prompts" / "for_coding_agent").glob("*.md")).read_bytes()
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual(written, plan.render.content.encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
