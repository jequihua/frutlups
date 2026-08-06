"""Tests for roadmap and loop-state diagnostics."""

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from frutlups.cli import main
from frutlups.project import build_status
from frutlups.state import Diagnostic, DiagnosticSeverity


ACTIVE_HEADER = "# Active Roadmap\n\n"
DETAILED_HEADER = "# Detailed Roadmap\n\n"


def _development_repo_root() -> Path | None:
    required = ("00_brief", "03_experiments", "05_governance", "06_infra", "08_pkg", "prompts")
    for candidate in Path(__file__).resolve().parents:
        if all((candidate / name).exists() for name in required):
            return candidate
    return None


class DiagnosticTypeTests(unittest.TestCase):
    def test_diagnostic_to_dict_uses_canonical_severity_string(self) -> None:
        diag = Diagnostic(
            code="example_code",
            severity=DiagnosticSeverity.WARNING,
            message="example message",
        )
        self.assertEqual(
            diag.to_dict(),
            {
                "code": "example_code",
                "severity": "warning",
                "message": "example message",
            },
        )

    def test_severity_values_are_canonical_strings(self) -> None:
        self.assertEqual(DiagnosticSeverity.INFO.value, "info")
        self.assertEqual(DiagnosticSeverity.WARNING.value, "warning")
        self.assertEqual(DiagnosticSeverity.ERROR.value, "error")


class DiagnosticCaseTests(unittest.TestCase):
    def test_no_active_roadmap_emits_error(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)

            status = build_status(root)

        codes = _codes(status.diagnostics)
        self.assertIn("no_active_roadmap", codes)
        self.assertEqual(
            _severity(status.diagnostics, "no_active_roadmap"), "error"
        )
        self.assertIn("no_detailed_roadmap", codes)
        self.assertIsNone(status.active_roadmap)
        self.assertIsNone(status.next_milestone)

    def test_multiple_active_roadmaps_warns_even_when_preferred_selected(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            experiments = root / "03_experiments"
            (experiments / "active_roadmap_frutlups.md").write_text(
                ACTIVE_HEADER + "### M001: A\n\nStatus: active\n",
                encoding="utf-8",
            )
            (experiments / "active_roadmap_alternate.md").write_text(
                ACTIVE_HEADER + "### M001: A\n\nStatus: active\n",
                encoding="utf-8",
            )

            status = build_status(root)

        codes = _codes(status.diagnostics)
        self.assertIn("multiple_active_roadmaps", codes)
        self.assertEqual(
            _severity(status.diagnostics, "multiple_active_roadmaps"), "warning"
        )
        self.assertEqual(
            status.active_roadmap.name, "active_roadmap_frutlups.md"
        )
        msg = _message(status.diagnostics, "multiple_active_roadmaps")
        self.assertIn("active_roadmap_frutlups.md", msg)
        self.assertIn("active_roadmap_alternate.md", msg)

    def test_no_detailed_roadmap_emits_warning(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
                ACTIVE_HEADER + "### M002: A\n\nStatus: active\n",
                encoding="utf-8",
            )

            status = build_status(root)

        codes = _codes(status.diagnostics)
        self.assertIn("no_detailed_roadmap", codes)
        self.assertEqual(
            _severity(status.diagnostics, "no_detailed_roadmap"), "warning"
        )

    def test_multiple_detailed_roadmaps_warns_even_when_preferred_selected(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            experiments = root / "03_experiments"
            (experiments / "active_roadmap_frutlups.md").write_text(
                ACTIVE_HEADER + "### M002: A\n\nStatus: active\n",
                encoding="utf-8",
            )
            (experiments / "development_roadmap_frutlups.md").write_text(
                DETAILED_HEADER + "### M002: A\n\nSlices:\n\n- M002-S01: a\n",
                encoding="utf-8",
            )
            (experiments / "development_roadmap_alternate.md").write_text(
                DETAILED_HEADER + "### M002: A\n\nSlices:\n\n- M002-S01: a\n",
                encoding="utf-8",
            )

            status = build_status(root)

        codes = _codes(status.diagnostics)
        self.assertIn("multiple_detailed_roadmaps", codes)
        self.assertEqual(
            _severity(status.diagnostics, "multiple_detailed_roadmaps"), "warning"
        )
        self.assertEqual(
            status.detailed_roadmap.name, "development_roadmap_frutlups.md"
        )

    def test_no_parsed_milestones_emits_error(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
                "# Active Roadmap\n\nNo milestones here, just prose.\n",
                encoding="utf-8",
            )

            status = build_status(root)

        codes = _codes(status.diagnostics)
        self.assertIn("no_milestones_parsed", codes)
        self.assertEqual(
            _severity(status.diagnostics, "no_milestones_parsed"), "error"
        )
        self.assertEqual(status.milestones, ())

    def test_unknown_status_emits_warning_per_milestone(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
                ACTIVE_HEADER
                + "### M001: A\n\nStatus: wandering\n\n"
                + "### M002: B\n\nStatus: active\n\n"
                + "### M003: C\n\nStatus: mystery\n",
                encoding="utf-8",
            )

            status = build_status(root)

        unknown_diags = [
            diag for diag in status.diagnostics
            if diag.code == "unknown_milestone_status"
        ]
        self.assertEqual(len(unknown_diags), 2)
        messages = " ".join(diag.message for diag in unknown_diags)
        self.assertIn("M001", messages)
        self.assertIn("M003", messages)
        self.assertNotIn("M002", messages)

    def test_next_milestone_has_no_slices_emits_warning(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
                ACTIVE_HEADER + "### M002: A\n\nStatus: active\n",
                encoding="utf-8",
            )
            (root / "03_experiments" / "development_roadmap_frutlups.md").write_text(
                DETAILED_HEADER + "### M003: Other\n\nSlices:\n\n- M003-S01: x\n",
                encoding="utf-8",
            )

            status = build_status(root)

        codes = _codes(status.diagnostics)
        self.assertIn("next_milestone_has_no_slices", codes)
        self.assertEqual(
            _severity(status.diagnostics, "next_milestone_has_no_slices"),
            "warning",
        )
        self.assertIsNone(status.next_slice)
        msg = _message(status.diagnostics, "next_milestone_has_no_slices")
        self.assertIn("M002", msg)

    def test_all_slices_accepted_emits_info(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
                ACTIVE_HEADER + "### M002: A\n\nStatus: active\n",
                encoding="utf-8",
            )
            (root / "03_experiments" / "development_roadmap_frutlups.md").write_text(
                DETAILED_HEADER
                + "### M002: A\n\nSlices:\n\n"
                + "- M002-S01: first\n- M002-S02: second\n",
                encoding="utf-8",
            )
            reviews = root / "05_governance" / "reviews"
            reviews.mkdir(parents=True, exist_ok=True)
            (reviews / "m002_s01_first_review_report.md").write_text(
                "## Verdict\n\npass\n", encoding="utf-8"
            )
            (reviews / "m002_s02_second_review_report.md").write_text(
                "## Verdict\n\npass\n", encoding="utf-8"
            )

            status = build_status(root)

        codes = _codes(status.diagnostics)
        self.assertIn("next_slice_unavailable_all_accepted", codes)
        self.assertEqual(
            _severity(
                status.diagnostics, "next_slice_unavailable_all_accepted"
            ),
            "info",
        )
        self.assertIsNone(status.next_slice)


class HappyPathTests(unittest.TestCase):
    def test_clean_synthetic_project_has_no_diagnostics(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
                ACTIVE_HEADER + "### M002: Active One\n\nStatus: active\n",
                encoding="utf-8",
            )
            (root / "03_experiments" / "development_roadmap_frutlups.md").write_text(
                DETAILED_HEADER
                + "### M002: Active One\n\nSlices:\n\n- M002-S01: first\n",
                encoding="utf-8",
            )

            status = build_status(root)

        self.assertEqual(status.diagnostics, ())
        self.assertIsNotNone(status.next_slice)
        self.assertEqual(status.next_slice.slice_id, "M002-S01")

    def test_live_repository_has_no_error_or_warning_diagnostics(self) -> None:
        # The live repository should always parse cleanly: an active
        # milestone is resolvable and no roadmap diagnostic with
        # severity ``error`` or ``warning`` fires. Informational
        # diagnostics (for example ``next_slice_unavailable_all_accepted``
        # once every slice of the active milestone has been accepted)
        # are expected as the project advances and do not represent a
        # regression. The specific next milestone / slice IDs are
        # intentionally not asserted to avoid brittleness across
        # future slices.
        from frutlups.state import DiagnosticSeverity

        repo_root = _development_repo_root()
        if repo_root is None:
            self.skipTest("live artifact development repository is not present")
        status = build_status(repo_root)

        # The project may have reached its completed end state (all milestones
        # completed -> no next milestone); require only that the roadmap parsed
        # cleanly to milestones, not that an unaccepted next milestone exists.
        self.assertIsNotNone(status.active_roadmap)
        self.assertTrue(status.milestones)
        blocking = [
            diag
            for diag in status.diagnostics
            if diag.severity
            in (DiagnosticSeverity.WARNING, DiagnosticSeverity.ERROR)
        ]
        self.assertEqual(
            blocking, [],
            f"unexpected error/warning diagnostics: {blocking!r}",
        )


class CliDiagnosticsOutputTests(unittest.TestCase):
    def test_human_output_omits_diagnostics_block_when_none(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
                ACTIVE_HEADER + "### M002: Active\n\nStatus: active\n",
                encoding="utf-8",
            )
            (root / "03_experiments" / "development_roadmap_frutlups.md").write_text(
                DETAILED_HEADER + "### M002: Active\n\nSlices:\n\n- M002-S01: x\n",
                encoding="utf-8",
            )
            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["status", str(root)])

        self.assertEqual(exit_code, 0)
        out = stdout.getvalue()
        self.assertNotIn("Diagnostics:", out)

    def test_human_output_includes_diagnostics_block_when_present(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            # Missing active roadmap → diagnostics fire
            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["status", str(root)])

        self.assertEqual(exit_code, 0)
        out = stdout.getvalue()
        self.assertIn("Diagnostics:", out)
        self.assertIn("[error] no_active_roadmap:", out)

    def test_json_output_always_includes_diagnostics_key(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
                ACTIVE_HEADER + "### M002: Active\n\nStatus: active\n",
                encoding="utf-8",
            )
            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["status", str(root), "--json"])

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertIn("diagnostics", payload)
        self.assertIsInstance(payload["diagnostics"], list)
        codes = [diag["code"] for diag in payload["diagnostics"]]
        self.assertIn("no_detailed_roadmap", codes)


def _codes(diagnostics: tuple[Diagnostic, ...]) -> list[str]:
    return [diag.code for diag in diagnostics]


def _severity(
    diagnostics: tuple[Diagnostic, ...], code: str
) -> str:
    for diag in diagnostics:
        if diag.code == code:
            return diag.severity.value
    raise AssertionError(f"diagnostic with code {code!r} not found")


def _message(diagnostics: tuple[Diagnostic, ...], code: str) -> str:
    for diag in diagnostics:
        if diag.code == code:
            return diag.message
    raise AssertionError(f"diagnostic with code {code!r} not found")


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
