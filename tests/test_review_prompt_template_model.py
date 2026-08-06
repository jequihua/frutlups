"""Tests for the typed review-prompt template data model."""

import unittest

from frutlups.prompt_template import MAX_PROMPT_SEQUENCE
from frutlups.review_prompt_template import (
    REVIEW_REQUIRED_READING_BASELINE,
    ReviewPromptTemplate,
    validate_review_prompt_template,
)


def _valid_template(**overrides: object) -> ReviewPromptTemplate:
    defaults: dict[str, object] = dict(
        sequence=22,
        milestone_id="M006",
        slice_id="M006-S01",
        slug="frutlups_m006_s01_review_prompt_template_model",
        title="Review Prompt Template Model",
        role_instructions="You are the review agent for frutlups.",
        required_reading=("CLAUDE.md", "README.md", "08_pkg/CONTEXT.md"),
        coding_prompt_path=(
            "prompts/for_coding_agent/"
            "022_frutlups_m006_s01_review_prompt_template_model.md"
        ),
        self_report_path=(
            "05_governance/reviews/"
            "m006_s01_review_prompt_template_model_self_report.md"
        ),
        review_output_path=(
            "05_governance/reviews/"
            "m006_s01_review_prompt_template_model_review_report.md"
        ),
        expected_changed_files=(
            "08_pkg/src/frutlups/review_prompt_template.py",
            "08_pkg/tests/test_review_prompt_template_model.py",
        ),
        verification_commands=(
            "python -m unittest discover -s tests",
            "python -m frutlups status ..",
        ),
        severity_guidance=(
            "blocker: correctness or scope failure",
            "major: missing test coverage",
            "minor: small completeness gap",
            "nit: style or wording only",
        ),
        verdict_choices=("pass", "needs_work", "blocked", "override"),
        prior_review_paths=(
            "05_governance/reviews/"
            "m005_s04_corrective_active_roadmap_alignment_review_report.md",
        ),
        non_goals=("do not render review prompt markdown",),
        notes=("model only",),
    )
    defaults.update(overrides)
    return ReviewPromptTemplate(**defaults)  # type: ignore[arg-type]


class ConstructionAndSerializationTests(unittest.TestCase):
    def test_construction_with_all_fields(self) -> None:
        template = _valid_template()

        self.assertEqual(template.sequence, 22)
        self.assertEqual(template.milestone_id, "M006")
        self.assertEqual(template.slice_id, "M006-S01")
        self.assertEqual(
            template.required_reading,
            ("CLAUDE.md", "README.md", "08_pkg/CONTEXT.md"),
        )
        self.assertEqual(
            template.verdict_choices,
            ("pass", "needs_work", "blocked", "override"),
        )

    def test_to_dict_shape_uses_plain_python_types(self) -> None:
        payload = _valid_template().to_dict()

        for tuple_field in (
            "required_reading",
            "expected_changed_files",
            "verification_commands",
            "severity_guidance",
            "verdict_choices",
            "prior_review_paths",
            "non_goals",
            "notes",
        ):
            with self.subTest(field=tuple_field):
                self.assertIsInstance(payload[tuple_field], list)
        for str_field in (
            "milestone_id",
            "slice_id",
            "slug",
            "title",
            "role_instructions",
            "coding_prompt_path",
            "self_report_path",
            "review_output_path",
        ):
            with self.subTest(field=str_field):
                self.assertIsInstance(payload[str_field], str)
        self.assertIsInstance(payload["sequence"], int)

    def test_to_dict_key_set_matches_documented_fields(self) -> None:
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
                "coding_prompt_path",
                "self_report_path",
                "review_output_path",
                "expected_changed_files",
                "verification_commands",
                "severity_guidance",
                "verdict_choices",
                "prior_review_paths",
                "non_goals",
                "notes",
            },
        )

    def test_no_path_objects_in_serialized_payload(self) -> None:
        from pathlib import Path

        payload = _valid_template().to_dict()
        for key, value in payload.items():
            with self.subTest(key=key):
                self.assertNotIsInstance(value, Path)

    def test_optional_collections_default_to_empty_tuples(self) -> None:
        template = ReviewPromptTemplate(
            sequence=1,
            milestone_id="M006",
            slice_id="M006-S01",
            slug="x",
            title="X",
            role_instructions="r",
            required_reading=("CLAUDE.md", "README.md"),
            coding_prompt_path="prompts/for_coding_agent/001.md",
            self_report_path="05_governance/reviews/sr.md",
            review_output_path="05_governance/reviews/rr.md",
            expected_changed_files=("a.py",),
            verification_commands=("c",),
            severity_guidance=("s",),
            verdict_choices=("pass",),
        )
        self.assertEqual(template.prior_review_paths, ())
        self.assertEqual(template.non_goals, ())
        self.assertEqual(template.notes, ())

    def test_dataclass_is_frozen(self) -> None:
        template = _valid_template()
        with self.assertRaises(Exception):
            template.sequence = 99  # type: ignore[misc]


class OrderPreservationTests(unittest.TestCase):
    def test_required_reading_order_preserved(self) -> None:
        ordered = (
            "CLAUDE.md",
            "README.md",
            "03_experiments/active_roadmap_frutlups.md",
            "08_pkg/CONTEXT.md",
        )
        payload = _valid_template(required_reading=ordered).to_dict()
        self.assertEqual(payload["required_reading"], list(ordered))

    def test_expected_changed_files_order_preserved(self) -> None:
        ordered = ("z.py", "a.py", "m.py")
        payload = _valid_template(expected_changed_files=ordered).to_dict()
        self.assertEqual(payload["expected_changed_files"], list(ordered))

    def test_verification_commands_order_preserved(self) -> None:
        ordered = (
            "python -m unittest discover -s tests",
            "python -m frutlups status ..",
            "python -m frutlups status .. --json",
            "python -m frutlups --help",
            "python -m compileall -q src",
        )
        payload = _valid_template(verification_commands=ordered).to_dict()
        self.assertEqual(payload["verification_commands"], list(ordered))

    def test_severity_guidance_order_preserved(self) -> None:
        ordered = ("blocker: x", "major: y", "minor: z", "nit: w")
        payload = _valid_template(severity_guidance=ordered).to_dict()
        self.assertEqual(payload["severity_guidance"], list(ordered))

    def test_verdict_choices_order_preserved(self) -> None:
        ordered = ("pass", "needs_work", "blocked", "override")
        payload = _valid_template(verdict_choices=ordered).to_dict()
        self.assertEqual(payload["verdict_choices"], list(ordered))


class OptionalCollectionsTests(unittest.TestCase):
    def test_prior_review_paths_empty_validates(self) -> None:
        self.assertEqual(
            validate_review_prompt_template(
                _valid_template(prior_review_paths=())
            ),
            (),
        )

    def test_non_goals_empty_validates(self) -> None:
        self.assertEqual(
            validate_review_prompt_template(_valid_template(non_goals=())),
            (),
        )

    def test_notes_empty_validates(self) -> None:
        self.assertEqual(
            validate_review_prompt_template(_valid_template(notes=())),
            (),
        )

    def test_blank_entry_in_optional_collection_reported(self) -> None:
        for field in ("prior_review_paths", "non_goals", "notes"):
            with self.subTest(field=field):
                errors = validate_review_prompt_template(
                    _valid_template(**{field: ("ok", "   ")})
                )
                self.assertIn(
                    f"{field}[1] must be a non-empty string", errors
                )


class RequiredReadingBaselineTests(unittest.TestCase):
    def test_baseline_constant(self) -> None:
        self.assertEqual(
            REVIEW_REQUIRED_READING_BASELINE, ("CLAUDE.md", "README.md")
        )

    def test_missing_claude_md_reported(self) -> None:
        errors = validate_review_prompt_template(
            _valid_template(required_reading=("README.md",))
        )
        self.assertIn("required_reading must include CLAUDE.md", errors)

    def test_missing_readme_md_reported(self) -> None:
        errors = validate_review_prompt_template(
            _valid_template(required_reading=("CLAUDE.md",))
        )
        self.assertIn("required_reading must include README.md", errors)

    def test_both_missing_reports_both(self) -> None:
        errors = validate_review_prompt_template(
            _valid_template(required_reading=("08_pkg/CONTEXT.md",))
        )
        self.assertIn("required_reading must include CLAUDE.md", errors)
        self.assertIn("required_reading must include README.md", errors)


class ValidationSuccessTests(unittest.TestCase):
    def test_valid_template_validates(self) -> None:
        self.assertEqual(
            validate_review_prompt_template(_valid_template()), ()
        )

    def test_list_collections_accepted(self) -> None:
        template = _valid_template(
            required_reading=["CLAUDE.md", "README.md"],  # type: ignore[arg-type]
            verification_commands=["python -m unittest discover -s tests"],  # type: ignore[arg-type]
        )
        self.assertEqual(validate_review_prompt_template(template), ())


class SequenceValidationTests(unittest.TestCase):
    def test_positive_sequences_accepted(self) -> None:
        for sequence in (1, 22, 100, MAX_PROMPT_SEQUENCE):
            with self.subTest(sequence=sequence):
                self.assertEqual(
                    validate_review_prompt_template(
                        _valid_template(sequence=sequence)
                    ),
                    (),
                )

    def test_zero_and_negative_rejected(self) -> None:
        for sequence in (0, -1, -999):
            with self.subTest(sequence=sequence):
                errors = validate_review_prompt_template(
                    _valid_template(sequence=sequence)
                )
                self.assertIn(
                    "sequence must be a positive integer", errors
                )

    def test_above_max_sequence_rejected(self) -> None:
        for sequence in (MAX_PROMPT_SEQUENCE + 1, 1000, 9999):
            with self.subTest(sequence=sequence):
                errors = validate_review_prompt_template(
                    _valid_template(sequence=sequence)
                )
                self.assertIn(
                    f"sequence must be at most {MAX_PROMPT_SEQUENCE}",
                    errors,
                )

    def test_bool_sequence_rejected(self) -> None:
        # ``True``/`False` are technically int subclasses but should
        # not be accepted as sequence numbers.
        errors = validate_review_prompt_template(
            _valid_template(sequence=True)  # type: ignore[arg-type]
        )
        self.assertIn("sequence must be a positive integer", errors)

    def test_non_int_sequence_rejected(self) -> None:
        for sequence in ("22", None, 1.5):
            with self.subTest(sequence=sequence):
                errors = validate_review_prompt_template(
                    _valid_template(sequence=sequence)  # type: ignore[arg-type]
                )
                self.assertIn(
                    "sequence must be a positive integer", errors
                )

    def test_upper_bound_does_not_cofire_positive_integer_error(self) -> None:
        errors = validate_review_prompt_template(
            _valid_template(sequence=MAX_PROMPT_SEQUENCE + 1)
        )
        self.assertIn(
            f"sequence must be at most {MAX_PROMPT_SEQUENCE}", errors
        )
        self.assertNotIn("sequence must be a positive integer", errors)


class RequiredStringFieldTests(unittest.TestCase):
    def test_empty_required_strings_reported(self) -> None:
        for field in (
            "milestone_id",
            "slice_id",
            "slug",
            "title",
            "role_instructions",
            "coding_prompt_path",
            "self_report_path",
            "review_output_path",
        ):
            with self.subTest(field=field):
                errors = validate_review_prompt_template(
                    _valid_template(**{field: "   "})
                )
                self.assertIn(
                    f"{field} must be a non-empty string", errors
                )


class RequiredCollectionFieldTests(unittest.TestCase):
    def test_empty_required_collections_reported(self) -> None:
        for field in (
            "required_reading",
            "expected_changed_files",
            "verification_commands",
            "severity_guidance",
            "verdict_choices",
        ):
            with self.subTest(field=field):
                errors = validate_review_prompt_template(
                    _valid_template(**{field: ()})
                )
                self.assertIn(f"{field} must be non-empty", errors)

    def test_blank_entry_in_required_collection_reported(self) -> None:
        for field in (
            "required_reading",
            "expected_changed_files",
            "verification_commands",
            "severity_guidance",
            "verdict_choices",
        ):
            with self.subTest(field=field):
                # Position 1 will be the blank one; first entry is
                # whatever default the field already has.
                bad = ("ok-1", "   ")
                errors = validate_review_prompt_template(
                    _valid_template(**{field: bad})
                )
                self.assertIn(
                    f"{field}[1] must be a non-empty string", errors
                )


class MalformedCollectionsDoNotRaiseTests(unittest.TestCase):
    """Inherits the M004-S01 never-raises posture."""

    def test_non_iterable_required_reading_reported(self) -> None:
        errors = validate_review_prompt_template(
            _valid_template(required_reading=42)  # type: ignore[arg-type]
        )
        self.assertIn(
            "required_reading must be a tuple or list of non-empty strings",
            errors,
        )

    def test_non_iterable_expected_changed_files_reported(self) -> None:
        errors = validate_review_prompt_template(
            _valid_template(expected_changed_files=42)  # type: ignore[arg-type]
        )
        self.assertIn(
            "expected_changed_files must be a tuple or list of non-empty strings",
            errors,
        )

    def test_non_iterable_verification_commands_reported(self) -> None:
        errors = validate_review_prompt_template(
            _valid_template(verification_commands=42)  # type: ignore[arg-type]
        )
        self.assertIn(
            "verification_commands must be a tuple or list of non-empty strings",
            errors,
        )

    def test_non_iterable_severity_guidance_reported(self) -> None:
        errors = validate_review_prompt_template(
            _valid_template(severity_guidance=42)  # type: ignore[arg-type]
        )
        self.assertIn(
            "severity_guidance must be a tuple or list of non-empty strings",
            errors,
        )

    def test_non_iterable_verdict_choices_reported(self) -> None:
        errors = validate_review_prompt_template(
            _valid_template(verdict_choices=42)  # type: ignore[arg-type]
        )
        self.assertIn(
            "verdict_choices must be a tuple or list of non-empty strings",
            errors,
        )

    def test_none_prior_review_paths_reported(self) -> None:
        errors = validate_review_prompt_template(
            _valid_template(prior_review_paths=None)  # type: ignore[arg-type]
        )
        self.assertIn(
            "prior_review_paths must be a tuple or list of non-empty strings",
            errors,
        )

    def test_none_non_goals_reported(self) -> None:
        errors = validate_review_prompt_template(
            _valid_template(non_goals=None)  # type: ignore[arg-type]
        )
        self.assertIn(
            "non_goals must be a tuple or list of non-empty strings",
            errors,
        )

    def test_none_notes_reported(self) -> None:
        errors = validate_review_prompt_template(
            _valid_template(notes=None)  # type: ignore[arg-type]
        )
        self.assertIn(
            "notes must be a tuple or list of non-empty strings",
            errors,
        )

    def test_fully_malformed_template_does_not_raise(self) -> None:
        template = ReviewPromptTemplate(
            sequence=0,
            milestone_id="",
            slice_id="",
            slug="",
            title="",
            role_instructions="",
            required_reading=42,  # type: ignore[arg-type]
            coding_prompt_path="",
            self_report_path="",
            review_output_path="",
            expected_changed_files=42,  # type: ignore[arg-type]
            verification_commands=42,  # type: ignore[arg-type]
            severity_guidance=42,  # type: ignore[arg-type]
            verdict_choices=42,  # type: ignore[arg-type]
            prior_review_paths=None,  # type: ignore[arg-type]
            non_goals=None,  # type: ignore[arg-type]
            notes=None,  # type: ignore[arg-type]
        )
        try:
            errors = validate_review_prompt_template(template)
        except Exception as exc:  # pragma: no cover - guard rail
            self.fail(f"validator raised {type(exc).__name__}: {exc}")
        self.assertGreater(len(errors), 0)


if __name__ == "__main__":
    unittest.main()
