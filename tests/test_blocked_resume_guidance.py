"""Tests for M011-S04: blocked-state resume guidance.

Covers:
- a blocked verdict / next-action input returns guidance for resuming the same
  slice, not advancing
- guidance includes slice id/title, review report path, verdict, next action,
  human/external input, and a resume checklist
- guidance recommends creating a question artifact for an ambiguous missing
  decision but does not write it automatically
- a supplied suggested question template is valid with the M011-S03 question API
- boundaries against pass recording, roadmap advancement, memory mutation,
  provider hard-coding, and guessing are present
- to_dict() is JSON-serializable plain Python
- building is deterministic and read-only
- malformed / missing optional inputs produce explicit gaps or errors, no raise
- package exports include the blocked guidance API without breaking existing
  handoff and question exports
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from frutlups.blocked_resume import (
    BlockedResumeGuidance,
    build_blocked_resume_guidance,
)
from frutlups.question import (
    QuestionArtifactTemplate,
    validate_question_artifact_template,
)
from frutlups.review_report import ReviewVerdict
from frutlups.state import (
    NextActionCommand,
    NextActionDecision,
    RoadmapSlice,
    compute_next_action_from_verdict,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SLICE = RoadmapSlice(
    slice_id="M011-S04",
    milestone_id="M011",
    title="blocked-state resume guidance",
)
_REVIEW_REPORT = (
    "05_governance/reviews/m011_s04_blocked_state_resume_guidance_review_report.md"
)
_VERDICT_RECORD = (
    "05_governance/reviews/m011_s04_blocked_state_resume_guidance_verdict_record.md"
)


def _blocked_next_action() -> NextActionDecision:
    return compute_next_action_from_verdict(
        NextActionCommand(
            verdict=ReviewVerdict.BLOCKED,
            current_slice=_SLICE,
            slices=(_SLICE,),
            accepted_slice_ids=(),
        )
    )


def _suggested_question() -> QuestionArtifactTemplate:
    return QuestionArtifactTemplate(
        question_id="m011-s04-missing-decision",
        title="Which corrective path should unblock M011-S04?",
        question="What human decision is required to unblock this slice?",
        rationale="The reviewer blocked pending an external decision.",
        asker_role="reviewer",
        answerer_role="human",
        milestone_id="M011",
        slice_id="M011-S04",
    )


def _guidance(**overrides: object) -> BlockedResumeGuidance:
    defaults: dict[str, object] = dict(
        slice_id="M011-S04",
        slice_title="blocked-state resume guidance",
        review_report_path=_REVIEW_REPORT,
        verdict_record_path=_VERDICT_RECORD,
        next_action=_blocked_next_action(),
        next_command="python -m frutlups status ..",
    )
    defaults.update(overrides)
    return build_blocked_resume_guidance(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Resume-same-slice semantics
# ---------------------------------------------------------------------------

class ResumeSameSliceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.g = _guidance()

    def test_valid(self) -> None:
        self.assertTrue(self.g.valid)
        self.assertEqual(self.g.errors, ())

    def test_verdict_is_blocked(self) -> None:
        self.assertEqual(self.g.verdict, "blocked")

    def test_next_action_is_unblock_same_slice(self) -> None:
        self.assertEqual(self.g.next_action_kind, "unblock_same_slice")
        self.assertIn("unblock_same_slice", self.g.content)

    def test_routes_to_same_slice(self) -> None:
        low = self.g.content.lower()
        self.assertIn("same slice", low)
        self.assertIn("never advance", low)

    def test_slice_id_from_next_action_only(self) -> None:
        g = build_blocked_resume_guidance(next_action=_blocked_next_action())
        self.assertTrue(g.valid)
        self.assertEqual(g.slice_id, "M011-S04")


# ---------------------------------------------------------------------------
# Required content
# ---------------------------------------------------------------------------

class ContentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.g = _guidance()
        self.c = self.g.content

    def test_slice_id_and_title(self) -> None:
        self.assertIn("M011-S04", self.c)
        self.assertIn("blocked-state resume guidance", self.c)

    def test_review_report_and_verdict_record(self) -> None:
        self.assertIn(_REVIEW_REPORT, self.c)
        self.assertIn(_VERDICT_RECORD, self.c)

    def test_verdict_and_next_action_visible(self) -> None:
        self.assertIn("`blocked`", self.c)
        self.assertIn("`unblock_same_slice`", self.c)

    def test_human_input_section(self) -> None:
        self.assertIn("Required Human / External Input", self.c)

    def test_resume_checklist_has_four_steps(self) -> None:
        self.assertEqual(len(self.g.resume_checklist), 4)
        for n in ("1.", "2.", "3.", "4."):
            self.assertIn(n, self.c)

    def test_next_command_visible(self) -> None:
        self.assertIn("python -m frutlups status ..", self.c)

    def test_custom_human_input_used(self) -> None:
        g = _guidance(human_input_needed="Need the licensing decision from legal.")
        self.assertIn("Need the licensing decision from legal.", g.content)


# ---------------------------------------------------------------------------
# Question-artifact recommendation
# ---------------------------------------------------------------------------

class QuestionRecommendationTests(unittest.TestCase):
    def test_checklist_recommends_question_artifact(self) -> None:
        c = _guidance().content.lower()
        self.assertIn("question artifact", c)

    def test_does_not_write_automatically(self) -> None:
        c = _guidance().content.lower()
        self.assertIn("not written automatically", c)

    def test_suggested_question_preserved_and_valid(self) -> None:
        q = _suggested_question()
        g = _guidance(suggested_question=q)
        self.assertIs(g.suggested_question, q)
        self.assertEqual(validate_question_artifact_template(g.suggested_question), ())
        self.assertTrue(g.valid)

    def test_suggested_question_rendered(self) -> None:
        g = _guidance(suggested_question=_suggested_question())
        self.assertIn("Suggested Question Artifact", g.content)
        self.assertIn("m011-s04-missing-decision", g.content)

    def test_invalid_suggested_question_surfaces_errors(self) -> None:
        bad = QuestionArtifactTemplate(
            question_id="Bad Id",
            title="",
            question="q",
            rationale="r",
            asker_role="reviewer",
            answerer_role="human",
        )
        g = _guidance(suggested_question=bad)
        self.assertFalse(g.valid)
        self.assertTrue(any("suggested_question invalid" in e for e in g.errors))


# ---------------------------------------------------------------------------
# Boundaries and memory posture
# ---------------------------------------------------------------------------

class BoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.c = _guidance().content.lower()

    def test_no_pass_recording(self) -> None:
        self.assertIn("`pass`", _guidance().content)
        self.assertIn("record", self.c)

    def test_no_roadmap_advancement(self) -> None:
        self.assertIn("roadmap", self.c)

    def test_no_memory_mutation(self) -> None:
        self.assertIn("mutate memory", self.c)

    def test_no_provider_hardcoding(self) -> None:
        self.assertIn("logical role", self.c)

    def test_no_guessing(self) -> None:
        self.assertIn("guess", self.c)

    def test_memory_posture_optional_read_only(self) -> None:
        self.assertIn("optional", self.c)
        self.assertIn("read-only", self.c)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

class SerializationTests(unittest.TestCase):
    def test_to_dict_json_safe(self) -> None:
        g = _guidance(suggested_question=_suggested_question())
        json.dumps(g.to_dict())

    def test_to_dict_question_is_dict(self) -> None:
        g = _guidance(suggested_question=_suggested_question())
        d = g.to_dict()
        self.assertEqual(d["suggested_question"]["question_id"], "m011-s04-missing-decision")

    def test_to_dict_question_none_when_absent(self) -> None:
        d = _guidance().to_dict()
        self.assertIsNone(d["suggested_question"])

    def test_to_dict_lists(self) -> None:
        d = _guidance().to_dict()
        self.assertIsInstance(d["resume_checklist"], list)
        self.assertIsInstance(d["boundaries"], list)
        self.assertIsInstance(d["memory_posture"], list)


# ---------------------------------------------------------------------------
# Determinism / read-only
# ---------------------------------------------------------------------------

class DeterminismReadOnlyTests(unittest.TestCase):
    def test_deterministic(self) -> None:
        self.assertEqual(_guidance().content, _guidance().content)

    def test_read_only(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = set(root.rglob("*"))
            _guidance(suggested_question=_suggested_question())
            after = set(root.rglob("*"))
        self.assertEqual(before, after)


# ---------------------------------------------------------------------------
# Malformed / missing inputs
# ---------------------------------------------------------------------------

class MalformedInputTests(unittest.TestCase):
    def test_missing_slice_id(self) -> None:
        g = build_blocked_resume_guidance()
        self.assertFalse(g.valid)
        self.assertEqual(g.content, "")
        self.assertTrue(any("slice_id is required" in e for e in g.errors))

    def test_non_blocked_verdict_warns(self) -> None:
        g = build_blocked_resume_guidance(
            slice_id="M011-S04", verdict=ReviewVerdict.PASS
        )
        self.assertFalse(g.valid)
        self.assertTrue(any("not 'blocked'" in e for e in g.errors))

    def test_wrong_next_action_type_does_not_raise(self) -> None:
        g = build_blocked_resume_guidance(
            slice_id="M011-S04", next_action="nope"  # type: ignore[arg-type]
        )
        self.assertTrue(any("next_action must be" in e for e in g.errors))

    def test_missing_optional_paths_render_gaps(self) -> None:
        g = build_blocked_resume_guidance(
            slice_id="M011-S04", next_action=_blocked_next_action()
        )
        self.assertTrue(g.valid)
        self.assertIn("(not provided)", g.content)

    def test_no_next_command_renders_placeholder(self) -> None:
        g = build_blocked_resume_guidance(
            slice_id="M011-S04", next_action=_blocked_next_action(), next_command=""
        )
        self.assertIn("none provided", g.content.lower())


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

class ExportTests(unittest.TestCase):
    def test_blocked_api_exported(self) -> None:
        import frutlups

        self.assertTrue(hasattr(frutlups, "build_blocked_resume_guidance"))
        self.assertTrue(hasattr(frutlups, "BlockedResumeGuidance"))

    def test_existing_apis_still_exported(self) -> None:
        import frutlups

        for name in (
            "build_coder_handoff",
            "build_reviewer_handoff",
            "render_question_artifact",
            "write_question_artifact",
        ):
            self.assertTrue(hasattr(frutlups, name), name)


if __name__ == "__main__":
    unittest.main()
