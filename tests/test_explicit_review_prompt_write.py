"""Tests for the M006-S04 deterministic filename, preview, and explicit
write surface for review prompts."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from frutlups.review_prompt_template import (
    REVIEW_PROMPT_DIR,
    ReviewPromptPreview,
    ReviewPromptTemplate,
    ReviewPromptWriteCommand,
    ReviewPromptWriteResult,
    preview_review_prompt,
    render_review_prompt,
    review_prompt_filename,
    write_review_prompt,
)


def _valid_template(**overrides: object) -> ReviewPromptTemplate:
    defaults: dict[str, object] = dict(
        sequence=26,
        milestone_id="M006",
        slice_id="M006-S04",
        slug="frutlups_m006_s04_write_review_prompts_deterministically",
        title="Write Review Prompts Deterministically",
        role_instructions="You are the review agent for frutlups.",
        required_reading=(
            "CLAUDE.md",
            "README.md",
            "08_pkg/CONTEXT.md",
        ),
        coding_prompt_path=(
            "prompts/for_coding_agent/"
            "026_frutlups_m006_s04_write_review_prompts_deterministically.md"
        ),
        self_report_path=(
            "05_governance/reviews/"
            "m006_s04_write_review_prompts_deterministically_self_report.md"
        ),
        review_output_path=(
            "05_governance/reviews/"
            "m006_s04_write_review_prompts_deterministically_review_report.md"
        ),
        expected_changed_files=(
            "08_pkg/src/frutlups/review_prompt_template.py",
            "08_pkg/tests/test_explicit_review_prompt_write.py",
        ),
        verification_commands=(
            "python -m unittest discover -s tests",
            "python -m frutlups status ..",
        ),
        severity_guidance=(
            "blocker: correctness, safety, or scope failure",
            "major: missing required behavior or tests",
            "minor: small completeness gap",
            "nit: style or wording only",
        ),
        verdict_choices=("pass", "needs_work", "blocked", "override"),
        prior_review_paths=(
            "05_governance/reviews/"
            "m006_s03_review_prompt_severity_verdict_review_report.md",
        ),
        non_goals=(
            "do not parse verdicts",
            "do not add CLI commands",
        ),
        notes=("explicit writer only",),
    )
    defaults.update(overrides)
    return ReviewPromptTemplate(**defaults)  # type: ignore[arg-type]


def _command(
    project_root: Path,
    *,
    template: ReviewPromptTemplate | None = None,
    overwrite: bool = False,
) -> ReviewPromptWriteCommand:
    return ReviewPromptWriteCommand(
        project_root=project_root,
        template=template if template is not None else _valid_template(),
        overwrite=overwrite,
    )


# ---------------------------------------------------------------------------
# Filename helper
# ---------------------------------------------------------------------------

class ReviewPromptFilenameTests(unittest.TestCase):
    def test_builds_filename_from_sequence_and_slug(self) -> None:
        template = _valid_template(sequence=1, slug="m006_s04_test")
        self.assertEqual(
            review_prompt_filename(template),
            "001_review_m006_s04_test.md",
        )

    def test_uses_three_digit_zero_padded_sequence(self) -> None:
        template = _valid_template(sequence=26)
        self.assertEqual(
            review_prompt_filename(template),
            "026_review_frutlups_m006_s04_write_review_prompts_deterministically.md",
        )

    def test_returns_empty_string_when_slug_is_empty(self) -> None:
        template = _valid_template(slug="")
        self.assertEqual(review_prompt_filename(template), "")

    def test_returns_empty_string_when_slug_is_whitespace(self) -> None:
        template = _valid_template(slug="   ")
        self.assertEqual(review_prompt_filename(template), "")

    def test_returns_empty_string_when_sequence_is_zero(self) -> None:
        template = _valid_template(sequence=0)
        self.assertEqual(review_prompt_filename(template), "")

    def test_returns_empty_string_when_sequence_is_negative(self) -> None:
        template = _valid_template(sequence=-1)
        self.assertEqual(review_prompt_filename(template), "")

    def test_returns_empty_string_when_sequence_is_non_int(self) -> None:
        for bad in ("26", None, 1.5):
            with self.subTest(sequence=bad):
                template = _valid_template(sequence=bad)  # type: ignore[arg-type]
                self.assertEqual(review_prompt_filename(template), "")

    def test_returns_empty_string_for_sequence_above_999(self) -> None:
        template = _valid_template(sequence=1000)
        self.assertEqual(review_prompt_filename(template), "")

    def test_strips_only_surrounding_whitespace_from_slug(self) -> None:
        template = _valid_template(slug="  myslug  ")
        self.assertEqual(review_prompt_filename(template), "026_review_myslug.md")

    def test_preserves_slug_verbatim_after_strip(self) -> None:
        template = _valid_template(slug="UPPER_Case-Slug")
        self.assertEqual(
            review_prompt_filename(template), "026_review_UPPER_Case-Slug.md"
        )

    def test_never_raises_for_non_string_slug(self) -> None:
        template = _valid_template(slug=42)  # type: ignore[arg-type]
        self.assertEqual(review_prompt_filename(template), "")

    def test_filename_includes_review_prefix(self) -> None:
        template = _valid_template(sequence=5, slug="myslug")
        filename = review_prompt_filename(template)
        self.assertTrue(filename.startswith("005_review_"), filename)


# ---------------------------------------------------------------------------
# Preview to_dict shape
# ---------------------------------------------------------------------------

class ReviewPromptPreviewToDictTests(unittest.TestCase):
    def test_to_dict_for_valid_template(self) -> None:
        preview = preview_review_prompt(_valid_template())

        self.assertEqual(
            preview.to_dict(),
            {
                "kind": "review",
                "sequence": 26,
                "sequence_formatted": "026",
                "filename": (
                    "026_review_frutlups_m006_s04_"
                    "write_review_prompts_deterministically.md"
                ),
                "target_path": (
                    "prompts/for_review_agent/"
                    "026_review_frutlups_m006_s04_"
                    "write_review_prompts_deterministically.md"
                ),
                "valid": True,
                "errors": [],
                "would_write": True,
                "wrote": False,
            },
        )

    def test_to_dict_uses_plain_python_types(self) -> None:
        payload = preview_review_prompt(_valid_template()).to_dict()
        self.assertIsInstance(payload["kind"], str)
        self.assertIsInstance(payload["sequence"], int)
        self.assertIsInstance(payload["sequence_formatted"], str)
        self.assertIsInstance(payload["filename"], str)
        self.assertIsInstance(payload["target_path"], str)
        self.assertIsInstance(payload["valid"], bool)
        self.assertIsInstance(payload["errors"], list)
        self.assertIsInstance(payload["would_write"], bool)
        self.assertIsInstance(payload["wrote"], bool)


# ---------------------------------------------------------------------------
# Preview helper behaviour
# ---------------------------------------------------------------------------

class PreviewReviewPromptTests(unittest.TestCase):
    def test_valid_template_yields_writable_preview(self) -> None:
        preview = preview_review_prompt(_valid_template())

        self.assertEqual(preview.kind, "review")
        self.assertEqual(preview.sequence, 26)
        self.assertEqual(preview.sequence_formatted, "026")
        self.assertEqual(
            preview.filename,
            "026_review_frutlups_m006_s04_"
            "write_review_prompts_deterministically.md",
        )
        self.assertTrue(
            preview.target_path.startswith(f"{REVIEW_PROMPT_DIR}/"),
            preview.target_path,
        )
        self.assertTrue(preview.valid)
        self.assertEqual(preview.errors, ())
        self.assertTrue(preview.would_write)
        self.assertFalse(preview.wrote)

    def test_target_path_is_under_review_agent_directory(self) -> None:
        preview = preview_review_prompt(_valid_template())
        self.assertTrue(
            preview.target_path.startswith(f"{REVIEW_PROMPT_DIR}/"),
            preview.target_path,
        )

    def test_invalid_sequence_surfaces_errors(self) -> None:
        preview = preview_review_prompt(_valid_template(sequence=0))

        self.assertFalse(preview.valid)
        self.assertIn("sequence must be a positive integer", preview.errors)
        self.assertFalse(preview.would_write)
        self.assertFalse(preview.wrote)
        self.assertEqual(preview.sequence, 0)
        self.assertEqual(preview.sequence_formatted, "")
        self.assertEqual(preview.filename, "")
        self.assertEqual(preview.target_path, "")

    def test_empty_slug_surfaces_errors(self) -> None:
        preview = preview_review_prompt(_valid_template(slug=""))

        self.assertFalse(preview.valid)
        self.assertIn("slug must be a non-empty string", preview.errors)
        self.assertFalse(preview.would_write)
        self.assertEqual(preview.filename, "")
        self.assertEqual(preview.target_path, "")

    def test_invalid_collection_does_not_raise(self) -> None:
        preview = preview_review_prompt(
            _valid_template(required_reading=42)  # type: ignore[arg-type]
        )

        self.assertFalse(preview.valid)
        self.assertIn(
            "required_reading must be a tuple or list of non-empty strings",
            preview.errors,
        )
        self.assertFalse(preview.would_write)

    def test_non_int_sequence_yields_none_sequence(self) -> None:
        preview = preview_review_prompt(_valid_template(sequence="26"))  # type: ignore[arg-type]
        self.assertIsNone(preview.sequence)
        self.assertEqual(preview.sequence_formatted, "")
        self.assertEqual(preview.filename, "")
        self.assertFalse(preview.would_write)

    def test_wrote_is_always_false(self) -> None:
        for overrides in (
            {},
            {"sequence": 0},
            {"slug": ""},
            {"required_reading": ()},  # type: ignore[arg-type]
        ):
            with self.subTest(overrides=overrides):
                preview = preview_review_prompt(_valid_template(**overrides))
                self.assertFalse(preview.wrote)

    def test_preview_is_pure_no_filesystem_required(self) -> None:
        template = _valid_template(
            coding_prompt_path="/does/not/exist/prompt.md",
        )
        preview = preview_review_prompt(template)
        self.assertTrue(preview.valid)
        self.assertTrue(preview.would_write)


class PreviewFrozenInvariantTests(unittest.TestCase):
    def test_preview_is_frozen(self) -> None:
        preview = preview_review_prompt(_valid_template())
        with self.assertRaises(Exception):
            preview.wrote = True  # type: ignore[misc]

    def test_preview_is_correct_type(self) -> None:
        self.assertIsInstance(
            preview_review_prompt(_valid_template()), ReviewPromptPreview
        )


# ---------------------------------------------------------------------------
# Write command / result shape
# ---------------------------------------------------------------------------

class CommandAndResultShapeTests(unittest.TestCase):
    def test_command_is_frozen(self) -> None:
        with TemporaryDirectory() as tmp:
            command = _command(Path(tmp))
            with self.assertRaises(Exception):
                command.overwrite = True  # type: ignore[misc]

    def test_result_is_frozen(self) -> None:
        with TemporaryDirectory() as tmp:
            result = write_review_prompt(_command(Path(tmp)))
            with self.assertRaises(Exception):
                result.wrote = False  # type: ignore[misc]

    def test_result_to_dict_for_successful_write(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = write_review_prompt(_command(root))

        payload = result.to_dict()
        self.assertEqual(
            set(payload.keys()),
            {"preview", "target_path", "wrote", "errors", "overwrote"},
        )
        self.assertIsInstance(payload["preview"], dict)
        self.assertEqual(payload["preview"]["kind"], "review")
        self.assertEqual(
            payload["preview"]["filename"],
            "026_review_frutlups_m006_s04_"
            "write_review_prompts_deterministically.md",
        )
        self.assertTrue(payload["wrote"])
        self.assertFalse(payload["overwrote"])
        self.assertEqual(payload["errors"], [])
        self.assertIsInstance(payload["target_path"], str)
        self.assertTrue(
            payload["target_path"].endswith(
                "026_review_frutlups_m006_s04_"
                "write_review_prompts_deterministically.md"
            )
        )

    def test_result_embeds_preview_via_preview_to_dict(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = _command(root)
            result = write_review_prompt(command)
        self.assertEqual(
            result.preview.to_dict(),
            preview_review_prompt(command.template).to_dict(),
        )

    def test_result_is_correct_type(self) -> None:
        with TemporaryDirectory() as tmp:
            result = write_review_prompt(_command(Path(tmp)))
        self.assertIsInstance(result, ReviewPromptWriteResult)


# ---------------------------------------------------------------------------
# Successful write
# ---------------------------------------------------------------------------

class SuccessfulWriteTests(unittest.TestCase):
    def test_writes_rendered_content_to_deterministic_path(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = _valid_template()
            result = write_review_prompt(_command(root, template=template))

            self.assertTrue(result.wrote)
            self.assertFalse(result.overwrote)
            written_path = (
                root
                / "prompts"
                / "for_review_agent"
                / "026_review_frutlups_m006_s04_"
                "write_review_prompts_deterministically.md"
            )
            self.assertTrue(written_path.exists())
            expected_content = render_review_prompt(template).content
            self.assertEqual(
                written_path.read_text(encoding="utf-8"), expected_content
            )
            self.assertEqual(Path(result.target_path), written_path)

    def test_creates_review_directory_if_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertFalse(
                (root / "prompts" / "for_review_agent").exists()
            )

            result = write_review_prompt(_command(root))

            self.assertTrue(result.wrote)
            self.assertTrue(
                (root / "prompts" / "for_review_agent").is_dir()
            )

    def test_written_content_is_valid_rendered_markdown(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = _valid_template()
            write_review_prompt(_command(root, template=template))

            written_path = (
                root
                / "prompts"
                / "for_review_agent"
                / "026_review_frutlups_m006_s04_"
                "write_review_prompts_deterministically.md"
            )
            content = written_path.read_text(encoding="utf-8")
            self.assertTrue(content.startswith("# Review Prompt 026:"))
            self.assertIn("M006-S04", content)

    def test_content_is_utf8_encoded(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = write_review_prompt(_command(root))
            self.assertTrue(result.wrote)
            written_path = Path(result.target_path)
            content = written_path.read_bytes()
            content.decode("utf-8")  # must not raise


# ---------------------------------------------------------------------------
# Directory creation only on successful write
# ---------------------------------------------------------------------------

class DirectoryCreationTests(unittest.TestCase):
    def test_directory_not_created_when_invalid_template(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = write_review_prompt(
                _command(root, template=_valid_template(sequence=0))
            )
            self.assertFalse(result.wrote)
            self.assertFalse(
                (root / "prompts" / "for_review_agent").exists(),
                "directory must not be created on failure",
            )

    def test_directory_not_created_for_overwrite_blocked(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            # first write creates directory
            self.assertTrue(write_review_prompt(_command(root)).wrote)
            target_dir = root / "prompts" / "for_review_agent"
            # remove the directory to confirm second attempt (overwrite=False)
            # only creates it when writing succeeds — but here the file already
            # exists so the write is blocked
            first_file = (
                target_dir
                / "026_review_frutlups_m006_s04_"
                "write_review_prompts_deterministically.md"
            )
            self.assertTrue(first_file.exists())
            result = write_review_prompt(_command(root))  # overwrite=False
            self.assertFalse(result.wrote)


# ---------------------------------------------------------------------------
# Overwrite behaviour
# ---------------------------------------------------------------------------

class OverwriteBehaviorTests(unittest.TestCase):
    def test_existing_target_without_overwrite_returns_error(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertTrue(write_review_prompt(_command(root)).wrote)

            second = write_review_prompt(_command(root))

            self.assertFalse(second.wrote)
            self.assertFalse(second.overwrote)
            self.assertTrue(
                any("already exists" in err for err in second.errors),
                f"expected 'already exists' error, got {second.errors}",
            )

    def test_existing_target_without_overwrite_preserves_content(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertTrue(write_review_prompt(_command(root)).wrote)

            written_path = (
                root
                / "prompts"
                / "for_review_agent"
                / "026_review_frutlups_m006_s04_"
                "write_review_prompts_deterministically.md"
            )
            original = written_path.read_text(encoding="utf-8")

            write_review_prompt(_command(root))

            self.assertEqual(written_path.read_text(encoding="utf-8"), original)

    def test_existing_target_with_overwrite_replaces_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertTrue(write_review_prompt(_command(root)).wrote)

            second = write_review_prompt(
                _command(root, template=_valid_template(), overwrite=True)
            )

            self.assertTrue(second.wrote)
            self.assertTrue(second.overwrote)

    def test_first_write_overwrote_is_false(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = write_review_prompt(_command(root))
            self.assertTrue(result.wrote)
            self.assertFalse(result.overwrote)


# ---------------------------------------------------------------------------
# Invalid template does not write
# ---------------------------------------------------------------------------

class InvalidTemplateTests(unittest.TestCase):
    def test_invalid_sequence_does_not_write(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = write_review_prompt(
                _command(root, template=_valid_template(sequence=0))
            )
            self.assertFalse(result.wrote)
            self.assertFalse(result.overwrote)
            self.assertIn("sequence must be a positive integer", result.errors)
            self.assertEqual(result.target_path, "")
            self.assertFalse(
                (root / "prompts" / "for_review_agent").exists(),
                "directory must not be created on failure",
            )

    def test_above_max_sequence_does_not_write(self) -> None:
        from frutlups.prompt_template import MAX_PROMPT_SEQUENCE

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = write_review_prompt(
                _command(root, template=_valid_template(sequence=1000))
            )
            self.assertFalse(result.wrote)
            self.assertIn(
                f"sequence must be at most {MAX_PROMPT_SEQUENCE}",
                result.errors,
            )

    def test_empty_slug_does_not_write(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = write_review_prompt(
                _command(root, template=_valid_template(slug=""))
            )
            self.assertFalse(result.wrote)
            self.assertIn("slug must be a non-empty string", result.errors)
            self.assertEqual(result.target_path, "")

    def test_missing_required_reading_does_not_write(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = write_review_prompt(
                _command(
                    root,
                    template=_valid_template(
                        required_reading=("README.md",)
                    ),
                )
            )
            self.assertFalse(result.wrote)
            self.assertIn(
                "required_reading must include CLAUDE.md", result.errors
            )

    def test_missing_severity_category_does_not_write(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = write_review_prompt(
                _command(
                    root,
                    template=_valid_template(
                        severity_guidance=("major: x", "minor: y", "nit: z")
                    ),
                )
            )
            self.assertFalse(result.wrote)
            self.assertIn(
                "severity_guidance must include a blocker entry", result.errors
            )


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------

class PathSafetyTests(unittest.TestCase):
    def test_slug_with_path_traversal_does_not_escape_directory(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = write_review_prompt(
                _command(
                    root,
                    template=_valid_template(slug="../../etc/passwd"),
                )
            )
            self.assertFalse(result.wrote)
            self.assertTrue(
                any(
                    "prompts/for_review_agent" in err for err in result.errors
                ),
                f"expected path-escape error, got {result.errors}",
            )
            self.assertFalse((root / "etc").exists())


# ---------------------------------------------------------------------------
# Renderer propagation: writer composes renderer
# ---------------------------------------------------------------------------

class RendererPropagationTests(unittest.TestCase):
    def test_written_content_matches_render_review_prompt_output(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = _valid_template()
            result = write_review_prompt(_command(root, template=template))

            self.assertTrue(result.wrote)
            written = Path(result.target_path).read_text(encoding="utf-8")
            expected = render_review_prompt(template).content
            self.assertEqual(written, expected)

    def test_renderer_errors_propagate_via_preview_errors(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = write_review_prompt(
                _command(root, template=_valid_template(sequence=0))
            )
            self.assertFalse(result.wrote)
            self.assertGreater(len(result.errors), 0)


# ---------------------------------------------------------------------------
# Coding-prompt writer does not touch the review prompt directory
# ---------------------------------------------------------------------------

class CodingWriterDoesNotTouchReviewDirTests(unittest.TestCase):
    def test_write_coding_prompt_does_not_create_review_directory(self) -> None:
        from frutlups.prompt_template import (
            CodingPromptTemplate,
            CodingPromptWriteCommand,
            write_coding_prompt,
        )

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            coding_template = CodingPromptTemplate(
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
                verification_commands=(
                    "python -m unittest discover -s tests",
                ),
                self_report_path=(
                    "05_governance/reviews/"
                    "m004_s03_explicit_coding_prompt_write_self_report.md"
                ),
            )
            write_coding_prompt(
                CodingPromptWriteCommand(
                    project_root=root,
                    template=coding_template,
                    content="# hello\n",
                )
            )
            self.assertFalse(
                (root / "prompts" / "for_review_agent").exists(),
                "coding-prompt writer must not create the review prompt directory",
            )


# ---------------------------------------------------------------------------
# Preview remains non-writing after M006-S04 is added
# ---------------------------------------------------------------------------

class PreviewIsStillNonWritingTests(unittest.TestCase):
    def test_preview_does_not_create_any_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = sorted(p.relative_to(root) for p in root.rglob("*"))
            _ = preview_review_prompt(_valid_template())
            after = sorted(p.relative_to(root) for p in root.rglob("*"))
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
