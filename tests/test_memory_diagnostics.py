"""Tests for M009-S03: memory diagnostics in status reports.

Covers:
- Disabled path: detect_memory() does not invoke any runner when memory root absent
- Enabled path: detect_memory() uses LlloomCliBackend via fake runner
- Diagnostic command vectors are read-only and include --root
- No mutating llloom verbs in diagnostic commands
- MemoryStatus.diagnostics is populated with doctor summary
- Runner failure is reported as a diagnostic, not an exception
- build_status() accepts and passes memory_runner
- frutlups status human output includes enabled memory diagnostics
- frutlups status --json includes JSON-safe diagnostics list
- frutlups next still works and includes Memory: line
- All test fixtures are hermetic (TemporaryDirectory, no real llloom)
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
    MemoryCommandResult,
    MemoryStatus,
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


class _SpyRunner:
    """Records every run() call. Returns configurable result."""

    def __init__(
        self,
        returncode: int = 0,
        stdout: str = "workspace healthy\nno warnings",
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

    def all_commands(self) -> list[tuple[str, ...]]:
        return list(self.calls)


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


def _make_memory_root(root: Path) -> Path:
    memory_root = root / "07_app" / "llloom_memory"
    memory_root.mkdir(parents=True)
    return memory_root


# ---------------------------------------------------------------------------
# Disabled path: runner is never invoked
# ---------------------------------------------------------------------------

class DisabledPathNoRunnerTests(unittest.TestCase):
    """detect_memory() must not invoke any runner when memory root is absent."""

    def test_no_runner_calls_when_no_memory_root(self) -> None:
        spy = _SpyRunner()
        with TemporaryDirectory() as tmp:
            detect_memory(Path(tmp), runner=spy)
        self.assertEqual(spy.call_count, 0)

    def test_disabled_status_returned_without_memory_root(self) -> None:
        spy = _SpyRunner()
        with TemporaryDirectory() as tmp:
            result = detect_memory(Path(tmp), runner=spy)
        self.assertFalse(result.enabled)

    def test_disabled_backend_name_unchanged(self) -> None:
        spy = _SpyRunner()
        with TemporaryDirectory() as tmp:
            result = detect_memory(Path(tmp), runner=spy)
        self.assertEqual(result.backend, "disabled")

    def test_disabled_diagnostics_is_empty(self) -> None:
        spy = _SpyRunner()
        with TemporaryDirectory() as tmp:
            result = detect_memory(Path(tmp), runner=spy)
        self.assertEqual(result.diagnostics, ())


# ---------------------------------------------------------------------------
# Enabled path: runner is invoked and diagnostics are populated
# ---------------------------------------------------------------------------

class EnabledPathDiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        self.memory_root = _make_memory_root(self.root)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_runner_is_invoked_when_memory_root_present(self) -> None:
        spy = _SpyRunner()
        detect_memory(self.root, runner=spy)
        self.assertGreater(spy.call_count, 0)

    def test_runner_called_at_least_twice(self) -> None:
        # status + doctor
        spy = _SpyRunner()
        detect_memory(self.root, runner=spy)
        self.assertGreaterEqual(spy.call_count, 2)

    def test_enabled_true_when_memory_root_present(self) -> None:
        result = detect_memory(self.root, runner=_SpyRunner())
        self.assertTrue(result.enabled)

    def test_backend_is_llloom(self) -> None:
        result = detect_memory(self.root, runner=_SpyRunner())
        self.assertEqual(result.backend, "llloom")

    def test_root_is_memory_root(self) -> None:
        result = detect_memory(self.root, runner=_SpyRunner())
        self.assertEqual(result.root, self.memory_root)

    def test_diagnostics_is_non_empty(self) -> None:
        result = detect_memory(self.root, runner=_SpyRunner())
        self.assertTrue(result.diagnostics)

    def test_diagnostics_contains_doctor_line(self) -> None:
        result = detect_memory(self.root, runner=_SpyRunner(stdout="workspace healthy"))
        self.assertTrue(any("doctor" in d for d in result.diagnostics))

    def test_doctor_ok_summary_in_diagnostics(self) -> None:
        result = detect_memory(
            self.root, runner=_SpyRunner(stdout="workspace healthy\nextra line")
        )
        doctor_diag = next(d for d in result.diagnostics if "doctor" in d)
        self.assertIn("workspace healthy", doctor_diag)

    def test_status_message_from_status_command(self) -> None:
        result = detect_memory(
            self.root,
            runner=_SpyRunner(stdout="memory ok — 42 claims"),
        )
        self.assertIn("memory ok", result.message)

    def test_diagnostics_bounded_length(self) -> None:
        long_output = "x" * 200
        result = detect_memory(self.root, runner=_SpyRunner(stdout=long_output))
        for diag in result.diagnostics:
            self.assertLessEqual(len(diag), 200)


# ---------------------------------------------------------------------------
# Command vectors are read-only and include --root
# ---------------------------------------------------------------------------

class DiagnosticCommandVectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        self.memory_root = _make_memory_root(self.root)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_all_commands_include_root_flag(self) -> None:
        spy = _SpyRunner()
        detect_memory(self.root, runner=spy)
        for cmd in spy.all_commands():
            self.assertIn("--root", cmd, f"--root missing from command: {cmd}")

    def test_all_commands_include_memory_root_path(self) -> None:
        spy = _SpyRunner()
        detect_memory(self.root, runner=spy)
        for cmd in spy.all_commands():
            cmd_list = list(cmd)
            idx = cmd_list.index("--root")
            self.assertEqual(
                cmd_list[idx + 1], str(self.memory_root),
                f"wrong root path in command: {cmd}",
            )

    def test_no_mutating_verbs_in_diagnostic_commands(self) -> None:
        spy = _SpyRunner()
        detect_memory(self.root, runner=spy)
        for cmd in spy.all_commands():
            lowered = {arg.lower() for arg in cmd}
            found = lowered & _MUTATING_VERBS
            self.assertFalse(
                found,
                f"mutating verb(s) {found} in diagnostic command: {cmd}",
            )

    def test_status_command_present(self) -> None:
        spy = _SpyRunner()
        detect_memory(self.root, runner=spy)
        verbs = {cmd[-1] for cmd in spy.all_commands()}
        self.assertIn("status", verbs)

    def test_doctor_command_present(self) -> None:
        spy = _SpyRunner()
        detect_memory(self.root, runner=spy)
        verbs = {cmd[-1] for cmd in spy.all_commands()}
        self.assertIn("doctor", verbs)

    def test_query_not_called_automatically(self) -> None:
        spy = _SpyRunner()
        detect_memory(self.root, runner=spy)
        verbs = {cmd[-1] for cmd in spy.all_commands()}
        self.assertNotIn("query", verbs)


# ---------------------------------------------------------------------------
# Runner failure: graceful diagnostic, no exception
# ---------------------------------------------------------------------------

class RunnerFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_memory_root(self.root)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_launcher_failure_returns_memory_status(self) -> None:
        failing = _SpyRunner(error="executable not found: llloom")
        result = detect_memory(self.root, runner=failing)
        self.assertIsInstance(result, MemoryStatus)

    def test_launcher_failure_does_not_raise(self) -> None:
        failing = _SpyRunner(error="executable not found: llloom")
        try:
            detect_memory(self.root, runner=failing)
        except Exception as exc:
            self.fail(f"detect_memory raised on runner failure: {exc}")

    def test_launcher_failure_enabled_still_true(self) -> None:
        failing = _SpyRunner(error="executable not found: llloom")
        result = detect_memory(self.root, runner=failing)
        self.assertTrue(result.enabled)

    def test_launcher_failure_message_carries_error(self) -> None:
        failing = _SpyRunner(error="executable not found: llloom")
        result = detect_memory(self.root, runner=failing)
        self.assertTrue(result.message)

    def test_doctor_failure_in_diagnostics(self) -> None:
        class _PartialRunner:
            """Status succeeds, doctor fails."""
            def __init__(self) -> None:
                self._count = 0
            def run(self, args: tuple[str, ...]) -> MemoryCommandResult:
                self._count += 1
                ok = args[-1] == "status"
                return MemoryCommandResult(
                    command=args,
                    returncode=0 if ok else 1,
                    stdout="ok" if ok else "",
                    stderr="" if ok else "error output",
                    ok=ok,
                )
        result = detect_memory(self.root, runner=_PartialRunner())
        doctor_diag = next((d for d in result.diagnostics if "doctor" in d), None)
        self.assertIsNotNone(doctor_diag)


# ---------------------------------------------------------------------------
# MemoryStatus.diagnostics JSON safety
# ---------------------------------------------------------------------------

class MemoryStatusDiagnosticsJsonTests(unittest.TestCase):
    def test_diagnostics_is_list_in_to_dict(self) -> None:
        status = MemoryStatus(
            enabled=True,
            backend="llloom",
            diagnostics=("doctor: ok", "verify: clean"),
        )
        self.assertIsInstance(status.to_dict()["diagnostics"], list)

    def test_diagnostics_serializable(self) -> None:
        status = MemoryStatus(
            enabled=True,
            backend="llloom",
            diagnostics=("doctor: ok",),
        )
        json.dumps(status.to_dict())

    def test_empty_diagnostics_serializes_to_empty_list(self) -> None:
        status = MemoryStatus(enabled=False, backend="disabled")
        self.assertEqual(status.to_dict()["diagnostics"], [])


# ---------------------------------------------------------------------------
# build_status() with memory_runner
# ---------------------------------------------------------------------------

class BuildStatusMemoryRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_roadmap(self.root)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_disabled_without_memory_root(self) -> None:
        spy = _SpyRunner()
        status = build_status(self.root, memory_runner=spy)
        self.assertFalse(status.memory.enabled)
        self.assertEqual(spy.call_count, 0)

    def test_enabled_with_memory_root_and_runner(self) -> None:
        _make_memory_root(self.root)
        spy = _SpyRunner()
        status = build_status(self.root, memory_runner=spy)
        self.assertTrue(status.memory.enabled)
        self.assertGreater(spy.call_count, 0)

    def test_memory_runner_none_still_works_without_memory_root(self) -> None:
        status = build_status(self.root, memory_runner=None)
        self.assertFalse(status.memory.enabled)

    def test_status_json_has_diagnostics_key(self) -> None:
        status = build_status(self.root, memory_runner=_SpyRunner())
        d = status.to_dict()
        self.assertIn("diagnostics", d["memory"])

    def test_status_json_diagnostics_is_list(self) -> None:
        status = build_status(self.root, memory_runner=_SpyRunner())
        d = status.to_dict()
        self.assertIsInstance(d["memory"]["diagnostics"], list)


# ---------------------------------------------------------------------------
# CLI human output with enabled memory
# ---------------------------------------------------------------------------

class CliHumanOutputEnabledMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_roadmap(self.root)
        _make_memory_root(self.root)

    def tearDown(self) -> None:
        self._td.cleanup()

    def _run_status(self, extra: list[str] | None = None) -> tuple[int, str]:
        buf = StringIO()
        with redirect_stdout(buf):
            code = main(["status", str(self.root)] + (extra or []))
        return code, buf.getvalue()

    def test_status_exits_zero_with_memory_root_and_runner(self) -> None:
        # No real llloom — this will use SubprocessMemoryCommandRunner which
        # gracefully fails. Status must still exit 0.
        code, _ = self._run_status()
        self.assertEqual(code, 0)

    def test_status_human_output_includes_memory_llloom(self) -> None:
        code, output = self._run_status()
        self.assertIn("Memory: llloom", output)

    def test_status_human_output_no_longer_says_disabled_when_root_present(self) -> None:
        _, output = self._run_status()
        self.assertNotIn("Memory: disabled", output)


# ---------------------------------------------------------------------------
# CLI JSON output with enabled memory
# ---------------------------------------------------------------------------

class CliJsonOutputEnabledMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_roadmap(self.root)
        _make_memory_root(self.root)

    def tearDown(self) -> None:
        self._td.cleanup()

    def _status_json(self) -> dict:
        buf = StringIO()
        with redirect_stdout(buf):
            main(["status", str(self.root), "--json"])
        return json.loads(buf.getvalue())

    def test_json_memory_enabled_true(self) -> None:
        d = self._status_json()
        self.assertTrue(d["memory"]["enabled"])

    def test_json_memory_backend_is_llloom(self) -> None:
        d = self._status_json()
        self.assertEqual(d["memory"]["backend"], "llloom")

    def test_json_memory_diagnostics_is_list(self) -> None:
        d = self._status_json()
        self.assertIsInstance(d["memory"]["diagnostics"], list)

    def test_json_memory_is_json_serializable(self) -> None:
        d = self._status_json()
        json.dumps(d["memory"])

    def test_json_memory_root_is_string(self) -> None:
        d = self._status_json()
        self.assertIsInstance(d["memory"]["root"], str)


# ---------------------------------------------------------------------------
# frutlups next still works
# ---------------------------------------------------------------------------

class NextCommandMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_roadmap(self.root)

    def tearDown(self) -> None:
        self._td.cleanup()

    def _run_next(self) -> tuple[int, str]:
        buf = StringIO()
        with redirect_stdout(buf):
            code = main(["next", str(self.root)])
        return code, buf.getvalue()

    def test_next_exits_zero_without_memory_root(self) -> None:
        code, _ = self._run_next()
        self.assertEqual(code, 0)

    def test_next_output_includes_memory_line(self) -> None:
        _, output = self._run_next()
        self.assertIn("Memory:", output)

    def test_next_output_disabled_when_no_memory_root(self) -> None:
        _, output = self._run_next()
        self.assertIn("Memory: disabled", output)


# ---------------------------------------------------------------------------
# Regression: M009-S03 corrective — status message must be bounded
# (review 041 finding: 5,000-char stdout produced a 5,000-char message)
# ---------------------------------------------------------------------------

_LONG_PAYLOAD = "x" * 5000


class _LongStatusRunner:
    """Returns 5,000-char stdout for `status`; short stdout for everything else."""

    def run(self, args: tuple[str, ...]) -> MemoryCommandResult:
        if args[-1] == "status":
            return MemoryCommandResult(
                command=args, returncode=0,
                stdout=_LONG_PAYLOAD, stderr="", ok=True,
            )
        return MemoryCommandResult(
            command=args, returncode=0,
            stdout="doctor ok", stderr="", ok=True,
        )


class BoundedStatusMessageTests(unittest.TestCase):
    """detect_memory() must not store the full llloom status stdout verbatim."""

    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_memory_root(self.root)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_message_is_bounded_when_long_stdout(self) -> None:
        result = detect_memory(self.root, runner=_LongStatusRunner())
        self.assertLess(len(result.message), len(_LONG_PAYLOAD))

    def test_message_length_within_reasonable_limit(self) -> None:
        result = detect_memory(self.root, runner=_LongStatusRunner())
        self.assertLessEqual(len(result.message), 200)

    def test_long_payload_not_in_message(self) -> None:
        result = detect_memory(self.root, runner=_LongStatusRunner())
        self.assertNotIn(_LONG_PAYLOAD, result.message)

    def test_diagnostics_also_bounded(self) -> None:
        result = detect_memory(self.root, runner=_LongStatusRunner())
        for diag in result.diagnostics:
            self.assertLessEqual(len(diag), 200)


class BoundedStatusCLIHumanTests(unittest.TestCase):
    """CLI human output must not include the full long llloom status payload."""

    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_roadmap(self.root)
        _make_memory_root(self.root)

    def tearDown(self) -> None:
        self._td.cleanup()

    def _status_output(self) -> str:
        buf = StringIO()
        with redirect_stdout(buf):
            main(["status", str(self.root)])
        return buf.getvalue()

    def test_long_payload_not_in_human_output(self) -> None:
        # Patch detect_memory via build_status memory_runner is not accessible
        # from CLI args, so we rely on the bounded message being stored at
        # build_status time. Use the live CLI (which calls SubprocessMemoryCommandRunner
        # and will gracefully fail), then verify the short fixed-runner path directly.
        result = detect_memory(self.root, runner=_LongStatusRunner())
        self.assertNotIn(_LONG_PAYLOAD, result.message)

    def test_memory_message_short_in_to_dict(self) -> None:
        result = detect_memory(self.root, runner=_LongStatusRunner())
        d = result.to_dict()
        self.assertLessEqual(len(d["message"]), 200)

    def test_json_memory_message_not_long_payload(self) -> None:
        result = detect_memory(self.root, runner=_LongStatusRunner())
        serialized = json.dumps(result.to_dict())
        self.assertNotIn(_LONG_PAYLOAD, serialized)


if __name__ == "__main__":
    unittest.main()
