"""Tests for the typed prompt inventory model."""

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from frutlups.artifacts import PromptDirectories
from frutlups.cli import main
from frutlups.project import build_status
from frutlups.prompts import (
    PromptArtifact,
    PromptKind,
    inventory_prompts,
    inventory_prompts_in_dir,
    parse_sequence,
)


class PromptKindTests(unittest.TestCase):
    def test_kind_values_are_canonical_strings(self) -> None:
        self.assertEqual(PromptKind.CODING.value, "coding")
        self.assertEqual(PromptKind.REVIEW.value, "review")


class ParseSequenceTests(unittest.TestCase):
    def test_parses_zero_padded_prefix(self) -> None:
        self.assertEqual(parse_sequence("001_foo.md"), 1)
        self.assertEqual(parse_sequence("014_review_obs.md"), 14)
        self.assertEqual(parse_sequence("100_review_m001.md"), 100)

    def test_returns_none_for_non_numeric_prefix(self) -> None:
        self.assertIsNone(parse_sequence("handoff.md"))
        self.assertIsNone(parse_sequence("README.md"))
        self.assertIsNone(parse_sequence("notes_001.md"))

    def test_returns_none_for_missing_separator(self) -> None:
        self.assertIsNone(parse_sequence("001foo.md"))
        self.assertIsNone(parse_sequence("001.md"))

    def test_never_raises(self) -> None:
        for name in ("", "x", "_underscore.md", "999_thing.md", "a_b.md"):
            parse_sequence(name)


class PromptArtifactTests(unittest.TestCase):
    def test_to_dict_uses_canonical_kind_string(self) -> None:
        artifact = PromptArtifact(
            kind=PromptKind.CODING,
            path=Path("/tmp/prompts/for_coding_agent/001_foo.md"),
            filename="001_foo.md",
            sequence=1,
        )

        self.assertEqual(
            artifact.to_dict(),
            {
                "kind": "coding",
                "path": str(Path("/tmp/prompts/for_coding_agent/001_foo.md")),
                "filename": "001_foo.md",
                "sequence": 1,
            },
        )

    def test_sequence_can_be_none(self) -> None:
        artifact = PromptArtifact(
            kind=PromptKind.REVIEW,
            path=Path("/tmp/prompts/for_review_agent/handoff.md"),
            filename="handoff.md",
            sequence=None,
        )

        self.assertIsNone(artifact.sequence)
        self.assertIsNone(artifact.to_dict()["sequence"])


class InventoryPromptsInDirTests(unittest.TestCase):
    def test_returns_empty_for_missing_directory(self) -> None:
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does_not_exist"
            self.assertEqual(
                inventory_prompts_in_dir(missing, PromptKind.CODING), ()
            )

    def test_returns_empty_for_directory_with_no_markdown_files(self) -> None:
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "notes.txt").write_text("ignored", encoding="utf-8")
            (Path(tmp) / "config.json").write_text("{}", encoding="utf-8")

            self.assertEqual(
                inventory_prompts_in_dir(Path(tmp), PromptKind.CODING), ()
            )

    def test_inventories_markdown_files_sorted_by_filename(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "002_b.md").write_text("b", encoding="utf-8")
            (root / "010_c.md").write_text("c", encoding="utf-8")
            (root / "001_a.md").write_text("a", encoding="utf-8")

            artifacts = inventory_prompts_in_dir(root, PromptKind.CODING)

        self.assertEqual(
            [a.filename for a in artifacts],
            ["001_a.md", "002_b.md", "010_c.md"],
        )
        self.assertTrue(all(a.kind == PromptKind.CODING for a in artifacts))

    def test_parses_sequence_numbers_when_present(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "001_first.md").write_text("a", encoding="utf-8")
            (root / "002_second.md").write_text("b", encoding="utf-8")
            (root / "100_review.md").write_text("c", encoding="utf-8")

            artifacts = inventory_prompts_in_dir(root, PromptKind.REVIEW)

        self.assertEqual([a.sequence for a in artifacts], [1, 2, 100])

    def test_inventories_non_conforming_filenames_without_validation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "001_normal.md").write_text("a", encoding="utf-8")
            (root / "handoff.md").write_text("b", encoding="utf-8")
            (root / "README.md").write_text("c", encoding="utf-8")
            (root / "weird-name.md").write_text("d", encoding="utf-8")

            artifacts = inventory_prompts_in_dir(root, PromptKind.CODING)

        by_name = {a.filename: a for a in artifacts}
        self.assertIn("handoff.md", by_name)
        self.assertIn("README.md", by_name)
        self.assertIn("weird-name.md", by_name)
        self.assertIsNone(by_name["handoff.md"].sequence)
        self.assertIsNone(by_name["README.md"].sequence)
        self.assertIsNone(by_name["weird-name.md"].sequence)
        self.assertEqual(by_name["001_normal.md"].sequence, 1)

    def test_path_field_is_full_path(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "001_x.md").write_text("a", encoding="utf-8")

            artifacts = inventory_prompts_in_dir(root, PromptKind.CODING)

        self.assertEqual(artifacts[0].path, root / "001_x.md")

    def test_skips_directories_named_with_md(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "001_real.md").write_text("a", encoding="utf-8")
            (root / "fake.md").mkdir()

            artifacts = inventory_prompts_in_dir(root, PromptKind.CODING)

        self.assertEqual([a.filename for a in artifacts], ["001_real.md"])


class InventoryPromptsCombinedTests(unittest.TestCase):
    def test_returns_coding_then_review_each_sorted_by_filename(self) -> None:
        with TemporaryDirectory() as tmp:
            prompts_root = Path(tmp) / "prompts"
            coding_dir = prompts_root / "for_coding_agent"
            review_dir = prompts_root / "for_review_agent"
            coding_dir.mkdir(parents=True)
            review_dir.mkdir(parents=True)
            (coding_dir / "002_c2.md").write_text("a", encoding="utf-8")
            (coding_dir / "001_c1.md").write_text("a", encoding="utf-8")
            (review_dir / "002_r2.md").write_text("a", encoding="utf-8")
            (review_dir / "001_r1.md").write_text("a", encoding="utf-8")

            artifacts = inventory_prompts(PromptDirectories(prompts_root))

        self.assertEqual(
            [(a.kind.value, a.filename) for a in artifacts],
            [
                ("coding", "001_c1.md"),
                ("coding", "002_c2.md"),
                ("review", "001_r1.md"),
                ("review", "002_r2.md"),
            ],
        )

    def test_both_missing_directories_yield_empty_inventory(self) -> None:
        with TemporaryDirectory() as tmp:
            prompts_root = Path(tmp) / "prompts"
            # do not create any subdirectories
            artifacts = inventory_prompts(PromptDirectories(prompts_root))

        self.assertEqual(artifacts, ())

    def test_only_one_kind_present_still_works(self) -> None:
        with TemporaryDirectory() as tmp:
            prompts_root = Path(tmp) / "prompts"
            review_dir = prompts_root / "for_review_agent"
            review_dir.mkdir(parents=True)
            (review_dir / "001_r1.md").write_text("a", encoding="utf-8")

            artifacts = inventory_prompts(PromptDirectories(prompts_root))

        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0].kind, PromptKind.REVIEW)


class ProjectStatusPromptArtifactsTests(unittest.TestCase):
    def test_build_status_exposes_prompt_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
                "### M002: Active\n\nStatus: active\n", encoding="utf-8"
            )
            (root / "prompts" / "for_coding_agent" / "001_alpha.md").write_text(
                "x", encoding="utf-8"
            )
            (root / "prompts" / "for_coding_agent" / "002_beta.md").write_text(
                "x", encoding="utf-8"
            )
            (root / "prompts" / "for_review_agent" / "001_review.md").write_text(
                "x", encoding="utf-8"
            )

            status = build_status(root)

        self.assertEqual(status.prompts.coding_count, 2)
        self.assertEqual(status.prompts.review_count, 1)
        self.assertEqual(len(status.prompt_artifacts), 3)
        self.assertEqual(
            [(a.kind.value, a.filename) for a in status.prompt_artifacts],
            [
                ("coding", "001_alpha.md"),
                ("coding", "002_beta.md"),
                ("review", "001_review.md"),
            ],
        )

    def test_build_status_prompt_artifacts_empty_when_no_prompts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
                "### M002: Active\n\nStatus: active\n", encoding="utf-8"
            )

            status = build_status(root)

        self.assertEqual(status.prompts.coding_count, 0)
        self.assertEqual(status.prompts.review_count, 0)
        self.assertEqual(status.prompt_artifacts, ())


class CliPromptArtifactsJsonTests(unittest.TestCase):
    def test_existing_prompt_count_fields_still_present(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_template(root)
            (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
                "### M002: Active\n\nStatus: active\n", encoding="utf-8"
            )
            (root / "prompts" / "for_coding_agent" / "001_a.md").write_text(
                "x", encoding="utf-8"
            )
            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["status", str(root), "--json"])

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["prompts"]["coding_count"], 1)
        self.assertEqual(payload["prompts"]["review_count"], 0)
        self.assertIn("prompt_artifacts", payload)
        self.assertEqual(len(payload["prompt_artifacts"]), 1)
        self.assertEqual(payload["prompt_artifacts"][0]["filename"], "001_a.md")
        self.assertEqual(payload["prompt_artifacts"][0]["kind"], "coding")
        self.assertEqual(payload["prompt_artifacts"][0]["sequence"], 1)


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
