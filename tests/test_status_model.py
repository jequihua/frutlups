"""Focused tests for MilestoneStatus representation and JSON serialization."""

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from frutlups.cli import main
from frutlups.state import MilestoneStatus, parse_milestones


CANONICAL_VALUES = ("planned", "active", "completed", "blocked", "needs_review")


class MilestoneStatusEnumTests(unittest.TestCase):
    def test_canonical_lists_five_known_statuses_in_order(self) -> None:
        canonical = MilestoneStatus.canonical()
        self.assertEqual(
            canonical,
            (
                MilestoneStatus.PLANNED,
                MilestoneStatus.ACTIVE,
                MilestoneStatus.COMPLETED,
                MilestoneStatus.BLOCKED,
                MilestoneStatus.NEEDS_REVIEW,
            ),
        )
        self.assertNotIn(MilestoneStatus.UNKNOWN, canonical)

    def test_each_canonical_value_is_its_lowercase_string(self) -> None:
        for status, value in zip(MilestoneStatus.canonical(), CANONICAL_VALUES):
            self.assertEqual(status.value, value)

    def test_str_enum_serializes_as_canonical_string(self) -> None:
        payload = json.dumps([status.value for status in MilestoneStatus.canonical()])
        self.assertEqual(
            json.loads(payload),
            ["planned", "active", "completed", "blocked", "needs_review"],
        )


class MilestoneStatusCoerceTests(unittest.TestCase):
    def test_coerce_each_canonical_string(self) -> None:
        for status, value in zip(MilestoneStatus.canonical(), CANONICAL_VALUES):
            self.assertEqual(MilestoneStatus.coerce(value), status)

    def test_coerce_is_case_insensitive(self) -> None:
        self.assertEqual(MilestoneStatus.coerce("ACTIVE"), MilestoneStatus.ACTIVE)
        self.assertEqual(MilestoneStatus.coerce("Blocked"), MilestoneStatus.BLOCKED)
        self.assertEqual(
            MilestoneStatus.coerce("Needs_Review"), MilestoneStatus.NEEDS_REVIEW
        )

    def test_coerce_accepts_needs_review_hyphen_alias(self) -> None:
        self.assertEqual(
            MilestoneStatus.coerce("needs-review"), MilestoneStatus.NEEDS_REVIEW
        )
        self.assertEqual(
            MilestoneStatus.coerce("NEEDS-REVIEW"), MilestoneStatus.NEEDS_REVIEW
        )

    def test_coerce_strips_surrounding_whitespace(self) -> None:
        self.assertEqual(
            MilestoneStatus.coerce("  completed  "), MilestoneStatus.COMPLETED
        )

    def test_coerce_none_returns_unknown(self) -> None:
        self.assertEqual(MilestoneStatus.coerce(None), MilestoneStatus.UNKNOWN)

    def test_coerce_empty_or_whitespace_returns_unknown(self) -> None:
        self.assertEqual(MilestoneStatus.coerce(""), MilestoneStatus.UNKNOWN)
        self.assertEqual(MilestoneStatus.coerce("   "), MilestoneStatus.UNKNOWN)

    def test_coerce_unrecognized_token_returns_unknown(self) -> None:
        self.assertEqual(MilestoneStatus.coerce("wandering"), MilestoneStatus.UNKNOWN)
        self.assertEqual(MilestoneStatus.coerce("in-flight"), MilestoneStatus.UNKNOWN)

    def test_coerce_never_raises(self) -> None:
        for value in (None, "", "   ", "unknown-thing", "12345", "active!"):
            MilestoneStatus.coerce(value)


class ParserStatusRepresentationTests(unittest.TestCase):
    def test_parser_resolves_all_canonical_statuses(self) -> None:
        body_lines = ["# Roadmap", ""]
        for index, value in enumerate(CANONICAL_VALUES, start=1):
            body_lines.extend(
                [
                    f"### M{index:03d}: Milestone {index}",
                    "",
                    f"Status: {value}",
                    "",
                ]
            )
        path = self._write_roadmap("\n".join(body_lines))

        milestones = parse_milestones(path)
        statuses = [m.status for m in milestones]

        self.assertEqual(statuses, list(MilestoneStatus.canonical()))

    def test_parser_treats_needs_review_hyphen_alias_as_needs_review(self) -> None:
        path = self._write_roadmap(
            "### M001: A\n\nStatus: needs-review\n"
        )

        milestones = parse_milestones(path)

        self.assertEqual(milestones[0].status, MilestoneStatus.NEEDS_REVIEW)

    def test_parser_falls_back_to_unknown_for_unrecognized_status(self) -> None:
        path = self._write_roadmap("### M001: A\n\nStatus: wandering\n")

        milestones = parse_milestones(path)

        self.assertEqual(milestones[0].status, MilestoneStatus.UNKNOWN)

    def test_milestone_to_dict_uses_canonical_status_string(self) -> None:
        path = self._write_roadmap(
            "### M001: A\n\nStatus: needs-review\n"
            "### M002: B\n\nStatus: wandering\n"
        )

        milestones = parse_milestones(path)

        self.assertEqual(milestones[0].to_dict()["status"], "needs_review")
        self.assertEqual(milestones[1].to_dict()["status"], "unknown")

    def _write_roadmap(self, body: str) -> Path:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        path = Path(self._tmp.name) / "roadmap.md"
        path.write_text(body, encoding="utf-8")
        return path


class CliStatusJsonRepresentationTests(unittest.TestCase):
    def test_json_status_uses_canonical_strings_for_all_milestones(self) -> None:
        valid = set(s.value for s in MilestoneStatus)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
                "# Active Roadmap\n\n"
                "### M001: A\n\nStatus: completed\n\n"
                "### M002: B\n\nStatus: active\n\n"
                "### M003: C\n\nStatus: needs-review\n\n"
                "### M004: D\n\nStatus: wandering\n\n"
                "### M005: E\n\nStatus: blocked\n",
                encoding="utf-8",
            )
            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["status", str(root), "--json"])

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        observed = [m["status"] for m in payload["milestones"]]

        self.assertEqual(
            observed,
            ["completed", "active", "needs_review", "unknown", "blocked"],
        )
        for status_value in observed:
            self.assertIn(status_value, valid)


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
