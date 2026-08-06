"""Tests for M009-S04: prompt snippets from summarized memory evidence.

Covers:
- build_memory_prompt_snippet() with no memory root: no runner call, empty snippet
- build_memory_prompt_snippet() with memory root: query called, lines populated
- Query command vector includes --root, query, --status reviewed,
  --verification-status verified
- No mutating verbs in query command
- Query failure / empty output returns empty snippet without raising
- Snippet lines are bounded in length
- render_coding_prompt() with snippet includes "Optional Memory Context" section
- Rendered text states repository artifacts remain authoritative
- render_coding_prompt() without snippet (or empty snippet) omits the section
- build_coding_prompt_plan() with memory_runner passes snippet when memory root present
- Existing prompt-template, make-coding-prompt, and memory tests unaffected
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from frutlups.memory import (
    MemoryCommandResult,
    MemoryPromptSnippet,
    build_memory_prompt_snippet,
)
from frutlups.prompt_template import (
    CodingPromptTemplate,
    render_coding_prompt,
)
from frutlups.project import build_coding_prompt_plan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MUTATING_VERBS = frozenset({
    "seed", "apply", "ingest", "render", "supersede",
    "unlock", "reconcile", "rebuild",
})


class _SpyRunner:
    """Records every run() call; returns configurable result."""

    def __init__(
        self,
        returncode: int = 0,
        stdout: str = "claim: the architecture is event-driven\nclaim: uses typed dataclasses",
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


def _make_memory_root(root: Path) -> Path:
    memory_root = root / "07_app" / "llloom_memory"
    memory_root.mkdir(parents=True)
    return memory_root


def _valid_template(**overrides: object) -> CodingPromptTemplate:
    defaults: dict[str, object] = dict(
        sequence=10,
        milestone_id="M009",
        slice_id="M009-S04",
        slug="frutlups_m009_s04_prompt_snippets",
        title="prompt snippets from summarized memory evidence",
        role_instructions="You are the coding agent for `frutlups`.",
        required_reading=("CLAUDE.md", "README.md"),
        scope_paths=("08_pkg/",),
        non_goals=("do not mutate memory",),
        definition_of_done=("tests pass",),
        verification_commands=("python -m unittest discover -s tests",),
        self_report_path="05_governance/reviews/m009_s04_self_report.md",
    )
    defaults.update(overrides)
    return CodingPromptTemplate(**defaults)  # type: ignore[arg-type]


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


def _write_active_and_detailed_roadmap(root: Path) -> None:
    (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
        "# Active Roadmap\n\n"
        "### M001: Scaffold\n\nStatus: active\n\n"
        "### M002: Next\n\nStatus: planned\n\n",
        encoding="utf-8",
    )
    (root / "03_experiments" / "development_roadmap_frutlups.md").write_text(
        "# Detailed Roadmap\n\n"
        "### M001: Scaffold\n\nSlices:\n\n- M001-S01: initial scaffold\n\n"
        "### M002: Next\n\nSlices:\n\n- M002-S01: next thing\n\n",
        encoding="utf-8",
    )
    # Write a pass review report for M001-S01 so frontier advances to M002-S01
    (root / "05_governance" / "reviews" / "m001_s01_initial_scaffold_review_report.md").write_text(
        "# Review\n\n## Verdict\n\npass\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# build_memory_prompt_snippet(): disabled path
# ---------------------------------------------------------------------------

class SnippetDisabledPathTests(unittest.TestCase):
    def test_no_runner_calls_when_no_memory_root(self) -> None:
        spy = _SpyRunner()
        with TemporaryDirectory() as tmp:
            build_memory_prompt_snippet(Path(tmp), "roadmap", runner=spy)
        self.assertEqual(spy.call_count, 0)

    def test_empty_snippet_when_no_memory_root(self) -> None:
        with TemporaryDirectory() as tmp:
            result = build_memory_prompt_snippet(Path(tmp), "roadmap")
        self.assertFalse(result.has_content)
        self.assertEqual(result.lines, ())

    def test_query_preserved_in_empty_snippet(self) -> None:
        with TemporaryDirectory() as tmp:
            result = build_memory_prompt_snippet(Path(tmp), "my query")
        self.assertEqual(result.query, "my query")

    def test_to_dict_is_json_safe(self) -> None:
        snippet = MemoryPromptSnippet(lines=(), query="test")
        json.dumps(snippet.to_dict())


# ---------------------------------------------------------------------------
# build_memory_prompt_snippet(): enabled path
# ---------------------------------------------------------------------------

class SnippetEnabledPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        self.memory_root = _make_memory_root(self.root)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_runner_invoked_when_memory_root_present(self) -> None:
        spy = _SpyRunner()
        build_memory_prompt_snippet(self.root, "roadmap", runner=spy)
        self.assertGreater(spy.call_count, 0)

    def test_snippet_has_content_from_successful_query(self) -> None:
        result = build_memory_prompt_snippet(
            self.root, "architecture", runner=_SpyRunner()
        )
        self.assertTrue(result.has_content)

    def test_snippet_lines_come_from_stdout(self) -> None:
        runner = _SpyRunner(stdout="line one\nline two\nline three")
        result = build_memory_prompt_snippet(self.root, "q", runner=runner)
        self.assertGreater(len(result.lines), 0)
        self.assertIn("line one", result.lines)

    def test_snippet_lines_bounded_count(self) -> None:
        long_output = "\n".join(f"claim {i}" for i in range(20))
        runner = _SpyRunner(stdout=long_output)
        result = build_memory_prompt_snippet(self.root, "q", runner=runner)
        self.assertLessEqual(len(result.lines), 5)

    def test_snippet_lines_bounded_length(self) -> None:
        runner = _SpyRunner(stdout=("x" * 200 + "\n") * 3)
        result = build_memory_prompt_snippet(self.root, "q", runner=runner)
        for line in result.lines:
            self.assertLessEqual(len(line), 120)

    def test_to_dict_is_json_safe(self) -> None:
        result = build_memory_prompt_snippet(
            self.root, "q", runner=_SpyRunner()
        )
        json.dumps(result.to_dict())

    def test_to_dict_lines_is_list(self) -> None:
        result = build_memory_prompt_snippet(
            self.root, "q", runner=_SpyRunner()
        )
        self.assertIsInstance(result.to_dict()["lines"], list)


# ---------------------------------------------------------------------------
# Query command vector
# ---------------------------------------------------------------------------

class QueryCommandVectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        self.memory_root = _make_memory_root(self.root)
        self.spy = _SpyRunner()
        build_memory_prompt_snippet(self.root, "my question", runner=self.spy)

    def tearDown(self) -> None:
        self._td.cleanup()

    def _command(self) -> tuple[str, ...]:
        self.assertGreater(self.spy.call_count, 0)
        return self.spy.calls[0]

    def test_command_includes_root_flag(self) -> None:
        self.assertIn("--root", self._command())

    def test_command_includes_memory_root_path(self) -> None:
        cmd = list(self._command())
        idx = cmd.index("--root")
        self.assertEqual(cmd[idx + 1], str(self.memory_root))

    def test_command_includes_query_verb(self) -> None:
        self.assertIn("query", self._command())

    def test_command_includes_question(self) -> None:
        self.assertIn("my question", self._command())

    def test_command_includes_status_reviewed(self) -> None:
        cmd = list(self._command())
        idx = cmd.index("--status")
        self.assertEqual(cmd[idx + 1], "reviewed")

    def test_command_includes_verification_status_verified(self) -> None:
        cmd = list(self._command())
        idx = cmd.index("--verification-status")
        self.assertEqual(cmd[idx + 1], "verified")

    def test_no_mutating_verbs_in_command(self) -> None:
        lowered = {arg.lower() for arg in self._command()}
        found = lowered & _MUTATING_VERBS
        self.assertFalse(found, f"mutating verb(s) {found} in query command")


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------

class SnippetFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_memory_root(self.root)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_launcher_failure_returns_empty_snippet(self) -> None:
        failing = _SpyRunner(error="executable not found: llloom")
        result = build_memory_prompt_snippet(self.root, "q", runner=failing)
        self.assertFalse(result.has_content)

    def test_launcher_failure_does_not_raise(self) -> None:
        failing = _SpyRunner(error="executable not found: llloom")
        try:
            build_memory_prompt_snippet(self.root, "q", runner=failing)
        except Exception as exc:
            self.fail(f"build_memory_prompt_snippet raised: {exc}")

    def test_nonzero_returncode_returns_empty_snippet(self) -> None:
        runner = _SpyRunner(returncode=1, stdout="")
        result = build_memory_prompt_snippet(self.root, "q", runner=runner)
        self.assertFalse(result.has_content)

    def test_empty_stdout_returns_empty_snippet(self) -> None:
        runner = _SpyRunner(stdout="")
        result = build_memory_prompt_snippet(self.root, "q", runner=runner)
        self.assertFalse(result.has_content)

    def test_whitespace_only_stdout_returns_empty_snippet(self) -> None:
        runner = _SpyRunner(stdout="   \n   ")
        result = build_memory_prompt_snippet(self.root, "q", runner=runner)
        self.assertFalse(result.has_content)


# ---------------------------------------------------------------------------
# render_coding_prompt() with snippet
# ---------------------------------------------------------------------------

class RenderWithSnippetTests(unittest.TestCase):
    def _render(self, snippet: MemoryPromptSnippet | None = None) -> str:
        return render_coding_prompt(_valid_template(), snippet=snippet).content

    def test_snippet_section_present_when_has_content(self) -> None:
        snippet = MemoryPromptSnippet(
            lines=("claim: uses typed dataclasses",), query="q"
        )
        content = self._render(snippet=snippet)
        self.assertIn("Optional Memory Context", content)

    def test_snippet_lines_in_rendered_content(self) -> None:
        snippet = MemoryPromptSnippet(
            lines=("claim: uses typed dataclasses",), query="q"
        )
        content = self._render(snippet=snippet)
        self.assertIn("claim: uses typed dataclasses", content)

    def test_authoritative_statement_present(self) -> None:
        snippet = MemoryPromptSnippet(lines=("some evidence",), query="q")
        content = self._render(snippet=snippet)
        self.assertIn("authoritative", content.lower())

    def test_optional_context_labeled_as_optional(self) -> None:
        snippet = MemoryPromptSnippet(lines=("evidence",), query="q")
        content = self._render(snippet=snippet)
        self.assertIn("optional", content.lower())

    def test_no_snippet_section_when_snippet_is_none(self) -> None:
        content = self._render(snippet=None)
        self.assertNotIn("Optional Memory Context", content)

    def test_no_snippet_section_when_snippet_empty(self) -> None:
        snippet = MemoryPromptSnippet(lines=(), query="q")
        content = self._render(snippet=snippet)
        self.assertNotIn("Optional Memory Context", content)

    def test_existing_sections_still_present(self) -> None:
        snippet = MemoryPromptSnippet(lines=("evidence",), query="q")
        content = self._render(snippet=snippet)
        for section in (
            "## Role", "## Active Roadmap Item", "## Required Reading",
            "## Non-Goals", "## Definition of Done",
            "## llloom Integration Posture",
        ):
            self.assertIn(section, content, f"missing section: {section}")

    def test_render_valid_with_snippet(self) -> None:
        snippet = MemoryPromptSnippet(lines=("evidence",), query="q")
        result = render_coding_prompt(_valid_template(), snippet=snippet)
        self.assertTrue(result.valid)

    def test_render_valid_without_snippet(self) -> None:
        result = render_coding_prompt(_valid_template())
        self.assertTrue(result.valid)


# ---------------------------------------------------------------------------
# build_coding_prompt_plan() with memory_runner
# ---------------------------------------------------------------------------

class CodingPromptPlanMemoryRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_and_detailed_roadmap(self.root)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_no_runner_calls_without_memory_root(self) -> None:
        spy = _SpyRunner()
        build_coding_prompt_plan(self.root, memory_runner=spy)
        self.assertEqual(spy.call_count, 0)

    def test_runner_called_with_memory_root(self) -> None:
        _make_memory_root(self.root)
        spy = _SpyRunner()
        build_coding_prompt_plan(self.root, memory_runner=spy)
        self.assertGreater(spy.call_count, 0)

    def test_plan_valid_without_memory_root(self) -> None:
        plan = build_coding_prompt_plan(self.root, memory_runner=_SpyRunner())
        self.assertTrue(plan.valid)

    def test_plan_render_unchanged_without_memory_root(self) -> None:
        spy = _SpyRunner()
        plan = build_coding_prompt_plan(self.root, memory_runner=spy)
        self.assertIsNotNone(plan.render)
        self.assertNotIn("Optional Memory Context", plan.render.content)

    def test_plan_render_includes_snippet_with_memory_root(self) -> None:
        _make_memory_root(self.root)
        spy = _SpyRunner(stdout="claim: typed dataclasses used throughout")
        plan = build_coding_prompt_plan(self.root, memory_runner=spy)
        self.assertIsNotNone(plan.render)
        self.assertIn("Optional Memory Context", plan.render.content)

    def test_plan_render_omits_snippet_on_empty_query_result(self) -> None:
        _make_memory_root(self.root)
        spy = _SpyRunner(stdout="")
        plan = build_coding_prompt_plan(self.root, memory_runner=spy)
        self.assertIsNotNone(plan.render)
        self.assertNotIn("Optional Memory Context", plan.render.content)

    def test_memory_runner_none_still_produces_valid_plan(self) -> None:
        plan = build_coding_prompt_plan(self.root, memory_runner=None)
        self.assertTrue(plan.valid)


# ---------------------------------------------------------------------------
# Regression: M009-S04 corrective — injected runner must cover ALL memory
# commands including status and doctor, not just the snippet query.
# (review 043 finding: build_status() used SubprocessMemoryCommandRunner
#  even when memory_runner was supplied to build_coding_prompt_plan())
# ---------------------------------------------------------------------------

class RunnerInjectionRegressionTests(unittest.TestCase):
    """build_coding_prompt_plan(memory_runner=spy) must route all memory
    commands through the injected runner when a memory root is present."""

    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_template(self.root)
        _write_active_and_detailed_roadmap(self.root)
        _make_memory_root(self.root)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_all_memory_commands_use_injected_runner(self) -> None:
        spy = _SpyRunner(stdout="claim: typed dataclasses used throughout")
        build_coding_prompt_plan(self.root, memory_runner=spy)
        # Should see status + doctor (from build_status) + query (from snippet)
        self.assertGreaterEqual(spy.call_count, 3)

    def test_injected_runner_receives_status_command(self) -> None:
        spy = _SpyRunner()
        build_coding_prompt_plan(self.root, memory_runner=spy)
        all_args = {arg for cmd in spy.calls for arg in cmd}
        self.assertIn("status", all_args)

    def test_injected_runner_receives_doctor_command(self) -> None:
        spy = _SpyRunner()
        build_coding_prompt_plan(self.root, memory_runner=spy)
        all_args = {arg for cmd in spy.calls for arg in cmd}
        self.assertIn("doctor", all_args)

    def test_injected_runner_receives_query_command(self) -> None:
        spy = _SpyRunner(stdout="claim: evidence line")
        build_coding_prompt_plan(self.root, memory_runner=spy)
        all_args = {arg for cmd in spy.calls for arg in cmd}
        self.assertIn("query", all_args)

    def test_plan_valid_with_injected_runner(self) -> None:
        spy = _SpyRunner(stdout="claim: typed dataclasses used throughout")
        plan = build_coding_prompt_plan(self.root, memory_runner=spy)
        self.assertTrue(plan.valid)

    def test_plan_includes_snippet_via_injected_runner(self) -> None:
        spy = _SpyRunner(stdout="claim: typed dataclasses used throughout")
        plan = build_coding_prompt_plan(self.root, memory_runner=spy)
        self.assertIn("Optional Memory Context", plan.render.content)

    def test_no_runner_calls_without_memory_root(self) -> None:
        # Remove memory root, confirm spy never called
        import shutil
        shutil.rmtree(str(self.root / "07_app"), ignore_errors=True)
        spy = _SpyRunner()
        build_coding_prompt_plan(self.root, memory_runner=spy)
        self.assertEqual(spy.call_count, 0)


if __name__ == "__main__":
    unittest.main()
