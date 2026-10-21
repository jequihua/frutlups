"""Reader parity, bounded admission and confirmed-refusal publication regressions."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest import mock

from test_protocol_v2 import event
from test_prompts_v2 import fixture
from test_scaffold import PASS_REPORT, native_accepted, run_git
from test_remediation import BLOCKED_REPORT

import copy
import yaml

import _common as c
import _commit as commit
import _protocol as protocol
import ledger
import prompt
import roadmap
import verify
import _workspace
import _integrity
import _findings


class UnsupportedInputTests(unittest.TestCase):
    def test_unsupported_inputs_refuse_at_cli_and_direct_boundaries_before_effects(self):
        import yaml

        for kind in ("roadmap", "first_event", "mixed_prefix", "receipt"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                fixture(root)
                sid = "M001-S01"
                path = root / commit.LEDGER
                prompt.coding(root, sid)
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(ledger.main(["--root", str(root), "coded", sid]), 0)
                self.assertTrue(verify.run(root, sid, 10)[0]["ok"])
                report = "05_governance/reviews/pass.md"
                (root / report).write_text(PASS_REPORT, encoding="utf-8")
                rm = roadmap.load(root)
                events = ledger.read(path)
                state = ledger.fold(events, rm)
                if kind == "roadmap":
                    rm["schema"] = "frutlups.roadmap/1"
                    (root / "roadmap.yaml").write_text(yaml.safe_dump(rm), encoding="utf-8")
                    diagnostic = "schema must be frutlups.roadmap/2"
                elif kind in ("first_event", "mixed_prefix"):
                    old = event("note", schema="frutlups.ledger/1", text="Keep exact old bytes")
                    prefix = (
                        [old]
                        if kind == "first_event"
                        else [old, event("note", text="Native suffix")]
                    )
                    events = prefix if kind == "first_event" else prefix + events
                    path.write_bytes(("\r\n".join(json.dumps(x) for x in events) + "\r\n").encode())
                    diagnostic = "unsupported legacy history; use pinned v4"
                else:
                    recorded = next(e for e in events if e["ev"] == "verified")
                    receipt = root / recorded["receipt"]
                    data = json.loads(receipt.read_text("utf-8"))
                    data["schema"] = "frutlups.receipt/1"
                    for key in ("manifest", "witness", "runtime", "observation"):
                        del data[key]
                    data["changed_files"] = []
                    receipt.write_text(json.dumps(data) + "\n", encoding="utf-8")
                    recorded["sha"] = c.sha(receipt)
                    path.write_text(
                        "\n".join(json.dumps(e) for e in events) + "\n",
                        encoding="utf-8",
                    )
                    diagnostic = "unsupported legacy receipt; use pinned v4"

                # Existing product and staged data must survive every refusal, too.
                product = root / "07_app/owner.txt"
                product.write_bytes(b"Owner's staged payload\r\n")
                subprocess.run(
                    ["git", "-C", str(root), "add", "07_app/owner.txt"],
                    check=True,
                    capture_output=True,
                )
                before = _workspace.snapshot(root)
                ledger_bytes = path.read_bytes()
                calls = {
                    "prompt.coding": lambda: prompt.coding(root, sid),
                    "prompt.review": lambda: prompt.review(root, sid),
                    "verify.run": lambda: verify.run(root, sid, 10),
                    "ledger.require_artifacts": lambda: ledger.require_artifacts(root, events),
                    "ledger.append": lambda: ledger.append(
                        path,
                        {"ev": "note", "by": "architect", "text": "refuse"},
                        rm,
                    ),
                    "ledger._record": lambda: ledger._record(
                        root,
                        report,
                        SimpleNamespace(by="architect", milestone=None),
                        rm,
                        events,
                        state,
                    ),
                    "ledger._coded": lambda: ledger._coded(
                        root,
                        SimpleNamespace(slice=sid),
                        rm,
                        events,
                        state,
                        path,
                    ),
                    "ledger._approve": lambda: ledger._approve(
                        root,
                        {"ev":"accepted", "by":"architect", "slice":sid, "round":1},
                        [report],
                        sid,
                        "Accept",
                        True,
                        rm,
                    ),
                }
                if kind == "roadmap":
                    self.assertIn(diagnostic, roadmap.validate(rm)[0])
                    calls["roadmap.load"] = lambda: roadmap.load(root)
                    calls["ledger.fold"] = lambda: ledger.fold(events, rm)
                elif kind in ("first_event", "mixed_prefix"):
                    calls["ledger.read"] = lambda: ledger.read(path)
                    calls["ledger.fold"] = lambda: ledger.fold(events, rm)
                    calls["findings.project"] = lambda: _findings.project(root, events)
                    calls["findings.reconcile"] = lambda: _findings.reconcile(root, events)
                    calls["protocol.ensure_writable"] = lambda: protocol.ensure_writable(root, rm)
                for label, call in calls.items():
                    real_run = verify._process.run

                    def guarded_run(argv, **kwargs):
                        # Git reads share this process module. No
                        # verification/runtime child may launch.
                        self.assertEqual(Path(argv[0]).stem.lower(), "git", (label, argv))
                        return real_run(argv, **kwargs)

                    with (
                        self.subTest(boundary=label),
                        mock.patch.object(c, "atomic_text") as write,
                        mock.patch.object(verify._process, "run", side_effect=guarded_run),
                    ):
                        with self.assertRaisesRegex(ValueError, diagnostic):
                            call()
                        write.assert_not_called()
                    self.assertEqual(path.read_bytes(), ledger_bytes)
                    self.assertEqual(_workspace.snapshot(root), before)
                commands = [
                    ("ledger.py", ["check"]), ("ledger.py", ["coded", sid]),
                    ("ledger.py", ["record", report]),
                    ("ledger.py", ["reconcile"]), ("prompt.py", [sid]),
                    ("prompt.py", [sid, "--review"]), ("verify.py", [sid]),
                ]
                if kind == "roadmap":
                    commands += [("roadmap.py", ["check"]), ("roadmap.py", ["render"])]
                commands.append(("ledger.py", ["accept", sid, "--commit"]))
                for script, args in commands:
                    with self.subTest(cli=(script, args)):
                        if kind == "receipt" and args[0] == "accept":
                            # Acceptance must reach evidence validation from accept_pending.
                            # Materialize an otherwise-valid
                            # native review after the rehashed
                            # receipt.
                            reviewed = event("reviewed", slice=sid, round=1, report=report,
                                             sha=c.sha(root / report), verdict="pass", open=[])
                            ledger._validate(reviewed)
                            events.append(reviewed)
                            self.assertEqual(
                                ledger.fold(events, rm)["slices"][sid]["step"],
                                "accept_pending",
                            )
                            path.write_text(
                                "\n".join(json.dumps(e) for e in events) + "\n",
                                encoding="utf-8",
                            )
                            before = _workspace.snapshot(root)
                            ledger_bytes = path.read_bytes()
                        result = subprocess.run(
                            [sys.executable, "-B", str(root / "scripts" / script),
                             "--root", str(root), *args],
                            capture_output=True,
                            text=True,
                            timeout=30,
                        )
                        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                        self.assertIn(diagnostic, result.stderr)
                        self.assertEqual(path.read_bytes(), ledger_bytes)
                        self.assertEqual(_workspace.snapshot(root), before)

    def test_non_git_writer_cannot_use_legacy_bypass(self):
        for schema in ("frutlups.roadmap/1", "frutlups.roadmap/2"):
            with self.subTest(schema=schema), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                (root / "roadmap.yaml").write_text("schema: " + schema + "\n", encoding="utf-8")
                before = (root / "roadmap.yaml").read_bytes()
                with self.assertRaisesRegex(ValueError, "initialize the project Git repository"):
                    with c.writer_lock(root):
                        self.fail("non-Git mutation was admitted")
                self.assertEqual((root / "roadmap.yaml").read_bytes(), before)
                self.assertEqual([x.name for x in root.iterdir()], ["roadmap.yaml"])


class NonHolisticCloseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        fixture(self.root)
        self.rm = roadmap.load(self.root)
        self.rm["milestones"][0]["holistic_review"] = False
        self.save_plan()
        self.checkpoint()

    def save_plan(self):
        (self.root / "roadmap.yaml").write_text(
            yaml.safe_dump(self.rm, sort_keys=False), encoding="utf-8", newline="\n"
        )

    def checkpoint(self):
        run_git(self.root, "add", ".")
        run_git(self.root, "commit", "-qm", "Fixture housekeeping")

    def cli(self, *args, script="ledger.py"):
        return subprocess.run(
            [sys.executable, "-B", str(self.root / "scripts" / script),
             "--root", str(self.root), *args],
            capture_output=True, text=True, timeout=30,
        )

    def success(self, *args, script="ledger.py"):
        result = self.cli(*args, script=script)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def witness(self):
        snapshot = _workspace.snapshot(self.root)
        files = {rel: (self.root / rel).read_bytes() for rel in snapshot["files"]}
        index = c.git(self.root, "ls-files", "--stage", "-v", "-z").stdout
        return snapshot, files, index

    def close(self, mid="M001", *, error=None, commit_flag=False):
        before = self.witness()
        state = ledger.fold(ledger.read(self.root / commit.LEDGER), self.rm)
        args = ("close", mid, "--commit") if commit_flag else ("close", mid)
        result = self.cli(*args)
        self.assertEqual(self.witness(), before)
        self.assertEqual(ledger.fold(ledger.read(self.root / commit.LEDGER), self.rm), state)
        self.assertEqual(result.returncode, 2 if error else 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "error: " + error + "\n" if error else "")
        if error:
            self.assertEqual(result.stdout, "")
        else:
            label = next(m["status"] for m in self.rm["milestones"] if m["id"] == mid)
            plan = ("roadmap already done; no edit needed" if label == "done"
                    else "mark roadmap milestone done separately")
            self.assertEqual(result.stdout,
                             f"{mid}: complete (all slices accepted; no holistic review); "
                             f"{plan}\n")

    def advance(self, step="accepted", sid="M001-S01", value="first"):
        if step == "unstarted":
            return
        prompt.coding(self.root, sid)
        (self.root / "07_app/result.txt").write_text(value, encoding="utf-8")
        if step == "coding":
            return
        self.success("coded", sid)
        if step == "verifying":
            return
        self.assertTrue(verify.run(self.root, sid, 10)[0]["ok"])
        if step == "reviewing":
            return
        rel = f"05_governance/reviews/{sid}_result.md"
        body = BLOCKED_REPORT if step == "blocked" else PASS_REPORT
        (self.root / rel).write_text(body.replace("M001-S01", sid), encoding="utf-8")
        self.success("record", rel)
        if step not in ("blocked", "accept_pending"):
            self.success("accept", sid)

    def test_close_label_independence_repeat_and_commit_refusal(self):
        self.advance()
        for label in ("active", "planned", "done"):
            with self.subTest(label=label):
                self.rm["milestones"][0]["status"] = label
                self.save_plan()
                self.checkpoint()
                self.close()
                self.close()
                self.close(commit_flag=True, error=
                           "non-holistic close writes nothing; --commit is not supported")

    def test_last_active_close_preserves_planned_block_and_separate_render(self):
        second = copy.deepcopy(self.rm["milestones"][0])
        second["id"] = "M002"
        second["slices"][0]["id"] = "M002-S01"
        self.rm["milestones"].append(second)
        self.save_plan()
        self.checkpoint()
        self.advance("blocked")
        self.rm["milestones"][0]["status"] = "planned"
        self.save_plan()
        self.checkpoint()
        self.advance(sid="M002-S01")
        self.checkpoint()
        self.close("M002")
        before = self.witness()
        before_state = ledger.fold(ledger.read(self.root / commit.LEDGER), self.rm)
        second["status"] = "done"
        self.save_plan()
        self.assertEqual(self.success("check", script="roadmap.py"), "roadmap: ok\n")
        self.assertEqual(self.success("render", script="roadmap.py"), "docs/roadmap.md\n")
        after = self.witness()
        self.assertEqual(before[0]["head"], after[0]["head"])
        self.assertEqual(before[2], after[2])
        changed = {rel for rel in before[1] if before[1][rel] != after[1][rel]}
        self.assertEqual(changed, {"roadmap.yaml", "docs/roadmap.md"})
        self.assertEqual(ledger.fold(ledger.read(self.root / commit.LEDGER), self.rm), before_state)
        self.assertEqual(self.success("status"),
                         "M001-S01 r1 blocked\nM002-S01 r1 accepted\n"
                         "no active milestone\nnext: none\n")
        self.checkpoint()
        self.close("M002")

    def test_unstarted_no_active_horizons_check_render_and_status(self):
        first = copy.deepcopy(self.rm["milestones"][0])
        second = copy.deepcopy(first)
        second["id"] = "M002"
        second["slices"][0]["id"] = "M002-S01"
        self.rm["milestones"] = [first, second]
        for labels in (("done", "planned"), ("planned", "planned"), ("done", "done")):
            with self.subTest(labels=labels):
                for milestone, label in zip(self.rm["milestones"], labels):
                    milestone["status"] = label
                self.save_plan()
                self.assertEqual(self.success("check", script="roadmap.py"), "roadmap: ok\n")
                self.assertEqual(self.success("render", script="roadmap.py"), "docs/roadmap.md\n")
                before = self.witness()
                warnings = "".join(
                    f"warning: {m['id']} declared done without ledger closure\n"
                    for m in self.rm["milestones"] if m["status"] == "done"
                )
                self.assertEqual(self.success("status"),
                                 "M001-S01 r1 unstarted\nM002-S01 r1 unstarted\n"
                                 "no active milestone\nnext: none\n" + warnings)
                self.assertEqual(self.success("next", script="roadmap.py"), "none\n")
                self.assertEqual(self.witness(), before)

    def test_no_active_holistic_pending_is_not_hidden(self):
        self.rm["milestones"][0]["holistic_review"] = True
        self.save_plan()
        self.checkpoint()
        self.advance()
        rel = "05_governance/reviews/holistic.md"
        (self.root / rel).write_text(
            PASS_REPORT.replace("M001-S01 round 1", "M001 round holistic"), encoding="utf-8"
        )
        self.success("record", rel, "--milestone", "M001")
        self.rm["milestones"][0]["status"] = "planned"
        self.save_plan()
        self.checkpoint()
        before = self.witness()
        self.assertEqual(self.success("status"),
                         "M001-S01 r1 accepted\nno active milestone\nnext: none\n"
                         "M001 holistic: close_pending\n")
        self.assertEqual(self.witness(), before)
        state = ledger.fold(ledger.read(self.root / commit.LEDGER), self.rm)
        self.assertEqual(state["holistic"]["M001"]["step"], "close_pending")
        self.assertNotIn("M001", state["milestones_done"])

    def test_no_active_reopen_and_explicit_manual_selection(self):
        self.advance()
        self.success("reopen", "M001-S01", "--reason", "owner correction")
        self.rm["milestones"][0]["status"] = "planned"
        self.save_plan()
        self.checkpoint()
        self.assertEqual(self.success("status"),
                         "M001-S01 r2 fix\nno active milestone\nnext: M001-S01\n")
        self.assertEqual(self.success("next", script="roadmap.py"), "M001-S01\n")
        self.success("M001-S01", script="prompt.py")
        current = ledger.fold(ledger.read(self.root / commit.LEDGER), self.rm)["slices"]
        self.assertEqual(current["M001-S01"]["step"], "coding")
        self.assertEqual(roadmap.load(self.root)["milestones"][0]["status"], "planned")

    def test_each_unaccepted_step_refuses_and_done_label_warns(self):
        for step in ("unstarted", "coding", "verifying", "reviewing", "accept_pending", "blocked"):
            with self.subTest(step=step), tempfile.TemporaryDirectory() as name:
                original_root, original_rm = self.root, self.rm
                try:
                    self.root = Path(name)
                    fixture(self.root)
                    self.rm = roadmap.load(self.root)
                    self.rm["milestones"][0].update(holistic_review=False, status="done")
                    self.save_plan()
                    self.checkpoint()
                    self.advance(step)
                    self.close(error="M001: non-holistic close requires all slices accepted")
                    self.assertEqual(self.success("status"),
                                     f"M001-S01 r1 {step}\nno active milestone\nnext: none\n"
                                     "warning: M001 declared done without ledger closure\n")
                finally:
                    self.root, self.rm = original_root, original_rm

    def test_bound_evidence_and_product_drift_refuse(self):
        self.advance()
        self.checkpoint()
        events = ledger.read(self.root / commit.LEDGER)
        report = self.root / next(e["report"] for e in events if e["ev"] == "reviewed")
        product = self.root / "07_app/result.txt"
        for path, diagnostic in (
            (report, "immutable evidence drift: drift: "
             "05_governance/reviews/M001-S01_result.md, reviewed M001-S01: "
             "immutable evidence drift (finding source drift): "
             "05_governance/reviews/M001-S01_result.md"),
            (product, "active product differs from recorded evidence: 07_app/result.txt"),
        ):
            with self.subTest(path=path.relative_to(self.root).as_posix()):
                saved = path.read_bytes()
                path.write_bytes(saved + b"unrecorded drift")
                before = self.witness()
                result = self.cli("close", "M001")
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stderr, "error: " + diagnostic + "\n")
                self.assertEqual(result.stdout, "")
                self.assertEqual(self.witness(), before)
                path.write_bytes(saved)

    def test_accepted_housekeeping_later_slice_and_owner_hotfix(self):
        second = copy.deepcopy(self.rm["milestones"][0])
        second["id"] = "M002"
        second["slices"][0]["id"] = "M002-S01"
        self.rm["milestones"].append(second)
        self.save_plan()
        self.checkpoint()
        self.advance()
        self.close()
        self.checkpoint()
        self.close()
        self.advance(sid="M002-S01", value="later accepted identity")
        self.checkpoint()
        self.close()
        (self.root / "07_app/result.txt").write_text("committed owner hotfix", encoding="utf-8")
        self.checkpoint()
        self.close()

    def test_unknown_target_and_unfinished_attempt_refusal_order(self):
        self.close("M999", error="unknown milestone: M999; choose a roadmap milestone")
        self.advance()
        self.rm["milestones"][0]["status"] = "planned"
        self.save_plan()
        value = event("attempt_started", invocation="I1", scope="M001-S01", round=1,
                      role="probe", contract="a" * 64, allowance_seconds=10, retry="initial")
        ledger.append(self.root / commit.LEDGER, value, self.rm)
        diagnostic = (
            "unresolved invocation; record completion or explicit attribution before new work"
        )
        for mid, flag in (("M001", False), ("M999", False), ("M001", True)):
            self.close(mid, error=diagnostic, commit_flag=flag)
        output = self.success("status")
        self.assertEqual(output, "M001-S01 r1 accepted\nno active milestone\nnext: none\n"
                         "unresolved invocation: I1\n")

    def test_pending_intent_precedes_target_and_commit_flag(self):
        self.advance("accept_pending")
        events = ledger.read(self.root / commit.LEDGER)
        paths = ledger._artifacts_for(self.root, events, "M001-S01")
        intent = commit.request_intent(self.root, paths, "M001-S01", "Accept", self.rm,
                                       "05_governance/reviews")
        ledger.append(self.root / commit.LEDGER,
                      event("accepted", slice="M001-S01", round=1, commit_intent=intent),
                      self.rm)
        for mid, flag in (("M001", False), ("M999", False), ("M001", True)):
            self.close(mid, error="commit pending; use ledger.py recover or cancel",
                       commit_flag=flag)
        self.assertIn(f"commit {intent['id']}: pending\n", self.success("status"))


class ReaderParityTests(unittest.TestCase):
    def test_false_holistic_flag_refuses_direct_fold(self):
        rm = roadmap.load(ledger.c.ROOT)
        rm["milestones"][0]["holistic_review"] = False
        decision = event(
            "holistic_reviewed",
            milestone="M001",
            report="h.md",
            sha="d" * 64,
            verdict="pass",
            open=[],
        )
        ledger._validate(decision)
        with self.assertRaisesRegex(ValueError, "does not require holistic"):
            ledger.fold(native_accepted() + [decision], rm)

    def test_unblocked_refuses_validation_and_direct_fold(self):
        value = event("unblocked", slice="M001-S01", round=2, reason="Unbound authority")
        with self.assertRaisesRegex(ValueError, "authority-bound"):
            ledger._validate(value)
        prior = native_accepted()[:4]
        prior[-1].update(verdict="blocked")
        with self.assertRaisesRegex(ValueError, "authority-bound"):
            ledger.fold(prior + [value], roadmap.load(ledger.c.ROOT))

    def test_canonical_real_utc_timestamp_and_unknown_schema(self):
        for stamp in (
            "20260912T000000Z",
            "2026-09-12T00:00:00.000Z",
            "2026-09-12 00:00:00Z",
            "2026-02-30T00:00:00Z",
            "2026-09-12T25:00:00Z",
        ):
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                ledger._validate(
                    event(
                        "resolved",
                        scope="M001-S01",
                        reason="Timestamp",
                        t=stamp,
                        authority=[{"path": "decision.md", "sha": "a" * 64}],
                    )
                )
        ledger._validate(event("note", text="canonical"))
        with self.assertRaises(ValueError):
            ledger._validate(event("note", text="future", schema="frutlups.ledger/3"))


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="template-review-protocol-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        fixture(self.root)

    def cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = ledger.main(["--root", str(self.root), *args])
        return code, out.getvalue()

    def test_attempt_input_read_is_bounded_before_json(self):
        path = self.root / "local_state/oversized.json"
        path.write_bytes(b" " * (protocol.EVENT_LIMIT + 1))
        with mock.patch.object(ledger.json, "loads", wraps=json.loads) as loads:
            code, out = self.cli("attempt", "local_state/oversized.json")
        self.assertEqual(code, 2)
        self.assertIn("attempt event exceeds 2 MiB", out)
        self.assertFalse(
            any(len(call.args[0]) > protocol.EVENT_LIMIT for call in loads.call_args_list)
        )

    def test_removed_commands_refuse_before_dispatch_without_writes(self):
        import _workspace
        before = _workspace.snapshot(self.root)
        for args, diagnostic in (
            (("prompt", "M001-S01", "prompts/for_coding_agent/README.md"),
             "invalid choice: 'prompt'"),
            (("unblock", "M001-S01", "--reason", "owner"), "invalid choice: 'unblock'"),
            (("accept", "M001-S01", "--commit-id", "a" * 40),
             "unrecognized arguments: --commit-id"),
        ):
            output = io.StringIO()
            with self.subTest(args=args), mock.patch.object(ledger, "_execute") as dispatch:
                with contextlib.redirect_stderr(output), self.assertRaises(SystemExit) as failure:
                    ledger.main(["--root", str(self.root), *args])
                self.assertEqual(failure.exception.code, 2)
                self.assertIn(diagnostic, output.getvalue())
                dispatch.assert_not_called()
                self.assertEqual(_workspace.snapshot(self.root), before)

    def test_gate_reused_inside_command_revalidates_changed_ledger_and_next_command(self):
        rm = roadmap.load(self.root)
        path = self.root / commit.LEDGER
        with mock.patch.object(ledger, "fold", wraps=ledger.fold) as fold:
            with c.command_scope(), mock.patch.object(c, "file_key", return_value=("same",)):
                protocol.ensure_writable(self.root, rm)
                protocol.ensure_writable(self.root, rm)
                self.assertEqual(fold.call_count, 1)
                path.write_text(json.dumps(event("note", text="new event")) + "\n")
                protocol.ensure_writable(self.root, rm)
                self.assertEqual(fold.call_count, 2)
                path.write_text(json.dumps(event("note", text="old event")) + "\n")
                protocol.ensure_writable(self.root, rm)
                self.assertEqual(fold.call_count, 3)
            with c.command_scope():
                protocol.ensure_writable(self.root, rm)
            self.assertEqual(fold.call_count, 4)

    def test_immutable_json_cache_detects_tamper_and_does_not_share_mutable_results(self):
        path = self.root / "05_governance/reviews/data.json"
        path.write_text('{"items": [1]}\n')
        ref = {"path": "05_governance/reviews/data.json", "sha": c.sha(path)}
        with c.command_scope(), mock.patch.object(c, "file_key", return_value=("same",)):
            first = protocol.read_reference(self.root, ref)
            first["items"].append(2)
            self.assertEqual(protocol.read_reference(self.root, ref), {"items": [1]})
            with self.assertRaisesRegex(ValueError, "read bound"):
                protocol.read_reference(self.root, ref, 2)
            path.write_text('{"items": [2]}\n')
            with self.assertRaisesRegex(ValueError, "immutable evidence drift"):
                protocol.read_reference(self.root, ref)
        with c.command_scope(), self.assertRaisesRegex(ValueError, "immutable evidence drift"):
            protocol.read_reference(self.root, ref)

    def test_full_evidence_memo_budget_still_validates_without_caching(self):
        path = self.root / "05_governance/reviews/data.json"
        path.write_text('{"items": [1]}\n')
        ref = {"path": "05_governance/reviews/data.json", "sha": c.sha(path)}
        with (
            c.command_scope(),
            mock.patch.object(c, "MEMO_LIMIT", 1),
            mock.patch.object(protocol.json, "loads", wraps=json.loads) as loads,
        ):
            self.assertEqual(protocol.read_reference(self.root, ref), {"items": [1]})
            self.assertEqual(protocol.read_reference(self.root, ref), {"items": [1]})
            self.assertEqual(loads.call_count, 2)
            self.assertEqual(c.cache("json_evidence"), {})

    def test_refused_coded_and_intent_append_leave_no_new_json(self):
        prompt.coding(self.root, "M001-S01")
        path = self.root / "05_governance/reviews"
        product = self.root / "07_app/staged.txt"
        product.write_text("pre-existing staged work\n")
        c.git(self.root, "add", "07_app/staged.txt")

        def snapshot():
            return {
                "artifacts": {p.relative_to(path).as_posix(): p.read_bytes()
                              for p in path.rglob("*") if p.is_file()},
                "ledger": (self.root / commit.LEDGER).read_bytes(),
                "index": c.git(self.root, "ls-files", "--stage", "-v", "-z").stdout,
                "head": c.git(self.root, "rev-parse", "HEAD").stdout,
                "product": product.read_bytes(),
            }

        before = snapshot()
        with mock.patch.object(
            ledger, "append", side_effect=ValueError("confirmed refusal")
        ) as append:
            code, output = self.cli("coded", "M001-S01")
        append.assert_called_once()
        self.assertEqual(code, 2)
        self.assertIn("confirmed refusal", output)
        self.assertEqual(snapshot(), before)
        self.assertEqual(self.cli("coded", "M001-S01")[0], 0)
        self.assertTrue(verify.run(self.root, "M001-S01", 10)[0]["ok"])
        (path / "pass.md").write_text(PASS_REPORT)
        self.assertEqual(self.cli("record", "05_governance/reviews/pass.md")[0], 0)
        before = snapshot()
        with mock.patch.object(
            ledger, "append", side_effect=ValueError("confirmed refusal")
        ) as append:
            code, output = self.cli("accept", "M001-S01", "--commit")
        append.assert_called_once()
        self.assertEqual(code, 2)
        self.assertIn("confirmed refusal", output)
        self.assertIn("commit_intent", append.call_args.args[1])
        self.assertEqual(snapshot(), before)

    def test_artifact_rollback_preserves_existing_changed_and_uncertain_files(self):
        old = self.root / "05_governance/reviews/old.json"
        new = old.with_name("new.json")
        old.write_text("old\n")
        with self.assertRaises(ValueError), c.artifact_batch(self.root):
            c.artifact_text(old, "old\n")
            c.artifact_text(new, "new\n")
            new.write_text("owner changed it\n")
            raise ValueError("confirmed refusal")
        self.assertEqual(old.read_text(), "old\n")
        self.assertEqual(new.read_text(), "owner changed it\n")
        uncertain = old.with_name("uncertain.json")
        with self.assertRaises(ValueError), c.artifact_batch(self.root):
            c.artifact_text(uncertain, "evidence\n")
            with (self.root / commit.LEDGER).open("a") as stream:
                stream.write('{"partial":')
            raise ValueError("uncertain append")
        self.assertEqual(uncertain.read_text(), "evidence\n")

    def test_holistic_waiver_cli_requires_human_after_slice_acceptance(self):
        prompt.coding(self.root, "M001-S01")
        self.assertEqual(self.cli("coded", "M001-S01")[0], 0)
        self.assertTrue(verify.run(self.root, "M001-S01", 10)[0]["ok"])
        folder = self.root / "05_governance/reviews"
        (folder / "pass.md").write_text(PASS_REPORT)
        self.assertEqual(self.cli("record", "05_governance/reviews/pass.md")[0], 0)
        self.assertEqual(self.cli("accept", "M001-S01", "--commit")[0], 0)
        text = PASS_REPORT.replace("M001-S01 round 1", "M001 round holistic")
        text = text.replace(
            "| --- | --- | --- | --- |",
            "| --- | --- | --- | --- |\n| M001-S01-F1 | P2 | waived_by_human | Waiver |",
        )
        (folder / "holistic.md").write_text(text)
        before = (self.root / commit.LEDGER).read_bytes()
        code, out = self.cli(
            "record",
            "05_governance/reviews/holistic.md",
            "--milestone",
            "M001",
            "--by",
            "architect",
        )
        self.assertEqual(code, 2)
        self.assertIn("requires --by human", out)
        self.assertEqual((self.root / commit.LEDGER).read_bytes(), before)
        code, out = self.cli(
            "record",
            "05_governance/reviews/holistic.md",
            "--milestone",
            "M001",
            "--by",
            "human",
        )
        self.assertEqual(code, 0, out)

    def test_pending_intent_freezes_all_ordinary_mutations(self):
        prompt.coding(self.root, "M001-S01")
        self.assertEqual(self.cli("coded", "M001-S01")[0], 0)
        self.assertTrue(verify.run(self.root, "M001-S01", 10)[0]["ok"])
        (self.root / "05_governance/reviews/pass.md").write_text(PASS_REPORT)
        self.assertEqual(self.cli("record", "05_governance/reviews/pass.md")[0], 0)
        with mock.patch.object(commit, "recover", side_effect=ValueError("interruption")):
            self.assertEqual(self.cli("accept", "M001-S01", "--commit")[0], 2)
        before = (self.root / commit.LEDGER).read_bytes()
        for args in (
            ("coded", "M001-S01"),
            ("accept", "M001-S01"),
            ("close", "M001"),
            ("record", "05_governance/reviews/pass.md"),
            ("reconcile",),
            ("reopen", "M001-S01", "--reason", "wait"),
            ("resolve", "M001-S01", "--reason", "wait", "--authority", "00_brief/decisions.md"),
            ("index", "--output", "local_state/index.md"),
            ("attempt", "local_state/missing.json"),
            ("blocked", "M001-S01"),
        ):
            with self.subTest(args=args):
                code, out = self.cli(*args)
                self.assertEqual(code, 2)
                self.assertIn("commit pending", out)
                self.assertEqual((self.root / commit.LEDGER).read_bytes(), before)
        with self.assertRaises(ValueError):
            prompt.coding(self.root, "M001-S01")
        with self.assertRaises(ValueError):
            verify.run(self.root, "M001-S01", 10)
        self.assertEqual((self.root / commit.LEDGER).read_bytes(), before)
