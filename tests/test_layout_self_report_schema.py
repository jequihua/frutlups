"""Tests for M019-S01: layout-driven self-report schema validation.

Reproduces the downstream false negative where frutlups advertised the v2
``self_report_required_headings`` in ``status --json`` but rejected a report
written exactly to that contract (it only parsed ATX headings and enforced the
old hardcoded baseline). These tests cover plain ``Heading:`` parsing, the
profile-driven schema flowing through loop-resume / make-review-prompt / reviewer
handoff, and the configurable-schema validator contract.
"""

from __future__ import annotations

import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from frutlups.cli import main
from frutlups.handoff import build_reviewer_handoff
from frutlups.project import (
    LoopResumeStep,
    build_loop_resume_status,
    build_review_prompt_plan,
    build_status,
)
from frutlups.self_report import (
    SelfReportSchema,
    self_report_schema_from_headings,
    validate_self_report_schema,
)

V2_HEADINGS = (
    "Intent",
    "Files Changed",
    "Behavior Implemented",
    "Tests Added Or Updated",
    "Verification Run",
    "Definition Of Done Audit",
    "Non-Goals Confirmed",
    "Memory Used",
    "Memory Update Requested",
    "Known Limits / Follow-Up",
    "Recommended Next Move",
)

_LAYOUT_YAML = (
    "schema_version: frutlups_layout_config_v0\n"
    "profile_id: artifact_first_template_v2\n"
    'template_root: "."\n'
    "workspace_map:\n"
    "  required_for_base_profile:\n"
    "    - 00_brief\n"
    "    - 03_experiments\n"
    "    - 05_governance\n"
    "    - prompts\n"
    "    - questions\n"
    "roadmaps:\n"
    '  directory: "03_experiments"\n'
    '  active_roadmap_glob: "*active_roadmap*.md"\n'
    '  development_roadmap_glob: "*development_roadmap*.md"\n'
    "prompts:\n"
    '  coding_prompt_dir: "prompts/for_coding_agent"\n'
    '  review_prompt_dir: "prompts/for_review_agent"\n'
    "  section_roles:\n"
    '    required_reading: "Read First"\n'
    '    self_report: "Self-Report"\n'
    '    non_goals: "Non-Goals"\n'
    "  metadata:\n"
    "    parse_front_matter: true\n"
    "reports:\n"
    '  reviews_dir: "05_governance/reviews"\n'
    "  self_report_required_headings:\n" + "".join(f'    - "{h}"\n' for h in V2_HEADINGS)
)

_CODING_PROMPT = (
    "---\n"
    "milestone: M001\n"
    "slice: M001-S01\n"
    "role: coder\n"
    "title: first slice\n"
    "---\n\n"
    "# Coding Prompt 001: first slice\n\n"
    "## Read First\n\n- `CLAUDE.md`\n\n"
    "## Task\n\nDo the thing.\n\n"
    "## Non-Goals\n\n- none\n\n"
    "## Self-Report\n\n"
    "Write a self-report at `05_governance/reviews/m001_s01_self_report.md`.\n"
)


def _v2_self_report(omit: str | None = None) -> str:
    parts: list[str] = []
    for heading in V2_HEADINGS:
        if heading == omit:
            continue
        if heading == "Files Changed":
            body = "- 08_pkg/src/frutlups/example.py"
        elif heading == "Verification Run":
            body = "- python -m unittest discover -s tests"
        else:
            body = "some content"
        parts.append(f"{heading}:\n\n{body}\n")
    return "\n".join(parts)


def _make_v2_project(root: Path, *, self_report: str | None) -> None:
    for name in (
        "00_brief",
        "03_experiments",
        "05_governance/reviews",
        "prompts/for_coding_agent",
        "prompts/for_review_agent",
        "prompts/templates",
        "questions",
    ):
        (root / name).mkdir(parents=True, exist_ok=True)
    # M003-S03: v2 profiles render through the configured scaffolds; the
    # accepted scaffold bytes ship as package-relative fixtures.
    repo_templates = Path(__file__).resolve().parent / "fixtures" / "front_repo_contract"
    for scaffold in ("coding_prompt.md", "review_prompt.md"):
        (root / "prompts" / "templates" / scaffold).write_text(
            (repo_templates / scaffold).read_text(encoding="utf-8"), encoding="utf-8"
        )
    (root / "frutlups.layout.yaml").write_text(_LAYOUT_YAML, encoding="utf-8")
    (root / "PROJECT_STATE.md").write_text(
        "# Project State\n\nMemory mode:\n- none\n\nFrutlups mode:\n- manual\n", encoding="utf-8"
    )
    (root / "03_experiments" / "active_roadmap.md").write_text(
        "### M001: First\n\nStatus: active\n", encoding="utf-8"
    )
    (root / "03_experiments" / "development_roadmap.md").write_text(
        "### M001: First\n\nSlices:\n\n- M001-S01: first slice\n", encoding="utf-8"
    )
    (root / "prompts" / "for_coding_agent" / "001_first_slice.md").write_text(
        _CODING_PROMPT, encoding="utf-8"
    )
    if self_report is not None:
        (root / "05_governance" / "reviews" / "m001_s01_self_report.md").write_text(
            self_report, encoding="utf-8"
        )


class LayoutDrivenLoopResumeTests(unittest.TestCase):
    def test_v2_plain_heading_report_advances_to_make_review_prompt(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, self_report=_v2_self_report())
            resume = build_loop_resume_status(build_status(root))
        # The v2 self-report written to the configured (plain-heading) contract is
        # accepted, so the loop advances past fix_self_report.
        self.assertEqual(resume.step, LoopResumeStep.MAKE_REVIEW_PROMPT)

    def test_missing_configured_heading_fails_with_named_diagnostic(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, self_report=_v2_self_report(omit="Verification Run"))
            resume = build_loop_resume_status(build_status(root))
        self.assertEqual(resume.step, LoopResumeStep.FIX_SELF_REPORT)
        # The diagnostic names the configured heading exactly (display casing).
        self.assertTrue(
            any("self-report missing required field: Verification Run" in d for d in resume.diagnostics),
            msg=str(resume.diagnostics),
        )

    def test_status_json_advertises_and_accepts_the_same_headings(self) -> None:
        out = StringIO()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, self_report=_v2_self_report())
            with redirect_stdout(out):
                code = main(["status", str(root), "--json"])
            resume = build_loop_resume_status(build_status(root))
        self.assertEqual(code, 0)
        import json

        headings = json.loads(out.getvalue())["layout"]["profile"]["self_report_required_headings"]
        self.assertEqual(tuple(headings), V2_HEADINGS)
        # The advertised contract is the same one that just validated the report.
        self.assertEqual(resume.step, LoopResumeStep.MAKE_REVIEW_PROMPT)


class LayoutDrivenMakeReviewPromptTests(unittest.TestCase):
    def test_make_review_prompt_accepts_v2_self_report(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, self_report=_v2_self_report())
            plan = build_review_prompt_plan(root)
        self.assertTrue(plan.valid, msg=str(plan.errors))
        self.assertIsNotNone(plan.evidence)
        assert plan.evidence is not None
        self.assertIn("08_pkg/src/frutlups/example.py", plan.evidence.expected_changed_files)
        self.assertTrue(
            any("unittest" in c for c in plan.evidence.verification_commands),
            msg=str(plan.evidence.verification_commands),
        )


class LayoutDrivenReviewerHandoffTests(unittest.TestCase):
    def test_handoff_evidence_derived_from_verification_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, self_report=_v2_self_report())
            handoff = build_reviewer_handoff(root)
        # Reviewer-handoff evidence honors the same configured schema: it derives
        # the changed file and the verification command from "Verification Run".
        self.assertIn("08_pkg/src/frutlups/example.py", handoff.content)
        self.assertIn("python -m unittest discover -s tests", handoff.content)


class SchemaContractTests(unittest.TestCase):
    def test_custom_schema_from_headings_is_valid(self) -> None:
        schema = self_report_schema_from_headings(("Intent", "Verification Run"))
        self.assertEqual(validate_self_report_schema(schema), ())

    def test_custom_schema_duplicates_and_blanks_still_fail(self) -> None:
        self.assertTrue(validate_self_report_schema(SelfReportSchema(required_fields=("a", "a"))))
        self.assertTrue(validate_self_report_schema(SelfReportSchema(required_fields=("", "b"))))
        self.assertTrue(validate_self_report_schema(SelfReportSchema(required_fields=())))


if __name__ == "__main__":
    unittest.main()
