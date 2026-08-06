"""Tests for the M007-S01 verdict enum and review report schema."""

import unittest

from frutlups.review_report import (
    REVIEW_REPORT_OPTIONAL_FIELDS,
    REVIEW_REPORT_REQUIRED_FIELDS,
    REVIEW_REPORT_SCHEMA_KIND,
    REVIEW_REPORT_SCHEMA_VERSION,
    ReviewReportSchema,
    ReviewVerdict,
    default_review_report_schema,
    validate_review_report_schema,
)


BASELINE_REQUIRED = (
    "verdict",
    "findings",
    "review notes",
    "verification",
    "residual risk",
    "memory",
)


def _valid_schema(**overrides: object) -> ReviewReportSchema:
    defaults: dict[str, object] = dict(
        required_fields=REVIEW_REPORT_REQUIRED_FIELDS,
        optional_fields=REVIEW_REPORT_OPTIONAL_FIELDS,
    )
    defaults.update(overrides)
    return ReviewReportSchema(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Verdict enum
# ---------------------------------------------------------------------------

class ReviewVerdictTests(unittest.TestCase):
    def test_verdict_values(self) -> None:
        self.assertEqual(ReviewVerdict.PASS.value, "pass")
        self.assertEqual(ReviewVerdict.NEEDS_WORK.value, "needs_work")
        self.assertEqual(ReviewVerdict.BLOCKED.value, "blocked")
        self.assertEqual(ReviewVerdict.OVERRIDE.value, "override")

    def test_exactly_four_verdicts(self) -> None:
        self.assertEqual(len(list(ReviewVerdict)), 4)

    def test_canonical_ordering(self) -> None:
        values = [v.value for v in ReviewVerdict]
        self.assertEqual(
            values, ["pass", "needs_work", "blocked", "override"]
        )

    def test_str_enum_compares_equal_to_string(self) -> None:
        self.assertEqual(ReviewVerdict.PASS, "pass")
        self.assertEqual(ReviewVerdict.NEEDS_WORK, "needs_work")
        self.assertEqual(ReviewVerdict.BLOCKED, "blocked")
        self.assertEqual(ReviewVerdict.OVERRIDE, "override")

    def test_str_conversion(self) -> None:
        self.assertEqual(str(ReviewVerdict.PASS), "pass")
        self.assertEqual(str(ReviewVerdict.NEEDS_WORK), "needs_work")

    def test_verdict_is_str_enum(self) -> None:
        import enum
        self.assertTrue(issubclass(ReviewVerdict, str))
        self.assertTrue(issubclass(ReviewVerdict, enum.Enum))


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

class ModuleConstantTests(unittest.TestCase):
    def test_required_fields_match_baseline_exactly(self) -> None:
        self.assertEqual(REVIEW_REPORT_REQUIRED_FIELDS, BASELINE_REQUIRED)

    def test_optional_fields_is_a_tuple(self) -> None:
        self.assertIsInstance(REVIEW_REPORT_OPTIONAL_FIELDS, tuple)

    def test_kind_is_review_report(self) -> None:
        self.assertEqual(REVIEW_REPORT_SCHEMA_KIND, "review_report")

    def test_version_is_stable_string(self) -> None:
        self.assertEqual(
            REVIEW_REPORT_SCHEMA_VERSION, "review_report_schema_v1"
        )

    def test_required_fields_contains_verdict(self) -> None:
        self.assertIn("verdict", REVIEW_REPORT_REQUIRED_FIELDS)

    def test_required_fields_contains_memory(self) -> None:
        self.assertIn("memory", REVIEW_REPORT_REQUIRED_FIELDS)


# ---------------------------------------------------------------------------
# Default schema
# ---------------------------------------------------------------------------

class DefaultSchemaTests(unittest.TestCase):
    def test_default_schema_contains_all_baseline_required_fields(self) -> None:
        schema = default_review_report_schema()
        for f in BASELINE_REQUIRED:
            with self.subTest(field=f):
                self.assertIn(f, schema.required_fields)

    def test_default_schema_uses_module_constants(self) -> None:
        schema = default_review_report_schema()
        self.assertEqual(schema.required_fields, REVIEW_REPORT_REQUIRED_FIELDS)
        self.assertEqual(schema.optional_fields, REVIEW_REPORT_OPTIONAL_FIELDS)

    def test_default_schema_kind_and_version(self) -> None:
        schema = default_review_report_schema()
        self.assertEqual(schema.kind, REVIEW_REPORT_SCHEMA_KIND)
        self.assertEqual(schema.version, REVIEW_REPORT_SCHEMA_VERSION)

    def test_default_schema_allowed_verdicts_are_all_four(self) -> None:
        schema = default_review_report_schema()
        self.assertEqual(set(schema.allowed_verdicts), set(ReviewVerdict))
        self.assertEqual(len(schema.allowed_verdicts), 4)

    def test_default_schema_allowed_verdicts_canonical_order(self) -> None:
        schema = default_review_report_schema()
        self.assertEqual(
            list(schema.allowed_verdicts),
            list(ReviewVerdict),
        )

    def test_default_schema_validates(self) -> None:
        self.assertEqual(
            validate_review_report_schema(default_review_report_schema()), ()
        )

    def test_default_schema_is_frozen(self) -> None:
        schema = default_review_report_schema()
        with self.assertRaises(Exception):
            schema.required_fields = ()  # type: ignore[misc]


# ---------------------------------------------------------------------------
# to_dict serialization
# ---------------------------------------------------------------------------

class SchemaSerializationTests(unittest.TestCase):
    def test_to_dict_key_set(self) -> None:
        payload = default_review_report_schema().to_dict()
        self.assertEqual(
            set(payload.keys()),
            {"kind", "version", "required_fields", "optional_fields",
             "allowed_verdicts"},
        )

    def test_to_dict_plain_python_types(self) -> None:
        payload = default_review_report_schema().to_dict()
        self.assertIsInstance(payload["kind"], str)
        self.assertIsInstance(payload["version"], str)
        self.assertIsInstance(payload["required_fields"], list)
        self.assertIsInstance(payload["optional_fields"], list)
        self.assertIsInstance(payload["allowed_verdicts"], list)

    def test_to_dict_required_fields_order_preserved(self) -> None:
        payload = default_review_report_schema().to_dict()
        self.assertEqual(
            payload["required_fields"], list(REVIEW_REPORT_REQUIRED_FIELDS)
        )

    def test_to_dict_allowed_verdicts_are_strings(self) -> None:
        payload = default_review_report_schema().to_dict()
        for item in payload["allowed_verdicts"]:
            with self.subTest(item=item):
                self.assertIsInstance(item, str)
        self.assertEqual(
            payload["allowed_verdicts"],
            ["pass", "needs_work", "blocked", "override"],
        )

    def test_to_dict_no_enum_objects(self) -> None:
        import enum
        payload = default_review_report_schema().to_dict()
        for key, value in payload.items():
            with self.subTest(key=key):
                if isinstance(value, list):
                    for item in value:
                        self.assertNotIsInstance(item, enum.Enum)
                else:
                    self.assertNotIsInstance(value, enum.Enum)


# ---------------------------------------------------------------------------
# Validation success
# ---------------------------------------------------------------------------

class ValidationSuccessTests(unittest.TestCase):
    def test_default_schema_validates(self) -> None:
        self.assertEqual(
            validate_review_report_schema(default_review_report_schema()), ()
        )

    def test_custom_schema_with_extra_required_fields_validates(self) -> None:
        schema = ReviewReportSchema(
            required_fields=REVIEW_REPORT_REQUIRED_FIELDS + ("extra field",),
            optional_fields=(),
        )
        self.assertEqual(validate_review_report_schema(schema), ())

    def test_empty_optional_fields_validates(self) -> None:
        schema = ReviewReportSchema(
            required_fields=REVIEW_REPORT_REQUIRED_FIELDS,
            optional_fields=(),
        )
        self.assertEqual(validate_review_report_schema(schema), ())

    def test_list_collections_accepted(self) -> None:
        schema = ReviewReportSchema(
            required_fields=list(REVIEW_REPORT_REQUIRED_FIELDS),  # type: ignore[arg-type]
            optional_fields=[],  # type: ignore[arg-type]
        )
        self.assertEqual(validate_review_report_schema(schema), ())


# ---------------------------------------------------------------------------
# Missing required fields
# ---------------------------------------------------------------------------

class MissingRequiredFieldTests(unittest.TestCase):
    def test_each_baseline_field_individually_required(self) -> None:
        for missing in BASELINE_REQUIRED:
            with self.subTest(missing=missing):
                remaining = tuple(
                    f for f in REVIEW_REPORT_REQUIRED_FIELDS if f != missing
                )
                schema = ReviewReportSchema(required_fields=remaining)
                errors = validate_review_report_schema(schema)
                self.assertIn(
                    f"required_fields must include {missing}", errors
                )

    def test_all_required_fields_missing_when_empty(self) -> None:
        schema = ReviewReportSchema(required_fields=())
        errors = validate_review_report_schema(schema)
        for baseline in BASELINE_REQUIRED:
            with self.subTest(field=baseline):
                self.assertIn(
                    f"required_fields must include {baseline}", errors
                )


# ---------------------------------------------------------------------------
# Malformed collections do not raise
# ---------------------------------------------------------------------------

class MalformedCollectionsDoNotRaiseTests(unittest.TestCase):
    def test_non_iterable_required_fields_does_not_raise(self) -> None:
        schema = ReviewReportSchema(
            required_fields=42,  # type: ignore[arg-type]
        )
        try:
            errors = validate_review_report_schema(schema)
        except Exception as exc:
            self.fail(f"validator raised {type(exc).__name__}: {exc}")
        self.assertIn(
            "required_fields must be a tuple or list of non-empty strings",
            errors,
        )

    def test_none_required_fields_does_not_raise(self) -> None:
        schema = ReviewReportSchema(
            required_fields=None,  # type: ignore[arg-type]
        )
        try:
            errors = validate_review_report_schema(schema)
        except Exception as exc:
            self.fail(f"validator raised {type(exc).__name__}: {exc}")
        self.assertGreater(len(errors), 0)

    def test_non_iterable_optional_fields_does_not_raise(self) -> None:
        schema = ReviewReportSchema(
            required_fields=REVIEW_REPORT_REQUIRED_FIELDS,
            optional_fields=42,  # type: ignore[arg-type]
        )
        try:
            errors = validate_review_report_schema(schema)
        except Exception as exc:
            self.fail(f"validator raised {type(exc).__name__}: {exc}")
        self.assertIn(
            "optional_fields must be a tuple or list of non-empty strings",
            errors,
        )

    def test_none_optional_fields_does_not_raise(self) -> None:
        schema = ReviewReportSchema(
            required_fields=REVIEW_REPORT_REQUIRED_FIELDS,
            optional_fields=None,  # type: ignore[arg-type]
        )
        try:
            errors = validate_review_report_schema(schema)
        except Exception as exc:
            self.fail(f"validator raised {type(exc).__name__}: {exc}")
        self.assertGreater(len(errors), 0)

    def test_non_iterable_allowed_verdicts_does_not_raise(self) -> None:
        schema = ReviewReportSchema(
            required_fields=REVIEW_REPORT_REQUIRED_FIELDS,
            allowed_verdicts=42,  # type: ignore[arg-type]
        )
        try:
            errors = validate_review_report_schema(schema)
        except Exception as exc:
            self.fail(f"validator raised {type(exc).__name__}: {exc}")
        self.assertIn(
            "allowed_verdicts must be a tuple or list of ReviewVerdict values",
            errors,
        )

    def test_none_allowed_verdicts_does_not_raise(self) -> None:
        schema = ReviewReportSchema(
            required_fields=REVIEW_REPORT_REQUIRED_FIELDS,
            allowed_verdicts=None,  # type: ignore[arg-type]
        )
        try:
            errors = validate_review_report_schema(schema)
        except Exception as exc:
            self.fail(f"validator raised {type(exc).__name__}: {exc}")
        self.assertGreater(len(errors), 0)

    def test_fully_malformed_schema_does_not_raise(self) -> None:
        schema = ReviewReportSchema(
            required_fields=42,  # type: ignore[arg-type]
            optional_fields=None,  # type: ignore[arg-type]
            kind="",
            version="",
            allowed_verdicts=None,  # type: ignore[arg-type]
        )
        try:
            errors = validate_review_report_schema(schema)
        except Exception as exc:
            self.fail(f"validator raised {type(exc).__name__}: {exc}")
        self.assertGreater(len(errors), 0)

    def test_non_iterable_required_does_not_fire_missing_baseline_errors(self) -> None:
        schema = ReviewReportSchema(required_fields=42)  # type: ignore[arg-type]
        errors = validate_review_report_schema(schema)
        self.assertIn(
            "required_fields must be a tuple or list of non-empty strings",
            errors,
        )
        for baseline in BASELINE_REQUIRED:
            with self.subTest(field=baseline):
                self.assertNotIn(
                    f"required_fields must include {baseline}", errors
                )

    def test_non_iterable_allowed_verdicts_does_not_fire_missing_verdict_errors(
        self,
    ) -> None:
        schema = ReviewReportSchema(
            required_fields=REVIEW_REPORT_REQUIRED_FIELDS,
            allowed_verdicts=42,  # type: ignore[arg-type]
        )
        errors = validate_review_report_schema(schema)
        self.assertIn(
            "allowed_verdicts must be a tuple or list of ReviewVerdict values",
            errors,
        )
        for verdict in ReviewVerdict:
            with self.subTest(verdict=verdict.value):
                self.assertNotIn(
                    f"allowed_verdicts must include {verdict.value}", errors
                )


# ---------------------------------------------------------------------------
# Duplicate fields
# ---------------------------------------------------------------------------

class DuplicateFieldTests(unittest.TestCase):
    def test_duplicate_in_required_fields_reported(self) -> None:
        schema = ReviewReportSchema(
            required_fields=REVIEW_REPORT_REQUIRED_FIELDS + ("verdict",),
        )
        errors = validate_review_report_schema(schema)
        self.assertTrue(
            any("duplicate" in e and "verdict" in e for e in errors),
            f"expected duplicate error, got {errors}",
        )

    def test_duplicate_in_optional_fields_reported(self) -> None:
        schema = ReviewReportSchema(
            required_fields=REVIEW_REPORT_REQUIRED_FIELDS,
            optional_fields=("open questions", "open questions"),
        )
        errors = validate_review_report_schema(schema)
        self.assertTrue(
            any("duplicate" in e and "open questions" in e for e in errors),
            f"expected duplicate error, got {errors}",
        )

    def test_blank_entry_in_required_fields_reported(self) -> None:
        schema = ReviewReportSchema(
            required_fields=REVIEW_REPORT_REQUIRED_FIELDS + ("   ",),
        )
        errors = validate_review_report_schema(schema)
        self.assertTrue(
            any("must be a non-empty string" in e for e in errors),
            f"expected blank-entry error, got {errors}",
        )

    def test_blank_entry_in_optional_fields_reported(self) -> None:
        schema = ReviewReportSchema(
            required_fields=REVIEW_REPORT_REQUIRED_FIELDS,
            optional_fields=("ok", "   "),
        )
        errors = validate_review_report_schema(schema)
        self.assertTrue(
            any("must be a non-empty string" in e for e in errors),
            f"expected blank-entry error, got {errors}",
        )


# ---------------------------------------------------------------------------
# Malformed kind and version
# ---------------------------------------------------------------------------

class MalformedKindVersionTests(unittest.TestCase):
    def test_empty_kind_reported(self) -> None:
        schema = _valid_schema(kind="")
        errors = validate_review_report_schema(schema)
        self.assertIn("kind must be a non-empty string", errors)

    def test_whitespace_kind_reported(self) -> None:
        schema = _valid_schema(kind="   ")
        errors = validate_review_report_schema(schema)
        self.assertIn("kind must be a non-empty string", errors)

    def test_non_string_kind_reported(self) -> None:
        schema = _valid_schema(kind=42)  # type: ignore[arg-type]
        errors = validate_review_report_schema(schema)
        self.assertIn("kind must be a non-empty string", errors)

    def test_empty_version_reported(self) -> None:
        schema = _valid_schema(version="")
        errors = validate_review_report_schema(schema)
        self.assertIn("version must be a non-empty string", errors)

    def test_whitespace_version_reported(self) -> None:
        schema = _valid_schema(version="   ")
        errors = validate_review_report_schema(schema)
        self.assertIn("version must be a non-empty string", errors)

    def test_non_string_version_reported(self) -> None:
        schema = _valid_schema(version=None)  # type: ignore[arg-type]
        errors = validate_review_report_schema(schema)
        self.assertIn("version must be a non-empty string", errors)


# ---------------------------------------------------------------------------
# Missing canonical verdicts
# ---------------------------------------------------------------------------

class MissingVerdictTests(unittest.TestCase):
    def test_each_canonical_verdict_required(self) -> None:
        for missing in ReviewVerdict:
            with self.subTest(verdict=missing.value):
                remaining = tuple(
                    v for v in ReviewVerdict if v != missing
                )
                schema = _valid_schema(allowed_verdicts=remaining)
                errors = validate_review_report_schema(schema)
                self.assertIn(
                    f"allowed_verdicts must include {missing.value}", errors
                )

    def test_empty_allowed_verdicts_reports_all_missing(self) -> None:
        schema = _valid_schema(allowed_verdicts=())
        errors = validate_review_report_schema(schema)
        for verdict in ReviewVerdict:
            with self.subTest(verdict=verdict.value):
                self.assertIn(
                    f"allowed_verdicts must include {verdict.value}", errors
                )


# ---------------------------------------------------------------------------
# Malformed and duplicate allowed verdicts
# ---------------------------------------------------------------------------

class MalformedVerdictTests(unittest.TestCase):
    def test_plain_string_not_accepted_as_verdict(self) -> None:
        schema = _valid_schema(
            allowed_verdicts=("pass", "needs_work", "blocked", "override")
        )
        errors = validate_review_report_schema(schema)
        self.assertTrue(
            any("must be a ReviewVerdict instance" in e for e in errors),
            f"expected ReviewVerdict instance error, got {errors}",
        )

    def test_non_verdict_int_item_reported(self) -> None:
        schema = _valid_schema(
            allowed_verdicts=(42,)  # type: ignore[arg-type]
        )
        errors = validate_review_report_schema(schema)
        self.assertTrue(
            any("must be a ReviewVerdict instance" in e for e in errors),
            f"expected ReviewVerdict instance error, got {errors}",
        )

    def test_duplicate_verdict_reported(self) -> None:
        schema = _valid_schema(
            allowed_verdicts=(
                ReviewVerdict.PASS,
                ReviewVerdict.PASS,
                ReviewVerdict.NEEDS_WORK,
                ReviewVerdict.BLOCKED,
                ReviewVerdict.OVERRIDE,
            )
        )
        errors = validate_review_report_schema(schema)
        self.assertTrue(
            any("duplicate verdict" in e and "pass" in e for e in errors),
            f"expected duplicate verdict error, got {errors}",
        )

    def test_mixed_valid_and_invalid_items(self) -> None:
        schema = _valid_schema(
            allowed_verdicts=(
                ReviewVerdict.PASS,
                "needs_work",  # plain string, not accepted
                ReviewVerdict.BLOCKED,
                ReviewVerdict.OVERRIDE,
            )
        )
        errors = validate_review_report_schema(schema)
        self.assertTrue(
            any("must be a ReviewVerdict instance" in e for e in errors),
            f"expected ReviewVerdict instance error, got {errors}",
        )


# ---------------------------------------------------------------------------
# Validator purity
# ---------------------------------------------------------------------------

class ValidatorPurityTests(unittest.TestCase):
    def test_validator_is_pure_no_filesystem_required(self) -> None:
        from tempfile import TemporaryDirectory
        from pathlib import Path

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = sorted(p.relative_to(root) for p in root.rglob("*"))
            validate_review_report_schema(default_review_report_schema())
            after = sorted(p.relative_to(root) for p in root.rglob("*"))
            self.assertEqual(before, after)

    def test_validator_called_multiple_times_is_deterministic(self) -> None:
        schema = default_review_report_schema()
        first = validate_review_report_schema(schema)
        second = validate_review_report_schema(schema)
        self.assertEqual(first, second)
        self.assertEqual(first, ())


if __name__ == "__main__":
    unittest.main()
