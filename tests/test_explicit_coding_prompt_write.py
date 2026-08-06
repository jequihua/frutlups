"""Tests for the explicit coding-prompt write surface."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from frutlups.prompt_template import (
    CodingPromptTemplate,
    CodingPromptWriteCommand,
    CodingPromptWriteResult,
    preview_coding_prompt,
    write_coding_prompt,
)


def _valid_template(**overrides: object) -> CodingPromptTemplate:
    defaults: dict[str, object] = dict(
        sequence=14,
        milestone_id="M004",
        slice_id="M004-S03",
        slug="frutlups_m004_s03_explicit_coding_prompt_write",
        title="Explicit Coding Prompt Write",
        role_instructions="You are the coding agent for frutlups.",
        required_reading=("CLAUDE.md", "README.md"),
        scope_paths=("08_pkg/src/frutlups/",),
        non_goals=("do not render markdown",),
        definition_of_done=("writer exists",),
        verification_commands=("python -m unittest discover -s tests",),
        self_report_path=(
            "05_governance/reviews/"
            "m004_s03_explicit_coding_prompt_write_self_report.md"
        ),
    )
    defaults.update(overrides)
    return CodingPromptTemplate(**defaults)  # type: ignore[arg-type]


def _command(
    project_root: Path,
    *,
    template: CodingPromptTemplate | None = None,
    content: object = "# hello\n",
    overwrite: bool = False,
) -> CodingPromptWriteCommand:
    return CodingPromptWriteCommand(
        project_root=project_root,
        template=template if template is not None else _valid_template(),
        content=content,  # type: ignore[arg-type]
        overwrite=overwrite,
    )


class CommandAndResultShapeTests(unittest.TestCase):
    def test_command_is_frozen(self) -> None:
        with TemporaryDirectory() as tmp:
            command = _command(Path(tmp))
            with self.assertRaises(Exception):
                command.content = "x"  # type: ignore[misc]

    def test_result_is_frozen(self) -> None:
        with TemporaryDirectory() as tmp:
            result = write_coding_prompt(_command(Path(tmp)))
            with self.assertRaises(Exception):
                result.wrote = False  # type: ignore[misc]

    def test_result_to_dict_for_successful_write(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = write_coding_prompt(_command(root))

        payload = result.to_dict()
        self.assertEqual(
            set(payload.keys()),
            {"preview", "target_path", "wrote", "errors", "overwrote"},
        )
        self.assertIsInstance(payload["preview"], dict)
        self.assertEqual(payload["preview"]["kind"], "coding")
        self.assertEqual(
            payload["preview"]["filename"],
            "014_frutlups_m004_s03_explicit_coding_prompt_write.md",
        )
        self.assertTrue(payload["wrote"])
        self.assertFalse(payload["overwrote"])
        self.assertEqual(payload["errors"], [])
        self.assertIsInstance(payload["target_path"], str)
        self.assertTrue(payload["target_path"].endswith(
            "014_frutlups_m004_s03_explicit_coding_prompt_write.md"
        ))

    def test_result_embeds_preview_via_preview_to_dict(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = _command(root)
            result = write_coding_prompt(command)
        self.assertEqual(
            result.preview.to_dict(),
            preview_coding_prompt(command.template).to_dict(),
        )


class SuccessfulWriteTests(unittest.TestCase):
    def test_writes_supplied_content_to_deterministic_path(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = write_coding_prompt(
                _command(root, content="# original\n")
            )

            self.assertTrue(result.wrote)
            self.assertFalse(result.overwrote)
            written_path = (
                root
                / "prompts"
                / "for_coding_agent"
                / "014_frutlups_m004_s03_explicit_coding_prompt_write.md"
            )
            self.assertTrue(written_path.exists())
            self.assertEqual(
                written_path.read_text(encoding="utf-8"), "# original\n"
            )
            self.assertEqual(Path(result.target_path), written_path)

    def test_creates_coding_directory_if_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertFalse(
                (root / "prompts" / "for_coding_agent").exists()
            )

            result = write_coding_prompt(_command(root))

            self.assertTrue(result.wrote)
            self.assertTrue(
                (root / "prompts" / "for_coding_agent").is_dir()
            )

    def test_does_not_touch_review_prompt_directory(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_coding_prompt(_command(root))
            self.assertFalse(
                (root / "prompts" / "for_review_agent").exists(),
                "writer must not create or touch the review prompt directory",
            )


class InvalidTemplateTests(unittest.TestCase):
    def test_invalid_sequence_does_not_write(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = _command(root, template=_valid_template(sequence=0))

            result = write_coding_prompt(command)

            self.assertFalse(result.wrote)
            self.assertFalse(result.overwrote)
            self.assertIn(
                "sequence must be a positive integer", result.errors
            )
            self.assertEqual(result.target_path, "")
            self.assertFalse(
                (root / "prompts" / "for_coding_agent").exists(),
                "no directory should have been created on failure",
            )

    def test_above_max_sequence_does_not_write(self) -> None:
        from frutlups.prompt_template import MAX_PROMPT_SEQUENCE

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = _command(
                root, template=_valid_template(sequence=1000)
            )

            result = write_coding_prompt(command)

            self.assertFalse(result.wrote)
            self.assertIn(
                f"sequence must be at most {MAX_PROMPT_SEQUENCE}",
                result.errors,
            )

    def test_empty_slug_does_not_write(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = _command(root, template=_valid_template(slug=""))

            result = write_coding_prompt(command)

            self.assertFalse(result.wrote)
            self.assertIn("slug must be a non-empty string", result.errors)
            self.assertEqual(result.target_path, "")


class InvalidContentTests(unittest.TestCase):
    def test_empty_content_does_not_write(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = _command(root, content="")

            result = write_coding_prompt(command)

            self.assertFalse(result.wrote)
            self.assertIn(
                "content must be a non-empty string", result.errors
            )
            self.assertEqual(result.target_path, "")

    def test_non_string_content_does_not_write(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = _command(root, content=42)

            result = write_coding_prompt(command)

            self.assertFalse(result.wrote)
            self.assertIn(
                "content must be a non-empty string", result.errors
            )

    def test_none_content_does_not_write(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = _command(root, content=None)

            result = write_coding_prompt(command)

            self.assertFalse(result.wrote)
            self.assertIn(
                "content must be a non-empty string", result.errors
            )


class OverwriteBehaviorTests(unittest.TestCase):
    def test_existing_target_without_overwrite_returns_error(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            # first write succeeds
            self.assertTrue(write_coding_prompt(_command(root)).wrote)

            second = write_coding_prompt(
                _command(root, content="# new\n")
            )

            self.assertFalse(second.wrote)
            self.assertFalse(second.overwrote)
            self.assertTrue(
                any("already exists" in err for err in second.errors),
                f"expected 'already exists' error, got {second.errors}",
            )
            # original content preserved
            written_path = (
                root
                / "prompts"
                / "for_coding_agent"
                / "014_frutlups_m004_s03_explicit_coding_prompt_write.md"
            )
            self.assertEqual(
                written_path.read_text(encoding="utf-8"), "# hello\n"
            )

    def test_existing_target_with_overwrite_replaces_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertTrue(write_coding_prompt(_command(root)).wrote)

            second = write_coding_prompt(
                _command(root, content="# replaced\n", overwrite=True)
            )

            self.assertTrue(second.wrote)
            self.assertTrue(second.overwrote)
            written_path = (
                root
                / "prompts"
                / "for_coding_agent"
                / "014_frutlups_m004_s03_explicit_coding_prompt_write.md"
            )
            self.assertEqual(
                written_path.read_text(encoding="utf-8"), "# replaced\n"
            )

    def test_first_write_overwrote_is_false(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = write_coding_prompt(_command(root))
            self.assertTrue(result.wrote)
            self.assertFalse(result.overwrote)


class PathSafetyTests(unittest.TestCase):
    def test_slug_with_path_traversal_does_not_escape_directory(self) -> None:
        # A malicious slug containing path traversal must be rejected;
        # the writer must refuse to write outside
        # prompts/for_coding_agent/. The filename helper produces
        # `014_../../etc/passwd.md`, which would resolve outside the
        # coding directory.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = _command(
                root, template=_valid_template(slug="../../etc/passwd")
            )

            result = write_coding_prompt(command)

            self.assertFalse(result.wrote)
            self.assertTrue(
                any(
                    "prompts/for_coding_agent" in err for err in result.errors
                ),
                f"expected path-escape error, got {result.errors}",
            )
            # confirm nothing was written outside the temporary tree
            self.assertFalse((root / "etc").exists())


class PreviewIsStillNonWritingTests(unittest.TestCase):
    def test_preview_does_not_create_any_file(self) -> None:
        # Regression: M004-S02 preview must remain pure even after the
        # M004-S03 writer is added.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = _valid_template()
            _ = preview_coding_prompt(template)
            self.assertFalse(
                (root / "prompts").exists(),
                "preview must not create any directories",
            )


class WriteResultTypeTests(unittest.TestCase):
    def test_write_result_is_correct_type(self) -> None:
        with TemporaryDirectory() as tmp:
            result = write_coding_prompt(_command(Path(tmp)))
        self.assertIsInstance(result, CodingPromptWriteResult)


if __name__ == "__main__":
    unittest.main()
