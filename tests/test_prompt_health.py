"""Tests for prompt-health computation, status integration, and CLI output."""

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from frutlups.cli import main
from frutlups.project import build_status
from frutlups.prompts import (
    PromptArtifact,
    PromptHealth,
    PromptHealthFinding,
    PromptHealthSeverity,
    PromptKind,
    compute_prompt_health,
)


def _development_repo_root() -> Path | None:
    required = ("00_brief", "03_experiments", "05_governance", "06_infra", "08_pkg", "prompts")
    for candidate in Path(__file__).resolve().parents:
        if all((candidate / name).exists() for name in required):
            return candidate
    return None


def _coding(sequence: int | None, filename: str) -> PromptArtifact:
    return PromptArtifact(
        kind=PromptKind.CODING,
        path=Path(f"/tmp/prompts/for_coding_agent/{filename}"),
        filename=filename,
        sequence=sequence,
    )


def _review(sequence: int | None, filename: str) -> PromptArtifact:
    return PromptArtifact(
        kind=PromptKind.REVIEW,
        path=Path(f"/tmp/prompts/for_review_agent/{filename}"),
        filename=filename,
        sequence=sequence,
    )


def _balanced(n: int) -> tuple[PromptArtifact, ...]:
    artifacts: list[PromptArtifact] = []
    for i in range(1, n + 1):
        artifacts.append(_coding(i, f"{i:03d}_coding_{i}.md"))
        artifacts.append(_review(i, f"{i:03d}_review_{i}.md"))
    return tuple(artifacts)


class PromptHealthSeverityTests(unittest.TestCase):
    def test_canonical_severity_strings(self) -> None:
        self.assertEqual(PromptHealthSeverity.INFO.value, "info")
        self.assertEqual(PromptHealthSeverity.WARNING.value, "warning")
        self.assertEqual(PromptHealthSeverity.ERROR.value, "error")


class PromptHealthFindingToDictTests(unittest.TestCase):
    def test_to_dict_shape(self) -> None:
        finding = PromptHealthFinding(
            severity=PromptHealthSeverity.WARNING,
            code="unmatched_coding_prompt",
            kind=PromptKind.CODING,
            sequence=9,
            filenames=("009_a.md",),
            message="coding prompt sequence 009 has no matching review prompt.",
        )

        self.assertEqual(
            finding.to_dict(),
            {
                "severity": "warning",
                "code": "unmatched_coding_prompt",
                "kind": "coding",
                "sequence": 9,
                "filenames": ["009_a.md"],
                "message": "coding prompt sequence 009 has no matching review prompt.",
            },
        )


class PromptHealthToDictTests(unittest.TestCase):
    def test_to_dict_shape_for_happy_inventory(self) -> None:
        health = compute_prompt_health(_balanced(2))

        payload = health.to_dict()
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["total"], 4)
        self.assertEqual(payload["inherited"], 0)
        self.assertEqual(payload["ignored"], 0)
        self.assertEqual(payload["analyzed"], 4)
        self.assertEqual(payload["findings"], [])

    def test_to_dict_serialises_findings(self) -> None:
        artifacts = (
            _coding(1, "001_a.md"),
            _coding(2, "002_b.md"),
            _review(1, "001_a.md"),
        )
        health = compute_prompt_health(artifacts)

        payload = health.to_dict()
        self.assertFalse(payload["ok"])
        self.assertEqual(len(payload["findings"]), 1)
        finding = payload["findings"][0]
        self.assertEqual(finding["code"], "unmatched_coding_prompt")
        self.assertEqual(finding["severity"], "warning")
        self.assertEqual(finding["kind"], "coding")
        self.assertEqual(finding["sequence"], 2)


class ComputePromptHealthCases(unittest.TestCase):
    def test_happy_paired_inventory_is_ok(self) -> None:
        health = compute_prompt_health(_balanced(3))
        self.assertTrue(health.ok)
        self.assertEqual(health.findings, ())
        self.assertEqual(health.total, 6)
        self.assertEqual(health.inherited, 0)
        self.assertEqual(health.ignored, 0)
        self.assertEqual(health.analyzed, 6)

    def test_inherited_examples_counted_and_ignored(self) -> None:
        # The inherited examples share sequences 1, 2, 14 across both
        # kinds. Without filtering they would produce duplicate findings;
        # with the M003-S03 filter they should be ignored and the real
        # paired set 1..3 should leave the inventory healthy.
        artifacts = (
            _coding(1, "001_frutlups_a.md"),
            _coding(2, "002_frutlups_b.md"),
            _coding(3, "003_frutlups_c.md"),
            _review(1, "001_review_frutlups_a.md"),
            _review(2, "002_review_frutlups_b.md"),
            _review(3, "003_review_frutlups_c.md"),
            _coding(1, "001_geecomposer_core_foundations.md"),
            _coding(2, "002_geecomposer_core_foundations_closure.md"),
            _coding(14, "014_geecomposer_milestone_006_cleanup.md"),
            _review(1, "001_review_core_foundations.md"),
            _review(2, "002_review_core_foundations_corrective.md"),
            _review(14, "014_review_observation_count_cleanup.md"),
        )

        health = compute_prompt_health(artifacts)

        self.assertTrue(health.ok)
        self.assertEqual(health.findings, ())
        self.assertEqual(health.total, 12)
        self.assertEqual(health.inherited, 6)
        self.assertEqual(health.ignored, 6)
        self.assertEqual(health.analyzed, 6)

    def test_duplicate_sequence_makes_health_not_ok(self) -> None:
        artifacts = (
            _coding(1, "001_a.md"),
            _coding(1, "001_b.md"),
            _review(1, "001_a.md"),
        )
        health = compute_prompt_health(artifacts)
        self.assertFalse(health.ok)
        codes = [f.code for f in health.findings]
        self.assertIn("duplicate_prompt_sequence", codes)
        self.assertTrue(
            all(f.severity == PromptHealthSeverity.WARNING for f in health.findings)
        )

    def test_missing_sequence_makes_health_not_ok(self) -> None:
        artifacts = (
            _coding(1, "001_a.md"),
            _coding(3, "003_c.md"),
            _review(1, "001_a.md"),
            _review(2, "002_b.md"),
            _review(3, "003_c.md"),
        )
        health = compute_prompt_health(artifacts)
        self.assertFalse(health.ok)
        codes = [f.code for f in health.findings]
        self.assertIn("missing_prompt_sequence", codes)


class UnmatchedSequencesAreWarnings(unittest.TestCase):
    def test_highest_unmatched_coding_sequence_is_warning(self) -> None:
        # The newest coding prompt has no matching review prompt yet.
        # The project owner does not want an "in-flight" exception, so
        # this must still flip ok to False.
        artifacts = (
            _coding(1, "001_a.md"),
            _coding(2, "002_b.md"),
            _review(1, "001_a.md"),
            _review(2, "002_b.md"),
            _coding(3, "003_c.md"),  # newest coding, no review yet
        )
        health = compute_prompt_health(artifacts)
        self.assertFalse(health.ok)
        unmatched = [f for f in health.findings if f.code == "unmatched_coding_prompt"]
        self.assertEqual(len(unmatched), 1)
        self.assertEqual(unmatched[0].sequence, 3)
        self.assertEqual(unmatched[0].severity, PromptHealthSeverity.WARNING)

    def test_non_highest_unmatched_coding_sequence_is_warning(self) -> None:
        artifacts = (
            _coding(1, "001_a.md"),
            _coding(2, "002_b.md"),
            _coding(3, "003_c.md"),
            _review(1, "001_a.md"),
            _review(3, "003_c.md"),
        )
        health = compute_prompt_health(artifacts)
        self.assertFalse(health.ok)
        unmatched_coding = [
            f for f in health.findings if f.code == "unmatched_coding_prompt"
        ]
        self.assertEqual([f.sequence for f in unmatched_coding], [2])

    def test_unmatched_review_sequence_is_warning(self) -> None:
        artifacts = (
            _coding(1, "001_a.md"),
            _review(1, "001_a.md"),
            _review(2, "002_b.md"),
        )
        health = compute_prompt_health(artifacts)
        self.assertFalse(health.ok)
        unmatched_review = [
            f for f in health.findings if f.code == "unmatched_review_prompt"
        ]
        self.assertEqual([f.sequence for f in unmatched_review], [2])
        self.assertTrue(
            all(f.severity == PromptHealthSeverity.WARNING for f in unmatched_review)
        )


class ProjectStatusPromptHealthIntegrationTests(unittest.TestCase):
    def test_build_status_exposes_prompt_health(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
                "### M003: Active\n\nStatus: active\n", encoding="utf-8"
            )
            (root / "prompts" / "for_coding_agent" / "001_a.md").write_text(
                "x", encoding="utf-8"
            )
            (root / "prompts" / "for_review_agent" / "001_a.md").write_text(
                "x", encoding="utf-8"
            )

            status = build_status(root)

        self.assertIsInstance(status.prompt_health, PromptHealth)
        self.assertTrue(status.prompt_health.ok)
        self.assertEqual(status.prompt_health.findings, ())

    def test_build_status_prompt_health_flags_unmatched(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
                "### M003: Active\n\nStatus: active\n", encoding="utf-8"
            )
            (root / "prompts" / "for_coding_agent" / "001_a.md").write_text(
                "x", encoding="utf-8"
            )
            (root / "prompts" / "for_coding_agent" / "002_b.md").write_text(
                "x", encoding="utf-8"
            )
            (root / "prompts" / "for_review_agent" / "001_a.md").write_text(
                "x", encoding="utf-8"
            )

            status = build_status(root)

        self.assertFalse(status.prompt_health.ok)
        codes = [f.code for f in status.prompt_health.findings]
        self.assertIn("unmatched_coding_prompt", codes)


class CliPromptHealthJsonTests(unittest.TestCase):
    def test_json_includes_prompt_health_and_preserves_existing_keys(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
                "### M003: Active\n\nStatus: active\n", encoding="utf-8"
            )
            (root / "prompts" / "for_coding_agent" / "001_a.md").write_text(
                "x", encoding="utf-8"
            )
            (root / "prompts" / "for_review_agent" / "001_a.md").write_text(
                "x", encoding="utf-8"
            )
            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["status", str(root), "--json"])

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())

        # New key
        self.assertIn("prompt_health", payload)
        self.assertTrue(payload["prompt_health"]["ok"])
        self.assertEqual(payload["prompt_health"]["total"], 2)
        self.assertEqual(payload["prompt_health"]["inherited"], 0)
        self.assertEqual(payload["prompt_health"]["findings"], [])

        # Existing keys preserved
        self.assertEqual(payload["prompts"]["coding_count"], 1)
        self.assertEqual(payload["prompts"]["review_count"], 1)
        self.assertIn("prompt_artifacts", payload)
        self.assertIn("diagnostics", payload)


class CliPromptHealthHumanTests(unittest.TestCase):
    def test_human_output_prints_prompt_health_ok_after_prompts_line(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
                "### M003: Active\n\nStatus: active\n", encoding="utf-8"
            )
            (root / "prompts" / "for_coding_agent" / "001_a.md").write_text(
                "x", encoding="utf-8"
            )
            (root / "prompts" / "for_review_agent" / "001_a.md").write_text(
                "x", encoding="utf-8"
            )
            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["status", str(root)])

        self.assertEqual(exit_code, 0)
        out = stdout.getvalue()
        self.assertIn("Prompts: 1 coding, 1 review", out)
        self.assertIn("Prompt health: ok", out)
        lines = out.splitlines()
        prompts_idx = next(i for i, line in enumerate(lines) if line.startswith("Prompts:"))
        health_idx = next(
            i for i, line in enumerate(lines) if line.startswith("Prompt health:")
        )
        self.assertEqual(health_idx, prompts_idx + 1)

    def test_human_output_lists_warnings_when_not_ok(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
                "### M003: Active\n\nStatus: active\n", encoding="utf-8"
            )
            (root / "prompts" / "for_coding_agent" / "001_a.md").write_text(
                "x", encoding="utf-8"
            )
            (root / "prompts" / "for_coding_agent" / "002_b.md").write_text(
                "x", encoding="utf-8"
            )
            (root / "prompts" / "for_review_agent" / "001_a.md").write_text(
                "x", encoding="utf-8"
            )
            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["status", str(root)])

        self.assertEqual(exit_code, 0)
        out = stdout.getvalue()
        self.assertIn("Prompts: 2 coding, 1 review", out)
        self.assertIn("Prompt health: warnings (1)", out)
        self.assertIn("[warning] unmatched_coding_prompt:", out)


class LiveRepositoryPromptHealthTests(unittest.TestCase):
    def test_live_status_resolves_with_no_blocking_roadmap_diagnostics(self) -> None:
        # Informational diagnostics (for example
        # ``next_slice_unavailable_all_accepted`` once every detailed
        # slice of the active milestone has been accepted) are
        # expected as the project advances. Only ``warning`` and
        # ``error`` severities count as regressions for this check.
        from frutlups.state import DiagnosticSeverity

        repo_root = _development_repo_root()
        if repo_root is None:
            self.skipTest("live artifact development repository is not present")
        status = build_status(repo_root)

        # The roadmap must parse cleanly to milestones. The project may have
        # reached its completed end state (all milestones completed -> no next
        # milestone), so we do not require an unaccepted next milestone.
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
        # Prompt health may or may not be ok depending on whether the
        # matching review prompt for this slice has been authored yet.
        # We do not assert ok here to avoid brittleness; the dedicated
        # health-finding test asserts the warning shape.

    def test_live_unmatched_coding_prompt_is_a_health_warning(self) -> None:
        repo_root = _development_repo_root()
        if repo_root is None:
            self.skipTest("live artifact development repository is not present")
        status = build_status(repo_root)

        unmatched_coding = [
            f for f in status.prompt_health.findings
            if f.code == "unmatched_coding_prompt"
        ]
        # When this slice runs without its matching review prompt, the
        # health should expose exactly one unmatched coding finding for
        # the newest sequence. When the review prompt is later added,
        # this list becomes empty; either case is acceptable.
        for finding in unmatched_coding:
            self.assertEqual(finding.severity, PromptHealthSeverity.WARNING)
            self.assertEqual(finding.kind, PromptKind.CODING)


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
