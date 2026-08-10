"""Contained nested review discovery (layout ``reports.discovery``).

Final-form tests for the owner-authorized compatibility correction: a
configured reviews root may contain milestone subdirectories when the layout
declares ``reports: discovery: "recursive_contained"``. Discovery remains one
bounded deterministic contained inventory of ordinary files; flat layouts
retain identical behavior.
"""

from __future__ import annotations

import json
import os
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from frutlups.cli import main
from frutlups.layout import load_layout_profile
from frutlups.project import (
    LoopResumeStep,
    _collect_acceptance_evidence,
    build_loop_resume_status,
    build_status,
)

PASS_REPORT = """# Review Report

## Findings

None.

Verdict: pass - next: record the verdict
"""

NEEDS_WORK_REPORT = """# Review Report

## Findings

One finding.

Verdict: needs_work - next: repair
"""


def record_text(slice_id: str, report_rel: str, next_slice: str = "M001-S02") -> str:
    milestone = slice_id.split("-")[0]
    return (
        f"# Verdict Record: {slice_id}\n"
        "\n"
        "## Source\n"
        "\n"
        f"Review report: `{report_rel}`\n"
        "\n"
        "## Slice\n"
        "\n"
        f"Slice ID: `{slice_id}`\n"
        "Title: fixture slice\n"
        f"Milestone: `{milestone}`\n"
        "\n"
        "## Parsed Verdict\n"
        "\n"
        "Verdict: `pass`\n"
        "\n"
        "## Next Action\n"
        "\n"
        "Kind: `advance_to_next_slice`\n"
        f"Next slice: `{next_slice}`\n"
        "Message: accepted; advance\n"
    )


LAYOUT_RECURSIVE = """schema_version: frutlups_layout_config_v0
profile_id: artifact_first_template_v3
prompts:
  coding_prompt_dir: "prompts/for_coding_agent"
  review_prompt_dir: "prompts/for_review_agent"
reports:
  reviews_dir: "05_governance/reviews"
  discovery: "recursive_contained"
"""


def make_project(root: Path, *, layout: str | None = LAYOUT_RECURSIVE) -> Path:
    (root / "00_brief").mkdir(parents=True)
    (root / "prompts" / "for_coding_agent").mkdir(parents=True)
    (root / "prompts" / "for_review_agent").mkdir(parents=True)
    (root / "03_experiments").mkdir()
    (root / "05_governance" / "reviews").mkdir(parents=True)
    (root / "PROJECT_STATE.md").write_text("# Project State\n", encoding="utf-8")
    (root / "03_experiments" / "active_roadmap_fixture.md").write_text(
        "# Roadmap\n\n### M001: First Milestone\n\nStatus: active\n",
        encoding="utf-8",
    )
    (root / "03_experiments" / "development_roadmap_fixture.md").write_text(
        "# Development Roadmap\n\n### M001: First Milestone\n\nStatus: active\n"
        "\nSlices:\n\n- M001-S01: first fixture slice\n- M001-S02: second fixture slice\n",
        encoding="utf-8",
    )
    if layout is not None:
        (root / "frutlups.layout.yaml").write_text(layout, encoding="utf-8")
    return root


def write(root: Path, rel: str, text: str) -> Path:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


class NestedDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = make_project(Path(self._tmp.name) / "project")

    def evidence(self):
        loaded = load_layout_profile(self.root)
        return _collect_acceptance_evidence(self.root, loaded.profile)

    def test_nested_pass_report_is_discovered_and_accepted(self) -> None:
        write(
            self.root,
            "05_governance/reviews/m001/m001_s01_first_review_report.md",
            PASS_REPORT,
        )
        evidence = self.evidence()
        self.assertIn("M001-S01", evidence.accepted_slice_ids)
        self.assertIn(
            "05_governance/reviews/m001/m001_s01_first_review_report.md",
            evidence.pass_reports,
        )

    def test_nested_unrecorded_pass_surfaces_record_verdict_beside_report(self) -> None:
        write(
            self.root,
            "05_governance/reviews/m001/m001_s01_first_review_report.md",
            PASS_REPORT,
        )
        resume = build_loop_resume_status(self.root)
        self.assertEqual(resume.step, LoopResumeStep.RECORD_VERDICT)
        self.assertEqual(
            resume.review_report_path,
            "05_governance/reviews/m001/m001_s01_first_review_report.md",
        )
        self.assertEqual(
            resume.verdict_record_path,
            "05_governance/reviews/m001/m001_s01_first_verdict_record.md",
        )

    def test_nested_record_report_pairing_accepts_without_contradiction(self) -> None:
        report_rel = "05_governance/reviews/m001/m001_s01_first_review_report.md"
        write(self.root, report_rel, PASS_REPORT)
        write(
            self.root,
            "05_governance/reviews/m001/m001_s01_first_verdict_record.md",
            record_text("M001-S01", report_rel),
        )
        evidence = self.evidence()
        self.assertIn("M001-S01", evidence.accepted_slice_ids)
        self.assertEqual(evidence.contradictions, ())
        self.assertEqual(evidence.unrecorded_pass_reports, ())

    def test_same_basename_in_distinct_directories_stays_distinct(self) -> None:
        write(
            self.root,
            "05_governance/reviews/m001/m001_s01_first_review_report.md",
            PASS_REPORT,
        )
        write(
            self.root,
            "05_governance/reviews/archive/m001_s01_first_review_report.md",
            NEEDS_WORK_REPORT,
        )
        evidence = self.evidence()
        self.assertIn(
            "05_governance/reviews/m001/m001_s01_first_review_report.md",
            evidence.pass_reports,
        )
        self.assertNotIn(
            "05_governance/reviews/archive/m001_s01_first_review_report.md",
            evidence.pass_reports,
        )
        self.assertIn("M001-S01", evidence.accepted_slice_ids)

    def test_nested_record_with_missing_report_is_contradiction(self) -> None:
        write(
            self.root,
            "05_governance/reviews/m001/m001_s02_second_verdict_record.md",
            record_text(
                "M001-S02",
                "05_governance/reviews/m001/m001_s02_second_review_report.md",
            ),
        )
        evidence = self.evidence()
        self.assertEqual(len(evidence.contradictions), 1)
        contradiction = evidence.contradictions[0]
        self.assertEqual(
            contradiction.record_path,
            "05_governance/reviews/m001/m001_s02_second_verdict_record.md",
        )
        self.assertEqual(
            contradiction.report_path,
            "05_governance/reviews/m001/m001_s02_second_review_report.md",
        )

    def test_nested_citation_across_directories_pairs_when_contained(self) -> None:
        report_rel = "05_governance/reviews/m001/m001_s01_first_review_report.md"
        write(self.root, report_rel, PASS_REPORT)
        write(
            self.root,
            "05_governance/reviews/m001_s01_first_verdict_record.md",
            record_text("M001-S01", report_rel),
        )
        evidence = self.evidence()
        self.assertEqual(evidence.contradictions, ())
        self.assertEqual(evidence.unrecorded_pass_reports, ())

    def test_escaping_citation_stays_unsafe(self) -> None:
        write(
            self.root,
            "05_governance/reviews/m001/m001_s01_first_review_report.md",
            PASS_REPORT,
        )
        write(
            self.root,
            "05_governance/reviews/m001/m001_s01_first_verdict_record.md",
            record_text(
                "M001-S01",
                "05_governance/reviews/../../escape_review_report.md",
            ),
        )
        evidence = self.evidence()
        self.assertEqual(len(evidence.contradictions), 1)
        self.assertEqual(evidence.contradictions[0].kind.value, "unsafe_citation")

    def test_ordering_is_deterministic_by_repo_relative_path(self) -> None:
        write(
            self.root,
            "05_governance/reviews/m002/m002_s01_b_review_report.md",
            PASS_REPORT,
        )
        write(
            self.root,
            "05_governance/reviews/m001/m001_s01_a_review_report.md",
            PASS_REPORT,
        )
        write(
            self.root,
            "05_governance/reviews/m001_s02_flat_review_report.md",
            PASS_REPORT,
        )
        first = self.evidence()
        second = self.evidence()
        self.assertEqual(first.pass_reports, second.pass_reports)
        self.assertEqual(
            tuple(first.pass_reports),
            tuple(sorted(first.pass_reports)),
            "pass reports must be ordered by repo-relative path",
        )

    def test_symlinked_entries_never_become_evidence(self) -> None:
        real_target = write(
            self.root,
            "05_governance/decoy_review_report_source.md",
            PASS_REPORT,
        )
        link = self.root / "05_governance/reviews/m001/m001_s09_link_review_report.md"
        link.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(real_target, link)
        except OSError as error:
            code = getattr(error, "winerror", None) or error.errno
            self.skipTest(
                f"host refuses link creation without elevation (error {code})"
            )
        evidence = self.evidence()
        self.assertNotIn("M001-S09", evidence.accepted_slice_ids)
        self.assertEqual(
            [rel for rel in evidence.pass_reports if "m001_s09" in rel], []
        )

    def test_cli_status_reports_nested_acceptance(self) -> None:
        report_rel = "05_governance/reviews/m001/m001_s01_first_review_report.md"
        write(self.root, report_rel, PASS_REPORT)
        write(
            self.root,
            "05_governance/reviews/m001/m001_s01_first_verdict_record.md",
            record_text("M001-S01", report_rel),
        )
        out = StringIO()
        with redirect_stdout(out):
            code = main(["status", str(self.root), "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertIn("M001-S01", payload["accepted_slice_ids"])
        self.assertEqual(payload["next_slice"]["id"], "M001-S02")


class FlatRegressionTests(unittest.TestCase):
    """Without ``discovery: recursive_contained`` the flat behavior is
    byte-identical: nested entries are invisible and never evidence."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = make_project(Path(self._tmp.name) / "project", layout=None)

    def test_flat_default_ignores_nested_directories(self) -> None:
        write(
            self.root,
            "05_governance/reviews/m001/m001_s01_first_review_report.md",
            PASS_REPORT,
        )
        loaded = load_layout_profile(self.root)
        self.assertEqual(loaded.profile.reports_discovery, "flat")
        evidence = _collect_acceptance_evidence(self.root, loaded.profile)
        self.assertEqual(evidence.accepted_slice_ids, ())
        self.assertEqual(evidence.pass_reports, ())

    def test_flat_files_still_accept_identically(self) -> None:
        write(
            self.root,
            "05_governance/reviews/m001_s01_first_review_report.md",
            PASS_REPORT,
        )
        status, _resume = build_status(self.root), None
        self.assertIn("M001-S01", status.accepted_slice_ids)


if __name__ == "__main__":
    unittest.main()
