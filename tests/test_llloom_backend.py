"""Tests for M009-S02: llloom CLI backend for status, doctor, query, and verify.

Covers:
- MemoryCommandResult.to_dict() emits only JSON-safe plain values
- LlloomCliBackend constructs correct command vectors for all four methods
- All commands include --root <memory-root>
- Commands are tuples/lists of strings, not shell strings
- query includes --status reviewed --verification-status verified
- A fake runner is used; llloom need not be installed
- Missing executable or runner failure returns a result, not an exception
- No mutating command verbs (seed, apply, ingest, render, supersede, unlock,
  rebuild) appear in any read-only command vector
- LlloomCliBackend satisfies the MemoryBackend protocol
- SubprocessMemoryCommandRunner satisfies the MemoryCommandRunner protocol
- Disabled backend from M009-S01 is unchanged
- Project without memory root still reports Memory: disabled
- No test writes outside its TemporaryDirectory
"""

from __future__ import annotations

import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from frutlups.cli import main
from frutlups.memory import (
    DisabledMemoryBackend,
    LlloomCliBackend,
    MemoryBackend,
    MemoryCommandResult,
    MemoryCommandRunner,
    MemoryStatus,
    SubprocessMemoryCommandRunner,
    detect_memory,
)
from frutlups.project import build_status


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MUTATING_VERBS = frozenset({
    "seed", "apply", "ingest", "render", "supersede",
    "unlock", "reconcile", "rebuild",
})

_MEMORY_ROOT = Path("/fake/memory/root")


class _CapturingRunner:
    """Records the most recent args tuple; returns a configurable result."""

    def __init__(
        self,
        returncode: int = 0,
        stdout: str = "ok",
        stderr: str = "",
        error: str = "",
    ) -> None:
        self.captured: tuple[str, ...] | None = None
        self._returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._error = error

    def run(self, args: tuple[str, ...]) -> MemoryCommandResult:
        self.captured = args
        launcher_failure = bool(self._error)
        return MemoryCommandResult(
            command=args,
            returncode=None if launcher_failure else self._returncode,
            stdout=self._stdout,
            stderr=self._stderr,
            ok=not launcher_failure and self._returncode == 0,
            error=self._error,
        )


def _make_template(root: Path) -> None:
    for name in (
        "00_brief",
        "03_experiments",
        "05_governance",
        "06_infra",
        "08_pkg",
        "prompts/for_coding_agent",
        "prompts/for_review_agent",
    ):
        (root / name).mkdir(parents=True, exist_ok=True)


def _write_active_roadmap(root: Path) -> None:
    (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
        "# Active Roadmap\n\n### M001: Scaffold\n\nStatus: active\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# MemoryCommandResult.to_dict() JSON safety
# ---------------------------------------------------------------------------

class MemoryCommandResultToDictTests(unittest.TestCase):
    def _result(self) -> MemoryCommandResult:
        return MemoryCommandResult(
            command=("llloom", "--root", "/mem", "status"),
            returncode=0,
            stdout="ok\n",
            stderr="",
            ok=True,
        )

    def test_command_serializes_to_list(self) -> None:
        d = self._result().to_dict()
        self.assertIsInstance(d["command"], list)
        self.assertNotIsInstance(d["command"], tuple)

    def test_returncode_is_int_or_none(self) -> None:
        d = self._result().to_dict()
        self.assertIsInstance(d["returncode"], int)

    def test_returncode_none_when_launcher_failure(self) -> None:
        result = MemoryCommandResult(
            command=("llloom",),
            returncode=None,
            stdout="",
            stderr="",
            ok=False,
            error="executable not found: llloom",
        )
        self.assertIsNone(result.to_dict()["returncode"])

    def test_stdout_is_str(self) -> None:
        self.assertIsInstance(self._result().to_dict()["stdout"], str)

    def test_stderr_is_str(self) -> None:
        self.assertIsInstance(self._result().to_dict()["stderr"], str)

    def test_ok_is_bool(self) -> None:
        self.assertIsInstance(self._result().to_dict()["ok"], bool)

    def test_error_is_str(self) -> None:
        self.assertIsInstance(self._result().to_dict()["error"], str)

    def test_to_dict_is_json_serializable(self) -> None:
        json.dumps(self._result().to_dict())

    def test_no_path_objects_in_values(self) -> None:
        for v in self._result().to_dict().values():
            self.assertNotIsInstance(v, Path)

    def test_result_is_frozen(self) -> None:
        result = self._result()
        with self.assertRaises((AttributeError, TypeError)):
            result.ok = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# LlloomCliBackend: status command vector
# ---------------------------------------------------------------------------

class StatusCommandVectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = _CapturingRunner(stdout="workspace ok")
        self.backend = LlloomCliBackend(root=_MEMORY_ROOT, runner=self.runner)

    def test_status_runs_command(self) -> None:
        self.backend.status()
        self.assertIsNotNone(self.runner.captured)

    def test_status_command_starts_with_executable(self) -> None:
        self.backend.status()
        self.assertEqual(self.runner.captured[0], "llloom")

    def test_status_command_includes_root_flag(self) -> None:
        self.backend.status()
        self.assertIn("--root", self.runner.captured)

    def test_status_command_includes_root_path(self) -> None:
        self.backend.status()
        idx = list(self.runner.captured).index("--root")
        self.assertEqual(self.runner.captured[idx + 1], str(_MEMORY_ROOT))

    def test_status_command_ends_with_status(self) -> None:
        self.backend.status()
        self.assertEqual(self.runner.captured[-1], "status")

    def test_status_returns_memory_status(self) -> None:
        result = self.backend.status()
        self.assertIsInstance(result, MemoryStatus)

    def test_status_returns_enabled_true(self) -> None:
        result = self.backend.status()
        self.assertTrue(result.enabled)

    def test_status_returns_backend_llloom(self) -> None:
        result = self.backend.status()
        self.assertEqual(result.backend, "llloom")

    def test_status_root_matches_backend_root(self) -> None:
        result = self.backend.status()
        self.assertEqual(result.root, _MEMORY_ROOT)

    def test_status_message_from_stdout_when_ok(self) -> None:
        result = self.backend.status()
        self.assertIn("workspace ok", result.message)

    def test_status_message_from_error_when_launcher_fails(self) -> None:
        runner = _CapturingRunner(error="executable not found: llloom")
        backend = LlloomCliBackend(root=_MEMORY_ROOT, runner=runner)
        result = backend.status()
        self.assertIn("executable not found", result.message)

    def test_custom_executable_used_in_command(self) -> None:
        runner = _CapturingRunner()
        backend = LlloomCliBackend(
            root=_MEMORY_ROOT, executable="llloom-dev", runner=runner
        )
        backend.status()
        self.assertEqual(runner.captured[0], "llloom-dev")


# ---------------------------------------------------------------------------
# LlloomCliBackend: doctor command vector
# ---------------------------------------------------------------------------

class DoctorCommandVectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = _CapturingRunner()
        self.backend = LlloomCliBackend(root=_MEMORY_ROOT, runner=self.runner)

    def test_doctor_command_includes_root_flag(self) -> None:
        self.backend.doctor()
        self.assertIn("--root", self.runner.captured)

    def test_doctor_command_includes_root_path(self) -> None:
        self.backend.doctor()
        idx = list(self.runner.captured).index("--root")
        self.assertEqual(self.runner.captured[idx + 1], str(_MEMORY_ROOT))

    def test_doctor_command_ends_with_doctor(self) -> None:
        self.backend.doctor()
        self.assertEqual(self.runner.captured[-1], "doctor")

    def test_doctor_returns_memory_command_result(self) -> None:
        self.assertIsInstance(self.backend.doctor(), MemoryCommandResult)

    def test_doctor_result_command_is_tuple(self) -> None:
        result = self.backend.doctor()
        self.assertIsInstance(result.command, tuple)


# ---------------------------------------------------------------------------
# LlloomCliBackend: query command vector
# ---------------------------------------------------------------------------

class QueryCommandVectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = _CapturingRunner(stdout="[]\n")
        self.backend = LlloomCliBackend(root=_MEMORY_ROOT, runner=self.runner)

    def test_query_command_includes_root(self) -> None:
        self.backend.query("what is the roadmap?")
        self.assertIn("--root", self.runner.captured)

    def test_query_command_includes_question(self) -> None:
        self.backend.query("what is the roadmap?")
        self.assertIn("what is the roadmap?", self.runner.captured)

    def test_query_command_includes_query_verb(self) -> None:
        self.backend.query("anything")
        self.assertIn("query", self.runner.captured)

    def test_query_command_includes_status_reviewed(self) -> None:
        self.backend.query("anything")
        args = list(self.runner.captured)
        idx = args.index("--status")
        self.assertEqual(args[idx + 1], "reviewed")

    def test_query_command_includes_verification_status_verified(self) -> None:
        self.backend.query("anything")
        args = list(self.runner.captured)
        idx = args.index("--verification-status")
        self.assertEqual(args[idx + 1], "verified")

    def test_query_returns_memory_command_result(self) -> None:
        self.assertIsInstance(self.backend.query("q"), MemoryCommandResult)

    def test_query_result_records_question_in_command(self) -> None:
        result = self.backend.query("unique question text")
        self.assertIn("unique question text", result.command)


# ---------------------------------------------------------------------------
# LlloomCliBackend: verify command vector
# ---------------------------------------------------------------------------

class VerifyCommandVectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = _CapturingRunner()
        self.backend = LlloomCliBackend(root=_MEMORY_ROOT, runner=self.runner)

    def test_verify_command_includes_root(self) -> None:
        self.backend.verify()
        self.assertIn("--root", self.runner.captured)

    def test_verify_command_ends_with_verify(self) -> None:
        self.backend.verify()
        self.assertEqual(self.runner.captured[-1], "verify")

    def test_verify_returns_memory_command_result(self) -> None:
        self.assertIsInstance(self.backend.verify(), MemoryCommandResult)


# ---------------------------------------------------------------------------
# No mutating command verbs in any read-only command
# ---------------------------------------------------------------------------

class ReadOnlyCommandWordsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = _CapturingRunner()
        self.backend = LlloomCliBackend(root=_MEMORY_ROOT, runner=self.runner)

    def _assert_no_mutating_verbs(self, args: tuple[str, ...]) -> None:
        lowered = {a.lower() for a in args}
        found = lowered & _MUTATING_VERBS
        self.assertFalse(
            found,
            f"mutating verb(s) {found} found in command: {args}",
        )

    def test_status_command_has_no_mutating_verbs(self) -> None:
        self.backend.status()
        self._assert_no_mutating_verbs(self.runner.captured)

    def test_doctor_command_has_no_mutating_verbs(self) -> None:
        self.backend.doctor()
        self._assert_no_mutating_verbs(self.runner.captured)

    def test_query_command_has_no_mutating_verbs(self) -> None:
        self.backend.query("something")
        self._assert_no_mutating_verbs(self.runner.captured)

    def test_verify_command_has_no_mutating_verbs(self) -> None:
        self.backend.verify()
        self._assert_no_mutating_verbs(self.runner.captured)


# ---------------------------------------------------------------------------
# Missing executable / runner failure → result, not exception
# ---------------------------------------------------------------------------

class LauncherFailureTests(unittest.TestCase):
    def _failing_runner(self) -> _CapturingRunner:
        return _CapturingRunner(error="executable not found: llloom")

    def test_status_with_launcher_failure_returns_memory_status(self) -> None:
        backend = LlloomCliBackend(root=_MEMORY_ROOT, runner=self._failing_runner())
        result = backend.status()
        self.assertIsInstance(result, MemoryStatus)

    def test_doctor_with_launcher_failure_returns_result(self) -> None:
        backend = LlloomCliBackend(root=_MEMORY_ROOT, runner=self._failing_runner())
        result = backend.doctor()
        self.assertIsInstance(result, MemoryCommandResult)
        self.assertFalse(result.ok)

    def test_query_with_launcher_failure_returns_result(self) -> None:
        backend = LlloomCliBackend(root=_MEMORY_ROOT, runner=self._failing_runner())
        result = backend.query("q")
        self.assertIsInstance(result, MemoryCommandResult)
        self.assertFalse(result.ok)

    def test_verify_with_launcher_failure_returns_result(self) -> None:
        backend = LlloomCliBackend(root=_MEMORY_ROOT, runner=self._failing_runner())
        result = backend.verify()
        self.assertIsInstance(result, MemoryCommandResult)
        self.assertFalse(result.ok)

    def test_subprocess_runner_catches_file_not_found(self) -> None:
        runner = SubprocessMemoryCommandRunner()
        result = runner.run(("__nonexistent_executable_xyz__",))
        self.assertFalse(result.ok)
        self.assertIsNone(result.returncode)
        self.assertIn("executable not found", result.error)

    def test_subprocess_runner_result_is_not_exception(self) -> None:
        runner = SubprocessMemoryCommandRunner()
        try:
            result = runner.run(("__nonexistent_executable_xyz__",))
        except Exception as exc:
            self.fail(f"runner raised instead of returning result: {exc}")
        self.assertIsInstance(result, MemoryCommandResult)


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

class ProtocolConformanceTests(unittest.TestCase):
    def test_llloom_backend_satisfies_memory_backend_protocol(self) -> None:
        backend = LlloomCliBackend(
            root=_MEMORY_ROOT, runner=_CapturingRunner()
        )
        self.assertIsInstance(backend, MemoryBackend)

    def test_subprocess_runner_satisfies_memory_command_runner_protocol(self) -> None:
        self.assertIsInstance(SubprocessMemoryCommandRunner(), MemoryCommandRunner)

    def test_llloom_backend_is_frozen(self) -> None:
        backend = LlloomCliBackend(root=_MEMORY_ROOT, runner=_CapturingRunner())
        with self.assertRaises((AttributeError, TypeError)):
            backend.executable = "mutated"  # type: ignore[misc]

    def test_subprocess_runner_is_frozen(self) -> None:
        runner = SubprocessMemoryCommandRunner()
        with self.assertRaises((AttributeError, TypeError)):
            runner.timeout_seconds = 999.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Disabled backend is unchanged from M009-S01
# ---------------------------------------------------------------------------

class DisabledBackendUnchangedTests(unittest.TestCase):
    def test_disabled_backend_status_enabled_false(self) -> None:
        self.assertFalse(DisabledMemoryBackend().status().enabled)

    def test_disabled_backend_name_is_disabled(self) -> None:
        self.assertEqual(DisabledMemoryBackend().status().backend, "disabled")

    def test_detect_memory_returns_disabled_without_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            status = detect_memory(Path(tmp))
        self.assertFalse(status.enabled)


# ---------------------------------------------------------------------------
# Project without memory root still reports Memory: disabled
# ---------------------------------------------------------------------------

class ProjectWithoutMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_roadmap(self.root)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_build_status_memory_disabled(self) -> None:
        status = build_status(self.root)
        self.assertFalse(status.memory.enabled)

    def test_build_status_memory_backend_is_disabled(self) -> None:
        status = build_status(self.root)
        self.assertEqual(status.memory.backend, "disabled")

    def test_cli_status_human_output_includes_memory_disabled(self) -> None:
        buf = StringIO()
        with redirect_stdout(buf):
            main(["status", str(self.root)])
        self.assertIn("Memory: disabled", buf.getvalue())

    def test_cli_status_json_memory_disabled(self) -> None:
        buf = StringIO()
        with redirect_stdout(buf):
            main(["status", str(self.root), "--json"])
        d = json.loads(buf.getvalue())
        self.assertFalse(d["memory"]["enabled"])
        self.assertEqual(d["memory"]["backend"], "disabled")


if __name__ == "__main__":
    unittest.main()
