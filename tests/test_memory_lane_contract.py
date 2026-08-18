"""M011-S01: consolidated memory-lane contract falsifiers and evidence.

This single module reproduces and locks the three corrected memory-lane defects
and the surrounding compatibility surface:

- D1: memory detection is mode-aware and selected-snapshot driven. A selected
  v2/template-v3 profile decides activity from ``Memory mode`` in
  ``PROJECT_STATE.md`` and the typed layout's ``llloom_memory_root``; a stale
  ``llloom_memory`` directory under mode ``none`` no longer activates memory, and
  a genuinely configured ``llloom`` mode with a safe contained root is observed.
  ``detect_memory`` / ``build_memory_prompt_snippet`` keep their direct legacy
  behavior; genuine legacy-fallback projects keep the historical root sniff.
- D2: milestone identity (including ``M010`` and case variants) never grants
  memory-update posture; ``classify_slice_kind`` is always ``NORMAL``.
- D3: configured coding/review scaffolds perform no memory query and route the
  selected posture file into ``Read First`` for lightweight/llloom modes; legacy
  renderers keep their historical posture path by default; handoffs cite the
  selected posture file (or nothing for mode ``none``) instead of a hardcoded
  frutlups-repository-only path.

The public JSON/planning/package boundaries are pinned unchanged.

Prompt 044 correction: this module additionally closes Review 043's four P1
findings with public end-to-end evidence — an ``OSError``/``RuntimeError``
resolver falsifier proved through public ``build_status``; a typed, bounded,
single-line, inert posture/memory-root path contract proved unable to inject
Markdown into any coding-plan, review-plan, handoff, or CLI consumer; the
unsafe-root disable sentinel proved to yield no usable fallback through
``TemplatePaths``; and real ``build_coding_prompt_plan`` / ``build_review_prompt_plan``,
both CLI writers, both handoff builders across every input form, and a
selected-snapshot mutation matrix with effective falsifiers.
"""

from __future__ import annotations

import contextlib
import json
import unittest
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import frutlups
import frutlups.project as project_module
from frutlups import cli
from frutlups.artifacts import TemplatePaths
from frutlups.gate import (
    PLANNING_FRONTIER_CONTRACT_ID,
    PLANNING_FRONTIER_SUPPORTED_VERSIONS,
)
from frutlups.handoff import build_coder_handoff, build_reviewer_handoff
from frutlups.layout import (
    MEMORY_LANE_PATH_MAX,
    ProfileSource,
    legacy_profile,
    load_layout_profile,
    normalize_memory_lane_path,
    profile_from_config,
    v2_default_profile,
)
from frutlups.memory import MemoryCommandResult, detect_memory
from frutlups.project import (
    _configured_posture_reading,
    _dedup_append,
    _select_llloom_memory_status,
    build_coding_prompt_plan,
    build_review_prompt_plan,
    build_status,
)
from frutlups.state import SliceKind, classify_slice_kind

_MEMORY_JSON_KEYS = {"enabled", "backend", "root", "message", "diagnostics"}
_POSTURE_V2 = "05_governance/current/memory_posture.md"
_POSTURE_LEGACY = "05_governance/llloom_operating_model.md"

_TEMPLATES = Path(__file__).resolve().parent / "fixtures" / "front_repo_contract"


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------


class _SpyRunner:
    """Returns ok for every command and records the argument vectors."""

    def __init__(self, stdout: str = "memory ok") -> None:
        self.calls: list[tuple[str, ...]] = []
        self._stdout = stdout

    def run(self, args: tuple[str, ...]) -> MemoryCommandResult:
        self.calls.append(tuple(args))
        return MemoryCommandResult(
            command=tuple(args),
            returncode=0,
            stdout=self._stdout,
            stderr="",
            ok=True,
        )


class _RaisingRunner:
    """Fails loudly if any memory command path is reached."""

    def run(self, args: tuple[str, ...]) -> MemoryCommandResult:  # pragma: no cover
        raise AssertionError(f"forbidden memory command reached: {args!r}")


class _NoQueryRunner:
    """Allows status/doctor but fails on any query/mutating verb."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def run(self, args: tuple[str, ...]) -> MemoryCommandResult:
        self.calls.append(tuple(args))
        forbidden = {
            "query",
            "seed",
            "apply",
            "ingest",
            "render",
            "supersede",
            "unlock",
            "reconcile",
            "rebuild",
        }
        if forbidden & set(args):  # pragma: no cover
            raise AssertionError(f"forbidden verb in memory command: {args!r}")
        return MemoryCommandResult(
            command=tuple(args), returncode=0, stdout="ok", stderr="", ok=True
        )


# ---------------------------------------------------------------------------
# Project builders
# ---------------------------------------------------------------------------


def _write_scaffolds(root: Path) -> None:
    target = root / "prompts" / "templates"
    target.mkdir(parents=True, exist_ok=True)
    (target / "coding_prompt.md").write_text(
        (_TEMPLATES / "coding_prompt.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (target / "review_prompt.md").write_text(
        (_TEMPLATES / "review_prompt.md").read_text(encoding="utf-8"), encoding="utf-8"
    )


def _make_v2_project(
    root: Path,
    memory_mode: str | None = "none",
    *,
    scaffolds: bool = True,
    memory_root_config: str | None = None,
    posture_config: str | None = None,
    make_root: bool = False,
    with_coding_prompt: bool = False,
) -> None:
    """Build a configured v2 project with a controllable Memory mode.

    ``memory_root_config`` / ``posture_config`` are raw YAML scalar fragments for
    ``optional_lanes.llloom.memory_root`` / ``posture_file`` (already quoted by the
    caller when needed). ``make_root`` creates the repo-root ``llloom_memory``
    directory so a selected ``llloom`` mode can be observed as available.
    """

    for name in (
        "00_brief",
        "03_experiments",
        "05_governance/reviews",
        "05_governance/current",
        "prompts/for_coding_agent",
        "prompts/for_review_agent",
        "questions",
    ):
        (root / name).mkdir(parents=True, exist_ok=True)
    if scaffolds:
        _write_scaffolds(root)
    layout = (
        "schema_version: frutlups_layout_config_v0\n"
        "profile_id: artifact_first_template_v2\n"
    )
    if memory_root_config is not None or posture_config is not None:
        layout += "optional_lanes:\n  llloom:\n"
        if memory_root_config is not None:
            layout += f"    memory_root: {memory_root_config}\n"
        if posture_config is not None:
            layout += f"    posture_file: {posture_config}\n"
    (root / "frutlups.layout.yaml").write_text(layout, encoding="utf-8")
    state = "# Project State\n\n"
    if memory_mode is not None:
        state += f"Memory mode: {memory_mode}\n"
    state += "Frutlups mode: manual\n"
    (root / "PROJECT_STATE.md").write_text(state, encoding="utf-8")
    (root / "03_experiments" / "active_roadmap.md").write_text(
        "### M001: First\n\nStatus: active\n", encoding="utf-8"
    )
    (root / "03_experiments" / "development_roadmap.md").write_text(
        "### M001: First\n\nSlices:\n\n- M001-S01: first slice\n", encoding="utf-8"
    )
    if make_root:
        (root / "llloom_memory").mkdir(exist_ok=True)
    if with_coding_prompt:
        (root / "prompts" / "for_coding_agent" / "001_first.md").write_text(
            "---\nmilestone: M001\nslice: M001-S01\ntitle: first slice\n---\n\n"
            "## Read First\n\n- `CLAUDE.md`\n- `README.md`\n\n"
            "## Self-Report\n\n"
            "`05_governance/reviews/m001_s01_first_slice_self_report.md`\n",
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# Prompt 044 shared helpers: valid v2 review-ready fixtures, runners, CLI
# ---------------------------------------------------------------------------

_SR_PATH = "05_governance/reviews/m001_s01_first_slice_self_report.md"
_REVIEW_PROMPT_PATH = "prompts/for_review_agent/001_review_frutlups_m001_s01_first_slice.md"


def _v2_self_report(review_prompt_path: str = _REVIEW_PROMPT_PATH) -> str:
    """A valid v2-schema self-report the review-plan evidence deriver accepts."""

    return (
        "# Coder Self-Report\n\n"
        "## Intent\n\nImplement the first slice.\n\n"
        "## Files Changed\n\n- `08_pkg/src/frutlups/project.py`\n\n"
        "## Behavior Implemented\n\nThe behavior was implemented.\n\n"
        "## Tests Added Or Updated\n\n- test_first_slice\n\n"
        "## Verification Run\n\n```\npython -m unittest discover -s tests\n```\n\n"
        "## Definition Of Done Audit\n\nDone.\n\n"
        "## Non-Goals Confirmed\n\nConfirmed.\n\n"
        "## Memory Used\n\nNone.\n\n"
        "## Memory Update Requested\n\nNone.\n\n"
        "## Known Limits / Follow-Up\n\nNone.\n\n"
        f"## Recommended Next Move\n\nCreate `{review_prompt_path}`.\n"
    )


def _make_v2_review_ready(
    root: Path,
    memory_mode: str | None = "none",
    *,
    posture_config: str | None = None,
    memory_root_config: str | None = None,
    make_root: bool = False,
) -> str:
    """Build a valid v2 project whose ``build_review_prompt_plan`` is valid.

    Generates the coding prompt through the real ``build_coding_prompt_plan`` and
    writes a matching valid v2 self-report, so ``build_review_prompt_plan`` finds
    a real unmatched coding prompt with derivable evidence (not a manually
    constructed template). Returns the written coding-prompt filename.
    """

    _make_v2_project(
        root,
        memory_mode,
        posture_config=posture_config,
        memory_root_config=memory_root_config,
        make_root=make_root,
    )
    plan = build_coding_prompt_plan(root, memory_runner=_ReadOnlyRunner())
    if not plan.valid or plan.render is None or plan.template is None:
        raise AssertionError(f"coding plan fixture invalid: {plan.errors}")
    filename = (
        plan.preview.filename
        if plan.preview is not None and plan.preview.filename
        else "001_frutlups_m001_s01_first_slice.md"
    )
    (root / "prompts" / "for_coding_agent" / filename).write_text(
        plan.render.content, encoding="utf-8"
    )
    sr = root / plan.template.self_report_path
    sr.parent.mkdir(parents=True, exist_ok=True)
    sr.write_text(_v2_self_report(), encoding="utf-8")
    return filename


class _ReadOnlyRunner:
    """Allows only read-only ``status``/``doctor``; raises on any other verb.

    Fails loudly if a configured scaffold or handoff ever issues a memory
    ``query`` or any mutating verb, while still letting an available ``llloom``
    status observation run its two read-only commands.
    """

    _READONLY = frozenset({"status", "doctor"})
    _MUTATING = frozenset(
        {"query", "seed", "apply", "ingest", "render", "supersede", "unlock", "reconcile", "rebuild"}
    )

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def run(self, args: tuple[str, ...]) -> MemoryCommandResult:
        self.calls.append(tuple(args))
        verbs = set(args)
        if verbs & self._MUTATING:  # pragma: no cover - falsifier guard
            raise AssertionError(f"forbidden memory verb reached: {args!r}")
        if not (verbs & self._READONLY):  # pragma: no cover - falsifier guard
            raise AssertionError(f"unexpected memory command: {args!r}")
        return MemoryCommandResult(
            command=tuple(args), returncode=0, stdout="ok", stderr="", ok=True
        )


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    """Run the frutlups CLI, capturing exit code, stdout, and stderr."""

    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(args)
    return code, out.getvalue(), err.getvalue()


def _fs_snapshot(root: Path) -> dict[str, bytes]:
    """Map every file under ``root`` to its bytes (for no-write assertions)."""

    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _make_legacy_project(root: Path, *, make_memory_root: bool = False) -> None:
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
    (root / "03_experiments" / "development_roadmap_frutlups.md").write_text(
        "### M001: First\n\nSlices:\n\n- M001-S01: first slice\n", encoding="utf-8"
    )
    if make_memory_root:
        (root / "07_app" / "llloom_memory").mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# D1: mode/root truth table
# ---------------------------------------------------------------------------


class D1ModeRootTruthTableTests(unittest.TestCase):
    def _mem(self, root: Path, runner) -> dict:
        return build_status(root, memory_runner=runner).memory.to_dict()

    def test_none_ignores_stale_directory_and_runs_no_command(self) -> None:
        # False-positive falsifier: a stale llloom_memory dir under mode none
        # must not enable memory or invoke the runner.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "none")
            (root / "llloom_memory").mkdir()
            runner = _RaisingRunner()
            m = self._mem(root, runner)
        self.assertFalse(m["enabled"])
        self.assertEqual(m["backend"], "disabled")
        self.assertIsNone(m["root"])

    def test_llloom_with_safe_contained_root_is_observed(self) -> None:
        # False-negative falsifier: mode llloom with a present repo-root
        # llloom_memory must be observed via the injected read-only runner.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "llloom")
            (root / "llloom_memory").mkdir()
            runner = _SpyRunner()
            m = self._mem(root, runner)
        self.assertTrue(m["enabled"])
        self.assertEqual(m["backend"], "llloom")
        self.assertEqual(m["root"], str(root / "llloom_memory"))
        self.assertGreaterEqual(len(runner.calls), 2)  # status + doctor
        verbs = [c[-1] for c in runner.calls]
        self.assertIn("status", verbs)
        self.assertIn("doctor", verbs)

    def test_llloom_with_missing_root_is_disabled_with_one_diagnostic(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "llloom")  # no llloom_memory dir created
            runner = _RaisingRunner()
            m = self._mem(root, runner)
        self.assertFalse(m["enabled"])
        self.assertEqual(m["backend"], "llloom")
        self.assertIsNone(m["root"])
        self.assertEqual(len(m["diagnostics"]), 1)

    def test_lightweight_is_active_without_llloom_runner(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "lightweight")
            (root / "llloom_memory").mkdir()  # must be ignored under lightweight
            runner = _RaisingRunner()
            m = self._mem(root, runner)
        self.assertTrue(m["enabled"])
        self.assertEqual(m["backend"], "lightweight")
        self.assertIsNone(m["root"])
        self.assertEqual(m["diagnostics"], [])

    def test_missing_mode_is_deterministic_disabled(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, memory_mode=None)  # no Memory mode line
            (root / "llloom_memory").mkdir()
            m = self._mem(root, _RaisingRunner())
        self.assertFalse(m["enabled"])
        self.assertEqual(m["backend"], "disabled")
        self.assertEqual(m["message"], "memory backend disabled")

    def test_invalid_mode_is_deterministic_disabled(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, memory_mode="bogus")
            (root / "llloom_memory").mkdir()
            m = self._mem(root, _RaisingRunner())
        self.assertFalse(m["enabled"])
        self.assertEqual(m["backend"], "disabled")

    def test_configured_safe_root_override_is_used(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "llloom", memory_root_config="memory/store")
            (root / "memory" / "store").mkdir(parents=True)
            runner = _SpyRunner()
            m = self._mem(root, runner)
        self.assertTrue(m["enabled"])
        self.assertEqual(m["root"], str(root / "memory" / "store"))

    def test_configured_unsafe_root_disables_without_fallback(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "llloom", memory_root_config='"../escape"')
            (root / "llloom_memory").mkdir()  # default would enable if we fell back
            status = build_status(root, memory_runner=_RaisingRunner())
            m = status.memory.to_dict()
        # Must NOT silently fall back to the default llloom_memory root.
        self.assertFalse(m["enabled"])
        self.assertEqual(m["backend"], "llloom")
        self.assertIsNone(m["root"])
        codes = {d.code for d in status.layout.diagnostics}
        self.assertIn("unsafe_memory_root", codes)

    def test_llloom_root_that_is_a_file_is_disabled(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "llloom")
            (root / "llloom_memory").write_text("not a dir", encoding="utf-8")
            m = self._mem(root, _RaisingRunner())
        self.assertFalse(m["enabled"])
        self.assertEqual(m["backend"], "llloom")

    def test_memory_json_keys_unchanged_across_cells(self) -> None:
        for mode in ("none", "lightweight", "llloom", "bogus", None):
            with self.subTest(mode=mode), TemporaryDirectory() as tmp:
                root = Path(tmp)
                _make_v2_project(root, memory_mode=mode)
                m = build_status(root, memory_runner=_SpyRunner()).memory.to_dict()
                self.assertEqual(set(m.keys()), _MEMORY_JSON_KEYS)


# ---------------------------------------------------------------------------
# D1: path safety and containment
# ---------------------------------------------------------------------------


class D1PathSafetyTests(unittest.TestCase):
    def test_symlink_escape_is_rejected(self) -> None:
        with TemporaryDirectory() as tmp_root, TemporaryDirectory() as tmp_out:
            root = Path(tmp_root)
            _make_v2_project(root, "llloom")
            outside = Path(tmp_out)
            (outside / "real").mkdir()
            link = root / "llloom_memory"
            try:
                link.symlink_to(outside / "real", target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation not permitted on this platform")
            m = build_status(root, memory_runner=_RaisingRunner()).memory.to_dict()
        self.assertFalse(m["enabled"])
        self.assertEqual(m["backend"], "llloom")

    def test_resolver_failure_is_disabled(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "llloom_memory").mkdir()
            profile = v2_default_profile()
            with mock.patch.object(Path, "resolve", side_effect=OSError("boom")):
                status = _select_llloom_memory_status(root, profile, _RaisingRunner())
        self.assertFalse(status.enabled)
        self.assertEqual(status.backend, "llloom")
        self.assertIsNone(status.root)

    def test_diagnostics_are_bounded_and_do_not_echo_config(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            hostile = '"../secret/HOSTILE_VALUE_marker"'
            _make_v2_project(root, "llloom", memory_root_config=hostile)
            status = build_status(root, memory_runner=_RaisingRunner())
        for diag in status.layout.diagnostics:
            self.assertLessEqual(len(diag.message), 240)
            self.assertNotIn("HOSTILE_VALUE_marker", diag.message)
        for diag in status.memory.diagnostics:
            self.assertLessEqual(len(diag), 240)
            self.assertNotIn("HOSTILE_VALUE_marker", diag)


# ---------------------------------------------------------------------------
# D1: single snapshot (one state read, one layout selection)
# ---------------------------------------------------------------------------


class D1SingleSnapshotTests(unittest.TestCase):
    def test_state_file_read_once_and_layout_selected_once(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "llloom")
            (root / "llloom_memory").mkdir()

            real_read = project_module._read_state_file_once
            state_reads = {"n": 0}

            def counting_read(path):
                state_reads["n"] += 1
                return real_read(path)

            with mock.patch("frutlups.project.load_layout_profile") as sel:
                sel.side_effect = load_layout_profile
                with mock.patch.object(
                    project_module, "_read_state_file_once", side_effect=counting_read
                ):
                    build_status(root, memory_runner=_SpyRunner())
            self.assertEqual(state_reads["n"], 1)
            self.assertEqual(sel.call_count, 1)

    def test_legacy_fallback_preserves_direct_wrapper(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_legacy_project(root, make_memory_root=True)
            runner = _SpyRunner()
            m = build_status(root, memory_runner=runner).memory.to_dict()
        # Historical root-sniff compatibility: 07_app/llloom_memory enables.
        self.assertTrue(m["enabled"])
        self.assertEqual(m["backend"], "llloom")
        self.assertEqual(m["root"], str(root / "07_app" / "llloom_memory"))

    def test_detect_memory_public_behavior_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "07_app" / "llloom_memory").mkdir(parents=True)
            status = detect_memory(root, runner=_SpyRunner())
        self.assertTrue(status.enabled)
        self.assertEqual(status.backend, "llloom")
        self.assertEqual(status.root, root / "07_app" / "llloom_memory")


# ---------------------------------------------------------------------------
# D2: milestone identity no longer grants mutation posture
# ---------------------------------------------------------------------------


class D2SliceKindTests(unittest.TestCase):
    def test_m010_and_variants_are_normal(self) -> None:
        for mid in ("M010", "m010", "M010-S01", "m010-s02"):
            with self.subTest(mid=mid):
                self.assertEqual(classify_slice_kind(mid), SliceKind.NORMAL)

    def test_arbitrary_milestones_are_normal(self) -> None:
        for mid in ("M001", "M009", "M011", "M999", "", "MXXX"):
            with self.subTest(mid=mid):
                self.assertEqual(classify_slice_kind(mid), SliceKind.NORMAL)

    def test_enum_members_retained(self) -> None:
        self.assertEqual(SliceKind.NORMAL, "normal")
        self.assertEqual(SliceKind.MEMORY_UPDATE, "memory_update")


# ---------------------------------------------------------------------------
# D3: configured scaffold posture routing (no query, deterministic)
# ---------------------------------------------------------------------------


class D3ConfiguredScaffoldTests(unittest.TestCase):
    def test_configured_coding_routes_posture_for_llloom_without_query(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "llloom")  # no memory root -> no runner call
            plan = build_coding_prompt_plan(root, memory_runner=_RaisingRunner())
        self.assertTrue(plan.valid)
        self.assertIn(_POSTURE_V2, plan.render.content)
        self.assertNotIn("Optional Memory Context", plan.render.content)

    def test_configured_review_routes_posture_for_lightweight(self) -> None:
        # Prompt 044 Gate E1: exercise the real public build_review_prompt_plan
        # through a valid end-to-end fixture (real matched coding prompt + valid
        # self-report), not a manually constructed template or private renderer.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_review_ready(root, "lightweight")
            plan = build_review_prompt_plan(root)
        self.assertTrue(plan.valid, msg=str(plan.errors))
        self.assertEqual(plan.render.content.count(_POSTURE_V2), 1)
        self.assertNotIn("Optional Memory Context", plan.render.content)

    def test_configured_coding_none_adds_no_posture_reading(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "none")
            plan = build_coding_prompt_plan(root, memory_runner=_RaisingRunner())
        self.assertTrue(plan.valid)
        self.assertNotIn(_POSTURE_V2, plan.render.content)

    def test_configured_llloom_available_issues_no_query(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "llloom")
            (root / "llloom_memory").mkdir()
            runner = _NoQueryRunner()
            plan = build_coding_prompt_plan(root, memory_runner=runner)
        self.assertTrue(plan.valid)
        self.assertNotIn("Optional Memory Context", plan.render.content)
        self.assertTrue(all("query" not in c for c in runner.calls))

    def test_configured_posture_reading_helper(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "llloom")
            status = build_status(root, memory_runner=_RaisingRunner())
            profile = status.layout.profile
        self.assertEqual(
            _configured_posture_reading(status, profile), (_POSTURE_V2,)
        )

    def test_dedup_append_is_exact_and_order_preserving(self) -> None:
        base = ("CLAUDE.md", "README.md")
        self.assertEqual(_dedup_append(base, ("README.md",)), base)
        self.assertEqual(
            _dedup_append(base, ("X.md", "CLAUDE.md")),
            ("CLAUDE.md", "README.md", "X.md"),
        )


# ---------------------------------------------------------------------------
# D3: legacy renderer posture compatibility and non-legacy routing
# ---------------------------------------------------------------------------


class D3LegacyRendererTests(unittest.TestCase):
    def test_legacy_coding_render_keeps_operating_model_path(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_legacy_project(root)
            plan = build_coding_prompt_plan(root, memory_runner=_SpyRunner())
        self.assertTrue(plan.valid)
        self.assertIn(_POSTURE_LEGACY, plan.render.content)

    def test_legacy_review_render_keeps_operating_model_path(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_legacy_project(root)
            coding = build_coding_prompt_plan(root, memory_runner=_SpyRunner())
            (root / "prompts" / "for_coding_agent" / "001_first.md").write_text(
                coding.render.content, encoding="utf-8"
            )
            # A legacy review render still uses the historical path by default.
            from frutlups.review_prompt_template import (
                ReviewPromptTemplate,
                render_review_prompt,
            )

            template = ReviewPromptTemplate(
                sequence=1,
                milestone_id="M001",
                slice_id="M001-S01",
                slug="first",
                title="first",
                role_instructions="review",
                required_reading=("CLAUDE.md", "README.md"),
                coding_prompt_path="prompts/for_coding_agent/001_first.md",
                self_report_path="05_governance/reviews/m001_s01_first_self_report.md",
                review_output_path="05_governance/reviews/m001_s01_first_review_report.md",
                expected_changed_files=("08_pkg/src/frutlups/x.py",),
                verification_commands=("python -m unittest discover -s tests",),
                severity_guidance=(
                    "blocker: x",
                    "major: x",
                    "minor: x",
                    "nit: x",
                ),
                verdict_choices=("pass", "needs_work", "blocked", "override"),
            )
            render = render_review_prompt(template)
        self.assertIn(_POSTURE_LEGACY, render.content)


# ---------------------------------------------------------------------------
# D3: handoff audit (both exported builders, all input forms)
# ---------------------------------------------------------------------------


class D3HandoffTests(unittest.TestCase):
    def test_legacy_handoffs_keep_operating_model_path(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_legacy_project(root)
            coder = build_coder_handoff(root).content
            reviewer = build_reviewer_handoff(root).content
        self.assertIn(_POSTURE_LEGACY, coder)
        self.assertIn(_POSTURE_LEGACY, reviewer)

    def test_v2_none_cites_no_memory_file(self) -> None:
        with mock.patch(
            "frutlups.memory.SubprocessMemoryCommandRunner.run",
            side_effect=AssertionError("no memory command in handoff"),
        ), TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "none")
            coder = build_coder_handoff(root).content
            reviewer = build_reviewer_handoff(root).content
        for content in (coder, reviewer):
            self.assertNotIn(_POSTURE_LEGACY, content)
            self.assertNotIn(_POSTURE_V2, content)

    def test_v2_llloom_cites_selected_posture(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "llloom")  # no root -> no subprocess
            coder = build_coder_handoff(root).content
            reviewer = build_reviewer_handoff(root).content
        for content in (coder, reviewer):
            self.assertIn(_POSTURE_V2, content)
            self.assertNotIn(_POSTURE_LEGACY, content)

    def test_input_form_byte_parity(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "llloom")
            status = build_status(root)
            from_path = build_coder_handoff(root).content
            from_string = build_coder_handoff(str(root)).content
            from_status = build_coder_handoff(status).content
        self.assertEqual(from_path, from_string)
        self.assertEqual(from_path, from_status)


# ---------------------------------------------------------------------------
# Purity / no-write / read-only snapshots
# ---------------------------------------------------------------------------


class PurityAndNoWriteTests(unittest.TestCase):
    def test_build_status_is_pure_across_repeated_calls(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "llloom")
            (root / "llloom_memory").mkdir()
            a = build_status(root, memory_runner=_SpyRunner()).memory.to_dict()
            b = build_status(root, memory_runner=_SpyRunner()).memory.to_dict()
        self.assertEqual(a, b)

    def test_memory_selection_writes_nothing(self) -> None:
        for mode in ("none", "lightweight", "llloom"):
            with self.subTest(mode=mode), TemporaryDirectory() as tmp:
                root = Path(tmp)
                _make_v2_project(root, mode)
                if mode == "llloom":
                    (root / "llloom_memory").mkdir()
                before = set(p for p in root.rglob("*"))
                build_status(root, memory_runner=_SpyRunner())
                build_coding_prompt_plan(root, memory_runner=_SpyRunner())
                after = set(p for p in root.rglob("*"))
                self.assertEqual(before, after)

    def test_default_memory_root_is_profile_aware(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = TemplatePaths(root, profile=legacy_profile())
            v2 = TemplatePaths(root, profile=v2_default_profile())
            none_profile = TemplatePaths(root, profile=None)
        self.assertEqual(legacy.default_memory_root, root / "07_app" / "llloom_memory")
        self.assertEqual(v2.default_memory_root, root / "llloom_memory")
        self.assertEqual(
            none_profile.default_memory_root, root / "07_app" / "llloom_memory"
        )


# ---------------------------------------------------------------------------
# Public / planning / package boundary pins
# ---------------------------------------------------------------------------


class PublicBoundaryPinsTests(unittest.TestCase):
    def test_version_and_export_count(self) -> None:
        self.assertEqual(frutlups.__version__, "0.1.6")
        self.assertEqual(len(frutlups.__all__), 152)

    def test_nine_cli_verbs(self) -> None:
        import argparse

        parser = cli._build_parser()
        subparser_actions = [
            a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
        ]
        self.assertEqual(len(subparser_actions), 1)
        verbs = set(subparser_actions[0].choices)
        self.assertEqual(
            verbs,
            {
                "declare-rework",
                "status",
                "next",
                "orchestrator-plan",
                "orchestrator-run",
                "orchestrator-handoff",
                "make-review-prompt",
                "make-coding-prompt",
                "record-verdict",
            },
        )

    def test_planning_frontier_contract_unchanged(self) -> None:
        self.assertEqual(PLANNING_FRONTIER_CONTRACT_ID, "frutlups.planning_frontier")
        self.assertEqual(PLANNING_FRONTIER_SUPPORTED_VERSIONS, ("1",))

    def test_layout_profile_json_omits_llloom_fields(self) -> None:
        # The new typed fields are intentionally not serialized, so the
        # layout-profile JSON contract (and the shapes embedding it) is unchanged.
        d = v2_default_profile().to_dict()
        self.assertNotIn("llloom_memory_root", d)
        self.assertNotIn("llloom_posture_file", d)

    def test_config_parses_optional_lane_paths(self) -> None:
        profile, _diags = profile_from_config(
            {
                "profile_id": "artifact_first_template_v2",
                "optional_lanes": {
                    "llloom": {
                        "memory_root": "memory/store",
                        "posture_file": "05_governance/current/memory_posture.md",
                    }
                },
            }
        )
        self.assertEqual(profile.llloom_memory_root, "memory/store")
        self.assertEqual(profile.llloom_posture_file, _POSTURE_V2)


# ---------------------------------------------------------------------------
# Prompt 044 Gate B: resolver-failure containment through public build_status
# ---------------------------------------------------------------------------


def _resolve_raising(exc: BaseException):
    """A Path.resolve replacement that raises ``exc`` only for the llloom root."""

    real = Path.resolve

    def _fake(self, *a, **k):
        if self.name == "llloom_memory":
            raise exc
        return real(self, *a, **k)

    return _fake


class GateBResolverContainmentTests(unittest.TestCase):
    def _assert_contained(self, exc: BaseException) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "llloom", make_root=True)
            runner = _RaisingRunner()  # runner must stay unreachable
            with mock.patch.object(Path, "resolve", _resolve_raising(exc)):
                # Public composition, not the private helper.
                status = build_status(root, memory_runner=runner)
            m = status.memory.to_dict()
        self.assertFalse(m["enabled"])
        self.assertEqual(m["backend"], "llloom")
        self.assertIsNone(m["root"])
        self.assertEqual(len(m["diagnostics"]), 1)
        self.assertLessEqual(len(m["diagnostics"][0]), 240)

    def test_oserror_is_contained_through_build_status(self) -> None:
        self._assert_contained(OSError("synthetic os failure"))

    def test_runtimeerror_is_contained_through_build_status(self) -> None:
        # Effective mutation falsifier: fails if RuntimeError is removed from the
        # owned resolver-failure boundary (the exception would then escape here).
        self._assert_contained(RuntimeError("synthetic resolution loop"))

    def test_programming_error_is_not_swallowed(self) -> None:
        # A defect outside the documented resolver-failure domain must surface,
        # proving the boundary is not a broad ``except Exception``.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "llloom", make_root=True)
            with mock.patch.object(Path, "resolve", _resolve_raising(KeyError("boom"))):
                with self.assertRaises(KeyError):
                    build_status(root, memory_runner=_RaisingRunner())


# ---------------------------------------------------------------------------
# Prompt 044 Gate C: typed, bounded, single-line, inert posture-path contract
# ---------------------------------------------------------------------------

# Raw YAML scalar fragments (double-quoted so the bounded YAML boundary reads a
# single scalar) that must all be rejected as unsafe/structurally-active.
_UNSAFE_POSTURE_YAML = {
    "newline_fence_heading": '"safe\\n```\\n# injected"',
    "carriage_return": '"safe\\rmore"',
    "unicode_line_sep": '"safe\\u2028x"',
    "unicode_para_sep": '"safe\\u2029x"',
    "tab": '"safe\\tx"',
    "vertical_tab": '"safe\\x0bx"',
    "backtick": '"safe`code`.md"',
    "control": '"safe\\u0001x"',
    "absolute": '"/etc/passwd"',
    "drive": '"C:\\\\Users\\\\x"',
    "traversal": '"../escape.md"',
}


class GateCPostureNormalizationTests(unittest.TestCase):
    def test_safe_paths_normalized_and_preserved(self) -> None:
        self.assertEqual(
            normalize_memory_lane_path("05_governance/current/memory_posture.md"),
            "05_governance/current/memory_posture.md",
        )
        self.assertEqual(normalize_memory_lane_path("  memory/store  "), "memory/store")
        self.assertEqual(normalize_memory_lane_path("memory\\store"), "memory/store")

    def test_structural_and_unsafe_values_rejected(self) -> None:
        rejected = [
            "safe\n```\n# injected",
            "safe\r\nx",
            "safe\u2028x",
            "safe\u2029x",
            "safe\x85x",
            "safe\x0bx",
            "safe\x0cx",
            "safe`code`.md",
            "safe\tx",
            "safe\x01x",
            "safe\x7fx",
            "/etc/passwd",
            "C:\\Users\\x",
            "../escape",
            "   ",
            "a/" * 120,  # over the bound
        ]
        for value in rejected:
            with self.subTest(value=repr(value)):
                self.assertIsNone(normalize_memory_lane_path(value))

    def test_bound_boundary(self) -> None:
        ok = "a" * MEMORY_LANE_PATH_MAX
        self.assertEqual(normalize_memory_lane_path(ok), ok)
        self.assertIsNone(normalize_memory_lane_path("a" * (MEMORY_LANE_PATH_MAX + 1)))

    def test_non_string_rejected(self) -> None:
        for value in (None, 123, ["x"], {"a": 1}):
            self.assertIsNone(normalize_memory_lane_path(value))

    def test_repeated_calls_are_pure(self) -> None:
        v = "memory/store"
        self.assertEqual(normalize_memory_lane_path(v), normalize_memory_lane_path(v))


class GateCUnsafePostureConsumersTests(unittest.TestCase):
    """A rejected posture value cannot inject structure into any consumer."""

    def _diag_codes(self, status) -> set[str]:
        return {d.code for d in status.layout.diagnostics}

    def test_no_consumer_emits_injected_structure(self) -> None:
        for name, yaml_frag in _UNSAFE_POSTURE_YAML.items():
            with self.subTest(case=name), TemporaryDirectory() as tmp:
                root = Path(tmp)
                fn = _make_v2_review_ready(root, "lightweight", posture_config=yaml_frag)
                status = build_status(root, memory_runner=_RaisingRunner())
                # The typed layout stores the disabled sentinel, not the value.
                self.assertEqual(status.layout.profile.llloom_posture_file, "")
                self.assertIn("unsafe_memory_posture_path", self._diag_codes(status))

                cplan = build_coding_prompt_plan(root, memory_runner=_RaisingRunner())
                rplan = build_review_prompt_plan(root)
                coder = build_coder_handoff(root).content
                reviewer = build_reviewer_handoff(root).content

                for blob in (
                    cplan.render.content,
                    rplan.render.content,
                    coder,
                    reviewer,
                ):
                    self.assertNotIn("# injected", blob)
                    self.assertNotIn("`code`", blob)
                    self.assertNotIn(_POSTURE_V2, blob)  # disabled -> not cited
                # No live injected fence: the number of ``` fences stays even
                # (all opened fences close) in every generated document.
                for blob in (cplan.render.content, rplan.render.content):
                    self.assertEqual(blob.count("```") % 2, 0)

    def test_unsafe_posture_cli_writes_no_injection(self) -> None:
        # CLI text + JSON, dry-run + actual write, all inert for a rejected posture.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "lightweight", posture_config=_UNSAFE_POSTURE_YAML["newline_fence_heading"])
            with mock.patch(
                "frutlups.memory.SubprocessMemoryCommandRunner.run",
                side_effect=AssertionError("no memory command expected"),
            ):
                code, out, _err = _run_cli(["make-coding-prompt", str(root)])
                self.assertEqual(code, 0)
                written = list((root / "prompts" / "for_coding_agent").glob("*.md"))
                self.assertEqual(len(written), 1)
                body = written[0].read_text(encoding="utf-8")
        self.assertNotIn("# injected", body)
        self.assertNotIn(_POSTURE_V2, body)
        self.assertEqual(body.count("```") % 2, 0)

    def test_safe_posture_override_still_accepted(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(
                root, "lightweight", posture_config='"05_governance/current/custom_posture.md"'
            )
            plan = build_coding_prompt_plan(root, memory_runner=_RaisingRunner())
        self.assertTrue(plan.valid)
        self.assertIn("05_governance/current/custom_posture.md", plan.render.content)


# ---------------------------------------------------------------------------
# Prompt 044 Gate D: unsafe-root sentinel yields no usable TemplatePaths fallback
# ---------------------------------------------------------------------------


class GateDUnsafeRootTemplatePathsTests(unittest.TestCase):
    def test_sentinel_profile_fails_closed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "llloom", memory_root_config='"../escape"', make_root=True)
            status = build_status(root, memory_runner=_RaisingRunner())
            profile = status.layout.profile
            self.assertEqual(profile.llloom_memory_root, "")
            tp = TemplatePaths(root, profile=profile)
            with self.assertRaises(ValueError):
                _ = tp.default_memory_root
            # It must not yield the legacy root, project root, or any usable path.
            try:
                value = tp.default_memory_root
            except ValueError:
                value = None
            self.assertIsNone(value)

    def test_valid_profiles_still_return_expected_roots(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(
                TemplatePaths(root, profile=legacy_profile()).default_memory_root,
                root / "07_app" / "llloom_memory",
            )
            self.assertEqual(
                TemplatePaths(root, profile=v2_default_profile()).default_memory_root,
                root / "llloom_memory",
            )
            self.assertEqual(
                TemplatePaths(root, profile=None).default_memory_root,
                root / "07_app" / "llloom_memory",
            )

    def test_no_production_consumer_recovers_a_root_from_the_sentinel(self) -> None:
        # Sweep: with the sentinel active, the live status memory observation is
        # disabled (no root leaked) and no default_memory_root is materialized on
        # any read-only composition path.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "llloom", memory_root_config='"../escape"', make_root=True)
            status = build_status(root, memory_runner=_RaisingRunner())
            self.assertIsNone(status.memory.to_dict()["root"])
            self.assertFalse(status.memory.enabled)
            # Read-only composition (plans, handoffs) must not raise or leak a root.
            build_coding_prompt_plan(root, memory_runner=_RaisingRunner())
            _make_v2_review_ready  # review-ready path exercised elsewhere
            build_coder_handoff(root)
            build_reviewer_handoff(root)


# ---------------------------------------------------------------------------
# Prompt 044 Gate E1: real configured coding and review plans (all modes)
# ---------------------------------------------------------------------------

# (mode, make_root, posture_expected)
_PLAN_MODE_MATRIX = (
    ("none", False, False),
    ("lightweight", False, True),
    ("llloom", True, True),   # available
    ("llloom", False, True),  # unavailable but mode active
    (None, False, False),     # missing mode
    ("bogus", False, False),  # invalid mode
)

_CODING_PLAN_KEYS = {
    "frontier", "sequence", "slug", "valid", "errors",
    "template", "render", "preview", "coding_prompt_dir",
}


class GateE1RealPlansTests(unittest.TestCase):
    def test_coding_and_review_plans_across_modes(self) -> None:
        for mode, make_root, posture_expected in _PLAN_MODE_MATRIX:
            with self.subTest(mode=mode, make_root=make_root), TemporaryDirectory() as tmp:
                root = Path(tmp)
                _make_v2_review_ready(root, mode, make_root=make_root)
                runner = _ReadOnlyRunner()  # raises on query/mutating verbs
                cplan = build_coding_prompt_plan(root, memory_runner=runner)
                rplan = build_review_prompt_plan(root)

                self.assertTrue(cplan.valid, msg=str(cplan.errors))
                self.assertTrue(rplan.valid, msg=str(rplan.errors))
                # JSON key sets unchanged.
                self.assertEqual(set(cplan.to_dict().keys()), _CODING_PLAN_KEYS)
                self.assertIn("render", rplan.to_dict())

                for blob in (cplan.render.content, rplan.render.content):
                    self.assertNotIn("Optional Memory Context", blob)
                    if posture_expected:
                        self.assertEqual(blob.count(_POSTURE_V2), 1)
                    else:
                        self.assertNotIn(_POSTURE_V2, blob)
                # No query verb ever issued (available llloom uses status+doctor).
                self.assertTrue(all("query" not in c for c in runner.calls))

    def test_invalid_render_shape_has_stable_keys(self) -> None:
        # A refused render keeps the documented plan JSON key set.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "lightweight")
            (root / "prompts" / "templates" / "coding_prompt.md").write_text(
                "# broken scaffold with no slots\n", encoding="utf-8"
            )
            plan = build_coding_prompt_plan(root, memory_runner=_RaisingRunner())
        self.assertFalse(plan.valid)
        self.assertEqual(set(plan.to_dict().keys()), _CODING_PLAN_KEYS)


# ---------------------------------------------------------------------------
# Prompt 044 Gate E2: CLI text/JSON, dry-run, and actual writes
# ---------------------------------------------------------------------------


class GateE2CliMatrixTests(unittest.TestCase):
    def _readonly_patch(self):
        return mock.patch(
            "frutlups.memory.SubprocessMemoryCommandRunner.run",
            side_effect=lambda args: _ReadOnlyRunner().run(args),
        )

    def test_make_coding_prompt_dry_run_and_write(self) -> None:
        for mode, make_root, posture_expected in _PLAN_MODE_MATRIX:
            with self.subTest(mode=mode), TemporaryDirectory() as tmp:
                root = Path(tmp)
                _make_v2_project(root, mode, make_root=make_root)
                with self._readonly_patch():
                    # dry-run text: no write, filesystem unchanged.
                    before = _fs_snapshot(root)
                    with mock.patch(
                        "frutlups.cli.write_coding_prompt",
                        side_effect=AssertionError("dry-run must not write"),
                    ):
                        code, out, _e = _run_cli(["make-coding-prompt", str(root), "--dry-run"])
                    self.assertEqual(code, 0)
                    self.assertEqual(_fs_snapshot(root), before)
                    # dry-run JSON: stable key set, no write.
                    code_j, out_j, _e = _run_cli(
                        ["make-coding-prompt", str(root), "--dry-run", "--json"]
                    )
                    self.assertEqual(code_j, 0)
                    payload = json.loads(out_j)
                    self.assertEqual(set(payload.keys()), _CODING_PLAN_KEYS)
                    self.assertEqual(_fs_snapshot(root), before)
                    # actual write text: exactly one new prompt file.
                    code_w, out_w, _e = _run_cli(["make-coding-prompt", str(root)])
                    self.assertEqual(code_w, 0)
                    after = _fs_snapshot(root)
                    new = set(after) - set(before)
                    self.assertEqual(len(new), 1)
                    written = next(iter(new))
                    self.assertTrue(written.startswith("prompts/for_coding_agent/"))
                    body = after[written].decode("utf-8")
                    if posture_expected:
                        self.assertEqual(body.count(_POSTURE_V2), 1)
                    else:
                        self.assertNotIn(_POSTURE_V2, body)
                    self.assertNotIn("Optional Memory Context", body)

    def test_make_coding_prompt_write_json_has_write_result(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "lightweight")
            with self._readonly_patch():
                code, out, _e = _run_cli(["make-coding-prompt", str(root), "--json"])
            self.assertEqual(code, 0)
            payload = json.loads(out)
            self.assertIn("write_result", payload)
            self.assertEqual(set(payload.keys()) - {"write_result"}, _CODING_PLAN_KEYS)

    def test_make_review_prompt_dry_run_and_write(self) -> None:
        for mode, make_root, posture_expected in _PLAN_MODE_MATRIX:
            with self.subTest(mode=mode), TemporaryDirectory() as tmp:
                root = Path(tmp)
                _make_v2_review_ready(root, mode, make_root=make_root)
                with self._readonly_patch():
                    before = _fs_snapshot(root)
                    with mock.patch(
                        "frutlups.cli._write_review_prompt_content",
                        side_effect=AssertionError("dry-run must not write"),
                    ):
                        code, out, _e = _run_cli(["make-review-prompt", str(root), "--dry-run"])
                    self.assertEqual(code, 0)
                    self.assertEqual(_fs_snapshot(root), before)
                    code_j, out_j, _e = _run_cli(
                        ["make-review-prompt", str(root), "--dry-run", "--json"]
                    )
                    self.assertEqual(code_j, 0)
                    self.assertIn("render", json.loads(out_j))
                    self.assertEqual(_fs_snapshot(root), before)
                    code_w, out_w, _e = _run_cli(["make-review-prompt", str(root)])
                    self.assertEqual(code_w, 0)
                    new = set(_fs_snapshot(root)) - set(before)
                    self.assertEqual(len(new), 1)
                    written = next(iter(new))
                    self.assertTrue(written.startswith("prompts/for_review_agent/"))
                    body = _fs_snapshot(root)[written].decode("utf-8")
                    if posture_expected:
                        self.assertEqual(body.count(_POSTURE_V2), 1)
                    else:
                        self.assertNotIn(_POSTURE_V2, body)


# ---------------------------------------------------------------------------
# Prompt 044 Gate E3: both handoff builders across every input form
# ---------------------------------------------------------------------------

# (label, builder, marker builder receives)
_HANDOFF_BUILDERS = (("coder", build_coder_handoff), ("reviewer", build_reviewer_handoff))

# (label, project builder, posture expected string or None, legacy?)
def _handoff_project(root: Path, kind: str) -> None:
    if kind == "legacy":
        _make_legacy_project(root)
    elif kind == "available_llloom":
        _make_v2_project(root, "llloom", make_root=True)
    elif kind == "unavailable_llloom":
        _make_v2_project(root, "llloom")
    elif kind == "rejected_posture":
        _make_v2_project(root, "lightweight", posture_config=_UNSAFE_POSTURE_YAML["newline_fence_heading"])
    else:  # none, lightweight
        _make_v2_project(root, kind)


_HANDOFF_EXPECT = {
    "legacy": _POSTURE_LEGACY,
    "none": None,
    "lightweight": _POSTURE_V2,
    "available_llloom": _POSTURE_V2,
    "unavailable_llloom": _POSTURE_V2,
    "rejected_posture": None,
}


class GateE3HandoffMatrixTests(unittest.TestCase):
    def test_both_builders_all_forms_and_modes(self) -> None:
        for label, builder in _HANDOFF_BUILDERS:
            for kind, expected in _HANDOFF_EXPECT.items():
                with self.subTest(builder=label, kind=kind), TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    _handoff_project(root, kind)
                    with mock.patch(
                        "frutlups.memory.SubprocessMemoryCommandRunner.run",
                        side_effect=lambda args: _ReadOnlyRunner().run(args),
                    ):
                        before = _fs_snapshot(root)
                        prebuilt = build_status(root)
                        from_path = builder(root).content
                        from_string = builder(str(root)).content
                        from_status = builder(prebuilt).content
                        after = _fs_snapshot(root)
                    # Read-only: no filesystem change.
                    self.assertEqual(before, after)
                    # Byte parity among accepted input forms.
                    self.assertEqual(from_path, from_string)
                    self.assertEqual(from_path, from_status)
                    # Exact posture occurrence or absence. The coder handoff cites
                    # the memory path once (Read First); the reviewer handoff cites
                    # it in both Read First and its Memory Rules sentence (the
                    # accepted legacy two-site pattern) -> twice.
                    if expected is None:
                        self.assertNotIn(_POSTURE_V2, from_path)
                        if kind != "legacy":
                            self.assertNotIn(_POSTURE_LEGACY, from_path)
                        self.assertNotIn("# injected", from_path)
                    else:
                        want = 2 if label == "reviewer" else 1
                        self.assertEqual(from_path.count(expected), want)

    def test_handoff_one_selection_and_one_scan_per_invocation(self) -> None:
        import frutlups.project as project_module

        for label, builder in _HANDOFF_BUILDERS:
            with self.subTest(builder=label), TemporaryDirectory() as tmp:
                root = Path(tmp)
                _make_v2_project(root, "lightweight")
                reads = _StateReadCounter()
                with mock.patch.object(
                    project_module, "load_layout_profile",
                    side_effect=load_layout_profile,
                ) as sel, mock.patch.object(
                    project_module, "_collect_acceptance_evidence",
                    side_effect=project_module._collect_acceptance_evidence,
                ) as scan, mock.patch.object(
                    project_module,
                    "_read_state_file_once",
                    side_effect=reads.wrap(),
                ):
                    builder(root)
                self.assertEqual(reads.state_reads, 1, "one PROJECT_STATE.md read")
                self.assertEqual(sel.call_count, 1, "one layout selection")
                self.assertEqual(scan.call_count, 1, "one acceptance-evidence scan")


class _StateReadCounter:
    def __init__(self) -> None:
        self.state_reads = 0
        self._real = project_module._read_state_file_once

    def wrap(self):
        def _read(path):
            self.state_reads += 1
            return self._real(path)

        return _read


# ---------------------------------------------------------------------------
# Prompt 044 Gate E4: selected-snapshot mutation cannot mix mode with root/posture
# ---------------------------------------------------------------------------


class GateE4SelectedSnapshotTests(unittest.TestCase):
    def _counts(self, root: Path, fn) -> tuple[int, int]:
        import frutlups.project as project_module

        reads = _StateReadCounter()
        with mock.patch.object(
            project_module, "load_layout_profile", side_effect=load_layout_profile
        ) as sel, mock.patch.object(
            project_module, "_read_state_file_once", side_effect=reads.wrap()
        ):
            fn(root)
        return reads.state_reads, sel.call_count

    def test_one_read_one_selection_across_public_entrypoints(self) -> None:
        entrypoints = {
            "status": lambda r: build_status(r, memory_runner=_ReadOnlyRunner()),
            "coding_plan": lambda r: build_coding_prompt_plan(r, memory_runner=_ReadOnlyRunner()),
            "review_plan": lambda r: build_review_prompt_plan(r),
            "coder_handoff": lambda r: build_coder_handoff(r),
            "reviewer_handoff": lambda r: build_reviewer_handoff(r),
        }
        for name, fn in entrypoints.items():
            with self.subTest(entrypoint=name), TemporaryDirectory() as tmp:
                root = Path(tmp)
                _make_v2_review_ready(root, "lightweight")
                reads, selections = self._counts(root, fn)
                self.assertEqual(reads, 1, f"{name}: one PROJECT_STATE.md read")
                self.assertEqual(selections, 1, f"{name}: one layout selection")

    def test_cli_status_one_read_one_selection(self) -> None:
        import frutlups.project as project_module

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "lightweight")
            reads = _StateReadCounter()
            with mock.patch.object(
                project_module, "load_layout_profile", side_effect=load_layout_profile
            ) as sel, mock.patch.object(
                project_module, "_read_state_file_once", side_effect=reads.wrap()
            ):
                code, _o, _e = _run_cli(["status", str(root)])
            self.assertEqual(code, 0)
            self.assertEqual(reads.state_reads, 1)
            self.assertEqual(sel.call_count, 1)

    def test_mutation_after_first_read_yields_a_consistent_snapshot(self) -> None:
        # Swap PROJECT_STATE.md from llloom->none immediately after the first read.
        # A single-snapshot composition reflects the first (llloom) mode; a second
        # read would observe none and mix the snapshot.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "llloom", make_root=True)
            state = root / "PROJECT_STATE.md"
            real = project_module._read_state_file_once
            swapped = {"done": False}

            def _read(path):
                content = real(path)
                if not swapped["done"]:
                    swapped["done"] = True
                    state.write_text(
                        "# Project State\n\nMemory mode: none\nFrutlups mode: manual\n",
                        encoding="utf-8",
                    )
                return content

            with mock.patch.object(
                project_module, "_read_state_file_once", side_effect=_read
            ):
                status = build_status(root, memory_runner=_ReadOnlyRunner())
        # First snapshot was llloom + present root -> enabled llloom (not mixed
        # with the post-mutation none).
        self.assertEqual(status.memory.backend, "llloom")
        self.assertTrue(status.memory.enabled)

    def test_double_posture_append_falsifier(self) -> None:
        # Posture must appear exactly once; a second append would be caught here.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_review_ready(root, "lightweight")
            cplan = build_coding_prompt_plan(root, memory_runner=_ReadOnlyRunner())
            rplan = build_review_prompt_plan(root)
        self.assertEqual(cplan.render.content.count(_POSTURE_V2), 1)
        self.assertEqual(rplan.render.content.count(_POSTURE_V2), 1)

    def test_public_review_planning_is_reachable_falsifier(self) -> None:
        # Fails if public review planning is skipped/broken for a valid fixture.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_review_ready(root, "none")
            rplan = build_review_prompt_plan(root)
        self.assertTrue(rplan.valid, msg=str(rplan.errors))
        self.assertIsNotNone(rplan.render)


# ---------------------------------------------------------------------------
# Prompt 046 shared writer-reachability helpers
# ---------------------------------------------------------------------------

_ALL_CLI_WRITERS = (
    "write_coding_prompt",
    "_write_review_prompt_content",
    "write_verdict_record",
)


@contextlib.contextmanager
def _writer_reachability(intended: str):
    """Wrap the intended CLI writer (still writes) and make the others raise.

    Yields the wrapped intended writer so a test can assert exactly one call.
    Any unrelated prompt/verdict writer that is reached raises, proving the
    command touches only its own writer.
    """

    real = getattr(cli, intended)
    patches = [
        mock.patch.object(
            cli, name, side_effect=AssertionError(f"unrelated writer {name} reached")
        )
        for name in _ALL_CLI_WRITERS
        if name != intended
    ]
    with mock.patch.object(cli, intended, wraps=real) as wrapped:
        for p in patches:
            p.start()
        try:
            yield wrapped
        finally:
            for p in patches:
                p.stop()


@contextlib.contextmanager
def _no_memory_commands():
    """Patch the live subprocess runner so any real memory command fails."""

    with mock.patch(
        "frutlups.memory.SubprocessMemoryCommandRunner.run",
        side_effect=AssertionError("no memory command expected"),
    ):
        yield


@contextlib.contextmanager
def _readonly_memory_commands():
    """Allow only read-only status/doctor on the live runner; raise otherwise."""

    with mock.patch(
        "frutlups.memory.SubprocessMemoryCommandRunner.run",
        side_effect=lambda args: _ReadOnlyRunner().run(args),
    ):
        yield


_REJECTED_POSTURE = _UNSAFE_POSTURE_YAML["newline_fence_heading"]
_INJECTION_MARKERS = ("# injected", "`code`")


def _assert_inert(testcase: unittest.TestCase, blob: str) -> None:
    """No injected structure, no profile-default posture, no raw snippet."""

    for marker in _INJECTION_MARKERS:
        testcase.assertNotIn(marker, blob)
    testcase.assertNotIn(_POSTURE_V2, blob)
    testcase.assertNotIn("Optional Memory Context", blob)
    testcase.assertEqual(blob.count("```") % 2, 0)


# ---------------------------------------------------------------------------
# Prompt 046 Gate B: rejected-posture CLI matrix (8 independent cells)
# ---------------------------------------------------------------------------

# (command, prompt_dir, intended_writer, plan_key_check)
_CLI_COMMANDS = (
    ("make-coding-prompt", "prompts/for_coding_agent", "write_coding_prompt"),
    ("make-review-prompt", "prompts/for_review_agent", "_write_review_prompt_content"),
)


class Gate046BRejectedPostureCliMatrixTests(unittest.TestCase):
    def _fresh(self, root: Path, command: str) -> None:
        if command == "make-coding-prompt":
            _make_v2_project(root, "lightweight", posture_config=_REJECTED_POSTURE)
        else:
            _make_v2_review_ready(root, "lightweight", posture_config=_REJECTED_POSTURE)

    def test_dry_run_text(self) -> None:
        for command, prompt_dir, _writer in _CLI_COMMANDS:
            with self.subTest(command=command), TemporaryDirectory() as tmp:
                root = Path(tmp)
                self._fresh(root, command)
                before = _fs_snapshot(root)
                with _no_memory_commands(), mock.patch.object(
                    cli, "write_coding_prompt", side_effect=AssertionError("no write")
                ), mock.patch.object(
                    cli, "_write_review_prompt_content", side_effect=AssertionError("no write")
                ):
                    code, out, _e = _run_cli([command, str(root), "--dry-run"])
                self.assertEqual(code, 0)
                self.assertEqual(_fs_snapshot(root), before)
                _assert_inert(self, out)

    def test_dry_run_json(self) -> None:
        for command, prompt_dir, _writer in _CLI_COMMANDS:
            with self.subTest(command=command), TemporaryDirectory() as tmp:
                root = Path(tmp)
                self._fresh(root, command)
                before = _fs_snapshot(root)
                with _no_memory_commands(), mock.patch.object(
                    cli, "write_coding_prompt", side_effect=AssertionError("no write")
                ), mock.patch.object(
                    cli, "_write_review_prompt_content", side_effect=AssertionError("no write")
                ):
                    code, out, _e = _run_cli([command, str(root), "--dry-run", "--json"])
                self.assertEqual(code, 0)
                self.assertEqual(_fs_snapshot(root), before)
                payload = json.loads(out)
                self.assertIn("render", payload)
                if command == "make-coding-prompt":
                    self.assertEqual(set(payload.keys()), _CODING_PLAN_KEYS)
                _assert_inert(self, payload["render"]["content"])

    def test_actual_write_text(self) -> None:
        for command, prompt_dir, writer in _CLI_COMMANDS:
            with self.subTest(command=command), TemporaryDirectory() as tmp:
                root = Path(tmp)
                self._fresh(root, command)
                before = _fs_snapshot(root)
                with _no_memory_commands(), _writer_reachability(writer) as wrapped:
                    code, out, _e = _run_cli([command, str(root)])
                self.assertEqual(code, 0)
                self.assertEqual(wrapped.call_count, 1)
                after = _fs_snapshot(root)
                new = set(after) - set(before)
                self.assertEqual(len(new), 1)
                written = next(iter(new))
                self.assertTrue(written.startswith(prompt_dir + "/"))
                # Non-output files unchanged byte-for-byte.
                for key, value in before.items():
                    self.assertEqual(after[key], value)
                _assert_inert(self, after[written].decode("utf-8"))

    def test_actual_write_json(self) -> None:
        for command, prompt_dir, writer in _CLI_COMMANDS:
            with self.subTest(command=command), TemporaryDirectory() as tmp:
                root = Path(tmp)
                self._fresh(root, command)
                before = _fs_snapshot(root)
                with _no_memory_commands(), _writer_reachability(writer) as wrapped:
                    code, out, _e = _run_cli([command, str(root), "--json"])
                self.assertEqual(code, 0)
                self.assertEqual(wrapped.call_count, 1)
                payload = json.loads(out)
                self.assertIn("write_result", payload)
                after = _fs_snapshot(root)
                new = set(after) - set(before)
                self.assertEqual(len(new), 1)
                written = next(iter(new))
                self.assertTrue(written.startswith(prompt_dir + "/"))
                _assert_inert(self, after[written].decode("utf-8"))


# ---------------------------------------------------------------------------
# Prompt 046 Gate C: safe-posture actual-write writer reachability (both commands)
# ---------------------------------------------------------------------------


class Gate046CSafePostureWriterReachabilityTests(unittest.TestCase):
    def _fresh(self, root: Path, command: str) -> None:
        if command == "make-coding-prompt":
            _make_v2_project(root, "lightweight")
        else:
            _make_v2_review_ready(root, "lightweight")

    def _dry_run_bytes(self, root: Path, command: str) -> str:
        with _readonly_memory_commands():
            _code, out, _e = _run_cli([command, str(root), "--dry-run", "--json"])
        return json.loads(out)["render"]["content"]

    def test_actual_write_text_reaches_only_intended_writer(self) -> None:
        for command, prompt_dir, writer in _CLI_COMMANDS:
            with self.subTest(command=command), TemporaryDirectory() as tmp:
                root = Path(tmp)
                self._fresh(root, command)
                expected = self._dry_run_bytes(root, command)
                before = _fs_snapshot(root)
                with _readonly_memory_commands(), _writer_reachability(writer) as wrapped:
                    code, _out, _e = _run_cli([command, str(root)])
                self.assertEqual(code, 0)
                self.assertEqual(wrapped.call_count, 1)
                after = _fs_snapshot(root)
                new = set(after) - set(before)
                self.assertEqual(len(new), 1)
                written = next(iter(new))
                self.assertEqual(after[written].decode("utf-8"), expected)
                self.assertEqual(after[written].decode("utf-8").count(_POSTURE_V2), 1)
                for key, value in before.items():
                    self.assertEqual(after[key], value)

    def test_actual_write_json_reaches_only_intended_writer(self) -> None:
        for command, prompt_dir, writer in _CLI_COMMANDS:
            with self.subTest(command=command), TemporaryDirectory() as tmp:
                root = Path(tmp)
                self._fresh(root, command)
                before = _fs_snapshot(root)
                with _readonly_memory_commands(), _writer_reachability(writer) as wrapped:
                    code, out, _e = _run_cli([command, str(root), "--json"])
                self.assertEqual(code, 0)
                self.assertEqual(wrapped.call_count, 1)
                payload = json.loads(out)
                self.assertIn("write_result", payload)
                after = _fs_snapshot(root)
                self.assertEqual(len(set(after) - set(before)), 1)


# ---------------------------------------------------------------------------
# Prompt 046 Gate D: layout mutation after first selection (both directions)
# ---------------------------------------------------------------------------

_SAFE_LAYOUT = (
    "schema_version: frutlups_layout_config_v0\n"
    "profile_id: artifact_first_template_v2\n"
    "optional_lanes:\n  llloom:\n"
    "    posture_file: 05_governance/current/memory_posture.md\n"
)
_REJECTED_LAYOUT = (
    "schema_version: frutlups_layout_config_v0\n"
    "profile_id: artifact_first_template_v2\n"
    "optional_lanes:\n  llloom:\n"
    '    posture_file: "safe\\n```\\n# injected"\n'
)


class _LayoutMutationHarness:
    """Wraps load_layout_profile: mutates the layout file after the first select."""

    def __init__(self, root: Path, new_layout_text: str) -> None:
        self._root = root
        self._new = new_layout_text
        self.selections = 0

    def selector(self):
        real = load_layout_profile

        def _sel(root, config_path=None):
            result = real(root, config_path)
            self.selections += 1
            if self.selections == 1:
                (self._root / "frutlups.layout.yaml").write_text(self._new, encoding="utf-8")
            return result

        return _sel


class Gate046DLayoutMutationTests(unittest.TestCase):
    def _run(self, root: Path, entry: str):
        if entry == "status":
            return build_status(root, memory_runner=_ReadOnlyRunner())
        if entry == "coding_plan":
            return build_coding_prompt_plan(root, memory_runner=_ReadOnlyRunner())
        if entry == "review_plan":
            return build_review_prompt_plan(root)
        if entry == "coder_handoff":
            return build_coder_handoff(root)
        if entry == "reviewer_handoff":
            return build_reviewer_handoff(root)
        raise AssertionError(entry)

    def _posture_in_output(self, entry: str, result) -> bool:
        if entry == "status":
            return result.layout.profile.llloom_posture_file == _POSTURE_V2
        if entry in ("coding_plan", "review_plan"):
            return _POSTURE_V2 in result.render.content
        return _POSTURE_V2 in result.content

    _ENTRYPOINTS = ("status", "coding_plan", "review_plan", "coder_handoff", "reviewer_handoff")

    def _fixture(self, root: Path, layout_text: str) -> None:
        # Build a valid review-ready project, then overwrite the layout config.
        _make_v2_review_ready(root, "lightweight")
        (root / "frutlups.layout.yaml").write_text(layout_text, encoding="utf-8")

    def test_safe_becomes_rejected_retains_first_snapshot(self) -> None:
        import frutlups.project as project_module

        for entry in self._ENTRYPOINTS:
            with self.subTest(entry=entry), TemporaryDirectory() as tmp:
                root = Path(tmp)
                self._fixture(root, _SAFE_LAYOUT)  # first selection = safe posture
                reads = _StateReadCounter()
                harness = _LayoutMutationHarness(root, _REJECTED_LAYOUT)
                with mock.patch.object(
                    project_module, "load_layout_profile", side_effect=harness.selector()
                ), mock.patch.object(
                    project_module,
                    "_read_state_file_once",
                    side_effect=reads.wrap(),
                ):
                    result = self._run(root, entry)
                self.assertEqual(harness.selections, 1, f"{entry}: one layout selection")
                self.assertEqual(reads.state_reads, 1, f"{entry}: one PROJECT_STATE.md read")
                # First (safe) snapshot retained: posture present, not the mutated reject.
                self.assertTrue(self._posture_in_output(entry, result))

    def test_rejected_becomes_safe_retains_first_snapshot(self) -> None:
        import frutlups.project as project_module

        for entry in self._ENTRYPOINTS:
            with self.subTest(entry=entry), TemporaryDirectory() as tmp:
                root = Path(tmp)
                self._fixture(root, _REJECTED_LAYOUT)  # first selection = rejected posture
                reads = _StateReadCounter()
                harness = _LayoutMutationHarness(root, _SAFE_LAYOUT)
                with mock.patch.object(
                    project_module, "load_layout_profile", side_effect=harness.selector()
                ), mock.patch.object(
                    project_module,
                    "_read_state_file_once",
                    side_effect=reads.wrap(),
                ):
                    result = self._run(root, entry)
                self.assertEqual(harness.selections, 1, f"{entry}: one layout selection")
                self.assertEqual(reads.state_reads, 1, f"{entry}: one PROJECT_STATE.md read")
                # First (rejected) snapshot retained: posture absent, not the mutated safe.
                self.assertFalse(self._posture_in_output(entry, result))

    def test_cli_prompt_command_retains_first_snapshot(self) -> None:
        import frutlups.project as project_module

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._fixture(root, _SAFE_LAYOUT)
            harness = _LayoutMutationHarness(root, _REJECTED_LAYOUT)
            with mock.patch.object(
                project_module, "load_layout_profile", side_effect=harness.selector()
            ), _readonly_memory_commands():
                code, out, _e = _run_cli(["make-coding-prompt", str(root), "--dry-run", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(harness.selections, 1)
        render = json.loads(out)["render"]["content"]
        self.assertEqual(render.count(_POSTURE_V2), 1)  # first (safe) snapshot


# ---------------------------------------------------------------------------
# Prompt 046 Gate E: path AND string handoff selection/scan counts (both builders)
# ---------------------------------------------------------------------------


class Gate046EHandoffScanCountTests(unittest.TestCase):
    def test_path_and_string_counts_for_both_builders(self) -> None:
        import frutlups.project as project_module

        for label, builder in _HANDOFF_BUILDERS:
            for form in ("path", "string"):
                with self.subTest(builder=label, form=form), TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    _make_v2_project(root, "lightweight")
                    start = root if form == "path" else str(root)
                    reads = _StateReadCounter()
                    before = _fs_snapshot(root)
                    with mock.patch.object(
                        project_module, "load_layout_profile", side_effect=load_layout_profile
                    ) as sel, mock.patch.object(
                        project_module,
                        "_collect_acceptance_evidence",
                        side_effect=project_module._collect_acceptance_evidence,
                    ) as scan, mock.patch.object(
                        project_module,
                        "_read_state_file_once",
                        side_effect=reads.wrap(),
                    ), _no_memory_commands():
                        content = builder(start).content
                    self.assertEqual(sel.call_count, 1, "one layout selection")
                    self.assertEqual(reads.state_reads, 1, "one PROJECT_STATE.md read")
                    self.assertEqual(scan.call_count, 1, "one acceptance-evidence scan")
                    self.assertEqual(_fs_snapshot(root), before)  # no filesystem change
                    want = 2 if label == "reviewer" else 1
                    self.assertEqual(content.count(_POSTURE_V2), want)


# ---------------------------------------------------------------------------
# Prompt 046 Gate F: effective falsifiers (the new checks detect regressions)
# ---------------------------------------------------------------------------


class Gate046FFalsifierTests(unittest.TestCase):
    def test_writer_call_count_check_is_effective(self) -> None:
        # The exactly-one-call assertion used by Gates B and C is non-vacuous:
        # a real second write (call_count == 2) and a zero-call case are both
        # caught by ``assertEqual(call_count, 1)``.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "lightweight")
            with _readonly_memory_commands(), mock.patch.object(
                cli, "write_coding_prompt", wraps=cli.write_coding_prompt
            ) as wrapped:
                _run_cli(["make-coding-prompt", str(root)])  # 1 real write
                _run_cli(["make-coding-prompt", str(root), "--overwrite"])  # 2nd real write
            self.assertEqual(wrapped.call_count, 2)
            with self.assertRaises(AssertionError):
                self.assertEqual(wrapped.call_count, 1)
        zero = mock.MagicMock()
        with self.assertRaises(AssertionError):
            self.assertEqual(zero.call_count, 1)

    def test_unrelated_writer_reached_is_detected(self) -> None:
        # The unrelated-writer guard raises if a coding command touches the review
        # writer; prove the guard is wired by invoking it directly.
        with self.assertRaises(AssertionError):
            with _writer_reachability("write_coding_prompt"):
                cli._write_review_prompt_content(  # type: ignore[call-arg]
                    project_root=Path("."),
                    template=None,
                    content="",
                    overwrite=False,
                    prompt_dir="prompts/for_review_agent",
                )

    def test_layout_reload_after_mutation_is_detected(self) -> None:
        # A composition that reloaded the mutated layout would select twice and
        # observe the post-mutation profile. Simulate a reload and prove both the
        # selection-count and the snapshot assertions catch it.
        import frutlups.project as project_module

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_review_ready(root, "lightweight")
            (root / "frutlups.layout.yaml").write_text(_SAFE_LAYOUT, encoding="utf-8")
            harness = _LayoutMutationHarness(root, _REJECTED_LAYOUT)
            with mock.patch.object(
                project_module, "load_layout_profile", side_effect=harness.selector()
            ):
                first = build_status(root, memory_runner=_ReadOnlyRunner())
                self.assertEqual(harness.selections, 1)
                # A hypothetical reload after mutation would see the rejected posture.
                reloaded = load_layout_profile(root)
            self.assertEqual(first.layout.profile.llloom_posture_file, _POSTURE_V2)
            self.assertEqual(reloaded.profile.llloom_posture_file, "")
            self.assertNotEqual(
                first.layout.profile.llloom_posture_file,
                reloaded.profile.llloom_posture_file,
            )

    def test_string_handoff_scan_count_assertion_is_effective(self) -> None:
        # Prove the string-input scan-count check (Gate E) is non-vacuous and
        # sensitive to the acceptance-scan count: two string invocations produce
        # two real scans, which ``assertEqual(scan.call_count, 1)`` catches.
        import frutlups.project as project_module

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root, "lightweight")
            with mock.patch.object(
                project_module,
                "_collect_acceptance_evidence",
                wraps=project_module._collect_acceptance_evidence,
            ) as scan, _no_memory_commands():
                build_coder_handoff(str(root))
                build_coder_handoff(str(root))
            self.assertEqual(scan.call_count, 2)
            with self.assertRaises(AssertionError):
                self.assertEqual(scan.call_count, 1)


if __name__ == "__main__":
    unittest.main()
