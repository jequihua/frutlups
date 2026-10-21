"""Standalone /2 lifecycle, authority and immutable evidence integration tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_prompts_v2 import fixture
from test_scaffold import PASS_REPORT, native_accepted


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import _common as c  # noqa: E402
import _commit as commit  # noqa: E402
import _findings as findings  # noqa: E402
import _protocol as protocol  # noqa: E402
import ledger  # noqa: E402
import prompt  # noqa: E402
import roadmap  # noqa: E402
import verify  # noqa: E402


def event(kind, **values):
    return {
        "schema": protocol.SCHEMA,
        "t": "2026-09-12T00:00:00Z",
        "by": "architect",
        "ev": kind,
        **values,
    }




class GrammarTests(unittest.TestCase):
    def test_native_dispatch_preserves_schema_without_legacy_reentry(self):
        dispatch = ledger._validate
        rows = native_accepted() + [
            event("note", text="native note"),
            event("stop", by="frutlups", reason="budget", detail="No allowance"),
            event("reopened", slice="M001-S01", round=2, reason="defect"),
            event("milestone_done", milestone="M001", holistic_report="h.md"),
        ]
        for filename in ("protocol_v2.json", "autonomy_v1.json"):
            corpus = json.loads((ROOT / "tests/fixtures" / filename).read_text("utf-8"))
            rows.extend(
                row for case in corpus["cases"] if case["valid"]
                for row in case["events"] if row["schema"] == protocol.SCHEMA
            )
        for row in rows:
            with self.subTest(event=row["ev"]), mock.patch.object(
                ledger, "_validate", side_effect=AssertionError("schema admission reentered")
            ) as admission, mock.patch.object(
                ledger, "_validate_base", wraps=ledger._validate_base
            ) as base:
                original = json.loads(json.dumps(row))
                self.assertIs(dispatch(row), row)
                self.assertEqual(row, original)
                admission.assert_not_called()
                if row["ev"] in ledger.EVENTS and row.get("role") != "evidence":
                    projected = {
                        key: value for key, value in row.items()
                        if key not in protocol.EXTRA.get(row["ev"], set())
                    }
                    base.assert_called_once_with(projected)
                    self.assertEqual(base.call_args.args[0]["schema"], protocol.SCHEMA)
                else:
                    base.assert_not_called()

    def test_version_admission_stays_outside_shared_base_validation(self):
        native = event("note", text="shared grammar")
        with mock.patch.object(ledger, "_validate_base") as base:
            for schema, diagnostic in (("frutlups.ledger/1", "unsupported legacy history"),
                                       ("frutlups.ledger/3", "line 7: invalid schema or event")):
                with self.subTest(schema=schema), self.assertRaisesRegex(ValueError, diagnostic):
                    ledger._validate({**native, "schema": schema}, "line 7")
            with self.assertRaisesRegex(ValueError, "unknown ledger schema"):
                protocol.validate({**native, "schema": "frutlups.ledger/1"}, base)
            base.assert_not_called()
        # The private shared field helper does not admit a schema on its own.
        self.assertIs(ledger._validate_base(native), native)
        with self.assertRaisesRegex(ValueError, "unknown=\\['unexpected'\\]"):
            ledger._validate_base({**native, "unexpected": True})

    def test_inherited_native_fields_remain_valid_and_strict(self):
        lifecycle = native_accepted()
        change = {"path": "07_app/item.txt", "sha": "a" * 64, "kind": "modified"}
        usage = {"seat": "coder", "secs": 0.5, "tokens_in": 2, "tokens_out": 0, "cost_usd": 0}
        rows = [
            {**lifecycle[0], "baseline": [change]},
            {**lifecycle[1], "changed": [change], **usage,
             "notes_path": "notes.md", "notes_sha": "b" * 64},
            {**lifecycle[3], **usage},
            {**lifecycle[-1], "commit": "c" * 40},
            event("note", text="retained native note", slice="M001-S01"),
            event("stop", by="frutlups", reason="budget", detail="No allowance remains"),
            event("milestone_done", milestone="M001", holistic_report="h.md"),
        ]
        for role, scope, round_no, actor in (
            ("review_prompt", "M001-S01", 1, "architect"),
            ("holistic_prompt", "M001", "holistic", "frutlups"),
            ("holistic_report", "M001", "holistic", "human"),
            ("evidence", "M001-S01", 1, "architect"),
            ("evidence", "M001", "holistic", "architect"),
        ):
            rows.append(event(
                "artifact", scope=scope, round=round_no, by=actor,
                role=role, path="artifact.md", sha="d" * 64,
            ))
        for row in rows:
            with self.subTest(event=row["ev"], role=row.get("role")):
                self.assertEqual(ledger._validate(row), row)
                with self.assertRaisesRegex(ValueError, "unknown|invalid evidence artifact fields"):
                    ledger._validate({**row, "unexpected": True})
        for value, diagnostic in (
            ({**lifecycle[-1], "commit": "not-a-commit"}, "invalid commit"),
            ({**lifecycle[0], "baseline": [{**change, "kind": "unknown"}]}, "invalid baseline"),
            ({**lifecycle[1], "changed": [{**change, "sha": "A" * 64}]}, "invalid changed"),
            ({**lifecycle[1], "notes_path": "notes.md"}, "notes require a hash"),
            ({**lifecycle[1], "tokens_in": True}, "invalid text, usage, or actor field"),
            (event("stop", reason="stop", detail="detail"), "invalid text, usage, or actor field"),
            (event("note", text=" "), "invalid text, usage, or actor field"),
        ):
            with self.subTest(invalid=value), self.assertRaisesRegex(ValueError, diagnostic):
                ledger._validate(value)

    def test_native_resolution_requires_authority_actor_and_blocked_state(self):
        resolution = event(
            "resolved", scope="M001-S01", reason="Owner decision",
            authority=[{"path": "decision.md", "sha": "a" * 64}],
        )
        for actor in ("human", "architect"):
            value = {**resolution, "by": actor}
            ledger._validate(value)
            with self.assertRaisesRegex(ValueError, "resolution requires blocked work"):
                ledger.fold([value], roadmap.load(ROOT))
        with self.assertRaisesRegex(ValueError, "resolution requires human or architect"):
            ledger._validate({**resolution, "by": "frutlups"})
        with self.assertRaisesRegex(ValueError, "resolution needs a bound"):
            ledger._validate({**resolution, "authority": []})

    def test_shared_conformance_corpus(self):
        data = json.loads((ROOT / "tests/fixtures/protocol_v2.json").read_text(encoding="utf-8"))
        self.assertEqual(data["schema"], "template.conformance/3")
        self.assertEqual(len(data["cases"]), 16)
        self.assertEqual(len({case["name"] for case in data["cases"]}), 16)
        policy = {"legacy-prefix-new-suffix", "illegal-version-reversal"}
        rm = roadmap.load(ROOT)
        for case in data["cases"]:
            with self.subTest(case=case["name"]):
                self.assertEqual(
                    case["target"],
                    "template_policy" if case["name"] in policy else "shared_native",
                )
                if case["target"] == "template_policy":
                    self.assertFalse(case["valid"])
                    self.assertEqual(case["error"], "unsupported legacy history; use pinned v4")
                if "error" in case:
                    self.assertIsInstance(case["error"], str)
                    self.assertTrue(case["error"].strip())
                rm = roadmap.load(ROOT)
                if "holistic_review" in case:
                    rm["milestones"][0]["holistic_review"] = case["holistic_review"]
                if not case["valid"]:
                    self.assertTrue(case["error"])
                    with self.assertRaises(ValueError) as failure:
                        for row in case["events"]:
                            ledger._validate(row)
                        ledger.fold(case["events"], rm)
                    self.assertIn(case["error"], str(failure.exception))
                    continue
                for row in case["events"]:
                    ledger._validate(row)
                state = ledger.fold(case["events"], rm)
                expected = case["expected"]
                current = state["slices"]["M001-S01"]
                self.assertEqual(current["step"], expected["step"])
                self.assertEqual(current["round"], expected["round"])
                if "holistic" in expected:
                    self.assertEqual(state["holistic"]["M001"]["step"], expected["holistic"])
                if "done" in expected:
                    self.assertEqual("M001" in state["milestones_done"], expected["done"])
                for key in ("intents", "checkpoints"):
                    if key in expected:
                        self.assertEqual(len(state[key]), expected[key])
        sample = data["finding_update"]
        self.assertEqual(findings.updates(sample["text"]), sample["expected"])
        manifest = data["commit_manifest"]
        parsed = commit._manifest(manifest["text"].encode(), manifest["intent"], manifest["scope"])
        self.assertEqual(parsed, manifest["expected"])

    def test_version_order_unknown_fields_and_new_writer_declaration(self):
        rm = roadmap.load(ROOT)
        old = event("note", text="unsupported", schema="frutlups.ledger/1")
        for rows in ([old], [old, event("note", text="new")], [event("note", text="new"), old]):
            with (
                self.subTest(rows=rows),
                self.assertRaisesRegex(ValueError, "unsupported legacy history"),
            ):
                ledger.fold(rows, rm)
        with self.assertRaisesRegex(ValueError, "schema must be frutlups.roadmap/2"):
            ledger.fold([], {**rm, "schema": "frutlups.roadmap/1"})
        for bad in (event("note", text="x", unknown=True), event("made_up", text="x")):
            with self.subTest(event=bad["ev"]), self.assertRaises(ValueError):
                ledger._validate(bad)
        with self.assertRaises(ValueError):
            ledger._validate(event("prompt", slice="M001-S01", round=1, path="p.md", sha="a" * 64))
        invalid = event(
            "accepted",
            slice="M001-S01",
            round=1,
            commit_intent={
                "id": "arbitrary-name",
                "parent": "a" * 40,
                "manifest": {"path": "m.json", "sha": "a" * 64},
                "message": "Accept",
            },
        )
        with self.assertRaises(ValueError):
            ledger._validate(invalid)

    def test_legacy_prefix_refuses_append_and_preserves_bytes(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            fixture(root)
            path = root / commit.LEDGER
            original = (
                json.dumps(
                    {
                        "schema": "frutlups.ledger/1",
                        "ev": "note",
                        "by": "architect",
                        "t": "2026-09-12T00:00:00Z",
                        "text": "Legacy evidence stays exact",
                    }
                )
                + "\r\n"
            ).encode()
            path.write_bytes(original)
            import _workspace
            before = _workspace.snapshot(root)
            with self.assertRaisesRegex(ValueError, "unsupported legacy history"):
                ledger.append(
                    path,
                    {"ev": "note", "by": "architect", "text": "Refused"},
                    roadmap.load(root),
                )
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(_workspace.snapshot(root), before)


    def test_unknown_attempt_blocks_dispatch_and_usage_stays_unknown(self):
        rm = roadmap.load(ROOT)
        start = event(
            "attempt_started",
            invocation="review-1",
            scope="M001-S01",
            round=1,
            role="reviewer",
            contract="a" * 64,
            allowance_seconds=60,
            retry="initial",
        )
        with self.assertRaisesRegex(ValueError, "unresolved invocation"):
            ledger.fold([start, {**start, "invocation": "review-2"}], rm)
        finish = event(
            "attempt_finished",
            invocation="review-1",
            status="completed",
            usage={"completeness": "unknown"},
        )
        state = ledger.fold([start, finish], rm)
        self.assertEqual(
            state["attempts"]["review-1"]["finish"]["usage"], {"completeness": "unknown"}
        )
        checkpoint = event(
            "review_checkpoint",
            invocation="review-1",
            scope="M001-S01",
            round=1,
            contract="a" * 64,
            report="review.md",
            sha="b" * 64,
            seat="reviewer",
        )
        self.assertIn("review-1", ledger.fold([start, finish, checkpoint], rm)["checkpoints"])
        for change in ({"round": 2}, {"scope": "M001"}, {"contract": "c" * 64}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                ledger.fold([start, finish, {**checkpoint, **change}], rm)
        retry = {**start, "invocation": "review-2", "retry": "transport", "parent": "review-1"}
        for change in ({"scope": "M001"}, {"contract": "b" * 64}, {"round": 2}):
            with self.subTest(retry=change), self.assertRaises(ValueError):
                ledger.fold([start, finish, {**retry, **change}], rm)

    def test_holistic_block_resolution_needswork_and_explicit_close(self):
        rm = roadmap.load(ROOT)
        accepted = native_accepted()
        for row in accepted:
            ledger._validate(row)
        decision = event(
            "holistic_reviewed",
            milestone="M001",
            report="holistic.md",
            sha="d" * 64,
            verdict="blocked",
            open=["M001-S01-P2"],
        )
        state = ledger.fold(accepted + [decision], rm)
        self.assertEqual(state["holistic"]["M001"]["step"], "blocked")
        self.assertEqual(state["slices"]["M001-S01"]["step"], "accepted")
        with self.assertRaises(ValueError):
            ledger.fold(accepted + [decision, {**decision, "verdict": "pass", "open": []}], rm)
        resolution = event(
            "resolved",
            scope="M001",
            reason="Authority now recorded",
            authority=[{"path": "decision.md", "sha": "e" * 64}],
        )
        passed = {**decision, "verdict": "pass", "open": []}
        state = ledger.fold(accepted + [decision, resolution, passed], rm)
        self.assertEqual(state["holistic"]["M001"]["step"], "close_pending")
        self.assertNotIn("M001", state["milestones_done"])
        close = event("milestone_done", milestone="M001", holistic_report="holistic.md")
        self.assertIn("M001", ledger.fold(accepted + [passed, close], rm)["milestones_done"])
        state = ledger.fold(accepted + [{**decision, "verdict": "needs_work"}], rm)
        self.assertEqual(state["slices"]["M001-S01"]["step"], "fix")
        self.assertEqual(state["slices"]["M001-S01"]["round"], 2)
        with self.assertRaises(ValueError):
            ledger.fold(accepted + [close], rm)


class ManualIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="template-protocol-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        fixture(self.root)

    def cli(self, *args):
        return subprocess.run(
            [
                sys.executable,
                "-B",
                str(self.root / "scripts/ledger.py"),
                "--root",
                str(self.root),
                *args,
            ],
            capture_output=True,
            timeout=120,
        )

    def success(self, *args):
        result = self.cli(*args)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return result

    def state(self):
        return ledger.fold(ledger.read(self.root / commit.LEDGER), roadmap.load(self.root))

    def test_blocked_edits_resolution_and_notes_identity(self):
        prompt.coding(self.root, "M001-S01")
        (self.root / "07_app/partial.py").write_text("answer = 41\n")
        notes = self.root / "05_governance/reviews/coder.md"
        notes.write_text("Retained implementation awaits an explicit authority decision.\n")
        self.success(
            "blocked",
            "M001-S01",
            "--requirement",
            "acceptance 1",
            "--reason",
            "Experiment allowance absent",
            "--actor",
            "human",
            "--action",
            "Record allowance",
            "--notes",
            "05_governance/reviews/coder.md",
        )
        state = self.state()
        self.assertEqual(state["slices"]["M001-S01"]["step"], "blocked")
        events = ledger.read(self.root / commit.LEDGER)
        coded = next(e for e in events if e["ev"] == "coded")
        self.assertEqual(coded["result"], "blocked_authority")
        self.assertEqual(coded["notes_sha"], c.sha(notes))
        self.assertFalse(any(e["ev"] == "verified" for e in events))
        self.assertEqual((self.root / "07_app/partial.py").read_text(), "answer = 41\n")
        self.success(
            "resolve",
            "M001-S01",
            "--reason",
            "Allowance approved in decision register",
            "--authority",
            "00_brief/decisions.md",
            "--by",
            "architect",
        )
        self.assertEqual(self.state()["slices"]["M001-S01"]["step"], "fix")
        self.assertEqual(self.state()["slices"]["M001-S01"]["round"], 2)
        notes.write_text("Altered after recording\n")
        events = ledger.read(self.root / commit.LEDGER)
        errors = ledger.check(self.root, roadmap.load(self.root), events)
        self.assertTrue(any("drift" in message or "hash" in message for message in errors))

    def test_illegal_blocker_edits_are_preserved_and_not_recorded_as_valid(self):
        prompt.coding(self.root, "M001-S01")
        path = self.root / "00_brief/decisions.md"
        original = path.read_bytes()
        path.write_bytes(original + b"\nUnapproved coder authority change.\n")
        before = (self.root / commit.LEDGER).read_bytes()
        result = self.cli(
            "blocked",
            "M001-S01",
            "--requirement",
            "acceptance 1",
            "--reason",
            "Needs scope",
            "--actor",
            "architect",
            "--action",
            "Review boundary",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("boundary", result.stderr.decode())
        self.assertEqual((self.root / commit.LEDGER).read_bytes(), before)
        self.assertTrue(path.read_bytes().endswith(b"Unapproved coder authority change.\n"))

    def test_writer_lock_excludes_second_process_and_status_remains_readable(self):
        before = (self.root / commit.LEDGER).read_bytes()
        with c.writer_lock(self.root):
            blocked = self.cli("reconcile")
            self.assertEqual(blocked.returncode, 2)
            self.assertIn("another writer", blocked.stderr.decode())
            self.success("status")
        self.assertEqual((self.root / commit.LEDGER).read_bytes(), before)
        # A leftover lock filename is not ownership after the OS lock releases.
        self.success("reconcile")

    def test_unresolved_attempt_refuses_clean_tree_coder_until_attribution(self):
        path = self.root / "local_state/attempt.json"
        start = event(
            "attempt_started",
            invocation="lost-coder",
            scope="M001-S01",
            round=1,
            role="coder",
            contract="a" * 64,
            allowance_seconds=60,
            retry="initial",
        )
        path.write_text(json.dumps(start))
        self.success("attempt", "local_state/attempt.json")
        before = (self.root / commit.LEDGER).read_bytes()
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(self.root / "scripts/prompt.py"),
                "M001-S01",
                "--root",
                str(self.root),
            ],
            capture_output=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("unresolved invocation", result.stderr.decode())
        self.assertEqual((self.root / commit.LEDGER).read_bytes(), before)
        self.success("status")
        authority = self.root / "00_brief/decisions.md"
        finish = event(
            "attempt_finished",
            invocation="lost-coder",
            status="resolved",
            by="human",
            reason="Owned process exited; lost output remains unknown",
            usage={"completeness": "unknown"},
            evidence=[{"path": "00_brief/decisions.md", "sha": c.sha(authority)}],
        )
        path.write_text(json.dumps(finish))
        self.success("attempt", "local_state/attempt.json")
        self.assertEqual(
            self.state()["attempts"]["lost-coder"]["finish"]["usage"], {"completeness": "unknown"}
        )

    @unittest.skipUnless(
        os.environ.get("TEMPLATE_TEST_LEGACY_RUNNER"),
        "set TEMPLATE_TEST_LEGACY_RUNNER for optional paired legacy admission",
    )
    def test_installed_legacy_runner_refuses_upgrade_without_project_mutation(self):
        source = Path(os.environ["TEMPLATE_TEST_LEGACY_RUNNER"]) / "src"
        program = (
            "import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);"
            "from frutlups.roadmap import load;load(Path(sys.argv[2])/'roadmap.yaml')"
        )
        before = c.status_bytes(self.root), (self.root / commit.LEDGER).read_bytes()
        result = subprocess.run(
            [sys.executable, "-B", "-c", program, str(source), str(self.root)],
            capture_output=True,
            timeout=30,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("schema must be frutlups.roadmap/1", result.stderr.decode())
        after = c.status_bytes(self.root), (self.root / commit.LEDGER).read_bytes()
        self.assertEqual(after, before)

    def test_zero_change_manual_loop_and_pending_commit_freeze(self):
        prompt.coding(self.root, "M001-S01")
        self.success("coded", "M001-S01")
        self.assertEqual(self.state()["slices"]["M001-S01"]["changed"], [])
        self.assertTrue(verify.run(self.root, "M001-S01", 10)[0]["ok"])
        report = self.root / "05_governance/reviews/pass.md"
        report.write_text(PASS_REPORT)
        self.success("record", "05_governance/reviews/pass.md")
        # Fault before Git staging keeps one approval and the exact recoverable intent.
        real = commit.recover
        with mock.patch.object(commit, "recover", side_effect=ValueError("injected interruption")):
            result = ledger.main(["--root", str(self.root), "accept", "M001-S01", "--commit"])
            self.assertEqual(result, 2)
        events = ledger.read(self.root / commit.LEDGER)
        self.assertEqual(sum(e["ev"] == "accepted" for e in events), 1)
        before = (self.root / commit.LEDGER).read_bytes()
        result = self.cli("reopen", "M001-S01", "--reason", "must wait for commit")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.root / commit.LEDGER).read_bytes(), before)
        self.assertIn("pending", self.success("status").stdout.decode())
        rows = real(self.root, events, roadmap.load(self.root), execute=True)
        self.assertEqual(rows[0]["state"], "completed")
        self.assertEqual((self.root / commit.LEDGER).read_bytes(), before)
        self.assertEqual(self.success("check").returncode, 0)

    def test_holistic_block_record_resolve_review_and_close_in_fresh_commands(self):
        prompt.coding(self.root, "M001-S01")
        self.success("coded", "M001-S01")
        self.assertTrue(verify.run(self.root, "M001-S01", 10)[0]["ok"])
        review = self.root / "05_governance/reviews/slice.md"
        review.write_text(PASS_REPORT)
        self.success("record", "05_governance/reviews/slice.md")
        self.success("accept", "M001-S01")
        base = PASS_REPORT.replace("M001-S01 round 1", "M001 round holistic")
        blocked = base.replace(
            "| --- | --- | --- | --- |",
            "| --- | --- | --- | --- |\n| M001-S01-F1 | P2 | open | Human authority needed |",
        ).replace("Objective status: achieved", "Objective status: indeterminate")
        blocked = blocked.replace(
            "Verdict: pass - next: accept the slice",
            "Verdict: blocked - next: record missing authority",
        )
        path = self.root / "05_governance/reviews/holistic-blocked.md"
        path.write_text(blocked)
        self.success("record", "05_governance/reviews/holistic-blocked.md", "--milestone", "M001")
        self.assertEqual(self.state()["holistic"]["M001"]["step"], "blocked")
        self.assertEqual(self.state()["slices"]["M001-S01"]["step"], "accepted")
        self.success(
            "resolve",
            "M001",
            "--reason",
            "Human authority supplied",
            "--authority",
            "00_brief/decisions.md",
            "--by",
            "human",
        )
        updates = (
            "## Finding updates\n| source | sha | id | disposition | related |\n"
            "| --- | --- | --- | --- | --- |\n"
            f"| 05_governance/reviews/holistic-blocked.md | {c.sha(path)} | M001-S01-F1 | "
            "closed_by_review | - |\n\n"
        )
        passed = self.root / "05_governance/reviews/holistic-pass.md"
        passed.write_text(base.replace("## Closure Decision", updates + "## Closure Decision"))
        self.success("record", "05_governance/reviews/holistic-pass.md", "--milestone", "M001")
        self.assertEqual(self.state()["holistic"]["M001"]["step"], "close_pending")
        self.assertNotIn("M001", self.state()["milestones_done"])
        self.success("close", "M001", "--commit")
        self.assertIn("M001", self.state()["milestones_done"])
        self.assertEqual(c.status_bytes(self.root), b"")
        self.success("check")


class FindingsTests(unittest.TestCase):
    def test_explicit_partial_closure_and_projection_preserve_architect_prose(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            directory = root / "05_governance/reviews"
            directory.mkdir(parents=True)
            backlog = root / "05_governance/backlog.md"
            backlog.write_text("# Architect backlog\n\nKeep this independent plan.\n")
            original = "Keep this independent plan."
            report = PASS_REPORT.replace(
                "| --- | --- | --- | --- |",
                "| --- | --- | --- | --- |\n| OLD-1 | P3 | carried | First retained item |\n"
                "| OLD-2 | P3 | carried | Second retained item |",
            )
            first = directory / "first.md"
            first.write_text(report)
            rel = first.relative_to(root).as_posix()
            events = [
                event(
                    "reviewed",
                    slice="M001-S01",
                    round=1,
                    report=rel,
                    sha=c.sha(first),
                    verdict="pass",
                    open=[],
                )
            ]
            second = directory / "second.md"
            second.write_text(PASS_REPORT)
            next_event = event(
                "reviewed",
                slice="M001-S02",
                round=1,
                report=second.relative_to(root).as_posix(),
                sha=c.sha(second),
                verdict="pass",
                open=[],
            )
            projected, _ = findings.project(root, events + [next_event])
            self.assertEqual({v["disposition"] for v in projected.values()}, {"carried"})
            updates = (
                "\n## Finding updates\n| source | sha | id | disposition | related |\n"
                "| --- | --- | --- | --- | --- |\n"
                f"| {rel} | {c.sha(first)} | OLD-1 | closed_by_review | OLD-2 |\n"
            )
            updated_report = PASS_REPORT.replace(
                "## Closure Decision", updates + "\n## Closure Decision"
            )
            second.write_text(updated_report)
            next_event["sha"] = c.sha(second)
            events.append(next_event)
            findings.reconcile(root, events)
            self.assertIn(original, backlog.read_text())
            generated = backlog.read_bytes()
            findings.reconcile(root, events)
            self.assertEqual(backlog.read_bytes(), generated)
            projected, _ = findings.project(root, events)
            self.assertEqual(
                {v["id"]: v["disposition"] for v in projected.values()},
                {"OLD-1": "closed_by_review", "OLD-2": "carried"},
            )
            waived = updated_report.replace("closed_by_review", "waived_by_human")
            second.write_text(waived)
            events[-1]["sha"] = c.sha(second)
            with self.assertRaisesRegex(ValueError, "waiver requires"):
                findings.project(root, events)
            events[-1]["by"] = "human"
            self.assertEqual(
                findings.project(root, events)[0][(rel, c.sha(first), "OLD-1")]["disposition"],
                "waived_by_human",
            )


if __name__ == "__main__":
    unittest.main()
