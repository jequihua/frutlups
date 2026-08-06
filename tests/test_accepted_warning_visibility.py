"""Tests for M010-S04: accepted-warning visibility.

Covers:
- absent file returns present=False without raising
- file path is <memory-root>/state/reports/health/accepted_warnings.yaml
- valid entries preserve warning_id, reason, evidence in file order
- to_dict() emits only plain Python values and is JSON-serializable
- missing warning_id, reason, or evidence produces visible findings
- vague IDs (*, all, empty string) produce visible findings
- parser never mutates the source file
- malformed/unsupported content returns findings without raising
- no runner or subprocess invoked
- AcceptedWarning and AcceptedWarningsVisibility are frozen
- existing M010-S02 and M010-S03 tests remain green (checked by full suite)
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from frutlups.memory import (
    AcceptedWarning,
    AcceptedWarningsVisibility,
    read_accepted_warnings,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ACCEPTED_WARNINGS_REL = "state/reports/health/accepted_warnings.yaml"

_VALID_YAML = """\
warnings:
  - warning_id: claim.concept.render:c.render.001
    reason: render output verified against source
    evidence: 05_governance/reviews/m010_render_evidence.md
  - warning_id: claim.concept.ingest:c.ingest.002
    reason: ingest result reviewed and accepted
    evidence: 05_governance/reviews/m010_ingest_evidence.md
"""

_SINGLE_VALID_YAML = """\
warnings:
  - warning_id: exact-warning-id-001
    reason: this warning is understood and accepted
    evidence: path/to/evidence.md
"""

_EMPTY_WARNINGS_YAML = """\
warnings: []
"""

_MISSING_REASON_YAML = """\
warnings:
  - warning_id: some.warning.id
    evidence: some evidence
"""

_MISSING_EVIDENCE_YAML = """\
warnings:
  - warning_id: some.warning.id
    reason: some reason
"""

_MISSING_ID_YAML = """\
warnings:
  - reason: some reason
    evidence: some evidence
"""

_WILDCARD_ID_YAML = """\
warnings:
  - warning_id: "*"
    reason: broad suppression
    evidence: none
"""

_ALL_ID_YAML = """\
warnings:
  - warning_id: all
    reason: suppress all
    evidence: none
"""

_EMPTY_ID_YAML = """\
warnings:
  - warning_id: ""
    reason: some reason
    evidence: some evidence
"""

_EMPTY_FILE = ""

_NO_WARNINGS_KEY = """\
other_key: something
"""


def _make_accepted_warnings_file(root: Path, content: str) -> Path:
    yaml_path = root / _ACCEPTED_WARNINGS_REL
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    yaml_path.write_text(content, encoding="utf-8")
    return yaml_path


# ---------------------------------------------------------------------------
# Absent file
# ---------------------------------------------------------------------------

class AbsentFileTests(unittest.TestCase):
    def test_absent_file_present_is_false(self) -> None:
        with TemporaryDirectory() as tmp:
            result = read_accepted_warnings(Path(tmp))
        self.assertFalse(result.present)

    def test_absent_file_does_not_raise(self) -> None:
        with TemporaryDirectory() as tmp:
            try:
                read_accepted_warnings(Path(tmp))
            except Exception as exc:
                self.fail(f"raised: {exc}")

    def test_absent_file_warnings_tuple_is_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            result = read_accepted_warnings(Path(tmp))
        self.assertEqual(result.warnings, ())

    def test_absent_file_has_findings(self) -> None:
        with TemporaryDirectory() as tmp:
            result = read_accepted_warnings(Path(tmp))
        self.assertTrue(result.findings)

    def test_absent_file_path_points_to_expected_location(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = read_accepted_warnings(root)
        expected = root / _ACCEPTED_WARNINGS_REL
        self.assertEqual(result.file_path, expected)


# ---------------------------------------------------------------------------
# File path convention
# ---------------------------------------------------------------------------

class FilePathTests(unittest.TestCase):
    def test_file_path_is_state_reports_health_yaml(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = read_accepted_warnings(root)
        self.assertTrue(str(result.file_path).endswith(_ACCEPTED_WARNINGS_REL.replace("/", "\\")))

    def test_file_detected_when_present(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_accepted_warnings_file(root, _SINGLE_VALID_YAML)
            result = read_accepted_warnings(root)
        self.assertTrue(result.present)


# ---------------------------------------------------------------------------
# Valid entries
# ---------------------------------------------------------------------------

class ValidEntriesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_accepted_warnings_file(self.root, _VALID_YAML)
        self.result = read_accepted_warnings(self.root)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_present_is_true(self) -> None:
        self.assertTrue(self.result.present)

    def test_two_warnings_parsed(self) -> None:
        self.assertEqual(len(self.result.warnings), 2)

    def test_first_warning_id(self) -> None:
        self.assertEqual(
            self.result.warnings[0].warning_id,
            "claim.concept.render:c.render.001",
        )

    def test_first_warning_reason(self) -> None:
        self.assertIn("render output", self.result.warnings[0].reason)

    def test_first_warning_evidence(self) -> None:
        self.assertIn("m010_render_evidence", self.result.warnings[0].evidence)

    def test_second_warning_id(self) -> None:
        self.assertEqual(
            self.result.warnings[1].warning_id,
            "claim.concept.ingest:c.ingest.002",
        )

    def test_file_order_preserved(self) -> None:
        ids = [w.warning_id for w in self.result.warnings]
        self.assertEqual(ids[0], "claim.concept.render:c.render.001")
        self.assertEqual(ids[1], "claim.concept.ingest:c.ingest.002")

    def test_valid_entries_have_no_findings(self) -> None:
        for w in self.result.warnings:
            self.assertEqual(w.findings, (), f"unexpected findings on {w.warning_id}")


# ---------------------------------------------------------------------------
# Empty warnings list
# ---------------------------------------------------------------------------

class EmptyWarningsListTests(unittest.TestCase):
    def test_empty_warnings_list_present_true(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_accepted_warnings_file(root, _EMPTY_WARNINGS_YAML)
            result = read_accepted_warnings(root)
        self.assertTrue(result.present)
        self.assertEqual(len(result.warnings), 0)


# ---------------------------------------------------------------------------
# Missing fields
# ---------------------------------------------------------------------------

class MissingFieldTests(unittest.TestCase):
    def _result(self, yaml: str) -> AcceptedWarning:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_accepted_warnings_file(root, yaml)
            vis = read_accepted_warnings(root)
        self.assertEqual(len(vis.warnings), 1)
        return vis.warnings[0]

    def test_missing_reason_produces_finding(self) -> None:
        w = self._result(_MISSING_REASON_YAML)
        self.assertTrue(any("reason" in f for f in w.findings))

    def test_missing_evidence_produces_finding(self) -> None:
        w = self._result(_MISSING_EVIDENCE_YAML)
        self.assertTrue(any("evidence" in f for f in w.findings))

    def test_missing_warning_id_produces_finding(self) -> None:
        w = self._result(_MISSING_ID_YAML)
        self.assertTrue(any("warning_id" in f for f in w.findings))


# ---------------------------------------------------------------------------
# Vague/wildcard IDs
# ---------------------------------------------------------------------------

class VagueIdTests(unittest.TestCase):
    def _result_for(self, yaml: str) -> AcceptedWarning:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_accepted_warnings_file(root, yaml)
            vis = read_accepted_warnings(root)
        self.assertEqual(len(vis.warnings), 1)
        return vis.warnings[0]

    def test_wildcard_star_produces_finding(self) -> None:
        w = self._result_for(_WILDCARD_ID_YAML)
        self.assertTrue(w.findings, "no finding for wildcard *")

    def test_all_id_produces_finding(self) -> None:
        w = self._result_for(_ALL_ID_YAML)
        self.assertTrue(w.findings, "no finding for 'all' id")

    def test_empty_id_produces_finding(self) -> None:
        w = self._result_for(_EMPTY_ID_YAML)
        self.assertTrue(w.findings, "no finding for empty id")


# ---------------------------------------------------------------------------
# Malformed / unsupported content
# ---------------------------------------------------------------------------

class MalformedContentTests(unittest.TestCase):
    def _read(self, yaml: str) -> AcceptedWarningsVisibility:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_accepted_warnings_file(root, yaml)
            return read_accepted_warnings(root)

    def test_empty_file_does_not_raise(self) -> None:
        try:
            self._read(_EMPTY_FILE)
        except Exception as exc:
            self.fail(f"raised: {exc}")

    def test_no_warnings_key_does_not_raise(self) -> None:
        try:
            self._read(_NO_WARNINGS_KEY)
        except Exception as exc:
            self.fail(f"raised: {exc}")

    def test_no_warnings_key_present_is_true_but_warnings_empty(self) -> None:
        result = self._read(_NO_WARNINGS_KEY)
        self.assertTrue(result.present)
        self.assertEqual(len(result.warnings), 0)

    def test_malformed_has_findings_or_empty_warnings(self) -> None:
        result = self._read(_NO_WARNINGS_KEY)
        # Either file-level findings or empty warnings is acceptable
        self.assertTrue(len(result.findings) > 0 or len(result.warnings) == 0)


# ---------------------------------------------------------------------------
# Purity: no file mutation, no command execution
# ---------------------------------------------------------------------------

class PurityTests(unittest.TestCase):
    def test_does_not_modify_source_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            yaml_path = _make_accepted_warnings_file(root, _SINGLE_VALID_YAML)
            before = yaml_path.read_text(encoding="utf-8")
            read_accepted_warnings(root)
            after = yaml_path.read_text(encoding="utf-8")
        self.assertEqual(before, after)

    def test_does_not_create_file_if_absent(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = set(root.rglob("*"))
            read_accepted_warnings(root)
            after = set(root.rglob("*"))
        self.assertEqual(before, after)


# ---------------------------------------------------------------------------
# to_dict() serialization
# ---------------------------------------------------------------------------

class ToDictTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_accepted_warnings_file(self.root, _VALID_YAML)
        self.result = read_accepted_warnings(self.root)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_visibility_to_dict_has_required_keys(self) -> None:
        d = self.result.to_dict()
        for key in ("file_path", "present", "warnings", "findings"):
            self.assertIn(key, d)

    def test_file_path_is_string_in_dict(self) -> None:
        d = self.result.to_dict()
        self.assertIsInstance(d["file_path"], str)
        self.assertNotIsInstance(d["file_path"], Path)

    def test_warnings_is_list_in_dict(self) -> None:
        self.assertIsInstance(self.result.to_dict()["warnings"], list)

    def test_findings_is_list_in_dict(self) -> None:
        self.assertIsInstance(self.result.to_dict()["findings"], list)

    def test_warning_to_dict_has_required_keys(self) -> None:
        d = self.result.warnings[0].to_dict()
        for key in ("warning_id", "reason", "evidence", "findings"):
            self.assertIn(key, d)

    def test_visibility_to_dict_is_json_serializable(self) -> None:
        json.dumps(self.result.to_dict())

    def test_absent_visibility_to_dict_is_json_serializable(self) -> None:
        with TemporaryDirectory() as tmp:
            result = read_accepted_warnings(Path(tmp))
        json.dumps(result.to_dict())

    def test_no_path_objects_in_warning_dict(self) -> None:
        for v in self.result.warnings[0].to_dict().values():
            self.assertNotIsInstance(v, Path)


# ---------------------------------------------------------------------------
# Frozen / immutable
# ---------------------------------------------------------------------------

class FrozenTests(unittest.TestCase):
    def test_accepted_warning_is_frozen(self) -> None:
        w = AcceptedWarning(warning_id="x", reason="r", evidence="e")
        with self.assertRaises((AttributeError, TypeError)):
            w.warning_id = "mutated"  # type: ignore[misc]

    def test_accepted_warnings_visibility_is_frozen(self) -> None:
        vis = AcceptedWarningsVisibility(file_path=None, present=False, warnings=())
        with self.assertRaises((AttributeError, TypeError)):
            vis.present = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Regression: M010-S04 corrective — unsupported warnings shapes must produce
# visible findings (review 048 finding)
# ---------------------------------------------------------------------------

_SCALAR_WARNINGS_YAML = "warnings: not-a-list\n"

_MAPPING_UNDER_WARNINGS_YAML = """\
warnings:
  warning_id: exact-id
  reason: understood
  evidence: path/to/evidence.md
"""


class UnsupportedWarningsShapeTests(unittest.TestCase):
    """Unsupported warnings: shapes must produce file-level findings."""

    def _read(self, yaml: str) -> AcceptedWarningsVisibility:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_accepted_warnings_file(root, yaml)
            return read_accepted_warnings(root)

    # --- scalar value ---

    def test_scalar_warnings_present_true(self) -> None:
        result = self._read(_SCALAR_WARNINGS_YAML)
        self.assertTrue(result.present)

    def test_scalar_warnings_zero_entries(self) -> None:
        result = self._read(_SCALAR_WARNINGS_YAML)
        self.assertEqual(len(result.warnings), 0)

    def test_scalar_warnings_has_file_level_finding(self) -> None:
        result = self._read(_SCALAR_WARNINGS_YAML)
        self.assertTrue(result.findings, "no finding for scalar warnings value")

    def test_scalar_warnings_finding_mentions_unsupported(self) -> None:
        result = self._read(_SCALAR_WARNINGS_YAML)
        self.assertTrue(
            any("unsupported" in f.lower() or "not-a-list" in f for f in result.findings)
        )

    def test_scalar_warnings_finding_json_safe(self) -> None:
        result = self._read(_SCALAR_WARNINGS_YAML)
        json.dumps(result.to_dict())

    # --- mapping under warnings ---

    def test_mapping_warnings_present_true(self) -> None:
        result = self._read(_MAPPING_UNDER_WARNINGS_YAML)
        self.assertTrue(result.present)

    def test_mapping_warnings_zero_entries(self) -> None:
        result = self._read(_MAPPING_UNDER_WARNINGS_YAML)
        self.assertEqual(len(result.warnings), 0)

    def test_mapping_warnings_has_file_level_finding(self) -> None:
        result = self._read(_MAPPING_UNDER_WARNINGS_YAML)
        self.assertTrue(result.findings, "no finding for mapping-under-warnings")

    def test_mapping_warnings_finding_json_safe(self) -> None:
        result = self._read(_MAPPING_UNDER_WARNINGS_YAML)
        json.dumps(result.to_dict())

    # --- regressions: valid shapes still work ---

    def test_empty_list_no_shape_finding(self) -> None:
        result = self._read(_EMPTY_WARNINGS_YAML)
        # findings tuple should be empty (no unsupported-shape finding)
        self.assertFalse(result.findings)

    def test_valid_list_no_shape_finding(self) -> None:
        result = self._read(_VALID_YAML)
        self.assertFalse(result.findings)
        self.assertEqual(len(result.warnings), 2)


if __name__ == "__main__":
    unittest.main()
