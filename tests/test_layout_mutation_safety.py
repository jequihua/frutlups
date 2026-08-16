"""Tests for M002-S04: fail-closed mutation and labeled read-only fallback.

One private typed policy classifies error-severity selected-layout
diagnostics. Under such a diagnostic every write-capable command form refuses
with a named, bounded refusal before any artifact or journal write, while
read-only commands keep working against the safe fallback profile with a
clear label. Warning/info-only layouts and accepted layouts keep their
existing behavior exactly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import yaml

import frutlups
from frutlups.cli import main
from frutlups.gate import build_human_gate, write_final_handoff_artifact
from frutlups.journal import journal_path_for
from frutlups.orchestrator import (
    _run_one_step_from_status,
    build_orchestrator_plan,
    run_one_step,
)
from frutlups.project import build_status

from test_make_review_prompt import _simple_review_project


# ---------------------------------------------------------------------------
# Fixtures
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


def _write_roadmaps(root: Path) -> None:
    (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
        "### M001: First\n\nStatus: active\n", encoding="utf-8"
    )
    (root / "03_experiments" / "development_roadmap_frutlups.md").write_text(
        "### M001: First\n\nSlices:\n\n- M001-S01: one\n- M001-S02: two\n",
        encoding="utf-8",
    )


def _base_project(root: Path) -> None:
    """A legacy-profile project with a live frontier (good layout).

    M003-S04: declares the explicitly supported runner posture so automated
    one-step execution tests exercise execution, not the posture refusal.
    """

    _make_template(root)
    _write_roadmaps(root)
    (root / "frutlups.layout.yaml").write_text(
        "schema_version: frutlups_layout_config_v0\n"
        "profile_id: artifact_first_template_legacy_root\n"
        "automation_boundary:\n"
        "  runner_implemented: true\n",
        encoding="utf-8",
    )


def _write_bad_unreadable_config(root: Path) -> None:
    (root / "frutlups.layout.yaml").write_text('a: "unterminated\n', encoding="utf-8")


def _write_unsafe_path_config(root: Path) -> None:
    (root / "frutlups.layout.yaml").write_text(
        "schema_version: frutlups_layout_config_v0\n"
        "profile_id: artifact_first_template_legacy_root\n"
        "prompts:\n"
        '  coding_prompt_dir: "../escape/coding"\n',
        encoding="utf-8",
    )


def _write_warning_only_config(root: Path) -> None:
    (root / "frutlups.layout.yaml").write_text(
        "schema_version: frutlups_layout_config_v999\n"
        "profile_id: artifact_first_template_legacy_root\n"
        "automation_boundary:\n"
        "  runner_implemented: true\n",
        encoding="utf-8",
    )


def _write_info_only_config(root: Path) -> None:
    (root / "frutlups.layout.yaml").write_text(
        "schema_version: frutlups_layout_config_v0\n"
        "profile_id: artifact_first_template_legacy_root\n"
        "compatibility:\n"
        "  frutlups_source: 'C:\\machine\\local'\n",
        encoding="utf-8",
    )


def _verdict_project(root: Path) -> Path:
    """A legacy project where record-verdict would otherwise write."""

    _make_template(root)
    _write_roadmaps(root)
    report = root / "05_governance" / "reviews" / "m001_s01_one_review_report.md"
    report.write_text("# Review\n\n## Verdict\n\npass\n", encoding="utf-8")
    return report


def _make_v2_project(root: Path) -> None:
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
    (root / "frutlups.layout.yaml").write_text(
        "schema_version: frutlups_layout_config_v0\n"
        "profile_id: artifact_first_template_v2\n"
        "prompts:\n"
        "  required_coding_prompt_sections:\n"
        "    - Read First\n",
        encoding="utf-8",
    )
    (root / "PROJECT_STATE.md").write_text(
        "# Project State\n\nMemory mode:\n- none\n\nFrutlups mode:\n- manual\n",
        encoding="utf-8",
    )
    _write_roadmaps(root)


def _snapshot(root: Path) -> dict[str, str]:
    """Byte-for-byte filesystem snapshot: relative path -> dir marker or digest."""

    snap: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        snap[rel] = "dir" if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest()
    return snap


def _run(args: list[str]) -> tuple[int, str, str]:
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(args)
    return code, out.getvalue(), err.getvalue()


_REFUSAL = "layout mutation refused"
_LABEL = "layout fallback active"


class _BadLayoutCase(unittest.TestCase):
    """Shared helper: build a fixture, poison the layout, snapshot it."""

    def _project(self, poison: str = "unreadable", fixture=_base_project):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        fixture(root)
        if poison == "unreadable":
            _write_bad_unreadable_config(root)
        elif poison == "unsafe_path":
            _write_unsafe_path_config(root)
        return root


# ---------------------------------------------------------------------------
# The six mutating CLI forms refuse before any write (text and JSON).
# ---------------------------------------------------------------------------


class MutatingCliRefusalTests(_BadLayoutCase):
    def _assert_refusal(self, root: Path, args: list[str], *, json_mode: bool) -> None:
        before = _snapshot(root)
        code, out, err = _run(args)
        self.assertEqual(code, 2, (args, out, err))
        self.assertIn(_REFUSAL, err)
        self.assertIn("config_unreadable", err)
        if json_mode:
            json.loads(out)  # stdout stays parseable
        self.assertEqual(_snapshot(root), before)

    def test_make_coding_prompt_text(self) -> None:
        root = self._project()
        self._assert_refusal(root, ["make-coding-prompt", str(root)], json_mode=False)

    def test_make_coding_prompt_json(self) -> None:
        root = self._project()
        self._assert_refusal(
            root, ["make-coding-prompt", str(root), "--json"], json_mode=True
        )

    def test_declare_rework_text(self) -> None:
        root = self._project()
        self._assert_refusal(
            root,
            [
                "declare-rework",
                str(root),
                "--pass-id",
                "holistic_pass_001",
                "--slice",
                "M001-S01",
            ],
            json_mode=False,
        )

    def test_declare_rework_json(self) -> None:
        root = self._project()
        self._assert_refusal(
            root,
            [
                "declare-rework",
                str(root),
                "--pass-id",
                "holistic_pass_001",
                "--slice",
                "M001-S01",
                "--json",
            ],
            json_mode=True,
        )

    def test_make_review_prompt_text(self) -> None:
        root = self._project(fixture=_simple_review_project)
        self._assert_refusal(root, ["make-review-prompt", str(root)], json_mode=False)

    def test_make_review_prompt_json(self) -> None:
        root = self._project(fixture=_simple_review_project)
        self._assert_refusal(
            root, ["make-review-prompt", str(root), "--json"], json_mode=True
        )

    def test_record_verdict_text(self) -> None:
        root = self._verdict_root()
        report = root / "05_governance" / "reviews" / "m001_s01_one_review_report.md"
        self._assert_refusal(
            root, ["record-verdict", str(root), "--review-report", str(report)],
            json_mode=False,
        )

    def test_record_verdict_json(self) -> None:
        root = self._verdict_root()
        report = root / "05_governance" / "reviews" / "m001_s01_one_review_report.md"
        self._assert_refusal(
            root,
            ["record-verdict", str(root), "--review-report", str(report), "--json"],
            json_mode=True,
        )

    def _verdict_root(self) -> Path:
        return self._project(fixture=_verdict_project)

    def test_orchestrator_run_text(self) -> None:
        root = self._project()
        self._assert_refusal(root, ["orchestrator-run", str(root)], json_mode=False)

    def test_orchestrator_run_json(self) -> None:
        root = self._project()
        self._assert_refusal(root, ["orchestrator-run", str(root), "--json"], json_mode=True)

    def test_orchestrator_handoff_write_text(self) -> None:
        root = self._project()
        before = _snapshot(root)
        code, out, err = _run(["orchestrator-handoff", str(root), "--write"])
        self.assertEqual(code, 2, (out, err))
        self.assertIn(_REFUSAL, err)
        self.assertIn("config_unreadable", err)
        self.assertEqual(_snapshot(root), before)

    def test_orchestrator_handoff_write_json(self) -> None:
        root = self._project()
        before = _snapshot(root)
        code, out, err = _run(["orchestrator-handoff", str(root), "--write", "--json"])
        self.assertEqual(code, 2, (out, err))
        payload = json.loads(out)
        self.assertIn(_REFUSAL, payload["write_result"]["errors"][0])
        self.assertFalse(payload["write_result"]["wrote"])
        self.assertEqual(_snapshot(root), before)

    def test_record_verdict_fixture_precondition(self) -> None:
        # The verdict fixture establishes a writable precondition: without the
        # bad layout the same command writes (proved in preservation tests).
        root = self._verdict_root()
        self.assertTrue(
            (root / "05_governance" / "reviews" / "m001_s01_one_review_report.md").is_file()
        )


# ---------------------------------------------------------------------------
# Read-only forms stay available, clearly labeled, and non-mutating.
# ---------------------------------------------------------------------------


class ReadOnlyFallbackTests(_BadLayoutCase):
    def _assert_labeled_read_only(self, root: Path, args: list[str], *, json_mode: bool) -> None:
        before = _snapshot(root)
        code, out, err = _run(args)
        self.assertEqual(code, 0, (args, out, err))
        self.assertIn(_LABEL, err)
        self.assertIn("mutation not authorized", err)
        self.assertNotIn("Traceback", err)
        if json_mode:
            json.loads(out)
        self.assertEqual(_snapshot(root), before)

    def test_status(self) -> None:
        root = self._project()
        self._assert_labeled_read_only(root, ["status", str(root)], json_mode=False)

    def test_status_json(self) -> None:
        root = self._project()
        self._assert_labeled_read_only(root, ["status", str(root), "--json"], json_mode=True)

    def test_next(self) -> None:
        root = self._project()
        self._assert_labeled_read_only(root, ["next", str(root)], json_mode=False)

    def test_orchestrator_plan_json(self) -> None:
        root = self._project()
        self._assert_labeled_read_only(
            root, ["orchestrator-plan", str(root), "--json"], json_mode=True
        )

    def test_make_coding_prompt_dry_run(self) -> None:
        root = self._project()
        self._assert_labeled_read_only(
            root, ["make-coding-prompt", str(root), "--dry-run"], json_mode=False
        )

    def test_declare_rework_dry_run(self) -> None:
        root = self._project()
        self._assert_labeled_read_only(
            root,
            [
                "declare-rework",
                str(root),
                "--pass-id",
                "holistic_pass_001",
                "--slice",
                "M001-S01",
                "--dry-run",
            ],
            json_mode=False,
        )

    def test_make_review_prompt_dry_run(self) -> None:
        root = self._project(fixture=_simple_review_project)
        self._assert_labeled_read_only(
            root, ["make-review-prompt", str(root), "--dry-run"], json_mode=False
        )

    def test_record_verdict_dry_run(self) -> None:
        root = self._verdict_root()
        report = root / "05_governance" / "reviews" / "m001_s01_one_review_report.md"
        self._assert_labeled_read_only(
            root,
            ["record-verdict", str(root), "--review-report", str(report), "--dry-run"],
            json_mode=False,
        )

    def test_orchestrator_run_dry_run_journals_nothing(self) -> None:
        root = self._project()
        self._assert_labeled_read_only(
            root, ["orchestrator-run", str(root), "--dry-run"], json_mode=False
        )
        self.assertFalse(journal_path_for(root).exists())

    def test_orchestrator_handoff_read_only(self) -> None:
        root = self._project()
        self._assert_labeled_read_only(root, ["orchestrator-handoff", str(root)], json_mode=False)

    def _verdict_root(self) -> Path:
        return self._project(fixture=_verdict_project)


# ---------------------------------------------------------------------------
# A parsed unsafe-write-path config triggers the same policy by typed severity.
# ---------------------------------------------------------------------------


class UnsafeWritePathTriggerTests(_BadLayoutCase):
    def test_make_coding_prompt_refused_on_unsafe_write_path(self) -> None:
        root = self._project(poison="unsafe_path")
        before = _snapshot(root)
        code, _, err = _run(["make-coding-prompt", str(root)])
        self.assertEqual(code, 2)
        self.assertIn(_REFUSAL, err)
        self.assertIn("unsafe_write_path", err)
        self.assertEqual(_snapshot(root), before)

    def test_orchestrator_run_refused_on_unsafe_write_path(self) -> None:
        root = self._project(poison="unsafe_path")
        before = _snapshot(root)
        code, _, err = _run(["orchestrator-run", str(root)])
        self.assertEqual(code, 2)
        self.assertIn(_REFUSAL, err)
        self.assertIn("unsafe_write_path", err)
        self.assertEqual(_snapshot(root), before)


# ---------------------------------------------------------------------------
# Warning-only and info-only layouts never acquire mutation-blocking authority.
# ---------------------------------------------------------------------------


class WarningAndInfoOnlyLayoutTests(_BadLayoutCase):
    def _assert_mutation_allowed(self, root: Path) -> None:
        before_count = len(_snapshot(root))
        code, _, err = _run(["make-coding-prompt", str(root)])
        self.assertEqual(code, 0, err)
        self.assertNotIn(_REFUSAL, err)
        self.assertNotIn(_LABEL, err)
        self.assertGreater(len(_snapshot(root)), before_count)

    def test_warning_only_schema_version_does_not_block(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        _base_project(root)
        _write_warning_only_config(root)
        self._assert_mutation_allowed(root)

    def test_info_only_advisory_path_does_not_block(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        _base_project(root)
        _write_info_only_config(root)
        self._assert_mutation_allowed(root)

    def test_warning_only_orchestrator_run_writes_and_journals(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        _base_project(root)
        _write_warning_only_config(root)
        code, _, err = _run(["orchestrator-run", str(root)])
        self.assertEqual(code, 0, err)
        self.assertNotIn(_REFUSAL, err)
        self.assertTrue(journal_path_for(root).is_file())
        prompts = list((root / "prompts" / "for_coding_agent").glob("*.md"))
        self.assertEqual(len(prompts), 1)


# ---------------------------------------------------------------------------
# Composite library entry points guard as defense in depth.
# ---------------------------------------------------------------------------


class CompositeGuardTests(_BadLayoutCase):
    def test_run_one_step_dry_run_no_artifact_no_journal(self) -> None:
        root = self._project()
        before = _snapshot(root)
        result = run_one_step(root, dry_run=True, journal=True)
        self.assertFalse(result.attempted)
        self.assertFalse(result.wrote)
        self.assertFalse(result.refused)
        self.assertTrue(any("layout fallback active" in d for d in result.diagnostics))
        self.assertFalse(journal_path_for(root).exists())
        self.assertEqual(_snapshot(root), before)

    def test_run_one_step_execute_refuses_before_write_and_journal(self) -> None:
        root = self._project()
        before = _snapshot(root)
        result = run_one_step(root, dry_run=False, journal=True)
        self.assertTrue(result.refused)
        self.assertTrue(result.refusal_reason.startswith(_REFUSAL))
        self.assertIn("config_unreadable", result.refusal_reason)
        self.assertFalse(result.attempted)
        self.assertFalse(result.wrote)
        self.assertFalse(journal_path_for(root).exists())
        self.assertEqual(_snapshot(root), before)

    def test_write_final_handoff_artifact_refuses_before_creating_anything(self) -> None:
        root = self._project()
        before = _snapshot(root)
        handoff, result = write_final_handoff_artifact(root)
        self.assertFalse(result.wrote)
        self.assertEqual(len(result.errors), 1)
        self.assertIn(_REFUSAL, result.errors[0])
        self.assertFalse((root / "05_governance" / "orchestrator").exists())
        self.assertIsNotNone(handoff.gate)
        self.assertEqual(_snapshot(root), before)

    def test_low_level_writers_patched_to_raise_are_never_reached(self) -> None:
        root = self._project(fixture=_simple_review_project)
        report_root = self._verdict_root()
        report = report_root / "05_governance" / "reviews" / "m001_s01_one_review_report.md"
        boom = AssertionError("writer reached under a bad layout")
        with (
            mock.patch("frutlups.cli.write_coding_prompt", side_effect=boom),
            mock.patch("frutlups.cli.write_review_prompt", side_effect=boom),
            mock.patch("frutlups.cli.write_verdict_record", side_effect=boom),
            mock.patch("frutlups.gate.write_final_handoff", side_effect=boom),
            mock.patch("frutlups.orchestrator.append_run_journal_entry", side_effect=boom),
            mock.patch("frutlups.orchestrator.write_coding_prompt", side_effect=boom),
            mock.patch("frutlups.orchestrator.write_review_prompt", side_effect=boom),
            mock.patch("frutlups.orchestrator.write_verdict_record", side_effect=boom),
        ):
            self.assertEqual(_run(["make-coding-prompt", str(root)])[0], 2)
            self.assertEqual(_run(["make-review-prompt", str(root)])[0], 2)
            self.assertEqual(
                _run(["record-verdict", str(report_root), "--review-report", str(report)])[0], 2
            )
            self.assertEqual(_run(["orchestrator-run", str(root)])[0], 2)
            self.assertEqual(_run(["orchestrator-run", str(root), "--dry-run"])[0], 0)
            self.assertEqual(_run(["orchestrator-handoff", str(root), "--write"])[0], 2)
            result = run_one_step(root, dry_run=False, journal=True)
            self.assertTrue(result.refused)

    def _verdict_root(self) -> Path:
        return self._project(fixture=_verdict_project)


# ---------------------------------------------------------------------------
# Planner and human gate never contradict the mutation refusal.
# ---------------------------------------------------------------------------


class PlanAndGateTests(_BadLayoutCase):
    def test_plan_never_auto_safe_under_bad_layout(self) -> None:
        root = self._project()
        plan = build_orchestrator_plan(root)
        self.assertFalse(plan.safe_for_auto_execution)
        self.assertIn("mutation not authorized", plan.rationale)
        self.assertIn("config_unreadable", plan.rationale)

    def test_gate_never_open_under_bad_layout(self) -> None:
        root = self._project()
        gate = build_human_gate(root)
        self.assertNotEqual(gate.gate_state, "open")
        self.assertTrue(gate.requires_human_go)
        self.assertFalse(gate.safe_for_auto_execution)

    def test_plan_and_gate_unchanged_under_good_layout(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        _base_project(root)
        plan = build_orchestrator_plan(root)
        self.assertTrue(plan.safe_for_auto_execution)
        self.assertNotIn("mutation not authorized", plan.rationale)
        gate = build_human_gate(root)
        self.assertEqual(gate.gate_state, "open")


# ---------------------------------------------------------------------------
# Accepted layouts preserve command, journal, dry-run, and generated behavior.
# ---------------------------------------------------------------------------


class AcceptedLayoutPreservationTests(unittest.TestCase):
    def test_legacy_layout_write_paths_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _base_project(root)
            code, _, err = _run(["make-coding-prompt", str(root)])
            self.assertEqual(code, 0, err)
            self.assertEqual(err, "")
            prompts = sorted((root / "prompts" / "for_coding_agent").glob("*.md"))
            self.assertEqual(len(prompts), 1)
            content = prompts[0].read_text(encoding="utf-8")
            self.assertIn("# Coding Prompt", content)
            self.assertIn("M001-S01", content)

            code, _, _ = _run(["orchestrator-run", str(root)])
            self.assertEqual(code, 0)
            self.assertTrue(journal_path_for(root).is_file())

            code, _, _ = _run(["orchestrator-handoff", str(root), "--write"])
            self.assertEqual(code, 0)
            self.assertTrue(
                (root / "05_governance" / "orchestrator" / "m016_final_handoff.md").is_file()
            )

    def test_legacy_dry_run_has_no_label(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _base_project(root)
            before = _snapshot(root)
            code, out, err = _run(["make-coding-prompt", str(root), "--dry-run"])
            self.assertEqual(code, 0)
            self.assertNotIn(_LABEL, err)
            self.assertNotIn(_REFUSAL, err)
            self.assertEqual(_snapshot(root), before)

    def test_generated_prompt_bytes_are_deterministic(self) -> None:
        contents = []
        for _ in range(2):
            with TemporaryDirectory() as tmp:
                root = Path(tmp)
                _base_project(root)
                self.assertEqual(_run(["make-coding-prompt", str(root)])[0], 0)
                prompt = next((root / "prompts" / "for_coding_agent").glob("*.md"))
                contents.append(prompt.read_bytes())
        self.assertEqual(contents[0], contents[1])

    def test_v2_layout_write_path_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            code, _, err = _run(["make-coding-prompt", str(root)])
            self.assertEqual(code, 0, err)
            self.assertEqual(err, "")
            prompts = sorted((root / "prompts" / "for_coding_agent").glob("*.md"))
            self.assertEqual(len(prompts), 1)

    # The accepted template-v3 layout ships as an immutable, package-relative fixture
    # so this mutation-authority check runs from the flattened front-facing checkout.
    _SHIPPED_V3 = (
        Path(__file__).resolve().parent / "fixtures" / "front_repo_contract" / "frutlups.layout.yaml"
    )

    @unittest.skipUnless(_SHIPPED_V3.is_file(), "shipped template-v3 config not present")
    def test_template_v3_layout_keeps_mutation_authority(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            (root / "frutlups.layout.yaml").write_text(
                self._SHIPPED_V3.read_text(encoding="utf-8"), encoding="utf-8"
            )
            code, out, err = _run(["status", str(root), "--json"])
            self.assertEqual(code, 0)
            self.assertNotIn(_LABEL, err)
            payload = json.loads(out)
            error_diags = [
                d
                for d in payload["layout"]["diagnostics"]
                if d["severity"] == "error"
            ]
            self.assertEqual(error_diags, [])
            code, _, err = _run(["make-coding-prompt", str(root)])
            self.assertEqual(code, 0, err)
            self.assertNotIn(_REFUSAL, err)


# ---------------------------------------------------------------------------
# Refusal and label text stay bounded and echo nothing hostile.
# ---------------------------------------------------------------------------


class BoundedTextTests(_BadLayoutCase):
    HOSTILE = "X43Q_HOSTILE <script> 'C:\\evil\\secret'"

    def _project_with_hostile_config(self) -> Path:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        _base_project(root)
        (root / "frutlups.layout.yaml").write_text(
            f'"{self.HOSTILE}": "ok"\nbad: "unterminated\n', encoding="utf-8"
        )
        return root

    def test_refusal_text_bounded_and_echo_free(self) -> None:
        root = self._project_with_hostile_config()
        _, _, err = _run(["make-coding-prompt", str(root)])
        self.assertIn(_REFUSAL, err)
        self.assertNotIn(self.HOSTILE, err)
        self.assertNotIn(str(root), err)
        self.assertNotIn("Traceback", err)
        for line in err.splitlines():
            self.assertLessEqual(len(line), 240)

    def test_label_text_bounded_and_echo_free(self) -> None:
        root = self._project_with_hostile_config()
        _, _, err = _run(["status", str(root)])
        self.assertIn(_LABEL, err)
        self.assertNotIn(self.HOSTILE, err)
        self.assertNotIn(str(root), err)
        self.assertNotIn("Traceback", err)
        for line in err.splitlines():
            self.assertLessEqual(len(line), 240)


# ---------------------------------------------------------------------------
# Public surface: current verbs plus preserved exports and JSON key sets.
# ---------------------------------------------------------------------------


class PublicSurfaceTests(unittest.TestCase):
    def test_nine_verb_inventory_by_parser_choices(self) -> None:
        from frutlups.cli import _build_parser

        parser = _build_parser()
        subparsers = next(
            action
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        self.assertEqual(
            sorted(subparsers.choices),
            [
                "declare-rework",
                "make-coding-prompt",
                "make-review-prompt",
                "next",
                "orchestrator-handoff",
                "orchestrator-plan",
                "orchestrator-run",
                "record-verdict",
                "status",
            ],
        )

    def test_nine_verb_inventory_by_help_text(self) -> None:
        out = StringIO()
        with redirect_stdout(out):
            with self.assertRaises(SystemExit):
                main(["--help"])
        text = out.getvalue()
        for verb in (
            "declare-rework",
            "status",
            "next",
            "orchestrator-plan",
            "orchestrator-run",
            "orchestrator-handoff",
            "make-review-prompt",
            "make-coding-prompt",
            "record-verdict",
        ):
            self.assertIn(verb, text)

    def test_exports_unchanged_and_no_private_helper_leakage(self) -> None:
        self.assertEqual(len(frutlups.__all__), 152)
        for name in (
            "_layout_mutation_blockers",
            "_layout_mutation_refusal_message",
            "_layout_fallback_label_message",
            "_layout_guarded_plan",
        ):
            self.assertNotIn(name, frutlups.__all__)
            self.assertFalse(hasattr(frutlups, name), name)

    def test_json_key_sets_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _base_project(root)
            code, out, _ = _run(["status", str(root), "--json"])
            self.assertEqual(code, 0)
            payload = json.loads(out)
            self.assertIn("layout", payload)
            self.assertIn("loop_resume", payload)
            self.assertNotIn("layout_safe", payload)
            self.assertNotIn("fallback", payload)

            code, out, _ = _run(["orchestrator-plan", str(root), "--json"])
            self.assertEqual(code, 0)
            plan_payload = json.loads(out)
            self.assertEqual(
                set(plan_payload["plan"] if "plan" in plan_payload else plan_payload)
                & {"layout_safe", "fallback"},
                set(),
            )
            self.assertIn("human_gate", plan_payload)
            gate_keys = set(plan_payload["human_gate"])
            self.assertEqual(
                gate_keys,
                {
                    "gate_state",
                    "requires_human_go",
                    "reason",
                    "recommended_human_action",
                    "loop_step",
                    "actor",
                    "frontier_slice_id",
                    "frontier_slice_title",
                    "recommended_command",
                    "safe_for_auto_execution",
                    "diagnostics",
                },
            )


# ---------------------------------------------------------------------------
# Repeated calls mutate nothing global or local.
# ---------------------------------------------------------------------------


class RepeatedCallPurityTests(_BadLayoutCase):
    def test_repeated_calls_leave_everything_unchanged(self) -> None:
        constructors_before = dict(yaml.SafeLoader.yaml_constructors)
        multi_before = dict(yaml.SafeLoader.yaml_multi_constructors)
        recursion_before = sys.getrecursionlimit()
        environ_before = dict(os.environ)
        root = self._project()
        config_bytes = (root / "frutlups.layout.yaml").read_bytes()
        before = _snapshot(root)
        for _ in range(2):
            self.assertEqual(_run(["status", str(root)])[0], 0)
            self.assertEqual(_run(["make-coding-prompt", str(root)])[0], 2)
            self.assertEqual(_run(["orchestrator-run", str(root)])[0], 2)
        self.assertEqual(_snapshot(root), before)
        self.assertEqual((root / "frutlups.layout.yaml").read_bytes(), config_bytes)
        self.assertEqual(dict(yaml.SafeLoader.yaml_constructors), constructors_before)
        self.assertEqual(dict(yaml.SafeLoader.yaml_multi_constructors), multi_before)
        self.assertEqual(sys.getrecursionlimit(), recursion_before)
        self.assertEqual(dict(os.environ), environ_before)


# ---------------------------------------------------------------------------
# M002-S04 correction (prompt 011): layout authority dominates compound native
# failures, and every guard consumes the one already selected layout.
# ---------------------------------------------------------------------------


def _no_frontier_project(root: Path) -> None:
    """A legacy project whose coding-prompt plan is independently invalid."""

    _make_template(root)


class CompoundPrecedenceTests(_BadLayoutCase):
    """Bad layout plus independently invalid native plans (prompt 011)."""

    def _assert_compound_mutation(
        self, root: Path, args: list[str], *, json_mode: bool, code: str
    ) -> None:
        before = _snapshot(root)
        code_rc, out, err = _run(args)
        self.assertEqual(code_rc, 2, (args, out, err))
        self.assertIn(_REFUSAL, err)
        self.assertIn(code, err)
        self.assertNotIn(_LABEL, err)
        if json_mode:
            payload = json.loads(out)
            self.assertIn("valid", payload)
            self.assertFalse(payload["valid"])
        self.assertEqual(_snapshot(root), before)

    def _assert_compound_dry_run(
        self, root: Path, args: list[str], *, json_mode: bool
    ) -> None:
        before = _snapshot(root)
        code, out, err = _run(args)
        self.assertEqual(code, 0, (args, out, err))
        self.assertIn(_LABEL, err)
        self.assertNotIn(_REFUSAL, err)
        # The independent native plan invalidity stays visible in read-only mode.
        self.assertIn("frutlups:", err.replace(_LABEL, ""))
        if json_mode:
            payload = json.loads(out)
            self.assertIn("valid", payload)
            self.assertFalse(payload["valid"])
        self.assertEqual(_snapshot(root), before)

    def test_coding_prompt_compound_mutation_text(self) -> None:
        root = self._project(fixture=_no_frontier_project)
        self._assert_compound_mutation(
            root, ["make-coding-prompt", str(root)], json_mode=False, code="config_unreadable"
        )

    def test_coding_prompt_compound_mutation_json(self) -> None:
        root = self._project(fixture=_no_frontier_project)
        self._assert_compound_mutation(
            root, ["make-coding-prompt", str(root), "--json"],
            json_mode=True, code="config_unreadable",
        )

    def test_coding_prompt_compound_dry_run_text(self) -> None:
        root = self._project(fixture=_no_frontier_project)
        self._assert_compound_dry_run(
            root, ["make-coding-prompt", str(root), "--dry-run"], json_mode=False
        )

    def test_coding_prompt_compound_dry_run_json(self) -> None:
        root = self._project(fixture=_no_frontier_project)
        self._assert_compound_dry_run(
            root, ["make-coding-prompt", str(root), "--dry-run", "--json"], json_mode=True
        )

    def test_review_prompt_compound_mutation_text(self) -> None:
        root = self._project(fixture=_no_frontier_project)
        self._assert_compound_mutation(
            root, ["make-review-prompt", str(root)], json_mode=False, code="config_unreadable"
        )

    def test_review_prompt_compound_mutation_json(self) -> None:
        root = self._project(fixture=_no_frontier_project)
        self._assert_compound_mutation(
            root, ["make-review-prompt", str(root), "--json"],
            json_mode=True, code="config_unreadable",
        )

    def test_review_prompt_compound_dry_run_text(self) -> None:
        root = self._project(fixture=_no_frontier_project)
        self._assert_compound_dry_run(
            root, ["make-review-prompt", str(root), "--dry-run"], json_mode=False
        )

    def test_review_prompt_compound_dry_run_json(self) -> None:
        root = self._project(fixture=_no_frontier_project)
        self._assert_compound_dry_run(
            root, ["make-review-prompt", str(root), "--dry-run", "--json"], json_mode=True
        )

    def _verdict_compound_args(self, root: Path, *extra: str) -> list[str]:
        missing = root / "05_governance" / "reviews" / "m001_s01_one_review_report.md"
        return ["record-verdict", str(root), "--review-report", str(missing), *extra]

    def test_record_verdict_compound_mutation_text(self) -> None:
        root = self._project(fixture=_no_frontier_project)
        self._assert_compound_mutation(
            root, self._verdict_compound_args(root), json_mode=False, code="config_unreadable"
        )

    def test_record_verdict_compound_mutation_json(self) -> None:
        root = self._project(fixture=_no_frontier_project)
        self._assert_compound_mutation(
            root, self._verdict_compound_args(root, "--json"),
            json_mode=True, code="config_unreadable",
        )

    def test_record_verdict_compound_dry_run_text(self) -> None:
        root = self._project(fixture=_no_frontier_project)
        self._assert_compound_dry_run(
            root, self._verdict_compound_args(root, "--dry-run"), json_mode=False
        )

    def test_record_verdict_compound_dry_run_json(self) -> None:
        root = self._project(fixture=_no_frontier_project)
        self._assert_compound_dry_run(
            root, self._verdict_compound_args(root, "--dry-run", "--json"), json_mode=True
        )

    def test_compound_unsafe_write_path_same_precedence(self) -> None:
        root = self._project(poison="unsafe_path", fixture=_no_frontier_project)
        self._assert_compound_mutation(
            root, ["make-coding-prompt", str(root)], json_mode=False, code="unsafe_write_path"
        )

    def test_compound_writers_patched_to_raise_never_reached(self) -> None:
        root = self._project(fixture=_no_frontier_project)
        boom = AssertionError("writer reached under a blocked compound state")
        missing = root / "05_governance" / "reviews" / "m001_s01_one_review_report.md"
        with (
            mock.patch("frutlups.cli.write_coding_prompt", side_effect=boom),
            mock.patch("frutlups.cli.write_review_prompt", side_effect=boom),
            mock.patch("frutlups.cli.write_verdict_record", side_effect=boom),
        ):
            self.assertEqual(_run(["make-coding-prompt", str(root)])[0], 2)
            self.assertEqual(_run(["make-review-prompt", str(root)])[0], 2)
            self.assertEqual(
                _run(["record-verdict", str(root), "--review-report", str(missing)])[0], 2
            )


class AcceptedLayoutCompoundControlTests(unittest.TestCase):
    """Accepted layouts keep historical invalid-plan exit 1 with no S04 text."""

    def _assert_plain_invalid(self, args: list[str]) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _no_frontier_project(root)
            before = _snapshot(root)
            code, _, err = _run(args(root))
            self.assertEqual(code, 1, (args(root), err))
            self.assertNotIn(_REFUSAL, err)
            self.assertNotIn(_LABEL, err)
            self.assertEqual(_snapshot(root), before)

    def test_coding_prompt_invalid_mutation_exit_1(self) -> None:
        self._assert_plain_invalid(lambda root: ["make-coding-prompt", str(root)])

    def test_coding_prompt_invalid_dry_run_exit_1(self) -> None:
        self._assert_plain_invalid(
            lambda root: ["make-coding-prompt", str(root), "--dry-run"]
        )

    def test_review_prompt_invalid_mutation_exit_1(self) -> None:
        self._assert_plain_invalid(lambda root: ["make-review-prompt", str(root)])

    def test_record_verdict_invalid_mutation_exit_1(self) -> None:
        def args(root: Path) -> list[str]:
            missing = root / "05_governance" / "reviews" / "m001_s01_one_review_report.md"
            return ["record-verdict", str(root), "--review-report", str(missing)]

        self._assert_plain_invalid(args)


class SingleSelectionTests(_BadLayoutCase):
    """The S04 classifier adds no second selected-layout load (prompt 011)."""

    def _count_layout_loads(self, args: list[str]) -> int:
        import frutlups.layout as layout_module

        with mock.patch(
            "frutlups.project.load_layout_profile",
            wraps=layout_module.load_layout_profile,
        ) as counter:
            _run(args)
        return counter.call_count

    def test_status_selects_once(self) -> None:
        root = self._project()
        self.assertEqual(self._count_layout_loads(["status", str(root)]), 1)

    def test_next_selects_once(self) -> None:
        root = self._project()
        self.assertEqual(self._count_layout_loads(["next", str(root)]), 1)

    def test_orchestrator_plan_adds_no_guard_only_load(self) -> None:
        root = self._project()
        # Prompt 031: the journal-resume summary reuses the command's one
        # selected status/resume, so the whole composition selects once.
        self.assertEqual(
            self._count_layout_loads(["orchestrator-plan", str(root)]), 1
        )

    def test_orchestrator_run_dry_adds_no_guard_only_load(self) -> None:
        root = self._project()
        self.assertEqual(
            self._count_layout_loads(["orchestrator-run", str(root), "--dry-run"]), 1
        )

    def test_orchestrator_run_refusal_adds_no_guard_only_load(self) -> None:
        root = self._project()
        self.assertEqual(self._count_layout_loads(["orchestrator-run", str(root)]), 1)

    def test_orchestrator_handoff_read_only_selects_once(self) -> None:
        root = self._project()
        self.assertEqual(self._count_layout_loads(["orchestrator-handoff", str(root)]), 1)

    def test_orchestrator_handoff_write_selects_once(self) -> None:
        root = self._project()
        self.assertEqual(
            self._count_layout_loads(["orchestrator-handoff", str(root), "--write"]), 1
        )

    def test_make_coding_prompt_selects_once(self) -> None:
        root = self._project()
        self.assertEqual(self._count_layout_loads(["make-coding-prompt", str(root)]), 1)

    def test_make_coding_prompt_dry_run_selects_once(self) -> None:
        root = self._project()
        self.assertEqual(
            self._count_layout_loads(["make-coding-prompt", str(root), "--dry-run"]), 1
        )

    def test_make_review_prompt_selects_once(self) -> None:
        root = self._project(fixture=_simple_review_project)
        self.assertEqual(self._count_layout_loads(["make-review-prompt", str(root)]), 1)

    def test_record_verdict_selects_once(self) -> None:
        root = self._project(fixture=_verdict_project)
        report = root / "05_governance" / "reviews" / "m001_s01_one_review_report.md"
        self.assertEqual(
            self._count_layout_loads(
                ["record-verdict", str(root), "--review-report", str(report)]
            ),
            1,
        )

    def test_classifier_and_plan_derive_from_one_selection(self) -> None:
        from frutlups.project import (
            _build_coding_prompt_plan_from_status,
            _layout_mutation_blockers,
            build_coding_prompt_plan,
            build_status,
        )

        root = self._project()
        status = build_status(root)
        blockers = _layout_mutation_blockers(status.layout)
        self.assertTrue(blockers)
        from_status = _build_coding_prompt_plan_from_status(status)
        self.assertEqual(from_status.to_dict(), build_coding_prompt_plan(root).to_dict())


# ---------------------------------------------------------------------------
# M002-S04 correction (prompt 012): accepted executable orchestrator dispatch
# builds every artifact plan from the same already selected status/layout.
# ---------------------------------------------------------------------------


def _count_loads_during(fn) -> int:
    import frutlups.layout as layout_module

    with mock.patch(
        "frutlups.project.load_layout_profile",
        wraps=layout_module.load_layout_profile,
    ) as counter:
        fn()
    return counter.call_count


def _make_review_step_project(root: Path) -> None:
    """A project whose loop step is make_review_prompt (prompt-012 dispatch)."""

    import test_make_review_prompt as mrp

    mrp._make_template(root)
    mrp._write_active_roadmap(
        root,
        "# Active Roadmap\n\n### M001: First\n\nStatus: active\n\n### M002: Second\n\nStatus: planned\n\n",
    )
    mrp._write_detailed_roadmap(
        root,
        "# Detailed Roadmap\n\n### M001: First\n\nSlices:\n\n- M001-S01: test slice\n\n"
        "### M002: Second\n\nSlices:\n\n- M002-S01: next thing\n\n",
    )
    mrp._write_coding_prompt(
        root, mrp._CP_FILENAME_001, mrp._minimal_coding_prompt(1, self_report_path=mrp._SR_PATH_001)
    )
    mrp._write_self_report(root, mrp._SR_PATH_001, mrp._minimal_self_report())
    # M003-S04: successful automated execution requires a supported posture.
    (root / "frutlups.layout.yaml").write_text(
        "schema_version: frutlups_layout_config_v0\n"
        "profile_id: artifact_first_template_legacy_root\n"
        "automation_boundary:\n"
        "  runner_implemented: true\n",
        encoding="utf-8",
    )


class AcceptedDispatchSingleSelectionTests(unittest.TestCase):
    """Accepted non-dry-run dispatch performs no handler re-selection."""

    def test_direct_run_one_step_selects_exactly_once(self) -> None:
        from test_orchestrator import _make_project

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            with mock.patch(
                "frutlups.project.build_coding_prompt_plan",
                side_effect=AssertionError("public builder reached from dispatch"),
            ):
                loads = _count_loads_during(
                    lambda: setattr(self, "_result", run_one_step(root, journal=False))
                )
            result = self._result
            self.assertTrue(result.wrote, result.refusal_reason)
            self.assertEqual(loads, 1)
            prompts = list((root / "prompts" / "for_coding_agent").glob("*.md"))
            self.assertEqual(len(prompts), 1)

    def test_direct_run_one_step_with_journal_selects_exactly_once(self) -> None:
        from test_orchestrator import _make_project

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            loads = _count_loads_during(lambda: run_one_step(root, journal=True))
            self.assertEqual(loads, 1)
            self.assertTrue(journal_path_for(root).is_file())

    def test_cli_orchestrator_run_selects_exactly_twice(self) -> None:
        from test_orchestrator import _make_project

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            loads = _count_loads_during(lambda: _run(["orchestrator-run", str(root)]))
            # Prompt 031: the journal-resume summary reuses the command's one
            # selected status/resume, so the whole composition selects once
            # with no handler re-selection.
            self.assertEqual(loads, 1)
            self.assertTrue(journal_path_for(root).is_file())
            prompts = list((root / "prompts" / "for_coding_agent").glob("*.md"))
            self.assertEqual(len(prompts), 1)

    def test_review_handler_never_calls_public_builder(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_review_step_project(root)
            with mock.patch(
                "frutlups.project.build_review_prompt_plan",
                side_effect=AssertionError("public builder reached from dispatch"),
            ):
                loads = _count_loads_during(
                    lambda: setattr(self, "_result", run_one_step(root, journal=False))
                )
            self.assertTrue(self._result.wrote, self._result.refusal_reason)
            self.assertEqual(loads, 1)
            reviews = list((root / "prompts" / "for_review_agent").glob("*.md"))
            self.assertEqual(len(reviews), 1)

    def test_verdict_handler_never_calls_public_builder(self) -> None:
        from test_orchestrator import _make_record_verdict_project

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_record_verdict_project(root)
            with mock.patch(
                "frutlups.project.build_verdict_record_plan",
                side_effect=AssertionError("public builder reached from dispatch"),
            ):
                loads = _count_loads_during(
                    lambda: setattr(self, "_result", run_one_step(root, journal=False))
                )
            self.assertTrue(self._result.wrote, self._result.refusal_reason)
            self.assertEqual(loads, 1)
            records = list(
                (root / "05_governance" / "reviews").glob("*_verdict_record.md")
            )
            self.assertEqual(len(records), 1)


class DispatchIdentityTests(unittest.TestCase):
    """The guarded plan and each handler share one status/layout snapshot."""

    def test_coding_handler_receives_the_same_status_object(self) -> None:
        import frutlups.orchestrator as orch
        from test_orchestrator import _make_project

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            status = build_status(root)
            with mock.patch.object(
                orch,
                "_build_coding_prompt_plan_from_status",
                wraps=orch._build_coding_prompt_plan_from_status,
            ) as spy:
                result, _policy = orch._run_one_step_from_status(status, journal=False)
            self.assertTrue(result.wrote, result.refusal_reason)
            self.assertIs(spy.call_args.args[0], status)

    def test_review_handler_receives_the_same_status_object(self) -> None:
        import frutlups.orchestrator as orch

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_review_step_project(root)
            status = build_status(root)
            with mock.patch.object(
                orch,
                "_build_review_prompt_plan_from_status",
                wraps=orch._build_review_prompt_plan_from_status,
            ) as spy:
                result, _policy = orch._run_one_step_from_status(status, journal=False)
            self.assertTrue(result.wrote, result.refusal_reason)
            self.assertIs(spy.call_args.args[0], status)

    def test_verdict_handler_receives_the_same_root_and_profile(self) -> None:
        import frutlups.orchestrator as orch
        from test_orchestrator import _make_record_verdict_project

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_record_verdict_project(root)
            status = build_status(root)
            with mock.patch.object(
                orch,
                "_build_verdict_record_plan_from_profile",
                wraps=orch._build_verdict_record_plan_from_profile,
            ) as spy:
                result, _policy = orch._run_one_step_from_status(status, journal=False)
            self.assertTrue(result.wrote, result.refusal_reason)
            args = spy.call_args.args
            self.assertIs(args[0], status.root)
            self.assertIs(args[1], status.layout.profile)


class ConfigChangeSnapshotTests(unittest.TestCase):
    """A config changed after the first load is never reselected by a handler."""

    def test_handler_uses_the_original_snapshot(self) -> None:
        from test_orchestrator import _make_project

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            status = build_status(root)  # accepted layout selected once
            _write_bad_unreadable_config(root)  # config now malformed
            result, _policy = _run_one_step_from_status(status, journal=True)
            self.assertTrue(result.wrote, result.refusal_reason)
            self.assertFalse(result.refused)
            prompts = list((root / "prompts" / "for_coding_agent").glob("*.md"))
            self.assertEqual(len(prompts), 1)
            self.assertTrue(journal_path_for(root).is_file())
            # A fresh invocation now sees the bad layout and refuses.
            fresh = run_one_step(root, journal=False)
            self.assertTrue(fresh.refused)
            self.assertTrue(fresh.refusal_reason.startswith(_REFUSAL))

    def test_cli_write_uses_original_snapshot_when_config_changes_mid_run(self) -> None:
        from test_orchestrator import _make_project

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            real_build_status = build_status

            def corrupt_after_first_load(*args, **kwargs):
                status = real_build_status(*args, **kwargs)
                _write_bad_unreadable_config(root)
                return status

            with mock.patch(
                "frutlups.cli.build_status", side_effect=corrupt_after_first_load
            ):
                code, _, err = _run(["orchestrator-run", str(root)])
            self.assertEqual(code, 0, err)
            self.assertNotIn(_REFUSAL, err)
            prompts = list((root / "prompts" / "for_coding_agent").glob("*.md"))
            self.assertEqual(len(prompts), 1)
            self.assertTrue(journal_path_for(root).is_file())


class ExplicitConfigDispatchTests(unittest.TestCase):
    """Explicit alternate layout configs propagate through accepted dispatch."""

    _CUSTOM_DIR_CONFIG = (
        "schema_version: frutlups_layout_config_v0\n"
        "profile_id: artifact_first_template_legacy_root\n"
        "automation_boundary:\n"
        "  runner_implemented: true\n"
        "prompts:\n"
        '  coding_prompt_dir: "custom_prompts/coding"\n'
    )

    def test_explicit_config_propagates_through_dispatch(self) -> None:
        from test_orchestrator import _make_project

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            cfg = root / "elsewhere.yaml"
            cfg.write_text(self._CUSTOM_DIR_CONFIG, encoding="utf-8")
            result = run_one_step(root, layout_config=cfg, journal=False)
            self.assertTrue(result.wrote, result.refusal_reason)
            custom = list((root / "custom_prompts" / "coding").glob("*.md"))
            self.assertEqual(len(custom), 1)
            self.assertEqual(list((root / "prompts" / "for_coding_agent").glob("*.md")), [])

    def test_cli_explicit_config_execution(self) -> None:
        from test_orchestrator import _make_project

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            cfg = root / "elsewhere.yaml"
            cfg.write_text(self._CUSTOM_DIR_CONFIG, encoding="utf-8")
            code, _, err = _run(
                ["orchestrator-run", str(root), "--layout-config", str(cfg)]
            )
            self.assertEqual(code, 0, err)
            custom = list((root / "custom_prompts" / "coding").glob("*.md"))
            self.assertEqual(len(custom), 1)

    def test_explicit_warning_only_config_does_not_block(self) -> None:
        from test_orchestrator import _make_project

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            cfg = root / "warning.yaml"
            cfg.write_text(
                "schema_version: frutlups_layout_config_v999\n"
                "profile_id: artifact_first_template_legacy_root\n"
                "automation_boundary:\n"
                "  runner_implemented: true\n",
                encoding="utf-8",
            )
            result = run_one_step(root, layout_config=cfg, journal=False)
            self.assertTrue(result.wrote, result.refusal_reason)
            self.assertFalse(result.refused)


if __name__ == "__main__":
    unittest.main()
