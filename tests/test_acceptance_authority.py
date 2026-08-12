"""Tests for M003-S05: review-report and verdict-record acceptance rule.

Decision 5: the canonically parsed review report is the sole acceptance
authority; a verdict record is only a durable receipt paired to the report it
actually cites. A record whose corresponding review report is missing or does
not canonically parse to ``pass`` is contradictory durable state and fails
closed as ``fix_review_report`` ahead of frontier, runner, gate, writer, and
completion behavior. All states here are genuine typed native states; no truth
is manufactured from command text, diagnostics, paths, or filenames.
"""

from __future__ import annotations

import json
import unittest
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import frutlups
from frutlups.gate import HumanGateState, build_human_gate
from frutlups.journal import journal_path_for, read_run_journal
from frutlups.layout import legacy_profile
from frutlups.orchestrator import build_orchestrator_plan, run_one_step
from frutlups.project import (
    LoopResumeStep,
    _AuthorityDefectKind,
    _collect_acceptance_evidence,
    _RecordContradictionKind,
    build_loop_resume_status,
    build_status,
)
from frutlups.review_report import ReviewVerdict
from frutlups.state import NextActionKind, compute_next_action_from_verdict

from test_orchestrator import _make_template
from test_resumable_status import (
    _active_roadmap,
    _detailed_roadmap,
    _minimal_coding_prompt,
    _write_active_roadmap,
    _write_coding_prompt,
    _write_detailed_roadmap,
    _write_review_prompt,
    _write_review_report,
    _write_self_report,
)
from test_runner_policy import _run, _snapshot


RR = "05_governance/reviews/m001_s01_first_slice_review_report.md"
VR = "05_governance/reviews/m001_s01_first_slice_verdict_record.md"
REVIEWS = ("05_governance", "reviews")

_SUPPORTED_POSTURE = (
    "schema_version: frutlups_layout_config_v0\n"
    "profile_id: artifact_first_template_legacy_root\n"
    "automation_boundary:\n"
    "  runner_implemented: true\n"
)


def _in_flight_project(root: Path) -> None:
    """Full M001-S01 chain through the review prompt; report/record vary."""
    _make_template(root)
    _write_active_roadmap(root, _active_roadmap())
    _write_detailed_roadmap(root, _detailed_roadmap())
    _write_coding_prompt(root, "001_frutlups_m001_s01_first_slice.md", _minimal_coding_prompt(1))
    _write_self_report(root, "05_governance/reviews/m001_s01_first_slice_self_report.md")
    _write_review_prompt(root, "001_review_m001_s01_first_slice.md")


def _write_record(root: Path, content: str | bytes, name: str = VR.rsplit("/", 1)[-1]) -> Path:
    target = root / REVIEWS[0] / REVIEWS[1] / name
    if isinstance(content, bytes):
        target.write_bytes(content)
    else:
        target.write_text(content, encoding="utf-8")
    return target


def _generated_record(report_rel: str, slice_id: str = "M001-S01") -> str:
    """The current generated record shape: single-line source citation."""
    return (
        f"# Verdict Record: {slice_id}\n\n"
        "## Source\n\n"
        f"Review report: `{report_rel}`\n\n"
        "## Slice\n\n"
        f"Slice ID: `{slice_id}`\n"
        "Title: first slice\n"
        "Milestone: `M001`\n\n"
        "## Parsed Verdict\n\n"
        "Verdict: `pass`\n\n"
        "## Next Action\n\n"
        "Kind: `complete`\n"
    )


def _wrapped_record(report_rel: str, prior: tuple[str, ...] = (), slice_id: str = "M001-S01") -> str:
    """Accepted cumulative-correction shape: wrapped citation plus history."""
    lines = [
        f"# Verdict Record: {slice_id}",
        "",
        "## Source",
        "",
        "Passing cumulative correction review:",
        f"`{report_rel}`",
        "",
    ]
    if prior:
        lines.append("Prior review reports retained as durable findings history:")
        lines.append("")
        lines.extend(f"- `{path}`" for path in prior)
        lines.append("")
    lines.extend(["## Slice", "", f"Slice ID: `{slice_id}`", ""])
    return "\n".join(lines)


def _evidence(root: Path):
    status = build_status(root)
    profile = status.layout.profile if status.layout is not None else legacy_profile()
    return _collect_acceptance_evidence(root, profile)


def _resume(root: Path):
    return build_loop_resume_status(build_status(root))


def _write_supported_posture(root: Path, must_stop_on: tuple[str, ...] = ()) -> None:
    config = _SUPPORTED_POSTURE
    if must_stop_on:
        config += "  must_stop_on:\n" + "".join(f'    - "{value}"\n' for value in must_stop_on)
    (root / "frutlups.layout.yaml").write_text(config, encoding="utf-8")


class AcceptedDirectionTests(unittest.TestCase):
    """Canonical pass reports, and only those, accept; receipt absence routes
    exactly to record_verdict; a valid cited receipt then clears it."""

    def test_all_accepted_spellings_accept_and_route_record_verdict(self) -> None:
        spellings = ("pass", "PASS", "Pass", "`pass`", "- `pass`", "* pass", "1. `PASS`")
        for spelling in spellings:
            with self.subTest(spelling=spelling), TemporaryDirectory() as tmp:
                root = Path(tmp)
                _in_flight_project(root)
                _write_review_report(root, RR.rsplit("/", 1)[-1], spelling)
                evidence = _evidence(root)
                self.assertIn("M001-S01", evidence.accepted_slice_ids)
                self.assertIn(RR, evidence.unrecorded_pass_reports)
                self.assertEqual(evidence.contradictions, ())
                self.assertIn("M001-S01", build_status(root).accepted_slice_ids)
                resume = _resume(root)
                self.assertEqual(resume.step, LoopResumeStep.RECORD_VERDICT)
                self.assertIn("record-verdict", resume.next_command)
                self.assertEqual(resume.review_report_path, RR)

    def test_non_pass_spellings_never_accept(self) -> None:
        for verdict in ("needs_work", "blocked", "override", "passes", "pass note"):
            with self.subTest(verdict=verdict), TemporaryDirectory() as tmp:
                root = Path(tmp)
                _in_flight_project(root)
                _write_review_report(root, RR.rsplit("/", 1)[-1], verdict)
                self.assertNotIn("M001-S01", _evidence(root).accepted_slice_ids)

    def test_valid_cited_receipt_clears_record_verdict_without_regression(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            _write_review_report(root, RR.rsplit("/", 1)[-1], "pass")
            self.assertEqual(_resume(root).step, LoopResumeStep.RECORD_VERDICT)
            _write_record(root, _generated_record(RR))
            evidence = _evidence(root)
            self.assertIn("M001-S01", evidence.accepted_slice_ids)
            self.assertEqual(evidence.unrecorded_pass_reports, ())
            self.assertEqual(evidence.contradictions, ())
            resume = _resume(root)
            self.assertNotIn(
                resume.step,
                (LoopResumeStep.RECORD_VERDICT, LoopResumeStep.FIX_REVIEW_REPORT),
            )

    def test_receipt_absence_never_unaccepts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            _write_review_report(root, RR.rsplit("/", 1)[-1], "pass")
            _write_record(root, _generated_record(RR))
            (root / REVIEWS[0] / REVIEWS[1] / VR.rsplit("/", 1)[-1]).unlink()
            # Receipt removed: the slice stays accepted (report authority),
            # and the loop routes back to record_verdict for the receipt.
            self.assertIn("M001-S01", _evidence(root).accepted_slice_ids)
            self.assertEqual(_resume(root).step, LoopResumeStep.RECORD_VERDICT)


def _assert_contradiction(
    testcase: unittest.TestCase,
    root: Path,
    kind: "_RecordContradictionKind",
    *,
    report_path: str = RR,
    record_path: str = VR,
) -> None:
    evidence = _evidence(root)
    testcase.assertNotIn("M001-S01", evidence.accepted_slice_ids)
    testcase.assertEqual(len(evidence.contradictions), 1)
    contradiction = evidence.contradictions[0]
    testcase.assertEqual(contradiction.kind, kind)
    testcase.assertEqual(contradiction.record_path, record_path)
    testcase.assertEqual(contradiction.report_path, report_path)
    testcase.assertEqual(contradiction.slice_id, "M001-S01")
    testcase.assertLessEqual(len(contradiction.diagnostic), 240)
    testcase.assertNotIn("M001-S01", build_status(root).accepted_slice_ids)
    resume = _resume(root)
    testcase.assertEqual(resume.step, LoopResumeStep.FIX_REVIEW_REPORT)
    testcase.assertEqual(resume.next_command, "")
    testcase.assertEqual(resume.verdict_record_path, record_path)
    testcase.assertEqual(resume.review_report_path, report_path)
    testcase.assertIn(record_path, resume.message)
    testcase.assertLessEqual(len(resume.message), 240)
    testcase.assertEqual(resume.diagnostics[0], resume.message)


class ContradictionDirectionTests(unittest.TestCase):
    """Reverse direction: a record without a corresponding canonical passing
    report is typed contradictory durable state and fails closed."""

    def _assert_contradiction(
        self,
        root: Path,
        kind: "_RecordContradictionKind",
        *,
        report_path: str = RR,
        record_path: str = VR,
    ) -> None:
        _assert_contradiction(
            self, root, kind, report_path=report_path, record_path=record_path
        )

    def test_record_only_is_contradiction(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            _write_record(root, _generated_record(RR))
            self._assert_contradiction(root, _RecordContradictionKind.MISSING_REPORT)

    def test_record_with_missing_cited_report_is_contradiction(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            cited = "05_governance/reviews/m001_s01_correction_review_report.md"
            _write_review_report(root, RR.rsplit("/", 1)[-1], "needs_work")
            _write_record(root, _generated_record(cited))
            self._assert_contradiction(
                root, _RecordContradictionKind.MISSING_REPORT, report_path=cited
            )

    def test_invalid_utf8_cited_report_is_contradiction(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            (root / REVIEWS[0] / REVIEWS[1] / RR.rsplit("/", 1)[-1]).write_bytes(
                b"# Review\n\n## Verdict\n\n\xff\xfeinvalid\n"
            )
            _write_record(root, _generated_record(RR))
            self._assert_contradiction(root, _RecordContradictionKind.UNPARSEABLE_REPORT)

    def test_malformed_cited_report_is_contradiction(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            (root / REVIEWS[0] / REVIEWS[1] / RR.rsplit("/", 1)[-1]).write_text(
                "# Review\n\nNo verdict section here.\n", encoding="utf-8"
            )
            _write_record(root, _generated_record(RR))
            self._assert_contradiction(root, _RecordContradictionKind.UNPARSEABLE_REPORT)

    def test_records_citing_each_non_pass_verdict_are_contradictions(self) -> None:
        for verdict in ("needs_work", "blocked", "override"):
            with self.subTest(verdict=verdict), TemporaryDirectory() as tmp:
                root = Path(tmp)
                _in_flight_project(root)
                _write_review_report(root, RR.rsplit("/", 1)[-1], verdict)
                _write_record(root, _generated_record(RR))
                self._assert_contradiction(root, _RecordContradictionKind.NON_PASS_REPORT)

    def test_bare_record_is_missing_source_contradiction(self) -> None:
        # A bare record has no live source citation: typed missing_source even
        # though a same-stem report exists — a filename grants no receipt
        # authority, and the report's bytes are never consulted.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            _write_review_report(root, RR.rsplit("/", 1)[-1], "needs_work")
            _write_record(root, "# Verdict Record\n")
            self._assert_contradiction(root, _RecordContradictionKind.MISSING_SOURCE)

    def test_contradiction_is_not_ready_no_frontier_or_recorded(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            _write_record(root, _generated_record(RR))  # record only
            resume = _resume(root)
            self.assertEqual(resume.step, LoopResumeStep.FIX_REVIEW_REPORT)
            self.assertNotIn(
                resume.step,
                (
                    LoopResumeStep.NO_FRONTIER,
                    LoopResumeStep.FRONTIER_RECORDED,
                    LoopResumeStep.RECORD_VERDICT,
                ),
            )
            plan = build_orchestrator_plan(root)
            self.assertFalse(plan.safe_for_auto_execution)


class MutationFreshnessTests(unittest.TestCase):
    """Mutating or deleting the cited report after receipt creation fails the
    next fresh observation closed; restoring the pass report restores normal
    state with no cached authority."""

    def test_mutate_delete_retarget_restore(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            report_name = RR.rsplit("/", 1)[-1]
            report_abs = root / REVIEWS[0] / REVIEWS[1] / report_name
            _write_review_report(root, report_name, "pass")
            record = _generated_record(RR)
            _write_record(root, record)
            self.assertEqual(_evidence(root).contradictions, ())
            # pass -> non-pass after receipt creation
            _write_review_report(root, report_name, "needs_work")
            contradiction = _evidence(root).contradictions
            self.assertEqual(len(contradiction), 1)
            self.assertEqual(contradiction[0].kind, _RecordContradictionKind.NON_PASS_REPORT)
            self.assertEqual(_resume(root).step, LoopResumeStep.FIX_REVIEW_REPORT)
            # restore the canonical passing report: normal state, no cache
            _write_review_report(root, report_name, "pass")
            self.assertEqual(_evidence(root).contradictions, ())
            self.assertNotEqual(_resume(root).step, LoopResumeStep.FIX_REVIEW_REPORT)
            # delete the cited report
            report_abs.unlink()
            self.assertEqual(
                _evidence(root).contradictions[0].kind,
                _RecordContradictionKind.MISSING_REPORT,
            )
            # retarget the record at a different (passing) slice's report
            _write_review_report(root, report_name, "pass")
            _write_review_report(root, "m001_s02_second_slice_review_report.md", "pass")
            _write_record(
                root,
                _generated_record("05_governance/reviews/m001_s02_second_slice_review_report.md"),
            )
            self.assertEqual(
                _evidence(root).contradictions[0].kind,
                _RecordContradictionKind.DIFFERENT_SLICE,
            )


class NonAuthorityTests(unittest.TestCase):
    """Record prose, filenames, journal entries, and profile-shaped content
    never accept; only the cited canonical passing report does."""

    def test_pass_prose_record_without_passing_report_never_accepts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            # Hand-written record full of pass-looking prose but citing a
            # report that does not exist.
            _write_record(
                root,
                "# Verdict Record: M001-S01\n\n"
                "## Source\n\n"
                "Review report: `05_governance/reviews/m001_s01_first_slice_review_report.md`\n\n"
                "## Parsed Verdict\n\nVerdict: `pass`\n\n"
                "## Next Action\n\nKind: `complete`\nNext slice: `M001-S02`\n",
            )
            evidence = _evidence(root)
            self.assertNotIn("M001-S01", evidence.accepted_slice_ids)
            self.assertEqual(
                evidence.contradictions[0].kind, _RecordContradictionKind.MISSING_REPORT
            )
            self.assertEqual(_resume(root).step, LoopResumeStep.FIX_REVIEW_REPORT)

    def test_journal_entry_and_okf_shaped_content_grant_nothing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            # A journal entry claiming a recorded pass exists...
            journal = journal_path_for(root)
            journal.parent.mkdir(parents=True, exist_ok=True)
            journal.write_text(
                json.dumps({"event_kind": "write", "artifact_path": VR}) + "\n",
                encoding="utf-8",
            )
            # ...plus a record with profile/OKF-shaped frontmatter, and no
            # real passing report anywhere.
            _write_record(
                root,
                "---\ntype: okf_concept\nokf_concept:\n  result: pass\n---\n"
                "# Verdict Record: M001-S01\n\n## Source\n\n"
                "Review report: `05_governance/reviews/m001_s01_first_slice_review_report.md`\n",
            )
            evidence = _evidence(root)
            self.assertNotIn("M001-S01", evidence.accepted_slice_ids)
            self.assertEqual(len(evidence.contradictions), 1)
            self.assertEqual(_resume(root).step, LoopResumeStep.FIX_REVIEW_REPORT)


class SourcePairingTests(unittest.TestCase):
    """The bounded `## Source` reader pairs a receipt to the report it
    actually cites, never blindly to the suffix-derived name."""

    def test_generated_single_line_and_wrapped_citations_pair(self) -> None:
        for content in (_generated_record(RR), _wrapped_record(RR)):
            with self.subTest(shape=content.splitlines()[4].strip()), TemporaryDirectory() as tmp:
                root = Path(tmp)
                _in_flight_project(root)
                _write_review_report(root, RR.rsplit("/", 1)[-1], "pass")
                _write_record(root, content)
                evidence = _evidence(root)
                self.assertEqual(evidence.contradictions, ())
                self.assertEqual(evidence.unrecorded_pass_reports, ())

    def test_cumulative_receipt_first_citation_wins_over_prior_history(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            # The component base-name report is a retained earlier needs_work;
            # the receipt cites the later passing correction report.
            _write_review_report(root, RR.rsplit("/", 1)[-1], "needs_work")
            correction = "05_governance/reviews/m001_s01_first_slice_correction_review_report.md"
            _write_review_report(root, correction.rsplit("/", 1)[-1], "pass")
            _write_record(root, _wrapped_record(correction, prior=(RR,)))
            evidence = _evidence(root)
            self.assertEqual(evidence.contradictions, ())
            self.assertEqual(evidence.unrecorded_pass_reports, ())
            self.assertNotEqual(_resume(root).step, LoopResumeStep.FIX_REVIEW_REPORT)

    def test_suffix_paired_non_pass_report_cannot_displace_citation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            # The receipt's own stem report is needs_work, but the receipt
            # cites a different passing same-slice report: not contradictory.
            _write_review_report(root, RR.rsplit("/", 1)[-1], "needs_work")
            other = "05_governance/reviews/m001_s01_second_review_review_report.md"
            _write_review_report(root, other.rsplit("/", 1)[-1], "pass")
            _write_record(root, _generated_record(other))
            evidence = _evidence(root)
            self.assertEqual(evidence.contradictions, ())
            # ...and the stem report is not what the receipt records.
            self.assertNotIn(other, evidence.unrecorded_pass_reports)

    def test_foreign_slice_citation_is_contradiction(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            foreign = "05_governance/reviews/m001_s02_second_slice_review_report.md"
            _write_review_report(root, foreign.rsplit("/", 1)[-1], "pass")
            _write_record(root, _generated_record(foreign))
            contradiction = _evidence(root).contradictions
            self.assertEqual(len(contradiction), 1)
            self.assertEqual(contradiction[0].kind, _RecordContradictionKind.DIFFERENT_SLICE)
            # The foreign slice's own pass report stays accepted and now
            # surfaces for its own receipt only after the contradiction clears.
            self.assertIn("M001-S02", _evidence(root).accepted_slice_ids)

    def test_absolute_and_escape_citations_are_contradictions(self) -> None:
        for citation in (
            "C:/abs/m001_s01_first_slice_review_report.md",
            "../m001_s01_first_slice_review_report.md",
            "05_governance/m001_s01_first_slice_review_report.md",
            "05_governance/reviews/../../05_governance/reviews/m001_s01_first_slice_review_report.md",
        ):
            with self.subTest(citation=citation), TemporaryDirectory() as tmp:
                root = Path(tmp)
                _in_flight_project(root)
                _write_review_report(root, RR.rsplit("/", 1)[-1], "pass")
                _write_record(root, _generated_record(citation))
                contradiction = _evidence(root).contradictions
                self.assertEqual(len(contradiction), 1)
                self.assertEqual(
                    contradiction[0].kind, _RecordContradictionKind.UNSAFE_CITATION
                )
                # the hostile citation text is never echoed
                self.assertNotIn(citation, contradiction[0].diagnostic)

    def test_fenced_decoy_citation_is_inert(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            _write_review_report(root, RR.rsplit("/", 1)[-1], "pass")
            _write_record(
                root,
                "# Verdict Record: M001-S01\n\n"
                "## Source\n\n"
                "Example, not the source:\n\n"
                "```\n"
                "Review report: `05_governance/reviews/m001_s02_second_slice_review_report.md`\n"
                "```\n\n"
                f"Review report: `{RR}`\n",
            )
            self.assertEqual(_evidence(root).contradictions, ())

    def test_fenced_only_citation_is_missing_source(self) -> None:
        # The only citation lives inside a fence: inert, so the record has no
        # live source citation. The passing report still accepts the slice
        # (report authority is unaffected by a contradictory record).
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            _write_review_report(root, RR.rsplit("/", 1)[-1], "pass")
            _write_record(
                root,
                "# Verdict Record: M001-S01\n\n"
                "## Source\n\n"
                "```\n"
                "Review report: `05_governance/reviews/m001_s02_second_slice_review_report.md`\n"
                "```\n",
            )
            evidence = _evidence(root)
            self.assertEqual(len(evidence.contradictions), 1)
            self.assertEqual(
                evidence.contradictions[0].kind, _RecordContradictionKind.MISSING_SOURCE
            )
            self.assertIn("M001-S01", evidence.accepted_slice_ids)
            self.assertIn(RR, evidence.unrecorded_pass_reports)
            self.assertEqual(_resume(root).step, LoopResumeStep.FIX_REVIEW_REPORT)

    def test_missing_source_section_is_missing_source(self) -> None:
        # No `## Source` section at all: typed missing_source; the same-stem
        # passing report grants nothing and stays unrecorded.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            _write_review_report(root, RR.rsplit("/", 1)[-1], "pass")
            _write_record(root, "# Verdict Record\n")
            evidence = _evidence(root)
            self.assertEqual(len(evidence.contradictions), 1)
            self.assertEqual(
                evidence.contradictions[0].kind, _RecordContradictionKind.MISSING_SOURCE
            )
            self.assertEqual(
                evidence.contradictions[0].report_path, RR  # safe expected label only
            )
            self.assertIn(RR, evidence.unrecorded_pass_reports)
            self.assertEqual(_resume(root).step, LoopResumeStep.FIX_REVIEW_REPORT)

    def test_duplicate_source_heading_is_contradiction(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            _write_review_report(root, RR.rsplit("/", 1)[-1], "pass")
            _write_record(
                root,
                "# Verdict Record: M001-S01\n\n"
                "## Source\n\n"
                f"Review report: `{RR}`\n\n"
                "## Source\n\n"
                f"Review report: `{RR}`\n",
            )
            contradiction = _evidence(root).contradictions
            self.assertEqual(len(contradiction), 1)
            self.assertEqual(
                contradiction[0].kind, _RecordContradictionKind.AMBIGUOUS_SOURCE
            )

    def test_section_ends_at_next_live_h2(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            _write_review_report(root, RR.rsplit("/", 1)[-1], "pass")
            _write_record(
                root,
                "# Verdict Record: M001-S01\n\n"
                "## Slice\n\n"
                "Slice ID: `M001-S01`\n\n"
                "## Source\n\n"
                f"Review report: `{RR}`\n\n"
                "## Parsed Verdict\n\n"
                "Verdict: `pass`\n\n"
                "## Later\n\n"
                "`05_governance/reviews/m001_s02_second_slice_review_report.md`\n",
            )
            self.assertEqual(_evidence(root).contradictions, ())

    def test_invalid_utf8_and_oversized_records_are_contradictions(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            _write_review_report(root, RR.rsplit("/", 1)[-1], "pass")
            _write_record(root, b"# Verdict Record\n\xff\xfe")
            contradiction = _evidence(root).contradictions
            self.assertEqual(len(contradiction), 1)
            self.assertEqual(
                contradiction[0].kind, _RecordContradictionKind.UNREADABLE_RECORD
            )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            _write_review_report(root, RR.rsplit("/", 1)[-1], "pass")
            _write_record(root, "# Verdict Record\n" + "x" * (300 * 1024))
            contradiction = _evidence(root).contradictions
            self.assertEqual(len(contradiction), 1)
            self.assertEqual(
                contradiction[0].kind, _RecordContradictionKind.UNREADABLE_RECORD
            )

    def test_unrelated_files_and_non_slice_records_are_ignored(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            _write_review_report(root, RR.rsplit("/", 1)[-1], "pass")
            _write_record(root, _generated_record(RR))
            reviews = root / REVIEWS[0] / REVIEWS[1]
            (reviews / "m002_atomic_acceptance_verdict_record.md").write_text(
                "# Verdict Record\n", encoding="utf-8"
            )
            (reviews / "notes.md").write_text("# Notes\n", encoding="utf-8")
            (reviews / "random_review_report.md").write_text(
                "# Review\n\n## Verdict\n\nneeds_work\n", encoding="utf-8"
            )
            self.assertEqual(_evidence(root).contradictions, ())
            self.assertEqual(_evidence(root).accepted_slice_ids, ("M001-S01",))

    def test_configured_reviews_dir_and_suffixes_are_honored(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            (root / "frutlups.layout.yaml").write_text(
                "schema_version: frutlups_layout_config_v0\n"
                "profile_id: artifact_first_template_legacy_root\n"
                "reports:\n"
                "  reviews_dir: 05_governance/audit\n"
                "  review_report_suffix: _rr.md\n"
                "  verdict_record_suffix: _vr.md\n",
                encoding="utf-8",
            )
            audit = root / "05_governance" / "audit"
            audit.mkdir(parents=True)
            (audit / "m001_s01_first_slice_rr.md").write_text(
                "# Review\n\n## Verdict\n\nneeds_work\n", encoding="utf-8"
            )
            (audit / "m001_s01_first_slice_vr.md").write_text(
                "# Verdict Record\n\n## Source\n\n"
                "Review report: `05_governance/audit/m001_s01_first_slice_rr.md`\n",
                encoding="utf-8",
            )
            evidence = _evidence(root)
            self.assertEqual(len(evidence.contradictions), 1)
            self.assertEqual(
                evidence.contradictions[0].kind, _RecordContradictionKind.NON_PASS_REPORT
            )
            self.assertEqual(
                evidence.contradictions[0].record_path,
                "05_governance/audit/m001_s01_first_slice_vr.md",
            )
            self.assertEqual(
                evidence.contradictions[0].report_path,
                "05_governance/audit/m001_s01_first_slice_rr.md",
            )
            self.assertEqual(_resume(root).step, LoopResumeStep.FIX_REVIEW_REPORT)


class OrderingAndPurityTests(unittest.TestCase):
    def test_first_contradiction_by_record_path_wins_rest_bounded(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            reviews = root / REVIEWS[0] / REVIEWS[1]
            (reviews / "m001_s02_second_slice_verdict_record.md").write_text(
                "# Verdict Record\n", encoding="utf-8"
            )
            _write_record(root, "# Verdict Record\n")  # m001_s01 record sorts first
            resume = _resume(root)
            self.assertEqual(resume.step, LoopResumeStep.FIX_REVIEW_REPORT)
            self.assertEqual(resume.verdict_record_path, VR)
            self.assertEqual(len(resume.diagnostics), 2)
            self.assertIn("m001_s02_second_slice_verdict_record.md", resume.diagnostics[1])
            for diagnostic in resume.diagnostics:
                self.assertLessEqual(len(diagnostic), 240)

    def test_contradiction_beats_unrelated_missing_receipt(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            # An unrelated passing report without a receipt...
            _write_review_report(root, "m001_s02_second_slice_review_report.md", "pass")
            # ...plus a contradiction for M001-S01: the contradiction wins.
            _write_record(root, _generated_record(RR))
            resume = _resume(root)
            self.assertEqual(resume.step, LoopResumeStep.FIX_REVIEW_REPORT)
            self.assertEqual(resume.frontier_slice_id, "M001-S01")

    def test_multiple_unrecorded_passes_are_deterministic(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            _write_review_report(root, "m001_s02_second_slice_review_report.md", "pass")
            _write_review_report(root, RR.rsplit("/", 1)[-1], "pass")
            evidence = _evidence(root)
            self.assertEqual(
                evidence.unrecorded_pass_reports,
                (
                    "05_governance/reviews/m001_s01_first_slice_review_report.md",
                    "05_governance/reviews/m001_s02_second_slice_review_report.md",
                ),
            )
            resume = _resume(root)
            self.assertEqual(resume.step, LoopResumeStep.RECORD_VERDICT)
            self.assertEqual(resume.review_report_path, RR)

    def test_duplicate_receipts_same_pass_report_are_not_contradictions(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            _write_review_report(root, RR.rsplit("/", 1)[-1], "pass")
            _write_record(root, _generated_record(RR))
            _write_record(
                root, _generated_record(RR), name="m001_s01_duplicate_verdict_record.md"
            )
            self.assertEqual(_evidence(root).contradictions, ())
            self.assertEqual(_evidence(root).unrecorded_pass_reports, ())

    def test_repeated_calls_pure_and_read_only(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            _write_record(root, _generated_record(RR))  # contradiction
            before = _snapshot(root)
            first_evidence = _evidence(root)
            first_resume = _resume(root)
            second_evidence = _evidence(root)
            second_resume = _resume(root)
            self.assertEqual(first_evidence, second_evidence)
            self.assertEqual(first_resume, second_resume)
            self.assertEqual(before, _snapshot(root))


class TerminalClosureTests(unittest.TestCase):
    """Terminal closure stays exact; a contradiction blocks completion; a
    terminal tail cannot self-certify; correction receipts pair by citation."""

    def _completed_project(self, root: Path) -> None:
        _make_template(root)
        _write_active_roadmap(root, _active_roadmap())
        _write_detailed_roadmap(root, _detailed_roadmap(slices=[("M001-S01", "first slice")]))
        _write_coding_prompt(root, "001_frutlups_m001_s01_first_slice.md", _minimal_coding_prompt(1))
        _write_self_report(root, "05_governance/reviews/m001_s01_first_slice_self_report.md")
        _write_review_prompt(root, "001_review_m001_s01_first_slice.md")
        _write_review_report(root, RR.rsplit("/", 1)[-1], "pass")
        _write_record(root, _generated_record(RR))

    def test_completed_roadmap_with_valid_receipt_stays_no_frontier(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._completed_project(root)
            self.assertEqual(_resume(root).step, LoopResumeStep.NO_FRONTIER)

    def test_contradiction_blocks_terminal_closure(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._completed_project(root)
            (root / REVIEWS[0] / REVIEWS[1] / RR.rsplit("/", 1)[-1]).unlink()
            resume = _resume(root)
            self.assertEqual(resume.step, LoopResumeStep.FIX_REVIEW_REPORT)
            self.assertNotEqual(resume.step, LoopResumeStep.NO_FRONTIER)

    def test_terminal_tail_cannot_self_certify(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._completed_project(root)
            # A terminal closure-tail pass report with no receipt and no
            # independent non-terminal acceptance of its own slice must still
            # surface record_verdict rather than being skipped or accepting.
            tail = "m001_s02_record_001_verdict_review_report.md"
            _write_review_report(root, tail, "pass")
            resume = _resume(root)
            self.assertEqual(resume.step, LoopResumeStep.RECORD_VERDICT)
            self.assertIn(tail, resume.review_report_path)


class SurfaceIntegrationTests(unittest.TestCase):
    """status/next/CLI/orchestrator/gate fail closed under contradiction with
    unchanged public shapes and a preserved filesystem."""

    def _contradiction_project(self, root: Path, **policy) -> None:
        _in_flight_project(root)
        _write_supported_posture(root, **policy)
        _write_record(root, _generated_record(RR))  # record only: contradiction

    def test_cli_status_and_next_text_and_json_under_contradiction(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._contradiction_project(root)
            before = _snapshot(root)
            code, out, _ = _run(["status", str(root)])
            self.assertEqual(code, 0)
            self.assertIn("fix_review_report", out)
            code, out, _ = _run(["status", str(root), "--json"])
            self.assertEqual(code, 0)
            status_payload = json.loads(out)
            self.assertEqual(status_payload["loop_resume"]["step"], "fix_review_report")
            self.assertEqual(
                set(status_payload["loop_resume"]),
                {
                    "step",
                    "message",
                    "next_command",
                    "frontier_slice_id",
                    "frontier_slice_title",
                    "coding_prompt_path",
                    "self_report_path",
                    "review_prompt_path",
                    "review_report_path",
                    "verdict_record_path",
                    "diagnostics",
                },
            )
            code, out, _ = _run(["next", str(root), "--json"])
            self.assertEqual(code, 0)
            json.loads(out)
            self.assertEqual(before, _snapshot(root))

    def test_orchestrator_plan_and_gate_fail_closed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._contradiction_project(root)
            plan = build_orchestrator_plan(root)
            self.assertFalse(plan.safe_for_auto_execution)
            gate = build_human_gate(root)
            self.assertEqual(gate.gate_state, HumanGateState.STOP.value)
            self.assertTrue(gate.requires_human_go)

    def _assert_contradiction_no_write_cell(self, root: Path, invoke) -> None:
        """One contradiction invocation: no writer, complete snapshot, one
        bounded journal entry attributable to exactly this invocation."""
        before = _snapshot(root)
        journal_rel = str(journal_path_for(root).relative_to(root))
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
            result, code, out = invoke(root)
        self.assertEqual(code, 0)  # a safe refusal is not an error
        self.assertTrue(result.refused)
        self.assertFalse(result.attempted)
        self.assertFalse(result.wrote)
        self.assertEqual(result.artifact_path, "")
        self.assertIn("not safe for automatic local execution", result.refusal_reason)
        self.assertIn("refused", out.lower())
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
        # Exactly one bounded evidence-only entry from this invocation.
        read = read_run_journal(journal_path_for(root))
        self.assertEqual(len(read.entries), 1)
        entry = read.entries[0]
        self.assertEqual(entry.event_kind, "refuse")
        self.assertEqual(entry.refusal_reason, result.refusal_reason)
        self.assertTrue(entry.refused)
        self.assertFalse(entry.attempted)
        self.assertFalse(entry.wrote)
        self.assertEqual(entry.artifact_path, "")
        self.assertLessEqual(len(entry.refusal_reason), 240)
        for diagnostic in entry.diagnostics:
            self.assertLessEqual(len(diagnostic), 240)

    @staticmethod
    def _direct_invoke(root: Path):
        result = run_one_step(root, journal=True)
        return result, 0, "refused" if result.refused else ""

    @staticmethod
    def _cli_text_invoke(root: Path):
        code, out, _ = _run(["orchestrator-run", str(root)])
        journal = read_run_journal(journal_path_for(root))
        entry = journal.entries[-1]
        result = type(
            "R",
            (),
            {
                "refused": entry.refused,
                "attempted": entry.attempted,
                "wrote": entry.wrote,
                "artifact_path": entry.artifact_path,
                "refusal_reason": entry.refusal_reason,
            },
        )()
        return result, code, out

    @staticmethod
    def _cli_json_invoke(root: Path):
        code, out, _ = _run(["orchestrator-run", str(root), "--json"])
        payload = json.loads(out)
        result = type(
            "R",
            (),
            {
                "refused": payload["refused"],
                "attempted": payload["attempted"],
                "wrote": payload["wrote"],
                "artifact_path": payload["artifact_path"],
                "refusal_reason": payload["refusal_reason"],
            },
        )()
        return result, code, out

    def test_orchestrator_run_reaches_no_writer_and_journals_one_refusal(self) -> None:
        # Direct cell (keeps the Review 028-cited name): one invocation, no
        # writer reached, complete non-journal snapshot, exactly one bounded
        # refusal entry.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._contradiction_project(root)
            self._assert_contradiction_no_write_cell(root, self._direct_invoke)

    def test_contradiction_no_write_cli_text_cell(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._contradiction_project(root)
            self._assert_contradiction_no_write_cell(root, self._cli_text_invoke)

    def test_contradiction_no_write_cli_json_cell(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._contradiction_project(root)
            self._assert_contradiction_no_write_cell(root, self._cli_json_invoke)

    def test_snapshot_falsifier_mutation_and_deletion_detected(self) -> None:
        # A mutating/deleting invocation must fail the complete comparison.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._contradiction_project(root)
            sentinel = root / "prompts" / "for_coding_agent" / "001_frutlups_m001_s01_first_slice.md"

            def mutating_invoke(project_root: Path):
                result = self._direct_invoke(project_root)
                sentinel.write_text(
                    sentinel.read_text(encoding="utf-8") + "MUTATED\n", encoding="utf-8"
                )
                (project_root / "06_infra").rmdir()
                return result

            with self.assertRaises(AssertionError):
                self._assert_contradiction_no_write_cell(root, mutating_invoke)

    def test_journal_falsifier_double_append_detected(self) -> None:
        # A double append by one invocation must fail the exactly-one pin.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._contradiction_project(root)
            import frutlups.orchestrator as orchestrator_module

            real_append = orchestrator_module.append_run_journal_entry

            def double_append(*args, **kwargs):
                real_append(*args, **kwargs)
                return real_append(*args, **kwargs)

            with mock.patch.object(
                orchestrator_module, "append_run_journal_entry", side_effect=double_append
            ):
                with self.assertRaises(AssertionError):
                    self._assert_contradiction_no_write_cell(root, self._direct_invoke)

    def test_configured_invalid_review_report_stop_matches_contradiction(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._contradiction_project(root, must_stop_on=("invalid review report",))
            result = run_one_step(root, journal=False)
            self.assertTrue(result.refused)
            self.assertEqual(
                result.refusal_reason,
                "runner policy refused: must_stop_on condition "
                "'invalid review report' matched current state",
            )


class RecordVerdictCompatibilityTests(unittest.TestCase):
    """record-verdict and the pure next-action mapping stay compatible; a
    non-pass receipt is exposed as a contradiction on the next read."""

    def test_pure_next_action_mapping_unchanged(self) -> None:
        from frutlups.state import NextActionCommand, RoadmapSlice

        current = RoadmapSlice(
            milestone_id="M001", slice_id="M001-S01", title="first slice"
        )
        following = RoadmapSlice(
            milestone_id="M001", slice_id="M001-S02", title="second slice"
        )
        expected = {
            ReviewVerdict.PASS: NextActionKind.ADVANCE_TO_NEXT_SLICE,
            ReviewVerdict.NEEDS_WORK: NextActionKind.RECODE_SAME_SLICE,
            ReviewVerdict.BLOCKED: NextActionKind.UNBLOCK_SAME_SLICE,
            ReviewVerdict.OVERRIDE: NextActionKind.HUMAN_OVERRIDE_REQUIRED,
        }
        for verdict, kind in expected.items():
            with self.subTest(verdict=verdict):
                decision = compute_next_action_from_verdict(
                    NextActionCommand(
                        verdict=verdict,
                        current_slice=current,
                        slices=(current, following),
                    )
                )
                self.assertEqual(decision.kind, kind)

    def test_record_verdict_cli_all_four_verdicts_still_write(self) -> None:
        for verdict in ("pass", "needs_work", "blocked", "override"):
            with self.subTest(verdict=verdict), TemporaryDirectory() as tmp:
                root = Path(tmp)
                _in_flight_project(root)
                _write_review_report(root, RR.rsplit("/", 1)[-1], verdict)
                code, _, _ = _run(
                    ["record-verdict", str(root), "--review-report", str(root / RR)]
                )
                self.assertEqual(code, 0)
                self.assertTrue(
                    (root / REVIEWS[0] / REVIEWS[1] / VR.rsplit("/", 1)[-1]).is_file()
                )

    def test_non_pass_receipt_exposed_as_contradiction_on_next_read(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            _write_review_report(root, RR.rsplit("/", 1)[-1], "needs_work")
            code, _, _ = _run(
                ["record-verdict", str(root), "--review-report", str(root / RR)]
            )
            self.assertEqual(code, 0)  # the writer itself is unchanged
            resume = _resume(root)
            self.assertEqual(resume.step, LoopResumeStep.FIX_REVIEW_REPORT)
            self.assertNotIn("M001-S01", _evidence(root).accepted_slice_ids)


class ConfiguredProfilePublicMatrixTests(unittest.TestCase):
    """F1: the selected configured profile drives every public path — status,
    next, resume, record planning, orchestrator, and CLI — with no legacy
    parallel scan."""

    @staticmethod
    def _custom_project(root: Path) -> Path:
        _in_flight_project(root)
        (root / "frutlups.layout.yaml").write_text(
            "schema_version: frutlups_layout_config_v0\n"
            "profile_id: artifact_first_template_legacy_root\n"
            "automation_boundary:\n"
            "  runner_implemented: true\n"
            "reports:\n"
            "  reviews_dir: 05_governance/audit\n"
            "  review_report_suffix: _rr.md\n"
            "  verdict_record_suffix: _vr.md\n",
            encoding="utf-8",
        )
        audit = root / "05_governance" / "audit"
        audit.mkdir(parents=True)
        return audit

    def test_pass_no_receipt_public_orientation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit = self._custom_project(root)
            (audit / "m001_s01_first_slice_rr.md").write_text(
                "# Review\n\n## Verdict\n\npass\n", encoding="utf-8"
            )
            status = build_status(root)
            self.assertIn("M001-S01", status.accepted_slice_ids)
            resume = _resume(root)
            self.assertEqual(resume.step, LoopResumeStep.RECORD_VERDICT)
            self.assertEqual(
                resume.review_report_path, "05_governance/audit/m001_s01_first_slice_rr.md"
            )
            self.assertEqual(
                resume.verdict_record_path, "05_governance/audit/m001_s01_first_slice_vr.md"
            )
            self.assertIn("05_governance/audit/m001_s01_first_slice_rr.md", resume.next_command)
            code, out, _ = _run(["status", str(root), "--json"])
            self.assertEqual(code, 0)
            payload = json.loads(out)
            self.assertIn("M001-S01", payload["accepted_slice_ids"])
            self.assertEqual(payload["loop_resume"]["step"], "record_verdict")
            code, out, _ = _run(["next", str(root), "--json"])
            self.assertEqual(code, 0)
            json.loads(out)
            # verdict-record planning consumes the same accepted IDs
            from frutlups.project import build_verdict_record_plan

            plan = build_verdict_record_plan(root, audit / "m001_s01_first_slice_rr.md")
            self.assertTrue(plan.valid, plan.errors)
            self.assertEqual(
                plan.target_path, "05_governance/audit/m001_s01_first_slice_vr.md"
            )

    def test_pass_with_valid_receipt_frontier_advances(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit = self._custom_project(root)
            (audit / "m001_s01_first_slice_rr.md").write_text(
                "# Review\n\n## Verdict\n\npass\n", encoding="utf-8"
            )
            (audit / "m001_s01_first_slice_vr.md").write_text(
                "# Verdict Record\n\n## Source\n\n"
                "Review report: `05_governance/audit/m001_s01_first_slice_rr.md`\n",
                encoding="utf-8",
            )
            resume = _resume(root)
            self.assertNotIn(
                resume.step,
                (LoopResumeStep.RECORD_VERDICT, LoopResumeStep.FIX_REVIEW_REPORT),
            )
            self.assertEqual(build_status(root).next_slice.slice_id, "M001-S02")

    def test_missing_invalid_and_non_pass_receipts_contradict_publicly(self) -> None:
        cases = {
            "missing": None,
            "invalid": "# Review\n\nNo verdict section.\n",
            "non_pass": "# Review\n\n## Verdict\n\nneeds_work\n",
        }
        for label, report_bytes in cases.items():
            with self.subTest(label=label), TemporaryDirectory() as tmp:
                root = Path(tmp)
                audit = self._custom_project(root)
                if report_bytes is not None:
                    (audit / "m001_s01_first_slice_rr.md").write_text(
                        report_bytes, encoding="utf-8"
                    )
                (audit / "m001_s01_first_slice_vr.md").write_text(
                    "# Verdict Record\n\n## Source\n\n"
                    "Review report: `05_governance/audit/m001_s01_first_slice_rr.md`\n",
                    encoding="utf-8",
                )
                self.assertEqual(_resume(root).step, LoopResumeStep.FIX_REVIEW_REPORT)
                code, out, _ = _run(["status", str(root), "--json"])
                self.assertEqual(code, 0)
                payload = json.loads(out)
                self.assertEqual(payload["loop_resume"]["step"], "fix_review_report")
                self.assertNotIn("M001-S01", payload["accepted_slice_ids"])

    def test_multiple_configured_reports_deterministic_order(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit = self._custom_project(root)
            (audit / "m001_s02_second_slice_rr.md").write_text(
                "# Review\n\n## Verdict\n\npass\n", encoding="utf-8"
            )
            (audit / "m001_s01_first_slice_rr.md").write_text(
                "# Review\n\n## Verdict\n\npass\n", encoding="utf-8"
            )
            evidence = _evidence(root)
            self.assertEqual(
                evidence.unrecorded_pass_reports,
                (
                    "05_governance/audit/m001_s01_first_slice_rr.md",
                    "05_governance/audit/m001_s02_second_slice_rr.md",
                ),
            )
            resume = _resume(root)
            self.assertEqual(
                resume.review_report_path, "05_governance/audit/m001_s01_first_slice_rr.md"
            )

    def test_legacy_directory_decoys_have_no_influence(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit = self._custom_project(root)
            (audit / "m001_s01_first_slice_rr.md").write_text(
                "# Review\n\n## Verdict\n\npass\n", encoding="utf-8"
            )
            # Legacy-directory decoys: a passing legacy-suffix report and a
            # contradictory legacy record must not affect the configured
            # evidence.
            _write_review_report(root, RR.rsplit("/", 1)[-1], "pass")
            _write_record(root, "# Verdict Record\n")  # bare legacy record
            evidence = _evidence(root)
            self.assertEqual(evidence.contradictions, ())
            self.assertEqual(evidence.accepted_slice_ids, ("M001-S01",))
            self.assertEqual(
                evidence.unrecorded_pass_reports,
                ("05_governance/audit/m001_s01_first_slice_rr.md",),
            )
            self.assertEqual(_resume(root).step, LoopResumeStep.RECORD_VERDICT)


class CommonMarkSourceBoundaryTests(unittest.TestCase):
    """F2: the Source reader obeys the accepted CommonMark fence/indentation
    boundary — no second Markdown dialect."""

    def _run_case(self, source_body: str) -> tuple:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            _write_review_report(root, RR.rsplit("/", 1)[-1], "pass")
            _write_review_report(root, "m001_s02_second_slice_review_report.md", "pass")
            _write_record(
                root,
                "# Verdict Record: M001-S01\n\n## Source\n\n" + source_body,
            )
            evidence = _evidence(root)
        return evidence.contradictions

    def test_fence_matrix_decoys_inert_and_live_citation_pairs(self) -> None:
        decoy = "Review report: `05_governance/reviews/m001_s02_second_slice_review_report.md`"
        live = f"Review report: `{RR}`"
        valid_cases = {
            "backtick-3": f"```\n{decoy}\n```\n\n{live}\n",
            "backtick-4": f"````\n{decoy}\n````\n\n{live}\n",
            "backtick-6": f"``````\n{decoy}\n``````\n\n{live}\n",
            "tilde-3": f"~~~\n{decoy}\n~~~\n\n{live}\n",
            "tilde-4": f"~~~~\n{decoy}\n~~~~\n\n{live}\n",
            "tilde-6": f"~~~~~~\n{decoy}\n~~~~~~\n\n{live}\n",
            "info-string": f"```yaml\n{decoy}\n```\n\n{live}\n",
            "tilde-info": f"~~~text info\n{decoy}\n~~~\n\n{live}\n",
            "longer-closer": f"```\n{decoy}\n`````\n\n{live}\n",
            "indented-1": f" ```\n{decoy}\n ```\n\n{live}\n",
            "indented-3": f"   ```\n{decoy}\n   ```\n\n{live}\n",
            "shorter-run-inside": f"`````\n```\n{decoy}\n`````\n\n{live}\n",
        }
        for label, body in valid_cases.items():
            with self.subTest(label=label):
                self.assertEqual(self._run_case(body), ())

    def test_malformed_fences_cannot_hide_or_expose(self) -> None:
        decoy = "Review report: `05_governance/reviews/m001_s02_second_slice_review_report.md`"
        live = f"Review report: `{RR}`"
        contradiction_cases = {
            # shorter closer does not close: the live-looking citation stays
            # inside the unclosed fence and is inert -> missing source
            "shorter-closer": ("```\n" + decoy + "\n``\n\n" + live + "\n", "missing_source"),
            # unclosed fence: remainder inert -> missing source
            "unclosed": ("```\n" + decoy + "\n" + live + "\n", "missing_source"),
            # four-space-indented opener is code, not a fence: the decoy after
            # it is live and is the first citation -> different slice
            "indented-opener": ("    ```\n" + decoy + "\n", "different_slice"),
            # tab-indented opener is code as well
            "tab-opener": ("\t```\n" + decoy + "\n", "different_slice"),
            # four-space-indented heading: no live Source at all
            "indented-heading": ("", "missing_source"),
        }
        for label, (body, kind) in contradiction_cases.items():
            with self.subTest(label=label):
                if label == "indented-heading":
                    with TemporaryDirectory() as tmp:
                        root = Path(tmp)
                        _in_flight_project(root)
                        _write_review_report(root, RR.rsplit("/", 1)[-1], "pass")
                        _write_record(
                            root,
                            "# Verdict Record: M001-S01\n\n    ## Source\n\n"
                            f"    Review report: `{RR}`\n",
                        )
                        contradictions = _evidence(root).contradictions
                else:
                    contradictions = self._run_case(body)
                self.assertEqual(len(contradictions), 1)
                self.assertEqual(contradictions[0].kind.value, kind)

    def test_tab_indented_citation_is_inert(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            _write_review_report(root, RR.rsplit("/", 1)[-1], "pass")
            _write_record(
                root,
                "# Verdict Record: M001-S01\n\n## Source\n\n"
                f"\tReview report: `{RR}`\n",
            )
            contradiction = _evidence(root).contradictions
            self.assertEqual(len(contradiction), 1)
            self.assertEqual(contradiction[0].kind, _RecordContradictionKind.MISSING_SOURCE)

    def test_duplicate_heading_inside_fence_is_inert(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            _write_review_report(root, RR.rsplit("/", 1)[-1], "pass")
            _write_record(
                root,
                "# Verdict Record: M001-S01\n\n## Source\n\n"
                f"Review report: `{RR}`\n\n"
                "```\n## Source\n```\n",
            )
            self.assertEqual(_evidence(root).contradictions, ())

    def test_section_ends_at_next_live_h2_but_not_fenced_h2(self) -> None:
        decoy = "Review report: `05_governance/reviews/m001_s02_second_slice_review_report.md`"
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            _write_review_report(root, RR.rsplit("/", 1)[-1], "pass")
            _write_record(
                root,
                "# Verdict Record: M001-S01\n\n## Source\n\n"
                f"Review report: `{RR}`\n\n"
                "## Parsed Verdict\n\nVerdict: `pass`\n\n"
                f"```\n## Source\n{decoy}\n```\n",
            )
            self.assertEqual(_evidence(root).contradictions, ())


class ResolvedContainmentTests(unittest.TestCase):
    """F3: resolved containment is enforced before authority bytes are read."""

    def test_reviews_directory_junction_escape_is_root_defect_without_enumeration(
        self,
    ) -> None:
        # Prompt 031 (Review 030 finding 5): an escaped configured reviews
        # root is one typed root-level defect; its children — including a
        # record citing a real report — are never enumerated into evidence
        # identities or contradictions.
        import os
        import subprocess

        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            _make_template(root)
            _write_active_roadmap(root, _active_roadmap())
            _write_detailed_roadmap(root, _detailed_roadmap())
            outside = Path(tmp) / "outside"
            outside.mkdir()
            (outside / "m001_s01_first_slice_review_report.md").write_text(
                "# Review\n\n## Verdict\n\npass\n", encoding="utf-8"
            )
            (outside / "m001_s01_first_slice_verdict_record.md").write_text(
                "# Verdict Record: M001-S01\n\n## Source\n\n"
                f"Review report: `{RR}`\n",
                encoding="utf-8",
            )
            reviews = root / "05_governance" / "reviews"
            reviews.rmdir()
            try:
                os.symlink(outside, reviews, target_is_directory=True)
            except OSError:
                # Directory junctions need no privilege on Windows; both
                # exercise the same resolved-outside containment.
                result = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(reviews), str(outside)],
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            evidence = _evidence(root)
            self.assertEqual(evidence.accepted_slice_ids, ())
            self.assertEqual(evidence.pass_reports, ())
            self.assertEqual(evidence.contradictions, ())
            self.assertEqual(
                [defect.kind for defect in evidence.authority_defects],
                [_AuthorityDefectKind.ESCAPED_AUTHORITY_ROOT],
            )
            defect = evidence.authority_defects[0]
            self.assertEqual(defect.authority_path, "05_governance/reviews")
            self.assertLessEqual(len(defect.diagnostic), 240)
            self.assertNotIn("m001_s01_first_slice_verdict_record", defect.diagnostic)

    def test_injected_resolve_escape_never_parses_or_enumerates(self) -> None:
        # Deterministic zero-skip injection: root containment reports escape,
        # so no report verdict bytes may be parsed, no slice is accepted, and
        # no child artifact is enumerated into typed identities.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            _write_review_report(root, RR.rsplit("/", 1)[-1], "pass")
            _write_record(root, _generated_record(RR))
            import frutlups.project as project_module

            with (
                mock.patch.object(
                    project_module, "_is_within", return_value=False
                ),
                mock.patch.object(
                    project_module, "parse_review_report_verdict"
                ) as parse_spy,
            ):
                evidence = _evidence(root)
            self.assertEqual(evidence.accepted_slice_ids, ())
            self.assertEqual(evidence.contradictions, ())
            self.assertEqual(
                [defect.kind for defect in evidence.authority_defects],
                [_AuthorityDefectKind.ESCAPED_AUTHORITY_ROOT],
            )
            parse_spy.assert_not_called()


def _make_reviews_junction(test: unittest.TestCase, root: Path, outside: Path) -> None:
    """Replace the reviews directory with a real resolved escape to ``outside``."""

    import os
    import subprocess

    reviews = root / "05_governance" / "reviews"
    if reviews.is_dir():
        reviews.rmdir()
    try:
        os.symlink(outside, reviews, target_is_directory=True)
    except OSError:
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(reviews), str(outside)],
            capture_output=True,
        )
        test.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))


class AuthorityRootDefectTests(unittest.TestCase):
    """Prompt 030 Phase B (Review 029 P1): an escaped or unresolvable configured
    reviews authority root, and an escaped suffix-matching report, are typed
    fail-closed ``authority_defects`` independently of verdict-record iteration;
    resolution failures never raise and never echo unsafe values."""

    def test_empty_escaped_reviews_directory_is_typed_defect(self) -> None:
        # Review 029 counterexample 2: the escaped configured directory is
        # empty; previously this produced ordinary empty evidence.
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            _make_template(root)
            _write_active_roadmap(root, _active_roadmap())
            _write_detailed_roadmap(root, _detailed_roadmap())
            outside = Path(tmp) / "outside"
            outside.mkdir()
            _make_reviews_junction(self, root, outside)
            evidence = _evidence(root)
            self.assertEqual(evidence.accepted_slice_ids, ())
            self.assertEqual(evidence.pass_reports, ())
            self.assertEqual(evidence.contradictions, ())
            self.assertEqual(len(evidence.authority_defects), 1)
            defect = evidence.authority_defects[0]
            self.assertEqual(defect.kind, _AuthorityDefectKind.ESCAPED_AUTHORITY_ROOT)
            self.assertEqual(defect.authority_path, "05_governance/reviews")
            self.assertLessEqual(len(defect.diagnostic), 240)
            self.assertNotIn(tmp, defect.diagnostic)
            self.assertNotIn(str(outside), defect.diagnostic)

    def test_escaped_directory_with_passing_report_and_no_record(self) -> None:
        # Review 029 counterexample 1: a canonical passing report exists in the
        # escaped directory but no verdict record cites it. The defect must be
        # typed and the external report's verdict bytes must never be parsed.
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            _make_template(root)
            _write_active_roadmap(root, _active_roadmap())
            _write_detailed_roadmap(root, _detailed_roadmap())
            outside = Path(tmp) / "outside"
            outside.mkdir()
            (outside / "m001_s01_first_slice_review_report.md").write_text(
                "# Review\n\n## Verdict\n\npass\n", encoding="utf-8"
            )
            _make_reviews_junction(self, root, outside)
            import frutlups.project as project_module

            with mock.patch.object(
                project_module, "parse_review_report_verdict"
            ) as parse_spy:
                evidence = _collect_acceptance_evidence(root, legacy_profile())
            parse_spy.assert_not_called()
            self.assertEqual(evidence.accepted_slice_ids, ())
            self.assertEqual(evidence.pass_reports, ())
            self.assertEqual(evidence.contradictions, ())
            self.assertEqual(
                [defect.kind for defect in evidence.authority_defects],
                [_AuthorityDefectKind.ESCAPED_AUTHORITY_ROOT],
            )

    def test_escaped_report_without_record_is_typed_defect(self) -> None:
        # Review 029 counterexample 3: the reviews directory is contained but a
        # suffix-matching report resolves outside it, and no record cites it.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            _write_review_report(root, RR.rsplit("/", 1)[-1], "pass")
            import frutlups.project as project_module

            real_is_within = project_module._is_within

            def _escape_only_report(child: Path, parent: Path) -> bool:
                if child.name == RR.rsplit("/", 1)[-1]:
                    return False
                return real_is_within(child, parent)

            with (
                mock.patch.object(
                    project_module, "_is_within", side_effect=_escape_only_report
                ),
                mock.patch.object(
                    project_module, "parse_review_report_verdict"
                ) as parse_spy,
            ):
                evidence = _collect_acceptance_evidence(root, legacy_profile())
            parse_spy.assert_not_called()
            self.assertEqual(evidence.accepted_slice_ids, ())
            self.assertEqual(evidence.pass_reports, ())
            self.assertEqual(evidence.contradictions, ())
            self.assertEqual(len(evidence.authority_defects), 1)
            defect = evidence.authority_defects[0]
            self.assertEqual(defect.kind, _AuthorityDefectKind.ESCAPED_AUTHORITY_REPORT)
            self.assertEqual(defect.authority_path, RR)
            self.assertLessEqual(len(defect.diagnostic), 240)
            self.assertNotIn(tmp, defect.diagnostic)

    def test_outer_resolution_failure_is_typed_and_never_raises(self) -> None:
        # Review 029 counterexample 4: Path.resolve raising the non-OSError
        # loop class previously leaked from the public collector.
        for failure in (RuntimeError("Symlink loop hostile-value"), OSError(22, "bad")):
            with self.subTest(failure=type(failure).__name__):
                with TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    _in_flight_project(root)
                    _write_review_report(root, RR.rsplit("/", 1)[-1], "pass")

                    def _raise(self_path, strict=False):
                        raise failure

                    with mock.patch.object(Path, "resolve", _raise):
                        evidence = _collect_acceptance_evidence(root, legacy_profile())
                    self.assertEqual(evidence.accepted_slice_ids, ())
                    self.assertEqual(evidence.contradictions, ())
                    self.assertEqual(len(evidence.authority_defects), 1)
                    defect = evidence.authority_defects[0]
                    self.assertEqual(
                        defect.kind, _AuthorityDefectKind.UNRESOLVABLE_AUTHORITY_ROOT
                    )
                    self.assertEqual(defect.authority_path, "05_governance/reviews")
                    self.assertLessEqual(len(defect.diagnostic), 240)
                    self.assertNotIn("RuntimeError", defect.diagnostic)
                    self.assertNotIn("Symlink loop", defect.diagnostic)
                    self.assertNotIn("hostile", defect.diagnostic)
                    self.assertNotIn(tmp, defect.diagnostic)

    def test_inner_resolution_failure_stays_typed_and_never_raises(self) -> None:
        # A per-artifact resolution loop (root and reviews resolve cleanly)
        # must become typed state: the report is an escaped-report defect and a
        # record still produces its accepted RESOLVED_ESCAPE contradiction.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            _write_review_report(root, RR.rsplit("/", 1)[-1], "pass")
            _write_record(root, _generated_record(RR))
            real_resolve = Path.resolve
            calls = {"n": 0}

            def _raise_after_two(self_path, strict=False):
                calls["n"] += 1
                if calls["n"] <= 2:
                    return real_resolve(self_path, strict)
                raise RuntimeError("Symlink loop")

            with mock.patch.object(Path, "resolve", _raise_after_two):
                evidence = _collect_acceptance_evidence(root, legacy_profile())
            self.assertEqual(evidence.accepted_slice_ids, ())
            self.assertEqual(
                [defect.kind for defect in evidence.authority_defects],
                [_AuthorityDefectKind.ESCAPED_AUTHORITY_REPORT],
            )
            self.assertEqual(
                [c.kind for c in evidence.contradictions],
                [_RecordContradictionKind.RESOLVED_ESCAPE],
            )

    def test_repeated_calls_are_pure(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            _make_template(root)
            _write_active_roadmap(root, _active_roadmap())
            _write_detailed_roadmap(root, _detailed_roadmap())
            outside = Path(tmp) / "outside"
            outside.mkdir()
            (outside / "m001_s01_first_slice_review_report.md").write_text(
                "# Review\n\n## Verdict\n\npass\n", encoding="utf-8"
            )
            _make_reviews_junction(self, root, outside)
            first = _collect_acceptance_evidence(root, legacy_profile())
            second = _collect_acceptance_evidence(root, legacy_profile())
            self.assertEqual(first, second)

    def test_absent_reviews_directory_keeps_accepted_semantics(self) -> None:
        # Control: an ordinarily absent reviews directory stays ordinary empty
        # evidence with no authority defect (no new layout rule).
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            _make_template(root)
            _write_active_roadmap(root, _active_roadmap())
            _write_detailed_roadmap(root, _detailed_roadmap())
            reviews = root / "05_governance" / "reviews"
            if reviews.is_dir():
                reviews.rmdir()
            evidence = _collect_acceptance_evidence(root, legacy_profile())
            self.assertEqual(evidence.accepted_slice_ids, ())
            self.assertEqual(evidence.contradictions, ())
            self.assertEqual(evidence.authority_defects, ())

    def test_contained_normal_evidence_has_no_defect(self) -> None:
        # Control: the accepted normal chain is unchanged by the correction.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            _write_review_report(root, RR.rsplit("/", 1)[-1], "pass")
            _write_record(root, _generated_record(RR))
            evidence = _evidence(root)
            self.assertEqual(evidence.accepted_slice_ids, ("M001-S01",))
            self.assertEqual(evidence.contradictions, ())
            self.assertEqual(evidence.authority_defects, ())


class AuthorityDefectSurfaceTests(unittest.TestCase):
    """The typed authority defect preempts every normal step and write."""

    def _escaped_project(self, tmp: str) -> Path:
        root = Path(tmp) / "project"
        _make_template(root)
        _write_active_roadmap(root, _active_roadmap())
        _write_detailed_roadmap(root, _detailed_roadmap())
        outside = Path(tmp) / "outside"
        outside.mkdir()
        (outside / "m001_s01_first_slice_review_report.md").write_text(
            "# Review\n\n## Verdict\n\npass\n", encoding="utf-8"
        )
        _make_reviews_junction(self, root, outside)
        return root

    def test_resume_fails_closed_ahead_of_normal_work(self) -> None:
        with TemporaryDirectory() as tmp:
            root = self._escaped_project(tmp)
            resume = _resume(root)
            self.assertEqual(resume.step, LoopResumeStep.FIX_REVIEW_REPORT)
            self.assertEqual(resume.next_command, "")
            self.assertIn("acceptance authority defect", resume.message)
            self.assertIn("05_governance/reviews", resume.message)
            self.assertNotIn(tmp, resume.message)
            self.assertTrue(
                any("acceptance authority defect" in diag for diag in resume.diagnostics)
            )

    def test_gate_and_plan_fail_closed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = self._escaped_project(tmp)
            plan = build_orchestrator_plan(root)
            self.assertEqual(plan.loop_step, LoopResumeStep.FIX_REVIEW_REPORT.value)
            self.assertFalse(plan.safe_for_auto_execution)
            gate = build_human_gate(root)
            self.assertEqual(gate.gate_state, HumanGateState.STOP.value)
            self.assertTrue(gate.requires_human_go)

    def test_runner_refuses_and_writers_unreachable_with_full_snapshot(self) -> None:
        with TemporaryDirectory() as tmp:
            root = self._escaped_project(tmp)
            _write_supported_posture(root)
            import frutlups.orchestrator as orchestrator_module

            before = _snapshot(root)
            with (
                mock.patch.object(
                    orchestrator_module,
                    "write_coding_prompt",
                    side_effect=AssertionError("writer must be unreachable"),
                ),
                mock.patch.object(
                    orchestrator_module,
                    "_write_review_prompt_content",
                    side_effect=AssertionError("writer must be unreachable"),
                ),
                mock.patch.object(
                    orchestrator_module,
                    "write_verdict_record",
                    side_effect=AssertionError("writer must be unreachable"),
                ),
            ):
                result = run_one_step(root, dry_run=False, journal=False)
            self.assertTrue(result.refused)
            self.assertFalse(result.wrote)
            self.assertEqual(_snapshot(root), before)

    def test_direct_text_json_surfaces_are_read_only(self) -> None:
        with TemporaryDirectory() as tmp:
            root = self._escaped_project(tmp)
            before = _snapshot(root)
            evidence = _collect_acceptance_evidence(root, legacy_profile())
            self.assertEqual(len(evidence.authority_defects), 1)
            for args in (
                ["status", str(root)],
                ["status", str(root), "--json"],
                ["next", str(root)],
                ["next", str(root), "--json"],
                ["orchestrator-plan", str(root)],
                ["orchestrator-plan", str(root), "--json"],
            ):
                code, out, _err = _run(args)
                self.assertEqual(code, 0, args)
                if "--json" in args:
                    json.loads(out)
            self.assertEqual(_snapshot(root), before)
            journal = read_run_journal(journal_path_for(root))
            self.assertEqual(journal.entries, ())


class SuffixOverlapAuthorityTests(unittest.TestCase):
    """Phase D repair: a verdict record can never enter the acceptance-authority
    report scan, even under overlapping configured suffixes (Decision 5)."""

    def _overlap_profile(self):
        import dataclasses

        return dataclasses.replace(
            legacy_profile(),
            review_report_suffix="_report.md",
            verdict_record_suffix="_verdict_report.md",
        )

    def test_record_alone_cannot_accept_under_overlapping_suffixes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            reviews = root / "05_governance" / "reviews"
            reviews.mkdir(parents=True)
            (reviews / "m001_s01_first_slice_verdict_report.md").write_text(
                "# Verdict Record\n\n## Verdict\n\npass\n\n## Source\n\nnone\n",
                encoding="utf-8",
            )
            evidence = _collect_acceptance_evidence(root, self._overlap_profile())
            self.assertEqual(evidence.accepted_slice_ids, ())
            self.assertEqual(evidence.pass_reports, ())
            self.assertEqual(
                [c.kind for c in evidence.contradictions],
                [_RecordContradictionKind.MISSING_SOURCE],
            )
            self.assertEqual(evidence.authority_defects, ())

    def test_true_report_still_accepts_under_overlapping_suffixes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            reviews = root / "05_governance" / "reviews"
            reviews.mkdir(parents=True)
            (reviews / "m001_s01_first_slice_report.md").write_text(
                "# Review\n\n## Verdict\n\npass\n", encoding="utf-8"
            )
            evidence = _collect_acceptance_evidence(root, self._overlap_profile())
            self.assertEqual(evidence.accepted_slice_ids, ("M001-S01",))

    def test_identical_suffixes_fail_closed_without_acceptance(self) -> None:
        import dataclasses

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            reviews = root / "05_governance" / "reviews"
            reviews.mkdir(parents=True)
            (reviews / "m001_s01_first_slice_same.md").write_text(
                "# Anything\n\n## Verdict\n\npass\n", encoding="utf-8"
            )
            profile = dataclasses.replace(
                legacy_profile(),
                review_report_suffix="_same.md",
                verdict_record_suffix="_same.md",
            )
            evidence = _collect_acceptance_evidence(root, profile)
            self.assertEqual(evidence.accepted_slice_ids, ())
            self.assertEqual(evidence.pass_reports, ())
            self.assertEqual(len(evidence.contradictions), 1)

    def test_default_suffixes_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            _write_review_report(root, RR.rsplit("/", 1)[-1], "pass")
            _write_record(root, _generated_record(RR))
            evidence = _evidence(root)
            self.assertEqual(evidence.accepted_slice_ids, ("M001-S01",))
            self.assertEqual(evidence.contradictions, ())

    def test_mixed_case_record_never_accepts_under_overlapping_suffixes(self) -> None:
        # Review 030 finding 4: the exact reviewer counterexample. A record
        # file in any case combination classifies as a record and can never
        # enter accepted IDs or pass reports.
        for name in (
            "m001_s01_only_VERDICT_REPORT.MD",
            "m001_s01_only_verdict_report.MD",
            "M001_S01_ONLY_VERDICT_REPORT.md",
            "m001_s01_only_Verdict_Report.Md",
        ):
            with self.subTest(name=name):
                with TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    reviews = root / "05_governance" / "reviews"
                    reviews.mkdir(parents=True)
                    (reviews / name).write_text(
                        "# Verdict Record\n\n## Verdict\n\npass\n", encoding="utf-8"
                    )
                    evidence = _collect_acceptance_evidence(
                        root, self._overlap_profile()
                    )
                    self.assertEqual(evidence.accepted_slice_ids, ())
                    self.assertEqual(evidence.pass_reports, ())
                    self.assertEqual(
                        [c.kind for c in evidence.contradictions],
                        [_RecordContradictionKind.MISSING_SOURCE],
                    )

    def test_mixed_case_record_suffix_classification_under_defaults(self) -> None:
        # Default disjoint suffixes: a differently cased record filename still
        # classifies as a record (deterministic casefold, not host case rules).
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            reviews = root / "05_governance" / "reviews"
            reviews.mkdir(parents=True)
            (reviews / "m001_s01_first_slice_VERDICT_RECORD.MD").write_text(
                "# Verdict Record\n\n## Verdict\n\npass\n", encoding="utf-8"
            )
            evidence = _collect_acceptance_evidence(root, legacy_profile())
            self.assertEqual(evidence.accepted_slice_ids, ())
            self.assertEqual(evidence.pass_reports, ())
            self.assertEqual(len(evidence.contradictions), 1)

    def test_mixed_case_report_still_accepts_under_defaults(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            reviews = root / "05_governance" / "reviews"
            reviews.mkdir(parents=True)
            (reviews / "m001_s01_first_slice_REVIEW_REPORT.MD").write_text(
                "# Review\n\n## Verdict\n\npass\n", encoding="utf-8"
            )
            evidence = _collect_acceptance_evidence(root, legacy_profile())
            self.assertEqual(evidence.accepted_slice_ids, ("M001-S01",))

    def test_reverse_overlap_report_classified_as_record_fails_closed(self) -> None:
        # Reverse overlap: the report suffix ends with the record suffix, so
        # every report classifies as a record and acceptance is impossible
        # (fail-closed, never a record-derived acceptance).
        import dataclasses

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            reviews = root / "05_governance" / "reviews"
            reviews.mkdir(parents=True)
            (reviews / "m001_s01_only_special_record.md").write_text(
                "# Review\n\n## Verdict\n\npass\n", encoding="utf-8"
            )
            profile = dataclasses.replace(
                legacy_profile(),
                review_report_suffix="_special_record.md",
                verdict_record_suffix="_record.md",
            )
            evidence = _collect_acceptance_evidence(root, profile)
            self.assertEqual(evidence.accepted_slice_ids, ())
            self.assertEqual(evidence.pass_reports, ())

    def test_mixed_case_generated_paths_preserve_physical_suffixes(self) -> None:
        # Normalization is classification only: expected-report labels keep
        # the selected profile's physical suffix bytes.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            reviews = root / "05_governance" / "reviews"
            reviews.mkdir(parents=True)
            (reviews / "m001_s01_first_slice_VERDICT_RECORD.MD").write_text(
                "# Verdict Record\n", encoding="utf-8"
            )
            evidence = _collect_acceptance_evidence(root, legacy_profile())
            self.assertEqual(len(evidence.contradictions), 1)
            self.assertTrue(
                evidence.contradictions[0].report_path.endswith("_review_report.md")
            )


class EscapedRootNonEnumerationTests(unittest.TestCase):
    """Prompt 031 (Review 030 finding 5): an escaped authority root emits one
    safe root-level defect; external child names never reach any surface."""

    _HOSTILE_RECORD = "m001_s01_SECRET_TOKEN_947_verdict_record.md"
    _HOSTILE_REPORT = "m001_s01_EVIL_NAME_313_review_report.md"

    def _escaped_project(self, tmp: str, children: tuple[str, ...]) -> Path:
        root = Path(tmp) / "project"
        _make_template(root)
        _write_active_roadmap(root, _active_roadmap())
        _write_detailed_roadmap(root, _detailed_roadmap())
        outside = Path(tmp) / "outside"
        outside.mkdir()
        for name in children:
            body = (
                "# Review\n\n## Verdict\n\npass\n"
                if name.endswith("_review_report.md")
                else f"# Verdict Record\n\n## Source\n\nReview report: `{RR}`\n"
            )
            (outside / name).write_text(body, encoding="utf-8")
        _make_reviews_junction(self, root, outside)
        return root

    def _assert_no_hostile(self, *texts: str) -> None:
        for text in texts:
            self.assertNotIn("SECRET_TOKEN_947", text)
            self.assertNotIn("EVIL_NAME_313", text)

    def test_zero_children_yields_only_the_root_defect(self) -> None:
        with TemporaryDirectory() as tmp:
            root = self._escaped_project(tmp, ())
            evidence = _collect_acceptance_evidence(root, legacy_profile())
            self.assertEqual(
                [defect.kind for defect in evidence.authority_defects],
                [_AuthorityDefectKind.ESCAPED_AUTHORITY_ROOT],
            )
            self.assertEqual(evidence.contradictions, ())

    def test_hostile_children_never_reach_any_surface(self) -> None:
        with TemporaryDirectory() as tmp:
            root = self._escaped_project(
                tmp, (self._HOSTILE_RECORD, self._HOSTILE_REPORT)
            )
            import frutlups.project as project_module

            with (
                mock.patch.object(
                    project_module, "parse_review_report_verdict"
                ) as parse_spy,
                mock.patch.object(
                    project_module, "_read_record_evidence"
                ) as read_spy,
            ):
                evidence = _collect_acceptance_evidence(root, legacy_profile())
            parse_spy.assert_not_called()
            read_spy.assert_not_called()
            self.assertEqual(evidence.contradictions, ())
            self.assertEqual(evidence.pass_reports, ())
            self.assertEqual(
                [defect.kind for defect in evidence.authority_defects],
                [_AuthorityDefectKind.ESCAPED_AUTHORITY_ROOT],
            )
            self._assert_no_hostile(
                *[defect.diagnostic for defect in evidence.authority_defects],
                *[defect.authority_path for defect in evidence.authority_defects],
            )

            resume = _resume(root)
            self._assert_no_hostile(resume.message, *resume.diagnostics)
            self.assertEqual(resume.step, LoopResumeStep.FIX_REVIEW_REPORT)
            self.assertEqual(resume.next_command, "")

            code, out, err = _run(["status", str(root), "--json"])
            self.assertEqual(code, 0)
            self._assert_no_hostile(out, err)
            payload = json.loads(out)
            self.assertEqual(payload["planning_frontier"]["outcome"], "invalid")

            from frutlups.gate import build_human_gate
            gate = build_human_gate(root)
            self._assert_no_hostile(json.dumps(gate.to_dict()))
            self.assertEqual(gate.gate_state, HumanGateState.STOP.value)

            code, out, err = _run(["orchestrator-plan", str(root), "--json"])
            self.assertEqual(code, 0)
            self._assert_no_hostile(out, err)

    def test_citing_record_beneath_escaped_root_is_not_enumerated(self) -> None:
        with TemporaryDirectory() as tmp:
            root = self._escaped_project(
                tmp,
                (
                    "m001_s01_first_slice_review_report.md",
                    "m001_s01_first_slice_verdict_record.md",
                ),
            )
            _write_supported_posture(root)
            import frutlups.orchestrator as orchestrator_module

            before = _snapshot(root)
            with mock.patch.object(
                orchestrator_module,
                "write_verdict_record",
                side_effect=AssertionError("writer must be unreachable"),
            ):
                result = run_one_step(root, dry_run=False, journal=False)
            self.assertTrue(result.refused)
            self.assertEqual(_snapshot(root), before)
            journal = read_run_journal(journal_path_for(root))
            self.assertEqual(journal.entries, ())


class SingleSnapshotCompositionTests(unittest.TestCase):
    """Prompt 031 (Review 030 finding 3): every public path-based composition
    selects acceptance evidence exactly once, and one emitted composite
    response never combines two evidence snapshots."""

    def _project(self, tmp: str) -> Path:
        root = Path(tmp)
        _in_flight_project(root)
        return root

    def _count_scans(self, action) -> int:
        import frutlups.project as project_module

        with mock.patch.object(
            project_module,
            "_collect_acceptance_evidence",
            wraps=project_module._collect_acceptance_evidence,
        ) as spy:
            action()
        return spy.call_count

    def test_direct_compositions_scan_exactly_once(self) -> None:
        from frutlups.gate import (
            build_human_gate,
            build_planning_frontier_status,
            decide_planning_frontier_step,
        )
        from frutlups.orchestrator import build_orchestrator_plan, run_one_step
        from frutlups.project import build_loop_resume_status

        cases = {
            "build_status": lambda root: build_status(root),
            "build_loop_resume_status": lambda root: build_loop_resume_status(root),
            "build_planning_frontier_status": lambda root: build_planning_frontier_status(root),
            "build_human_gate": lambda root: build_human_gate(root),
            "build_orchestrator_plan": lambda root: build_orchestrator_plan(root),
            "run_one_step_dry": lambda root: run_one_step(root, dry_run=True, journal=False),
            "decide_planning_frontier_step": lambda root: decide_planning_frontier_step(root),
        }
        for name, action in cases.items():
            with self.subTest(composition=name):
                with TemporaryDirectory() as tmp:
                    root = self._project(tmp)
                    self.assertEqual(self._count_scans(lambda: action(root)), 1)

    def test_cli_compositions_scan_exactly_once(self) -> None:
        for args in (
            ["status", "{root}"],
            ["status", "{root}", "--json"],
            ["next", "{root}"],
            ["orchestrator-plan", "{root}"],
            ["orchestrator-plan", "{root}", "--json"],
            ["orchestrator-run", "{root}", "--dry-run"],
            ["orchestrator-handoff", "{root}"],
        ):
            with self.subTest(args=args[0:1] + args[2:]):
                with TemporaryDirectory() as tmp:
                    root = self._project(tmp)
                    argv = [arg.replace("{root}", str(root)) for arg in args]
                    self.assertEqual(self._count_scans(lambda: _run(argv)), 1)

    def test_record_verdict_cli_scans_exactly_once(self) -> None:
        with TemporaryDirectory() as tmp:
            root = self._project(tmp)
            _write_review_report(root, RR.rsplit("/", 1)[-1], "pass")
            argv = [
                "record-verdict",
                str(root),
                "--review-report",
                str(root / RR),
                "--dry-run",
            ]
            self.assertEqual(self._count_scans(lambda: _run(argv)), 1)

    def test_explicit_layout_config_is_selected_once(self) -> None:
        with TemporaryDirectory() as tmp:
            root = self._project(tmp)
            config = root / "explicit.layout.yaml"
            config.write_text(_SUPPORTED_POSTURE, encoding="utf-8")
            argv = ["status", str(root), "--layout-config", str(config), "--json"]
            self.assertEqual(self._count_scans(lambda: _run(argv)), 1)

    def test_threaded_evidence_object_identity(self) -> None:
        import frutlups.project as project_module

        with TemporaryDirectory() as tmp:
            root = self._project(tmp)
            status, evidence = project_module._build_status_with_evidence(root)
            resume, _verdict, used = project_module._loop_resume_with_verdict_and_evidence(
                status, evidence=evidence
            )
            self.assertIs(used, evidence)
            self.assertEqual(status.accepted_slice_ids, evidence.accepted_slice_ids)

    def test_mutated_second_scan_cannot_split_the_response(self) -> None:
        # Both evidence-mutation directions: with one scan, a differing
        # would-be second scan can never reach the same composite response.
        import frutlups.project as project_module
        from frutlups.gate import _build_status_resume_and_frontier

        empty = project_module._AcceptanceEvidence((), (), (), ())
        accepted = project_module._AcceptanceEvidence(
            ("M001-S01",),
            (RR,),
            (RR,),
            (),
        )
        for first, second, expect_step in (
            (empty, accepted, "execute_review_prompt"),
            (accepted, empty, "record_verdict"),
        ):
            with self.subTest(first_accepted=bool(first.accepted_slice_ids)):
                with TemporaryDirectory() as tmp:
                    root = self._project(tmp)
                    with mock.patch.object(
                        project_module,
                        "_collect_acceptance_evidence",
                        side_effect=[first, second],
                    ) as spy:
                        status, resume, frontier = _build_status_resume_and_frontier(root)
                    self.assertEqual(spy.call_count, 1)
                    self.assertEqual(
                        status.accepted_slice_ids, first.accepted_slice_ids
                    )
                    self.assertEqual(resume.step.value, expect_step)
                    self.assertEqual(frontier.outcome, "ready")

    def test_prebuilt_status_begins_one_fresh_scan(self) -> None:
        # The allowed exception: a deliberately passed public ProjectStatus
        # starts a new read-only resume invocation with exactly one scan.
        with TemporaryDirectory() as tmp:
            root = self._project(tmp)
            status = build_status(root)
            self.assertEqual(
                self._count_scans(lambda: build_loop_resume_status(status)), 1
            )

    def test_handoff_builders_scan_exactly_once(self) -> None:
        # Prompt 032 (Review 031 finding 1): both exported path-based handoff
        # builders are single-scan compositions, for Path and string input.
        from frutlups.handoff import build_coder_handoff, build_reviewer_handoff

        for name, builder in (
            ("coder", build_coder_handoff),
            ("reviewer", build_reviewer_handoff),
        ):
            for kind, as_input in (("Path", Path), ("str", str)):
                with self.subTest(builder=name, input=kind):
                    with TemporaryDirectory() as tmp:
                        root = self._project(tmp)
                        self.assertEqual(
                            self._count_scans(lambda: builder(as_input(root))), 1
                        )


class HandoffSnapshotTests(unittest.TestCase):
    """Prompt 032 (Review 031 finding 1): one selected acceptance snapshot per
    exported handoff composition; a rendered handoff never combines two."""

    def _project(self, tmp: str) -> Path:
        root = Path(tmp)
        _in_flight_project(root)
        _write_review_report(root, RR.rsplit("/", 1)[-1], "pass")
        return root

    def _builders(self):
        from frutlups.handoff import build_coder_handoff, build_reviewer_handoff

        return (("coder", build_coder_handoff), ("reviewer", build_reviewer_handoff))

    def _defect_evidence(self):
        import frutlups.project as project_module

        return project_module._AcceptanceEvidence(
            (),
            (),
            (),
            (),
            (
                project_module._AuthorityDefect(
                    project_module._AuthorityDefectKind.UNRESOLVABLE_AUTHORITY_ROOT,
                    "05_governance/reviews",
                    "acceptance authority defect: configured reviews directory "
                    "05_governance/reviews exists but cannot be safely resolved; "
                    "acceptance evidence is unavailable",
                ),
            ),
        )

    def _accepted_evidence(self):
        import frutlups.project as project_module

        return project_module._AcceptanceEvidence(("M001-S01",), (RR,), (RR,), ())

    def test_selected_evidence_object_identity(self) -> None:
        # The resume consumes the exact evidence object the status selection
        # returned — by identity, not an equal reconstruction.
        import frutlups.project as project_module

        for name, builder in self._builders():
            with self.subTest(builder=name):
                with TemporaryDirectory() as tmp:
                    root = self._project(tmp)
                    collected: list = []
                    received: list = []
                    real_collect = project_module._collect_acceptance_evidence
                    real_resume = project_module._loop_resume_with_verdict_and_evidence

                    def _collect(root_arg, profile):
                        result = real_collect(root_arg, profile)
                        collected.append(result)
                        return result

                    def _resume(status, evidence=None):
                        received.append(evidence)
                        return real_resume(status, evidence=evidence)

                    with (
                        mock.patch.object(
                            project_module,
                            "_collect_acceptance_evidence",
                            side_effect=_collect,
                        ),
                        mock.patch(
                            "frutlups.handoff._loop_resume_with_verdict_and_evidence",
                            side_effect=_resume,
                        ),
                    ):
                        builder(root)
                    self.assertEqual(len(collected), 1)
                    self.assertEqual(len(received), 1)
                    self.assertIs(received[0], collected[0])

    def test_both_mutation_directions_stay_coherent(self) -> None:
        # A would-be second scan with different evidence can never reach the
        # rendered handoff: only the first snapshot governs.
        import frutlups.project as project_module

        accepted = self._accepted_evidence()
        defect = self._defect_evidence()
        for name, builder in self._builders():
            for first, second, expect_defect_resume in (
                (accepted, defect, False),
                (defect, accepted, True),
            ):
                with self.subTest(builder=name, first_defect=bool(first.authority_defects)):
                    with TemporaryDirectory() as tmp:
                        root = self._project(tmp)
                        with mock.patch.object(
                            project_module,
                            "_collect_acceptance_evidence",
                            side_effect=[first, second],
                        ) as spy:
                            handoff = builder(root)
                        self.assertEqual(spy.call_count, 1)
                        self.assertEqual(
                            "fix_review_report" in handoff.content,
                            expect_defect_resume,
                        )

    def test_second_scan_is_unreachable_for_path_input(self) -> None:
        import frutlups.project as project_module

        for name, builder in self._builders():
            with self.subTest(builder=name):
                with TemporaryDirectory() as tmp:
                    root = self._project(tmp)
                    with mock.patch.object(
                        project_module,
                        "_collect_acceptance_evidence",
                        side_effect=[
                            self._accepted_evidence(),
                            AssertionError("second acceptance scan reached"),
                        ],
                    ):
                        handoff = builder(root)
                    self.assertTrue(handoff.content)

    def test_prebuilt_status_begins_exactly_one_fresh_scan(self) -> None:
        import frutlups.project as project_module

        for name, builder in self._builders():
            with self.subTest(builder=name):
                with TemporaryDirectory() as tmp:
                    root = self._project(tmp)
                    status = build_status(root)
                    with mock.patch.object(
                        project_module,
                        "_collect_acceptance_evidence",
                        wraps=project_module._collect_acceptance_evidence,
                    ) as spy:
                        builder(status)
                    self.assertEqual(spy.call_count, 1)

    def test_layout_selected_once_for_path_input(self) -> None:
        import frutlups.layout as layout_module

        for name, builder in self._builders():
            with self.subTest(builder=name):
                with TemporaryDirectory() as tmp:
                    root = self._project(tmp)
                    with mock.patch(
                        "frutlups.project.load_layout_profile",
                        wraps=layout_module.load_layout_profile,
                    ) as spy:
                        builder(root)
                    self.assertEqual(spy.call_count, 1)

    def test_evidence_state_controls_render_normally(self) -> None:
        # Typed-defect, accepted-report, and ordinary-no-evidence controls.
        for name, builder in self._builders():
            with self.subTest(builder=name, control="accepted"):
                with TemporaryDirectory() as tmp:
                    root = self._project(tmp)
                    handoff = builder(root)
                    self.assertNotIn("fix_review_report", handoff.content)
            with self.subTest(builder=name, control="no-evidence"):
                with TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    _in_flight_project(root)
                    handoff = builder(root)
                    self.assertTrue(handoff.content)
            with self.subTest(builder=name, control="authority-defect"):
                with TemporaryDirectory() as tmp:
                    root = self._project(tmp)
                    _write_record(root, _generated_record(RR))
                    import frutlups.project as project_module

                    real_is_within = project_module._is_within

                    def _escape_root(child: Path, parent: Path) -> bool:
                        if child.name == "reviews":
                            return False
                        return real_is_within(child, parent)

                    with mock.patch.object(
                        project_module, "_is_within", side_effect=_escape_root
                    ):
                        handoff = builder(root)
                    self.assertIn("fix_review_report", handoff.content)

    def test_read_only_snapshot_and_no_journal(self) -> None:
        for name, builder in self._builders():
            with self.subTest(builder=name):
                with TemporaryDirectory() as tmp:
                    root = self._project(tmp)
                    before = _snapshot(root)
                    builder(root)
                    builder(str(root))
                    self.assertEqual(_snapshot(root), before)
                    journal = read_run_journal(journal_path_for(root))
                    self.assertEqual(journal.entries, ())

    def test_repeated_calls_are_pure(self) -> None:
        for name, builder in self._builders():
            with self.subTest(builder=name):
                with TemporaryDirectory() as tmp:
                    root = self._project(tmp)
                    self.assertEqual(builder(root).content, builder(root).content)


class IdentityMatrixTests(unittest.TestCase):
    """F5: every contradiction kind carries the correct safe identities."""

    def _single(self, report_verdict, record_content, kind, report_path):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            if report_verdict is not None:
                _write_review_report(root, RR.rsplit("/", 1)[-1], report_verdict)
            _write_record(root, record_content)
            evidence = _evidence(root)
            self.assertEqual(len(evidence.contradictions), 1)
            contradiction = evidence.contradictions[0]
            self.assertEqual(contradiction.kind, kind)
            self.assertEqual(contradiction.record_path, VR)
            self.assertEqual(contradiction.report_path, report_path)
            self.assertEqual(contradiction.slice_id, "M001-S01")
            self.assertLessEqual(len(contradiction.diagnostic), 240)
            self.assertIn(VR, contradiction.diagnostic)
            self.assertIn(report_path, contradiction.diagnostic)

    def test_missing_report_names_cited_path(self) -> None:
        cited = "05_governance/reviews/m001_s01_correction_review_report.md"
        self._single(None, _generated_record(cited), _RecordContradictionKind.MISSING_REPORT, cited)

    def test_missing_source_names_expected_path(self) -> None:
        self._single("pass", "# Verdict Record\n", _RecordContradictionKind.MISSING_SOURCE, RR)

    def test_unreadable_record_names_both_identities(self) -> None:
        self._single(
            "pass", b"# Verdict Record\n\xff\xfe", _RecordContradictionKind.UNREADABLE_RECORD, RR
        )

    def test_ambiguous_source_names_expected_path(self) -> None:
        self._single(
            "pass",
            "# Verdict Record: M001-S01\n\n## Source\n\n"
            f"Review report: `{RR}`\n\n## Source\n\nReview report: `{RR}`\n",
            _RecordContradictionKind.AMBIGUOUS_SOURCE,
            RR,
        )

    def test_unsafe_citation_names_expected_not_hostile(self) -> None:
        hostile = "../m001_s01_first_slice_review_report.md"
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            _write_review_report(root, RR.rsplit("/", 1)[-1], "pass")
            _write_record(root, _generated_record(hostile))
            contradiction = _evidence(root).contradictions[0]
            self.assertEqual(contradiction.kind, _RecordContradictionKind.UNSAFE_CITATION)
            self.assertEqual(contradiction.report_path, RR)
            self.assertIn(RR, contradiction.diagnostic)
            self.assertNotIn("..", contradiction.diagnostic)

    def test_unparseable_and_non_pass_name_the_paired_report(self) -> None:
        self._single(None, _generated_record(RR), _RecordContradictionKind.MISSING_REPORT, RR)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            (root / REVIEWS[0] / REVIEWS[1] / RR.rsplit("/", 1)[-1]).write_text(
                "# Review\n\nno verdict\n", encoding="utf-8"
            )
            _write_record(root, _generated_record(RR))
            contradiction = _evidence(root).contradictions[0]
            self.assertEqual(contradiction.kind, _RecordContradictionKind.UNPARSEABLE_REPORT)
            self.assertEqual(contradiction.report_path, RR)
        self._single("needs_work", _generated_record(RR), _RecordContradictionKind.NON_PASS_REPORT, RR)

    def test_different_slice_names_the_actual_cited_report(self) -> None:
        cited = "05_governance/reviews/m001_s02_second_slice_review_report.md"
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            _write_review_report(root, cited.rsplit("/", 1)[-1], "pass")
            _write_record(root, _generated_record(cited))
            contradiction = _evidence(root).contradictions[0]
            self.assertEqual(contradiction.kind, _RecordContradictionKind.DIFFERENT_SLICE)
            self.assertEqual(contradiction.report_path, cited)
            self.assertIn(cited, contradiction.diagnostic)
            self.assertNotIn(RR, contradiction.diagnostic)


class MissingSourceMatrixTests(unittest.TestCase):
    """F4: no live citation is never a receipt, whatever else the record says."""

    def _assert_missing_source(self, record_content: str) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            _write_review_report(root, RR.rsplit("/", 1)[-1], "pass")
            _write_record(root, record_content)
            contradiction = _evidence(root).contradictions
            self.assertEqual(len(contradiction), 1)
            self.assertEqual(contradiction[0].kind, _RecordContradictionKind.MISSING_SOURCE)
            self.assertEqual(_resume(root).step, LoopResumeStep.FIX_REVIEW_REPORT)

    def test_empty_source_section(self) -> None:
        self._assert_missing_source("# Verdict Record: M001-S01\n\n## Source\n\n")

    def test_source_with_only_prose(self) -> None:
        self._assert_missing_source(
            "# Verdict Record: M001-S01\n\n## Source\n\nVerdict: `pass`\n"
        )

    def test_unsafe_first_citation_beats_later_safe_citation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            _write_review_report(root, RR.rsplit("/", 1)[-1], "pass")
            _write_record(
                root,
                "# Verdict Record: M001-S01\n\n## Source\n\n"
                "Review report: `../escape_review_report.md`\n"
                f"Review report: `{RR}`\n",
            )
            contradiction = _evidence(root).contradictions
            self.assertEqual(len(contradiction), 1)
            self.assertEqual(contradiction[0].kind, _RecordContradictionKind.UNSAFE_CITATION)


class PublicSurfaceTests(unittest.TestCase):
    def test_exports_verbs_and_no_new_public_symbols(self) -> None:
        # 134 M003-S05 baseline exports plus the eight M003-S06
        # planning-frontier exports approved and enumerated in
        # 02_analysis/m003_planning_frontier_status_compatibility_record.md.
        self.assertEqual(len(frutlups.__all__), 152)
        for name in (
            "_collect_acceptance_evidence",
            "_AcceptanceEvidence",
            "_VerdictRecordContradiction",
            "_RecordContradictionKind",
            "_read_source_citation",
        ):
            self.assertFalse(hasattr(frutlups, name), name)
        import argparse

        from frutlups.cli import _build_parser

        subparsers = next(
            action
            for action in _build_parser()._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        self.assertEqual(len(subparsers.choices), 8)

    def test_no_s06_fields_in_status_or_next_json(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _in_flight_project(root)
            _write_review_report(root, RR.rsplit("/", 1)[-1], "pass")
            code, out, _ = _run(["status", str(root), "--json"])
            self.assertEqual(code, 0)
            status_payload = json.loads(out)
            code, out, _ = _run(["next", str(root), "--json"])
            self.assertEqual(code, 0)
            next_payload = json.loads(out)
        for payload in (status_payload, next_payload):
            for forbidden in ("version", "outcome", "evidence", "contract", "frontier_state"):
                self.assertNotIn(forbidden, payload)


if __name__ == "__main__":
    unittest.main()
