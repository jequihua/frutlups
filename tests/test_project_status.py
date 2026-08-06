from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from frutlups.project import build_status, find_project_root
from frutlups.state import MilestoneStatus, parse_milestones


class ProjectStatusTests(unittest.TestCase):
    def test_build_status_reads_template_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
                "# Active Roadmap\n\n"
                "### M001: Package Scaffold\n\n"
                "Status: active\n\n",
                encoding="utf-8",
            )
            (root / "prompts" / "for_coding_agent" / "001_example.md").write_text(
                "# Coding\n",
                encoding="utf-8",
            )

            status = build_status(root)

        self.assertTrue(status.ok)
        self.assertEqual(status.prompts.coding_count, 1)
        self.assertEqual(status.prompts.review_count, 0)
        self.assertIsNotNone(status.next_milestone)
        self.assertEqual(status.next_milestone.milestone_id, "M001")
        self.assertFalse(status.memory.enabled)

    def test_find_project_root_from_child(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            child = root / "08_pkg" / "src"

            discovered = find_project_root(child)

        self.assertEqual(discovered, root.resolve())

    def test_parse_milestones(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "roadmap.md"
            path.write_text(
                "# Roadmap\n\n"
                "### M001: First\n\n"
                "Status: completed\n\n"
                "### M002: Second\n\n"
                "Status: planned\n",
                encoding="utf-8",
            )

            milestones = parse_milestones(path)

        self.assertEqual(len(milestones), 2)
        self.assertEqual(milestones[0].status, MilestoneStatus.COMPLETED)
        self.assertEqual(milestones[1].status, MilestoneStatus.PLANNED)


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


if __name__ == "__main__":
    unittest.main()
