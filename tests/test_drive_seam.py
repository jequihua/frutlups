"""Tests for M005-S02: the subprocess Drive seam and its frozen fixture corpus.

The corpus under ``tests/fixtures/drive_seam_v1`` is the local oracle. Every expected
exit code, stdout document, stderr class, and complete before/after map is read
from the frozen case files and compared literally; nothing here derives an
expectation from product constants or tables. Subprocess replays bind the
explicit current interpreter, set ``PYTHONPATH`` to the product source tree
explicitly, carry no ambient ``PATH`` or ``PYTHONPATH``, and run from a foreign
working directory. Temporary projects live beneath the repository's ignored
``local_state/`` and are removed after each test.

The deterministic seam tests cover what portable fixture bytes cannot express:
unreadable inputs, oversized sidecar/prompt/proposal bytes, invalid UTF-8
proposals, and the reached-boundary refusal receipt that needs a mutation fault.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

import frutlups
from frutlups import drive_seam, publication
from frutlups.cli import main

TEST_ROOT = Path(__file__).resolve().parent
REPO_ROOT = TEST_ROOT.parent
LOCAL_STATE = REPO_ROOT / "local_state"
FIXTURE_DIR = TEST_ROOT / "fixtures" / "drive_seam_v1"
FIXTURE_PREFIX = "08_pkg/tests/fixtures/drive_seam_v1/"
SRC_DIR = Path(frutlups.__file__).resolve().parents[1]

CASE_FILES = (
    "dry_run_cases.json",
    "frontier_cases.json",
    "payload_cases.json",
    "publication_cases.json",
    "refusal_cases.json",
)
CASE_KEYS = (
    "id", "verb", "argv", "stdin_utf8", "project_nodes", "expected_exit",
    "expected_stdout", "expected_stderr_class", "expected_before", "expected_after",
)
VERBS = ("drive-payload", "drive-frontier", "corrective-publish")
STDERR_CLASSES = ("none", "usage", "diagnostic")
ROUTE_TO_STEP = {
    "advance_to_next_slice": "advance_slice",
    "milestone_complete": "complete_milestone",
    "recode_same_slice": "recode_slice",
    "unblock_same_slice": "unblock_slice",
    "human_override_required": "human_gate",
    "invalid": "stop_invalid",
}

# Simulated non-following identity of a Windows junction: a directory whose
# lstat carries the mount-point reparse tag (M007-A2-F4 deterministic evidence).
_REPARSE_DIR_STAT = SimpleNamespace(
    st_mode=stat.S_IFDIR | 0o755,
    st_reparse_tag=getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003),
)
# The twelve public M004 refusal codes that are portable corpus rows. The corpus
# is not a fourteen-code static cross: ``publish_write_failed`` is proven only by
# deterministic mutation fault injection (DeterministicSeamTests), and
# ``role_impure`` is fail-closed behind the earlier singular ``entry_unhealthy``
# seam code with the underlying diagnostic retained in ``detail`` (M005-R1-F3).
M004_CODES_IN_CORPUS = (
    "layout_unresolved", "not_corrective", "entry_not_ready", "entry_unhealthy",
    "rework_context_unresolved", "target_unbound", "slice_not_in_sidecar",
    "history_unresolved", "attempt_not_fresh", "prompt_collision",
    "sidecar_update_invalid", "recovery_required",
)
INPUT_MAX_BYTES = 1_048_576
REFUSAL_SCHEMA = "frutlups.drive_seam_refusal.v1"
RECEIPT_SCHEMA = "frutlups.corrective_publication_receipt.v1"
FRONTIER_SCHEMA = "frutlups.frontier.v2"
PAYLOAD_SCHEMA = "frutlups.drive_payload.v1"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(document: object) -> bytes:
    return json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _load_cases(name: str) -> list[dict]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))["cases"]


def _case(name: str, case_id: str) -> dict:
    return next(case for case in _load_cases(name) if case["id"] == case_id)


def _all_cases() -> list[dict]:
    return [case for name in CASE_FILES for case in _load_cases(name)]


def _materialize(root: Path, nodes: list[dict]) -> None:
    for node in nodes:
        target = root.joinpath(*node["path"].split("/"))
        if node["kind"] == "directory":
            target.mkdir(parents=True, exist_ok=True)
        elif node["kind"] == "file":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(node["content_utf8"].encode("utf-8"))


def _observe(root: Path, paths) -> dict[str, dict[str, str]]:
    """An independent typed observation of every watched relative path."""

    observed: dict[str, dict[str, str]] = {}
    for relative in paths:
        target = root.joinpath(*relative.split("/"))
        try:
            os.lstat(target)
        except FileNotFoundError:
            observed[relative] = {"state": "absent"}
            continue
        if os.path.islink(target) or not target.is_file():
            observed[relative] = {"state": "unsafe", "identity": "non_regular"}
            continue
        observed[relative] = {"state": "present", "sha256": _sha256(target.read_bytes())}
    return observed


def _subprocess_env() -> dict[str, str]:
    """Explicit bindings only: no ambient PATH or PYTHONPATH reaches the child."""

    env = {"PYTHONPATH": str(SRC_DIR), "PYTHONDONTWRITEBYTECODE": "1"}
    for name in ("SYSTEMROOT", "APPDATA", "USERPROFILE", "HOME"):
        if name in os.environ:
            env[name] = os.environ[name]
    return env


def _replay(case: dict, workdir: Path):
    """Materialize the case project, run the verb, and return the observations."""

    root = workdir / "project"
    root.mkdir()
    _materialize(root, case["project_nodes"])
    watched = list(case["expected_before"])
    before = _observe(root, watched)
    argv = [arg.replace("$PROJECT_ROOT", str(root)) for arg in case["argv"]]
    stdin = case["stdin_utf8"]
    proc = subprocess.run(
        [sys.executable, *argv],
        input=None if stdin is None else stdin.encode("utf-8"),
        capture_output=True,
        cwd=workdir,
        env=_subprocess_env(),
        timeout=120,
    )
    after = _observe(root, watched)
    return proc, before, after


def _fresh_project(nodes: list[dict]):
    LOCAL_STATE.mkdir(exist_ok=True)
    tmp = TemporaryDirectory(prefix="m005_s02_seam_", dir=LOCAL_STATE)
    root = Path(tmp.name) / "project"
    root.mkdir()
    _materialize(root, nodes)
    return tmp, root


class FixtureManifestTests(unittest.TestCase):
    def test_manifest_shape_members_and_recomputed_digests(self) -> None:
        manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(set(manifest), {"schema", "version", "members"})
        self.assertEqual(manifest["schema"], "frutlups.drive_seam_fixture_manifest.v1")
        self.assertEqual(manifest["version"], 1)
        members = manifest["members"]
        self.assertEqual([m["path"] for m in members], sorted(m["path"] for m in members))
        self.assertEqual([m["path"] for m in members], [FIXTURE_PREFIX + name for name in CASE_FILES])
        for member in members:
            with self.subTest(path=member["path"]):
                self.assertEqual(set(member), {"path", "sha256"})
                local_member = FIXTURE_DIR / Path(member["path"]).name
                self.assertEqual(_sha256(local_member.read_bytes()), member["sha256"])

    def test_every_case_file_and_case_carries_the_documented_schema(self) -> None:
        seen: set[str] = set()
        for name in CASE_FILES:
            document = json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
            self.assertEqual(set(document), {"schema", "version", "cases"})
            self.assertEqual(document["schema"], "frutlups.drive_seam_cases.v1")
            self.assertEqual(document["version"], 1)
            self.assertTrue(document["cases"])
            for case in document["cases"]:
                with self.subTest(file=name, case=case.get("id")):
                    self.assertEqual(tuple(sorted(case)), tuple(sorted(CASE_KEYS)))
                    self.assertNotIn(case["id"], seen)
                    seen.add(case["id"])
                    self.assertIn(case["verb"], VERBS)
                    self.assertEqual(case["argv"][:3], ["-m", "frutlups", case["verb"]])
                    self.assertTrue(all(isinstance(arg, str) for arg in case["argv"]))
                    self.assertTrue(case["stdin_utf8"] is None or isinstance(case["stdin_utf8"], str))
                    self.assertIn(case["expected_stderr_class"], STDERR_CLASSES)
                    self.assertIsInstance(case["expected_exit"], int)
                    watched = []
                    for node in case["project_nodes"]:
                        self.assertIn(node["kind"], ("file", "directory", "absent"))
                        if node["kind"] == "file":
                            self.assertEqual(set(node), {"path", "kind", "content_utf8", "sha256"})
                            self.assertEqual(_sha256(node["content_utf8"].encode("utf-8")), node["sha256"])
                        else:
                            self.assertEqual(set(node), {"path", "kind"})
                        if node["kind"] != "directory":
                            watched.append(node["path"])
                    self.assertEqual(sorted(case["expected_before"]), sorted(watched))
                    self.assertEqual(sorted(case["expected_after"]), sorted(watched))
                    if case["expected_stderr_class"] == "usage":
                        self.assertEqual(case["expected_exit"], 2)
                        self.assertIsNone(case["expected_stdout"])
                    else:
                        self.assertIsInstance(case["expected_stdout"], dict)


class FixtureReplayTests(unittest.TestCase):
    """Every fixture row replayed through the real subprocess seam."""

    def _replay_file(self, name: str) -> None:
        LOCAL_STATE.mkdir(exist_ok=True)
        for case in _load_cases(name):
            with self.subTest(case=case["id"]):
                with TemporaryDirectory(prefix="m005_s02_replay_", dir=LOCAL_STATE) as tmp:
                    proc, before, after = _replay(case, Path(tmp))
                self.assertEqual(before, case["expected_before"])
                self.assertEqual(proc.returncode, case["expected_exit"])
                if case["expected_stdout"] is None:
                    self.assertEqual(proc.stdout, b"")
                else:
                    self.assertEqual(proc.stdout, _canonical(case["expected_stdout"]) + b"\n")
                    self.assertLessEqual(len(proc.stdout), 2_097_152)
                if case["expected_stderr_class"] == "none":
                    self.assertEqual(proc.stderr, b"")
                else:
                    self.assertNotEqual(proc.stderr, b"")
                    self.assertLessEqual(len(proc.stderr), 65_536)
                self.assertEqual(after, case["expected_after"])
                if case["expected_exit"] in (2, 3) or "--dry-run" in case["argv"]:
                    self.assertEqual(after, before, "a refusal or dry-run wrote to the project")

    def test_payload_cases(self) -> None:
        self._replay_file("payload_cases.json")

    def test_frontier_cases(self) -> None:
        self._replay_file("frontier_cases.json")

    def test_publication_cases(self) -> None:
        self._replay_file("publication_cases.json")

    def test_dry_run_cases(self) -> None:
        self._replay_file("dry_run_cases.json")

    def test_refusal_cases(self) -> None:
        self._replay_file("refusal_cases.json")


class CorpusCoverageTests(unittest.TestCase):
    """The frozen corpus independently covers the enumerated domain."""

    def test_frontier_rows_cover_every_route_and_step_and_receipt_separation(self) -> None:
        documents = [c["expected_stdout"] for c in _load_cases("frontier_cases.json") if c["expected_exit"] == 0]
        self.assertEqual({d["route"] for d in documents}, set(ROUTE_TO_STEP))
        for document in documents:
            with self.subTest(reason=document["reason"]):
                self.assertEqual(set(document), {
                    "schema", "version", "milestone", "slice", "step", "outcome", "route",
                    "milestone_complete", "reason", "receipt", "receipt_sha256",
                })
                self.assertEqual(document["schema"], FRONTIER_SCHEMA)
                self.assertEqual(document["version"], 2)
                self.assertEqual(document["outcome"], document["route"])
                self.assertEqual(document["step"], ROUTE_TO_STEP[document["route"]])
                self.assertEqual(document["milestone_complete"], document["route"] == "milestone_complete")
                self.assertTrue(document["slice"].startswith(document["milestone"] + "-"))
                if document["route"] == "invalid":
                    self.assertIsNone(document["receipt"])
                    self.assertIsNone(document["receipt_sha256"])
                    self.assertTrue(document["reason"].startswith("closure_refused:"))
                else:
                    self.assertEqual(set(document["receipt"]), {"verdict", "objective_status", "route"})
                    self.assertEqual(document["receipt"]["route"], document["route"])
                    self.assertEqual(document["receipt_sha256"], _sha256(_canonical(document["receipt"])))
        completions = [d for d in documents if d["route"] == "milestone_complete"]
        self.assertEqual(
            sorted((d["receipt"]["verdict"], d["receipt"]["objective_status"], d["reason"]) for d in completions),
            [
                ("override", "achieved", "accepted_achieved_last_slice"),
                ("pass", "achieved", "accepted_achieved_last_slice"),
                ("pass", "not_applicable", "accepted_not_applicable_explicit_milestone_complete"),
            ],
        )

    def test_payload_rows_carry_the_exact_adoption_block(self) -> None:
        documents = [c["expected_stdout"] for c in _load_cases("payload_cases.json") if c["expected_exit"] == 0]
        self.assertEqual(len(documents), 2)
        for document in documents:
            with self.subTest(slice=document["adoption"]["slice"]):
                self.assertEqual(set(document), {"schema", "version", "payload", "adoption"})
                self.assertEqual(document["schema"], PAYLOAD_SCHEMA)
                self.assertEqual(document["version"], 1)
                self.assertEqual(document["payload"]["schema"], "frutlups.slice_prompt_payload.v1")
                self.assertEqual(document["payload"]["contract_version"], 1)
                adoption = document["adoption"]
                self.assertEqual(set(adoption), {
                    "slice", "attempt", "prompt_path", "prompt_sha256", "self_report_path",
                    "evidence_paths", "prior_evidence",
                })
                self.assertEqual(adoption["slice"], document["payload"]["slice"])
                self.assertEqual(adoption["attempt"], document["payload"]["attempt"])
                self.assertRegex(adoption["prompt_sha256"], r"^[0-9a-f]{64}$")
        routine = next(d["adoption"] for d in documents if d["adoption"]["slice"] == "M001-S01")
        corrective = next(d["adoption"] for d in documents if d["adoption"]["slice"] == "M002-S02")
        self.assertIsNone(routine["attempt"])
        self.assertEqual(routine["evidence_paths"], [])
        self.assertEqual(routine["prior_evidence"], [])
        self.assertEqual(routine["self_report_path"], "05_governance/reviews/m001_s01_route_cost_ledger_self_report.md")
        self.assertEqual(corrective["attempt"], "001")
        self.assertEqual(corrective["evidence_paths"], ["01_data/evidence/m002_s02_attempt_001/joined_ledger.json"])
        self.assertEqual(corrective["self_report_path"], "05_governance/reviews/m002_s02_attempt_001_self_report.md")
        self.assertEqual([set(row) for row in corrective["prior_evidence"]], [{"path", "sha256"}])
        prompt_node = next(
            n for n in _case("payload_cases.json", "payload_routine_entry_without_attempt")["project_nodes"]
            if n["path"] == routine["prompt_path"]
        )
        self.assertEqual(routine["prompt_sha256"], prompt_node["sha256"])

    def test_refusal_documents_are_exact_bounded_and_cover_the_named_codes(self) -> None:
        codes_by_verb: dict[str, set[str]] = {verb: set() for verb in VERBS}
        for case in _all_cases():
            document = case["expected_stdout"]
            if not isinstance(document, dict) or document.get("schema") != REFUSAL_SCHEMA:
                continue
            with self.subTest(case=case["id"]):
                self.assertEqual(case["expected_exit"], 3)
                self.assertEqual(set(document), {"schema", "version", "verb", "code", "detail"})
                self.assertEqual(document["version"], 1)
                self.assertEqual(document["verb"], case["verb"])
                self.assertTrue(document["detail"])
                self.assertLessEqual(len(document["detail"].encode("utf-8")), 1024)
                self.assertNotIn("$PROJECT_ROOT", document["detail"])
            codes_by_verb[case["verb"]].add(document["code"])
        for verb in VERBS:
            self.assertIn("unsupported_version", codes_by_verb[verb])
            self.assertIn("project_root_unavailable", codes_by_verb[verb])
        self.assertIn("prompt_absent", codes_by_verb["drive-payload"])
        self.assertIn("review_report_oversized", codes_by_verb["drive-frontier"])
        self.assertIn("review_report_absent", codes_by_verb["drive-frontier"])
        self.assertIn("sidecar_absent", codes_by_verb["corrective-publish"])
        for code in (
            "malformed_json", "proposal_empty", "proposal_invalid", "proposal_target_mismatch",
            *M004_CODES_IN_CORPUS[:-1],
        ):
            self.assertIn(code, codes_by_verb["corrective-publish"], code)

    def test_receipts_cover_dry_run_publication_and_recovery_with_recomputable_identities(self) -> None:
        outcomes: dict[tuple[str, str], int] = {}
        for case in _all_cases():
            document = case["expected_stdout"]
            if not isinstance(document, dict) or document.get("schema") != RECEIPT_SCHEMA:
                continue
            with self.subTest(case=case["id"]):
                self.assertEqual(set(document), {
                    "schema", "version", "mode", "transaction_id", "proposal_sha256", "slice",
                    "attempt", "outcome", "sidecar_entry", "rendered_prompt", "refusal_codes",
                    "before", "after", "receipt_sha256",
                })
                self.assertEqual(document["version"], 1)
                proposal_sha256 = _sha256(case["stdin_utf8"].encode("utf-8"))
                self.assertEqual(document["proposal_sha256"], proposal_sha256)
                self.assertEqual(document["transaction_id"], "cp." + proposal_sha256)
                self.assertRegex(document["transaction_id"], r"^cp\.[0-9a-f]{64}$")
                self.assertEqual(len(document["transaction_id"]), 67)
                self.assertRegex(document["attempt"], r"^[0-9]{3}$")
                self.assertIsInstance(document["refusal_codes"], list)
                self.assertEqual(set(document["sidecar_entry"]), {"path", "sha256"})
                self.assertEqual(set(document["rendered_prompt"]), {"path", "sha256"})
                self.assertNotIn("{attempt}", document["rendered_prompt"]["path"])
                self.assertIn(document["attempt"], document["rendered_prompt"]["path"])
                self.assertEqual(document["mode"], "dry_run" if "--dry-run" in case["argv"] else "publish")
                without_digest = {k: v for k, v in document.items() if k != "receipt_sha256"}
                self.assertEqual(document["receipt_sha256"], _sha256(_canonical(without_digest)))
                proposal = json.loads(case["stdin_utf8"])
                entry = dict(proposal["entry_template"])
                entry["attempt"] = document["attempt"]
                self.assertEqual(document["sidecar_entry"]["sha256"], _sha256(_canonical(entry)))
                self.assertEqual(document["sidecar_entry"]["path"], proposal["sidecar_path"])
                self.assertEqual(
                    document["rendered_prompt"]["path"],
                    proposal["prompt_path"].replace("{attempt}", document["attempt"]),
                )
                for key in ("before", "after"):
                    self.assertEqual(set(document[key]), {
                        document["sidecar_entry"]["path"],
                        document["rendered_prompt"]["path"],
                        document["sidecar_entry"]["path"] + ".publish-tmp",
                        document["sidecar_entry"]["path"] + ".rollback-tmp",
                    })
                    for observation in document[key].values():
                        self.assertIn(observation["state"], ("absent", "present", "unreadable", "unsafe"))
                expected_exit = {"validated": 0, "published": 0, "refused": 3, "recovery_required": 4}
                self.assertEqual(case["expected_exit"], expected_exit[document["outcome"]])
                if document["outcome"] in ("validated", "refused"):
                    self.assertEqual(document["before"], document["after"])
                    self.assertEqual(case["expected_before"], case["expected_after"])
                if document["outcome"] == "published":
                    self.assertEqual(document["refusal_codes"], [])
                    self.assertEqual(
                        document["after"][document["rendered_prompt"]["path"]],
                        {"state": "present", "sha256": document["rendered_prompt"]["sha256"]},
                    )
                    self.assertEqual(document["before"][document["rendered_prompt"]["path"]], {"state": "absent"})
                    self.assertEqual(
                        case["expected_after"][document["rendered_prompt"]["path"]],
                        document["after"][document["rendered_prompt"]["path"]],
                    )
                if document["outcome"] == "recovery_required":
                    self.assertEqual(document["refusal_codes"], ["recovery_required"])
            outcomes[(document["mode"], document["outcome"])] = outcomes.get((document["mode"], document["outcome"]), 0) + 1
        self.assertIn(("dry_run", "validated"), outcomes)
        self.assertIn(("publish", "published"), outcomes)
        self.assertIn(("publish", "recovery_required"), outcomes)

    def test_dry_run_and_publish_of_identical_bytes_resolve_the_same_identity(self) -> None:
        dry = _case("dry_run_cases.json", "dry_run_validated")["expected_stdout"]
        published = _case("publication_cases.json", "publish_clean_success")["expected_stdout"]
        reallocated = _case("dry_run_cases.json", "dry_run_history_change_reallocates")["expected_stdout"]
        self.assertEqual(dry["transaction_id"], published["transaction_id"])
        self.assertEqual(dry["attempt"], published["attempt"])
        self.assertEqual(dry["rendered_prompt"], published["rendered_prompt"])
        self.assertEqual(dry["sidecar_entry"], published["sidecar_entry"])
        self.assertEqual(dry["before"], published["before"])
        self.assertEqual(reallocated["transaction_id"], dry["transaction_id"])
        self.assertNotEqual(reallocated["attempt"], dry["attempt"])
        self.assertNotEqual(reallocated["rendered_prompt"]["path"], dry["rendered_prompt"]["path"])


class DeterministicSeamTests(unittest.TestCase):
    """Conditions portable fixture bytes cannot express, proven in-process."""

    def setUp(self) -> None:
        case = _case("dry_run_cases.json", "dry_run_validated")
        self._tmp, self.root = _fresh_project(case["project_nodes"])
        self.addCleanup(self._tmp.cleanup)
        self.proposal = case["stdin_utf8"].encode("utf-8")
        self.sidecar = "03_experiments/active_roadmap.slices.yaml"
        self.prompt_template = "prompts/for_coding_agent/004_m002_s02_attempt_{attempt}.md"
        self.report = "05_governance/reviews/m001_s01_review_report.md"
        (self.root / "05_governance" / "reviews").mkdir(parents=True)
        (self.root / self.report).write_text(
            "## Closure Decision\n\nObjective status: achieved\nObjective evidence: cited\n\n"
            "## Verdict\n\nVerdict: pass - next: one move\n",
            encoding="utf-8",
        )

    def _payload(self, **overrides):
        kwargs = dict(project_root=str(self.root), sidecar=self.sidecar, slice_id="M001-S01",
                      prompt="prompts/for_coding_agent/001_m001_s01_ledger.md", version="1")
        kwargs.update(overrides)
        return drive_seam.run_drive_payload(**kwargs)

    def _frontier(self, **overrides):
        kwargs = dict(project_root=str(self.root), sidecar=self.sidecar, slice_id="M001-S01",
                      review_report=self.report, version="2")
        kwargs.update(overrides)
        return drive_seam.run_drive_frontier(**kwargs)

    def _publish(self, **overrides):
        kwargs = dict(project_root=str(self.root), sidecar=self.sidecar, prompt=self.prompt_template,
                      version="1", proposal_bytes=self.proposal, dry_run=False)
        kwargs.update(overrides)
        return drive_seam.run_corrective_publish(**kwargs)

    def _assert_refusal(self, result, verb: str, code: str) -> None:
        self.assertEqual(result.exit_code, 3)
        self.assertEqual(result.document["schema"], REFUSAL_SCHEMA)
        self.assertEqual(result.document["verb"], verb)
        self.assertEqual(result.document["code"], code)

    def _snapshot(self) -> dict[str, str]:
        return {
            str(path.relative_to(self.root)): _sha256(path.read_bytes())
            for path in sorted(self.root.rglob("*")) if path.is_file()
        }

    def test_unreadable_inputs_are_typed_refusals_per_verb(self) -> None:
        real = publication._read_bounded

        def unreadable(name: str):
            def read(path, limit):
                if path.name == name:
                    raise PermissionError("read denied")
                return real(path, limit)
            return read

        rows = (
            ("sidecar", "active_roadmap.slices.yaml", self._payload, "drive-payload", "sidecar_unreadable"),
            ("prompt", "001_m001_s01_ledger.md", self._payload, "drive-payload", "prompt_unreadable"),
            ("review report", "m001_s01_review_report.md", self._frontier, "drive-frontier", "review_report_unreadable"),
            ("corrective sidecar", "active_roadmap.slices.yaml", self._publish, "corrective-publish", "sidecar_unreadable"),
        )
        for story, name, run, verb, code in rows:
            with self.subTest(story=story), mock.patch.object(drive_seam, "_read_bounded", side_effect=unreadable(name)):
                self._assert_refusal(run(), verb, code)
        with mock.patch.object(publication, "_read_bounded", side_effect=unreadable("frutlups.layout.yaml")):
            self._assert_refusal(self._payload(), "drive-payload", "layout_unresolved")

    def test_review_report_that_is_not_utf8_is_unreadable_not_a_frontier(self) -> None:
        (self.root / self.report).write_bytes(b"## Closure Decision\n\xff\n")
        self._assert_refusal(self._frontier(), "drive-frontier", "review_report_unreadable")

    def test_oversized_sidecar_prompt_and_proposal_refuse_before_any_parse(self) -> None:
        oversized = b"x" * (INPUT_MAX_BYTES + 1)
        (self.root / "prompts" / "for_coding_agent" / "001_m001_s01_ledger.md").write_bytes(oversized)
        self._assert_refusal(self._payload(), "drive-payload", "prompt_oversized")
        (self.root / self.sidecar).write_bytes(oversized)
        with mock.patch.object(drive_seam, "parse_sidecar", autospec=True) as parse:
            self._assert_refusal(self._payload(), "drive-payload", "sidecar_oversized")
            self._assert_refusal(self._frontier(), "drive-frontier", "sidecar_oversized")
            self._assert_refusal(self._publish(), "corrective-publish", "sidecar_oversized")
        self.assertEqual(parse.call_count, 0)
        with mock.patch.object(drive_seam, "_parse_proposal", autospec=True) as parse_proposal:
            self._assert_refusal(self._publish(proposal_bytes=oversized), "corrective-publish", "proposal_oversized")
        self.assertEqual(parse_proposal.call_count, 0)
        self.assertLessEqual(len(oversized), INPUT_MAX_BYTES + 1)

    def test_proposal_byte_defects_refuse_proposal_invalid_without_mutation(self) -> None:
        rows = (
            ("invalid UTF-8", b'{"schema": "\xff"}', "proposal_invalid"),
            ("infinity", self.proposal.replace(b'"version": 1', b'"version": Infinity', 1), "proposal_invalid"),
            ("trailing garbage", self.proposal + b"{}", "malformed_json"),
            ("empty", b"", "proposal_empty"),
        )
        before = self._snapshot()
        for story, raw, code in rows:
            with self.subTest(story=story):
                self._assert_refusal(self._publish(proposal_bytes=raw), "corrective-publish", code)
        self.assertEqual(self._snapshot(), before)

    def test_dry_run_writes_nothing_and_publish_of_the_same_bytes_matches_it(self) -> None:
        before = self._snapshot()
        dry = self._publish(dry_run=True)
        self.assertEqual(dry.exit_code, 0)
        self.assertEqual(dry.document["outcome"], "validated")
        self.assertEqual(dry.document["mode"], "dry_run")
        self.assertEqual(self._snapshot(), before)

        published = self._publish()
        self.assertEqual(published.exit_code, 0)
        self.assertEqual(published.document["outcome"], "published")
        self.assertEqual(published.document["transaction_id"], dry.document["transaction_id"])
        self.assertEqual(published.document["attempt"], dry.document["attempt"])
        self.assertEqual(published.document["rendered_prompt"], dry.document["rendered_prompt"])
        prompt = self.root / published.document["rendered_prompt"]["path"]
        self.assertEqual(_sha256(prompt.read_bytes()), published.document["rendered_prompt"]["sha256"])
        self.assertEqual(
            published.document["after"][self.sidecar]["sha256"],
            _sha256((self.root / self.sidecar).read_bytes()),
        )
        self.assertEqual(published.document["before"], dry.document["before"])
        self.assertIn("## Typed Entry", prompt.read_text(encoding="utf-8"))

        # A retry of the identical proposal now allocates the next attempt.
        retry = self._publish(dry_run=True)
        self.assertEqual(retry.document["transaction_id"], dry.document["transaction_id"])
        self.assertEqual(retry.document["attempt"], "003")
        self.assertTrue(retry.document["rendered_prompt"]["path"].endswith("_003.md"))

    def test_reached_boundary_refusal_carries_a_receipt_with_equal_maps_on_exit_3(self) -> None:
        before = self._snapshot()
        with mock.patch.object(publication, "_create_exclusive", side_effect=OSError("prompt open failed")):
            result = self._publish()
        self.assertEqual(result.exit_code, 3)
        document = result.document
        self.assertEqual(document["schema"], RECEIPT_SCHEMA)
        self.assertEqual(document["mode"], "publish")
        self.assertEqual(document["outcome"], "refused")
        self.assertEqual(document["refusal_codes"], ["publish_write_failed"])
        self.assertEqual(document["before"], document["after"])
        self.assertEqual(self._snapshot(), before)
        without_digest = {k: v for k, v in document.items() if k != "receipt_sha256"}
        self.assertEqual(document["receipt_sha256"], _sha256(_canonical(without_digest)))

    def test_recovery_required_after_partial_mutation_is_exit_4_with_observed_maps(self) -> None:
        with mock.patch.object(publication, "_create_exclusive", side_effect=OSError("prompt open failed")), \
                mock.patch.object(publication, "_rollback", side_effect=OSError("rollback failed")):
            result = self._publish()
        self.assertEqual(result.exit_code, 4)
        self.assertEqual(result.document["outcome"], "recovery_required")
        self.assertEqual(result.document["mode"], "publish")
        self.assertEqual(result.document["refusal_codes"], ["recovery_required"])
        self.assertEqual(
            result.document["after"][self.sidecar]["sha256"],
            _sha256((self.root / self.sidecar).read_bytes()),
        )
        self.assertNotEqual(result.document["before"], result.document["after"])

    def test_explicit_routing_status_outside_the_vocabulary_refuses_in_process(self) -> None:
        self._assert_refusal(self._frontier(explicit_routing_status="finished"), "drive-frontier", "routing_status_invalid")

    def test_refusal_detail_is_bounded_to_1024_bytes(self) -> None:
        document = drive_seam._refusal("drive-payload", "sidecar_invalid", "é" * 2000).document
        self.assertLessEqual(len(document["detail"].encode("utf-8")), 1024)
        self.assertTrue(document["detail"])
        self.assertEqual(drive_seam._refusal("drive-payload", "x", "").document["detail"], "no further detail")


_RELATIVE_ROOTS = ("project", ".", "..", "project/../project", "./project", "../project")
_RELATIVE_ROOT_REFUSAL = {
    "schema": REFUSAL_SCHEMA,
    "version": 1,
    "code": "project_root_unavailable",
    "detail": "PROJECT_ROOT must be an absolute path; relative spellings are never resolved against cwd",
}


class RootAuthorityCorrectionTests(unittest.TestCase):
    """M005-R1-F1: PROJECT_ROOT is admitted only as an absolute, cwd-independent value."""

    def setUp(self) -> None:
        self.case = _case("dry_run_cases.json", "dry_run_validated")
        self.proposal = self.case["stdin_utf8"]

    def _argv(self, verb: str, root: str) -> list[str]:
        sidecar = "03_experiments/active_roadmap.slices.yaml"
        if verb == "drive-payload":
            return ["-m", "frutlups", verb, root, "--sidecar", sidecar, "--slice", "M001-S01",
                    "--prompt", "prompts/for_coding_agent/001_m001_s01_ledger.md", "--version", "1"]
        if verb == "drive-frontier":
            return ["-m", "frutlups", verb, root, "--sidecar", sidecar, "--slice", "M001-S01",
                    "--review-report", "05_governance/reviews/m001_s01_review_report.md", "--version", "2"]
        return ["-m", "frutlups", verb, root, "--sidecar", sidecar,
                "--prompt", "prompts/for_coding_agent/004_m002_s02_attempt_{attempt}.md", "--version", "1"]

    def _tree(self, root: Path) -> dict[str, str]:
        return {str(p.relative_to(root)): _sha256(p.read_bytes()) for p in sorted(root.rglob("*")) if p.is_file()}

    def test_relative_roots_refuse_identically_from_two_foreign_cwds_with_zero_write(self) -> None:
        LOCAL_STATE.mkdir(exist_ok=True)
        with TemporaryDirectory(prefix="m005_s02_cwd_a_", dir=LOCAL_STATE) as cwd_a, \
                TemporaryDirectory(prefix="m005_s02_cwd_b_", dir=LOCAL_STATE) as cwd_b:
            cwds = (Path(cwd_a), Path(cwd_b))
            for cwd in cwds:
                _materialize(cwd / "project", self.case["project_nodes"])
                (cwd / "project" / "05_governance" / "reviews").mkdir(parents=True)
                (cwd / "project" / "05_governance" / "reviews" / "m001_s01_review_report.md").write_text(
                    "## Closure Decision\n\nObjective status: achieved\nObjective evidence: cited\n\n"
                    "## Verdict\n\nVerdict: pass - next: one move\n", encoding="utf-8",
                )
            # The second repository differs so a cwd-selected root would be visible.
            (cwds[1] / "project" / "prompts" / "for_coding_agent" / "001_m001_s01_ledger.md").write_text(
                "different prompt bytes\n", encoding="utf-8"
            )
            before = [self._tree(cwd / "project") for cwd in cwds]
            self.assertNotEqual(before[0], before[1])
            for verb in VERBS:
                for root in _RELATIVE_ROOTS:
                    with self.subTest(verb=verb, root=root):
                        documents = []
                        for cwd in cwds:
                            proc = subprocess.run(
                                [sys.executable, *self._argv(verb, root)],
                                input=self.proposal.encode("utf-8") if verb == "corrective-publish" else None,
                                capture_output=True, cwd=cwd, env=_subprocess_env(), timeout=120,
                            )
                            self.assertEqual(proc.returncode, 3)
                            self.assertEqual(proc.stderr, b"")
                            documents.append(json.loads(proc.stdout))
                        self.assertEqual(documents[0], documents[1])
                        self.assertEqual(documents[0], {**_RELATIVE_ROOT_REFUSAL, "verb": verb})
            self.assertEqual([self._tree(cwd / "project") for cwd in cwds], before)

            # Absolute positive controls from a foreign cwd keep the reviewed outputs.
            for cwd in cwds:
                for verb, expected in (
                    ("drive-payload", PAYLOAD_SCHEMA),
                    ("drive-frontier", FRONTIER_SCHEMA),
                    ("corrective-publish", RECEIPT_SCHEMA),
                ):
                    with self.subTest(control=verb, cwd=cwd.name):
                        argv = self._argv(verb, str(cwds[0] / "project"))
                        if verb == "corrective-publish":
                            argv.append("--dry-run")
                        proc = subprocess.run(
                            [sys.executable, *argv],
                            input=self.proposal.encode("utf-8") if verb == "corrective-publish" else None,
                            capture_output=True, cwd=cwd, env=_subprocess_env(), timeout=120,
                        )
                        self.assertEqual(proc.returncode, 0)
                        self.assertEqual(json.loads(proc.stdout)["schema"], expected)
            payload_case = _case("payload_cases.json", "payload_routine_entry_without_attempt")
            proc = subprocess.run(
                [sys.executable, *self._argv("drive-payload", str(cwds[0] / "project"))],
                capture_output=True, cwd=cwds[1], env=_subprocess_env(), timeout=120,
            )
            self.assertEqual(json.loads(proc.stdout), payload_case["expected_stdout"])
            self.assertEqual([self._tree(cwd / "project") for cwd in cwds], before)

    def test_relative_roots_reach_no_authority_observation_or_mutation_seam(self) -> None:
        seams = {
            "drive_seam._load_layout_authority": (drive_seam, "_load_layout_authority"),
            "drive_seam._read_governed": (drive_seam, "_read_governed"),
            "drive_seam.prepare_corrective_attempt": (drive_seam, "prepare_corrective_attempt"),
            "drive_seam.observe_owned_state": (drive_seam, "observe_owned_state"),
            "drive_seam.commit_prepared_publication": (drive_seam, "commit_prepared_publication"),
            "publication._stage_and_replace": (publication, "_stage_and_replace"),
            "publication._create_exclusive": (publication, "_create_exclusive"),
            "os.lstat": (drive_seam.os, "lstat"),
        }
        for root in _RELATIVE_ROOTS:
            for verb, run in (
                ("drive-payload", lambda r: drive_seam.run_drive_payload(
                    project_root=r, sidecar="s", slice_id="M001-S01", prompt="p", version="1")),
                ("drive-frontier", lambda r: drive_seam.run_drive_frontier(
                    project_root=r, sidecar="s", slice_id="M001-S01", review_report="r", version="2")),
                ("corrective-publish", lambda r: drive_seam.run_corrective_publish(
                    project_root=r, sidecar="03_experiments/active_roadmap.slices.yaml",
                    prompt="prompts/for_coding_agent/004_m002_s02_attempt_{attempt}.md", version="1",
                    proposal_bytes=self.proposal.encode("utf-8"), dry_run=False)),
            ):
                with self.subTest(verb=verb, root=root):
                    with mock.patch.object(Path, "resolve", side_effect=AssertionError("resolve reached")) as resolve:
                        spies = {}
                        stack = []
                        try:
                            for name, (owner, attribute) in seams.items():
                                patcher = mock.patch.object(owner, attribute, autospec=True)
                                spies[name] = patcher.start()
                                stack.append(patcher)
                            result = run(root)
                        finally:
                            for patcher in stack:
                                patcher.stop()
                    self.assertEqual(result.exit_code, 3)
                    self.assertEqual(result.document, {**_RELATIVE_ROOT_REFUSAL, "verb": verb})
                    self.assertEqual(resolve.call_count, 0)
                    for name, spy in spies.items():
                        self.assertEqual(spy.call_count, 0, f"{name} reached for {verb} root {root!r}")


class AliasAdmissionCorrectionTests(unittest.TestCase):
    """M005-R1-F2: intermediate aliases refuse before any final-child identity access."""

    def setUp(self) -> None:
        case = _case("dry_run_cases.json", "dry_run_validated")
        self._tmp, self.root = _fresh_project(case["project_nodes"])
        self.addCleanup(self._tmp.cleanup)
        self.proposal = case["stdin_utf8"].encode("utf-8")
        self.report = "05_governance/reviews/m001_s01_review_report.md"
        (self.root / "05_governance" / "reviews").mkdir(parents=True)
        (self.root / self.report).write_text(
            "## Closure Decision\n\nObjective status: achieved\nObjective evidence: cited\n\n"
            "## Verdict\n\nVerdict: pass - next: one move\n", encoding="utf-8",
        )

    # (story, verb, kind, keyword carrying the governed reference, runner)
    def _rows(self):
        sidecar = "03_experiments/active_roadmap.slices.yaml"
        prompt = "prompts/for_coding_agent/001_m001_s01_ledger.md"
        template = "prompts/for_coding_agent/004_m002_s02_attempt_{attempt}.md"

        def payload(**kw):
            args = dict(project_root=str(self.root), sidecar=sidecar, slice_id="M001-S01", prompt=prompt, version="1")
            args.update(kw)
            return drive_seam.run_drive_payload(**args)

        def frontier(**kw):
            args = dict(project_root=str(self.root), sidecar=sidecar, slice_id="M001-S01", review_report=self.report, version="2")
            args.update(kw)
            return drive_seam.run_drive_frontier(**args)

        def publish(**kw):
            args = dict(project_root=str(self.root), sidecar=sidecar, prompt=template, version="1",
                        proposal_bytes=self.proposal, dry_run=True)
            args.update(kw)
            return drive_seam.run_corrective_publish(**args)

        return (
            ("drive-payload", "sidecar", "sidecar", payload),
            ("drive-payload", "prompt", "prompt", payload),
            ("drive-frontier", "sidecar", "sidecar", frontier),
            ("drive-frontier", "review_report", "review_report", frontier),
            ("corrective-publish", "sidecar", "sidecar", publish),
        )

    def _publish_with_proposal_paths(self, sidecar_path: str):
        proposal = json.loads(self.proposal)
        proposal["sidecar_path"] = sidecar_path
        return drive_seam.run_corrective_publish(
            project_root=str(self.root), sidecar=sidecar_path, prompt=proposal["prompt_path"], version="1",
            proposal_bytes=json.dumps(proposal).encode("utf-8"), dry_run=True,
        )

    def test_intermediate_alias_table_refuses_before_final_child_identity_for_every_kind(self) -> None:
        for verb, kind, keyword, run in self._rows():
            for child_state in ("absent", "present"):
                with self.subTest(verb=verb, kind=kind, child=child_state):
                    reference = f"alias_dir/{child_state}_child.md"
                    alias_dir = self.root / "alias_dir"
                    alias_dir.mkdir(exist_ok=True)
                    child = alias_dir / f"{child_state}_child.md"
                    if child_state == "present":
                        child.write_bytes(b"outside bytes\n")
                    elif child.exists():
                        child.unlink()

                    def fake_is_symlink(path_self, *, target=alias_dir):
                        return path_self == target

                    with mock.patch.object(Path, "is_symlink", fake_is_symlink), \
                            mock.patch.object(drive_seam, "_lstat_target", wraps=drive_seam._lstat_target) as lstat_seam, \
                            mock.patch.object(drive_seam, "_read_bounded", wraps=drive_seam._read_bounded) as read_seam:
                        if verb == "corrective-publish":
                            result = self._publish_with_proposal_paths(reference)
                        else:
                            result = run(**{keyword: reference})
                    self.assertEqual(result.exit_code, 3)
                    self.assertEqual(result.document["schema"], REFUSAL_SCHEMA)
                    self.assertEqual(result.document["verb"], verb)
                    self.assertEqual(result.document["code"], "target_unbound")
                    self.assertEqual(result.document["detail"], f"{kind} has an intermediate alias component: {reference}")
                    # Earlier governed reads (the sidecar before a prompt or report) are
                    # legitimate; nothing beneath the alias may ever be probed or read.
                    touched = [call.args[0] for call in lstat_seam.call_args_list + read_seam.call_args_list]
                    self.assertFalse(
                        [p for p in touched if alias_dir in Path(p).parents or Path(p) == alias_dir],
                        f"final child beneath the alias was probed for {verb}/{kind}: {touched}",
                    )
        for path in (self.root / "alias_dir" / "present_child.md",):
            if path.exists():
                path.unlink()

        # M007-A2-F4: a legacy Path without is_junction (the Python 3.11 floor)
        # must still recognize a Windows junction through its mount-point reparse
        # tag, observed without following it, so the same refusal precedes any
        # final-child identity access. The directory is real and stays a
        # non-symlink; only the non-following lstat reparse evidence is
        # simulated, which needs no junction-creation privilege. Junctions exist
        # only on Windows, so this pass is skipped elsewhere.
        if os.name == "nt":
            for verb, kind, keyword, run in self._rows():
                with self.subTest(verb=verb, kind=kind, child="reparse"):
                    reference = "alias_dir/reparse_child.md"
                    alias_dir = self.root / "alias_dir"
                    alias_dir.mkdir(exist_ok=True)
                    child = alias_dir / "reparse_child.md"
                    child.write_bytes(b"outside bytes\n")

                    real_lstat = os.lstat
                    alias_observations = []

                    def reparse_lstat(path, *, target=alias_dir):
                        if Path(path) == target:
                            alias_observations.append(str(target))
                            return _REPARSE_DIR_STAT
                        return real_lstat(path)

                    with mock.patch.object(Path, "is_junction", None, create=True), \
                            mock.patch.object(os, "lstat", reparse_lstat), \
                            mock.patch.object(drive_seam, "_lstat_target", wraps=drive_seam._lstat_target) as lstat_seam, \
                            mock.patch.object(drive_seam, "_read_bounded", wraps=drive_seam._read_bounded) as read_seam:
                        if verb == "corrective-publish":
                            result = self._publish_with_proposal_paths(reference)
                        else:
                            result = run(**{keyword: reference})
                    self.assertEqual(result.exit_code, 3)
                    self.assertEqual(result.document["schema"], REFUSAL_SCHEMA)
                    self.assertEqual(result.document["verb"], verb)
                    self.assertEqual(result.document["code"], "target_unbound")
                    self.assertEqual(result.document["detail"], f"{kind} has an intermediate alias component: {reference}")
                    # The refusal is causal: the legacy fallback observed the
                    # intermediate component's non-following reparse identity,
                    # and nothing beneath the alias was ever probed or read.
                    self.assertTrue(alias_observations, f"reparse evidence never consulted for {verb}/{kind}")
                    touched = [call.args[0] for call in lstat_seam.call_args_list + read_seam.call_args_list]
                    self.assertFalse(
                        [p for p in touched if alias_dir in Path(p).parents or Path(p) == alias_dir],
                        f"final child beneath the reparse alias was probed for {verb}/{kind}: {touched}",
                    )
            reparse_child = self.root / "alias_dir" / "reparse_child.md"
            if reparse_child.exists():
                reparse_child.unlink()

    def test_alias_free_absent_and_readable_controls_keep_their_trusted_path_codes(self) -> None:
        for verb, kind, keyword, run in self._rows():
            with self.subTest(verb=verb, kind=kind, state="absent"):
                reference = "plain_dir/missing_child.md"
                (self.root / "plain_dir").mkdir(exist_ok=True)
                child = self.root / "plain_dir" / "missing_child.md"
                with mock.patch.object(drive_seam, "_lstat_target", wraps=drive_seam._lstat_target) as lstat_seam:
                    if verb == "corrective-publish":
                        result = self._publish_with_proposal_paths(reference)
                    else:
                        result = run(**{keyword: reference})
                self.assertEqual(result.document["code"], f"{kind}_absent")
                self.assertEqual(result.document["detail"], f"{kind} is absent: {reference}")
                self.assertEqual([c.args[0] for c in lstat_seam.call_args_list if Path(c.args[0]) == child], [child])
            with self.subTest(verb=verb, kind=kind, state="unreadable"):
                real_lstat = drive_seam._lstat_target
                target_name = {
                    "sidecar": "active_roadmap.slices.yaml",
                    "prompt": "001_m001_s01_ledger.md",
                    "review_report": "m001_s01_review_report.md",
                }[kind]

                def denied(path, *, name=target_name):
                    if Path(path).name == name:
                        raise PermissionError("identity denied")
                    return real_lstat(path)

                with mock.patch.object(drive_seam, "_lstat_target", side_effect=denied):
                    if verb == "corrective-publish":
                        result = self._publish_with_proposal_paths("03_experiments/active_roadmap.slices.yaml")
                    else:
                        result = run()
                self.assertEqual(result.document["code"], f"{kind}_unreadable")
        with self.subTest(control="readable"):
            self.assertEqual(self._rows()[0][3]().exit_code, 0)
            self.assertEqual(self._rows()[2][3]().exit_code, 0)
            self.assertEqual(self._rows()[4][3]().document["outcome"], "validated")

    def test_optional_host_junction_never_changes_the_refusal_with_the_child_absent_or_present(self) -> None:
        outside = Path(self._tmp.name) / "outside"
        outside.mkdir()
        junction = self.root / "alias_dir"
        try:
            if os.name == "nt":
                import _winapi  # type: ignore[import-not-found]

                _winapi.CreateJunction(str(outside), str(junction))
            else:
                junction.symlink_to(outside, target_is_directory=True)
        except (AttributeError, NotImplementedError, OSError):
            return  # host cannot create the alias; the deterministic table is authoritative
        try:
            documents = []
            for present in (False, True):
                if present:
                    (outside / "secret.md").write_bytes(b"outside\n")
                with mock.patch.object(drive_seam, "_lstat_target", wraps=drive_seam._lstat_target) as lstat_seam, \
                        mock.patch.object(drive_seam, "_read_bounded", wraps=drive_seam._read_bounded) as read_seam:
                    result = drive_seam.run_drive_payload(
                        project_root=str(self.root), sidecar="03_experiments/active_roadmap.slices.yaml",
                        slice_id="M001-S01", prompt="alias_dir/secret.md", version="1",
                    )
                touched = [Path(c.args[0]) for c in lstat_seam.call_args_list + read_seam.call_args_list]
                self.assertFalse([p for p in touched if junction in p.parents or p == junction], touched)
                documents.append(result.document)
            self.assertEqual(documents[0], documents[1])
            self.assertEqual(documents[0]["code"], "target_unbound")
        finally:
            if junction.is_dir() and not junction.is_symlink():
                os.rmdir(junction)
            else:
                junction.unlink()
            for child in outside.iterdir():
                child.unlink()
            outside.rmdir()


class CliWireTests(unittest.TestCase):
    """The CLI writes exactly one canonical document and keeps the 0.1.8 surface."""

    def setUp(self) -> None:
        case = _case("payload_cases.json", "payload_routine_entry_without_attempt")
        self._tmp, self.root = _fresh_project(case["project_nodes"])
        self.addCleanup(self._tmp.cleanup)
        self.case = case

    def _main(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_in_process_main_emits_the_frozen_document_once_with_one_final_lf(self) -> None:
        argv = [arg.replace("$PROJECT_ROOT", str(self.root)) for arg in self.case["argv"][2:]]
        code, out, err = self._main(argv)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(out.encode("utf-8"), _canonical(self.case["expected_stdout"]) + b"\n")
        self.assertEqual(out.count("\n"), 1)

    def test_usage_errors_exit_2_for_each_verb(self) -> None:
        for argv in (
            ["drive-payload"],
            ["drive-frontier", str(self.root), "--sidecar", "x", "--slice", "M001-S01", "--version", "2"],
            ["corrective-publish", str(self.root), "--sidecar", "x", "--version", "1"],
            ["drive-frontier", str(self.root), "--sidecar", "x", "--slice", "M001-S01", "--review-report", "r",
             "--version", "2", "--explicit-routing-status", "finished"],
        ):
            with self.subTest(argv=argv), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    main(argv)
                self.assertEqual(raised.exception.code, 2)

    def test_unknown_version_refuses_through_the_cli_without_touching_the_project(self) -> None:
        before = {p: _sha256(p.read_bytes()) for p in self.root.rglob("*") if p.is_file()}
        code, out, err = self._main([
            "corrective-publish", str(self.root), "--sidecar", "x", "--prompt", "y", "--version", "9",
        ])
        self.assertEqual(code, 3)
        document = json.loads(out)
        self.assertEqual(document["schema"], REFUSAL_SCHEMA)
        self.assertEqual(document["code"], "unsupported_version")
        self.assertEqual(err, "")
        self.assertEqual({p: _sha256(p.read_bytes()) for p in self.root.rglob("*") if p.is_file()}, before)

    def test_status_json_observation_surface_is_unchanged_and_separate(self) -> None:
        code, out, err = self._main(["status", str(self.root), "--json"])
        self.assertEqual(code, 0)
        document = json.loads(out)
        self.assertIn("planning_frontier", document)
        self.assertIn("loop_resume", document)
        self.assertEqual(document["planning_frontier"]["contract_id"], "frutlups.planning_frontier")
        self.assertEqual(document["planning_frontier"]["contract_version"], "1")
        text = json.dumps(document)
        for schema in (PAYLOAD_SCHEMA, FRONTIER_SCHEMA, RECEIPT_SCHEMA, REFUSAL_SCHEMA):
            self.assertNotIn(schema, text)
        self.assertNotIn("drive_payload", document)
        self.assertNotIn("frontier_v2", document)

    def test_help_lists_the_three_verbs_and_the_exit_contract(self) -> None:
        for verb in VERBS:
            with self.subTest(verb=verb):
                out = io.StringIO()
                with redirect_stdout(out), self.assertRaises(SystemExit) as raised:
                    main([verb, "--help"])
                self.assertEqual(raised.exception.code, 0)
                self.assertIn("exit 4", out.getvalue())
                self.assertIn("--version", out.getvalue())


if __name__ == "__main__":
    unittest.main()
