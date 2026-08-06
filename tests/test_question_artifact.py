"""Tests for M011-S03: question artifact generator.

Covers:
- valid templates render required sections in deterministic order
- to_dict() is JSON-serializable plain Python
- validation rejects missing/empty required fields, bad statuses, unknown roles
  without raising
- filename/path helper rejects traversal, absolute paths, blank ids, unsafe chars
- preview/render is read-only (creates no files)
- explicit write creates only the expected file under 05_governance/questions/
- explicit write refuses to overwrite by default; overwrite works on request
- written content matches the rendered preview/render output
- context paths, options, and next action preserve caller order
- rendered content routes blocking ambiguity to a human/architect answer
- roles remain logical/provider-neutral; memory is not required
- package exports include the question API without breaking handoff exports
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from frutlups.question import (
    QUESTION_DIR,
    QuestionArtifact,
    QuestionArtifactPreview,
    QuestionArtifactTemplate,
    QuestionArtifactWriteCommand,
    QuestionArtifactWriteResult,
    preview_question_artifact,
    question_artifact_filename,
    render_question_artifact,
    validate_question_artifact_template,
    write_question_artifact,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CONTEXT = (
    "03_experiments/development_roadmap_frutlups.md",
    "CLAUDE.md",
)
_OPTIONS = (
    "Add attachment links now",
    "Defer attachments to a later slice",
)


def _valid_template(**overrides: object) -> QuestionArtifactTemplate:
    defaults: dict[str, object] = dict(
        question_id="m011-s03-attachment-scope",
        title="Should question artifacts support attachment links?",
        question="Does M011-S03 require attachment links in question artifacts?",
        rationale=(
            "The roadmap is silent on attachments; guessing risks scope creep."
        ),
        asker_role="coder",
        answerer_role="architect",
        status="open",
        milestone_id="M011",
        slice_id="M011-S03",
        context_paths=_CONTEXT,
        options=_OPTIONS,
        next_action="python -m frutlups status ..",
        notes=("raised during implementation",),
    )
    defaults.update(overrides)
    return QuestionArtifactTemplate(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Rendering and section order
# ---------------------------------------------------------------------------

class RenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.result = render_question_artifact(_valid_template())

    def test_valid_render(self) -> None:
        self.assertTrue(self.result.valid)
        self.assertEqual(self.result.errors, ())
        self.assertTrue(self.result.content.strip())

    def test_title_and_id_present(self) -> None:
        self.assertIn("# Question: Should question artifacts", self.result.content)
        self.assertIn("`m011-s03-attachment-scope`", self.result.content)

    def test_status_and_roles_present(self) -> None:
        c = self.result.content
        self.assertIn("Status: `open`", c)
        self.assertIn("Asker role: `coder`", c)
        self.assertIn("Answerer role: `architect`", c)

    def test_milestone_and_slice_present(self) -> None:
        c = self.result.content
        self.assertIn("`M011`", c)
        self.assertIn("`M011-S03`", c)

    def test_required_sections_in_deterministic_order(self) -> None:
        c = self.result.content
        order = [
            "# Question:",
            "## Question",
            "## Why It Matters",
            "## Context Artifacts",
            "## Options / Candidate Decisions",
            "## Recommended Next Action",
            "## Answer",
            "## Resolution Notes",
            "## Stop / Route Guidance",
        ]
        positions = [c.index(h) for h in order]
        self.assertEqual(positions, sorted(positions))

    def test_answer_and_resolution_placeholders(self) -> None:
        c = self.result.content
        self.assertIn("## Answer", c)
        self.assertIn("to be completed", c.lower())
        self.assertIn("## Resolution Notes", c)

    def test_stop_route_guidance(self) -> None:
        low = self.result.content.lower()
        self.assertIn("human or architect", low)
        self.assertIn("speculative", low)

    def test_context_paths_preserve_order(self) -> None:
        c = self.result.content
        self.assertLess(c.index(_CONTEXT[0]), c.index(_CONTEXT[1]))

    def test_options_preserve_order(self) -> None:
        c = self.result.content
        self.assertLess(c.index(_OPTIONS[0]), c.index(_OPTIONS[1]))

    def test_next_action_present(self) -> None:
        self.assertIn("python -m frutlups status ..", self.result.content)

    def test_empty_optional_collections_render_none(self) -> None:
        result = render_question_artifact(
            _valid_template(context_paths=(), options=(), next_action="", notes=())
        )
        self.assertTrue(result.valid)
        self.assertIn("*(none provided)*", result.content)
        self.assertIn("await an answer", result.content.lower())

    def test_deterministic_repeated_render(self) -> None:
        a = render_question_artifact(_valid_template()).content
        b = render_question_artifact(_valid_template()).content
        self.assertEqual(a, b)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

class SerializationTests(unittest.TestCase):
    def test_artifact_to_dict_json_safe(self) -> None:
        result = render_question_artifact(_valid_template())
        json.dumps(result.to_dict())
        self.assertIn("content", result.to_dict())

    def test_template_to_dict_json_safe(self) -> None:
        d = _valid_template().to_dict()
        json.dumps(d)
        self.assertIsInstance(d["context_paths"], list)
        self.assertIsInstance(d["options"], list)

    def test_preview_to_dict_json_safe(self) -> None:
        json.dumps(preview_question_artifact(_valid_template()).to_dict())


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class ValidationTests(unittest.TestCase):
    def test_valid_template_has_no_errors(self) -> None:
        self.assertEqual(validate_question_artifact_template(_valid_template()), ())

    def test_missing_required_fields(self) -> None:
        for fname in ("title", "question", "rationale"):
            errs = validate_question_artifact_template(_valid_template(**{fname: ""}))
            self.assertTrue(any(fname in e for e in errs), fname)

    def test_blank_question_id(self) -> None:
        errs = validate_question_artifact_template(_valid_template(question_id="  "))
        self.assertTrue(any("question_id" in e for e in errs))

    def test_unsupported_status(self) -> None:
        errs = validate_question_artifact_template(_valid_template(status="pending"))
        self.assertTrue(any("status" in e for e in errs))

    def test_unknown_asker_role(self) -> None:
        errs = validate_question_artifact_template(_valid_template(asker_role="robot"))
        self.assertTrue(any("asker_role" in e for e in errs))

    def test_unknown_answerer_role(self) -> None:
        errs = validate_question_artifact_template(
            _valid_template(answerer_role="robot")
        )
        self.assertTrue(any("answerer_role" in e for e in errs))

    def test_malformed_collection_does_not_raise(self) -> None:
        # Non-string entries must produce errors, not exceptions.
        errs = validate_question_artifact_template(
            _valid_template(context_paths=("ok", 123))  # type: ignore[arg-type]
        )
        self.assertTrue(any("context_paths" in e for e in errs))

    def test_none_collection_does_not_raise(self) -> None:
        errs = validate_question_artifact_template(
            _valid_template(options=None)  # type: ignore[arg-type]
        )
        self.assertTrue(any("options" in e for e in errs))

    def test_invalid_template_renders_empty(self) -> None:
        result = render_question_artifact(_valid_template(title=""))
        self.assertFalse(result.valid)
        self.assertEqual(result.content, "")
        self.assertTrue(result.errors)


# ---------------------------------------------------------------------------
# Filename / path safety
# ---------------------------------------------------------------------------

class FilenameSafetyTests(unittest.TestCase):
    def test_valid_filename(self) -> None:
        self.assertEqual(
            question_artifact_filename(_valid_template(question_id="abc-1_2")),
            "abc-1_2.md",
        )

    def test_rejects_path_traversal(self) -> None:
        self.assertEqual(
            question_artifact_filename(_valid_template(question_id="../evil")), ""
        )

    def test_rejects_slash(self) -> None:
        self.assertEqual(
            question_artifact_filename(_valid_template(question_id="a/b")), ""
        )

    def test_rejects_absolute_like(self) -> None:
        self.assertEqual(
            question_artifact_filename(_valid_template(question_id="/etc/passwd")), ""
        )

    def test_rejects_blank(self) -> None:
        self.assertEqual(
            question_artifact_filename(_valid_template(question_id="")), ""
        )

    def test_rejects_uppercase_and_spaces(self) -> None:
        self.assertEqual(
            question_artifact_filename(_valid_template(question_id="Bad Id")), ""
        )

    def test_rejects_dot(self) -> None:
        self.assertEqual(
            question_artifact_filename(_valid_template(question_id="a.b")), ""
        )

    def test_preview_target_path_uses_questions_dir(self) -> None:
        preview = preview_question_artifact(_valid_template())
        self.assertEqual(preview.target_path, f"{QUESTION_DIR}/m011-s03-attachment-scope.md")


# ---------------------------------------------------------------------------
# Read-only render / preview
# ---------------------------------------------------------------------------

class ReadOnlyTests(unittest.TestCase):
    def test_render_creates_no_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = set(root.rglob("*"))
            render_question_artifact(_valid_template())
            preview_question_artifact(_valid_template())
            after = set(root.rglob("*"))
        self.assertEqual(before, after)


# ---------------------------------------------------------------------------
# Explicit write surface
# ---------------------------------------------------------------------------

class WriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def _write(self, *, overwrite: bool = False, template=None):
        return write_question_artifact(
            QuestionArtifactWriteCommand(
                project_root=self.root,
                template=template if template is not None else _valid_template(),
                overwrite=overwrite,
            )
        )

    def test_write_creates_expected_file_only(self) -> None:
        result = self._write()
        self.assertTrue(result.wrote)
        target = self.root / "05_governance" / "questions" / "m011-s03-attachment-scope.md"
        self.assertTrue(target.is_file())
        created = [p for p in self.root.rglob("*") if p.is_file()]
        self.assertEqual(created, [target])

    def test_written_under_questions_dir(self) -> None:
        result = self._write()
        rel = Path(result.target_path).relative_to(self.root)
        self.assertEqual(rel.parent, Path("05_governance") / "questions")

    def test_written_content_matches_render(self) -> None:
        self._write()
        target = self.root / "05_governance" / "questions" / "m011-s03-attachment-scope.md"
        rendered = render_question_artifact(_valid_template()).content
        self.assertEqual(target.read_text(encoding="utf-8"), rendered)

    def test_refuses_overwrite_by_default(self) -> None:
        self.assertTrue(self._write().wrote)
        second = self._write()
        self.assertFalse(second.wrote)
        self.assertTrue(any("already exists" in e for e in second.errors))

    def test_overwrite_when_requested(self) -> None:
        self.assertTrue(self._write().wrote)
        again = self._write(overwrite=True)
        self.assertTrue(again.wrote)
        self.assertTrue(again.overwrote)

    def test_invalid_template_does_not_write(self) -> None:
        result = self._write(template=_valid_template(title=""))
        self.assertFalse(result.wrote)
        created = [p for p in self.root.rglob("*") if p.is_file()]
        self.assertEqual(created, [])

    def test_traversal_id_does_not_write(self) -> None:
        result = self._write(template=_valid_template(question_id="../escape"))
        self.assertFalse(result.wrote)
        created = [p for p in self.root.rglob("*") if p.is_file()]
        self.assertEqual(created, [])

    def test_result_to_dict_json_safe(self) -> None:
        json.dumps(self._write().to_dict())


# ---------------------------------------------------------------------------
# Provider neutrality and memory independence
# ---------------------------------------------------------------------------

class NeutralityAndMemoryTests(unittest.TestCase):
    def test_roles_are_logical(self) -> None:
        # All allowed roles render without naming a provider/model family.
        for role in ("architect", "reviewer", "coder", "human"):
            result = render_question_artifact(
                _valid_template(asker_role=role, answerer_role="human")
            )
            self.assertTrue(result.valid)
            low = result.content.lower()
            self.assertNotIn("anthropic", low)
            self.assertNotIn("openai", low)

    def test_no_provider_required_for_render(self) -> None:
        # GPT/Claude are presets, never required: not named in the artifact.
        c = render_question_artifact(_valid_template()).content.lower()
        self.assertNotIn("must use", c)

    def test_module_has_no_memory_or_subprocess_dependency(self) -> None:
        import frutlups.question as q

        source = Path(q.__file__).read_text(encoding="utf-8")
        self.assertNotIn("subprocess", source)
        self.assertNotIn("llloom", source)


# ---------------------------------------------------------------------------
# Package exports
# ---------------------------------------------------------------------------

class ExportTests(unittest.TestCase):
    def test_question_api_exported(self) -> None:
        import frutlups

        for name in (
            "QuestionArtifact",
            "QuestionArtifactTemplate",
            "QuestionArtifactWriteCommand",
            "QuestionArtifactWriteResult",
            "render_question_artifact",
            "preview_question_artifact",
            "write_question_artifact",
        ):
            self.assertTrue(hasattr(frutlups, name), name)

    def test_handoff_api_still_exported(self) -> None:
        import frutlups

        for name in (
            "build_coder_handoff",
            "CoderHandoff",
            "build_reviewer_handoff",
            "ReviewerHandoff",
        ):
            self.assertTrue(hasattr(frutlups, name), name)


if __name__ == "__main__":
    unittest.main()
