"""Tests for the typed self-report schema surface."""

import unittest

from frutlups.self_report import (
    SELF_REPORT_OPTIONAL_FIELDS,
    SELF_REPORT_REQUIRED_FIELDS,
    SELF_REPORT_SCHEMA_KIND,
    SELF_REPORT_SCHEMA_VERSION,
    SelfReportSchema,
    default_self_report_schema,
    validate_self_report_schema,
)


BASELINE_FIELDS = (
    "files changed",
    "behavior implemented",
    "tests added or updated",
    "verification commands and results",
    "live status summary",
    "known limits and intentional deferrals",
    "memory usage statement",
    "matching review prompt path created by the coder",
    "blockers or open questions",
)


class ModuleConstantTests(unittest.TestCase):
    def test_required_fields_match_documented_baseline_exactly(self) -> None:
        self.assertEqual(SELF_REPORT_REQUIRED_FIELDS, BASELINE_FIELDS)

    def test_optional_fields_is_a_tuple(self) -> None:
        self.assertIsInstance(SELF_REPORT_OPTIONAL_FIELDS, tuple)

    def test_kind_and_version_constants_are_stable_strings(self) -> None:
        self.assertEqual(SELF_REPORT_SCHEMA_KIND, "coder_self_report")
        self.assertEqual(SELF_REPORT_SCHEMA_VERSION, "self_report_schema_v1")


class DefaultSchemaTests(unittest.TestCase):
    def test_default_schema_contains_all_baseline_required_fields(self) -> None:
        schema = default_self_report_schema()
        for field in BASELINE_FIELDS:
            with self.subTest(field=field):
                self.assertIn(field, schema.required_fields)

    def test_default_schema_uses_module_constants(self) -> None:
        schema = default_self_report_schema()
        self.assertEqual(schema.required_fields, SELF_REPORT_REQUIRED_FIELDS)
        self.assertEqual(schema.optional_fields, SELF_REPORT_OPTIONAL_FIELDS)

    def test_default_schema_kind_and_version(self) -> None:
        schema = default_self_report_schema()
        self.assertEqual(schema.kind, SELF_REPORT_SCHEMA_KIND)
        self.assertEqual(schema.version, SELF_REPORT_SCHEMA_VERSION)

    def test_default_schema_validates(self) -> None:
        self.assertEqual(
            validate_self_report_schema(default_self_report_schema()), ()
        )


class SchemaSerializationTests(unittest.TestCase):
    def test_to_dict_uses_plain_python_types(self) -> None:
        schema = default_self_report_schema()
        payload = schema.to_dict()
        self.assertEqual(
            set(payload.keys()),
            {"kind", "version", "required_fields", "optional_fields"},
        )
        self.assertIsInstance(payload["kind"], str)
        self.assertIsInstance(payload["version"], str)
        self.assertIsInstance(payload["required_fields"], list)
        self.assertIsInstance(payload["optional_fields"], list)

    def test_to_dict_preserves_required_field_order(self) -> None:
        schema = default_self_report_schema()
        self.assertEqual(
            schema.to_dict()["required_fields"], list(BASELINE_FIELDS)
        )

    def test_schema_is_frozen(self) -> None:
        schema = default_self_report_schema()
        with self.assertRaises(Exception):
            schema.required_fields = ()  # type: ignore[misc]


class ValidationSuccessTests(unittest.TestCase):
    def test_custom_schema_with_all_baseline_required_validates(self) -> None:
        schema = SelfReportSchema(
            required_fields=SELF_REPORT_REQUIRED_FIELDS + ("custom extra field",),
            optional_fields=("note",),
        )
        self.assertEqual(validate_self_report_schema(schema), ())

    def test_empty_optional_fields_validates(self) -> None:
        schema = SelfReportSchema(
            required_fields=SELF_REPORT_REQUIRED_FIELDS,
            optional_fields=(),
        )
        self.assertEqual(validate_self_report_schema(schema), ())

    def test_list_collections_are_accepted(self) -> None:
        schema = SelfReportSchema(
            required_fields=list(SELF_REPORT_REQUIRED_FIELDS),  # type: ignore[arg-type]
            optional_fields=["note"],  # type: ignore[arg-type]
        )
        self.assertEqual(validate_self_report_schema(schema), ())


class MalformedCollectionTests(unittest.TestCase):
    """Regression: the validator must never raise for malformed inputs."""

    def test_non_iterable_required_fields_reported(self) -> None:
        schema = SelfReportSchema(required_fields=42)  # type: ignore[arg-type]
        errors = validate_self_report_schema(schema)
        self.assertIn(
            "required_fields must be a tuple or list of non-empty strings",
            errors,
        )

    def test_none_optional_fields_reported(self) -> None:
        schema = SelfReportSchema(
            required_fields=SELF_REPORT_REQUIRED_FIELDS,
            optional_fields=None,  # type: ignore[arg-type]
        )
        errors = validate_self_report_schema(schema)
        self.assertIn(
            "optional_fields must be a tuple or list of non-empty strings",
            errors,
        )

    def test_blank_required_entry_reported_with_index(self) -> None:
        schema = SelfReportSchema(
            required_fields=("files changed", "  ", "behavior implemented"),
        )
        errors = validate_self_report_schema(schema)
        self.assertIn(
            "required_fields[1] must be a non-empty string", errors
        )

    def test_non_string_entry_reported_with_index(self) -> None:
        schema = SelfReportSchema(
            required_fields=(42, "files changed"),  # type: ignore[arg-type]
        )
        errors = validate_self_report_schema(schema)
        self.assertIn(
            "required_fields[0] must be a non-empty string", errors
        )

    def test_blank_optional_entry_reported(self) -> None:
        schema = SelfReportSchema(
            required_fields=SELF_REPORT_REQUIRED_FIELDS,
            optional_fields=("note", ""),
        )
        errors = validate_self_report_schema(schema)
        self.assertIn(
            "optional_fields[1] must be a non-empty string", errors
        )

    def test_validator_never_raises_for_fully_malformed_schema(self) -> None:
        schema = SelfReportSchema(
            required_fields=42,  # type: ignore[arg-type]
            optional_fields=None,  # type: ignore[arg-type]
            kind="",
            version="",
        )
        try:
            errors = validate_self_report_schema(schema)
        except Exception as exc:  # pragma: no cover - guard rail
            self.fail(f"validator raised {type(exc).__name__}: {exc}")
        # Should at minimum report the collection problems and the
        # empty kind/version strings.
        for expected in (
            "required_fields must be a tuple or list of non-empty strings",
            "optional_fields must be a tuple or list of non-empty strings",
            "kind must be a non-empty string",
            "version must be a non-empty string",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, errors)


class DuplicateFieldTests(unittest.TestCase):
    def test_duplicate_in_required_fields_reported(self) -> None:
        schema = SelfReportSchema(
            required_fields=SELF_REPORT_REQUIRED_FIELDS + ("files changed",),
        )
        errors = validate_self_report_schema(schema)
        self.assertIn(
            "required_fields contains duplicate field: files changed",
            errors,
        )

    def test_duplicate_in_optional_fields_reported(self) -> None:
        schema = SelfReportSchema(
            required_fields=SELF_REPORT_REQUIRED_FIELDS,
            optional_fields=("note", "note"),
        )
        errors = validate_self_report_schema(schema)
        self.assertIn(
            "optional_fields contains duplicate field: note", errors
        )


class ConfigurableSchemaContractTests(unittest.TestCase):
    """M019-S01: the hardcoded baseline is the *default* schema, not a universal
    floor. A custom configured schema is valid without including every baseline
    field; only structural rules (and a non-empty required_fields) are enforced."""

    def test_custom_schema_without_baseline_fields_is_valid(self) -> None:
        # A v2-style configured schema that shares no labels with the baseline.
        schema = SelfReportSchema(required_fields=("Intent", "Verification Run"))
        self.assertEqual(validate_self_report_schema(schema), ())

    def test_missing_single_baseline_field_no_longer_errors(self) -> None:
        without_files_changed = tuple(
            f for f in SELF_REPORT_REQUIRED_FIELDS if f != "files changed"
        )
        schema = SelfReportSchema(required_fields=without_files_changed)
        errors = validate_self_report_schema(schema)
        self.assertNotIn("required_fields must include files changed", errors)
        # No "must include" error remains for any baseline field.
        self.assertFalse([e for e in errors if e.startswith("required_fields must include")])

    def test_empty_required_fields_is_an_explicit_error(self) -> None:
        schema = SelfReportSchema(required_fields=())
        errors = validate_self_report_schema(schema)
        self.assertIn("required_fields must not be empty", errors)

    def test_non_iterable_required_fields_reports_collection_error_without_raising(self) -> None:
        schema = SelfReportSchema(required_fields=42)  # type: ignore[arg-type]
        errors = validate_self_report_schema(schema)
        self.assertIn(
            "required_fields must be a tuple or list of non-empty strings",
            errors,
        )
        # No "must include" baseline-superset errors are produced anymore.
        self.assertFalse([e for e in errors if e.startswith("required_fields must include")])


class DocstringNitTests(unittest.TestCase):
    def test_prompt_template_docstring_no_longer_claims_renderer_is_caller(self) -> None:
        from frutlups import prompt_template

        doc = prompt_template.__doc__ or ""
        self.assertNotIn("rendering itself remains the caller", doc.lower())
        self.assertIn("renderer", doc.lower())


if __name__ == "__main__":
    unittest.main()
