"""Tests for M016-S04: persistent run journal and resume behavior.

Covers the journal model and helpers (build/append/read/summarize), the
``run_one_step(journal=True)`` contract (execute / dry-run / refuse each append
exactly one entry; dry-run/refuse write no prompt/review/verdict artifact),
exactly-one-step behavior under journaling, malformed-line tolerance, child-path
journaling under the project root, and the CLI resume surface.
"""

from __future__ import annotations

import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from frutlups.cli import main
from frutlups.journal import (
    JOURNAL_REL_PATH,
    OrchestratorEventKind,
    RunJournalEntry,
    append_run_journal_entry,
    build_resume_summary,
    build_run_journal_entry,
    journal_path_for,
    read_run_journal,
    summarize_run_journal,
)
from frutlups.orchestrator import run_one_step
from frutlups.project import build_loop_resume_status, build_status

from test_orchestrator import _make_project, _make_record_verdict_project, _make_template


def _entry(**overrides: object) -> RunJournalEntry:
    defaults: dict[str, object] = dict(
        timestamp="2026-06-03T12:00:00Z",
        event_kind="execute",
        loop_step="make_coding_prompt",
        actor="orchestrator",
        frontier_slice_id="M002-S01",
        frontier_slice_title="first",
        recommended_command="python -m frutlups make-coding-prompt ..",
        safe_for_auto_execution=True,
        attempted=True,
        wrote=True,
        artifact_path="prompts/for_coding_agent/001_x.md",
        refused=False,
        refusal_reason="",
        diagnostics=(),
        layout_profile_id="artifact_first_template_legacy_root",
        layout_config_path="",
    )
    defaults.update(overrides)
    return RunJournalEntry(**defaults)  # type: ignore[arg-type]


class JournalModelTests(unittest.TestCase):
    def test_entry_round_trips_through_json(self) -> None:
        entry = _entry(diagnostics=("a", "b"))
        d = entry.to_dict()
        json.dumps(d)  # JSON-safe
        again = RunJournalEntry.from_dict(json.loads(json.dumps(d)))
        self.assertEqual(again, entry)

    def test_from_dict_tolerates_missing_keys(self) -> None:
        entry = RunJournalEntry.from_dict({"event_kind": "refuse"})
        self.assertEqual(entry.event_kind, "refuse")
        self.assertEqual(entry.loop_step, "")
        self.assertFalse(entry.wrote)

    def test_from_dict_rejects_non_object(self) -> None:
        with self.assertRaises(ValueError):
            RunJournalEntry.from_dict(["not", "a", "dict"])  # type: ignore[arg-type]


class AppendReadTests(unittest.TestCase):
    def test_append_is_append_only(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / JOURNAL_REL_PATH
            self.assertTrue(append_run_journal_entry(path, _entry(timestamp="t1")))
            self.assertTrue(append_run_journal_entry(path, _entry(timestamp="t2")))
            read = read_run_journal(path)
        self.assertTrue(read.exists)
        self.assertEqual(len(read.entries), 2)
        self.assertEqual([e.timestamp for e in read.entries], ["t1", "t2"])
        self.assertEqual(read.malformed_count, 0)

    def test_read_missing_journal(self) -> None:
        with TemporaryDirectory() as tmp:
            read = read_run_journal(Path(tmp) / JOURNAL_REL_PATH)
        self.assertFalse(read.exists)
        self.assertEqual(read.entries, ())

    def test_malformed_lines_are_skipped_not_fatal(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "j.jsonl"
            path.write_text(
                json.dumps(_entry(timestamp="t1").to_dict())
                + "\n"
                + "this is not json\n"
                + "[1, 2, 3]\n"  # JSON, but not an object
                + json.dumps(_entry(timestamp="t2").to_dict())
                + "\n",
                encoding="utf-8",
            )
            read = read_run_journal(path)
        self.assertEqual(len(read.entries), 2)
        self.assertEqual(read.malformed_count, 2)


class SummaryTests(unittest.TestCase):
    def _live(self, root: Path):
        return build_loop_resume_status(build_status(root))

    def test_summary_empty_journal(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            read = read_run_journal(journal_path_for(root))
            summary = summarize_run_journal(read, self._live(root))
        self.assertFalse(summary.stale)
        self.assertEqual(summary.entry_count, 0)
        self.assertEqual(summary.latest, None)
        self.assertEqual(summary.live_loop_step, "make_coding_prompt")

    def test_summary_stale_via_inline_read(self) -> None:
        from frutlups.journal import RunJournalReadResult

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)  # live step is make_coding_prompt
            live = self._live(root)
            read = RunJournalReadResult(
                path="x",
                exists=True,
                entries=(_entry(loop_step="record_verdict", event_kind="execute"),),
                malformed_count=0,
            )
            summary = summarize_run_journal(read, live)
        self.assertTrue(summary.stale)
        self.assertEqual(summary.latest_observed_step, "record_verdict")
        self.assertEqual(summary.live_loop_step, "make_coding_prompt")


class RunOneStepJournalTests(unittest.TestCase):
    def test_execute_journals_one_entry_and_one_step(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            result = run_one_step(root, journal=True)
            coding = sorted((root / "prompts" / "for_coding_agent").glob("*.md"))
            read = read_run_journal(journal_path_for(root))
        self.assertTrue(result.wrote)
        self.assertEqual(len(coding), 1)  # exactly one step
        self.assertEqual(len(read.entries), 1)
        self.assertEqual(read.entries[0].event_kind, OrchestratorEventKind.EXECUTE.value)
        self.assertTrue(read.entries[0].wrote)

    def test_dry_run_journals_dry_run_and_writes_no_artifact(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            result = run_one_step(root, dry_run=True, journal=True)
            coding = sorted((root / "prompts" / "for_coding_agent").glob("*.md"))
            read = read_run_journal(journal_path_for(root))
        self.assertFalse(result.wrote)
        self.assertEqual(coding, [])
        self.assertEqual(len(read.entries), 1)
        self.assertEqual(read.entries[0].event_kind, OrchestratorEventKind.DRY_RUN.value)

    def test_refuse_is_journaled(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)  # no roadmap -> no frontier -> refuse
            # M003-S04: supported posture so the generic refusal is journaled.
            (root / "frutlups.layout.yaml").write_text(
                "schema_version: frutlups_layout_config_v0\n"
                "profile_id: artifact_first_template_legacy_root\n"
                "automation_boundary:\n"
                "  runner_implemented: true\n",
                encoding="utf-8",
            )
            result = run_one_step(root, journal=True)
            read = read_run_journal(journal_path_for(root))
        self.assertTrue(result.refused)
        self.assertEqual(len(read.entries), 1)
        self.assertEqual(read.entries[0].event_kind, OrchestratorEventKind.REFUSE.value)

    def test_library_default_does_not_journal(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            run_one_step(root)  # journal defaults to False
            journal_exists = journal_path_for(root).exists()
        self.assertFalse(journal_exists)

    def test_second_run_appends_not_overwrites(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            run_one_step(root, dry_run=True, journal=True)
            run_one_step(root, dry_run=True, journal=True)
            read = read_run_journal(journal_path_for(root))
        self.assertEqual(len(read.entries), 2)

    def test_child_path_journals_under_project_root(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)  # _make_template creates an 08_pkg child dir
            child = root / "08_pkg"
            self.assertTrue(child.is_dir())
            run_one_step(child, dry_run=True, journal=True)
            journals = list(root.rglob("run_journal.jsonl"))
            child_journal_exists = (child / JOURNAL_REL_PATH).exists()
        # Exactly one journal, under the project root's 05_governance/orchestrator,
        # never under the 08_pkg child directory.
        self.assertEqual(len(journals), 1)
        normalized = str(journals[0]).replace("\\", "/")
        self.assertTrue(normalized.endswith("05_governance/orchestrator/run_journal.jsonl"))
        self.assertNotIn("08_pkg", normalized)
        self.assertFalse(child_journal_exists)


class RecordVerdictJournalTests(unittest.TestCase):
    def test_record_verdict_execute_is_journaled(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_record_verdict_project(root)
            result = run_one_step(root, journal=True)
            read = read_run_journal(journal_path_for(root))
        self.assertTrue(result.wrote)
        self.assertEqual(len(read.entries), 1)
        self.assertEqual(read.entries[0].event_kind, OrchestratorEventKind.EXECUTE.value)
        self.assertEqual(read.entries[0].loop_step, "record_verdict")


class BuildResumeSummaryTests(unittest.TestCase):
    def test_build_resume_summary_after_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            run_one_step(root, dry_run=True, journal=True)
            summary = build_resume_summary(root)
        self.assertTrue(summary.has_journal)
        self.assertEqual(summary.entry_count, 1)
        self.assertEqual(summary.latest_event_kind, "dry_run")


class CliJournalTests(unittest.TestCase):
    def _run(self, args: list[str]) -> tuple[int, str]:
        out = StringIO()
        with redirect_stdout(out):
            code = main(args)
        return code, out.getvalue()

    def test_orchestrator_run_json_includes_resume(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            code, out = self._run(["orchestrator-run", str(root), "--once", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertIn("resume", payload)
        self.assertEqual(payload["resume"]["latest_event_kind"], "execute")

    def test_orchestrator_plan_is_read_only_and_summarizes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root)
            before = set(root.rglob("*"))
            code, out = self._run(["orchestrator-plan", str(root), "--json"])
            after = set(root.rglob("*"))
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertIn("resume", payload)
        # Planning must remain fully read-only: no journal (or any) file created.
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
