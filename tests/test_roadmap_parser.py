from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from frutlups.state import (
    MilestoneStatus,
    RoadmapMilestone,
    next_actionable_milestone,
    parse_milestones,
)


class RoadmapParserTests(unittest.TestCase):
    def test_parses_done_criteria_as_ordered_tuple(self) -> None:
        path = self._write_roadmap(
            "# Roadmap\n\n"
            "### M001: First\n\n"
            "Status: active\n\n"
            "Some intro prose.\n\n"
            "Done when:\n\n"
            "- criterion alpha\n"
            "- criterion beta\n"
            "- criterion gamma\n\n"
            "Trailing prose that is not a criterion.\n"
        )

        milestones = parse_milestones(path)

        self.assertEqual(len(milestones), 1)
        milestone = milestones[0]
        self.assertEqual(milestone.milestone_id, "M001")
        self.assertEqual(milestone.title, "First")
        self.assertEqual(milestone.status, MilestoneStatus.ACTIVE)
        self.assertEqual(
            milestone.done_criteria,
            ("criterion alpha", "criterion beta", "criterion gamma"),
        )

    def test_milestone_without_done_section_has_empty_criteria(self) -> None:
        path = self._write_roadmap(
            "### M002: Bare\n\n"
            "Status: planned\n\n"
            "No bullet list follows.\n"
        )

        milestones = parse_milestones(path)

        self.assertEqual(len(milestones), 1)
        self.assertEqual(milestones[0].done_criteria, ())

    def test_unknown_status_does_not_crash(self) -> None:
        path = self._write_roadmap(
            "### M003: Mystery\n\n"
            "Status: wandering\n\n"
            "Done when:\n\n"
            "- something happens\n"
        )

        milestones = parse_milestones(path)

        self.assertEqual(milestones[0].status, MilestoneStatus.UNKNOWN)
        self.assertEqual(milestones[0].done_criteria, ("something happens",))

    def test_supports_all_named_statuses(self) -> None:
        body = (
            "### M001: A\nStatus: planned\n\n"
            "### M002: B\nStatus: active\n\n"
            "### M003: C\nStatus: completed\n\n"
            "### M004: D\nStatus: blocked\n\n"
            "### M005: E\nStatus: needs_review\n\n"
            "### M006: F\nStatus: needs-review\n\n"
        )
        path = self._write_roadmap(body)

        milestones = parse_milestones(path)
        statuses = [m.status for m in milestones]

        self.assertEqual(
            statuses,
            [
                MilestoneStatus.PLANNED,
                MilestoneStatus.ACTIVE,
                MilestoneStatus.COMPLETED,
                MilestoneStatus.BLOCKED,
                MilestoneStatus.NEEDS_REVIEW,
                MilestoneStatus.NEEDS_REVIEW,
            ],
        )

    def test_joins_wrapped_continuation_lines(self) -> None:
        path = self._write_roadmap(
            "### M001: Wraps\n\n"
            "Status: active\n\n"
            "Done when:\n\n"
            "- a long criterion that wraps\n"
            "  onto a second line\n"
            "- another short one\n"
        )

        milestones = parse_milestones(path)

        self.assertEqual(
            milestones[0].done_criteria,
            ("a long criterion that wraps onto a second line", "another short one"),
        )

    def test_next_milestone_heading_terminates_done_section(self) -> None:
        path = self._write_roadmap(
            "### M001: First\n\n"
            "Status: completed\n\n"
            "Done when:\n\n"
            "- first criterion\n\n"
            "### M002: Second\n\n"
            "Status: active\n\n"
            "Done when:\n\n"
            "- second criterion\n"
        )

        milestones = parse_milestones(path)

        self.assertEqual(len(milestones), 2)
        self.assertEqual(milestones[0].done_criteria, ("first criterion",))
        self.assertEqual(milestones[1].done_criteria, ("second criterion",))

    def test_done_dict_serialization_includes_criteria(self) -> None:
        milestone = RoadmapMilestone(
            milestone_id="M001",
            title="X",
            status=MilestoneStatus.ACTIVE,
            done_criteria=("one", "two"),
        )

        self.assertEqual(
            milestone.to_dict(),
            {
                "id": "M001",
                "title": "X",
                "status": "active",
                "done_criteria": ["one", "two"],
            },
        )

    def test_next_actionable_prefers_needs_review_over_active(self) -> None:
        milestones = (
            RoadmapMilestone("M001", "A", MilestoneStatus.ACTIVE),
            RoadmapMilestone("M002", "B", MilestoneStatus.NEEDS_REVIEW),
            RoadmapMilestone("M003", "C", MilestoneStatus.PLANNED),
        )
        chosen = next_actionable_milestone(milestones)
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen.milestone_id, "M002")

    def _write_roadmap(self, body: str) -> Path:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        path = Path(self._tmp.name) / "roadmap.md"
        path.write_text(body, encoding="utf-8")
        return path


if __name__ == "__main__":
    unittest.main()
