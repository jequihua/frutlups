"""Tests for the typed self-report locator surface."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from frutlups.prompt_template import CodingPromptTemplate
from frutlups.self_report import (
    SelfReportLocationCommand,
    SelfReportLocationResult,
    locate_expected_self_report,
)


def _valid_template(**overrides: object) -> CodingPromptTemplate:
    defaults: dict[str, object] = dict(
        sequence=18,
        milestone_id="M005",
        slice_id="M005-S02",
        slug="frutlups_m005_s02_self_report_locator",
        title="Self-Report Locator",
        role_instructions="You are the coding agent for frutlups.",
        required_reading=("CLAUDE.md", "README.md"),
        scope_paths=("08_pkg/src/frutlups/",),
        non_goals=("do not parse markdown",),
        definition_of_done=("locator exists",),
        verification_commands=("python -m unittest discover -s tests",),
        self_report_path=(
            "05_governance/reviews/"
            "m005_s02_self_report_locator_self_report.md"
        ),
    )
    defaults.update(overrides)
    return CodingPromptTemplate(**defaults)  # type: ignore[arg-type]


def _command(
    root: Path, *, template: CodingPromptTemplate | None = None
) -> SelfReportLocationCommand:
    return SelfReportLocationCommand(
        project_root=root,
        template=template if template is not None else _valid_template(),
    )


class CommandAndResultShapeTests(unittest.TestCase):
    def test_command_is_frozen(self) -> None:
        with TemporaryDirectory() as tmp:
            command = _command(Path(tmp))
            with self.assertRaises(Exception):
                command.template = command.template  # type: ignore[misc]

    def test_result_is_frozen(self) -> None:
        with TemporaryDirectory() as tmp:
            result = locate_expected_self_report(_command(Path(tmp)))
            with self.assertRaises(Exception):
                result.exists = True  # type: ignore[misc]

    def test_result_to_dict_shape_for_missing_target(self) -> None:
        with TemporaryDirectory() as tmp:
            result = locate_expected_self_report(_command(Path(tmp)))
        payload = result.to_dict()
        self.assertEqual(
            set(payload.keys()),
            {
                "expected_path",
                "repo_relative_path",
                "exists",
                "is_file",
                "is_dir",
                "errors",
            },
        )
        self.assertIsInstance(payload["expected_path"], str)
        self.assertIsInstance(payload["repo_relative_path"], str)
        self.assertIsInstance(payload["exists"], bool)
        self.assertIsInstance(payload["is_file"], bool)
        self.assertIsInstance(payload["is_dir"], bool)
        self.assertIsInstance(payload["errors"], list)

    def test_result_type_identity(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertIsInstance(
                locate_expected_self_report(_command(Path(tmp))),
                SelfReportLocationResult,
            )


class HappyPathTests(unittest.TestCase):
    def test_missing_file_returns_resolved_path_with_exists_false(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = locate_expected_self_report(_command(root))

        self.assertEqual(result.errors, ())
        self.assertFalse(result.exists)
        self.assertFalse(result.is_file)
        self.assertFalse(result.is_dir)
        self.assertEqual(
            result.repo_relative_path,
            "05_governance/reviews/m005_s02_self_report_locator_self_report.md",
        )
        self.assertTrue(result.expected_path.endswith(
            "m005_s02_self_report_locator_self_report.md"
        ))
        # The resolved path lives under the supplied project_root.
        self.assertTrue(
            Path(result.expected_path).is_relative_to(Path(tmp).resolve()),
        )

    def test_existing_file_reports_is_file_true(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = (
                root
                / "05_governance"
                / "reviews"
                / "m005_s02_self_report_locator_self_report.md"
            )
            target.parent.mkdir(parents=True)
            target.write_text("placeholder", encoding="utf-8")

            result = locate_expected_self_report(_command(root))

        self.assertEqual(result.errors, ())
        self.assertTrue(result.exists)
        self.assertTrue(result.is_file)
        self.assertFalse(result.is_dir)

    def test_existing_directory_reports_is_dir_true(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = (
                root
                / "05_governance"
                / "reviews"
                / "m005_s02_self_report_locator_self_report.md"
            )
            # Make the path a directory rather than a file (an unusual
            # but representable case the locator should report
            # honestly).
            target.mkdir(parents=True)

            result = locate_expected_self_report(_command(root))

        self.assertEqual(result.errors, ())
        self.assertTrue(result.exists)
        self.assertFalse(result.is_file)
        self.assertTrue(result.is_dir)


class InvalidTemplateTests(unittest.TestCase):
    def test_invalid_template_surfaces_validation_errors(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = _command(root, template=_valid_template(sequence=0))

            result = locate_expected_self_report(command)

        self.assertIn(
            "sequence must be a positive integer", result.errors
        )
        self.assertEqual(result.expected_path, "")
        self.assertFalse(result.exists)
        self.assertFalse(result.is_file)
        self.assertFalse(result.is_dir)

    def test_empty_self_report_path_surfaces_template_error(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = _command(
                root, template=_valid_template(self_report_path="")
            )

            result = locate_expected_self_report(command)

        self.assertIn(
            "self_report_path must be a non-empty string", result.errors
        )
        self.assertEqual(result.expected_path, "")

    def test_malformed_collection_does_not_raise(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = _command(
                root, template=_valid_template(required_reading=42)  # type: ignore[arg-type]
            )

            result = locate_expected_self_report(command)

        self.assertIn(
            "required_reading must be a tuple or list of non-empty strings",
            result.errors,
        )
        self.assertEqual(result.expected_path, "")
        self.assertFalse(result.exists)


class PathSafetyTests(unittest.TestCase):
    def test_absolute_self_report_path_is_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Use a platform-appropriate absolute path that does not
            # depend on the actual filesystem.
            absolute = str(Path(tmp) / "outside" / "absolute.md")
            command = _command(
                root, template=_valid_template(self_report_path=absolute)
            )

            result = locate_expected_self_report(command)

        self.assertIn(
            "self_report_path must be repo-relative", result.errors
        )
        self.assertEqual(result.expected_path, "")
        self.assertFalse(result.exists)

    def test_traversal_path_escaping_root_is_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "inner"
            root.mkdir()
            command = _command(
                root,
                template=_valid_template(
                    self_report_path="../outside/escape.md"
                ),
            )

            result = locate_expected_self_report(command)

        self.assertIn(
            "self_report_path must resolve inside project root",
            result.errors,
        )
        self.assertEqual(result.expected_path, "")
        self.assertFalse(result.exists)

    def test_traversal_through_governance_is_rejected(self) -> None:
        # Even a path that starts with the expected directory must be
        # rejected when its resolved form escapes the project root.
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "inner"
            root.mkdir()
            command = _command(
                root,
                template=_valid_template(
                    self_report_path=(
                        "05_governance/reviews/../../../outside/escape.md"
                    )
                ),
            )

            result = locate_expected_self_report(command)

        self.assertIn(
            "self_report_path must resolve inside project root",
            result.errors,
        )

    def test_safe_repo_relative_path_is_accepted(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = _command(
                root,
                template=_valid_template(
                    self_report_path="05_governance/reviews/x.md"
                ),
            )

            result = locate_expected_self_report(command)

        self.assertEqual(result.errors, ())
        self.assertEqual(
            result.repo_relative_path, "05_governance/reviews/x.md"
        )


class LocatorDoesNotMutateTests(unittest.TestCase):
    def test_locator_does_not_create_directories(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            locate_expected_self_report(_command(root))
            # Locator must not create the governance / reviews tree.
            self.assertFalse((root / "05_governance").exists())

    def test_locator_does_not_write_target_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = locate_expected_self_report(_command(root))
            # The resolved path does not exist after the locator runs.
            self.assertFalse(Path(result.expected_path).exists())
            self.assertFalse(result.exists)

    def test_locator_does_not_read_existing_file_content(self) -> None:
        # Smoke check: we cannot directly assert "did not open the
        # file", but we can confirm the locator does not return any
        # content-derived field. The result only carries path and
        # state flags.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = (
                root
                / "05_governance"
                / "reviews"
                / "m005_s02_self_report_locator_self_report.md"
            )
            target.parent.mkdir(parents=True)
            target.write_text("# real content", encoding="utf-8")

            result = locate_expected_self_report(_command(root))

        for forbidden_attr in ("content", "body", "fields", "schema"):
            with self.subTest(attr=forbidden_attr):
                self.assertFalse(hasattr(result, forbidden_attr))


class NeverRaisesTests(unittest.TestCase):
    def test_fully_malformed_template_does_not_raise(self) -> None:
        with TemporaryDirectory() as tmp:
            command = _command(
                Path(tmp),
                template=_valid_template(
                    sequence=0,
                    slug="",
                    title="",
                    role_instructions="",
                    required_reading=42,  # type: ignore[arg-type]
                    self_report_path="",
                ),
            )
            try:
                result = locate_expected_self_report(command)
            except Exception as exc:  # pragma: no cover - guard rail
                self.fail(f"locator raised {type(exc).__name__}: {exc}")

            self.assertGreater(len(result.errors), 0)
            self.assertEqual(result.expected_path, "")


if __name__ == "__main__":
    unittest.main()
