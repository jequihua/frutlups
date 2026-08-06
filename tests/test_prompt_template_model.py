"""Tests for the coding prompt template data model and validator."""

import unittest

from frutlups.prompt_template import (
    CodingPromptTemplate,
    validate_coding_prompt_template,
)


def _valid_template(**overrides: object) -> CodingPromptTemplate:
    defaults: dict[str, object] = dict(
        sequence=10,
        milestone_id="M004",
        slice_id="M004-S01",
        slug="prompt_template_model",
        title="Prompt Template Model",
        role_instructions="You are the coding agent for frutlups.",
        required_reading=(
            "CLAUDE.md",
            "README.md",
            "08_pkg/CONTEXT.md",
        ),
        scope_paths=("08_pkg/src/frutlups/", "08_pkg/tests/"),
        non_goals=("do not render markdown",),
        definition_of_done=("typed model exists", "tests added"),
        verification_commands=(
            "python -m unittest discover -s tests",
            "python -m frutlups status ..",
        ),
        self_report_path="05_governance/reviews/m004_s01_prompt_template_model_self_report.md",
        notes=("matches existing coding style",),
    )
    defaults.update(overrides)
    return CodingPromptTemplate(**defaults)  # type: ignore[arg-type]


class ConstructionAndSerializationTests(unittest.TestCase):
    def test_construction_with_all_fields(self) -> None:
        template = _valid_template()

        self.assertEqual(template.sequence, 10)
        self.assertEqual(template.milestone_id, "M004")
        self.assertEqual(template.slice_id, "M004-S01")
        self.assertEqual(template.slug, "prompt_template_model")
        self.assertEqual(template.title, "Prompt Template Model")
        self.assertEqual(
            template.required_reading,
            ("CLAUDE.md", "README.md", "08_pkg/CONTEXT.md"),
        )
        self.assertEqual(
            template.verification_commands,
            (
                "python -m unittest discover -s tests",
                "python -m frutlups status ..",
            ),
        )
        self.assertEqual(template.notes, ("matches existing coding style",))

    def test_to_dict_uses_plain_python_types(self) -> None:
        payload = _valid_template().to_dict()

        for key in (
            "required_reading",
            "scope_paths",
            "non_goals",
            "definition_of_done",
            "verification_commands",
            "notes",
        ):
            with self.subTest(key=key):
                self.assertIsInstance(payload[key], list)
        self.assertIsInstance(payload["sequence"], int)
        for key in (
            "milestone_id",
            "slice_id",
            "slug",
            "title",
            "role_instructions",
            "self_report_path",
        ):
            with self.subTest(key=key):
                self.assertIsInstance(payload[key], str)

    def test_to_dict_shape_matches_documented_keys(self) -> None:
        payload = _valid_template().to_dict()

        self.assertEqual(
            set(payload.keys()),
            {
                "sequence",
                "milestone_id",
                "slice_id",
                "slug",
                "title",
                "role_instructions",
                "required_reading",
                "scope_paths",
                "non_goals",
                "definition_of_done",
                "verification_commands",
                "self_report_path",
                "notes",
                "memory_update",
            },
        )

    def test_required_reading_order_is_preserved_in_to_dict(self) -> None:
        ordered = (
            "CLAUDE.md",
            "README.md",
            "03_experiments/active_roadmap_frutlups.md",
            "08_pkg/CONTEXT.md",
        )
        payload = _valid_template(required_reading=ordered).to_dict()
        self.assertEqual(payload["required_reading"], list(ordered))

    def test_verification_command_order_is_preserved_in_to_dict(self) -> None:
        ordered = (
            "python -m unittest discover -s tests",
            "python -m frutlups status ..",
            "python -m frutlups status .. --json",
            "python -m frutlups --help",
            "python -m compileall -q src",
        )
        payload = _valid_template(verification_commands=ordered).to_dict()
        self.assertEqual(payload["verification_commands"], list(ordered))

    def test_optional_notes_default_to_empty_tuple(self) -> None:
        template = CodingPromptTemplate(
            sequence=1,
            milestone_id="M004",
            slice_id="M004-S01",
            slug="x",
            title="X",
            role_instructions="r",
            required_reading=("CLAUDE.md",),
            scope_paths=("08_pkg/",),
            non_goals=(),
            definition_of_done=("d",),
            verification_commands=("c",),
            self_report_path="p",
        )
        self.assertEqual(template.notes, ())
        self.assertEqual(template.to_dict()["notes"], [])

    def test_self_report_path_is_represented_explicitly(self) -> None:
        template = _valid_template(
            self_report_path=(
                "05_governance/reviews/"
                "m004_s01_prompt_template_model_self_report.md"
            )
        )
        self.assertEqual(
            template.to_dict()["self_report_path"],
            "05_governance/reviews/"
            "m004_s01_prompt_template_model_self_report.md",
        )

    def test_dataclass_is_frozen(self) -> None:
        template = _valid_template()
        with self.assertRaises(Exception):
            template.sequence = 99  # type: ignore[misc]


class ValidationTests(unittest.TestCase):
    def test_fully_populated_template_validates(self) -> None:
        self.assertEqual(validate_coding_prompt_template(_valid_template()), ())

    def test_sequence_zero_or_negative_reported(self) -> None:
        for bad in (0, -1, -100):
            with self.subTest(sequence=bad):
                errors = validate_coding_prompt_template(_valid_template(sequence=bad))
                self.assertIn("sequence must be a positive integer", errors)

    def test_positive_sequence_passes(self) -> None:
        errors = validate_coding_prompt_template(_valid_template(sequence=1))
        self.assertEqual(errors, ())
        errors = validate_coding_prompt_template(_valid_template(sequence=999))
        self.assertEqual(errors, ())

    def test_empty_required_reading_reported(self) -> None:
        errors = validate_coding_prompt_template(_valid_template(required_reading=()))
        self.assertIn("required_reading must be non-empty", errors)

    def test_empty_scope_paths_reported(self) -> None:
        errors = validate_coding_prompt_template(_valid_template(scope_paths=()))
        self.assertIn("scope_paths must be non-empty", errors)

    def test_empty_definition_of_done_reported(self) -> None:
        errors = validate_coding_prompt_template(
            _valid_template(definition_of_done=())
        )
        self.assertIn("definition_of_done must be non-empty", errors)

    def test_empty_verification_commands_reported(self) -> None:
        errors = validate_coding_prompt_template(
            _valid_template(verification_commands=())
        )
        self.assertIn("verification_commands must be non-empty", errors)

    def test_empty_string_field_reported(self) -> None:
        for field in (
            "milestone_id",
            "slice_id",
            "slug",
            "title",
            "role_instructions",
            "self_report_path",
        ):
            with self.subTest(field=field):
                errors = validate_coding_prompt_template(
                    _valid_template(**{field: "   "})
                )
                self.assertIn(f"{field} must be a non-empty string", errors)

    def test_blank_required_reading_entry_reported(self) -> None:
        errors = validate_coding_prompt_template(
            _valid_template(required_reading=("CLAUDE.md", "   ", "README.md"))
        )
        self.assertIn(
            "required_reading[1] must be a non-empty string", errors
        )

    def test_blank_note_entry_reported(self) -> None:
        errors = validate_coding_prompt_template(
            _valid_template(notes=("ok", "   "))
        )
        self.assertIn("notes[1] must be a non-empty string", errors)

    def test_empty_non_goals_allowed(self) -> None:
        self.assertEqual(
            validate_coding_prompt_template(_valid_template(non_goals=())), ()
        )

    def test_empty_notes_allowed(self) -> None:
        self.assertEqual(
            validate_coding_prompt_template(_valid_template(notes=())), ()
        )

    def test_validator_never_raises(self) -> None:
        # A clearly broken template should still return errors rather
        # than raise.
        template = CodingPromptTemplate(
            sequence=0,
            milestone_id="",
            slice_id="",
            slug="",
            title="",
            role_instructions="",
            required_reading=(),
            scope_paths=(),
            non_goals=(),
            definition_of_done=(),
            verification_commands=(),
            self_report_path="",
            notes=(),
        )
        errors = validate_coding_prompt_template(template)
        self.assertGreater(len(errors), 0)


class ValidatorNeverRaisesOnMalformedCollections(unittest.TestCase):
    """Regression: the validator must never raise for malformed but
    constructible dataclass values (see M004-S01 review blocker)."""

    def test_non_iterable_required_reading_reported(self) -> None:
        template = _valid_template(required_reading=42)  # type: ignore[arg-type]
        errors = validate_coding_prompt_template(template)
        self.assertIn(
            "required_reading must be a tuple or list of non-empty strings",
            errors,
        )

    def test_non_iterable_scope_paths_reported(self) -> None:
        template = _valid_template(scope_paths=42)  # type: ignore[arg-type]
        errors = validate_coding_prompt_template(template)
        self.assertIn(
            "scope_paths must be a tuple or list of non-empty strings", errors
        )

    def test_non_iterable_definition_of_done_reported(self) -> None:
        template = _valid_template(definition_of_done=42)  # type: ignore[arg-type]
        errors = validate_coding_prompt_template(template)
        self.assertIn(
            "definition_of_done must be a tuple or list of non-empty strings",
            errors,
        )

    def test_non_iterable_verification_commands_reported(self) -> None:
        template = _valid_template(verification_commands=42)  # type: ignore[arg-type]
        errors = validate_coding_prompt_template(template)
        self.assertIn(
            "verification_commands must be a tuple or list of non-empty strings",
            errors,
        )

    def test_none_non_goals_reported(self) -> None:
        template = _valid_template(non_goals=None)  # type: ignore[arg-type]
        errors = validate_coding_prompt_template(template)
        self.assertIn(
            "non_goals must be a tuple or list of non-empty strings", errors
        )

    def test_none_notes_reported(self) -> None:
        template = _valid_template(notes=None)  # type: ignore[arg-type]
        errors = validate_coding_prompt_template(template)
        self.assertIn(
            "notes must be a tuple or list of non-empty strings", errors
        )

    def test_fully_malformed_template_does_not_raise(self) -> None:
        # Stress-test the documented review-probe scenarios: every
        # collection field is non-iterable or None at the same time.
        template = _valid_template(
            required_reading=42,  # type: ignore[arg-type]
            scope_paths=42,  # type: ignore[arg-type]
            definition_of_done=42,  # type: ignore[arg-type]
            verification_commands=42,  # type: ignore[arg-type]
            non_goals=None,  # type: ignore[arg-type]
            notes=None,  # type: ignore[arg-type]
        )
        try:
            errors = validate_coding_prompt_template(template)
        except Exception as exc:  # pragma: no cover - guard rails
            self.fail(f"validator raised {type(exc).__name__}: {exc}")
        for field in (
            "required_reading",
            "scope_paths",
            "definition_of_done",
            "verification_commands",
            "non_goals",
            "notes",
        ):
            with self.subTest(field=field):
                self.assertIn(
                    f"{field} must be a tuple or list of non-empty strings",
                    errors,
                )

    def test_list_collections_are_accepted(self) -> None:
        # The validator should accept both tuples and lists; the
        # corrective fix must not regress list inputs.
        template = _valid_template(
            required_reading=["CLAUDE.md", "README.md"],  # type: ignore[arg-type]
            verification_commands=["python -m unittest discover -s tests"],  # type: ignore[arg-type]
        )
        self.assertEqual(validate_coding_prompt_template(template), ())


if __name__ == "__main__":
    unittest.main()
