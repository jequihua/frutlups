"""Tests for M016-S05: human stop/go gates and final milestone handoff.

Covers the typed :class:`HumanGate` states (open / stop / final_handoff /
no_frontier), the gate blocks in ``orchestrator-plan`` / ``orchestrator-run``
JSON (and that plan stays read-only while run still journals one entry), the
refusal-with-gate-reason path, and the final milestone handoff (deterministic
render, JSON-safe metadata, explicit-write-only, child-path resolves under the
project root).
"""

from __future__ import annotations

import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from frutlups.cli import main
from frutlups.gate import (
    FINAL_HANDOFF_REL_PATH,
    HumanGateState,
    build_final_handoff,
    build_human_gate,
    final_handoff_path_for,
    human_gate_from_plan,
    render_final_handoff,
    write_final_handoff,
    write_final_handoff_artifact,
)

from test_orchestrator import _make_project, _make_template, _resume
from test_resumable_status import (
    _active_roadmap,
    _detailed_roadmap,
    _make_template as _make_resume_template,
    _minimal_coding_prompt,
    _write_active_roadmap,
    _write_coding_prompt,
    _write_detailed_roadmap,
    _write_review_prompt,
    _write_review_report,
    _write_self_report,
)


def _make_execute_coding_prompt(root: Path) -> None:
    """A project whose loop step is execute_coding_prompt (coder must act)."""
    _make_resume_template(root)
    _write_active_roadmap(root, _active_roadmap())
    _write_detailed_roadmap(root, _detailed_roadmap())
    _write_coding_prompt(root, "001_frutlups_m001_s01_first_slice.md", _minimal_coding_prompt(1))
    # M003-S04: refusal-behavior fixtures declare the supported posture so the
    # generic safe refusal, not the posture refusal, is what fires.
    (root / "frutlups.layout.yaml").write_text(
        "schema_version: frutlups_layout_config_v0\n"
        "profile_id: artifact_first_template_legacy_root\n"
        "automation_boundary:\n"
        "  runner_implemented: true\n",
        encoding="utf-8",
    )


def _make_execute_review_prompt(root: Path) -> None:
    """A project whose loop step is execute_review_prompt (reviewer must act)."""
    _make_execute_coding_prompt(root)
    _write_self_report(root, "05_governance/reviews/m001_s01_first_slice_self_report.md")
    _write_review_prompt(root, "001_review_m001_s01_first_slice.md")


class HumanGateStateTests(unittest.TestCase):
    def test_safe_step_is_open_no_human_go(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)  # make_coding_prompt (safe)
            gate = build_human_gate(root)
        self.assertEqual(gate.gate_state, HumanGateState.OPEN.value)
        self.assertFalse(gate.requires_human_go)
        self.assertTrue(gate.safe_for_auto_execution)

    def test_coder_step_is_stop_requires_human_go(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_execute_coding_prompt(root)
            gate = build_human_gate(root)
        self.assertEqual(gate.gate_state, HumanGateState.STOP.value)
        self.assertTrue(gate.requires_human_go)
        self.assertEqual(gate.actor, "coder")
        self.assertIn("coder", gate.recommended_human_action.lower())

    def test_reviewer_step_is_stop_requires_human_go(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_execute_review_prompt(root)
            gate = build_human_gate(root)
        self.assertEqual(gate.gate_state, HumanGateState.STOP.value)
        self.assertTrue(gate.requires_human_go)
        self.assertEqual(gate.actor, "reviewer")

    def test_frontier_recorded_is_final_handoff(self) -> None:
        # M003-S05: a verdict record paired to a non-pass review report is
        # contradictory durable state (Decision 5), so a genuine
        # frontier_recorded project can no longer be constructed from durable
        # artifacts. The gate mapping for the retained step value is exercised
        # from the synthesized typed resume instead.
        from frutlups.orchestrator import plan_from_resume_status
        from frutlups.project import LoopResumeStep

        gate = human_gate_from_plan(
            plan_from_resume_status(_resume(LoopResumeStep.FRONTIER_RECORDED))
        )
        self.assertEqual(gate.gate_state, HumanGateState.FINAL_HANDOFF.value)
        self.assertTrue(gate.requires_human_go)

    def test_no_frontier_reports_no_frontier(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)  # no roadmap -> no frontier
            gate = build_human_gate(root)
        self.assertEqual(gate.gate_state, HumanGateState.NO_FRONTIER.value)
        self.assertFalse(gate.requires_human_go)

    def test_gate_is_json_safe(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            gate = build_human_gate(root)
        json.dumps(gate.to_dict())


class CliGateTests(unittest.TestCase):
    def _run(self, args: list[str]) -> tuple[int, str]:
        out = StringIO()
        with redirect_stdout(out):
            code = main(args)
        return code, out.getvalue()

    def test_orchestrator_plan_json_includes_gate_and_is_read_only(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            before = set(root.rglob("*"))
            code, out = self._run(["orchestrator-plan", str(root), "--json"])
            after = set(root.rglob("*"))
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertIn("human_gate", payload)
        self.assertEqual(payload["human_gate"]["gate_state"], "open")
        self.assertEqual(before, after)  # plan never writes

    def test_orchestrator_run_json_includes_gate_and_journals_once(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            code, out = self._run(["orchestrator-run", str(root), "--once", "--json"])
            journal = root / "05_governance" / "orchestrator" / "run_journal.jsonl"
            lines = journal.read_text(encoding="utf-8").splitlines() if journal.exists() else []
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertIn("human_gate", payload)
        self.assertEqual(len(lines), 1)  # exactly one journal entry

    def test_run_refusal_includes_gate_reason(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_execute_coding_prompt(root)  # coder step -> refused
            code, out = self._run(["orchestrator-run", str(root), "--once", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["refused"])
        self.assertEqual(payload["human_gate"]["gate_state"], "stop")
        self.assertTrue(payload["human_gate"]["requires_human_go"])


class FinalHandoffTests(unittest.TestCase):
    def test_render_is_deterministic_and_mentions_non_authorization(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            handoff = build_final_handoff(root)
            a = render_final_handoff(handoff)
            b = render_final_handoff(handoff)
        self.assertEqual(a, b)
        self.assertIn("Final Milestone Handoff: M016", a)
        self.assertIn("does not commit", a)

    def test_to_dict_is_json_safe(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            handoff = build_final_handoff(root)
        json.dumps(handoff.to_dict())

    def test_build_is_read_only(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            before = set(root.rglob("*"))
            build_final_handoff(root)
            after = set(root.rglob("*"))
        self.assertEqual(before, after)

    def test_explicit_write_only(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            handoff = build_final_handoff(root)
            self.assertFalse(final_handoff_path_for(root).exists())
            result = write_final_handoff(root, handoff)
            existed_after = final_handoff_path_for(root).is_file()
        self.assertTrue(result.wrote)
        self.assertTrue(existed_after)

    def test_no_overwrite_without_flag(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            handoff = build_final_handoff(root)
            write_final_handoff(root, handoff)
            second = write_final_handoff(root, handoff)
            third = write_final_handoff(root, handoff, overwrite=True)
        self.assertFalse(second.wrote)
        self.assertTrue(third.wrote)
        self.assertTrue(third.overwrote)

    def test_child_path_writes_under_project_root(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)  # creates an 08_pkg child dir
            child = root / "08_pkg"
            self.assertTrue(child.is_dir())
            _, result = write_final_handoff_artifact(child)
            found = list(root.rglob("m016_final_handoff.md"))
            child_handoff_exists = (child / FINAL_HANDOFF_REL_PATH).exists()
        self.assertTrue(result.wrote)
        self.assertEqual(len(found), 1)
        self.assertNotIn("08_pkg", str(found[0]).replace("\\", "/"))
        self.assertFalse(child_handoff_exists)


class CliHandoffTests(unittest.TestCase):
    def _run(self, args: list[str]) -> tuple[int, str]:
        out = StringIO()
        with redirect_stdout(out):
            code = main(args)
        return code, out.getvalue()

    def test_handoff_read_only_by_default(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            before = set(root.rglob("*"))
            code, out = self._run(["orchestrator-handoff", str(root)])
            after = set(root.rglob("*"))
        self.assertEqual(code, 0)
        self.assertIn("Final Milestone Handoff", out)
        self.assertEqual(before, after)  # read-only default writes nothing

    def test_handoff_json_read_only(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            code, out = self._run(["orchestrator-handoff", str(root), "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["milestone_id"], "M016")
        self.assertIn("gate", payload)

    def test_handoff_write_flag_writes_artifact(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            code, out = self._run(["orchestrator-handoff", str(root), "--write"])
            wrote = final_handoff_path_for(root).is_file()
        self.assertEqual(code, 0)
        self.assertIn("Final handoff written", out)
        self.assertTrue(wrote)


if __name__ == "__main__":
    unittest.main()
