"""Tests for M008-S01: frutlups next command and LoopFrontier."""

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from frutlups.cli import main
from frutlups.project import LoopFrontier, build_next_frontier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _write_active_roadmap(root: Path, content: str) -> None:
    (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
        content, encoding="utf-8"
    )


def _write_detailed_roadmap(root: Path, content: str) -> None:
    (root / "03_experiments" / "development_roadmap_frutlups.md").write_text(
        content, encoding="utf-8"
    )


def _write_review_report(root: Path, filename: str, verdict: str = "pass") -> None:
    (root / "05_governance" / "reviews" / filename).write_text(
        f"# Review\n\n## Verdict\n\n{verdict}\n", encoding="utf-8"
    )


def _active(mid: str, title: str, status: str = "active") -> str:
    return f"### {mid}: {title}\n\nStatus: {status}\n\n"


def _detailed_milestone(mid: str, title: str, slices: list[tuple[str, str]]) -> str:
    lines = [f"### {mid}: {title}\n\nSlices:\n"]
    for sid, stitle in slices:
        lines.append(f"- {sid}: {stitle}\n")
    lines.append("\n")
    return "".join(lines)


def _run_next(root: Path, extra: list[str] | None = None) -> tuple[int, str]:
    buf = StringIO()
    args = ["next", str(root)] + (extra or [])
    with redirect_stdout(buf):
        code = main(args)
    return code, buf.getvalue()


# ---------------------------------------------------------------------------
# CLI help tests
# ---------------------------------------------------------------------------

class CliHelpTests(unittest.TestCase):
    def test_help_includes_next_command(self) -> None:
        buf = StringIO()
        with redirect_stdout(buf):
            try:
                main(["--help"])
            except SystemExit:
                pass
        self.assertIn("next", buf.getvalue())

    def test_next_appears_in_subcommands(self) -> None:
        buf = StringIO()
        with redirect_stdout(buf):
            try:
                main(["next", "--help"])
            except SystemExit:
                pass
        output = buf.getvalue()
        self.assertIn("next", output.lower())


# ---------------------------------------------------------------------------
# Frontier inference: authored next slice present
# ---------------------------------------------------------------------------

class AuthoredNextSlicePresentTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _make_template(self.root)
        _write_active_roadmap(
            self.root,
            "# Active Roadmap\n\n"
            + _active("M001", "First Milestone", "active"),
        )
        _write_detailed_roadmap(
            self.root,
            "# Detailed Roadmap\n\n"
            + _detailed_milestone("M001", "First Milestone", [("M001-S01", "slice one"), ("M001-S02", "slice two")]),
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_authored_slice_used_as_frontier_when_present(self) -> None:
        frontier = build_next_frontier(self.root)
        self.assertIsNotNone(frontier.inferred_slice)
        self.assertEqual(frontier.inferred_slice.slice_id, "M001-S01")

    def test_inferred_milestone_matches_authored(self) -> None:
        frontier = build_next_frontier(self.root)
        self.assertIsNotNone(frontier.inferred_milestone)
        self.assertEqual(frontier.inferred_milestone.milestone_id, "M001")

    def test_authored_next_slice_field_populated(self) -> None:
        frontier = build_next_frontier(self.root)
        self.assertIsNotNone(frontier.authored_next_slice)
        self.assertEqual(frontier.authored_next_slice.slice_id, "M001-S01")

    def test_action_contains_slice_id(self) -> None:
        frontier = build_next_frontier(self.root)
        self.assertIn("M001-S01", frontier.action)


# ---------------------------------------------------------------------------
# Frontier inference: authored milestone exhausted, search forward
# ---------------------------------------------------------------------------

class ExhaustedMilestoneSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _make_template(self.root)
        _write_active_roadmap(
            self.root,
            "# Active Roadmap\n\n"
            + _active("M001", "First Milestone", "active")
            + _active("M002", "Second Milestone", "planned"),
        )
        _write_detailed_roadmap(
            self.root,
            "# Detailed Roadmap\n\n"
            + _detailed_milestone("M001", "First Milestone", [("M001-S01", "slice one")])
            + _detailed_milestone("M002", "Second Milestone", [("M002-S01", "slice two")]),
        )
        _write_review_report(self.root, "m001_s01_foo_review_report.md", "pass")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_infers_forward_to_next_planned_milestone(self) -> None:
        frontier = build_next_frontier(self.root)
        self.assertIsNotNone(frontier.inferred_slice)
        self.assertEqual(frontier.inferred_slice.slice_id, "M002-S01")

    def test_inferred_milestone_is_second(self) -> None:
        frontier = build_next_frontier(self.root)
        self.assertIsNotNone(frontier.inferred_milestone)
        self.assertEqual(frontier.inferred_milestone.milestone_id, "M002")

    def test_authored_next_slice_is_none_exhausted(self) -> None:
        frontier = build_next_frontier(self.root)
        self.assertIsNone(frontier.authored_next_slice)

    def test_authored_next_milestone_still_visible(self) -> None:
        frontier = build_next_frontier(self.root)
        self.assertIsNotNone(frontier.authored_next_milestone)
        self.assertEqual(frontier.authored_next_milestone.milestone_id, "M001")

    def test_action_contains_inferred(self) -> None:
        frontier = build_next_frontier(self.root)
        self.assertIn("M002-S01", frontier.action)


# ---------------------------------------------------------------------------
# Completed milestones skipped
# ---------------------------------------------------------------------------

class CompletedMilestoneSkippedTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _make_template(self.root)
        _write_active_roadmap(
            self.root,
            "# Active Roadmap\n\n"
            + _active("M001", "Done Milestone", "completed")
            + _active("M002", "Next Milestone", "planned"),
        )
        _write_detailed_roadmap(
            self.root,
            "# Detailed Roadmap\n\n"
            + _detailed_milestone("M001", "Done Milestone", [("M001-S01", "done slice")])
            + _detailed_milestone("M002", "Next Milestone", [("M002-S01", "next slice")]),
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_completed_milestone_not_selected(self) -> None:
        frontier = build_next_frontier(self.root)
        self.assertIsNotNone(frontier.inferred_slice)
        self.assertNotEqual(frontier.inferred_slice.milestone_id, "M001")

    def test_planned_milestone_selected_instead(self) -> None:
        frontier = build_next_frontier(self.root)
        self.assertIsNotNone(frontier.inferred_slice)
        self.assertEqual(frontier.inferred_slice.slice_id, "M002-S01")


# ---------------------------------------------------------------------------
# Blocked and unknown milestones not auto-selected
# ---------------------------------------------------------------------------

class BlockedUnknownMilestoneNotSelectedTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _make_template(self.root)
        _write_active_roadmap(
            self.root,
            "# Active Roadmap\n\n"
            + _active("M001", "Blocked Milestone", "blocked")
            + _active("M002", "Unknown Milestone", "gibberish_status")
            + _active("M003", "Planned Milestone", "planned"),
        )
        _write_detailed_roadmap(
            self.root,
            "# Detailed Roadmap\n\n"
            + _detailed_milestone("M001", "Blocked Milestone", [("M001-S01", "blocked slice")])
            + _detailed_milestone("M002", "Unknown Milestone", [("M002-S01", "unknown slice")])
            + _detailed_milestone("M003", "Planned Milestone", [("M003-S01", "planned slice")]),
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_blocked_milestone_not_selected(self) -> None:
        frontier = build_next_frontier(self.root)
        if frontier.inferred_slice is not None:
            self.assertNotEqual(frontier.inferred_slice.milestone_id, "M001")

    def test_unknown_milestone_not_selected(self) -> None:
        frontier = build_next_frontier(self.root)
        if frontier.inferred_slice is not None:
            self.assertNotEqual(frontier.inferred_slice.milestone_id, "M002")

    def test_planned_milestone_selected(self) -> None:
        frontier = build_next_frontier(self.root)
        self.assertIsNotNone(frontier.inferred_slice)
        self.assertEqual(frontier.inferred_slice.slice_id, "M003-S01")


# ---------------------------------------------------------------------------
# All candidate milestones accepted; falls through to next unaccepted
# ---------------------------------------------------------------------------

class AllCandidatesAcceptedFallThroughTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _make_template(self.root)
        _write_active_roadmap(
            self.root,
            "# Active Roadmap\n\n"
            + _active("M001", "First", "active")
            + _active("M002", "Second", "planned")
            + _active("M003", "Third", "planned"),
        )
        _write_detailed_roadmap(
            self.root,
            "# Detailed Roadmap\n\n"
            + _detailed_milestone("M001", "First", [("M001-S01", "s1")])
            + _detailed_milestone("M002", "Second", [("M002-S01", "s1"), ("M002-S02", "s2")])
            + _detailed_milestone("M003", "Third", [("M003-S01", "s1")]),
        )
        _write_review_report(self.root, "m001_s01_foo_review_report.md", "pass")
        _write_review_report(self.root, "m002_s01_foo_review_report.md", "pass")
        _write_review_report(self.root, "m002_s02_foo_review_report.md", "pass")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_falls_through_to_first_unaccepted_slice(self) -> None:
        frontier = build_next_frontier(self.root)
        self.assertIsNotNone(frontier.inferred_slice)
        self.assertEqual(frontier.inferred_slice.slice_id, "M003-S01")


# ---------------------------------------------------------------------------
# No frontier found
# ---------------------------------------------------------------------------

class NoFrontierFoundTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _make_template(self.root)
        _write_active_roadmap(
            self.root,
            "# Active Roadmap\n\n"
            + _active("M001", "Only Milestone", "active"),
        )
        _write_detailed_roadmap(
            self.root,
            "# Detailed Roadmap\n\n"
            + _detailed_milestone("M001", "Only Milestone", [("M001-S01", "only slice")]),
        )
        _write_review_report(self.root, "m001_s01_foo_review_report.md", "pass")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_no_frontier_returns_none_inferred_slice(self) -> None:
        frontier = build_next_frontier(self.root)
        self.assertIsNone(frontier.inferred_slice)

    def test_no_frontier_does_not_raise(self) -> None:
        frontier = build_next_frontier(self.root)
        self.assertIsInstance(frontier, LoopFrontier)

    def test_no_frontier_adds_diagnostic(self) -> None:
        frontier = build_next_frontier(self.root)
        codes = [d.code for d in frontier.diagnostics]
        self.assertIn("no_frontier_slice", codes)

    def test_no_frontier_action_is_descriptive(self) -> None:
        frontier = build_next_frontier(self.root)
        self.assertIn("no frontier", frontier.action.lower())


# ---------------------------------------------------------------------------
# LoopFrontier.to_dict() returns only plain Python values
# ---------------------------------------------------------------------------

class LoopFrontierToDictTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _make_template(self.root)
        _write_active_roadmap(
            self.root,
            "# Active Roadmap\n\n" + _active("M001", "Milestone", "active"),
        )
        _write_detailed_roadmap(
            self.root,
            "# Detailed Roadmap\n\n"
            + _detailed_milestone("M001", "Milestone", [("M001-S01", "slice")]),
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_to_dict_is_json_serializable(self) -> None:
        frontier = build_next_frontier(self.root)
        serialized = json.dumps(frontier.to_dict())
        self.assertIsInstance(serialized, str)

    def test_to_dict_inferred_slice_has_id_key(self) -> None:
        frontier = build_next_frontier(self.root)
        d = frontier.to_dict()
        self.assertIsNotNone(d["inferred_slice"])
        self.assertIn("id", d["inferred_slice"])

    def test_to_dict_contains_expected_keys(self) -> None:
        frontier = build_next_frontier(self.root)
        d = frontier.to_dict()
        for key in (
            "root",
            "active_roadmap",
            "detailed_roadmap",
            "authored_next_milestone",
            "authored_next_slice",
            "inferred_milestone",
            "inferred_slice",
            "accepted_slice_ids",
            "prompt_health",
            "memory",
            "diagnostics",
            "action",
        ):
            self.assertIn(key, d, f"missing key: {key}")

    def test_to_dict_accepted_slice_ids_is_list(self) -> None:
        frontier = build_next_frontier(self.root)
        d = frontier.to_dict()
        self.assertIsInstance(d["accepted_slice_ids"], list)

    def test_to_dict_diagnostics_is_list(self) -> None:
        frontier = build_next_frontier(self.root)
        d = frontier.to_dict()
        self.assertIsInstance(d["diagnostics"], list)

    def test_to_dict_root_is_string(self) -> None:
        frontier = build_next_frontier(self.root)
        d = frontier.to_dict()
        self.assertIsInstance(d["root"], str)

    def test_to_dict_when_no_inferred_slice(self) -> None:
        _write_review_report(self.root, "m001_s01_foo_review_report.md", "pass")
        frontier = build_next_frontier(self.root)
        d = frontier.to_dict()
        self.assertIsNone(d["inferred_slice"])
        self.assertIsNone(d["inferred_milestone"])


# ---------------------------------------------------------------------------
# LoopFrontier is frozen
# ---------------------------------------------------------------------------

class LoopFrontierFrozenTests(unittest.TestCase):
    def test_frontier_is_frozen(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            _write_active_roadmap(
                root,
                "# Active Roadmap\n\n" + _active("M001", "M", "active"),
            )
            frontier = build_next_frontier(root)
        with self.assertRaises((AttributeError, TypeError)):
            frontier.action = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Prompt health visible
# ---------------------------------------------------------------------------

class PromptHealthVisibleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _make_template(self.root)
        _write_active_roadmap(
            self.root,
            "# Active Roadmap\n\n" + _active("M001", "Milestone", "active"),
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_frontier_has_prompt_health(self) -> None:
        frontier = build_next_frontier(self.root)
        self.assertIsNotNone(frontier.prompt_health)
        self.assertIsInstance(frontier.prompt_health.ok, bool)

    def test_cli_human_output_includes_prompt_health(self) -> None:
        code, output = _run_next(self.root)
        self.assertEqual(code, 0)
        self.assertIn("Prompt health", output)

    def test_cli_json_output_includes_prompt_health(self) -> None:
        code, output = _run_next(self.root, ["--json"])
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertIn("prompt_health", payload)
        self.assertIn("ok", payload["prompt_health"])


# ---------------------------------------------------------------------------
# CLI human-readable output
# ---------------------------------------------------------------------------

class CliHumanOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _make_template(self.root)
        _write_active_roadmap(
            self.root,
            "# Active Roadmap\n\n"
            + _active("M001", "First", "active")
            + _active("M002", "Second", "planned"),
        )
        _write_detailed_roadmap(
            self.root,
            "# Detailed Roadmap\n\n"
            + _detailed_milestone("M001", "First", [("M001-S01", "s1")])
            + _detailed_milestone("M002", "Second", [("M002-S01", "s1")]),
        )
        _write_review_report(self.root, "m001_s01_foo_review_report.md", "pass")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_cli_exits_zero(self) -> None:
        code, _ = _run_next(self.root)
        self.assertEqual(code, 0)

    def test_cli_output_contains_project_path(self) -> None:
        _, output = _run_next(self.root)
        self.assertIn("Project:", output)

    def test_cli_output_contains_inferred_slice(self) -> None:
        _, output = _run_next(self.root)
        self.assertIn("M002-S01", output)

    def test_cli_output_contains_authored_active_milestone(self) -> None:
        _, output = _run_next(self.root)
        self.assertIn("M001", output)

    def test_cli_output_contains_action(self) -> None:
        _, output = _run_next(self.root)
        self.assertIn("Action:", output)

    def test_cli_output_contains_memory_status(self) -> None:
        _, output = _run_next(self.root)
        self.assertIn("Memory:", output)

    def test_cli_output_contains_diagnostics_header_when_present(self) -> None:
        _, output = _run_next(self.root)
        self.assertIn("Diagnostics:", output)


# ---------------------------------------------------------------------------
# CLI JSON output
# ---------------------------------------------------------------------------

class CliJsonOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _make_template(self.root)
        _write_active_roadmap(
            self.root,
            "# Active Roadmap\n\n"
            + _active("M001", "First", "active")
            + _active("M002", "Second", "planned"),
        )
        _write_detailed_roadmap(
            self.root,
            "# Detailed Roadmap\n\n"
            + _detailed_milestone("M001", "First", [("M001-S01", "s1")])
            + _detailed_milestone("M002", "Second", [("M002-S01", "s1")]),
        )
        _write_review_report(self.root, "m001_s01_foo_review_report.md", "pass")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_json_output_is_valid(self) -> None:
        code, output = _run_next(self.root, ["--json"])
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertIsInstance(payload, dict)

    def test_json_output_inferred_slice_present(self) -> None:
        _, output = _run_next(self.root, ["--json"])
        payload = json.loads(output)
        self.assertIsNotNone(payload["inferred_slice"])
        self.assertEqual(payload["inferred_slice"]["id"], "M002-S01")

    def test_json_output_inferred_milestone_present(self) -> None:
        _, output = _run_next(self.root, ["--json"])
        payload = json.loads(output)
        self.assertIsNotNone(payload["inferred_milestone"])
        self.assertEqual(payload["inferred_milestone"]["id"], "M002")

    def test_json_output_authored_next_milestone_present(self) -> None:
        _, output = _run_next(self.root, ["--json"])
        payload = json.loads(output)
        self.assertIsNotNone(payload["authored_next_milestone"])
        self.assertEqual(payload["authored_next_milestone"]["id"], "M001")

    def test_json_output_authored_next_slice_none_when_exhausted(self) -> None:
        _, output = _run_next(self.root, ["--json"])
        payload = json.loads(output)
        self.assertIsNone(payload["authored_next_slice"])

    def test_json_output_has_accepted_slice_ids(self) -> None:
        _, output = _run_next(self.root, ["--json"])
        payload = json.loads(output)
        self.assertIn("M001-S01", payload["accepted_slice_ids"])

    def test_json_is_sorted(self) -> None:
        _, output = _run_next(self.root, ["--json"])
        payload = json.loads(output)
        keys = list(payload.keys())
        self.assertEqual(keys, sorted(keys))


# ---------------------------------------------------------------------------
# Diagnostics remain visible
# ---------------------------------------------------------------------------

class DiagnosticsVisibleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _make_template(self.root)
        _write_active_roadmap(
            self.root,
            "# Active Roadmap\n\n"
            + _active("M001", "Milestone", "active")
            + _active("M002", "Next", "planned"),
        )
        _write_detailed_roadmap(
            self.root,
            "# Detailed Roadmap\n\n"
            + _detailed_milestone("M001", "Milestone", [("M001-S01", "s1")])
            + _detailed_milestone("M002", "Next", [("M002-S01", "s1")]),
        )
        _write_review_report(self.root, "m001_s01_foo_review_report.md", "pass")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_exhausted_authored_milestone_diagnostic_present(self) -> None:
        frontier = build_next_frontier(self.root)
        codes = [d.code for d in frontier.diagnostics]
        self.assertIn("next_slice_unavailable_all_accepted", codes)

    def test_diagnostic_visible_in_json(self) -> None:
        _, output = _run_next(self.root, ["--json"])
        payload = json.loads(output)
        codes = [d["code"] for d in payload["diagnostics"]]
        self.assertIn("next_slice_unavailable_all_accepted", codes)


# ---------------------------------------------------------------------------
# Missing project root behaves like status
# ---------------------------------------------------------------------------

class MissingRootErrorTests(unittest.TestCase):
    def test_missing_root_exits_nonzero(self) -> None:
        buf = StringIO()
        with redirect_stdout(buf):
            code = main(["next", "/no/such/path/that/could/exist/frutlups"])
        self.assertNotEqual(code, 0)


# ---------------------------------------------------------------------------
# Status command remains compatible
# ---------------------------------------------------------------------------

class StatusCommandCompatibilityTests(unittest.TestCase):
    def test_status_still_works(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            _write_active_roadmap(
                root,
                "# Active Roadmap\n\n" + _active("M001", "Milestone", "active"),
            )
            buf = StringIO()
            with redirect_stdout(buf):
                code = main(["status", str(root)])
            self.assertEqual(code, 0)
            self.assertIn("Project:", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
