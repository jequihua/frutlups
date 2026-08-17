"""Regression coverage for terminal accepted-slice rework declarations."""

from __future__ import annotations

import hashlib
import json
import shutil
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import FrozenInstanceError
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import get_type_hints
from unittest import mock

from test_configured_prompt_scaffold import (
    _SR_HEADINGS,
    _make_v2_review_project,
    _write_scaffolds,
)
from test_layout import _review_template as _legacy_review_template
from test_make_review_prompt import _minimal_self_report
from test_planning_frontier import _completed_project, _fresh_project
from test_resumable_status import _write_review_report, _write_self_report

import frutlups
import frutlups.project as project_module
from frutlups.cli import main
from frutlups.gate import build_planning_frontier_status
from frutlups.project import (
    PLANNING_FRONTIER_CONTRACT_VERSION,
    _review_output_path_from_prompt,
    build_loop_resume_status,
    build_review_prompt_plan,
    build_rework_declaration_plan,
    build_status,
)
from frutlups.review_prompt_template import render_review_prompt
from frutlups.rework import (
    MAX_REWORK_DECLARATIONS,
    REWORK_DECLARATION_COUNT_EXHAUSTED,
    ReworkDeclaration,
    ReworkDeclarationPlan,
    ReworkDeclarationWriteCommand,
    declaration_path,
    load_rework_declarations,
    render_rework_declaration,
    write_rework_declaration,
)


def _write_declaration(
    root: Path,
    *,
    sequence: int = 1,
    pass_id: str = "holistic_pass_001",
    baseline_prompt_sequence: int = 0,
    slice_ids: tuple[str, ...] = ("M001-S01",),
) -> Path:
    directory = root / "05_governance" / "rework_declarations"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{sequence:03d}_{pass_id}.json"
    path.write_text(
        json.dumps(
            {
                "contract_id": "frutlups.rework_declaration",
                "contract_version": "1",
                "declaration_sequence": sequence,
                "pass_id": pass_id,
                "baseline_prompt_sequence": baseline_prompt_sequence,
                "slice_ids": list(slice_ids),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _run(args: list[str]) -> tuple[int, str, str]:
    out = StringIO()
    err = StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(args)
    return code, out.getvalue(), err.getvalue()


def _status(root: Path) -> dict:
    code, out, err = _run(["status", str(root), "--json"])
    if code != 0:
        raise AssertionError(err)
    return json.loads(out)


def _complete_fresh_chain(root: Path, verdict: str = "pass") -> str:
    """Drive the currently reopened slice through one fresh verdict receipt."""

    code, out, err = _run(["make-coding-prompt", str(root), "--json"])
    if code != 0:
        raise AssertionError(err or out)
    coding = json.loads(out)
    coding_target = coding["write_result"]["target_path"]
    marker = coding_target.split("_rework_", 1)[1].removesuffix(".md")

    live = _status(root)["loop_resume"]
    if live["step"] != "execute_coding_prompt":
        raise AssertionError(live)
    _write_self_report(
        root,
        live["self_report_path"],
        _minimal_self_report(),
    )

    code, out, err = _run(["make-review-prompt", str(root), "--json"])
    if code != 0:
        raise AssertionError(err or out)
    review = json.loads(out)
    if marker not in review["write_result"]["target_path"]:
        raise AssertionError(review)

    live = _status(root)["loop_resume"]
    if live["step"] != "execute_review_prompt":
        raise AssertionError(live)
    report_rel = live["review_report_path"]
    _write_review_report(root, report_rel.rsplit("/", 1)[-1], verdict)

    live = _status(root)["loop_resume"]
    if verdict == "needs_work":
        if live["step"] != "make_coding_prompt":
            raise AssertionError(live)
        return marker
    if live["step"] != "record_verdict":
        raise AssertionError(live)
    code, out, err = _run(
        [
            "record-verdict",
            str(root),
            "--review-report",
            str(root / report_rel),
            "--json",
        ]
    )
    if code != 0:
        raise AssertionError(err or out)
    return marker


def _configured_self_report() -> str:
    """A minimal valid report for the selected template-v2 schema."""

    parts = [
        f"{heading}\n\n"
        + ("- 08_pkg/src/frutlups/x.py\n" if heading == "Files Changed:" else "x\n")
        for heading in _SR_HEADINGS
    ]
    return "# Coder Self-Report\n\n" + "\n".join(parts)


def _configured_rework_project(root: Path) -> None:
    """Completed history plus the configured shape and a sequence-014 tail."""

    _completed_project(root, closure=True)
    _write_scaffolds(root)
    (root / "frutlups.layout.yaml").write_text(
        "schema_version: frutlups_layout_config_v0\n"
        "profile_id: artifact_first_template_v2\n"
        "automation_boundary:\n"
        "  runner_implemented: true\n",
        encoding="utf-8",
    )
    (root / "prompts" / "for_coding_agent" / "014_frutlups_m001_s01_prior.md").write_text(
        "# Coding Prompt 014: M001-S01 prior acceptance\n\n"
        "Workflow metadata:\n\n"
        "```yaml\n"
        "milestone: M001\n"
        "slice: M001-S01\n"
        "title: prior acceptance\n"
        "role: coder\n"
        "```\n",
        encoding="utf-8",
    )
    (root / "prompts" / "for_review_agent" / "014_frutlups_m001_s01_prior.md").write_text(
        "# Review Prompt 014: M001-S01 prior acceptance\n\n"
        "Workflow metadata:\n\n"
        "```yaml\n"
        "milestone: M001\n"
        "slice: M001-S01\n"
        "title: prior acceptance\n"
        "role: reviewer\n"
        "```\n\n"
        "## Read First\n\n"
        "- coding prompt under review: "
        "`prompts/for_coding_agent/014_frutlups_m001_s01_prior.md`\n",
        encoding="utf-8",
    )


def _advance_configured_rework_to_review(root: Path) -> tuple[str, Path]:
    """Declare rework and create its sequence-015 configured review prompt."""

    code, out, err = _run(
        [
            "declare-rework",
            str(root),
            "--pass-id",
            "holistic_pass_001",
            "--slice",
            "M001-S01",
            "--json",
        ]
    )
    if code != 0:
        raise AssertionError(err or out)
    declaration = json.loads(out)["declaration"]
    if declaration["baseline_prompt_sequence"] != 14:
        raise AssertionError(declaration)

    code, out, err = _run(["make-coding-prompt", str(root), "--json"])
    if code != 0:
        raise AssertionError(err or out)
    coding = json.loads(out)
    if coding["template"]["sequence"] != 15:
        raise AssertionError(coding)
    self_report = root / coding["template"]["self_report_path"]
    self_report.parent.mkdir(parents=True, exist_ok=True)
    self_report.write_text(_configured_self_report(), encoding="utf-8")

    code, out, err = _run(["make-review-prompt", str(root), "--json"])
    if code != 0:
        raise AssertionError(err or out)
    review = json.loads(out)
    review_path = root / review["write_result"]["target_path"]
    return review["coding_prompt_meta"]["review_output_path"], review_path


# ---------------------------------------------------------------------------
# Declaration-count boundary fixtures (Review 052 P1).
#
# The boundary is the real ``MAX_REWORK_DECLARATIONS`` value, never a smaller
# test-only constant, so the at-limit history has to be produced by genuinely
# driving the public lifecycle. That drive is expensive, so it runs once per
# module and every cell works on its own ``copytree`` of the result: each CLI
# cell still gets a fresh project and makes exactly one invocation, so no
# earlier call can supply another cell's evidence.
# ---------------------------------------------------------------------------

_BOUNDARY_TMP: TemporaryDirectory | None = None
_BOUNDARY_ROOTS: dict[int, Path] = {}


def _snapshot(root: Path) -> dict[str, str]:
    """Complete relative-path -> directory-marker-or-digest snapshot.

    Every path under ``root`` is represented, so a deletion or an in-place
    modification is as visible as an addition.
    """

    return {
        str(path.relative_to(root)).replace("\\", "/"): (
            "dir" if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest()
        )
        for path in sorted(root.rglob("*"))
    }


def _fast_pass(root: Path, pass_id: str) -> None:
    """One real declaration plus its complete fresh evidence chain."""

    code, out, err = _run(
        ["declare-rework", str(root), "--pass-id", pass_id, "--slice", "M001-S01", "--json"]
    )
    if code != 0:
        raise AssertionError(err or out)
    code, out, err = _run(["make-coding-prompt", str(root), "--json"])
    if code != 0:
        raise AssertionError(err or out)
    self_report_rel = json.loads(out)["template"]["self_report_path"]
    _write_self_report(root, self_report_rel, _minimal_self_report())
    code, out, err = _run(["make-review-prompt", str(root), "--json"])
    if code != 0:
        raise AssertionError(err or out)
    report_rel = json.loads(out)["coding_prompt_meta"]["review_output_path"]
    _write_review_report(root, report_rel.rsplit("/", 1)[-1], "pass")
    code, out, err = _run(
        ["record-verdict", str(root), "--review-report", str(root / report_rel), "--json"]
    )
    if code != 0:
        raise AssertionError(err or out)


def _boundary_source(count: int) -> Path:
    """A cached completed project holding exactly ``count`` declarations."""

    global _BOUNDARY_TMP
    if count in _BOUNDARY_ROOTS:
        return _BOUNDARY_ROOTS[count]
    if _BOUNDARY_TMP is None:
        _BOUNDARY_TMP = TemporaryDirectory()
    base = Path(_BOUNDARY_TMP.name)
    below = MAX_REWORK_DECLARATIONS - 1
    if count == below:
        root = base / f"declarations_{below}"
        _completed_project(root, closure=True)
        for index in range(1, below + 1):
            _fast_pass(root, f"pass_{index:03d}")
    elif count == MAX_REWORK_DECLARATIONS:
        root = base / f"declarations_{MAX_REWORK_DECLARATIONS}"
        shutil.copytree(_boundary_source(below), root)
        _fast_pass(root, f"pass_{MAX_REWORK_DECLARATIONS:03d}")
    else:  # pragma: no cover - defensive
        raise AssertionError(f"unsupported boundary fixture size {count}")
    inventory = load_rework_declarations(root)
    if not inventory.valid or len(inventory.declarations) != count:
        raise AssertionError(
            f"boundary fixture {count} is not a valid inventory: "
            f"valid={inventory.valid} n={len(inventory.declarations)}"
        )
    if build_planning_frontier_status(root).outcome != "complete":
        raise AssertionError(f"boundary fixture {count} did not reach a complete frontier")
    _BOUNDARY_ROOTS[count] = root
    return root


def _boundary_project(stack: object, count: int) -> Path:
    """A fresh disposable copy of the cached ``count``-declaration project."""

    tmp = TemporaryDirectory()
    stack.addCleanup(tmp.cleanup)  # type: ignore[attr-defined]
    root = Path(tmp.name) / "project"
    shutil.copytree(_boundary_source(count), root)
    return root


def tearDownModule() -> None:
    global _BOUNDARY_TMP
    _BOUNDARY_ROOTS.clear()
    if _BOUNDARY_TMP is not None:
        _BOUNDARY_TMP.cleanup()
        _BOUNDARY_TMP = None


class TerminalReworkRegressionTests(unittest.TestCase):
    def test_valid_declaration_reopens_completed_accepted_slice(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root, closure=True)
            self.assertEqual(build_planning_frontier_status(root).outcome, "complete")

            _write_declaration(root)

            frontier = build_planning_frontier_status(root)
            resume = build_loop_resume_status(root)

        self.assertEqual(frontier.outcome, "ready")
        self.assertEqual(resume.step.value, "make_coding_prompt")
        self.assertEqual(resume.frontier_slice_id, "M001-S01")

    def test_absent_declaration_preserves_terminal_contract_and_history(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root, closure=True)
            before = build_status(root)
            payload = _status(root)

        self.assertEqual(payload["planning_frontier"]["outcome"], "complete")
        self.assertEqual(payload["loop_resume"]["step"], "no_frontier")
        self.assertEqual(before.accepted_slice_ids, ("M001-S01", "M001-S02"))
        self.assertNotIn("rework_declarations", payload)

    def test_cli_declaration_is_typed_canonical_and_append_only(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root, closure=True)
            args = [
                "declare-rework",
                str(root),
                "--pass-id",
                "holistic_pass_001",
                "--slice",
                "M001-S02",
                "--slice",
                "M001-S01",
                "--json",
            ]
            code, out, err = _run(args[:-1] + ["--dry-run", "--json"])
            self.assertEqual(code, 0, err)
            preview = json.loads(out)
            self.assertEqual(preview["declaration"]["slice_ids"], ["M001-S01", "M001-S02"])
            self.assertFalse((root / preview["target_path"]).exists())

            code, out, err = _run(args)
            self.assertEqual(code, 0, err)
            payload = json.loads(out)
            target = root / payload["target_path"]
            self.assertTrue(target.is_file())
            inventory = load_rework_declarations(root)
            self.assertTrue(inventory.valid)
            self.assertEqual(inventory.declarations[0].slice_ids, ("M001-S01", "M001-S02"))

            stale_plan = build_rework_declaration_plan(
                root,
                pass_id="another_pass",
                slice_ids=("M001-S01",),
            )
            # The active pass prevents a second declaration from overlapping it.
            self.assertFalse(stale_plan.valid)
            original_plan = payload["declaration"]
            self.assertEqual(original_plan["contract_id"], "frutlups.rework_declaration")

    def test_writer_refuses_replacement_without_changing_original_bytes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root, closure=True)
            plan = build_rework_declaration_plan(
                root,
                pass_id="holistic_pass_001",
                slice_ids=("M001-S01",),
            )
            command = ReworkDeclarationWriteCommand(project_root=root, plan=plan)
            first = write_rework_declaration(command)
            original = (root / plan.target_path).read_bytes()
            second = write_rework_declaration(command)
            after = (root / plan.target_path).read_bytes()

        self.assertTrue(first.wrote)
        self.assertFalse(second.wrote)
        self.assertIn("replacement is refused", second.errors[0])
        self.assertEqual(after, original)

    def test_unknown_unaccepted_and_noncanonical_declarations_fail_closed(self) -> None:
        cases = (
            (("M999-S99",), "rework_declaration_slice_unknown"),
            (("M001-S02", "M001-S01"), "rework_declaration_order_invalid"),
        )
        for slice_ids, diagnostic in cases:
            with self.subTest(diagnostic=diagnostic), TemporaryDirectory() as tmp:
                root = Path(tmp)
                _completed_project(root, closure=True)
                _write_declaration(root, slice_ids=slice_ids)
                payload = _status(root)
                self.assertEqual(payload["planning_frontier"]["outcome"], "invalid")
                self.assertIn(diagnostic, {item["code"] for item in payload["diagnostics"]})

    def test_extra_schema_field_is_rejected_with_bounded_diagnostic(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root, closure=True)
            path = _write_declaration(root)
            value = json.loads(path.read_text(encoding="utf-8"))
            value["comment"] = "not routing authority"
            path.write_text(json.dumps(value), encoding="utf-8")
            inventory = load_rework_declarations(root)

        self.assertFalse(inventory.valid)
        self.assertEqual(inventory.diagnostics[0].code, "rework_declaration_schema_invalid")
        self.assertLessEqual(len(inventory.diagnostics[0].message), 240)

    def test_declaration_of_historically_unaccepted_slice_fails_closed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fresh_project(root)
            _write_declaration(root, slice_ids=("M001-S01",))
            payload = _status(root)

        self.assertEqual(payload["planning_frontier"]["outcome"], "invalid")
        self.assertIn(
            "rework_declaration_slice_unaccepted",
            {item["code"] for item in payload["diagnostics"]},
        )

    def test_one_slice_requires_and_completes_a_fresh_evidence_chain(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root, closure=True)
            historical = build_status(root).accepted_slice_ids
            code, _, err = _run(
                [
                    "declare-rework",
                    str(root),
                    "--pass-id",
                    "holistic_pass_001",
                    "--slice",
                    "M001-S01",
                ]
            )
            self.assertEqual(code, 0, err)
            self.assertEqual(build_status(root).accepted_slice_ids, historical)

            marker = _complete_fresh_chain(root)
            payload = _status(root)

        self.assertIn("001_holistic_pass_001", marker)
        self.assertEqual(payload["loop_resume"]["step"], "no_frontier")
        self.assertEqual(payload["planning_frontier"]["outcome"], "complete")
        self.assertEqual(tuple(payload["accepted_slice_ids"]), historical)

    def test_needs_work_receipt_keeps_slice_open_until_a_later_pass(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root, closure=True)
            code, _, err = _run(
                [
                    "declare-rework",
                    str(root),
                    "--pass-id",
                    "holistic_pass_001",
                    "--slice",
                    "M001-S01",
                ]
            )
            self.assertEqual(code, 0, err)
            first_marker = _complete_fresh_chain(root, "needs_work")
            after_rejection = _status(root)
            self.assertEqual(after_rejection["loop_resume"]["step"], "make_coding_prompt")
            self.assertEqual(after_rejection["planning_frontier"]["outcome"], "ready")

            second_marker = _complete_fresh_chain(root, "pass")
            final = _status(root)

        self.assertNotEqual(first_marker, second_marker)
        self.assertEqual(final["planning_frontier"]["outcome"], "complete")

    def test_multiple_slices_reopen_and_complete_in_roadmap_order(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root, closure=True)
            code, _, err = _run(
                [
                    "declare-rework",
                    str(root),
                    "--pass-id",
                    "holistic_pass_002",
                    "--slice",
                    "M001-S02",
                    "--slice",
                    "M001-S01",
                ]
            )
            self.assertEqual(code, 0, err)
            self.assertEqual(_status(root)["next_slice"]["id"], "M001-S01")
            first_marker = _complete_fresh_chain(root)
            self.assertEqual(_status(root)["next_slice"]["id"], "M001-S02")
            second_marker = _complete_fresh_chain(root)
            payload = _status(root)

        self.assertNotEqual(first_marker, second_marker)
        self.assertEqual(payload["loop_resume"]["step"], "no_frontier")
        self.assertEqual(payload["planning_frontier"]["outcome"], "complete")

    def test_consecutive_passes_have_disjoint_prompt_sequence_windows(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root, closure=True)
            for pass_id in ("holistic_pass_001", "holistic_pass_002"):
                code, _, err = _run(
                    [
                        "declare-rework",
                        str(root),
                        "--pass-id",
                        pass_id,
                        "--slice",
                        "M001-S01",
                    ]
                )
                self.assertEqual(code, 0, err)
                _complete_fresh_chain(root)
                self.assertEqual(_status(root)["planning_frontier"]["outcome"], "complete")

            inventory = load_rework_declarations(root)
            payload = _status(root)

        self.assertEqual(len(inventory.declarations), 2)
        self.assertLess(
            inventory.declarations[0].baseline_prompt_sequence,
            inventory.declarations[1].baseline_prompt_sequence,
        )
        self.assertEqual(payload["loop_resume"]["step"], "no_frontier")


class ReviewOutputRecognitionTests(unittest.TestCase):
    """Q007: every emitted declaration clears, unknown shapes fail closed."""

    _DIAGNOSTIC_CODE = "rework_review_output_declaration_unrecognized"
    _DIAGNOSTIC_MESSAGE = (
        "paired rework review prompt has no recognizable review-output declaration"
    )

    def test_every_released_composer_shape_and_historical_section_are_recognized(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_review_project(root)
            configured = build_review_prompt_plan(root)
            self.assertTrue(configured.valid, configured.errors)
            configured_path = root / configured.preview.target_path
            configured_path.parent.mkdir(parents=True, exist_ok=True)
            configured_path.write_text(configured.render.content, encoding="utf-8")
            self.assertIn(
                f"- Write the review report at `{configured.template.review_output_path}`.",
                configured.render.content,
            )
            self.assertEqual(
                _review_output_path_from_prompt(configured_path),
                configured.template.review_output_path,
            )

            legacy_render = render_review_prompt(_legacy_review_template())
            self.assertTrue(legacy_render.valid, legacy_render.errors)
            legacy_path = root / "legacy_generated_review.md"
            legacy_path.write_text(legacy_render.content, encoding="utf-8")
            self.assertIn(
                f"Review output: `{_legacy_review_template().review_output_path}`",
                legacy_render.content,
            )
            self.assertEqual(
                _review_output_path_from_prompt(legacy_path),
                _legacy_review_template().review_output_path,
            )
            historical_inline_path = root / "historical_inline_placement.md"
            historical_inline_path.write_text(
                "# Hand-authored Review\n\n## Historical Notes\n\n"
                f"Review output: `{_legacy_review_template().review_output_path}`\n",
                encoding="utf-8",
            )
            self.assertEqual(
                _review_output_path_from_prompt(historical_inline_path),
                _legacy_review_template().review_output_path,
            )

            historical_path = root / "historical_review.md"
            historical_declared = "05_governance/reviews/historical_review_report.md"
            historical_path.write_text(
                "# Review\n\n## Review Output Location\n\n"
                f"`{historical_declared}`\n",
                encoding="utf-8",
            )
            self.assertEqual(
                _review_output_path_from_prompt(historical_path), historical_declared
            )

    def test_declaration_shape_precedence_is_stable(self) -> None:
        inline = "05_governance/reviews/inline_review_report.md"
        configured = "05_governance/reviews/configured_review_report.md"
        historical = "05_governance/reviews/historical_review_report.md"
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            all_shapes = root / "all.md"
            all_shapes.write_text(
                "# Review\n\n## Review Output Location\n\n"
                f"`{historical}`\n\n## Definition Of Done\n\n"
                f"- Write the review report at `{configured}`.\n\n## Pairing\n\n"
                f"Review output: `{inline}`\n",
                encoding="utf-8",
            )
            configured_and_historical = root / "configured_and_historical.md"
            configured_and_historical.write_text(
                "# Review\n\n## Review Output Location\n\n"
                f"`{historical}`\n\n## Definition Of Done\n\n"
                f"- Write the review report at `{configured}`.\n",
                encoding="utf-8",
            )
            self.assertEqual(_review_output_path_from_prompt(all_shapes), inline)
            self.assertEqual(
                _review_output_path_from_prompt(configured_and_historical), configured
            )

    def test_configured_sequence_015_lifecycle_clears_without_rewriting_history(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _configured_rework_project(root)
            historical = build_status(root).accepted_slice_ids
            historical_snapshot = _snapshot(root)

            report_rel, review_path = _advance_configured_rework_to_review(root)
            self.assertTrue(review_path.name.startswith("015_"), review_path.name)
            self.assertIn(
                "_rework_001_holistic_pass_001_015_review_report.md", report_rel
            )
            self.assertEqual(_review_output_path_from_prompt(review_path), report_rel)
            _write_review_report(root, report_rel.rsplit("/", 1)[-1], "pass")
            live = _status(root)["loop_resume"]
            self.assertEqual(live["step"], "record_verdict")
            code, out, err = _run(
                [
                    "record-verdict",
                    str(root),
                    "--review-report",
                    str(root / report_rel),
                    "--json",
                ]
            )
            self.assertEqual(code, 0, err or out)
            payload = _status(root)

            for rel, digest in historical_snapshot.items():
                self.assertEqual(_snapshot(root).get(rel), digest, rel)
        self.assertEqual(payload["loop_resume"]["step"], "no_frontier")
        self.assertEqual(payload["planning_frontier"]["outcome"], "complete")
        self.assertEqual(tuple(payload["accepted_slice_ids"]), historical)

    def test_unknown_paired_declaration_is_one_typed_invalid_diagnostic_and_pure(self) -> None:
        hostile = "SECRET_Q007_DO_NOT_ECHO"
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _configured_rework_project(root)
            _report_rel, review_path = _advance_configured_rework_to_review(root)
            content = review_path.read_text(encoding="utf-8")
            owned = next(
                line for line in content.splitlines() if "Write the review report at" in line
            )
            review_path.write_text(
                content.replace(
                    owned,
                    f"- Send unrelated evidence to `{hostile}_review_report.md`.",
                    1,
                ),
                encoding="utf-8",
            )
            before = _snapshot(root)
            with (
                mock.patch(
                    "frutlups.project._review_output_path_from_prompt",
                    wraps=project_module._review_output_path_from_prompt,
                ) as output_reader,
                mock.patch(
                    "frutlups.cli.write_coding_prompt",
                    side_effect=AssertionError("coding writer reached during status"),
                ),
                mock.patch(
                    "frutlups.cli.write_review_prompt",
                    side_effect=AssertionError("review writer reached during status"),
                ),
                mock.patch(
                    "frutlups.cli.write_verdict_record",
                    side_effect=AssertionError("verdict writer reached during status"),
                ),
                mock.patch(
                    "frutlups.cli.write_rework_declaration",
                    side_effect=AssertionError("declaration writer reached during status"),
                ),
            ):
                first = _status(root)
                second = _status(root)
            after = _snapshot(root)

        errors = [item for item in first["diagnostics"] if item["severity"] == "error"]
        self.assertEqual(
            [(item["code"], item["message"]) for item in errors],
            [(self._DIAGNOSTIC_CODE, self._DIAGNOSTIC_MESSAGE)],
        )
        self.assertEqual(first, second)
        self.assertEqual(output_reader.call_count, 2)
        self.assertEqual(first["planning_frontier"]["outcome"], "invalid")
        self.assertIn(
            self._DIAGNOSTIC_CODE,
            " ".join(first["planning_frontier"]["diagnostics"]),
        )
        self.assertEqual(after, before)
        self.assertLessEqual(len(errors[0]["message"]), 240)
        self.assertNotIn(hostile, errors[0]["message"])
        self.assertNotIn(str(root), errors[0]["message"])
        self.assertFalse(any("journal" in rel.lower() for rel in after))

    def test_missing_then_recognized_unreviewed_prompt_remain_ordinary_pending_work(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _configured_rework_project(root)
            code, out, err = _run(
                [
                    "declare-rework",
                    str(root),
                    "--pass-id",
                    "holistic_pass_001",
                    "--slice",
                    "M001-S01",
                    "--json",
                ]
            )
            self.assertEqual(code, 0, err or out)
            code, out, err = _run(["make-coding-prompt", str(root), "--json"])
            self.assertEqual(code, 0, err or out)
            coding = json.loads(out)
            self_report = root / coding["template"]["self_report_path"]
            self_report.parent.mkdir(parents=True, exist_ok=True)
            self_report.write_text(_configured_self_report(), encoding="utf-8")

            before_review = _status(root)
            self.assertEqual(before_review["loop_resume"]["step"], "make_review_prompt")
            self.assertEqual(before_review["planning_frontier"]["outcome"], "ready")
            self.assertNotIn(
                self._DIAGNOSTIC_CODE,
                {item["code"] for item in before_review["diagnostics"]},
            )

            code, out, err = _run(["make-review-prompt", str(root), "--json"])
            self.assertEqual(code, 0, err or out)
            after_review = _status(root)

        self.assertEqual(after_review["loop_resume"]["step"], "execute_review_prompt")
        self.assertEqual(after_review["planning_frontier"]["outcome"], "ready")
        self.assertNotIn(
            self._DIAGNOSTIC_CODE,
            {item["code"] for item in after_review["diagnostics"]},
        )

    def test_recognized_different_output_path_stays_conservatively_pending(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _configured_rework_project(root)
            report_rel, review_path = _advance_configured_rework_to_review(root)
            different = "05_governance/reviews/m001_s01_different_review_report.md"
            review_path.write_text(
                review_path.read_text(encoding="utf-8").replace(
                    f"`{report_rel}`", f"`{different}`", 1
                ),
                encoding="utf-8",
            )
            extracted = _review_output_path_from_prompt(review_path)
            payload = _status(root)

        self.assertEqual(extracted, different)
        self.assertEqual(payload["planning_frontier"]["outcome"], "ready")
        self.assertNotIn(
            self._DIAGNOSTIC_CODE,
            {item["code"] for item in payload["diagnostics"]},
        )

    def test_invalid_utf8_paired_prompt_fails_closed_with_the_owned_diagnostic(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _configured_rework_project(root)
            _report_rel, review_path = _advance_configured_rework_to_review(root)
            review_path.write_bytes(b"# Review\n\n\xff\xfe\n")
            payload = _status(root)

        errors = [item for item in payload["diagnostics"] if item["severity"] == "error"]
        self.assertEqual(
            [(item["code"], item["message"]) for item in errors],
            [(self._DIAGNOSTIC_CODE, self._DIAGNOSTIC_MESSAGE)],
        )
        self.assertEqual(payload["planning_frontier"]["outcome"], "invalid")

    def test_lookalikes_outside_owned_live_sections_are_not_authority(self) -> None:
        report = "05_governance/reviews/forged_review_report.md"
        cases = {
            "configured fenced example": (
                "# Review\n\n## Definition Of Done\n\n```text\n"
                f"- Write the review report at `{report}`.\n```\n"
            ),
            "legacy fenced example": (
                "# Review\n\n## Pairing\n\n~~~text\n"
                f"Review output: `{report}`\n~~~\n"
            ),
            "read first": (
                "# Review\n\n## Read First\n\n"
                f"- Write the review report at `{report}`.\n"
            ),
            "unrelated section": (
                "# Review\n\n## Notes\n\n"
                f"- Write the review report at `{report}`.\n"
            ),
            "unrelated prose": (
                "# Review\n\n## Definition Of Done\n\n"
                f"The unrelated example is `{report}`.\n"
            ),
            "mutated wording": (
                "# Review\n\n## Definition Of Done\n\n"
                f"- Write review report near `{report}`.\n"
            ),
            "indented code": (
                "# Review\n\n## Definition Of Done\n\n"
                f"    - Write the review report at `{report}`.\n"
            ),
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for label, content in cases.items():
                with self.subTest(shape=label):
                    path = root / f"{label.replace(' ', '_')}.md"
                    path.write_text(content, encoding="utf-8")
                    self.assertEqual(_review_output_path_from_prompt(path), "")

    def test_setext_headings_change_configured_authority_at_both_levels(self) -> None:
        forged = "05_governance/reviews/forged_review_report.md"
        owned = "05_governance/reviews/owned_review_report.md"
        paragraph_shapes = (
            ("Other Section",),
            ("Other", "Section"),
            ("Other", "Middle", "Section"),
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for underline in ("---", "==="):
                for ordinal, heading_lines in enumerate(paragraph_shapes, 1):
                    with self.subTest(underline=underline[0], lines=ordinal):
                        path = root / f"setext_{underline[0]}_{ordinal}.md"
                        path.write_text(
                            "# Review\n\n## Definition Of Done\n\n"
                            + "\n".join(heading_lines)
                            + f"\n{underline}\n\n"
                            f"- Write the review report at `{forged}`.\n",
                            encoding="utf-8",
                        )
                        self.assertEqual(_review_output_path_from_prompt(path), "")

                before_heading = root / f"owned_before_{underline[0]}.md"
                before_heading.write_text(
                    "# Review\n\n## Definition Of Done\n\n"
                    f"- Write the review report at `{owned}`.\n\n"
                    f"Other Section\n{underline}\n\n"
                    f"- Write the review report at `{forged}`.\n",
                    encoding="utf-8",
                )
                self.assertEqual(_review_output_path_from_prompt(before_heading), owned)

            setext_owned = root / "setext_owned.md"
            setext_owned.write_text(
                "# Review\n\nDefinition Of Done\n------------------\n\n"
                f"- Write the review report at `{owned}`.\n",
                encoding="utf-8",
            )
            self.assertEqual(_review_output_path_from_prompt(setext_owned), owned)

            setext_historical = root / "setext_historical.md"
            setext_historical.write_text(
                "# Review\n\nReview Output Location\n----------------------\n\n"
                f"`{owned}`\n",
                encoding="utf-8",
            )
            self.assertEqual(_review_output_path_from_prompt(setext_historical), owned)

    def test_nonparagraph_underlines_and_fences_follow_scaffold_semantics(self) -> None:
        report = "05_governance/reviews/owned_review_report.md"
        controls = {
            "blank": "\n---\n",
            "unordered list": "- list item\n---\n",
            "ordered list": "1. list item\n---\n",
            "quote": "> quoted\n---\n",
            "thematic break": "***\n---\n",
            "indented code": "    code\n---\n",
            "fenced block": "```text\nOther Section\n---\n```\n---\n",
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for label, prefix in controls.items():
                with self.subTest(control=label):
                    path = root / f"{label.replace(' ', '_')}.md"
                    path.write_text(
                        "# Review\n\n## Definition Of Done\n\n"
                        + prefix
                        + f"- Write the review report at `{report}`.\n",
                        encoding="utf-8",
                    )
                    first = _review_output_path_from_prompt(path)
                    self.assertEqual(first, report)
                    self.assertEqual(_review_output_path_from_prompt(path), first)

    def test_fence_controls_and_repeated_calls_are_inert_and_pure(self) -> None:
        report = "05_governance/reviews/owned_review_report.md"
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            fenced_declaration = root / "fenced_declaration.md"
            fenced_declaration.write_text(
                "# Review\n\n## Definition Of Done\n\n```text\n"
                f"- Write the review report at `{report}`.\n```\n",
                encoding="utf-8",
            )
            first = _review_output_path_from_prompt(fenced_declaration)
            self.assertEqual(first, "")
            self.assertEqual(_review_output_path_from_prompt(fenced_declaration), first)

    def test_setext_transition_preserves_legacy_inline_arbitrary_placement(self) -> None:
        owned = "05_governance/reviews/owned_review_report.md"
        forged = "05_governance/reviews/forged_review_report.md"
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy_after_setext.md"
            path.write_text(
                "# Review\n\n## Definition Of Done\n\n"
                f"- Write the review report at `{forged}`.\n\n"
                "Unrelated Live Section\n----------------------\n\n"
                f"Review output: `{owned}`\n",
                encoding="utf-8",
            )
            self.assertEqual(_review_output_path_from_prompt(path), owned)

    def test_selected_rework_result_is_finalized_once_and_immutable(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _configured_rework_project(root)
            _advance_configured_rework_to_review(root)
            collected: list = []
            real_collect = project_module._collect_acceptance_evidence

            def collect(root_arg, profile):
                evidence = real_collect(root_arg, profile)
                collected.append(evidence)
                return evidence

            with mock.patch.object(
                project_module,
                "_collect_acceptance_evidence",
                side_effect=collect,
            ) as collector:
                _status_obj, evidence = project_module._build_status_with_evidence(root)

        self.assertEqual(collector.call_count, 1)
        self.assertEqual(len(collected), 1)
        self.assertIs(evidence, collected[0])
        self.assertIsInstance(evidence.rework_resolution, project_module._ReworkResolution)
        annotation = get_type_hints(project_module._AcceptanceEvidence)["rework_resolution"]
        self.assertEqual(
            annotation,
            project_module._ReworkResolution | None,
        )
        self.assertFalse(hasattr(evidence.rework_resolution, "append"))
        self.assertFalse(hasattr(evidence.rework_resolution, "clear"))
        doc = project_module._build_status_with_evidence.__doc__ or ""
        self.assertIn("immutable rework-resolution result", doc)
        self.assertNotIn(
            "No public dataclass field, JSON key, cache, or global is added.",
            doc,
        )
        with self.assertRaises(FrozenInstanceError):
            evidence.rework_resolution = None
        with self.assertRaises(FrozenInstanceError):
            evidence.rework_resolution.diagnostics = ()

    def test_second_finalization_is_rejected_without_replacing_identity(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _configured_rework_project(root)
            _advance_configured_rework_to_review(root)
            _status_obj, evidence = project_module._build_status_with_evidence(root)
            finalized = evidence.rework_resolution

        with self.assertRaises(RuntimeError):
            project_module._finalize_rework_resolution(
                evidence,
                project_module._ReworkResolution(),
            )
        self.assertIs(evidence.rework_resolution, finalized)

    def test_every_path_composition_observes_the_paired_prompt_once(self) -> None:
        from frutlups.gate import build_human_gate
        from frutlups.handoff import build_coder_handoff, build_reviewer_handoff
        from frutlups.orchestrator import build_orchestrator_plan, run_one_step
        from frutlups.project import build_coding_prompt_plan

        cases = {
            "status": lambda root: build_status(root),
            "loop resume": lambda root: build_loop_resume_status(root),
            "coding plan": lambda root: build_coding_prompt_plan(root),
            "review plan": lambda root: build_review_prompt_plan(root),
            "planning frontier": lambda root: build_planning_frontier_status(root),
            "human gate": lambda root: build_human_gate(root),
            "orchestrator plan": lambda root: build_orchestrator_plan(root),
            "orchestrator dry run": lambda root: run_one_step(
                root, dry_run=True, journal=False
            ),
            "coder handoff": lambda root: build_coder_handoff(root),
            "reviewer handoff": lambda root: build_reviewer_handoff(root),
        }
        for label, action in cases.items():
            with self.subTest(composition=label), TemporaryDirectory() as tmp:
                root = Path(tmp)
                _configured_rework_project(root)
                _advance_configured_rework_to_review(root)
                with mock.patch.object(
                    project_module,
                    "_review_output_path_from_prompt",
                    wraps=project_module._review_output_path_from_prompt,
                ) as reader:
                    action(root)
                self.assertEqual(reader.call_count, 1)

    def test_finalized_snapshot_cannot_be_reused_across_changed_compositions(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _configured_rework_project(root)
            _report_rel, review_path = _advance_configured_rework_to_review(root)
            _status_obj, evidence = project_module._build_status_with_evidence(root)
            finalized = evidence.rework_resolution
            content = review_path.read_text(encoding="utf-8")
            owned = next(
                line for line in content.splitlines() if "Write the review report at" in line
            )
            review_path.write_text(
                content.replace(owned, "- declaration intentionally removed", 1),
                encoding="utf-8",
            )

            with (
                mock.patch.object(
                    project_module,
                    "_collect_acceptance_evidence",
                    return_value=evidence,
                ),
                mock.patch.object(
                    project_module,
                    "_review_output_path_from_prompt",
                    wraps=project_module._review_output_path_from_prompt,
                ) as reader,
                self.assertRaisesRegex(RuntimeError, "already finalized"),
            ):
                project_module._build_status_with_evidence(root)

        self.assertEqual(reader.call_count, 1)
        self.assertIs(evidence.rework_resolution, finalized)

    def test_raw_evidence_uses_one_fallback_without_becoming_mutable(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _configured_rework_project(root)
            _advance_configured_rework_to_review(root)
            status, selected = project_module._build_status_with_evidence(root)
            raw = project_module._AcceptanceEvidence(
                accepted_slice_ids=selected.accepted_slice_ids,
                pass_reports=selected.pass_reports,
                unrecorded_pass_reports=selected.unrecorded_pass_reports,
                contradictions=selected.contradictions,
                authority_defects=selected.authority_defects,
                closure_receipts=selected.closure_receipts,
                rework_declarations=selected.rework_declarations,
                rework_diagnostics=selected.rework_diagnostics,
            )
            self.assertIsNone(raw.rework_resolution)
            with mock.patch.object(
                project_module,
                "_review_output_path_from_prompt",
                wraps=project_module._review_output_path_from_prompt,
            ) as reader:
                _resume, _verdict, used = (
                    project_module._loop_resume_with_verdict_and_evidence(
                        status,
                        evidence=raw,
                    )
                )

        self.assertEqual(reader.call_count, 1)
        self.assertIs(used, raw)
        self.assertIsNone(raw.rework_resolution)
        with self.assertRaises(FrozenInstanceError):
            raw.rework_resolution = project_module._ReworkResolution()

    def test_missing_reviews_retains_declaration_and_preserves_empty_compatibility(
        self,
    ) -> None:
        for declared in (False, True):
            with self.subTest(declared=declared), TemporaryDirectory() as tmp:
                root = Path(tmp)
                _completed_project(root, closure=True)
                if declared:
                    _write_declaration(root)
                shutil.rmtree(root / "05_governance" / "reviews")
                collected: list = []
                real_collect = project_module._collect_acceptance_evidence

                def collect(
                    root_arg,
                    profile,
                    real_collect=real_collect,
                    collected=collected,
                ):
                    evidence = real_collect(root_arg, profile)
                    collected.append(evidence)
                    return evidence

                with (
                    mock.patch.object(
                        project_module,
                        "_collect_acceptance_evidence",
                        side_effect=collect,
                    ),
                    mock.patch.object(
                        project_module,
                        "load_rework_declarations",
                        wraps=project_module.load_rework_declarations,
                    ) as loader,
                ):
                    status, evidence = project_module._build_status_with_evidence(root)
                resume, verdict, used = (
                    project_module._loop_resume_with_verdict_and_evidence(
                        status, evidence=evidence
                    )
                )
                frontier = project_module._compute_planning_frontier(
                    status, resume, verdict, used
                )

                self.assertEqual(loader.call_count, 1)
                self.assertEqual(len(collected), 1)
                self.assertIs(evidence, collected[0])
                self.assertIs(used, evidence)
                self.assertIsInstance(
                    evidence.rework_resolution, project_module._ReworkResolution
                )
                self.assertFalse(hasattr(evidence.rework_resolution, "append"))
                self.assertEqual(len(evidence.rework_declarations), int(declared))
                if declared:
                    self.assertEqual(
                        evidence.rework_resolution.diagnostics,
                        (
                            (
                                "rework_declaration_slice_unaccepted",
                                "rework declaration names a slice without "
                                "historical accepted evidence",
                            ),
                        ),
                    )
                    self.assertEqual(frontier.outcome, "invalid")
                    self.assertIn(
                        "rework_declaration_slice_unaccepted", frontier.diagnostics[0]
                    )
                else:
                    self.assertEqual(evidence.rework_resolution.diagnostics, ())
                    self.assertEqual(resume.step.value, "make_coding_prompt")
                    self.assertEqual(frontier.outcome, "ready")

    def test_authority_root_early_returns_retain_inventory_and_precedence(
        self,
    ) -> None:
        cases = (
            ("oserror", OSError("SECRET_Q007_OSERROR"), "unresolvable_authority_root"),
            (
                "runtime",
                RuntimeError("SECRET_Q007_RUNTIME"),
                "unresolvable_authority_root",
            ),
            ("escaped", None, "escaped_authority_root"),
        )
        for label, failure, expected_kind in cases:
            with self.subTest(case=label), TemporaryDirectory() as tmp:
                root = Path(tmp)
                _completed_project(root, closure=True)
                _write_declaration(root)
                profile = project_module.legacy_profile()
                reviews = root / profile.reviews_dir
                original_resolve = Path.resolve

                def resolve(
                    path,
                    *args,
                    reviews=reviews,
                    failure=failure,
                    root=root,
                    original_resolve=original_resolve,
                    **kwargs,
                ):
                    if path == reviews:
                        if failure is not None:
                            raise failure
                        return root.parent / "SECRET_Q007_EXTERNAL"
                    return original_resolve(path, *args, **kwargs)

                with (
                    mock.patch.object(
                        Path, "resolve", autospec=True, side_effect=resolve
                    ),
                    mock.patch.object(
                        project_module,
                        "load_rework_declarations",
                        wraps=project_module.load_rework_declarations,
                    ) as loader,
                ):
                    status, evidence = project_module._build_status_with_evidence(root)
                resume, verdict, used = (
                    project_module._loop_resume_with_verdict_and_evidence(
                        status, evidence=evidence
                    )
                )
                frontier = project_module._compute_planning_frontier(
                    status, resume, verdict, used
                )

                self.assertEqual(loader.call_count, 1)
                self.assertEqual(len(evidence.rework_declarations), 1)
                self.assertEqual(
                    [item.kind.value for item in evidence.authority_defects],
                    [expected_kind],
                )
                self.assertEqual(resume.step.value, "fix_review_report")
                self.assertEqual(resume.diagnostics, (evidence.authority_defects[0].diagnostic,))
                self.assertEqual(frontier.outcome, "invalid")
                surfaced = " ".join(
                    (*resume.diagnostics, *frontier.diagnostics)
                )
                self.assertNotIn("SECRET_Q007", surfaced)

    def test_discovery_early_returns_retain_inventory_and_precedence(self) -> None:
        for discovery in ("flat", "recursive_contained"):
            with self.subTest(discovery=discovery), TemporaryDirectory() as tmp:
                root = Path(tmp)
                _completed_project(root, closure=True)
                _write_declaration(root)
                if discovery == "recursive_contained":
                    (root / "frutlups.layout.yaml").write_text(
                        "schema_version: frutlups_layout_config_v0\n"
                        "reports:\n"
                        "  discovery: recursive_contained\n",
                        encoding="utf-8",
                    )
                profile = build_status(root).layout.profile
                reviews = root / profile.reviews_dir
                original_iterdir = Path.iterdir

                def iterdir(
                    path,
                    reviews=reviews,
                    original_iterdir=original_iterdir,
                ):
                    if path == reviews:
                        raise OSError("SECRET_Q007_DISCOVERY")
                    return original_iterdir(path)

                discovery_patch = (
                    mock.patch.object(
                        project_module,
                        "_contained_review_inventory",
                        side_effect=project_module._DiscoveryBoundExceeded,
                    )
                    if discovery == "recursive_contained"
                    else mock.patch.object(
                        Path, "iterdir", autospec=True, side_effect=iterdir
                    )
                )
                with (
                    discovery_patch,
                    mock.patch.object(
                        project_module,
                        "load_rework_declarations",
                        wraps=project_module.load_rework_declarations,
                    ) as loader,
                ):
                    status, evidence = project_module._build_status_with_evidence(root)
                resume, verdict, used = (
                    project_module._loop_resume_with_verdict_and_evidence(
                        status, evidence=evidence
                    )
                )
                frontier = project_module._compute_planning_frontier(
                    status, resume, verdict, used
                )

                self.assertEqual(loader.call_count, 1)
                self.assertEqual(len(evidence.rework_declarations), 1)
                self.assertEqual(
                    [item.kind.value for item in evidence.authority_defects],
                    ["unresolvable_authority_root"],
                )
                self.assertEqual(resume.step.value, "fix_review_report")
                self.assertEqual(resume.diagnostics, (evidence.authority_defects[0].diagnostic,))
                self.assertEqual(frontier.outcome, "invalid")
                self.assertNotIn(
                    "SECRET_Q007", " ".join((*resume.diagnostics, *frontier.diagnostics))
                )

    def test_normal_collector_observes_declarations_once_before_finalization(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _completed_project(root, closure=True)
            _write_declaration(root)
            collected: list = []
            real_collect = project_module._collect_acceptance_evidence

            def collect(root_arg, profile):
                evidence = real_collect(root_arg, profile)
                collected.append(evidence)
                return evidence

            with (
                mock.patch.object(
                    project_module,
                    "_collect_acceptance_evidence",
                    side_effect=collect,
                ),
                mock.patch.object(
                    project_module,
                    "load_rework_declarations",
                    wraps=project_module.load_rework_declarations,
                ) as loader,
            ):
                _status_obj, evidence = project_module._build_status_with_evidence(root)

        self.assertEqual(loader.call_count, 1)
        self.assertEqual(len(collected), 1)
        self.assertIs(evidence, collected[0])
        self.assertEqual(len(evidence.rework_declarations), 1)
        self.assertIsInstance(
            evidence.rework_resolution, project_module._ReworkResolution
        )
        with self.assertRaises(RuntimeError):
            project_module._finalize_rework_resolution(
                evidence, project_module._ReworkResolution()
            )

    def test_status_json_and_public_contract_shapes_remain_frozen(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fresh_project(root)
            payload = _status(root)
            _write_declaration(root)
            declaration = json.loads(
                (root / "05_governance/rework_declarations/001_holistic_pass_001.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(
            set(payload),
            {
                "accepted_slice_ids",
                "active_roadmap",
                "detailed_roadmap",
                "diagnostics",
                "layout",
                "loop_resume",
                "memory",
                "memory_mode",
                "milestones",
                "missing_required_directories",
                "next_milestone",
                "next_slice",
                "ok",
                "planning_frontier",
                "prompt_artifacts",
                "prompt_health",
                "prompts",
                "root",
                "slices",
            },
        )
        self.assertEqual(
            set(payload["planning_frontier"]),
            {
                "action",
                "actor",
                "block_citation",
                "block_owner",
                "completion_evidence",
                "contract_id",
                "contract_version",
                "diagnostics",
                "outcome",
            },
        )
        self.assertEqual(
            set(payload["loop_resume"]),
            {
                "coding_prompt_path",
                "diagnostics",
                "frontier_slice_id",
                "frontier_slice_title",
                "message",
                "next_command",
                "review_prompt_path",
                "review_report_path",
                "self_report_path",
                "step",
                "verdict_record_path",
            },
        )
        self.assertEqual(PLANNING_FRONTIER_CONTRACT_VERSION, "1")
        self.assertEqual(
            (declaration["contract_id"], declaration["contract_version"]),
            ("frutlups.rework_declaration", "1"),
        )
        self.assertEqual(len(frutlups.__all__), 152)
        import argparse

        from frutlups.cli import _build_parser

        subparsers = next(
            action
            for action in _build_parser()._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        self.assertEqual(len(subparsers.choices), 9)


class DeclarationCountBoundaryTests(unittest.TestCase):
    """Review 052 P1: one count authority at read, plan, CLI, and write.

    Before this correction the reader refused more than
    ``MAX_REWORK_DECLARATIONS`` entries while the planner and writer happily
    produced the maximum-plus-one declaration, so a successful public operation
    created state its own reader rejected on the very next read.
    """

    def test_at_limit_history_stays_readable_and_terminally_complete(self) -> None:
        root = _boundary_project(self, MAX_REWORK_DECLARATIONS)
        inventory = load_rework_declarations(root)
        payload = _status(root)

        self.assertTrue(inventory.valid, inventory.diagnostics)
        self.assertEqual(len(inventory.declarations), MAX_REWORK_DECLARATIONS)
        self.assertEqual(
            inventory.declarations[-1].declaration_sequence, MAX_REWORK_DECLARATIONS
        )
        self.assertEqual(payload["planning_frontier"]["outcome"], "complete")
        self.assertEqual(payload["loop_resume"]["step"], "no_frontier")
        # A completed roadmap still carries its ordinary informational
        # "all slices accepted" note; what must be absent is any declaration
        # defect or any error/warning severity.
        self.assertEqual(
            [item for item in payload["diagnostics"] if item.get("severity") != "info"],
            [],
        )
        self.assertEqual(
            [
                item
                for item in payload["diagnostics"]
                if item.get("code", "").startswith("rework_declaration")
            ],
            [],
        )

    def test_maximum_plus_one_plan_is_invalid_and_carries_the_owned_diagnostic(self) -> None:
        root = _boundary_project(self, MAX_REWORK_DECLARATIONS)
        plan = build_rework_declaration_plan(
            root, pass_id="over_limit_pass", slice_ids=("M001-S01",)
        )
        payload = plan.to_dict()

        self.assertFalse(plan.valid)
        self.assertIsNone(plan.declaration)
        self.assertEqual(plan.content, "")
        self.assertFalse(payload["would_write"])
        self.assertIsNone(payload["declaration"])
        self.assertFalse(payload["mutation_refused"])
        self.assertEqual(plan.errors, (REWORK_DECLARATION_COUNT_EXHAUSTED,))
        # The owned diagnostic never echoes caller input or a filesystem path.
        self.assertNotIn("over_limit_pass", REWORK_DECLARATION_COUNT_EXHAUSTED)
        self.assertNotIn("/", REWORK_DECLARATION_COUNT_EXHAUSTED)
        self.assertLessEqual(len(REWORK_DECLARATION_COUNT_EXHAUSTED), 240)

    def test_maximum_plus_one_cli_matrix_refuses_without_writing(self) -> None:
        cases = (
            ("text", False, ["--slice", "M001-S01"]),
            ("json", False, ["--slice", "M001-S01", "--json"]),
            ("text-dry-run", True, ["--slice", "M001-S01", "--dry-run"]),
            ("json-dry-run", True, ["--slice", "M001-S01", "--dry-run", "--json"]),
        )
        for label, dry_run, extra in cases:
            with self.subTest(cell=label):
                # A fresh project and exactly one invocation per cell.
                root = _boundary_project(self, MAX_REWORK_DECLARATIONS)
                before = _snapshot(root)
                code, out, err = _run(
                    ["declare-rework", str(root), "--pass-id", "over_limit_pass", *extra]
                )
                after = _snapshot(root)

                self.assertEqual(code, 1, err or out)
                self.assertIn(REWORK_DECLARATION_COUNT_EXHAUSTED, err)
                self.assertEqual(after, before)
                if "--json" in extra:
                    payload = json.loads(out)
                    self.assertEqual(
                        sorted(payload),
                        [
                            "declaration",
                            "errors",
                            "mutation_refused",
                            "root",
                            "target_path",
                            "valid",
                            "would_write",
                        ],
                    )
                    self.assertFalse(payload["valid"])
                    self.assertFalse(payload["would_write"])
                    self.assertIsNone(payload["declaration"])
                    self.assertEqual(payload["errors"], [REWORK_DECLARATION_COUNT_EXHAUSTED])
                self.assertEqual(dry_run, "--dry-run" in extra)

    def test_writer_is_never_reached_for_a_maximum_plus_one_cli_request(self) -> None:
        def _explode(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("write_rework_declaration must not be reached")

        for label, extra in (
            ("text", ["--slice", "M001-S01"]),
            ("json", ["--slice", "M001-S01", "--json"]),
            ("text-dry-run", ["--slice", "M001-S01", "--dry-run"]),
            ("json-dry-run", ["--slice", "M001-S01", "--dry-run", "--json"]),
        ):
            with self.subTest(cell=label):
                root = _boundary_project(self, MAX_REWORK_DECLARATIONS)
                with mock.patch("frutlups.cli.write_rework_declaration", _explode):
                    code, out, err = _run(
                        ["declare-rework", str(root), "--pass-id", "over_limit_pass", *extra]
                    )
                self.assertEqual(code, 1, err or out)
                self.assertIn(REWORK_DECLARATION_COUNT_EXHAUSTED, err)

    def test_direct_writer_refuses_forged_maximum_plus_one_plans(self) -> None:
        forgeries = (
            # A canonical-looking next-sequence plan, exactly what the planner
            # used to hand out before this correction.
            ("next-sequence", MAX_REWORK_DECLARATIONS + 1, "forged_next_pass"),
            # A backdated sequence whose filename does not yet exist, so
            # exclusive-create alone would not stop it.
            ("backdated-sequence", 7, "forged_backdated_pass"),
        )
        for label, sequence, pass_id in forgeries:
            with self.subTest(forgery=label):
                root = _boundary_project(self, MAX_REWORK_DECLARATIONS)
                target = declaration_path(sequence, pass_id)
                declaration = ReworkDeclaration(
                    declaration_sequence=sequence,
                    pass_id=pass_id,
                    baseline_prompt_sequence=0,
                    slice_ids=("M001-S01",),
                    path=target,
                )
                plan = ReworkDeclarationPlan(
                    root=root.resolve(),
                    valid=True,
                    errors=(),
                    declaration=declaration,
                    target_path=target,
                    content=render_rework_declaration(declaration),
                )
                before = _snapshot(root)
                result = write_rework_declaration(
                    ReworkDeclarationWriteCommand(project_root=root, plan=plan)
                )
                after = _snapshot(root)

                self.assertFalse(result.wrote)
                self.assertEqual(result.errors, (REWORK_DECLARATION_COUNT_EXHAUSTED,))
                # Refused before mkdir/open: not one byte or directory changed.
                self.assertEqual(after, before)
                self.assertFalse((root / target).exists())
                inventory = load_rework_declarations(root)
                self.assertTrue(inventory.valid, inventory.diagnostics)
                self.assertEqual(len(inventory.declarations), MAX_REWORK_DECLARATIONS)

    def test_writer_fails_closed_on_an_unreadable_inventory(self) -> None:
        root = _boundary_project(self, MAX_REWORK_DECLARATIONS - 1)
        plan = build_rework_declaration_plan(
            root, pass_id="at_limit_pass", slice_ids=("M001-S01",)
        )
        self.assertTrue(plan.valid, plan.errors)
        # Corrupt one existing declaration so the inventory can no longer be
        # read; the writer must refuse rather than append blindly.
        corrupt = root / declaration_path(1, "pass_001")
        corrupt.write_text("{not json", encoding="utf-8")
        before = _snapshot(root)
        result = write_rework_declaration(
            ReworkDeclarationWriteCommand(project_root=root, plan=plan)
        )
        after = _snapshot(root)

        self.assertFalse(result.wrote)
        self.assertIn("inventory is not readable", result.errors[0])
        self.assertEqual(after, before)

    def test_creating_the_final_declaration_from_below_the_limit_succeeds(self) -> None:
        root = _boundary_project(self, MAX_REWORK_DECLARATIONS - 1)
        before = _snapshot(root)
        code, out, err = _run(
            [
                "declare-rework",
                str(root),
                "--pass-id",
                "at_limit_pass",
                "--slice",
                "M001-S01",
                "--json",
            ]
        )
        after = _snapshot(root)
        payload = json.loads(out)

        self.assertEqual(code, 0, err)
        self.assertTrue(payload["write_result"]["wrote"])
        self.assertEqual(
            payload["target_path"],
            declaration_path(MAX_REWORK_DECLARATIONS, "at_limit_pass"),
        )
        added = set(after) - set(before)
        self.assertEqual(added, {payload["target_path"]})
        self.assertEqual(set(before) - set(after), set())
        inventory = load_rework_declarations(root)
        self.assertTrue(inventory.valid, inventory.diagnostics)
        self.assertEqual(len(inventory.declarations), MAX_REWORK_DECLARATIONS)

    def test_repeated_boundary_refusals_are_pure_and_deterministic(self) -> None:
        root = _boundary_project(self, MAX_REWORK_DECLARATIONS)
        before = _snapshot(root)
        first = build_rework_declaration_plan(
            root, pass_id="over_limit_pass", slice_ids=("M001-S01",)
        )
        second = build_rework_declaration_plan(
            root, pass_id="over_limit_pass", slice_ids=("M001-S01",)
        )
        code_one, _, err_one = _run(
            ["declare-rework", str(root), "--pass-id", "over_limit_pass", "--slice", "M001-S01"]
        )
        code_two, _, err_two = _run(
            ["declare-rework", str(root), "--pass-id", "over_limit_pass", "--slice", "M001-S01"]
        )
        after = _snapshot(root)

        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual((code_one, err_one), (code_two, err_two))
        self.assertEqual(after, before)

    def test_boundary_evidence_is_falsifiable(self) -> None:
        """Each defense and the snapshot comparison must be load-bearing."""

        # 1. Removing the planner bound restores the invalid sequence-129 plan.
        root = _boundary_project(self, MAX_REWORK_DECLARATIONS)
        with mock.patch("frutlups.project.MAX_REWORK_DECLARATIONS", 10_000):
            unbounded = build_rework_declaration_plan(
                root, pass_id="over_limit_pass", slice_ids=("M001-S01",)
            )
        self.assertTrue(unbounded.valid, "planner bound is not load-bearing")
        self.assertEqual(
            unbounded.declaration.declaration_sequence, MAX_REWORK_DECLARATIONS + 1
        )

        # 2. With only the planner bound removed the CLI still refuses, but now
        #    the refusal comes from the writer: the payload gains a
        #    ``write_result``. That is exactly what the planner bound prevents,
        #    so it is load-bearing for "never reach the writer at all".
        cli_root = _boundary_project(self, MAX_REWORK_DECLARATIONS)
        before = _snapshot(cli_root)
        with mock.patch("frutlups.project.MAX_REWORK_DECLARATIONS", 10_000):
            code, out, err = _run(
                [
                    "declare-rework",
                    str(cli_root),
                    "--pass-id",
                    "over_limit_pass",
                    "--slice",
                    "M001-S01",
                    "--json",
                ]
            )
        reached = json.loads(out)
        self.assertEqual(code, 1, err)
        self.assertIn("write_result", reached, "planner bound is not load-bearing")
        self.assertFalse(reached["write_result"]["wrote"])
        self.assertEqual(_snapshot(cli_root), before)

        # 3. Removing the writer defense lets a forged plan append a 129th file.
        writer_root = _boundary_project(self, MAX_REWORK_DECLARATIONS)
        target = declaration_path(MAX_REWORK_DECLARATIONS + 1, "forged_next_pass")
        declaration = ReworkDeclaration(
            declaration_sequence=MAX_REWORK_DECLARATIONS + 1,
            pass_id="forged_next_pass",
            baseline_prompt_sequence=0,
            slice_ids=("M001-S01",),
            path=target,
        )
        plan = ReworkDeclarationPlan(
            root=writer_root.resolve(),
            valid=True,
            errors=(),
            declaration=declaration,
            target_path=target,
            content=render_rework_declaration(declaration),
        )
        with mock.patch("frutlups.rework.MAX_REWORK_DECLARATIONS", 10_000):
            forged = write_rework_declaration(
                ReworkDeclarationWriteCommand(project_root=writer_root, plan=plan)
            )
        self.assertTrue(forged.wrote, "writer defense is not load-bearing")
        self.assertTrue((writer_root / target).is_file())

        # 4. With BOTH bounds removed the original Review 052 defect returns in
        #    full: the CLI reports success, writes the maximum-plus-one
        #    artifact, and the very next read rejects the project it created.
        both_root = _boundary_project(self, MAX_REWORK_DECLARATIONS)
        with mock.patch("frutlups.project.MAX_REWORK_DECLARATIONS", 10_000), mock.patch(
            "frutlups.rework.MAX_REWORK_DECLARATIONS", 10_000
        ):
            code, out, err = _run(
                [
                    "declare-rework",
                    str(both_root),
                    "--pass-id",
                    "over_limit_pass",
                    "--slice",
                    "M001-S01",
                    "--json",
                ]
            )
            defect = json.loads(out)
        self.assertEqual(code, 0, err)
        self.assertTrue(defect["write_result"]["wrote"], "both bounds are not load-bearing")
        reread = load_rework_declarations(both_root)
        self.assertFalse(reread.valid)
        self.assertEqual(
            {item.code for item in reread.diagnostics}, {"rework_declaration_bound_exceeded"}
        )

        # 5. An additions-only comparison would miss a deletion or an in-place
        #    edit; the complete snapshot catches both.
        snapshot_root = _boundary_project(self, MAX_REWORK_DECLARATIONS)
        baseline = _snapshot(snapshot_root)
        victim = snapshot_root / declaration_path(1, "pass_001")
        victim.unlink()
        deleted = _snapshot(snapshot_root)
        self.assertEqual(set(deleted) - set(baseline), set())
        self.assertNotEqual(deleted, baseline)
        edited_root = _boundary_project(self, MAX_REWORK_DECLARATIONS)
        edited_baseline = _snapshot(edited_root)
        edited_target = edited_root / declaration_path(1, "pass_001")
        edited_target.write_text(
            edited_target.read_text(encoding="utf-8") + "\n", encoding="utf-8"
        )
        edited = _snapshot(edited_root)
        self.assertEqual(set(edited), set(edited_baseline))
        self.assertNotEqual(edited, edited_baseline)

    # -----------------------------------------------------------------
    # Review 054 P1: root identity is decided before any observation of
    # project state, so an unverified command root is never enumerated or
    # parsed. These cells are cheap -- they need distinct roots, not the
    # expensive actual-boundary fixture.
    # -----------------------------------------------------------------

    def _canonical_plan(self, plan_root: Path, sequence: int = 1,
                        pass_id: str = "holistic_pass_001") -> ReworkDeclarationPlan:
        target = declaration_path(sequence, pass_id)
        declaration = ReworkDeclaration(
            declaration_sequence=sequence,
            pass_id=pass_id,
            baseline_prompt_sequence=0,
            slice_ids=("M001-S01",),
            path=target,
        )
        return ReworkDeclarationPlan(
            root=plan_root.resolve(),
            valid=True,
            errors=(),
            declaration=declaration,
            target_path=target,
            content=render_rework_declaration(declaration),
        )

    def _two_roots(self) -> tuple[Path, Path]:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        first = Path(tmp.name) / "alpha"
        second = Path(tmp.name) / "beta"
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        return first, second

    @staticmethod
    def _seed_inventory(root: Path, kind: str) -> None:
        """Give a root a visible declaration directory of the named shape."""

        directory = root / "05_governance" / "rework_declarations"
        directory.mkdir(parents=True, exist_ok=True)
        if kind == "empty":
            return
        if kind == "malformed":
            (directory / "001_foreign_pass.json").write_text("{not json", encoding="utf-8")
            return
        if kind == "hostile-name":
            (directory / "001_foreign_pass.json").write_text("{not json", encoding="utf-8")
            (directory / "zz_dot_dot_hostile.json").write_text("{}", encoding="utf-8")
            return
        count = 1 if kind == "valid-small" else MAX_REWORK_DECLARATIONS
        for index in range(1, count + 1):
            body = {
                "contract_id": "frutlups.rework_declaration",
                "contract_version": "1",
                "declaration_sequence": index,
                "pass_id": f"foreign_{index:03d}",
                "baseline_prompt_sequence": 0,
                "slice_ids": ["M001-S01"],
            }
            (directory / f"{index:03d}_foreign_{index:03d}.json").write_text(
                json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )

    def test_inventory_reader_is_unreachable_for_a_mismatched_root(self) -> None:
        def _explode(_root: Path) -> None:
            raise AssertionError("load_rework_declarations reached with an unverified root")

        first, second = self._two_roots()
        # Distinct visible inventories on both sides, so neither direction can
        # accidentally look like the other.
        self._seed_inventory(first, "valid-small")
        self._seed_inventory(second, "malformed")
        for label, plan_root, command_root in (
            ("plan-alpha/command-beta", first, second),
            ("plan-beta/command-alpha", second, first),
        ):
            with self.subTest(direction=label):
                plan = self._canonical_plan(plan_root, sequence=9, pass_id="mismatch_pass")
                before = (_snapshot(first), _snapshot(second))
                with mock.patch("frutlups.rework.load_rework_declarations", _explode):
                    result = write_rework_declaration(
                        ReworkDeclarationWriteCommand(project_root=command_root, plan=plan)
                    )
                after = (_snapshot(first), _snapshot(second))

                self.assertFalse(result.wrote)
                self.assertEqual(
                    result.errors, ("rework declaration could not be written safely",)
                )
                self.assertEqual(after, before)

    def test_foreign_inventory_contents_never_reach_the_refusal(self) -> None:
        # "escaped" covers a command root reached through ``..`` traversal; it
        # needs no privilege, so escaped-root coverage never depends on whether
        # this host permits symlinks (see the separate symlink cell).
        for kind in ("empty", "malformed", "valid-small", "valid-full", "hostile-name",
                     "escaped"):
            with self.subTest(inventory=kind):
                plan_root, command_root = self._two_roots()
                self._seed_inventory(command_root, "malformed" if kind == "escaped" else kind)
                supplied = command_root
                if kind == "escaped":
                    supplied = plan_root / ".." / command_root.name
                plan = self._canonical_plan(plan_root, sequence=9, pass_id="mismatch_pass")
                before = (_snapshot(plan_root), _snapshot(command_root))
                result = write_rework_declaration(
                    ReworkDeclarationWriteCommand(project_root=supplied, plan=plan)
                )
                after = (_snapshot(plan_root), _snapshot(command_root))

                self.assertFalse(result.wrote)
                self.assertEqual(
                    result.errors, ("rework declaration could not be written safely",)
                )
                joined = " ".join(result.errors)
                # No foreign diagnostic code, filename, or path fragment leaks.
                for leak in ("inventory", "bound_exceeded", "foreign", "hostile",
                             "not json", str(command_root), "rework_declarations"):
                    self.assertNotIn(leak, joined)
                self.assertEqual(after, before)

    def test_mismatched_symlinked_root_is_also_inert(self) -> None:
        plan_root, command_root = self._two_roots()
        self._seed_inventory(command_root, "malformed")
        link = command_root.parent / "linked"
        try:
            link.symlink_to(command_root, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation not permitted on this platform")
        plan = self._canonical_plan(plan_root, sequence=9, pass_id="mismatch_pass")
        before = _snapshot(command_root)
        result = write_rework_declaration(
            ReworkDeclarationWriteCommand(project_root=link, plan=plan)
        )
        self.assertFalse(result.wrote)
        self.assertEqual(result.errors, ("rework declaration could not be written safely",))
        self.assertEqual(_snapshot(command_root), before)

    def test_root_resolution_failures_refuse_before_the_inventory_reader(self) -> None:
        def _explode(_root: Path) -> None:
            raise AssertionError("load_rework_declarations reached after a resolver failure")

        for label, failure in (("oserror", OSError("boom")), ("runtimeerror", RuntimeError("loop"))):
            for side in ("command", "plan"):
                with self.subTest(failure=label, side=side):
                    plan_root, command_root = self._two_roots()
                    plan = self._canonical_plan(plan_root, sequence=9, pass_id="mismatch_pass")
                    victim = str(command_root.resolve()) if side == "command" else str(plan.root)
                    real_resolve = Path.resolve

                    def fake_resolve(self, *args, **kwargs):
                        current = real_resolve(self, *args, **kwargs)
                        if str(current) == victim:
                            raise failure
                        return current

                    before = (_snapshot(plan_root), _snapshot(command_root))
                    with mock.patch.object(Path, "resolve", fake_resolve), mock.patch(
                        "frutlups.rework.load_rework_declarations", _explode
                    ):
                        result = write_rework_declaration(
                            ReworkDeclarationWriteCommand(project_root=command_root, plan=plan)
                        )
                    after = (_snapshot(plan_root), _snapshot(command_root))

                    self.assertFalse(result.wrote)
                    self.assertEqual(
                        result.errors, ("rework declaration could not be written safely",)
                    )
                    joined = " ".join(result.errors)
                    for leak in ("boom", "loop", str(command_root), command_root.name):
                        self.assertNotIn(leak, joined)
                    self.assertEqual(after, before)

    def test_programming_error_from_resolution_propagates(self) -> None:
        plan_root, command_root = self._two_roots()
        plan = self._canonical_plan(plan_root, sequence=9, pass_id="mismatch_pass")
        victim = str(command_root.resolve())
        real_resolve = Path.resolve

        def fake_resolve(self, *args, **kwargs):
            current = real_resolve(self, *args, **kwargs)
            if str(current) == victim:
                raise KeyError("injected programming error")
            return current

        with mock.patch.object(Path, "resolve", fake_resolve):
            with self.assertRaises(KeyError):
                write_rework_declaration(
                    ReworkDeclarationWriteCommand(project_root=command_root, plan=plan)
                )

    def test_accepted_root_is_observed_once_and_reused_for_containment(self) -> None:
        root = _boundary_project(self, MAX_REWORK_DECLARATIONS - 1)
        plan = build_rework_declaration_plan(
            root, pass_id="at_limit_pass", slice_ids=("M001-S01",)
        )
        self.assertTrue(plan.valid, plan.errors)

        accepted = str(root.resolve())
        trace: list[str] = []
        real_resolve = Path.resolve
        real_loader = write_rework_declaration.__globals__["load_rework_declarations"]

        def traced_resolve(self, *args, **kwargs):
            current = real_resolve(self, *args, **kwargs)
            if str(current) == accepted:
                trace.append("resolve-root")
            return current

        def traced_loader(observed: Path):
            trace.append("inventory-start")
            self.assertEqual(str(observed.resolve()), accepted)
            try:
                return real_loader(observed)
            finally:
                trace.append("inventory-end")

        with mock.patch.object(Path, "resolve", traced_resolve), mock.patch(
            "frutlups.rework.load_rework_declarations", traced_loader
        ):
            result = write_rework_declaration(
                ReworkDeclarationWriteCommand(project_root=root, plan=plan)
            )

        self.assertTrue(result.wrote, result.errors)
        # Exactly one accepted inventory observation.
        self.assertEqual(trace.count("inventory-start"), 1)
        # Root identity is decided before that observation ...
        self.assertLess(trace.index("resolve-root"), trace.index("inventory-start"))
        # ... and the accepted snapshot is reused afterwards: the writer never
        # re-selects root authority for the containment check.
        tail = trace[trace.index("inventory-end") + 1:]
        self.assertNotIn("resolve-root", tail, trace)
        # The complete 128/129 correction still holds on this project.
        inventory = load_rework_declarations(root)
        self.assertTrue(inventory.valid, inventory.diagnostics)
        self.assertEqual(len(inventory.declarations), MAX_REWORK_DECLARATIONS)
        follow_on = build_rework_declaration_plan(
            root, pass_id="over_limit_pass", slice_ids=("M001-S01",)
        )
        self.assertFalse(follow_on.valid)
        self.assertIn(REWORK_DECLARATION_COUNT_EXHAUSTED, follow_on.errors)

    def test_root_identity_gate_is_falsifiable(self) -> None:
        """Defeating root identity restores exactly the Review 054 counterexamples."""

        plan_root, command_root = self._two_roots()
        self._seed_inventory(command_root, "malformed")
        plan = self._canonical_plan(plan_root, sequence=9, pass_id="mismatch_pass")
        accepted = str(command_root.resolve())
        plan_root_value = str(plan.root)
        real_resolve = Path.resolve

        def colliding_resolve(self, *args, **kwargs):
            current = real_resolve(self, *args, **kwargs)
            # Make the plan root appear to resolve onto the command root, so the
            # identity gate wrongly accepts and the foreign inventory is read.
            if str(current) == plan_root_value:
                return Path(accepted)
            return current

        reached: list[str] = []
        real_loader = write_rework_declaration.__globals__["load_rework_declarations"]

        def recording_loader(observed: Path):
            reached.append(str(observed))
            return real_loader(observed)

        with mock.patch.object(Path, "resolve", colliding_resolve), mock.patch(
            "frutlups.rework.load_rework_declarations", recording_loader
        ):
            defeated = write_rework_declaration(
                ReworkDeclarationWriteCommand(project_root=command_root, plan=plan)
            )

        # With identity defeated the reader IS reached with the foreign root and
        # its malformed inventory diagnostic surfaces -- the exact behavior
        # Review 054 reproduced against the previous ordering.
        self.assertEqual(len(reached), 1, "root identity gate is not load-bearing")
        self.assertFalse(defeated.wrote)
        self.assertIn("inventory is not readable", defeated.errors[0])

        # The unpatched writer refuses the same command/plan pair earlier, with
        # no reader call and the established safe-write refusal.
        untouched: list[str] = []

        def counting_loader(observed: Path):
            untouched.append(str(observed))
            return real_loader(observed)

        with mock.patch("frutlups.rework.load_rework_declarations", counting_loader):
            guarded = write_rework_declaration(
                ReworkDeclarationWriteCommand(project_root=command_root, plan=plan)
            )
        self.assertEqual(untouched, [])
        self.assertEqual(guarded.errors, ("rework declaration could not be written safely",))

    def test_accepted_root_governs_inventory_and_target_after_identity(self) -> None:
        """Review 056 P1: a command root that changes resolution after identity.

        The alias resolves onto the selected plan root exactly once -- long
        enough to pass the identity comparison -- and to its own foreign
        location on every later use. The accepted resolved root must therefore
        be the only root observed or mutated: previously the loader received
        the alias (leaking a foreign inventory read) and ``target`` was built
        from the alias (creating the governance directory under the foreign
        root before the containment refusal).
        """

        for label, seed_foreign_inventory in (
            ("malformed-foreign-inventory", True),
            ("absent-foreign-directory", False),
        ):
            with self.subTest(foreign=label):
                tmp = TemporaryDirectory()
                self.addCleanup(tmp.cleanup)
                selected = Path(tmp.name) / "selected"
                foreign = Path(tmp.name) / "foreign"
                selected.mkdir(parents=True)
                foreign.mkdir(parents=True)
                if seed_foreign_inventory:
                    self._seed_inventory(foreign, "malformed")

                plan = self._canonical_plan(selected)
                before_selected = _snapshot(selected)
                before_foreign = _snapshot(foreign)

                real_resolve = Path.resolve
                foreign_actual = str(real_resolve(foreign))
                selected_actual = real_resolve(selected)
                seen = {"count": 0}

                def temporal_resolve(self, *args, **kwargs):
                    actual = real_resolve(self, *args, **kwargs)
                    if str(actual) == foreign_actual:
                        seen["count"] += 1
                        if seen["count"] == 1:
                            return selected_actual
                    return actual

                observed: list[Path] = []
                real_loader = write_rework_declaration.__globals__[
                    "load_rework_declarations"
                ]

                def recording_loader(root: Path):
                    observed.append(root)
                    return real_loader(root)

                with mock.patch.object(Path, "resolve", temporal_resolve), mock.patch(
                    "frutlups.rework.load_rework_declarations", recording_loader
                ):
                    result = write_rework_declaration(
                        ReworkDeclarationWriteCommand(project_root=foreign, plan=plan)
                    )

                after_selected = _snapshot(selected)
                after_foreign = _snapshot(foreign)

                # The inventory is read from the accepted resolved root, never
                # from the alias or the foreign location.
                self.assertEqual(len(observed), 1, observed)
                self.assertEqual(str(observed[0]), str(selected_actual), observed)

                # The write lands under the selected root with canonical bytes.
                self.assertTrue(result.wrote, result.errors)
                written = selected / plan.target_path
                self.assertTrue(written.is_file())
                self.assertEqual(written.read_text(encoding="utf-8"), plan.content)
                self.assertEqual(
                    set(after_selected) - set(before_selected),
                    {
                        "05_governance",
                        "05_governance/rework_declarations",
                        plan.target_path,
                    },
                )

                # The foreign root is byte- and topology-identical: not read
                # into the result, and not created under.
                self.assertEqual(after_foreign, before_foreign)
                joined = " ".join(result.errors)
                for leak in ("inventory", "foreign", "not json", str(foreign)):
                    self.assertNotIn(leak, joined)

    def test_accepted_root_reuse_is_falsifiable_at_both_use_sites(self) -> None:
        """Reverting either use site to the caller's alias must be detectable."""

        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        selected = Path(tmp.name) / "selected"
        foreign = Path(tmp.name) / "foreign"
        selected.mkdir(parents=True)
        foreign.mkdir(parents=True)
        self._seed_inventory(foreign, "malformed")
        plan = self._canonical_plan(selected)

        real_resolve = Path.resolve
        foreign_actual = str(real_resolve(foreign))
        selected_actual = real_resolve(selected)

        # Loader use site: handing the alias to the reader resurfaces exactly
        # the foreign-inventory diagnostic Review 056 reproduced.
        alias_inventory = load_rework_declarations(foreign)
        self.assertFalse(alias_inventory.valid)
        self.assertEqual(
            {item.code for item in alias_inventory.diagnostics},
            {"rework_declaration_unreadable"},
        )
        accepted_inventory = load_rework_declarations(selected_actual)
        self.assertTrue(accepted_inventory.valid, accepted_inventory.diagnostics)
        self.assertNotEqual(alias_inventory.valid, accepted_inventory.valid)

        # Target use site: building the target from the alias would place the
        # governance directory under the foreign root, which containment then
        # rejects -- the mutation-before-refusal Review 056 reproduced.
        alias_target = foreign / plan.target_path
        accepted_target = selected_actual / plan.target_path
        self.assertNotEqual(alias_target.parent, accepted_target.parent)
        self.assertFalse(
            _is_within_for_test(alias_target.parent, selected_actual),
            "alias target must fall outside the accepted root",
        )
        self.assertTrue(_is_within_for_test(accepted_target.parent, selected_actual))

        # And the real writer, with the temporal alias in play, uses neither.
        seen = {"count": 0}

        def temporal_resolve(self, *args, **kwargs):
            actual = real_resolve(self, *args, **kwargs)
            if str(actual) == foreign_actual:
                seen["count"] += 1
                if seen["count"] == 1:
                    return selected_actual
            return actual

        before_foreign = _snapshot(foreign)
        with mock.patch.object(Path, "resolve", temporal_resolve):
            result = write_rework_declaration(
                ReworkDeclarationWriteCommand(project_root=foreign, plan=plan)
            )
        self.assertTrue(result.wrote, result.errors)
        self.assertEqual(_snapshot(foreign), before_foreign)


def _is_within_for_test(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


if __name__ == "__main__":
    unittest.main()
