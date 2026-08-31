"""Tests for M004-S01 attempt 003: changed-method governed publication.

The proof tables are hand-frozen at the public boundary. They do not derive
expected classifications, reason codes, owned paths, or outcomes from product
constants. Temporary repositories live beneath the repository's ignored
``local_state/`` and are removed by ``TemporaryDirectory`` after each test.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import inspect
import os
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

from frutlups import publication
from frutlups.publication import (
    PUBLICATION_OUTCOMES,
    PUBLICATION_REFUSAL_CODES,
    PUBLISHED,
    RECOVERY_REQUIRED,
    REFUSED,
    AbsentObservation,
    PresentObservation,
    PublicationError,
    UnreadableObservation,
    UnsafeObservation,
    allocate_attempt,
    publish_corrective_attempt,
)
from frutlups.slice_prompt import ContractVocab, SliceEntry, parse_sidecar, render_prompt

TEST_ROOT = Path(__file__).resolve().parent
REPO_ROOT = TEST_ROOT.parent
RELEASE_AUTHORITY = TEST_ROOT / "fixtures" / "release_v0_2_0"
LAYOUT_PATH = RELEASE_AUTHORITY / "frutlups.layout.yaml"
FIXTURES = RELEASE_AUTHORITY / "slice_contract"
LOCAL_STATE = REPO_ROOT / "local_state"

CORRECTIVE = ("all_fields.slices.yaml", "M002-S02")
ROUTINE = ("all_fields.slices.yaml", "M001-S01")
INITIAL_SIDECAR = "all_fields_attempt_001.slices.yaml"
SLICE_ID = "M002-S02"

PRIOR_EVIDENCE_BYTES = b'{"ledger": "partial"}\n'
RULING_BYTES = b"# Owner note 003\n"

# Target-content/stat and mutation seams that an untrusted target may not reach.
TARGET_SEAMS = (
    "_read_bytes",
    "_target_exists",
    "_observe_path",
    "_stage_and_replace",
    "_create_exclusive",
    "_rollback",
    "_remove",
)


def _vocab() -> ContractVocab:
    return ContractVocab.from_layout(LAYOUT_PATH)


def _entry(name: str, slice_id: str) -> SliceEntry:
    parsed = parse_sidecar(FIXTURES / name, _vocab(), sidecar_path=FIXTURES / name)
    entry = parsed.entry(slice_id)
    assert entry is not None, f"{slice_id} not found in {name}"
    return entry


def _with_correction(entry: SliceEntry, **overrides: object) -> SliceEntry:
    data = copy.deepcopy(dict(entry.data))
    data["correction"] = {**data["correction"], **overrides}
    return SliceEntry(data)


def _with_fields(entry: SliceEntry, **overrides: object) -> SliceEntry:
    data = copy.deepcopy(dict(entry.data))
    data.update(overrides)
    return SliceEntry(data)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _present(data: bytes) -> PresentObservation:
    return PresentObservation(_sha256(data))


def _write(root: Path, relative: str, data: bytes) -> str:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return _sha256(data)


def _carrier_bytes(slice_id: str, attempt: str | None) -> bytes:
    lines = ["# Coding Prompt", "", "## Typed Entry", "", "```yaml", f'slice: "{slice_id}"']
    if attempt is not None:
        lines.append(f'attempt: "{attempt}"')
    lines += ["```", ""]
    return "\n".join(lines).encode("utf-8")


def _entry_block(sidecar: bytes, slice_id: str) -> bytes:
    """Extract one hand-selected top-level entry block from a frozen fixture."""

    lines = sidecar.splitlines(keepends=True)
    start = next(
        index
        for index, line in enumerate(lines)
        if line.rstrip(b"\r\n") == f"- slice: {slice_id}".encode("utf-8")
    )
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].startswith(b"- slice:")
        ),
        len(lines),
    )
    return b"".join(lines[start:end])


def _sidecar_document(*entries: bytes) -> bytes:
    return (
        b"slice_prompt_contract_version: 1\n"
        b"roadmap: active_roadmap.md\n"
        b"slices:\n"
        + b"".join(entries)
    )


def _identity(mode: int, *, attributes: int = 0) -> SimpleNamespace:
    """A frozen non-following identity for the semantic filesystem shim."""

    return SimpleNamespace(
        st_mode=mode,
        st_dev=11,
        st_ino=17,
        st_file_attributes=attributes,
    )


class AllocateAttemptTests(unittest.TestCase):
    def test_empty_history_allocates_the_first_attempt(self) -> None:
        self.assertEqual(allocate_attempt([]), "001")

    def test_next_attempt_is_strictly_above_the_max_even_when_gapped(self) -> None:
        rows = ((["001"], "002"), (["001", "002"], "003"), (["002", "001"], "003"), (["005"], "006"))
        for existing, expected in rows:
            with self.subTest(existing=existing):
                self.assertEqual(allocate_attempt(existing), expected)

    def test_invalid_and_exhausted_history_raise(self) -> None:
        for existing in (["abc"], ["000"], ["1"], ["0001"], ["999"]):
            with self.subTest(existing=existing):
                with self.assertRaises(PublicationError):
                    allocate_attempt(existing)


class PublicBoundaryTests(unittest.TestCase):
    def test_public_signature_has_no_caller_supplied_authority_object(self) -> None:
        self.assertEqual(
            tuple(inspect.signature(publish_corrective_attempt).parameters),
            ("entry", "repo_root", "sidecar_path", "prompt_path"),
        )
        with self.assertRaises(TypeError):
            publish_corrective_attempt(
                entry=_entry(*CORRECTIVE),
                repo_root=REPO_ROOT,
                sidecar_path="03_experiments/active_roadmap.slices.yaml",
                prompt_path="prompts/for_coding_agent/999_rogue.md",
                layout=object(),  # type: ignore[call-arg]
            )


class _PublishRepo(unittest.TestCase):
    """A bounded temporary repository with governed publication inputs."""

    def setUp(self) -> None:
        LOCAL_STATE.mkdir(exist_ok=True)
        self._tmp = TemporaryDirectory(prefix="m004_publication_", dir=LOCAL_STATE)
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "repo"
        self.root.mkdir()
        self.vocab = _vocab()
        self.layout_bytes = LAYOUT_PATH.read_bytes()
        (self.root / "frutlups.layout.yaml").write_bytes(self.layout_bytes)

        base = _entry(*CORRECTIVE)
        correction = base.data["correction"]
        self.evidence_path = correction["prior_evidence"][0]["path"]
        self.ruling_path = correction["controlling_ruling"]
        evidence_digest = _write(self.root, self.evidence_path, PRIOR_EVIDENCE_BYTES)
        _write(self.root, self.ruling_path, RULING_BYTES)
        self.entry = _with_correction(
            base,
            prior_evidence=[{"path": self.evidence_path, "sha256": evidence_digest}],
        )

        self.sidecar_bytes = (FIXTURES / INITIAL_SIDECAR).read_bytes()
        self.sidecar_rel = "03_experiments/active_roadmap.slices.yaml"
        self.sidecar = self.root / self.sidecar_rel
        self.sidecar.parent.mkdir(parents=True, exist_ok=True)
        (self.sidecar.parent / "active_roadmap.md").write_bytes(
            (FIXTURES / "active_roadmap.md").read_bytes()
        )
        self.sidecar.write_bytes(self.sidecar_bytes)

        self.prompt_dir = self.root / "prompts" / "for_coding_agent"
        self.prompt_dir.mkdir(parents=True, exist_ok=True)
        self.prompt_rel = "prompts/for_coding_agent/013_m002_s02_attempt_002.md"
        self.prompt = self.root / self.prompt_rel
        self.publish_tmp_rel = self.sidecar_rel + ".publish-tmp"
        self.rollback_tmp_rel = self.sidecar_rel + ".rollback-tmp"
        self.publish_tmp = self.root / self.publish_tmp_rel
        self.rollback_tmp = self.root / self.rollback_tmp_rel

    def _publish(self, **overrides: object):
        kwargs = dict(
            entry=self.entry,
            repo_root=self.root,
            sidecar_path=self.sidecar_rel,
            prompt_path=self.prompt_rel,
        )
        kwargs.update(overrides)
        return publish_corrective_attempt(**kwargs)

    def _add_carrier(self, name: str, body: bytes) -> Path:
        target = self.prompt_dir / name
        target.write_bytes(body)
        return target

    def _sidecar_entry(self, slice_id: str) -> SliceEntry | None:
        return parse_sidecar(
            self.sidecar.read_bytes(), self.vocab, sidecar_path=self.sidecar
        ).entry(slice_id)

    def _reset_artifacts(self) -> None:
        self.sidecar.write_bytes(self.sidecar_bytes)
        for child in tuple(self.prompt_dir.iterdir()):
            if child.is_file() or child.is_symlink():
                child.unlink()
        for temporary in (self.publish_tmp, self.rollback_tmp):
            if temporary.exists():
                temporary.unlink()

    def _owned(self, sidecar, prompt, publish_tmp=None, rollback_tmp=None):
        absent = AbsentObservation()
        return {
            self.sidecar_rel: absent if sidecar is None else _present(sidecar),
            self.prompt_rel: absent if prompt is None else _present(prompt),
            self.publish_tmp_rel: absent if publish_tmp is None else _present(publish_tmp),
            self.rollback_tmp_rel: absent if rollback_tmp is None else _present(rollback_tmp),
        }


class HappyPathTests(_PublishRepo):
    def test_fixture_root_is_beneath_repository_local_state(self) -> None:
        self.assertEqual(Path(self._tmp.name).parent, LOCAL_STATE)
        self.assertTrue(Path(self._tmp.name).is_relative_to(REPO_ROOT / "local_state"))

    def test_public_reason_and_outcome_vocabulary_is_frozen(self) -> None:
        self.assertEqual(PUBLICATION_OUTCOMES, ("published", "refused", "recovery_required"))
        self.assertEqual(PUBLICATION_REFUSAL_CODES, (
            "layout_unresolved", "not_corrective", "entry_not_ready", "entry_unhealthy",
            "role_impure", "rework_context_unresolved", "target_unbound",
            "slice_not_in_sidecar", "history_unresolved", "attempt_not_fresh",
            "prompt_collision", "sidecar_update_invalid", "publish_write_failed",
            "recovery_required",
        ))

    def test_valid_loaded_layout_control_publishes_with_complete_typed_maps(self) -> None:
        result = self._publish()

        self.assertEqual(result.outcome, PUBLISHED)
        self.assertTrue(result.published)
        self.assertEqual(result.refusals, ())
        self.assertEqual(result.attempt, "002")
        self.assertEqual(result.sidecar_path, str(self.sidecar))
        self.assertEqual(result.prompt_path, str(self.prompt))
        expected_prompt = render_prompt(self.entry, self.vocab).encode("utf-8")
        self.assertEqual(self.prompt.read_bytes(), expected_prompt)
        self.assertEqual(dict(self._sidecar_entry(SLICE_ID).data), dict(self.entry.data))
        self.assertEqual(result.before, self._owned(self.sidecar_bytes, None))
        self.assertEqual(result.after, self._owned(self.sidecar.read_bytes(), expected_prompt))

    def test_other_sidecar_entries_are_preserved(self) -> None:
        original = parse_sidecar(self.sidecar_bytes, self.vocab).entry("M001-S01")
        self.assertTrue(self._publish().published)
        self.assertEqual(dict(self._sidecar_entry("M001-S01").data), dict(original.data))


class AuthorityTests(_PublishRepo):
    @contextlib.contextmanager
    def _spy_target_seams(self):
        with contextlib.ExitStack() as stack:
            yield {
                name: stack.enter_context(mock.patch.object(publication, name, autospec=True))
                for name in TARGET_SEAMS
            }

    def _assert_no_target_io(self, result, spies, code: str, story: str) -> None:
        self.assertEqual(result.outcome, REFUSED)
        self.assertIn(code, result.refusal_codes())
        for name, spy in spies.items():
            self.assertEqual(spy.call_count, 0, f"{name} was reached for {story}")
        self.assertEqual(self.sidecar.read_bytes(), self.sidecar_bytes)

    def test_rogue_layout_keyword_is_unrepresentable_and_reaches_no_target_seam(self) -> None:
        with self._spy_target_seams() as spies:
            with self.assertRaises(TypeError):
                publish_corrective_attempt(
                    entry=self.entry,
                    repo_root=self.root,
                    sidecar_path=self.sidecar_rel,
                    prompt_path=self.prompt_rel,
                    layout=object(),  # type: ignore[call-arg]
                )
        for name, spy in spies.items():
            self.assertEqual(spy.call_count, 0, f"{name} was reached for rogue authority")

    def test_frozen_lexical_and_shape_table_refuses_before_target_io(self) -> None:
        cases = (
            ("absolute in-root sidecar", {"sidecar_path": self.sidecar}),
            ("absolute in-root prompt", {"prompt_path": self.prompt}),
            ("sidecar traversal", {"sidecar_path": "03_experiments/../escape.slices.yaml"}),
            ("prompt traversal", {"prompt_path": "prompts/for_coding_agent/../../013_x.md"}),
            ("sidecar wrong directory", {"sidecar_path": "08_pkg/active_roadmap.slices.yaml"}),
            ("sidecar wrong suffix", {"sidecar_path": "03_experiments/active_roadmap.yaml"}),
            ("sidecar empty stem", {"sidecar_path": "03_experiments/.slices.yaml"}),
            ("sidecar absent", {"sidecar_path": "03_experiments/missing.slices.yaml"}),
            ("prompt wrong directory", {"prompt_path": "prompts/for_review_agent/013_x.md"}),
            ("prompt wrong filename shape", {"prompt_path": "prompts/for_coding_agent/draft.md"}),
        )
        for story, override in cases:
            with self.subTest(story=story), self._spy_target_seams() as spies:
                result = self._publish(**override)
                self._assert_no_target_io(result, spies, "target_unbound", story)

    def test_alias_rows_refuse_before_target_content_or_mutation_seams(self) -> None:
        for story, target in (("sidecar alias", self.sidecar), ("prompt alias", self.prompt)):
            with self.subTest(story=story):
                def fake_is_symlink(path_self, *, expected=target):
                    return path_self == expected

                with self._spy_target_seams() as spies, mock.patch.object(Path, "is_symlink", fake_is_symlink):
                    result = self._publish()
                self._assert_no_target_io(result, spies, "target_unbound", story)

    def test_alias_layout_and_unsupported_layout_refuse_before_target_io(self) -> None:
        layout_path = self.root / "frutlups.layout.yaml"

        def fake_layout_alias(path_self):
            return path_self == layout_path

        with self._spy_target_seams() as spies, mock.patch.object(Path, "is_symlink", fake_layout_alias):
            result = self._publish()
        self._assert_no_target_io(result, spies, "layout_unresolved", "layout alias")

        bad_layout = self.layout_bytes.replace(
            b'filename_pattern: "{sequence:03d}_{slug}.md"',
            b'filename_pattern: "{slug}.md"',
        )
        self.assertNotEqual(bad_layout, self.layout_bytes)
        layout_path.write_bytes(bad_layout)
        with self._spy_target_seams() as spies:
            result = self._publish()
        self._assert_no_target_io(result, spies, "layout_unresolved", "unsupported layout")


class HistoryTests(_PublishRepo):
    def test_absent_current_attempt_is_the_initial_001_state(self) -> None:
        routine = _entry_block(self.sidecar_bytes, "M001-S01")
        routine = routine.replace(b"- slice: M001-S01", b"- slice: M002-S02", 1)
        routine = routine.replace(b"  milestone: M001", b"  milestone: M002", 1)
        changed = _sidecar_document(routine)
        parsed = parse_sidecar(changed, self.vocab, sidecar_path=self.sidecar)
        self.assertEqual(parsed.diagnostic_codes(), ())
        self.assertNotIn(b"  attempt:", changed)
        self.sidecar.write_bytes(changed)

        result = self._publish()

        self.assertEqual(result.outcome, PUBLISHED)
        self.assertEqual(result.attempt, "002")

    def test_complete_sidecar_diagnostic_table_stops_before_consumption(self) -> None:
        target_001 = _entry_block(self.sidecar_bytes, SLICE_ID)
        target_999 = target_001.replace(b"  attempt: '001'", b"  attempt: '999'", 1)
        unrelated = _entry_block(self.sidecar_bytes, "M001-S01")
        malformed_unrelated = unrelated.replace(
            b"  authored_by: architect_reviewer", b"  authored_by: coder", 1
        )
        rows = (
            (
                "target attempt 001 then 999",
                _sidecar_document(target_001, target_999),
                ("duplicate_slice",),
            ),
            (
                "target attempt 999 then 001",
                _sidecar_document(target_999, target_001),
                ("duplicate_slice",),
            ),
            (
                "duplicate unrelated slice",
                _sidecar_document(target_001, unrelated, unrelated),
                ("duplicate_slice",),
            ),
            (
                "malformed unrelated entry",
                _sidecar_document(target_001, malformed_unrelated),
                ("authored_by_invalid",),
            ),
            (
                "invalid top-level structure",
                (FIXTURES / "sidecar_not_mapping.slices.yaml").read_bytes(),
                ("sidecar_not_mapping",),
            ),
            (
                "invalid entry structure",
                (FIXTURES / "slice_not_mapping.slices.yaml").read_bytes(),
                ("slice_not_mapping",),
            ),
        )
        stopped_seams = (
            "_slice_history",
            "render_prompt",
            "_splice_entry",
            "_commit",
            "_stage_and_replace",
            "_create_exclusive",
            "_rollback",
            "_remove",
        )
        for story, body, expected_diagnostics in rows:
            with self.subTest(story=story):
                self._reset_artifacts()
                self.sidecar.write_bytes(body)
                parsed = parse_sidecar(body, self.vocab, sidecar_path=self.sidecar)
                self.assertEqual(parsed.diagnostic_codes(), expected_diagnostics)
                with contextlib.ExitStack() as stack:
                    spies = {
                        name: stack.enter_context(
                            mock.patch.object(publication, name, autospec=True)
                        )
                        for name in stopped_seams
                    }
                    result = self._publish()
                self.assertEqual(result.outcome, REFUSED)
                self.assertEqual(result.refusal_codes(), ("history_unresolved",))
                self.assertFalse(self.prompt.exists())
                for name, spy in spies.items():
                    self.assertEqual(spy.call_count, 0, f"{story} reached {name}")

    def test_diagnostic_free_single_target_control_publishes(self) -> None:
        target = _entry_block(self.sidecar_bytes, SLICE_ID)
        body = _sidecar_document(target)
        self.sidecar.write_bytes(body)
        self.assertEqual(
            parse_sidecar(body, self.vocab, sidecar_path=self.sidecar).diagnostic_codes(),
            (),
        )

        result = self._publish()

        self.assertEqual(result.outcome, PUBLISHED)
        self.assertEqual(result.refusal_codes(), ())

    def test_post_splice_complete_parse_must_be_diagnostic_free_and_exact(self) -> None:
        target = _entry_block(self.sidecar_bytes, SLICE_ID)
        invalid_proposal = _sidecar_document(target, target).decode("utf-8")
        with mock.patch.object(
            publication, "_splice_entry", return_value=invalid_proposal
        ), mock.patch.object(publication, "_commit", autospec=True) as commit:
            result = self._publish()

        self.assertEqual(result.outcome, REFUSED)
        self.assertEqual(result.refusal_codes(), ("sidecar_update_invalid",))
        self.assertEqual(commit.call_count, 0)
        self.assertEqual(self.sidecar.read_bytes(), self.sidecar_bytes)
        self.assertFalse(self.prompt.exists())

    def test_present_invalid_current_attempt_is_unresolved_not_initial(self) -> None:
        changed = self.sidecar_bytes.replace(b"  attempt: '001'", b"  attempt: BAD", 1)
        self.assertNotEqual(changed, self.sidecar_bytes)
        self.sidecar.write_bytes(changed)

        result = self._publish()

        self.assertEqual(result.outcome, REFUSED)
        self.assertEqual(result.refusal_codes(), ("history_unresolved",))
        self.assertFalse(self.prompt.exists())

    def test_frozen_total_raw_marker_classifier_table(self) -> None:
        valid_same = _carrier_bytes(SLICE_ID, "004")
        valid_other = _carrier_bytes("M009-S09", "777")
        legacy_invalid_utf8 = b"# legacy bytes\n\xff\n"
        invalid_utf8_marked = b"# prompt\n## Typed Entry\n```yaml\nslice: M002-S02\nattempt: '004'\n```\n\xff"
        missing_fence = b"# prompt\n## Typed Entry\nslice: M002-S02\nattempt: '004'\n"
        missing_closing_fence = b"## Typed Entry\n```yaml\nslice: M002-S02\nattempt: '004'\n"
        duplicate_fence = (
            b"## Typed Entry\n```yaml\nslice: M002-S02\nattempt: '004'\n```\n"
            b"```yaml\nslice: M002-S02\nattempt: '005'\n```\n"
        )
        yaml_failure = b"## Typed Entry\n```yaml\nslice: [\n```\n"
        non_mapping = b"## Typed Entry\n```yaml\n- M002-S02\n- '004'\n```\n"
        missing_slice = b"## Typed Entry\n```yaml\nattempt: '004'\n```\n"
        invalid_slice = b"## Typed Entry\n```yaml\nslice: bad\nattempt: '004'\n```\n"
        missing_attempt = b"## Typed Entry\n```yaml\nslice: M002-S02\n```\n"
        invalid_attempt = b"## Typed Entry\n```yaml\nslice: M002-S02\nattempt: BAD\n```\n"
        marker_disagreement = (
            b"## Typed Entry\n```yaml\nslice: M002-S02\nattempt: '004'\n```\n"
            b"## Typed Entry\n```yaml\nslice: M002-S02\nattempt: '005'\n```\n"
        )
        oversized = b"## Typed Entry\n" + (b"x" * 1_048_576)

        cases = (
            ("legacy invalid UTF-8", legacy_invalid_utf8, "legacy_non_contract", PUBLISHED, ()),
            ("valid other slice", valid_other, "valid_other_slice", PUBLISHED, ()),
            ("valid same slice higher", valid_same, "valid_same_slice", REFUSED, ("attempt_not_fresh",)),
            ("invalid UTF-8 after marker", invalid_utf8_marked, "malformed", REFUSED, ("history_unresolved",)),
            ("missing carrier fence", missing_fence, "malformed", REFUSED, ("history_unresolved",)),
            ("missing closing fence", missing_closing_fence, "malformed", REFUSED, ("history_unresolved",)),
            ("duplicate carrier fence", duplicate_fence, "malformed", REFUSED, ("history_unresolved",)),
            ("YAML failure", yaml_failure, "malformed", REFUSED, ("history_unresolved",)),
            ("non-mapping data", non_mapping, "malformed", REFUSED, ("history_unresolved",)),
            ("missing slice", missing_slice, "malformed", REFUSED, ("history_unresolved",)),
            ("invalid slice", invalid_slice, "malformed", REFUSED, ("history_unresolved",)),
            ("missing attempt", missing_attempt, "malformed", REFUSED, ("history_unresolved",)),
            ("invalid attempt", invalid_attempt, "malformed", REFUSED, ("history_unresolved",)),
            ("carrier disagreement", marker_disagreement, "malformed", REFUSED, ("history_unresolved",)),
            ("bounded oversize", oversized, "malformed", REFUSED, ("history_unresolved",)),
        )
        for story, body, expected_kind, expected_outcome, expected_codes in cases:
            with self.subTest(story=story):
                self._reset_artifacts()
                carrier = self._add_carrier("010_history_case.md", body)
                kind, _ = publication._carrier_attempt(carrier, SLICE_ID)
                self.assertEqual(kind, expected_kind)
                result = self._publish()
                self.assertEqual(result.outcome, expected_outcome)
                self.assertEqual(result.refusal_codes(), expected_codes)

    def test_higher_duplicate_gapped_exhausted_and_collision_rows(self) -> None:
        cases = (
            ("duplicate alternate path", "002", self.entry, self.prompt_rel, REFUSED, ("attempt_not_fresh",)),
            ("higher stale", "004", self.entry, self.prompt_rel, REFUSED, ("attempt_not_fresh",)),
            ("exhausted", "999", self.entry, self.prompt_rel, REFUSED, ("attempt_not_fresh",)),
            (
                "gapped allocation above max",
                "004",
                _with_fields(self.entry, attempt="005"),
                "prompts/for_coding_agent/014_m002_s02_attempt_005.md",
                PUBLISHED,
                (),
            ),
        )
        for story, historical_attempt, entry, target, expected_outcome, expected_codes in cases:
            with self.subTest(story=story):
                self._reset_artifacts()
                self._add_carrier("010_alternate.md", _carrier_bytes(SLICE_ID, historical_attempt))
                result = self._publish(entry=entry, prompt_path=target)
                self.assertEqual(result.outcome, expected_outcome)
                self.assertEqual(result.refusal_codes(), expected_codes)

        self._reset_artifacts()
        self.prompt.write_bytes(b"legacy destination collision\n")
        result = self._publish()
        self.assertEqual(result.outcome, REFUSED)
        self.assertEqual(result.refusal_codes(), ("prompt_collision",))
        self.assertEqual(self.prompt.read_bytes(), b"legacy destination collision\n")


class RecoveryTests(_PublishRepo):
    """Frozen mutation-order matrix over the complete four-path state."""

    def _assert_receipt(self, result, outcome, codes, before, after) -> None:
        self.assertEqual(result.outcome, outcome)
        self.assertEqual(result.refusal_codes(), codes)
        self.assertEqual(result.before, before)
        self.assertEqual(result.after, after)
        self.assertEqual(tuple(result.before), (
            self.sidecar_rel,
            self.prompt_rel,
            self.publish_tmp_rel,
            self.rollback_tmp_rel,
        ))
        self.assertEqual(tuple(result.after), tuple(result.before))

    def test_stage_write_and_replace_fault_rows(self) -> None:
        before = self._owned(self.sidecar_bytes, None)
        with mock.patch.object(
            publication, "_write_descriptor", side_effect=OSError("stage write failed")
        ):
            result = self._publish()
        self._assert_receipt(result, REFUSED, ("publish_write_failed",), before, before)

        self._reset_artifacts()
        with mock.patch.object(publication.os, "replace", side_effect=OSError("replace failed")):
            result = self._publish()
        self._assert_receipt(result, REFUSED, ("publish_write_failed",), before, before)

        self._reset_artifacts()
        real_unlink = Path.unlink

        def fail_publish_cleanup(path_self, *args, **kwargs):
            if path_self == self.publish_tmp:
                raise OSError("publish temporary cleanup failed")
            return real_unlink(path_self, *args, **kwargs)

        with mock.patch.object(publication.os, "replace", side_effect=OSError("replace failed")), \
                mock.patch.object(Path, "unlink", fail_publish_cleanup):
            result = self._publish()
        publish_residue = self.publish_tmp.read_bytes()
        self._assert_receipt(
            result,
            RECOVERY_REQUIRED,
            ("recovery_required",),
            before,
            self._owned(self.sidecar_bytes, None, publish_tmp=publish_residue),
        )

    def test_exclusive_create_collision_partial_and_clean_failure_rows(self) -> None:
        before = self._owned(self.sidecar_bytes, None)
        with mock.patch.object(publication, "_create_exclusive", side_effect=OSError("open failed")):
            result = self._publish()
        self._assert_receipt(result, REFUSED, ("publish_write_failed",), before, before)

        self._reset_artifacts()

        def true_collision(target, data):
            Path(target).write_bytes(b"FOREIGN")
            raise FileExistsError("exclusive-create collision")

        with mock.patch.object(publication, "_create_exclusive", side_effect=true_collision):
            result = self._publish()
        self._assert_receipt(
            result,
            RECOVERY_REQUIRED,
            ("recovery_required",),
            before,
            self._owned(self.sidecar_bytes, b"FOREIGN"),
        )

        self._reset_artifacts()

        def partial_prompt(target, data):
            Path(target).write_bytes(b"PARTIAL")
            raise OSError("partial prompt")

        with mock.patch.object(publication, "_create_exclusive", side_effect=partial_prompt), \
                mock.patch.object(publication, "_remove", side_effect=OSError("cleanup failed")):
            result = self._publish()
        self._assert_receipt(
            result,
            RECOVERY_REQUIRED,
            ("recovery_required",),
            before,
            self._owned(self.sidecar_bytes, b"PARTIAL"),
        )

    def test_rollback_replace_and_cleanup_fault_rows(self) -> None:
        before = self._owned(self.sidecar_bytes, None)
        real_replace = publication.os.replace

        def fail_rollback_replace(source, target):
            if str(source).endswith(".rollback-tmp"):
                raise OSError("rollback replace failed")
            return real_replace(source, target)

        with mock.patch.object(publication, "_create_exclusive", side_effect=OSError("prompt open failed")), \
                mock.patch.object(publication.os, "replace", side_effect=fail_rollback_replace):
            result = self._publish()
        advanced = self.sidecar.read_bytes()
        self._assert_receipt(
            result,
            RECOVERY_REQUIRED,
            ("recovery_required",),
            before,
            self._owned(advanced, None),
        )

        self._reset_artifacts()
        real_unlink = Path.unlink

        def fail_rollback_cleanup(path_self, *args, **kwargs):
            if path_self == self.rollback_tmp:
                raise OSError("rollback temporary cleanup failed")
            return real_unlink(path_self, *args, **kwargs)

        with mock.patch.object(publication, "_create_exclusive", side_effect=OSError("prompt open failed")), \
                mock.patch.object(publication.os, "replace", side_effect=fail_rollback_replace), \
                mock.patch.object(Path, "unlink", fail_rollback_cleanup):
            result = self._publish()
        advanced = self.sidecar.read_bytes()
        rollback_residue = self.rollback_tmp.read_bytes()
        self._assert_receipt(
            result,
            RECOVERY_REQUIRED,
            ("recovery_required",),
            before,
            self._owned(advanced, None, rollback_tmp=rollback_residue),
        )

    def test_preexisting_temporary_and_unreadable_before_state_fail_closed(self) -> None:
        self.publish_tmp.write_bytes(b"PREEXISTING")
        expected = self._owned(self.sidecar_bytes, None, publish_tmp=b"PREEXISTING")
        result = self._publish()
        self._assert_receipt(result, RECOVERY_REQUIRED, ("recovery_required",), expected, expected)
        self.assertEqual(self.sidecar.read_bytes(), self.sidecar_bytes)

        self._reset_artifacts()
        real_observe = publication._observe_path

        def unreadable_rollback_temp(path):
            if path == self.rollback_tmp:
                return UnreadableObservation()
            return real_observe(path)

        unreadable = self._owned(self.sidecar_bytes, None)
        unreadable[self.rollback_tmp_rel] = UnreadableObservation()
        with mock.patch.object(publication, "_observe_path", side_effect=unreadable_rollback_temp):
            result = self._publish()
        self._assert_receipt(result, RECOVERY_REQUIRED, ("recovery_required",), unreadable, unreadable)
        self.assertEqual(self.sidecar.read_bytes(), self.sidecar_bytes)

    def test_nonfollowing_temporary_identity_semantic_shim_table(self) -> None:
        rows = (
            (
                "dangling symlink",
                _identity(stat.S_IFLNK | 0o777),
                UnsafeObservation("alias"),
            ),
            (
                "live symlink",
                _identity(stat.S_IFLNK | 0o777),
                UnsafeObservation("alias"),
            ),
            (
                "junction or reparse alias",
                _identity(stat.S_IFDIR | 0o777, attributes=0x400),
                UnsafeObservation("alias"),
            ),
            (
                "directory",
                _identity(stat.S_IFDIR | 0o777),
                UnsafeObservation("directory"),
            ),
            (
                "other non-regular node",
                _identity(stat.S_IFIFO | 0o600),
                UnsafeObservation("non_regular"),
            ),
            (
                "identity unreadable",
                PermissionError("identity denied"),
                UnreadableObservation(),
            ),
        )
        real_lstat = publication._lstat
        real_open = publication._open_for_observation
        for story, identity_or_error, expected_observation in rows:
            with self.subTest(story=story):
                self._reset_artifacts()

                def semantic_lstat(path, *, row=identity_or_error):
                    if path == self.publish_tmp:
                        if isinstance(row, OSError):
                            raise row
                        return row
                    return real_lstat(path)

                expected = self._owned(self.sidecar_bytes, None)
                expected[self.publish_tmp_rel] = expected_observation
                with mock.patch.object(
                    publication, "_lstat", side_effect=semantic_lstat
                ), mock.patch.object(
                    publication, "_open_for_observation", wraps=real_open
                ) as opened, mock.patch.object(
                    publication, "_stage_and_replace", autospec=True
                ) as stage:
                    result = self._publish()
                self._assert_receipt(
                    result,
                    RECOVERY_REQUIRED,
                    ("recovery_required",),
                    expected,
                    expected,
                )
                self.assertNotIn(mock.call(self.publish_tmp), opened.call_args_list)
                self.assertEqual(stage.call_count, 0)
                self.assertEqual(self.sidecar.read_bytes(), self.sidecar_bytes)
                self.assertFalse(self.prompt.exists())

    def test_real_directory_and_regular_temporary_collisions_are_not_absent(self) -> None:
        self.publish_tmp.mkdir()
        directory_map = self._owned(self.sidecar_bytes, None)
        directory_map[self.publish_tmp_rel] = UnsafeObservation("directory")
        result = self._publish()
        self._assert_receipt(
            result,
            RECOVERY_REQUIRED,
            ("recovery_required",),
            directory_map,
            directory_map,
        )
        self.assertTrue(self.publish_tmp.is_dir())
        self.publish_tmp.rmdir()

        self.publish_tmp.write_bytes(b"REGULAR COLLISION")
        regular_map = self._owned(
            self.sidecar_bytes, None, publish_tmp=b"REGULAR COLLISION"
        )
        result = self._publish()
        self._assert_receipt(
            result,
            RECOVERY_REQUIRED,
            ("recovery_required",),
            regular_map,
            regular_map,
        )
        self.assertEqual(self.publish_tmp.read_bytes(), b"REGULAR COLLISION")

    def test_absent_temporary_control_uses_exclusive_descriptor_staging(self) -> None:
        with mock.patch.object(
            Path,
            "write_bytes",
            side_effect=AssertionError("Path.write_bytes reached after setup"),
        ) as following_write:
            result = self._publish()

        self.assertEqual(result.outcome, PUBLISHED)
        self.assertEqual(following_write.call_count, 0)
        self.assertEqual(result.before, self._owned(self.sidecar_bytes, None))
        self.assertEqual(
            result.after,
            self._owned(
                self.sidecar.read_bytes(),
                render_prompt(self.entry, self.vocab).encode("utf-8"),
            ),
        )

    def test_exclusive_create_collision_and_identity_disagreement_force_recovery(self) -> None:
        unchanged = self._owned(self.sidecar_bytes, None)
        with mock.patch.object(
            publication,
            "_open_exclusive",
            side_effect=FileExistsError("exclusive-create collision"),
        ):
            result = self._publish()
        self._assert_receipt(
            result,
            RECOVERY_REQUIRED,
            ("recovery_required",),
            unchanged,
            unchanged,
        )

        self._reset_artifacts()
        real_open_exclusive = publication._open_exclusive
        real_fstat = publication._fstat
        staged_descriptors: set[int] = set()

        def track_exclusive(path):
            descriptor = real_open_exclusive(path)
            staged_descriptors.add(descriptor)
            return descriptor

        def disagreeing_fstat(descriptor):
            if descriptor in staged_descriptors:
                staged_descriptors.remove(descriptor)
                return _identity(stat.S_IFIFO | 0o600)
            return real_fstat(descriptor)

        with mock.patch.object(
            publication, "_open_exclusive", side_effect=track_exclusive
        ), mock.patch.object(
            publication, "_fstat", side_effect=disagreeing_fstat
        ):
            result = self._publish()
        self._assert_receipt(
            result,
            RECOVERY_REQUIRED,
            ("recovery_required",),
            unchanged,
            unchanged,
        )
        self.assertFalse(self.publish_tmp.exists())

    def test_observation_identity_change_is_unsafe(self) -> None:
        self.publish_tmp.write_bytes(b"REGULAR")
        with mock.patch.object(publication, "_same_identity", return_value=False):
            observed = publication._observe_path(self.publish_tmp)
        self.assertEqual(observed, UnsafeObservation("identity_changed"))

    def test_optional_host_dangling_alias_probe_never_counts_as_absent(self) -> None:
        missing_target = self.root / "missing-alias-target"
        try:
            self.publish_tmp.symlink_to(missing_target)
        except (NotImplementedError, OSError):
            return
        self.assertTrue(os.path.lexists(self.publish_tmp))

        result = self._publish()

        expected = self._owned(self.sidecar_bytes, None)
        expected[self.publish_tmp_rel] = UnsafeObservation("alias")
        self._assert_receipt(
            result,
            RECOVERY_REQUIRED,
            ("recovery_required",),
            expected,
            expected,
        )
        self.assertFalse(missing_target.exists())

    def test_unreadable_after_state_cannot_report_published(self) -> None:
        real_observe_owned = publication._observe_owned
        calls = 0

        def unreadable_after(paths):
            nonlocal calls
            calls += 1
            observed = real_observe_owned(paths)
            if calls == 2:
                observed[self.prompt_rel] = UnreadableObservation()
            return observed

        with mock.patch.object(publication, "_observe_owned", side_effect=unreadable_after):
            result = self._publish()

        expected_prompt = render_prompt(self.entry, self.vocab).encode("utf-8")
        expected_after = self._owned(self.sidecar.read_bytes(), expected_prompt)
        expected_after[self.prompt_rel] = UnreadableObservation()
        self._assert_receipt(
            result,
            RECOVERY_REQUIRED,
            ("recovery_required",),
            self._owned(self.sidecar_bytes, None),
            expected_after,
        )


class PreconditionRefusalTests(_PublishRepo):
    def test_non_corrective_entry_refuses(self) -> None:
        result = self._publish(entry=_entry(*ROUTINE))
        self.assertEqual(result.outcome, REFUSED)
        self.assertEqual(result.refusal_codes(), ("not_corrective",))
        self.assertIsNone(result.before)
        self.assertIsNone(result.after)

    def test_role_crossing_manifest_row_refuses(self) -> None:
        data = copy.deepcopy(dict(self.entry.data))
        for write in data["writes"]:
            if write["artifact_type"] == "review_report":
                write["role_owner"] = "coder"
        result = self._publish(entry=SliceEntry(data))
        self.assertEqual(result.outcome, REFUSED)
        self.assertIn("role_impure", result.refusal_codes())

    def test_forged_prior_evidence_refuses(self) -> None:
        result = self._publish(
            entry=_with_correction(
                self.entry,
                prior_evidence=[{"path": self.evidence_path, "sha256": "0" * 64}],
            )
        )
        self.assertEqual(result.outcome, REFUSED)
        self.assertEqual(result.refusal_codes(), ("rework_context_unresolved",))

    def test_slice_absent_from_the_sidecar_refuses(self) -> None:
        empty_rel = "03_experiments/empty.slices.yaml"
        unrelated = _entry_block(self.sidecar_bytes, "M001-S01")
        unrelated = unrelated.replace(b"- slice: M001-S01", b"- slice: M009-S09", 1)
        unrelated = unrelated.replace(b"  milestone: M001", b"  milestone: M009", 1)
        body = _sidecar_document(unrelated)
        (self.root / empty_rel).write_bytes(body)
        self.assertEqual(
            parse_sidecar(
                body, self.vocab, sidecar_path=self.root / empty_rel
            ).diagnostic_codes(),
            (),
        )
        result = self._publish(sidecar_path=empty_rel)
        self.assertEqual(result.outcome, REFUSED)
        self.assertEqual(result.refusal_codes(), ("slice_not_in_sidecar",))


if __name__ == "__main__":
    unittest.main()
