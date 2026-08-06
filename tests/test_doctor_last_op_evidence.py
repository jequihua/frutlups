"""Tests for M010-S03: doctor --last-op evidence capture.

Covers:
- command vector equals ("llloom", "--root", <root>, "doctor", "--last-op")
- custom executable honored
- capture helper invokes the supplied fake runner
- no mutating verbs (seed, apply, ingest, render, supersede, unlock, reconcile, rebuild)
- successful output captured in bounded stdout_summary
- empty successful output handled deterministically
- nonzero exit returns evidence without raising
- launcher failure (returncode=None) returns evidence without raising
- stdout/stderr/error summaries are bounded
- to_dict() emits only plain Python values and is JSON-serializable
- DoctorLastOpEvidence is frozen (immutable)
- LlloomCliBackend.doctor_last_op() delegates to capture helper
- existing M009 and M010-S02 tests remain green (checked by full suite)
"""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from frutlups.memory import (
    DoctorLastOpEvidence,
    LlloomCliBackend,
    MemoryCommandResult,
    capture_doctor_last_op_evidence,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MUTATING_VERBS = frozenset({
    "seed", "apply", "ingest", "render", "supersede",
    "unlock", "reconcile", "rebuild",
})

_ROOT = Path("/memory/root")
_EXE = "llloom"

_EXPECTED_COMMAND = (_EXE, "--root", str(_ROOT), "doctor", "--last-op")


class _FakeRunner:
    """Returns a configurable MemoryCommandResult; records calls."""

    def __init__(
        self,
        returncode: int = 0,
        stdout: str = "Journal: op-001\nClaims updated: 3",
        stderr: str = "",
        error: str = "",
    ) -> None:
        self.calls: list[tuple[str, ...]] = []
        self._returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._error = error

    def run(self, args: tuple[str, ...]) -> MemoryCommandResult:
        self.calls.append(args)
        launcher_failure = bool(self._error)
        return MemoryCommandResult(
            command=args,
            returncode=None if launcher_failure else self._returncode,
            stdout=self._stdout,
            stderr=self._stderr,
            ok=not launcher_failure and self._returncode == 0,
            error=self._error,
        )

    @property
    def call_count(self) -> int:
        return len(self.calls)


# ---------------------------------------------------------------------------
# Command vector
# ---------------------------------------------------------------------------

class CommandVectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = _FakeRunner()
        self.evidence = capture_doctor_last_op_evidence(_ROOT, self.runner)

    def test_command_equals_expected(self) -> None:
        self.assertEqual(self.evidence.command, _EXPECTED_COMMAND)

    def test_command_starts_with_executable(self) -> None:
        self.assertEqual(self.evidence.command[0], _EXE)

    def test_command_includes_root_flag(self) -> None:
        self.assertIn("--root", self.evidence.command)

    def test_command_includes_root_path(self) -> None:
        cmd = list(self.evidence.command)
        idx = cmd.index("--root")
        self.assertEqual(cmd[idx + 1], str(_ROOT))

    def test_command_includes_doctor_verb(self) -> None:
        self.assertIn("doctor", self.evidence.command)

    def test_command_includes_last_op_flag(self) -> None:
        self.assertIn("--last-op", self.evidence.command)

    def test_command_is_tuple(self) -> None:
        self.assertIsInstance(self.evidence.command, tuple)

    def test_command_all_args_are_strings(self) -> None:
        for arg in self.evidence.command:
            self.assertIsInstance(arg, str)


# ---------------------------------------------------------------------------
# Custom executable
# ---------------------------------------------------------------------------

class CustomExecutableTests(unittest.TestCase):
    def test_custom_executable_in_command(self) -> None:
        runner = _FakeRunner()
        evidence = capture_doctor_last_op_evidence(_ROOT, runner, executable="llloom-dev")
        self.assertEqual(evidence.command[0], "llloom-dev")

    def test_default_executable_is_llloom(self) -> None:
        runner = _FakeRunner()
        evidence = capture_doctor_last_op_evidence(_ROOT, runner)
        self.assertEqual(evidence.command[0], "llloom")


# ---------------------------------------------------------------------------
# No mutating verbs
# ---------------------------------------------------------------------------

class NoMutatingVerbsTests(unittest.TestCase):
    def test_no_mutating_verbs_in_command(self) -> None:
        runner = _FakeRunner()
        evidence = capture_doctor_last_op_evidence(_ROOT, runner)
        lowered = {arg.lower() for arg in evidence.command}
        found = lowered & _MUTATING_VERBS
        self.assertFalse(found, f"mutating verb(s) {found} in command")


# ---------------------------------------------------------------------------
# Runner invocation
# ---------------------------------------------------------------------------

class RunnerInvocationTests(unittest.TestCase):
    def test_runner_is_invoked_exactly_once(self) -> None:
        runner = _FakeRunner()
        capture_doctor_last_op_evidence(_ROOT, runner)
        self.assertEqual(runner.call_count, 1)

    def test_runner_receives_expected_command(self) -> None:
        runner = _FakeRunner()
        capture_doctor_last_op_evidence(_ROOT, runner)
        self.assertEqual(runner.calls[0], _EXPECTED_COMMAND)


# ---------------------------------------------------------------------------
# Successful capture
# ---------------------------------------------------------------------------

class SuccessfulCaptureTests(unittest.TestCase):
    def test_ok_is_true_on_success(self) -> None:
        evidence = capture_doctor_last_op_evidence(_ROOT, _FakeRunner())
        self.assertTrue(evidence.ok)

    def test_returncode_is_zero_on_success(self) -> None:
        evidence = capture_doctor_last_op_evidence(_ROOT, _FakeRunner())
        self.assertEqual(evidence.returncode, 0)

    def test_stdout_summary_comes_from_stdout(self) -> None:
        runner = _FakeRunner(stdout="Journal: op-001")
        evidence = capture_doctor_last_op_evidence(_ROOT, runner)
        self.assertIn("Journal", evidence.stdout_summary)

    def test_stdout_summary_takes_first_line_only(self) -> None:
        runner = _FakeRunner(stdout="line one\nline two\nline three")
        evidence = capture_doctor_last_op_evidence(_ROOT, runner)
        self.assertIn("line one", evidence.stdout_summary)
        self.assertNotIn("line two", evidence.stdout_summary)

    def test_empty_stdout_produces_empty_summary(self) -> None:
        runner = _FakeRunner(stdout="")
        evidence = capture_doctor_last_op_evidence(_ROOT, runner)
        self.assertEqual(evidence.stdout_summary, "")

    def test_whitespace_only_stdout_produces_empty_summary(self) -> None:
        runner = _FakeRunner(stdout="   \n   ")
        evidence = capture_doctor_last_op_evidence(_ROOT, runner)
        self.assertEqual(evidence.stdout_summary, "")

    def test_stdout_summary_is_bounded(self) -> None:
        runner = _FakeRunner(stdout="x" * 500)
        evidence = capture_doctor_last_op_evidence(_ROOT, runner)
        self.assertLessEqual(len(evidence.stdout_summary), 200)


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------

class FailureHandlingTests(unittest.TestCase):
    def test_nonzero_exit_does_not_raise(self) -> None:
        runner = _FakeRunner(returncode=1, stdout="", stderr="doctor failed")
        try:
            capture_doctor_last_op_evidence(_ROOT, runner)
        except Exception as exc:
            self.fail(f"capture raised: {exc}")

    def test_nonzero_exit_ok_is_false(self) -> None:
        runner = _FakeRunner(returncode=1, stdout="")
        evidence = capture_doctor_last_op_evidence(_ROOT, runner)
        self.assertFalse(evidence.ok)

    def test_nonzero_exit_returncode_preserved(self) -> None:
        runner = _FakeRunner(returncode=2)
        evidence = capture_doctor_last_op_evidence(_ROOT, runner)
        self.assertEqual(evidence.returncode, 2)

    def test_launcher_failure_does_not_raise(self) -> None:
        runner = _FakeRunner(error="executable not found: llloom")
        try:
            capture_doctor_last_op_evidence(_ROOT, runner)
        except Exception as exc:
            self.fail(f"capture raised: {exc}")

    def test_launcher_failure_ok_is_false(self) -> None:
        runner = _FakeRunner(error="executable not found: llloom")
        evidence = capture_doctor_last_op_evidence(_ROOT, runner)
        self.assertFalse(evidence.ok)

    def test_launcher_failure_returncode_is_none(self) -> None:
        runner = _FakeRunner(error="executable not found: llloom")
        evidence = capture_doctor_last_op_evidence(_ROOT, runner)
        self.assertIsNone(evidence.returncode)

    def test_stderr_summary_from_stderr_on_failure(self) -> None:
        runner = _FakeRunner(returncode=1, stderr="workspace locked")
        evidence = capture_doctor_last_op_evidence(_ROOT, runner)
        self.assertIn("workspace locked", evidence.stderr_summary)

    def test_error_message_in_stderr_summary_on_launcher_failure(self) -> None:
        runner = _FakeRunner(error="executable not found: llloom")
        evidence = capture_doctor_last_op_evidence(_ROOT, runner)
        self.assertIn("executable not found", evidence.stderr_summary)

    def test_stderr_summary_is_bounded(self) -> None:
        runner = _FakeRunner(returncode=1, stderr="e" * 500)
        evidence = capture_doctor_last_op_evidence(_ROOT, runner)
        self.assertLessEqual(len(evidence.stderr_summary), 200)


# ---------------------------------------------------------------------------
# to_dict() serialization
# ---------------------------------------------------------------------------

class ToDictTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence = capture_doctor_last_op_evidence(_ROOT, _FakeRunner())
        self.d = self.evidence.to_dict()

    def test_has_command_key(self) -> None:
        self.assertIn("command", self.d)

    def test_has_returncode_key(self) -> None:
        self.assertIn("returncode", self.d)

    def test_has_ok_key(self) -> None:
        self.assertIn("ok", self.d)

    def test_has_stdout_summary_key(self) -> None:
        self.assertIn("stdout_summary", self.d)

    def test_has_stderr_summary_key(self) -> None:
        self.assertIn("stderr_summary", self.d)

    def test_command_is_list(self) -> None:
        self.assertIsInstance(self.d["command"], list)

    def test_returncode_is_int_or_none(self) -> None:
        v = self.d["returncode"]
        self.assertTrue(isinstance(v, int) or v is None)

    def test_ok_is_bool(self) -> None:
        self.assertIsInstance(self.d["ok"], bool)

    def test_stdout_summary_is_str(self) -> None:
        self.assertIsInstance(self.d["stdout_summary"], str)

    def test_stderr_summary_is_str(self) -> None:
        self.assertIsInstance(self.d["stderr_summary"], str)

    def test_no_path_objects_in_values(self) -> None:
        for v in self.d.values():
            self.assertNotIsInstance(v, Path)

    def test_to_dict_is_json_serializable(self) -> None:
        json.dumps(self.d)


# ---------------------------------------------------------------------------
# Frozen / immutable
# ---------------------------------------------------------------------------

class FrozenTests(unittest.TestCase):
    def test_evidence_is_frozen(self) -> None:
        evidence = capture_doctor_last_op_evidence(_ROOT, _FakeRunner())
        with self.assertRaises((AttributeError, TypeError)):
            evidence.ok = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

class DeterminismTests(unittest.TestCase):
    def test_repeated_calls_with_same_runner_result_are_consistent(self) -> None:
        e1 = capture_doctor_last_op_evidence(_ROOT, _FakeRunner())
        e2 = capture_doctor_last_op_evidence(_ROOT, _FakeRunner())
        self.assertEqual(e1.command, e2.command)
        self.assertEqual(e1.ok, e2.ok)
        self.assertEqual(e1.stdout_summary, e2.stdout_summary)


# ---------------------------------------------------------------------------
# LlloomCliBackend.doctor_last_op()
# ---------------------------------------------------------------------------

class LlloomCliBackendDoctorLastOpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = _FakeRunner(stdout="Journal: op-001")
        self.backend = LlloomCliBackend(root=_ROOT, runner=self.runner)

    def test_doctor_last_op_returns_evidence(self) -> None:
        evidence = self.backend.doctor_last_op()
        self.assertIsInstance(evidence, DoctorLastOpEvidence)

    def test_doctor_last_op_uses_backend_runner(self) -> None:
        self.backend.doctor_last_op()
        self.assertEqual(self.runner.call_count, 1)

    def test_doctor_last_op_uses_backend_root(self) -> None:
        evidence = self.backend.doctor_last_op()
        cmd = list(evidence.command)
        idx = cmd.index("--root")
        self.assertEqual(cmd[idx + 1], str(_ROOT))

    def test_doctor_last_op_uses_backend_executable(self) -> None:
        runner = _FakeRunner()
        backend = LlloomCliBackend(root=_ROOT, executable="llloom-dev", runner=runner)
        evidence = backend.doctor_last_op()
        self.assertEqual(evidence.command[0], "llloom-dev")

    def test_doctor_last_op_command_includes_last_op_flag(self) -> None:
        evidence = self.backend.doctor_last_op()
        self.assertIn("--last-op", evidence.command)


if __name__ == "__main__":
    unittest.main()
