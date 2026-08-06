"""Tests for M016-S02/M016-S03: the orchestrator planner and one-step executor.

Covers the pure plan model and safety classification (via synthetic
``LoopResumeStatus`` inputs), JSON serialization, the read-only live build over a
temporary repository (asserting no files are created), the CLI
``orchestrator-plan`` command, and the M016-S03 one-step executor / CLI
``orchestrator-run`` command (safe execution, unsafe refusal, dry-run,
exactly-one-step, JSON serialization, and no-shell typed dispatch).
"""

from __future__ import annotations

import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from frutlups.cli import main
from frutlups.orchestrator import (
    OrchestratorPlan,
    OrchestratorRunResult,
    StepActor,
    build_orchestrator_plan,
    plan_from_resume_status,
    run_one_step,
)
from frutlups.project import LoopResumeStatus, LoopResumeStep

from test_resumable_status import (
    _active_roadmap,
    _detailed_roadmap,
    _minimal_coding_prompt,
    _write_active_roadmap,
    _write_coding_prompt,
    _write_detailed_roadmap,
    _write_review_prompt,
    _write_review_report,
    _write_self_report,
)


def _resume(step: LoopResumeStep, next_command: str = "", **overrides: object) -> LoopResumeStatus:
    defaults: dict[str, object] = dict(
        step=step,
        message="msg",
        next_command=next_command,
        frontier_slice_id="M016-S02",
        frontier_slice_title="dry-run orchestrator plan",
        coding_prompt_path="",
        self_report_path="",
        review_prompt_path="",
        review_report_path="",
        verdict_record_path="",
        diagnostics=(),
    )
    defaults.update(overrides)
    return LoopResumeStatus(**defaults)  # type: ignore[arg-type]


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


class SafetyClassificationTests(unittest.TestCase):
    def test_make_coding_prompt_is_orchestrator_and_safe_with_command(self) -> None:
        plan = plan_from_resume_status(
            _resume(LoopResumeStep.MAKE_CODING_PROMPT, "python -m frutlups make-coding-prompt <project>")
        )
        self.assertEqual(plan.actor, StepActor.ORCHESTRATOR)
        self.assertTrue(plan.safe_for_auto_execution)
        self.assertFalse(plan.executed)

    def test_make_review_prompt_is_safe_candidate(self) -> None:
        plan = plan_from_resume_status(
            _resume(LoopResumeStep.MAKE_REVIEW_PROMPT, "python -m frutlups make-review-prompt <project>")
        )
        self.assertEqual(plan.actor, StepActor.ORCHESTRATOR)
        self.assertTrue(plan.safe_for_auto_execution)

    def test_record_verdict_is_safe_candidate(self) -> None:
        plan = plan_from_resume_status(
            _resume(LoopResumeStep.RECORD_VERDICT, "python -m frutlups record-verdict <project> --review-report x")
        )
        self.assertEqual(plan.actor, StepActor.ORCHESTRATOR)
        self.assertTrue(plan.safe_for_auto_execution)

    def test_execute_coding_prompt_is_coder_and_unsafe(self) -> None:
        plan = plan_from_resume_status(_resume(LoopResumeStep.EXECUTE_CODING_PROMPT))
        self.assertEqual(plan.actor, StepActor.CODER)
        self.assertFalse(plan.safe_for_auto_execution)

    def test_execute_review_prompt_is_reviewer_and_unsafe(self) -> None:
        plan = plan_from_resume_status(_resume(LoopResumeStep.EXECUTE_REVIEW_PROMPT))
        self.assertEqual(plan.actor, StepActor.REVIEWER)
        self.assertFalse(plan.safe_for_auto_execution)

    def test_fix_self_report_is_coder_and_unsafe(self) -> None:
        plan = plan_from_resume_status(_resume(LoopResumeStep.FIX_SELF_REPORT))
        self.assertEqual(plan.actor, StepActor.CODER)
        self.assertFalse(plan.safe_for_auto_execution)

    def test_frontier_recorded_is_human_and_unsafe(self) -> None:
        plan = plan_from_resume_status(_resume(LoopResumeStep.FRONTIER_RECORDED))
        self.assertEqual(plan.actor, StepActor.HUMAN)
        self.assertFalse(plan.safe_for_auto_execution)

    def test_no_frontier_is_none_actor_and_unsafe(self) -> None:
        plan = plan_from_resume_status(_resume(LoopResumeStep.NO_FRONTIER))
        self.assertEqual(plan.actor, StepActor.NONE)
        self.assertFalse(plan.safe_for_auto_execution)

    def test_safe_policy_without_command_is_not_auto_safe(self) -> None:
        # A normally-safe step is not auto-safe if the concrete command is unknown.
        plan = plan_from_resume_status(_resume(LoopResumeStep.MAKE_CODING_PROMPT, ""))
        self.assertEqual(plan.actor, StepActor.ORCHESTRATOR)
        self.assertFalse(plan.safe_for_auto_execution)
        self.assertIn("not auto-safe", plan.rationale)


class SerializationTests(unittest.TestCase):
    def test_to_dict_is_json_safe(self) -> None:
        plan = plan_from_resume_status(
            _resume(LoopResumeStep.RECORD_VERDICT, "python -m frutlups record-verdict <project> --review-report x")
        )
        d = plan.to_dict()
        json.dumps(d)
        self.assertEqual(d["actor"], "orchestrator")
        self.assertEqual(d["loop_step"], "record_verdict")
        self.assertTrue(d["safe_for_auto_execution"])
        self.assertFalse(d["executed"])
        self.assertIsInstance(d["diagnostics"], list)

    def test_recommended_command_is_passed_through(self) -> None:
        cmd = "python -m frutlups make-coding-prompt <project>"
        plan = plan_from_resume_status(_resume(LoopResumeStep.MAKE_CODING_PROMPT, cmd))
        self.assertEqual(plan.recommended_command, cmd)

    def test_diagnostics_preserved(self) -> None:
        plan = plan_from_resume_status(
            _resume(LoopResumeStep.MAKE_CODING_PROMPT, "cmd", diagnostics=("note one", "note two"))
        )
        self.assertEqual(plan.diagnostics, ("note one", "note two"))


class LiveBuildTests(unittest.TestCase):
    def test_build_is_read_only_and_creates_no_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
                "### M002: Active One\n\nStatus: active\n", encoding="utf-8"
            )
            (root / "03_experiments" / "development_roadmap_frutlups.md").write_text(
                "### M002: Active One\n\nSlices:\n\n- M002-S01: first\n",
                encoding="utf-8",
            )
            before = set(root.rglob("*"))
            plan = build_orchestrator_plan(root)
            after = set(root.rglob("*"))

        self.assertEqual(before, after, "orchestrator-plan must not create files")
        self.assertIsInstance(plan, OrchestratorPlan)
        # No coding prompt exists yet for the inferred slice -> make_coding_prompt.
        self.assertEqual(plan.loop_step, "make_coding_prompt")
        self.assertEqual(plan.frontier_slice_id, "M002-S01")
        self.assertEqual(plan.actor, StepActor.ORCHESTRATOR)
        self.assertTrue(plan.safe_for_auto_execution)
        json.dumps(plan.to_dict())


class CliOrchestratorPlanTests(unittest.TestCase):
    def _run(self, args: list[str]) -> tuple[int, str]:
        out = StringIO()
        with redirect_stdout(out):
            code = main(args)
        return code, out.getvalue()

    def _project(self, root: Path) -> None:
        _make_template(root)
        (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
            "### M002: Active One\n\nStatus: active\n", encoding="utf-8"
        )
        (root / "03_experiments" / "development_roadmap_frutlups.md").write_text(
            "### M002: Active One\n\nSlices:\n\n- M002-S01: first\n", encoding="utf-8"
        )

    def test_text_output(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            code, out = self._run(["orchestrator-plan", str(root)])
        self.assertEqual(code, 0)
        self.assertIn("Orchestrator plan", out)
        self.assertIn("Loop step:", out)
        self.assertIn("Executed: no", out)

    def test_text_output_has_no_stale_future_m016_s03_label(self) -> None:
        # M016-S03 is implemented; the safety line must not call it a future slice,
        # but must still report the safety classification.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            code, out = self._run(["orchestrator-plan", str(root)])
        self.assertEqual(code, 0)
        self.assertNotIn("future M016-S03", out)
        self.assertIn("Safe for automatic local execution:", out)

    def test_json_output_and_not_executed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            before = set(root.rglob("*"))
            code, out = self._run(["orchestrator-plan", str(root), "--json"])
            after = set(root.rglob("*"))
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertFalse(payload["executed"])
        self.assertEqual(payload["loop_step"], "make_coding_prompt")
        self.assertEqual(before, after, "CLI orchestrator-plan must not create files")

    def test_dry_run_flag_accepted(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            code, out = self._run(["orchestrator-plan", str(root), "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("Orchestrator plan", out)


def _make_project(root: Path) -> None:
    """A minimal project whose loop step is make_coding_prompt (a safe step)."""
    _make_template(root)
    # M003-S04: successful automated execution requires an explicitly
    # supported runner posture in the selected layout.
    (root / "frutlups.layout.yaml").write_text(
        "schema_version: frutlups_layout_config_v0\n"
        "profile_id: artifact_first_template_legacy_root\n"
        "automation_boundary:\n"
        "  runner_implemented: true\n",
        encoding="utf-8",
    )
    (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
        "### M002: Active One\n\nStatus: active\n", encoding="utf-8"
    )
    (root / "03_experiments" / "development_roadmap_frutlups.md").write_text(
        "### M002: Active One\n\nSlices:\n\n- M002-S01: first\n", encoding="utf-8"
    )


class OneStepExecutorTests(unittest.TestCase):
    def test_safe_step_executes_exactly_one_artifact(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            coding_dir = root / "prompts" / "for_coding_agent"
            self.assertEqual(list(coding_dir.glob("*.md")), [])

            result = run_one_step(root)

            written = sorted(coding_dir.glob("*.md"))
        self.assertIsInstance(result, OrchestratorRunResult)
        self.assertEqual(result.plan.loop_step, "make_coding_prompt")
        self.assertTrue(result.attempted)
        self.assertTrue(result.wrote)
        self.assertFalse(result.refused)
        self.assertTrue(result.artifact_path)
        # exactly one artifact written
        self.assertEqual(len(written), 1)

    def test_second_run_does_not_double_write_same_step(self) -> None:
        # Exactly-one-step: a fresh invocation advances at most one step, and
        # never overwrites an existing artifact (overwrite is always False).
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            first = run_one_step(root)
            self.assertTrue(first.wrote)
            coding_after_first = sorted((root / "prompts" / "for_coding_agent").glob("*.md"))

            # The loop step is no longer make_coding_prompt (a coding prompt now
            # exists); the next step is execute_coding_prompt (coder, unsafe).
            second = run_one_step(root)
            coding_after_second = sorted((root / "prompts" / "for_coding_agent").glob("*.md"))

        self.assertEqual(len(coding_after_first), 1)
        self.assertEqual(coding_after_first, coding_after_second, "no extra/overwritten files")
        self.assertFalse(second.wrote)
        self.assertTrue(second.refused)
        self.assertEqual(second.plan.loop_step, "execute_coding_prompt")

    def test_unsafe_step_refuses_and_writes_nothing(self) -> None:
        # A coder step is never executed.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            # Add a coding prompt so the step becomes execute_coding_prompt.
            run_one_step(root)
            before = set(root.rglob("*"))

            result = run_one_step(root)

            after = set(root.rglob("*"))
        self.assertEqual(before, after, "unsafe step must not create or change files")
        self.assertTrue(result.refused)
        self.assertFalse(result.attempted)
        self.assertFalse(result.wrote)
        self.assertIn("not safe for automatic local execution", result.refusal_reason)

    def test_no_frontier_refuses(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            # No roadmap -> no frontier.
            before = set(root.rglob("*"))
            result = run_one_step(root)
            after = set(root.rglob("*"))
        self.assertEqual(before, after)
        self.assertTrue(result.refused)
        self.assertFalse(result.wrote)

    def test_dry_run_writes_nothing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            before = set(root.rglob("*"))

            result = run_one_step(root, dry_run=True)

            after = set(root.rglob("*"))
        self.assertEqual(before, after, "dry run must not create files")
        self.assertTrue(result.dry_run)
        self.assertFalse(result.attempted)
        self.assertFalse(result.wrote)
        self.assertFalse(result.refused)

    def test_result_is_json_safe(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            result = run_one_step(root, dry_run=True)
        d = result.to_dict()
        json.dumps(d)
        self.assertIn("plan", d)
        self.assertIsInstance(d["plan"], dict)
        self.assertFalse(d["wrote"])
        self.assertIsInstance(d["diagnostics"], list)

    def test_dispatch_is_typed_not_shell(self) -> None:
        # The executor must dispatch by typed loop step, not by parsing or running
        # the recommended-command string. Even a malicious command string must not
        # be executed; only the typed safe step matters.
        import frutlups.orchestrator as orch

        # No subprocess / shell usage in the orchestrator module source. The
        # module docstring may *mention* "subprocess" to state it is not used, so
        # assert against actual import/call forms rather than the bare word.
        src = Path(orch.__file__).read_text(encoding="utf-8")
        self.assertNotIn("import subprocess", src)
        self.assertNotIn("subprocess.", src)
        self.assertNotIn("os.system", src)
        self.assertNotIn("shell=True", src)
        # Belt and suspenders: the imported module truly has no subprocess symbol.
        self.assertFalse(hasattr(orch, "subprocess"))

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            # Even if the resume status carried a dangerous recommended command,
            # the executor selects the handler by enum identity, not text.
            result = run_one_step(root)
        # The safe make_coding_prompt step still wrote exactly its artifact.
        self.assertTrue(result.wrote)


class CliOrchestratorRunTests(unittest.TestCase):
    def _run(self, args: list[str]) -> tuple[int, str]:
        out = StringIO()
        with redirect_stdout(out):
            code = main(args)
        return code, out.getvalue()

    def test_run_executes_safe_step(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            code, out = self._run(["orchestrator-run", str(root), "--once"])
            written = sorted((root / "prompts" / "for_coding_agent").glob("*.md"))
        self.assertEqual(code, 0)
        self.assertIn("one step executed", out)
        self.assertEqual(len(written), 1)

    def test_run_dry_run_writes_no_prompt_artifact_but_journals(self) -> None:
        # Journal contract: a CLI dry-run writes a journal entry (resume evidence)
        # but never a prompt/review/verdict artifact.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            code, out = self._run(["orchestrator-run", str(root), "--once", "--dry-run"])
            coding = sorted((root / "prompts" / "for_coding_agent").glob("*.md"))
            journal = root / "05_governance" / "orchestrator" / "run_journal.jsonl"
            journal_lines = journal.read_text(encoding="utf-8").splitlines() if journal.exists() else []
        self.assertEqual(code, 0)
        self.assertIn("dry run", out)
        # No prompt artifact written.
        self.assertEqual(coding, [])
        # Exactly one journal entry, of kind dry_run.
        self.assertEqual(len(journal_lines), 1)
        self.assertEqual(json.loads(journal_lines[0])["event_kind"], "dry_run")

    def test_run_json_output(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            code, out = self._run(["orchestrator-run", str(root), "--once", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["wrote"])
        self.assertIn("plan", payload)
        self.assertEqual(payload["plan"]["loop_step"], "make_coding_prompt")

    def test_run_refuses_unsafe_step_exit_zero(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)  # no roadmap -> no frontier -> refuse
            # M003-S04: supported posture so the generic refusal, not the
            # posture refusal, is what fires.
            (root / "frutlups.layout.yaml").write_text(
                "schema_version: frutlups_layout_config_v0\n"
                "profile_id: artifact_first_template_legacy_root\n"
                "automation_boundary:\n"
                "  runner_implemented: true\n",
                encoding="utf-8",
            )
            code, out = self._run(["orchestrator-run", str(root), "--once"])
            coding = sorted((root / "prompts" / "for_coding_agent").glob("*.md"))
            review = sorted((root / "prompts" / "for_review_agent").glob("*.md"))
            journal = root / "05_governance" / "orchestrator" / "run_journal.jsonl"
            journal_lines = journal.read_text(encoding="utf-8").splitlines() if journal.exists() else []
        self.assertEqual(code, 0)  # a safe refusal is not an error
        self.assertIn("refused", out.lower())
        # Refusal writes no prompt/review artifact, but is journaled as evidence.
        self.assertEqual(coding, [])
        self.assertEqual(review, [])
        self.assertEqual(len(journal_lines), 1)
        self.assertEqual(json.loads(journal_lines[0])["event_kind"], "refuse")


def _make_record_verdict_project(root: Path) -> None:
    """A project whose loop step is record_verdict.

    Full pass chain (coding prompt, self-report, review prompt, passing review
    report) with no verdict record yet, so the next safe step is
    ``record_verdict``. Mirrors the proven resumable-status fixture.
    """
    _make_template(root)
    # M003-S04: successful automated execution requires an explicitly
    # supported runner posture in the selected layout.
    (root / "frutlups.layout.yaml").write_text(
        "schema_version: frutlups_layout_config_v0\n"
        "profile_id: artifact_first_template_legacy_root\n"
        "automation_boundary:\n"
        "  runner_implemented: true\n",
        encoding="utf-8",
    )
    _write_active_roadmap(root, _active_roadmap())
    _write_detailed_roadmap(
        root,
        _detailed_roadmap(slices=[("M001-S01", "first slice"), ("M001-S02", "second slice")]),
    )
    _write_coding_prompt(root, "001_frutlups_m001_s01_first_slice.md", _minimal_coding_prompt(1))
    _write_self_report(root, "05_governance/reviews/m001_s01_first_slice_self_report.md")
    _write_review_prompt(root, "001_review_m001_s01_first_slice.md")
    _write_review_report(root, "m001_s01_first_slice_review_report.md", "pass")


class RecordVerdictChildPathTests(unittest.TestCase):
    """Regression for review 082: safe ``record_verdict`` execution must work
    whether ``start`` is the project root or any child path inside it."""

    @staticmethod
    def _verdict_records(base: Path) -> list[Path]:
        reviews = base / "05_governance" / "reviews"
        if not reviews.is_dir():
            return []
        return sorted(reviews.glob("*_verdict_record.md"))

    def test_fixture_state_is_record_verdict(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_record_verdict_project(root)
            result = run_one_step(root, dry_run=True)
        self.assertEqual(result.plan.loop_step, "record_verdict")

    def test_record_verdict_from_project_root(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_record_verdict_project(root)
            self.assertEqual(self._verdict_records(root), [])
            result = run_one_step(root)
            records = self._verdict_records(root)
        self.assertTrue(result.attempted, msg=result.refusal_reason)
        self.assertTrue(result.wrote)
        self.assertFalse(result.refused)
        self.assertEqual(len(records), 1)
        self.assertIn("verdict_record", result.artifact_path)

    def test_record_verdict_from_child_path(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_record_verdict_project(root)
            child = root / "08_pkg"
            self.assertTrue(child.is_dir())
            self.assertEqual(self._verdict_records(root), [])

            result = run_one_step(child)

            root_records = self._verdict_records(root)
            child_reviews = child / "05_governance"
        # The same verdict record is written from the child path...
        self.assertTrue(result.attempted, msg=result.refusal_reason)
        self.assertTrue(result.wrote)
        self.assertFalse(result.refused)
        self.assertEqual(len(root_records), 1)
        # ...and it lands under the project root, NOT under 08_pkg/05_governance.
        self.assertFalse(child_reviews.exists())

    def test_record_verdict_root_and_child_agree(self) -> None:
        # Both invocation shapes target the identical project-root artifact path.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_record_verdict_project(root)
            from_root = run_one_step(root, dry_run=True)
            from_child = run_one_step(root / "08_pkg", dry_run=True)
        # Dry-run plans are computed from the discovered project root in both
        # cases, so the recommended command and frontier slice agree.
        self.assertEqual(from_root.plan.loop_step, "record_verdict")
        self.assertEqual(from_child.plan.loop_step, "record_verdict")
        self.assertEqual(
            from_root.plan.frontier_slice_id, from_child.plan.frontier_slice_id
        )


if __name__ == "__main__":
    unittest.main()
