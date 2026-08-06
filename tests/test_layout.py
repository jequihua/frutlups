"""Tests for M017-S01: layout/profile configuration with a v2 default.

Covers the exported ``parse_simple_yaml`` compatibility wrapper, the built-in
v2/legacy profiles, config->profile
mapping (base detection, schema-version and unsafe-path diagnostics), the loader
precedence, path-escape safety, and the profile routed through ``build_status``,
coding-prompt metadata parsing, self-report derivation, and the CLI. Also includes
an integration check against the external v2 template when it is available.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import yaml

import frutlups
from frutlups._yaml import YamlBoundaryError, YamlFailure
from frutlups.cli import main
from frutlups.layout import (
    LayoutConfigError,
    LayoutDiagnosticSeverity,
    ProfileSource,
    default_profile,
    is_safe_relative,
    legacy_profile,
    load_config_file,
    load_layout_profile,
    parse_simple_yaml,
    profile_from_config,
    resolve_under_root,
    v2_default_profile,
)
from frutlups.project import (
    _parse_coding_prompt_meta,
    build_coding_prompt_plan,
    build_status,
)
from frutlups.prompt_template import (
    CodingPromptTemplate,
    CodingPromptWriteCommand,
    write_coding_prompt,
)
from frutlups.prompts import PromptArtifact, PromptKind
from frutlups.review_prompt_template import (
    ReviewPromptTemplate,
    ReviewPromptWriteCommand,
    write_review_prompt,
)

EXTERNAL_V2 = Path(os.environ.get("FRUTLUPS_EXTERNAL_V2_TEMPLATE", "__frutlups_external_v2_template_not_configured__"))

_V2_CONFIG = """\
schema_version: frutlups_layout_config_v0
profile_id: artifact_first_template_v2
template_root: "."

workspace_map:
  required_for_base_profile:
    - "00_brief"
    - "03_experiments"
    - "05_governance"
    - "prompts"
    - "questions"

state:
  canonical_file: "PROJECT_STATE.md"
  mode_fields:
    memory:
      label: "Memory mode"
      allowed_values:
        - "none"
        - "lightweight"
        - "llloom"
      default: "none"
    frutlups:
      label: "Frutlups mode"
      allowed_values:
        - "manual"
        - "semi-manual"
        - "automated driver"
      default: "manual"

roadmaps:
  directory: "03_experiments"
  active_roadmap_glob: "*active_roadmap*.md"
  development_roadmap_glob: "*development_roadmap*.md"

prompts:
  coding_prompt_dir: "prompts/for_coding_agent"
  review_prompt_dir: "prompts/for_review_agent"
  required_coding_prompt_sections:
    - "Current State"
    - "Read First"
    - "Task"
    - "Self-Report"

reports:
  reviews_dir: "05_governance/reviews"
"""

_PROJECT_STATE = """\
# Project State

Memory mode:
- none

Frutlups mode:
- manual
"""

_V2_CODING_PROMPT = """\
---
milestone: M001
slice: M001-S01
role: coder
title: first slice
---

# Coding Prompt 001: first slice

## Current State

Read PROJECT_STATE.md.

## Read First

- `CLAUDE.md`
- `README.md`

## Task

Do the thing.

## Non-Goals

- Do not over-build.

## Self-Report

Write a self-report using the canonical schema in
`prompts/templates/self_report.md`.

## Definition Of Done

- done
"""


def _make_v2_project(root: Path, *, with_config: bool = True, with_state: bool = True) -> None:
    for name in (
        "00_brief",
        "03_experiments",
        "05_governance/reviews",
        "prompts/for_coding_agent",
        "prompts/for_review_agent",
        "prompts/templates",
        "questions",
    ):
        (root / name).mkdir(parents=True, exist_ok=True)
    # M003-S03: v2 profiles render through the configured scaffolds; the
    # accepted scaffold bytes ship as package-relative fixtures.
    repo_templates = Path(__file__).resolve().parent / "fixtures" / "front_repo_contract"
    for scaffold in ("coding_prompt.md", "review_prompt.md"):
        (root / "prompts" / "templates" / scaffold).write_text(
            (repo_templates / scaffold).read_text(encoding="utf-8"), encoding="utf-8"
        )
    if with_config:
        (root / "frutlups.layout.yaml").write_text(_V2_CONFIG, encoding="utf-8")
    if with_state:
        (root / "PROJECT_STATE.md").write_text(_PROJECT_STATE, encoding="utf-8")
    (root / "03_experiments" / "active_roadmap.md").write_text(
        "### M001: First\n\nStatus: active\n", encoding="utf-8"
    )
    (root / "03_experiments" / "development_roadmap.md").write_text(
        "### M001: First\n\nSlices:\n\n- M001-S01: first slice\n", encoding="utf-8"
    )


def _make_legacy_project(root: Path) -> None:
    for name in (
        "00_brief",
        "03_experiments",
        "05_governance/reviews",
        "06_infra",
        "08_pkg",
        "prompts/for_coding_agent",
        "prompts/for_review_agent",
    ):
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / "03_experiments" / "active_roadmap_frutlups.md").write_text(
        "### M001: First\n\nStatus: active\n", encoding="utf-8"
    )


class SimpleYamlParserTests(unittest.TestCase):
    """The exported `parse_simple_yaml` compatibility wrapper (M002-S05).

    Expectations follow PyYAML SafeLoader semantics: plain scalars are typed,
    folded block scalars keep their trailing newline. The broad wrapper
    contract lives in `test_parse_simple_yaml_compat.py`.
    """

    def test_nested_maps_and_lists(self) -> None:
        data = parse_simple_yaml(
            "a: 1\nb:\n  c: hello\n  d:\n    - x\n    - y\n"
        )
        # Deliberate native change: the plain scalar is now typed, not "1".
        self.assertEqual(data["a"], 1)
        self.assertEqual(data["b"], {"c": "hello", "d": ["x", "y"]})

    def test_quoted_strings_and_escapes(self) -> None:
        data = parse_simple_yaml('p: "C:\\\\Users\\\\dev"\nq: \'plain\'\n')
        self.assertEqual(data["p"], "C:\\Users\\dev")
        self.assertEqual(data["q"], "plain")

    def test_null_and_bool(self) -> None:
        data = parse_simple_yaml("a: null\nb: true\nc: false\n")
        self.assertIsNone(data["a"])
        self.assertIs(data["b"], True)
        self.assertIs(data["c"], False)

    def test_comments_ignored(self) -> None:
        data = parse_simple_yaml("# header\na: 1  # trailing\nb: 2\n")
        self.assertEqual(data["a"], 1)
        self.assertEqual(data["b"], 2)

    def test_folded_block_scalar(self) -> None:
        data = parse_simple_yaml("note: >\n  line one\n  line two\nkey: v\n")
        # Native folded form keeps its single trailing newline.
        self.assertEqual(data["note"], "line one line two\n")
        self.assertEqual(data["key"], "v")

    def test_real_v2_config_parses(self) -> None:
        cfg = parse_simple_yaml(_V2_CONFIG)
        self.assertEqual(cfg["schema_version"], "frutlups_layout_config_v0")
        self.assertEqual(
            cfg["workspace_map"]["required_for_base_profile"][-1], "questions"
        )


class BuiltinProfileTests(unittest.TestCase):
    def test_default_is_v2(self) -> None:
        self.assertEqual(default_profile().profile_id, "artifact_first_template_v2")

    def test_v2_required_dirs_have_questions_not_pkg(self) -> None:
        prof = v2_default_profile()
        self.assertIn("questions", prof.required_directories)
        self.assertNotIn("08_pkg", prof.required_directories)
        self.assertTrue(prof.parse_front_matter)
        self.assertEqual(prof.state_file, "PROJECT_STATE.md")

    def test_legacy_required_dirs_match_history(self) -> None:
        prof = legacy_profile()
        self.assertIn("08_pkg", prof.required_directories)
        self.assertFalse(prof.parse_front_matter)
        self.assertEqual(prof.state_file, "")
        self.assertEqual(prof.active_roadmap_glob, "active_roadmap*.md")


class ProfileFromConfigTests(unittest.TestCase):
    def test_v2_base_detected(self) -> None:
        prof, diags = profile_from_config(parse_simple_yaml(_V2_CONFIG))
        self.assertEqual(prof.profile_id, "artifact_first_template_v2")
        self.assertTrue(prof.parse_front_matter)
        self.assertEqual(diags, ())

    def test_legacy_base_detected(self) -> None:
        cfg = parse_simple_yaml(
            "profile_id: artifact_first_template_legacy_root\n"
            "prompts:\n"
            "  required_coding_prompt_sections:\n"
            "    - Active Roadmap Item\n"
            "    - Required Self-Report\n"
        )
        prof, _ = profile_from_config(cfg)
        self.assertFalse(prof.parse_front_matter)
        self.assertEqual(prof.state_file, "")
        self.assertEqual(prof.self_report_section, "required self-report")

    def test_unsupported_schema_version_warns(self) -> None:
        cfg = parse_simple_yaml("schema_version: frutlups_layout_config_v999\n")
        _, diags = profile_from_config(cfg)
        codes = {d.code for d in diags}
        self.assertIn("unsupported_schema_version", codes)

    def test_unsafe_write_path_falls_back(self) -> None:
        cfg = parse_simple_yaml(
            "prompts:\n  coding_prompt_dir: \"../escape/coding\"\n"
        )
        prof, diags = profile_from_config(cfg)
        codes = {d.code for d in diags}
        self.assertIn("unsafe_write_path", codes)
        # Falls back to the safe default rather than the escaping path.
        self.assertEqual(prof.coding_prompt_dir, "prompts/for_coding_agent")
        self.assertTrue(
            any(d.severity == LayoutDiagnosticSeverity.ERROR for d in diags)
        )


class PathSafetyTests(unittest.TestCase):
    def test_rejects_absolute_and_escape(self) -> None:
        self.assertFalse(is_safe_relative("/etc/passwd"))
        self.assertFalse(is_safe_relative("C:\\Windows"))
        self.assertFalse(is_safe_relative("../outside"))
        self.assertFalse(is_safe_relative("a/../../b"))
        self.assertFalse(is_safe_relative(""))

    def test_accepts_repo_relative(self) -> None:
        self.assertTrue(is_safe_relative("prompts/for_coding_agent"))
        self.assertTrue(is_safe_relative("05_governance/reviews"))

    def test_resolve_under_root_raises_on_escape(self) -> None:
        with self.assertRaises(ValueError):
            resolve_under_root(Path("/tmp/root"), "../x")

    def test_resolve_under_root_ok(self) -> None:
        out = resolve_under_root(Path("/tmp/root"), "a/b")
        self.assertEqual(out, Path("/tmp/root") / "a" / "b")


class LoadLayoutProfileTests(unittest.TestCase):
    def test_project_config_precedence(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            loaded = load_layout_profile(root)
        self.assertEqual(loaded.source, ProfileSource.PROJECT_CONFIG)
        self.assertEqual(loaded.profile.profile_id, "artifact_first_template_v2")

    def test_explicit_config_precedence(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, with_config=False)
            cfg = root / "elsewhere.yaml"
            cfg.write_text(_V2_CONFIG, encoding="utf-8")
            loaded = load_layout_profile(root, config_path=cfg)
        self.assertEqual(loaded.source, ProfileSource.EXPLICIT_CONFIG)
        self.assertEqual(loaded.config_path, str(cfg))

    def test_v2_state_default_when_only_state(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, with_config=False, with_state=True)
            loaded = load_layout_profile(root)
        self.assertEqual(loaded.source, ProfileSource.V2_STATE_DEFAULT)
        self.assertEqual(loaded.profile.profile_id, "artifact_first_template_v2")

    def test_legacy_fallback_when_unmarked(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_legacy_project(root)
            loaded = load_layout_profile(root)
        self.assertEqual(loaded.source, ProfileSource.LEGACY_FALLBACK)
        self.assertEqual(loaded.profile.profile_id, "artifact_first_template_legacy_root")

    def test_unreadable_explicit_config_falls_back(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            loaded = load_layout_profile(root, config_path=root / "missing.yaml")
        self.assertEqual(loaded.source, ProfileSource.EXPLICIT_CONFIG)
        self.assertTrue(any(d.code == "config_unreadable" for d in loaded.diagnostics))
        self.assertEqual(loaded.profile.profile_id, "artifact_first_template_v2")


class V2DiscoveryTests(unittest.TestCase):
    def test_status_selects_v2_and_no_pkg_required(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            status = build_status(root)
        self.assertEqual(status.layout.profile.profile_id, "artifact_first_template_v2")
        # v2 does not require legacy 08_pkg.
        self.assertNotIn("08_pkg", status.missing_required_directories)
        self.assertEqual(status.missing_required_directories, ())
        self.assertTrue(status.ok)

    def test_v2_roadmap_glob_discovers_leading_wildcard_names(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            status = build_status(root)
        self.assertIsNotNone(status.active_roadmap)
        self.assertEqual(status.active_roadmap.name, "active_roadmap.md")

    def test_v2_prompt_inventory_under_configured_dirs(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            (root / "prompts" / "for_coding_agent" / "001_first_slice.md").write_text(
                _V2_CODING_PROMPT, encoding="utf-8"
            )
            status = build_status(root)
        self.assertEqual(status.prompts.coding_count, 1)

    def test_status_json_includes_layout_block(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            status = build_status(root)
        d = status.to_dict()
        self.assertIn("layout", d)
        self.assertEqual(d["layout"]["profile_id"], "artifact_first_template_v2")
        self.assertEqual(d["layout"]["source"], "project_config")

    def test_missing_state_file_diagnostic(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, with_state=False)
            status = build_status(root)
        codes = {d.code for d in status.diagnostics}
        self.assertIn("layout_state_file_missing", codes)

    def test_invalid_state_mode_diagnostic(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            (root / "PROJECT_STATE.md").write_text(
                "# Project State\n\nMemory mode:\n- bogus\n\nFrutlups mode:\n- manual\n",
                encoding="utf-8",
            )
            status = build_status(root)
        codes = {d.code for d in status.diagnostics}
        self.assertIn("layout_state_mode_invalid", codes)

    def test_missing_required_dir_diagnostic_for_v2(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            # Remove a required v2 dir.
            (root / "questions").rmdir()
            status = build_status(root)
        self.assertIn("questions", status.missing_required_directories)
        codes = {d.code for d in status.diagnostics}
        self.assertIn("layout_missing_required_directory", codes)


class CodingPromptMetaV2Tests(unittest.TestCase):
    def _artifact(self, root: Path, filename: str) -> PromptArtifact:
        path = root / "prompts" / "for_coding_agent" / filename
        return PromptArtifact(
            kind=PromptKind.CODING, path=path, filename=filename, sequence=1
        )

    def test_front_matter_and_sections(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "prompts" / "for_coding_agent").mkdir(parents=True)
            (root / "prompts" / "for_coding_agent" / "001_first_slice.md").write_text(
                _V2_CODING_PROMPT, encoding="utf-8"
            )
            meta = _parse_coding_prompt_meta(
                self._artifact(root, "001_first_slice.md"), root, v2_default_profile()
            )
        self.assertTrue(meta.valid, msg=meta.errors)
        self.assertEqual(meta.milestone_id, "M001")
        self.assertEqual(meta.slice_id, "M001-S01")
        self.assertIn("CLAUDE.md", meta.required_reading)
        self.assertEqual(meta.coding_prompt_path, "prompts/for_coding_agent/001_first_slice.md")

    def test_self_report_derived_from_template_reference(self) -> None:
        # The v2 prompt's Self-Report section only points at the template schema,
        # so the path is derived from reviews dir + slice slug + suffix.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "prompts" / "for_coding_agent").mkdir(parents=True)
            (root / "prompts" / "for_coding_agent" / "001_first_slice.md").write_text(
                _V2_CODING_PROMPT, encoding="utf-8"
            )
            meta = _parse_coding_prompt_meta(
                self._artifact(root, "001_first_slice.md"), root, v2_default_profile()
            )
        self.assertEqual(
            meta.self_report_path, "05_governance/reviews/m001_s01_self_report.md"
        )
        self.assertEqual(
            meta.review_output_path, "05_governance/reviews/m001_s01_review_report.md"
        )

    def test_explicit_backtick_path_preferred(self) -> None:
        prompt = _V2_CODING_PROMPT.replace(
            "Write a self-report using the canonical schema in\n"
            "`prompts/templates/self_report.md`.",
            "Write a self-report at `05_governance/reviews/custom_self_report.md`.",
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "prompts" / "for_coding_agent").mkdir(parents=True)
            (root / "prompts" / "for_coding_agent" / "001_first_slice.md").write_text(
                prompt, encoding="utf-8"
            )
            meta = _parse_coding_prompt_meta(
                self._artifact(root, "001_first_slice.md"), root, v2_default_profile()
            )
        self.assertEqual(
            meta.self_report_path, "05_governance/reviews/custom_self_report.md"
        )

    def test_seeded_v2_prompt_without_front_matter_surfaces_error(self) -> None:
        # A v2 coding prompt missing front matter should not be silently normalized.
        seeded = "# Template Scaffold\n\n## Current State\n\nRead state.\n\n## Task\n\nDo.\n"
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "prompts" / "for_coding_agent").mkdir(parents=True)
            (root / "prompts" / "for_coding_agent" / "001_seed.md").write_text(
                seeded, encoding="utf-8"
            )
            meta = _parse_coding_prompt_meta(
                self._artifact(root, "001_seed.md"), root, v2_default_profile()
            )
        self.assertFalse(meta.valid)
        self.assertTrue(any("milestone_id" in e for e in meta.errors))


class LegacyCompatTests(unittest.TestCase):
    def test_legacy_project_status_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_legacy_project(root)
            status = build_status(root)
        self.assertEqual(status.layout.source, ProfileSource.LEGACY_FALLBACK)
        # Legacy fallback emits no new layout diagnostics.
        self.assertFalse(any(d.code.startswith("layout_") for d in status.diagnostics))

    def test_explicit_legacy_config(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_legacy_project(root)
            cfg = root / "legacy.yaml"
            cfg.write_text(
                "profile_id: artifact_first_template_legacy_root\n"
                "prompts:\n"
                "  required_coding_prompt_sections:\n"
                "    - Active Roadmap Item\n",
                encoding="utf-8",
            )
            status = build_status(root, layout_config=cfg)
        self.assertEqual(status.layout.source, ProfileSource.EXPLICIT_CONFIG)
        self.assertEqual(
            status.layout.profile.profile_id, "artifact_first_template_legacy_root"
        )


class CliLayoutConfigTests(unittest.TestCase):
    def _run(self, args: list[str]) -> tuple[int, str]:
        out = StringIO()
        with redirect_stdout(out):
            code = main(args)
        return code, out.getvalue()

    def test_status_json_includes_layout(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            code, out = self._run(["status", str(root), "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertIn("layout", payload)
        self.assertEqual(payload["layout"]["profile_id"], "artifact_first_template_v2")

    def test_status_explicit_layout_config(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, with_config=False)
            cfg = root / "cfg.yaml"
            cfg.write_text(_V2_CONFIG, encoding="utf-8")
            code, out = self._run(
                ["status", str(root), "--layout-config", str(cfg), "--json"]
            )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["layout"]["source"], "explicit_config")


def _v2_config(
    *,
    template_root: str = ".",
    coding_dir: str = "prompts/for_coding_agent",
    review_dir: str = "prompts/for_review_agent",
) -> str:
    return (
        "schema_version: frutlups_layout_config_v0\n"
        "profile_id: artifact_first_template_v2\n"
        f'template_root: "{template_root}"\n'
        "workspace_map:\n"
        "  required_for_base_profile:\n"
        "    - 00_brief\n"
        "    - 03_experiments\n"
        "    - 05_governance\n"
        "    - prompts\n"
        "    - questions\n"
        "state:\n"
        '  canonical_file: "PROJECT_STATE.md"\n'
        "roadmaps:\n"
        '  directory: "03_experiments"\n'
        '  active_roadmap_glob: "*active_roadmap*.md"\n'
        '  development_roadmap_glob: "*development_roadmap*.md"\n'
        "prompts:\n"
        f'  coding_prompt_dir: "{coding_dir}"\n'
        f'  review_prompt_dir: "{review_dir}"\n'
        "  required_coding_prompt_sections:\n"
        "    - Current State\n"
        "    - Read First\n"
        "    - Task\n"
        "    - Self-Report\n"
        "reports:\n"
        '  reviews_dir: "05_governance/reviews"\n'
    )


def _coding_template() -> CodingPromptTemplate:
    return CodingPromptTemplate(
        sequence=1,
        milestone_id="M001",
        slice_id="M001-S01",
        slug="first_slice",
        title="First Slice",
        role_instructions="You are the coding agent.",
        required_reading=("CLAUDE.md", "README.md"),
        scope_paths=("08_pkg/",),
        non_goals=("Do not over-build.",),
        definition_of_done=("done",),
        verification_commands=("python -m unittest discover -s tests",),
        self_report_path="05_governance/reviews/m001_s01_first_self_report.md",
    )


def _review_template() -> ReviewPromptTemplate:
    return ReviewPromptTemplate(
        sequence=1,
        milestone_id="M001",
        slice_id="M001-S01",
        slug="first_slice",
        title="First Slice",
        role_instructions="You are the reviewer.",
        required_reading=("CLAUDE.md", "README.md"),
        coding_prompt_path="prompts/for_coding_agent/001_first_slice.md",
        self_report_path="05_governance/reviews/m001_s01_first_self_report.md",
        review_output_path="05_governance/reviews/m001_s01_first_review_report.md",
        expected_changed_files=("08_pkg/src/frutlups/x.py",),
        verification_commands=("python -m unittest discover -s tests",),
        severity_guidance=(
            "blocker: correctness",
            "major: incomplete",
            "minor: small gap",
            "nit: style",
        ),
        verdict_choices=("pass", "needs_work", "blocked", "override"),
        prior_review_paths=(),
        non_goals=("do not X",),
        notes=(),
    )


class WriteDirRoutingTests(unittest.TestCase):
    """Finding 1: configured prompt write directories must be honored end to end."""

    def test_write_coding_prompt_honors_custom_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = write_coding_prompt(
                CodingPromptWriteCommand(
                    project_root=root,
                    template=_coding_template(),
                    content="# coding prompt\n",
                    prompt_dir="custom_prompts/coding",
                )
            )
            wrote_custom = sorted((root / "custom_prompts" / "coding").glob("*.md"))
            legacy_dir_exists = (root / "prompts" / "for_coding_agent").exists()
        self.assertTrue(result.wrote, msg=result.errors)
        self.assertEqual(len(wrote_custom), 1)
        self.assertIn("custom_prompts/coding", result.preview.target_path)
        self.assertFalse(legacy_dir_exists)

    def test_write_review_prompt_honors_custom_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = write_review_prompt(
                ReviewPromptWriteCommand(
                    project_root=root,
                    template=_review_template(),
                    prompt_dir="custom_prompts/review",
                )
            )
            wrote_custom = sorted((root / "custom_prompts" / "review").glob("*.md"))
            legacy_dir_exists = (root / "prompts" / "for_review_agent").exists()
        self.assertTrue(result.wrote, msg=result.errors)
        self.assertEqual(len(wrote_custom), 1)
        self.assertIn("custom_prompts/review", result.preview.target_path)
        self.assertFalse(legacy_dir_exists)

    def test_write_coding_prompt_rejects_escape_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = write_coding_prompt(
                CodingPromptWriteCommand(
                    project_root=root,
                    template=_coding_template(),
                    content="# coding prompt\n",
                    prompt_dir="../escape",
                )
            )
        self.assertFalse(result.wrote)
        self.assertTrue(any("safe repo-relative" in e for e in result.errors))

    def test_legacy_default_dir_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = write_coding_prompt(
                CodingPromptWriteCommand(
                    project_root=root,
                    template=_coding_template(),
                    content="# coding prompt\n",
                )
            )
            legacy_count = len(sorted((root / "prompts" / "for_coding_agent").glob("*.md")))
        self.assertTrue(result.wrote, msg=result.errors)
        self.assertEqual(legacy_count, 1)

    def test_build_coding_prompt_plan_carries_configured_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, with_config=False)
            (root / "frutlups.layout.yaml").write_text(
                _v2_config(coding_dir="custom_prompts/coding"), encoding="utf-8"
            )
            plan = build_coding_prompt_plan(root)
        self.assertEqual(plan.coding_prompt_dir, "custom_prompts/coding")

    def test_cli_make_coding_prompt_writes_to_custom_dir(self) -> None:
        out = StringIO()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, with_config=False)
            (root / "frutlups.layout.yaml").write_text(
                _v2_config(
                    coding_dir="custom_prompts/coding",
                    review_dir="custom_prompts/review",
                ),
                encoding="utf-8",
            )
            with redirect_stdout(out):
                code = main(["make-coding-prompt", str(root)])
            wrote_custom = sorted((root / "custom_prompts" / "coding").glob("*.md"))
            # The v2 fixture pre-creates the legacy dir; assert no prompt landed there.
            legacy_md = sorted((root / "prompts" / "for_coding_agent").glob("*.md"))
        self.assertEqual(code, 0, msg=out.getvalue())
        self.assertEqual(len(wrote_custom), 1)
        self.assertEqual(legacy_md, [])


class TemplateRootTests(unittest.TestCase):
    """Finding 2: ``template_root`` must select the effective template root."""

    def test_template_root_dot_normal(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, with_config=False)
            (root / "frutlups.layout.yaml").write_text(
                _v2_config(template_root="."), encoding="utf-8"
            )
            status = build_status(root)
        self.assertEqual(status.root.resolve(), root.resolve())
        self.assertEqual(status.missing_required_directories, ())

    def test_template_root_child_nested(self) -> None:
        with TemporaryDirectory() as tmp:
            outer = Path(tmp)
            (outer / "frutlups.layout.yaml").write_text(
                _v2_config(template_root="child"), encoding="utf-8"
            )
            child = outer / "child"
            _make_v2_project(child, with_config=False)
            status = build_status(outer)
        # Effective root is outer/child; discovery happens there.
        self.assertEqual(status.root.resolve(), (outer / "child").resolve())
        self.assertEqual(status.missing_required_directories, ())
        self.assertIsNotNone(status.active_roadmap)
        self.assertEqual(status.active_roadmap.parent.resolve(), (child / "03_experiments").resolve())
        # The original config path is still reported in the layout block.
        self.assertEqual(
            Path(status.layout.config_path).resolve(),
            (outer / "frutlups.layout.yaml").resolve(),
        )

    def test_template_root_unsafe_diagnostic(self) -> None:
        with TemporaryDirectory() as tmp:
            outer = Path(tmp)
            (outer / "frutlups.layout.yaml").write_text(
                _v2_config(template_root="../escape"), encoding="utf-8"
            )
            _make_v2_project(outer, with_config=False)
            status = build_status(outer)
        codes = {d.code for d in status.diagnostics}
        self.assertIn("layout_unsafe_template_root", codes)
        # Falls back to the config directory as the effective root.
        self.assertEqual(status.root.resolve(), outer.resolve())


# M017-S02: v2 semantic roles, report headings, policies, advisory diagnostics.

_SEMANTIC_CONFIG = """\
schema_version: frutlups_layout_config_v0
profile_id: artifact_first_template_v2
profile_status: proposed
template_root: "."
compatibility:
  frutlups_source: null
  guide: "docs/template_framework/frutlups_driver_boundary.md"
prompts:
  coding_prompt_dir: "prompts/for_coding_agent"
  review_prompt_dir: "prompts/for_review_agent"
  required_coding_prompt_sections:
    - Read First
    - Self-Report
  section_roles:
    required_reading: "Sources"
    self_report: "Report"
    non_goals: "Out Of Scope"
    task: "Work"
    verification: "Checks"
  metadata:
    parse_front_matter: true
    milestone_field: "ms"
    slice_field: "sl"
    title_field: "name"
reports:
  reviews_dir: "05_governance/reviews"
  self_report_required_headings:
    - "Intent"
    - "Recommended Next Move"
automation_boundary:
  runner_implemented: false
  must_stop_on:
    - "blocked"
    - "no frontier"
git_policy:
  default: "architect-reviewer commits accepted milestones"
  runner_may_commit: false
  runner_may_report_commit_ready: true
pull_request_policy:
  default: "human-controlled"
  runner_may_open_pull_request: false
  runner_may_report_pull_request_ready: true
validation:
  command: "python -m unittest discover -s tests"
  command_in_redesign_repo_from_root: "python -m unittest discover -s 08_new_template/tests"
optional_lanes:
  llloom:
    install_source: null
"""


class V2SemanticRolesTests(unittest.TestCase):
    def test_section_roles_and_metadata_parsed(self) -> None:
        prof, diags = profile_from_config(parse_simple_yaml(_SEMANTIC_CONFIG))
        self.assertEqual(prof.required_reading_section, "sources")
        self.assertEqual(prof.self_report_section, "report")
        self.assertEqual(prof.non_goals_section, "out of scope")
        self.assertEqual(prof.task_section, "work")
        self.assertEqual(prof.verification_section, "checks")
        self.assertEqual(prof.front_matter_milestone_field, "ms")
        self.assertEqual(prof.front_matter_slice_field, "sl")
        self.assertEqual(prof.front_matter_title_field, "name")
        self.assertTrue(prof.parse_front_matter)
        self.assertEqual(diags, ())

    def test_absent_roles_fall_back_to_v2_defaults(self) -> None:
        # Older config without section_roles/metadata keeps v2 base behavior.
        prof, _ = profile_from_config(parse_simple_yaml(_V2_CONFIG))
        self.assertEqual(prof.required_reading_section, "read first")
        self.assertEqual(prof.self_report_section, "self-report")
        self.assertEqual(prof.front_matter_milestone_field, "milestone")
        self.assertTrue(prof.parse_front_matter)

    def test_configured_front_matter_field_used_by_parser(self) -> None:
        prof, _ = profile_from_config(parse_simple_yaml(_SEMANTIC_CONFIG))
        prompt = (
            "---\nms: M005\nsl: M005-S02\nname: custom slice\n---\n\n"
            "# Coding Prompt\n\n## Sources\n\n- `CLAUDE.md`\n\n## Report\n\n"
            "Write a self-report at `05_governance/reviews/m005_s02_x_self_report.md`.\n"
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "prompts" / "for_coding_agent").mkdir(parents=True)
            (root / "prompts" / "for_coding_agent" / "001_x.md").write_text(prompt, encoding="utf-8")
            artifact = PromptArtifact(
                kind=PromptKind.CODING,
                path=root / "prompts" / "for_coding_agent" / "001_x.md",
                filename="001_x.md",
                sequence=1,
            )
            meta = _parse_coding_prompt_meta(artifact, root, prof)
        self.assertTrue(meta.valid, msg=meta.errors)
        self.assertEqual(meta.milestone_id, "M005")
        self.assertEqual(meta.slice_id, "M005-S02")
        self.assertEqual(meta.title, "custom slice")
        self.assertIn("CLAUDE.md", meta.required_reading)


class ReportHeadingsAndPolicyTests(unittest.TestCase):
    def test_self_report_required_headings_parsed(self) -> None:
        prof, _ = profile_from_config(parse_simple_yaml(_SEMANTIC_CONFIG))
        self.assertEqual(
            prof.self_report_required_headings, ("Intent", "Recommended Next Move")
        )

    def test_policies_parsed(self) -> None:
        prof, _ = profile_from_config(parse_simple_yaml(_SEMANTIC_CONFIG))
        self.assertFalse(prof.git_policy.runner_may_commit)
        self.assertTrue(prof.git_policy.runner_may_report_commit_ready)
        self.assertFalse(prof.pull_request_policy.runner_may_open_pull_request)
        self.assertTrue(prof.pull_request_policy.runner_may_report_pull_request_ready)
        self.assertIn("blocked", prof.automation_boundary.must_stop_on)

    def test_safe_policy_defaults_when_absent(self) -> None:
        # A config with no policy blocks gets safe defaults (no runner commit/PR).
        prof, _ = profile_from_config(parse_simple_yaml(_V2_CONFIG))
        self.assertFalse(prof.git_policy.runner_may_commit)
        self.assertFalse(prof.pull_request_policy.runner_may_open_pull_request)

    def test_profile_status_and_redesign_command(self) -> None:
        prof, _ = profile_from_config(parse_simple_yaml(_SEMANTIC_CONFIG))
        self.assertEqual(prof.profile_status, "proposed")
        self.assertEqual(prof.validation_command, "python -m unittest discover -s tests")
        self.assertEqual(
            prof.validation_command_redesign,
            "python -m unittest discover -s 08_new_template/tests",
        )

    def test_profile_to_dict_is_json_safe_and_complete(self) -> None:
        prof, _ = profile_from_config(parse_simple_yaml(_SEMANTIC_CONFIG))
        d = prof.to_dict()
        json.dumps(d)
        for key in (
            "profile_status",
            "self_report_required_headings",
            "git_policy",
            "pull_request_policy",
            "automation_boundary",
            "validation_command_redesign",
            "front_matter_milestone_field",
        ):
            self.assertIn(key, d)
        self.assertIn("runner_may_commit", d["git_policy"])


class AdvisoryDiagnosticsTests(unittest.TestCase):
    def test_null_advisory_fields_no_diagnostic(self) -> None:
        _, diags = profile_from_config(parse_simple_yaml(_SEMANTIC_CONFIG))
        self.assertEqual(diags, ())

    def test_relative_guide_no_diagnostic(self) -> None:
        cfg = parse_simple_yaml(
            'compatibility:\n  guide: "docs/template_framework/frutlups_driver_boundary.md"\n'
        )
        _, diags = profile_from_config(cfg)
        self.assertEqual([d for d in diags if d.code == "advisory_machine_local_path"], [])

    def test_machine_local_advisory_path_is_non_blocking_info(self) -> None:
        cfg = parse_simple_yaml(
            'compatibility:\n'
            '  frutlups_source: "C:\\\\Users\\\\dev\\\\work\\\\frutlups-dev"\n'
            'optional_lanes:\n'
            '  llloom:\n'
            '    install_source: "C:\\\\Users\\\\dev\\\\work\\\\llloom"\n'
        )
        _, diags = profile_from_config(cfg)
        advisory = [d for d in diags if d.code == "advisory_machine_local_path"]
        self.assertEqual(len(advisory), 2)
        # Non-blocking: INFO severity, never ERROR.
        self.assertTrue(all(d.severity == LayoutDiagnosticSeverity.INFO for d in advisory))

    def test_status_json_surfaces_profile_policies(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, with_config=False)
            (root / "frutlups.layout.yaml").write_text(_SEMANTIC_CONFIG, encoding="utf-8")
            status = build_status(root)
        d = status.to_dict()
        self.assertIn("profile", d["layout"])
        prof = d["layout"]["profile"]
        self.assertIn("git_policy", prof)
        self.assertFalse(prof["git_policy"]["runner_may_commit"])
        self.assertFalse(prof["pull_request_policy"]["runner_may_open_pull_request"])
        self.assertEqual(prof["profile_status"], "proposed")
        self.assertEqual(len(prof["self_report_required_headings"]), 2)


_LOCAL_V2_EXAMPLE = (
    Path(__file__).resolve().parents[2] / "docs" / "config_files" / "v2" / "frutlups.layout.yaml"
)


class LocalV2ConfigExampleTests(unittest.TestCase):
    """M017-S02-C01: the repo-local v2 example must be portable (no machine-local
    advisory paths) and keep the full v2 semantic/policy contract."""

    def setUp(self) -> None:
        if not _LOCAL_V2_EXAMPLE.is_file():
            self.skipTest("local v2 config example not found")
        from frutlups.layout import load_config_file

        self.profile, self.diagnostics = profile_from_config(load_config_file(_LOCAL_V2_EXAMPLE))

    def test_no_machine_local_advisory_diagnostics(self) -> None:
        advisory = [d for d in self.diagnostics if d.code == "advisory_machine_local_path"]
        self.assertEqual(advisory, [])

    def test_runner_safe_policy_defaults_preserved(self) -> None:
        self.assertFalse(self.profile.git_policy.runner_may_commit)
        self.assertFalse(self.profile.pull_request_policy.runner_may_open_pull_request)

    def test_self_report_headings_contract_preserved(self) -> None:
        self.assertEqual(len(self.profile.self_report_required_headings), 11)
        self.assertEqual(self.profile.self_report_required_headings[0], "Intent")
        self.assertEqual(self.profile.self_report_required_headings[-1], "Recommended Next Move")

    def test_redesign_command_surfaced_as_advisory(self) -> None:
        self.assertEqual(
            self.profile.validation_command_redesign,
            "python -m unittest discover -s 08_new_template/tests",
        )
        self.assertEqual(self.profile.validation_command, "python -m unittest discover -s tests")

    def test_semantic_fields_still_present(self) -> None:
        self.assertEqual(self.profile.required_reading_section, "read first")
        self.assertEqual(self.profile.self_report_section, "self-report")
        self.assertTrue(self.profile.parse_front_matter)
        self.assertEqual(self.profile.front_matter_milestone_field, "milestone")


@unittest.skipUnless(EXTERNAL_V2.is_dir(), "external v2 template not available")
class ExternalV2IntegrationTests(unittest.TestCase):
    def test_status_discovers_external_v2_layout(self) -> None:
        cfg = EXTERNAL_V2 / "frutlups.layout.yaml"
        status = build_status(EXTERNAL_V2, layout_config=cfg)
        self.assertEqual(status.layout.profile.profile_id, "artifact_first_template_v2")
        # The v2 scaffold satisfies its own base-profile required directories.
        self.assertEqual(status.missing_required_directories, ())
        # No machine-local-path diagnostics for the current (cleaned-up) v2 YAML.
        advisory = [
            d for d in status.layout.diagnostics if d.code == "advisory_machine_local_path"
        ]
        self.assertEqual(advisory, [])

    def test_external_v2_policies_are_runner_safe(self) -> None:
        cfg = EXTERNAL_V2 / "frutlups.layout.yaml"
        prof = build_status(EXTERNAL_V2, layout_config=cfg).layout.profile
        self.assertFalse(prof.git_policy.runner_may_commit)
        self.assertFalse(prof.pull_request_policy.runner_may_open_pull_request)
        self.assertEqual(len(prof.self_report_required_headings), 11)
        self.assertEqual(prof.profile_status, "proposed")

    def test_external_v2_scaffold_has_no_frutlups_import(self) -> None:
        # The scaffold's own tests must not import frutlups (optional-lane rule).
        scaffold_test = EXTERNAL_V2 / "tests" / "test_template_scaffold.py"
        text = scaffold_test.read_text(encoding="utf-8")
        self.assertNotIn("import frutlups", text)


# ---------------------------------------------------------------------------
# M002-S03/S05: the production layout loader and the ``parse_simple_yaml``
# compatibility wrapper both ride the one bounded YAML boundary. The checks
# below prove wrapper/file-loader parity and that neither path can reach a
# custom parser.
# ---------------------------------------------------------------------------

# The accepted template-v3 layout ships as an immutable, package-relative fixture so
# the wrapper/loader parity below runs from the flattened front-facing checkout without
# reading a root layout above ``tests/``. See ``fixtures/front_repo_contract/manifest.json``.
_TARGET_V3_CONFIG = (
    Path(__file__).resolve().parent / "fixtures" / "front_repo_contract" / "frutlups.layout.yaml"
)

_LEGACY_CONFIG = """\
schema_version: frutlups_layout_config_v0
profile_id: artifact_first_template_legacy_root
template_root: "."
prompts:
  coding_prompt_dir: "prompts/for_coding_agent"
  review_prompt_dir: "prompts/for_review_agent"
  required_coding_prompt_sections:
    - "Active Roadmap Item"
    - "Required Self-Report"
    - "Required Reading"
    - "Non-Goals"
  required_review_prompt_sections:
    - "Review Objective"
    - "Review Checks"
    - "Verdict"
reports:
  reviews_dir: "05_governance/reviews"
"""

_DIAGNOSTIC_CONFIG = """\
schema_version: frutlups_layout_config_v999
prompts:
  coding_prompt_dir: "../escape/coding"
"""


def _write_config(root: Path, text: str, name: str = "frutlups.layout.yaml") -> Path:
    path = root / name
    path.write_text(text, encoding="utf-8")
    return path


def _wrapper_profile(path: Path) -> tuple[object, list[dict[str, str]]]:
    """The public wrapper path: compatibility wrapper + mapping logic."""

    profile, diags = profile_from_config(parse_simple_yaml(path.read_text(encoding="utf-8")))
    return profile, [d.to_dict() for d in diags]


def _production_profile(path: Path) -> tuple[object, list[dict[str, str]]]:
    """The production file-loader path: bounded boundary + schema + mapping."""

    profile, diags = profile_from_config(load_config_file(path))
    return profile, [d.to_dict() for d in diags]


class ProfileEquivalenceTests(unittest.TestCase):
    """S03/S05 acceptance unit: identical final profiles and diagnostics.

    After S05 both paths share the one bounded boundary and layout schema, so
    this is a wrapper/file-loader parity proof over the accepted template-v3,
    v2, and legacy configurations, keeping the S03 equivalence rows alive.
    """

    def _assert_equivalent(self, path: Path) -> None:
        wrapper_profile, wrapper_diags = _wrapper_profile(path)
        new_profile, new_diags = _production_profile(path)
        self.assertEqual(wrapper_profile.to_dict(), new_profile.to_dict())
        self.assertEqual(wrapper_diags, new_diags)

    @unittest.skipUnless(_TARGET_V3_CONFIG.is_file(), "shipped target config not present")
    def test_template_v3_target_config_equivalent(self) -> None:
        self._assert_equivalent(_TARGET_V3_CONFIG)

    def test_v2_fixture_config_equivalent(self) -> None:
        with TemporaryDirectory() as tmp:
            self._assert_equivalent(_write_config(Path(tmp), _V2_CONFIG))

    def test_legacy_config_equivalent(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _write_config(Path(tmp), _LEGACY_CONFIG)
            self._assert_equivalent(path)
            profile, _ = _production_profile(path)
        self.assertEqual(profile.profile_id, "artifact_first_template_legacy_root")
        self.assertFalse(profile.parse_front_matter)

    def test_diagnostics_bearing_config_equivalent(self) -> None:
        # A config that produces both a warning and an error diagnostic must
        # produce the same diagnostics on both paths.
        with TemporaryDirectory() as tmp:
            self._assert_equivalent(_write_config(Path(tmp), _DIAGNOSTIC_CONFIG))

    def test_semantic_config_fixture_equivalent(self) -> None:
        with TemporaryDirectory() as tmp:
            self._assert_equivalent(_write_config(Path(tmp), _SEMANTIC_CONFIG))


class ProductionRoutingTests(unittest.TestCase):
    """The production loader uses the private boundary, never the wrapper."""

    def test_load_config_file_calls_private_path_boundary(self) -> None:
        refusal = YamlBoundaryError(YamlFailure.INVALID_YAML, "yaml boundary refused: invalid_yaml")
        with TemporaryDirectory() as tmp:
            cfg = _write_config(Path(tmp), _V2_CONFIG)
            with mock.patch("frutlups.layout.load_yaml_path", side_effect=refusal) as boundary:
                with self.assertRaises(LayoutConfigError) as caught:
                    load_config_file(cfg)
        boundary.assert_called_once_with(cfg)
        self.assertIn("invalid_yaml", str(caught.exception))

    def test_parse_simple_yaml_is_not_a_fallback(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            with mock.patch(
                "frutlups.layout.parse_simple_yaml",
                side_effect=AssertionError("wrapper reached from production path"),
            ):
                config = load_config_file(root / "frutlups.layout.yaml")
                loaded = load_layout_profile(root)
        self.assertEqual(config["profile_id"], "artifact_first_template_v2")
        self.assertEqual(loaded.source, ProfileSource.PROJECT_CONFIG)
        self.assertEqual(loaded.profile.profile_id, "artifact_first_template_v2")


class LayoutSchemaAcceptanceTests(unittest.TestCase):
    """Historical accepted shapes and unknown block-form fields stay readable."""

    def test_unknown_block_form_fields_accepted_and_ignored(self) -> None:
        text = (
            "schema_version: frutlups_layout_config_v0\n"
            "profile_id: artifact_first_template_v2\n"
            "unknown_top: readable\n"
            "purpose:  # a comment line\n"
            "  summary: >\n"
            "    folded text kept as one block scalar\n"
            "  nested_unknown:\n"
            "    flag: true\n"
            "    other_flag: false\n"
            "    nothing: null\n"
            "    tilde: ~\n"
            "    items:\n"
            "      - plain\n"
            "      - 'single quoted'\n"
            '      - "double quoted"\n'
            "compatibility:\n"
            "  frutlups_source: null\n"
        )
        with TemporaryDirectory() as tmp:
            config = load_config_file(_write_config(Path(tmp), text))
        self.assertEqual(config["unknown_top"], "readable")
        self.assertIs(config["purpose"]["nested_unknown"]["flag"], True)
        self.assertIs(config["purpose"]["nested_unknown"]["other_flag"], False)
        self.assertIsNone(config["purpose"]["nested_unknown"]["nothing"])
        self.assertEqual(
            config["purpose"]["nested_unknown"]["items"],
            ["plain", "single quoted", "double quoted"],
        )
        profile, diags = profile_from_config(config)
        self.assertEqual(profile.profile_id, "artifact_first_template_v2")
        self.assertEqual([d for d in diags if d.severity == LayoutDiagnosticSeverity.ERROR], [])

    def test_quoted_numeric_and_boolean_keys_are_strings(self) -> None:
        text = '"1": "one"\n"true": "yes"\n'
        with TemporaryDirectory() as tmp:
            config = load_config_file(_write_config(Path(tmp), text))
        self.assertEqual(config, {"1": "one", "true": "yes"})

    def test_literal_block_scalar_accepted(self) -> None:
        text = "note: |\n  line one\n  line two\nkey: v\n"
        with TemporaryDirectory() as tmp:
            config = load_config_file(_write_config(Path(tmp), text))
        self.assertEqual(config["note"], "line one\nline two\n")
        self.assertEqual(config["key"], "v")


class LayoutSchemaRefusalTests(unittest.TestCase):
    """Fail-closed on every invalid or unapproved shape, with safe messages."""

    def _assert_refused(self, text: str, marker: str) -> str:
        with TemporaryDirectory() as tmp:
            cfg = _write_config(Path(tmp), text)
            with self.assertRaises(LayoutConfigError) as caught:
                load_config_file(cfg)
        message = str(caught.exception)
        self.assertIn(marker, message)
        self.assertNotIn(str(cfg), message)
        self.assertLessEqual(len(message), 240)
        self.assertIsNone(caught.exception.__cause__)
        return message

    def test_anchor_and_alias_refused(self) -> None:
        self._assert_refused("a: &x 1\nb: *x\n", "anchors and aliases are not approved")

    def test_merge_key_refused(self) -> None:
        text = "base: &b\n  k: v\nmerged:\n  <<: *b\n"
        self._assert_refused(text, "merge keys are not approved")

    def test_explicit_tag_refused(self) -> None:
        self._assert_refused("a: !!str 1\n", "explicit tags are not approved")

    def test_flow_sequence_refused(self) -> None:
        self._assert_refused('a: ["x", "y"]\n', "flow collections are not approved")

    def test_flow_mapping_refused(self) -> None:
        self._assert_refused("a: {b: c}\n", "flow collections are not approved")

    def test_sequence_root_refused(self) -> None:
        self._assert_refused("- a\n- b\n", "root must be exactly one mapping")

    def test_scalar_root_refused(self) -> None:
        self._assert_refused("just a scalar\n", "root must be exactly one mapping")

    def test_empty_document_refused(self) -> None:
        self._assert_refused("# only a comment\n", "root must be exactly one mapping")

    def test_non_string_root_key_refused(self) -> None:
        self._assert_refused("1: x\n", "mapping keys must be strings")

    def test_non_string_nested_key_refused(self) -> None:
        self._assert_refused("a:\n  true: x\n", "mapping keys must be strings")

    def test_non_string_key_in_sequence_mapping_refused(self) -> None:
        self._assert_refused("a:\n  - 1: x\n", "mapping keys must be strings")

    def test_complex_key_refused(self) -> None:
        # An unhashable complex key fails during bounded construction; the
        # refusal is still a bounded LayoutConfigError, never a traceback.
        with TemporaryDirectory() as tmp:
            cfg = _write_config(Path(tmp), "? [a, b]\n: v\n")
            with self.assertRaises(LayoutConfigError) as caught:
                load_config_file(cfg)
        self.assertLessEqual(len(str(caught.exception)), 240)

    def test_multiple_documents_refused(self) -> None:
        self._assert_refused("a: 1\n---\nb: 2\n", "multiple_documents")

    def test_malformed_yaml_refused(self) -> None:
        self._assert_refused('a: "unterminated\n', "invalid_yaml")

    def test_truncated_yaml_refused(self) -> None:
        self._assert_refused("a:\n  b: [1, 2\n", "invalid_yaml")

    def test_invalid_utf8_refused(self) -> None:
        with TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "frutlups.layout.yaml"
            cfg.write_bytes(b"a: \xff\xfe invalid\n")
            with self.assertRaises(LayoutConfigError) as caught:
                load_config_file(cfg)
        self.assertIn("invalid_utf8", str(caught.exception))

    def test_plain_duplicate_keys_refused(self) -> None:
        self._assert_refused("a: 1\na: 2\n", "duplicate_key")

    def test_semantic_duplicate_spellings_refused_before_collapse(self) -> None:
        # ``1`` and ``01`` resolve to the same integer key; the bounded
        # boundary refuses them as duplicates before the layout schema sees a
        # collapsed mapping.
        self._assert_refused("1: a\n01: b\n", "duplicate_key")

    def test_max_bytes_plus_one_refused(self) -> None:
        with TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "frutlups.layout.yaml"
            cfg.write_bytes(b"a: " + b"x" * 65_534 + b"\n")
            with self.assertRaises(LayoutConfigError) as caught:
                load_config_file(cfg)
        self.assertIn("input_too_large", str(caught.exception))

    def test_max_lines_plus_one_refused(self) -> None:
        with TemporaryDirectory() as tmp:
            cfg = _write_config(Path(tmp), "k: v\n" + "# pad\n" * 500)
            with self.assertRaises(LayoutConfigError) as caught:
                load_config_file(cfg)
        self.assertIn("too_many_lines", str(caught.exception))


class HostileDiagnosticTests(unittest.TestCase):
    """Refusals stay bounded and never echo hostile content or local paths."""

    HOSTILE = "X43Q_HOSTILE <script> C:\\secret\\path"

    def test_invalid_yaml_diagnostic_echoes_nothing_hostile(self) -> None:
        with TemporaryDirectory() as tmp:
            cfg = _write_config(Path(tmp), f'"{self.HOSTILE}": "ok"\nbad: "unterminated\n')
            before = sorted(p.name for p in Path(tmp).iterdir())
            with self.assertRaises(LayoutConfigError) as caught:
                load_config_file(cfg)
            after = sorted(p.name for p in Path(tmp).iterdir())
        message = str(caught.exception)
        self.assertNotIn(self.HOSTILE, message)
        self.assertNotIn(str(cfg), message)
        self.assertNotIn("Traceback", message)
        self.assertLessEqual(len(message), 240)
        self.assertEqual(before, after)

    def test_schema_refusal_echoes_no_hostile_key_text(self) -> None:
        with TemporaryDirectory() as tmp:
            cfg = _write_config(Path(tmp), f'"{self.HOSTILE}": 1\ntrue: x\n')
            with self.assertRaises(LayoutConfigError) as caught:
                load_config_file(cfg)
        message = str(caught.exception)
        self.assertNotIn(self.HOSTILE, message)
        self.assertNotIn(str(cfg), message)

    def test_load_layout_profile_invalid_explicit_config_falls_back_safely(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, with_config=False)
            cfg = _write_config(root, f'"{self.HOSTILE}": "ok"\nbad: "unterminated\n', "evil.yaml")
            loaded = load_layout_profile(root, config_path=cfg)
        self.assertEqual(loaded.source, ProfileSource.EXPLICIT_CONFIG)
        self.assertEqual(loaded.profile.profile_id, "artifact_first_template_v2")
        errors = [d for d in loaded.diagnostics if d.code == "config_unreadable"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].severity, LayoutDiagnosticSeverity.ERROR)
        self.assertNotIn(self.HOSTILE, errors[0].message)
        self.assertNotIn(str(cfg), errors[0].message)
        self.assertNotIn("Traceback", errors[0].message)


class InvalidConfigFallbackTests(unittest.TestCase):
    """Discovery precedence and fallback survive the new loader (N1/N2/N4)."""

    def test_invalid_project_config_falls_back_to_v2_state_default(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            (root / "frutlups.layout.yaml").write_text('a: "unterminated\n', encoding="utf-8")
            loaded = load_layout_profile(root)
        self.assertEqual(loaded.source, ProfileSource.V2_STATE_DEFAULT)
        self.assertEqual(loaded.profile.profile_id, "artifact_first_template_v2")
        errors = [d for d in loaded.diagnostics if d.code == "config_unreadable"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].severity, LayoutDiagnosticSeverity.ERROR)

    def test_invalid_project_config_without_state_falls_back_to_legacy(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_legacy_project(root)
            (root / "frutlups.layout.yaml").write_text("- not\n- a\n- mapping\n", encoding="utf-8")
            loaded = load_layout_profile(root)
        self.assertEqual(loaded.source, ProfileSource.LEGACY_FALLBACK)
        self.assertEqual(loaded.profile.profile_id, "artifact_first_template_legacy_root")
        self.assertTrue(any(d.code == "config_unreadable" for d in loaded.diagnostics))

    def test_malformed_explicit_config_keeps_explicit_source_and_default(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = _write_config(root, "a: &x 1\nb: *x\n", "bad.yaml")
            loaded = load_layout_profile(root, config_path=cfg)
        self.assertEqual(loaded.source, ProfileSource.EXPLICIT_CONFIG)
        self.assertEqual(loaded.profile.profile_id, "artifact_first_template_v2")
        self.assertTrue(any(d.code == "config_unreadable" for d in loaded.diagnostics))


class RepeatedReadPurityTests(unittest.TestCase):
    """Repeated reads mutate nothing: input, directory, loader tables, limits."""

    def test_repeated_reads_leave_everything_unchanged(self) -> None:
        constructors_before = dict(yaml.SafeLoader.yaml_constructors)
        multi_before = dict(yaml.SafeLoader.yaml_multi_constructors)
        recursion_before = sys.getrecursionlimit()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = _write_config(root, _V2_CONFIG)
            bytes_before = cfg.read_bytes()
            dir_before = sorted(p.name for p in root.iterdir())
            first = load_config_file(cfg)
            second = load_config_file(cfg)
            self.assertEqual(first, second)
            self.assertEqual(cfg.read_bytes(), bytes_before)
            self.assertEqual(sorted(p.name for p in root.iterdir()), dir_before)
        self.assertEqual(dict(yaml.SafeLoader.yaml_constructors), constructors_before)
        self.assertEqual(dict(yaml.SafeLoader.yaml_multi_constructors), multi_before)
        self.assertEqual(sys.getrecursionlimit(), recursion_before)


class UnchangedPublicSurfaceTests(unittest.TestCase):
    """Exports, CLI verb inventory, JSON shapes, and read-only no-write."""

    def test_public_exports_unchanged(self) -> None:
        self.assertTrue(callable(frutlups.parse_simple_yaml))
        self.assertTrue(callable(frutlups.load_layout_profile))
        for name in ("load_yaml_path", "load_yaml_bytes", "YamlDocument", "YamlBoundaryError"):
            self.assertNotIn(name, frutlups.__all__)
            self.assertFalse(hasattr(frutlups, name), name)

    def test_cli_verb_inventory_unchanged(self) -> None:
        from frutlups.cli import _build_parser

        parser = _build_parser()
        subparsers = next(
            action
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        self.assertEqual(
            sorted(subparsers.choices),
            [
                "make-coding-prompt",
                "make-review-prompt",
                "next",
                "orchestrator-handoff",
                "orchestrator-plan",
                "orchestrator-run",
                "record-verdict",
                "status",
            ],
        )

    def _run(self, args: list[str]) -> tuple[int, str]:
        out = StringIO()
        with redirect_stdout(out):
            code = main(args)
        return code, out.getvalue()

    def test_status_next_orchestrator_plan_json_shapes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            code, out = self._run(["status", str(root), "--json"])
            self.assertEqual(code, 0)
            status_payload = json.loads(out)
            self.assertEqual(
                status_payload["layout"]["profile_id"], "artifact_first_template_v2"
            )
            self.assertIn("loop_resume", status_payload)

            code, out = self._run(["next", str(root), "--json"])
            self.assertEqual(code, 0)
            self.assertIsInstance(json.loads(out), dict)

            code, out = self._run(["orchestrator-plan", str(root), "--json"])
            self.assertEqual(code, 0)
            plan_payload = json.loads(out)
            self.assertIn("resume", plan_payload)
            self.assertIn("human_gate", plan_payload)

    def test_read_only_status_writes_nothing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
            code, _ = self._run(["status", str(root), "--json"])
            after = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
        self.assertEqual(code, 0)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
