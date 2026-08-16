"""Tests for M003-S04: the accepted runner and layout policy boundary.

The selected layout's typed ``AutomationBoundaryPolicy`` governs non-dry-run
one-step automated execution: an unsupported posture (``runner_implemented:
false``) refuses before any writer or journal, and each currently mappable
``must_stop_on`` condition refuses from typed native state. Read-only
planning, dry-run previews, and all four manual writer families are outside
the runner gate. All policy decisions derive from the one already selected
``ProjectStatus``/``LoadedLayout`` snapshot.
"""

from __future__ import annotations

import json
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import yaml

import frutlups
import frutlups.orchestrator as orchestrator_module
from frutlups.cli import main
from frutlups.journal import journal_path_for, read_run_journal
from frutlups.layout import AutomationBoundaryPolicy
from frutlups.orchestrator import (
    _evaluate_runner_policy,
    _run_one_step_from_status,
    build_orchestrator_plan,
    run_one_step,
)
from frutlups.project import _loop_resume_with_verdict, build_status

from test_orchestrator import (
    _make_project,
    _make_record_verdict_project,
    _make_template,
)
from test_resumable_status import (
    _active_roadmap,
    _detailed_roadmap,
    _minimal_coding_prompt,
    _minimal_self_report,
    _write_active_roadmap,
    _write_coding_prompt,
    _write_detailed_roadmap,
    _write_review_prompt,
    _write_review_report,
    _write_self_report,
)


# The exact top-level keys of the `orchestrator-run --json` refusal payload:
# run-result keys plus the read-only resume and human-gate summaries. This
# shape is part of the frozen public surface.
_REFUSAL_JSON_KEYS = frozenset(
    {
        "plan",
        "dry_run",
        "attempted",
        "wrote",
        "artifact_path",
        "refused",
        "refusal_reason",
        "diagnostics",
        "resume",
        "human_gate",
    }
)

_POSTURE_REFUSAL = (
    "runner policy refused: runner_implemented is false; "
    "automated one-step execution is not authorized"
)

_EXTRACTABLE_SELF_REPORT = (
    "# Self-Report\n\n"
    "## Files Changed\n\n- 08_pkg/src/frutlups/project.py\n\n"
    "## Behavior Implemented\n\nThe behavior was implemented.\n\n"
    "## Tests Added or Updated\n\n- test_something\n\n"
    "## Verification Commands and Results\n\n```\npython -m unittest\n```\n\n"
    "## Live Status Summary\n\nPrompts: 1 coding, 0 review.\n\n"
    "## Known Limits and Intentional Deferrals\n\nNone.\n\n"
    "## Memory Usage Statement\n\nNo memory backend was queried or mutated.\n\n"
    "## Matching Review Prompt Path Created by the Coder\n\n"
    "prompts/for_review_agent/001_review_something.md\n\n"
    "## Blockers or Open Questions\n\nNone.\n"
)


def _policy_config(*, implemented: bool = True, must_stop_on=()) -> str:
    lines = [
        "schema_version: frutlups_layout_config_v0",
        "profile_id: artifact_first_template_legacy_root",
        "automation_boundary:",
        f"  runner_implemented: {'true' if implemented else 'false'}",
    ]
    if must_stop_on:
        lines.append("  must_stop_on:")
        lines.extend(f'    - "{value}"' for value in must_stop_on)
    return "\n".join(lines) + "\n"


def _write_policy(root: Path, *, implemented: bool = True, must_stop_on=()) -> None:
    (root / "frutlups.layout.yaml").write_text(
        _policy_config(implemented=implemented, must_stop_on=must_stop_on), encoding="utf-8"
    )


def _coding_step_project(root: Path, **policy) -> None:
    _make_project(root)
    _write_policy(root, **policy)


def _verdict_step_project(root: Path, verdict: str, **policy) -> None:
    _make_record_verdict_project(root)
    (root / "05_governance" / "reviews" / "m001_s01_first_slice_review_report.md").write_text(
        f"# Review\n\n## Verdict\n\n{verdict}\n", encoding="utf-8"
    )
    _write_policy(root, **policy)


def _no_frontier_project(root: Path, **policy) -> None:
    _make_template(root)
    _write_policy(root, **policy)


def _review_prompt_step_project(root: Path, **policy) -> None:
    """A project whose genuine typed loop step is write_review_prompt."""
    _make_template(root)
    _write_active_roadmap(root, _active_roadmap())
    _write_detailed_roadmap(root, _detailed_roadmap())
    _write_coding_prompt(root, "001_frutlups_m001_s01_first_slice.md", _minimal_coding_prompt(1))
    _write_self_report(
        root,
        "05_governance/reviews/m001_s01_first_slice_self_report.md",
        _EXTRACTABLE_SELF_REPORT,
    )
    _write_policy(root, **policy)


def _invalid_self_report_project(root: Path, **policy) -> None:
    _make_template(root)
    _write_active_roadmap(root, _active_roadmap())
    _write_detailed_roadmap(root, _detailed_roadmap())
    _write_coding_prompt(root, "001_frutlups_m001_s01_first_slice.md", _minimal_coding_prompt(1))
    _write_self_report(
        root,
        "05_governance/reviews/m001_s01_first_slice_self_report.md",
        "# Self-Report\n\nmissing the required sections\n",
    )
    _write_policy(root, **policy)


def _invalid_review_report_project(root: Path, **policy) -> None:
    _make_template(root)
    _write_active_roadmap(root, _active_roadmap())
    _write_detailed_roadmap(root, _detailed_roadmap())
    _write_coding_prompt(root, "001_frutlups_m001_s01_first_slice.md", _minimal_coding_prompt(1))
    _write_self_report(root, "05_governance/reviews/m001_s01_first_slice_self_report.md")
    _write_review_prompt(root, "001_review_m001_s01_first_slice.md")
    (root / "05_governance" / "reviews" / "m001_s01_first_slice_review_report.md").write_text(
        "# Review\n\nNo verdict section here.\n", encoding="utf-8"
    )
    _write_policy(root, **policy)


def _snapshot(root: Path) -> dict[str, str]:
    import hashlib

    return {
        str(path.relative_to(root)): (
            "dir" if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest()
        )
        for path in sorted(root.rglob("*"))
    }


def _run(args: list[str]) -> tuple[int, str, str]:
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(args)
    return code, out.getvalue(), err.getvalue()


class PostureRefusalTests(unittest.TestCase):
    """runner_implemented: false refuses non-dry-run execution before all writes."""

    def test_direct_refusal_shape_and_no_journal(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _coding_step_project(root, implemented=False)
            before = _snapshot(root)
            with (
                mock.patch(
                    "frutlups.orchestrator.write_coding_prompt",
                    side_effect=AssertionError("writer reached"),
                ),
                mock.patch(
                    "frutlups.orchestrator.append_run_journal_entry",
                    side_effect=AssertionError("journal reached"),
                ),
            ):
                result = run_one_step(root, journal=True)
            self.assertTrue(result.refused)
            self.assertFalse(result.attempted)
            self.assertFalse(result.wrote)
            self.assertEqual(result.artifact_path, "")
            self.assertEqual(
                result.refusal_reason,
                "runner policy refused: runner_implemented is false; "
                "automated one-step execution is not authorized",
            )
            self.assertEqual(before, _snapshot(root))
            self.assertFalse(journal_path_for(root).exists())

    def test_cli_refusal_exit_2_text_and_json(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _coding_step_project(root, implemented=False)
            before = _snapshot(root)
            code, out, err = _run(["orchestrator-run", str(root)])
            self.assertEqual(code, 2)
            self.assertIn("runner policy refused", out)
            code, out, err = _run(["orchestrator-run", str(root), "--json"])
            self.assertEqual(code, 2)
            payload = json.loads(out)
            self.assertTrue(payload["refused"])
            self.assertIn("runner_implemented is false", payload["refusal_reason"])
            self.assertEqual(before, _snapshot(root))

    def test_dry_run_stays_advisory_under_false_posture(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _coding_step_project(root, implemented=False)
            result = run_one_step(root, dry_run=True, journal=True)
            self.assertFalse(result.refused)
            self.assertEqual(result.refusal_reason, "")
            self.assertIn("dry-run: advisory only", result.diagnostics[0])
            read = read_run_journal(journal_path_for(root))
            self.assertEqual(len(read.entries), 1)

    def test_supported_posture_executes_all_three_safe_steps(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _coding_step_project(root)
            result = run_one_step(root, journal=False)
            self.assertTrue(result.wrote, result.refusal_reason)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _review_prompt_step_project(root)
            result = run_one_step(root, journal=False)
            self.assertTrue(result.wrote, result.refusal_reason)
            self.assertTrue(
                list((root / "prompts" / "for_review_agent").glob("*.md"))
            )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _verdict_step_project(root, "pass")
            result = run_one_step(root, journal=False)
            self.assertTrue(result.wrote, result.refusal_reason)
            self.assertTrue(
                list((root / "05_governance" / "reviews").glob("*_verdict_record.md"))
            )
    def _assert_false_posture_matrix(self, root: Path, writer_attr: str) -> None:
        """Direct + CLI text + CLI JSON posture refusal for one safe-step state."""
        before = _snapshot(root)
        with (
            mock.patch(
                f"frutlups.orchestrator.{writer_attr}",
                side_effect=AssertionError("writer reached"),
            ),
            mock.patch(
                "frutlups.orchestrator.append_run_journal_entry",
                side_effect=AssertionError("journal reached"),
            ),
        ):
            result = run_one_step(root, journal=True)
            code_text, out_text, _ = _run(["orchestrator-run", str(root)])
            code_json, out_json, _ = _run(["orchestrator-run", str(root), "--json"])
        # Direct library form.
        self.assertTrue(result.refused)
        self.assertFalse(result.attempted)
        self.assertFalse(result.wrote)
        self.assertEqual(result.artifact_path, "")
        self.assertEqual(result.refusal_reason, _POSTURE_REFUSAL)
        # CLI text form.
        self.assertEqual(code_text, 2)
        self.assertIn("runner policy refused", out_text)
        # CLI JSON form: parseable, unchanged key set, same named refusal.
        self.assertEqual(code_json, 2)
        payload = json.loads(out_json)
        self.assertEqual(set(payload), _REFUSAL_JSON_KEYS)
        self.assertTrue(payload["refused"])
        self.assertEqual(payload["refusal_reason"], _POSTURE_REFUSAL)
        self.assertFalse(payload["attempted"])
        self.assertFalse(payload["wrote"])
        self.assertEqual(payload["artifact_path"], "")
        # No writer, no journal, byte-for-byte stable filesystem.
        self.assertFalse(journal_path_for(root).exists())
        self.assertEqual(before, _snapshot(root))

    def test_false_posture_matrix_coding_prompt_creation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _coding_step_project(root, implemented=False)
            self._assert_false_posture_matrix(root, "write_coding_prompt")

    def test_false_posture_matrix_review_prompt_creation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _review_prompt_step_project(root, implemented=False)
            self._assert_false_posture_matrix(root, "write_review_prompt")

    def test_false_posture_matrix_verdict_recording(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _verdict_step_project(root, "pass", implemented=False)
            self._assert_false_posture_matrix(root, "write_verdict_record")


class PlannerOrientationTests(unittest.TestCase):
    """Planner, gate, and dry-run stay available under both postures."""

    def test_plan_unchanged_by_posture_except_diagnostics(self) -> None:
        plans = []
        for implemented in (False, True):
            with TemporaryDirectory() as tmp:
                root = Path(tmp)
                _coding_step_project(root, implemented=implemented, must_stop_on=("no frontier",))
                plans.append(build_orchestrator_plan(root))
        false_plan, true_plan = plans
        self.assertEqual(false_plan.loop_step, true_plan.loop_step)
        self.assertEqual(false_plan.actor, true_plan.actor)
        self.assertEqual(false_plan.recommended_command, true_plan.recommended_command)
        self.assertEqual(false_plan.safe_for_auto_execution, true_plan.safe_for_auto_execution)
        self.assertTrue(false_plan.safe_for_auto_execution)
        self.assertNotEqual(false_plan.diagnostics, ())
        self.assertTrue(
            any("runner_implemented=false" in d for d in false_plan.diagnostics)
        )
        self.assertTrue(
            any("must_stop_on 'no frontier' declared" in d for d in false_plan.diagnostics)
        )

    def test_policy_diagnostics_bounded_and_owned(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _coding_step_project(root, must_stop_on=("no frontier",))
            plan = build_orchestrator_plan(root)
        for diagnostic in plan.diagnostics:
            self.assertLessEqual(len(diagnostic), 240)

    def test_status_and_next_unchanged_by_posture(self) -> None:
        key_sets = []
        for implemented in (False, True):
            with TemporaryDirectory() as tmp:
                root = Path(tmp)
                _coding_step_project(root, implemented=implemented)
                code, out, _ = _run(["status", str(root), "--json"])
                self.assertEqual(code, 0)
                payload = json.loads(out)
                code2, out2, _ = _run(["next", str(root), "--json"])
                self.assertEqual(code2, 0)
                next_payload = json.loads(out2)
                key_sets.append(
                    (set(payload), set(payload["layout"]["profile"]), set(next_payload))
                )
        self.assertEqual(key_sets[0], key_sets[1])


class UnsupportedIdentityDedupTests(unittest.TestCase):
    """F1: every normalized identity is de-duplicated before classification."""

    def _diagnostics(self, values: tuple) -> tuple[str, ...]:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _coding_step_project(root)
            resume, verdict = _loop_resume_with_verdict(build_status(root))
        policy = AutomationBoundaryPolicy(
            runner_implemented=True, must_stop_on=tuple(values)
        )
        return _evaluate_runner_policy(policy, resume, verdict, dry_run=False).diagnostics

    @staticmethod
    def _unsupported(diagnostics: tuple[str, ...]) -> list[str]:
        return [d for d in diagnostics if "not a supported canonical condition" in d]

    def test_review_examples_dedup_to_first_occurrence(self) -> None:
        # Review 025 example 1: ("bogus", "bogus") -> one position-1 diagnostic.
        diagnostics = self._diagnostics(("bogus", "bogus"))
        unsupported = self._unsupported(diagnostics)
        self.assertEqual(len(unsupported), 1)
        self.assertIn("position 1", unsupported[0])
        self.assertNotIn("bogus", unsupported[0])  # no value echo
        # Review 025 example 2: (" BOGUS ", "bogus") -> the same single
        # position-1 diagnostic after exact strip/lowercase normalization.
        self.assertEqual(diagnostics, self._diagnostics((" BOGUS ", "bogus")))

    def test_empty_and_non_string_identities_first_occurrence(self) -> None:
        for values in (("", ""), (None, None), (None, ""), ("", None)):
            with self.subTest(values=values):
                unsupported = self._unsupported(self._diagnostics(values))
                self.assertEqual(len(unsupported), 1)
                self.assertIn("position 1", unsupported[0])
                # no type, representation, or bytes echoed
                self.assertNotIn("None", unsupported[0])

    def test_distinct_unsupported_identities_own_ordinals(self) -> None:
        unsupported = self._unsupported(self._diagnostics(("alpha", "beta", "alpha")))
        self.assertEqual(len(unsupported), 2)
        self.assertIn("position 1", unsupported[0])
        self.assertIn("position 2", unsupported[1])

    def test_supported_and_mixed_dedup_retained(self) -> None:
        diagnostics = self._diagnostics(("no frontier", "bogus", "no frontier"))
        declared = [d for d in diagnostics if "'no frontier' declared" in d]
        self.assertEqual(len(declared), 1)
        unsupported = self._unsupported(diagnostics)
        self.assertEqual(len(unsupported), 1)
        self.assertIn("position 2", unsupported[0])


class StopConditionTests(unittest.TestCase):
    """Each mappable must_stop_on condition refuses from typed native state."""

    def _assert_stop(self, root: Path, canonical: str) -> None:
        before = _snapshot(root)
        journal_rel = str(journal_path_for(root).relative_to(root))
        with (
            mock.patch(
                "frutlups.orchestrator.write_coding_prompt",
                side_effect=AssertionError("writer reached"),
            ),
            mock.patch(
                "frutlups.orchestrator.write_review_prompt",
                side_effect=AssertionError("writer reached"),
            ),
            mock.patch(
                "frutlups.orchestrator.write_verdict_record",
                side_effect=AssertionError("writer reached"),
            ),
        ):
            result = run_one_step(root, journal=True)
        self.assertTrue(result.refused)
        self.assertFalse(result.attempted)
        self.assertFalse(result.wrote)
        self.assertEqual(
            result.refusal_reason,
            f"runner policy refused: must_stop_on condition '{canonical}' matched current state",
        )
        # No artifact write: the only new filesystem entry is the journal.
        after = _snapshot(root)
        self.assertEqual(
            {key for key in after if key not in before},
            {journal_rel, str(Path(journal_rel).parent)},
        )
        # A supported-posture policy stop journals exactly one bounded refusal.
        read = read_run_journal(journal_path_for(root))
        self.assertEqual(len(read.entries), 1)
        self.assertEqual(read.entries[0].event_kind, "refuse")

    def test_blocked_verdict_stops(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _verdict_step_project(root, "blocked", must_stop_on=("blocked",))
            self._assert_stop(root, "blocked")

    def test_override_verdict_stops(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _verdict_step_project(root, "override", must_stop_on=("override required",))
            self._assert_stop(root, "override required")

    def test_invalid_self_report_stops(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _invalid_self_report_project(root, must_stop_on=("invalid self-report",))
            self._assert_stop(root, "invalid self-report")

    def test_invalid_review_report_stops(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _invalid_review_report_project(root, must_stop_on=("invalid review report",))
            self._assert_stop(root, "invalid review report")

    def test_no_frontier_stops(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _no_frontier_project(root, must_stop_on=("no frontier",))
            self._assert_stop(root, "no frontier")

    def _assert_stop_cli_cell(self, root: Path, canonical: str, cli_form: str) -> None:
        """One independently observable CLI invocation for one stop condition.

        Each ``(canonical stop, CLI form)`` cell gets its own fresh project and
        runs exactly one invocation, so the single journal append is
        attributable to that invocation alone.
        """
        expected = (
            f"runner policy refused: must_stop_on condition '{canonical}' "
            "matched current state"
        )
        before = _snapshot(root)
        journal_rel = str(journal_path_for(root).relative_to(root))
        args = ["orchestrator-run", str(root)]
        if cli_form == "json":
            args.append("--json")
        with ExitStack() as stack:
            for attr in (
                "write_coding_prompt",
                "write_review_prompt",
                "write_verdict_record",
            ):
                stack.enter_context(
                    mock.patch(
                        f"frutlups.orchestrator.{attr}",
                        side_effect=AssertionError("writer reached"),
                    )
                )
            code, out, _ = _run(args)
        self.assertEqual(code, 2)
        if cli_form == "json":
            # CLI JSON form: parseable unchanged shape, canonical reason.
            payload = json.loads(out)
            self.assertEqual(set(payload), _REFUSAL_JSON_KEYS)
            self.assertTrue(payload["refused"])
            self.assertEqual(payload["refusal_reason"], expected)
            self.assertFalse(payload["attempted"])
            self.assertFalse(payload["wrote"])
            self.assertEqual(payload["artifact_path"], "")
        else:
            # CLI text form: same canonical reason.
            self.assertIn(expected, out)
        # Complete non-journal snapshot equality: remove only the expected
        # journal file, and its parent directory only when that directory did
        # not exist before and was created solely to hold the journal. Any
        # other addition, deletion, or byte change fails the comparison.
        after = _snapshot(root)
        self.assertIn(journal_rel, after)
        after.pop(journal_rel)
        parent_rel = str(Path(journal_rel).parent)
        if parent_rel not in before:
            after.pop(parent_rel)
        self.assertEqual(before, after)
        # Exactly one journal entry, attributable to this invocation, pinning
        # the exact bounded canonical refusal evidence. The entry is evidence
        # only: it creates no acceptance, resolution, completion, or artifact
        # (attempted/wrote false, empty artifact path, unchanged non-journal
        # filesystem above).
        read = read_run_journal(journal_path_for(root))
        self.assertEqual(len(read.entries), 1)
        entry = read.entries[0]
        self.assertEqual(entry.event_kind, "refuse")
        self.assertTrue(entry.refused)
        self.assertFalse(entry.attempted)
        self.assertFalse(entry.wrote)
        self.assertEqual(entry.artifact_path, "")
        self.assertEqual(entry.refusal_reason, expected)
        self.assertLessEqual(len(entry.refusal_reason), 240)
        for diagnostic in entry.diagnostics:
            self.assertLessEqual(len(diagnostic), 240)

    def test_blocked_verdict_stops_cli(self) -> None:
        for form in ("text", "json"):
            with self.subTest(form=form), TemporaryDirectory() as tmp:
                root = Path(tmp)
                _verdict_step_project(root, "blocked", must_stop_on=("blocked",))
                self._assert_stop_cli_cell(root, "blocked", form)

    def test_override_verdict_stops_cli(self) -> None:
        for form in ("text", "json"):
            with self.subTest(form=form), TemporaryDirectory() as tmp:
                root = Path(tmp)
                _verdict_step_project(root, "override", must_stop_on=("override required",))
                self._assert_stop_cli_cell(root, "override required", form)

    def test_invalid_self_report_stops_cli(self) -> None:
        for form in ("text", "json"):
            with self.subTest(form=form), TemporaryDirectory() as tmp:
                root = Path(tmp)
                _invalid_self_report_project(root, must_stop_on=("invalid self-report",))
                self._assert_stop_cli_cell(root, "invalid self-report", form)

    def test_invalid_review_report_stops_cli(self) -> None:
        for form in ("text", "json"):
            with self.subTest(form=form), TemporaryDirectory() as tmp:
                root = Path(tmp)
                _invalid_review_report_project(root, must_stop_on=("invalid review report",))
                self._assert_stop_cli_cell(root, "invalid review report", form)

    def test_no_frontier_stops_cli(self) -> None:
        for form in ("text", "json"):
            with self.subTest(form=form), TemporaryDirectory() as tmp:
                root = Path(tmp)
                _no_frontier_project(root, must_stop_on=("no frontier",))
                self._assert_stop_cli_cell(root, "no frontier", form)

    def test_unconfigured_condition_adds_no_stop(self) -> None:
        # no-frontier project WITHOUT 'no frontier' configured: the generic
        # not-safe refusal fires, not the policy refusal.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _no_frontier_project(root)
            result = run_one_step(root, journal=False)
        self.assertTrue(result.refused)
        self.assertIn("not safe for automatic local execution", result.refusal_reason)

    def test_pass_and_needs_work_verdicts_do_not_match(self) -> None:
        for verdict in ("pass", "needs_work"):
            for condition in ("blocked", "override required"):
                with self.subTest(verdict=verdict, condition=condition):
                    with TemporaryDirectory() as tmp:
                        root = Path(tmp)
                        _verdict_step_project(root, verdict, must_stop_on=(condition,))
                        result = run_one_step(root, journal=False)
                    self.assertTrue(result.wrote, result.refusal_reason)

    def test_memory_and_environment_gates_declared_non_applicable(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _coding_step_project(
                root, must_stop_on=("memory gate failure", "environment gate failure")
            )
            result = run_one_step(root, journal=False)
            self.assertTrue(result.wrote, result.refusal_reason)
            policy = result  # diagnostics live on the plan
            self.assertTrue(
                any(
                    "memory gate failure' declared but non-applicable" in d
                    for d in result.plan.diagnostics
                )
            )
            self.assertTrue(
                any(
                    "environment gate failure' declared but non-applicable" in d
                    for d in result.plan.diagnostics
                )
            )

    def test_unknown_values_duplicates_and_case_variants(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _coding_step_project(
                root,
                must_stop_on=("No Frontier", "no frontier", "bogus condition", "no frontier"),
            )
            result = run_one_step(root, journal=False)
            self.assertTrue(result.wrote, result.refusal_reason)
            diagnostics = result.plan.diagnostics
            self.assertTrue(any("position 3" in d for d in diagnostics))
            self.assertFalse(any("bogus" in d for d in diagnostics))
            # exact canonical match deduped to one declaration
            self.assertEqual(
                sum(1 for d in diagnostics if "'no frontier' declared" in d), 1
            )

    def test_first_match_in_configured_order_wins(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _no_frontier_project(
                root, must_stop_on=("no frontier", "invalid self-report")
            )
            result = run_one_step(root, journal=False)
            self.assertIn("'no frontier'", result.refusal_reason)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _no_frontier_project(
                root, must_stop_on=("invalid self-report", "no frontier")
            )
            result = run_one_step(root, journal=False)
            self.assertIn("'no frontier'", result.refusal_reason)

    def test_repeated_calls_pure(self) -> None:
        constructors_before = dict(yaml.SafeLoader.yaml_constructors)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _coding_step_project(root, implemented=False)
            before = _snapshot(root)
            first = run_one_step(root, journal=False)
            second = run_one_step(root, journal=False)
            self.assertEqual(first.refusal_reason, second.refusal_reason)
            self.assertEqual(before, _snapshot(root))
        self.assertEqual(dict(yaml.SafeLoader.yaml_constructors), constructors_before)


class PrecedenceTests(unittest.TestCase):
    """The five-step precedence order for one invocation."""

    def test_bad_layout_beats_false_posture_and_no_journal(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _coding_step_project(root, implemented=False)
            (root / "frutlups.layout.yaml").write_text('a: "unterminated\n', encoding="utf-8")
            before = _snapshot(root)
            result = run_one_step(root, journal=True)
            self.assertTrue(result.refusal_reason.startswith("layout mutation refused"))
            self.assertFalse(journal_path_for(root).exists())
            self.assertEqual(before, _snapshot(root))
            code, _, _ = _run(["orchestrator-run", str(root)])
            self.assertEqual(code, 2)

    def test_bad_layout_beats_matched_stop(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _no_frontier_project(root, must_stop_on=("no frontier",))
            (root / "frutlups.layout.yaml").write_text('a: "unterminated\n', encoding="utf-8")
            result = run_one_step(root, journal=True)
            self.assertTrue(result.refusal_reason.startswith("layout mutation refused"))

    def test_dry_run_never_policy_refused(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _no_frontier_project(root, implemented=False, must_stop_on=("no frontier",))
            result = run_one_step(root, dry_run=True, journal=True)
            self.assertFalse(result.refused)
            self.assertEqual(result.refusal_reason, "")
            read = read_run_journal(journal_path_for(root))
            self.assertEqual(len(read.entries), 1)

    def test_false_posture_beats_matched_stop(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _no_frontier_project(root, implemented=False, must_stop_on=("no frontier",))
            result = run_one_step(root, journal=False)
            self.assertIn("runner_implemented is false", result.refusal_reason)

    def test_generic_unsafe_plan_with_configured_and_unconfigured_stop(self) -> None:
        # A coder-only step (execute_coding_prompt) with a stop configured
        # that does not match: generic unsafe refusal, not the policy one.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            _write_active_roadmap(root, _active_roadmap())
            _write_detailed_roadmap(root, _detailed_roadmap())
            _write_coding_prompt(
                root, "001_frutlups_m001_s01_first_slice.md", _minimal_coding_prompt(1)
            )
            _write_policy(root, must_stop_on=("blocked",))
            result = run_one_step(root, journal=False)
            self.assertIn("not safe for automatic local execution", result.refusal_reason)


class JournalContractTests(unittest.TestCase):
    def test_append_failure_surfaces_diagnostic(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _no_frontier_project(root, must_stop_on=("no frontier",))
            with mock.patch(
                "frutlups.orchestrator.append_run_journal_entry", return_value=False
            ):
                result = run_one_step(root, journal=True)
            self.assertTrue(result.refused)
            self.assertIn("run journal append failed", result.diagnostics)


class SingleSelectionTests(unittest.TestCase):
    def test_direct_and_cli_load_counts(self) -> None:
        import frutlups.layout as layout_module

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _coding_step_project(root)
            with mock.patch(
                "frutlups.project.load_layout_profile",
                side_effect=layout_module.load_layout_profile,
            ) as spy:
                run_one_step(root, journal=False)
            self.assertEqual(spy.call_count, 1)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _coding_step_project(root)
            with mock.patch(
                "frutlups.project.load_layout_profile",
                side_effect=layout_module.load_layout_profile,
            ) as spy:
                _run(["orchestrator-run", str(root)])
            # Prompt 031: the journal-resume summary reuses the command's one
            # selected status/resume, so the whole composition selects once.
            self.assertEqual(spy.call_count, 1)

    def test_config_change_after_first_load_cannot_alter_posture(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _coding_step_project(root)  # supported posture
            status = build_status(root)
            _write_policy(root, implemented=False)  # on-disk config now refuses
            result, _policy = _run_one_step_from_status(status, journal=False)
            self.assertTrue(result.wrote, result.refusal_reason)
            fresh = run_one_step(root, journal=False)
            self.assertTrue(fresh.refused)


class ManualWriterExclusionTests(unittest.TestCase):
    """Manual writers never consult the runner policy."""

    def test_manual_make_review_prompt_outside_runner_policy(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _review_prompt_step_project(root, implemented=False)
            with mock.patch(
                "frutlups.orchestrator._evaluate_runner_policy",
                side_effect=AssertionError("runner policy consulted by manual writer"),
            ):
                code, _, _ = _run(["make-review-prompt", str(root)])
            self.assertEqual(code, 0)
            self.assertTrue(list((root / "prompts" / "for_review_agent").glob("*.md")))

    def test_manual_record_verdict_outside_runner_policy(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            _write_detailed_roadmap(
                root,
                _detailed_roadmap(
                    slices=[("M001-S01", "first slice"), ("M001-S02", "second slice")]
                ),
            )
            _write_review_report(root, "m001_s01_first_slice_review_report.md", "pass")
            _write_policy(root, implemented=False)
            report = root / "05_governance" / "reviews" / "m001_s01_first_slice_review_report.md"
            with mock.patch(
                "frutlups.orchestrator._evaluate_runner_policy",
                side_effect=AssertionError("runner policy consulted by manual writer"),
            ):
                code, _, _ = _run(
                    ["record-verdict", str(root), "--review-report", str(report)]
                )
            self.assertEqual(code, 0)
            self.assertTrue(
                list((root / "05_governance" / "reviews").glob("*_verdict_record.md"))
            )

    def test_manual_writes_succeed_under_false_posture(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _coding_step_project(root, implemented=False)
            with mock.patch(
                "frutlups.orchestrator._evaluate_runner_policy",
                side_effect=AssertionError("runner policy consulted by manual writer"),
            ):
                code, _, _ = _run(["make-coding-prompt", str(root)])
            self.assertEqual(code, 0)
            self.assertTrue(list((root / "prompts" / "for_coding_agent").glob("*.md")))

    def test_handoff_write_outside_runner_policy(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _coding_step_project(root, implemented=False)
            with mock.patch(
                "frutlups.orchestrator._evaluate_runner_policy",
                side_effect=AssertionError("runner policy consulted by manual writer"),
            ):
                code, _, _ = _run(["orchestrator-handoff", str(root), "--write"])
            self.assertEqual(code, 0)
            self.assertTrue(
                (root / "05_governance" / "orchestrator" / "m016_final_handoff.md").is_file()
            )


class PublicSurfaceTests(unittest.TestCase):
    def test_exports_verbs_and_no_new_symbols(self) -> None:
        # 134 M003-S04/S05 baseline exports plus the eight M003-S06
        # planning-frontier exports approved and enumerated in
        # 02_analysis/m003_planning_frontier_status_compatibility_record.md.
        self.assertEqual(len(frutlups.__all__), 152)
        for name in ("_evaluate_runner_policy", "_RunnerPolicyEvaluation", "_loop_resume_with_verdict"):
            self.assertFalse(hasattr(frutlups, name), name)
        import argparse

        from frutlups.cli import _build_parser

        subparsers = next(
            action
            for action in _build_parser()._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        self.assertEqual(len(subparsers.choices), 9)


if __name__ == "__main__":
    unittest.main()
