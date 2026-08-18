"""Q008 report-format contracts emitted by every project composer shape."""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import yaml

import frutlups
import frutlups.handoff as handoff_module
import frutlups.project as project_module
from frutlups import prompt_template
from frutlups._scaffold import _value_has_fence_opener
from frutlups.cli import main
from frutlups.gate import build_planning_frontier_status
from frutlups.project import (
    VerdictRecordWriteCommand,
    build_coding_prompt_plan,
    build_loop_resume_status,
    build_review_prompt_plan,
    build_status,
    build_verdict_record_plan,
    write_verdict_record,
)
from frutlups.prompt_template import (
    CodingPromptTemplate,
    CodingPromptWriteCommand,
    render_coding_prompt,
    write_coding_prompt,
)
from frutlups.review_prompt_template import (
    REVIEW_VERDICT_CHOICES,
    ReviewPromptTemplate,
    _write_review_prompt_content,
    render_review_prompt,
)
from frutlups.review_report import (
    ReviewReportSchema,
    ReviewVerdict,
    default_review_report_schema,
    parse_review_report_verdict_text,
    review_report_format_contract,
)
from frutlups.self_report import (
    SELF_REPORT_REQUIRED_FIELDS,
    SelfReportLocationCommand,
    SelfReportSchema,
    SelfReportValidationCommand,
    self_report_format_contract,
    self_report_schema_for_profile,
    validate_expected_self_report,
)
from test_configured_prompt_scaffold import (
    _make_legacy_project,
    _make_v2_project,
    _make_v2_review_project,
)
from test_make_review_prompt import _simple_review_project
from test_resumable_status import (
    _active_roadmap,
    _detailed_roadmap,
    _make_template,
    _minimal_coding_prompt,
    _write_active_roadmap,
    _write_coding_prompt,
    _write_detailed_roadmap,
    _write_review_prompt,
    _write_self_report,
)


_FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures"
_CODING_CONTRACT_ERROR = (
    "coding report-format contract defect in package-owned definition of done section"
)
_REVIEW_CONTRACT_ERROR = (
    "review report-format contract defect in package-owned definition of done section"
)
_BANNED_LINE_START = re.compile(r"^(?:#|>|-|\*|\+|~|`+|\d+[.)])")


def _snapshot(root: Path) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        result[relative] = (
            ("dir", "")
            if path.is_dir()
            else ("file", hashlib.sha256(path.read_bytes()).hexdigest())
        )
    return result


def _section(content: str, heading: str) -> str:
    marker = f"## {heading}"
    start = content.index(marker) + len(marker)
    following = re.search(r"^## ", content[start:], re.MULTILINE)
    end = start + following.start() if following else len(content)
    return content[start:end]


def _contract_headings(content: str) -> tuple[str, ...]:
    line = next(
        line
        for line in content.splitlines()
        if line.startswith("- Required self-report section names: ")
    )
    return tuple(json.loads(token) for token in re.findall(r'"(?:\\.|[^"\\])*"', line))


def _canonical_verdict_form(content: str) -> str:
    match = re.search(r"Verdict: <value> - next: <one move>", content)
    if match is None:
        raise AssertionError("emitted prompt has no canonical verdict form")
    return match.group(0)


def _report_from_emitted_headings(content: str, *, plain: bool = False) -> str:
    sections: list[str] = []
    for heading in _contract_headings(content):
        normalized = heading.strip().lower()
        if normalized == "files changed":
            body = "- 08_pkg/src/frutlups/project.py"
        elif "verification" in normalized:
            body = "python -m unittest"
        else:
            body = "none"
        marker = f"{heading}:" if plain else f"## {heading}"
        sections.append(f"{marker}\n\n{body}")
    return "\n\n".join(sections) + "\n"


def _write_coding_plan(root: Path, plan) -> Path:
    result = write_coding_prompt(
        CodingPromptWriteCommand(
            project_root=root,
            template=plan.template,
            content=plan.render.content,
            prompt_dir=plan.coding_prompt_dir,
        )
    )
    if not result.wrote:
        raise AssertionError(result.errors)
    return Path(result.target_path)


def _write_review_plan(root: Path, plan) -> Path:
    result = _write_review_prompt_content(
        project_root=root,
        template=plan.template,
        content=plan.render.content,
        overwrite=False,
        prompt_dir=plan.review_prompt_dir,
    )
    if not result.wrote:
        raise AssertionError(result.errors)
    return Path(result.target_path)


def _validate_report(root: Path, template: CodingPromptTemplate, schema, content: str):
    target = root / template.self_report_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return validate_expected_self_report(
        SelfReportValidationCommand(
            location=SelfReportLocationCommand(project_root=root, template=template),
            schema=schema,
        )
    )


def _remove_definition_of_done(path: Path) -> None:
    content = path.read_text(encoding="utf-8")
    prefix, marker, _tail = content.partition("## Definition Of Done")
    if not marker:
        raise AssertionError("fixture has no Definition Of Done section")
    path.write_text(prefix.rstrip() + "\n", encoding="utf-8")


def _remove_definition_slot(path: Path) -> None:
    content = path.read_text(encoding="utf-8")
    prefix, marker, tail = content.partition("## Definition Of Done")
    if not marker or "- TBD" not in tail:
        raise AssertionError("fixture has no Definition Of Done list slot")
    path.write_text(
        prefix + marker + tail.replace("- TBD", "- already authored", 1),
        encoding="utf-8",
    )


def _indent_definition_slot(path: Path, width: int) -> None:
    content = path.read_text(encoding="utf-8")
    prefix, marker, tail = content.partition("## Definition Of Done")
    if not marker or "- TBD" not in tail:
        raise AssertionError("fixture has no Definition Of Done list slot")
    path.write_text(
        prefix + marker + tail.replace("- TBD", " " * width + "- TBD", 1),
        encoding="utf-8",
    )


def _remove_section(path: Path, heading: str) -> None:
    content = path.read_text(encoding="utf-8")
    updated, count = re.subn(
        rf"(?ms)^## {re.escape(heading)}\s*\n.*?(?=^## |\Z)",
        "",
        content,
        count=1,
    )
    if count != 1:
        raise AssertionError(f"fixture has no {heading} section")
    path.write_text(updated, encoding="utf-8")


def _fixed_coding_template() -> CodingPromptTemplate:
    return CodingPromptTemplate(
        sequence=7,
        milestone_id="M001",
        slice_id="M001-S01",
        slug="fixed",
        title="Fixed",
        role_instructions="Role.",
        required_reading=("CLAUDE.md", "README.md"),
        scope_paths=("08_pkg/",),
        non_goals=("Do not X.",),
        definition_of_done=("Done.",),
        verification_commands=("python -m unittest",),
        self_report_path="05_governance/reviews/fixed_self_report.md",
    )


def _fixed_review_template() -> ReviewPromptTemplate:
    return ReviewPromptTemplate(
        sequence=7,
        milestone_id="M001",
        slice_id="M001-S01",
        slug="fixed",
        title="Fixed",
        role_instructions="Role.",
        required_reading=("CLAUDE.md", "README.md"),
        coding_prompt_path="prompts/for_coding_agent/007_fixed.md",
        self_report_path="05_governance/reviews/fixed_self_report.md",
        review_output_path="05_governance/reviews/fixed_review_report.md",
        expected_changed_files=("08_pkg/src/frutlups/project.py",),
        verification_commands=("python -m unittest",),
        severity_guidance=(
            "blocker: correctness",
            "major: behavior",
            "minor: docs",
            "nit: cosmetic",
        ),
        verdict_choices=("pass", "needs_work", "blocked", "override"),
        prior_review_paths=(),
        non_goals=("Do not X.",),
        notes=(),
    )


class ContractBuilderTests(unittest.TestCase):
    def test_self_report_contract_is_exact_and_schema_derived(self) -> None:
        schema = SelfReportSchema(required_fields=("Alpha", "Beta"))
        self.assertEqual(
            self_report_format_contract(schema),
            (
                'Required self-report section names: "Alpha"; "Beta".',
                "Give each required section its own heading, either ## Name or a Name: line "
                "starting at the beginning of a line.",
                "Use each listed section name; only letter case and trailing "
                "punctuation may differ.",
                "Give every required section non-empty content; write none instead "
                "of leaving it blank.",
                "A heading inside a fenced code block, a list item, or a table is not recognized.",
                "Text before the first heading is ignored.",
            ),
        )

    def test_review_contract_is_exact_and_schema_derived(self) -> None:
        schema = replace(
            default_review_report_schema(),
            allowed_verdicts=(ReviewVerdict.PASS, ReviewVerdict.BLOCKED),
        )
        self.assertEqual(
            review_report_format_contract(schema),
            (
                "End the report with a ## Verdict section whose ATX heading text is "
                "exactly Verdict.",
                "Make the first non-empty line under that section exactly "
                "Verdict: <value> - next: <one move>.",
                'Choose <value> as exactly one of: "pass", "blocked".',
                "Use ASCII space-hyphen-space followed by lowercase next: and one space; "
                "an em dash or en dash is rejected.",
                "Put nothing between <value> and the separator: no severity tag, "
                "count, or parenthetical.",
                "Make <one move> non-empty.",
                "State one chosen verdict, never the list of verdict choices.",
            ),
        )

    def test_malformed_builders_never_raise_and_fail_empty_only_without_values(self) -> None:
        malformed_self = SelfReportSchema(required_fields=(None, "", "  ", "Intent"))
        empty_self = SelfReportSchema(required_fields=42)  # type: ignore[arg-type]
        malformed_review = ReviewReportSchema(
            required_fields=(),
            allowed_verdicts=(None, "", "pass"),  # type: ignore[arg-type]
        )
        empty_review = ReviewReportSchema(
            required_fields=(),
            allowed_verdicts=42,  # type: ignore[arg-type]
        )
        self.assertTrue(self_report_format_contract(malformed_self))
        self.assertEqual(self_report_format_contract(empty_self), ())
        self.assertTrue(review_report_format_contract(malformed_review))
        self.assertEqual(review_report_format_contract(empty_review), ())

    def test_every_contract_line_is_safe_for_the_existing_list_slot(self) -> None:
        configured = SelfReportSchema(
            required_fields=("Intent", "field with ``` text", "x_review_report.md")
        )
        for line in self_report_format_contract(configured) + review_report_format_contract():
            with self.subTest(line=line):
                self.assertNotIn("\n", line)
                self.assertIsNone(_BANNED_LINE_START.match(line))
                self.assertFalse(_value_has_fence_opener(line))
                self.assertIsNone(project_module._REVIEW_OUTPUT_CONFIGURED_RE.fullmatch(line))
                self.assertIsNone(project_module._PROMPT_SELF_REPORT_PATH_RE.search(line))


class EmittedRoundTripTests(unittest.TestCase):
    def test_self_report_built_from_each_emitted_coding_shape_clears(self) -> None:
        for name, maker in (
            ("legacy", _make_legacy_project),
            ("configured", _make_v2_project),
        ):
            with self.subTest(shape=name), TemporaryDirectory() as tmp:
                root = Path(tmp)
                maker(root)
                plan = build_coding_prompt_plan(root)
                self.assertTrue(plan.valid, plan.errors)
                written = _write_coding_plan(root, plan)
                emitted = written.read_text(encoding="utf-8")
                schema = self_report_schema_for_profile(build_status(root).layout.profile)
                result = _validate_report(
                    root,
                    plan.template,
                    schema,
                    _report_from_emitted_headings(emitted),
                )
                self.assertTrue(result.valid, result.errors)

    def test_review_built_from_each_emitted_review_shape_clears_every_verdict(self) -> None:
        for name, maker in (
            ("legacy", _simple_review_project),
            ("configured", _make_v2_review_project),
        ):
            with self.subTest(shape=name), TemporaryDirectory() as tmp:
                root = Path(tmp)
                maker(root)
                plan = build_review_prompt_plan(root)
                self.assertTrue(plan.valid, plan.errors)
                emitted = _write_review_plan(root, plan).read_text(encoding="utf-8")
                form = _canonical_verdict_form(emitted)
                for verdict in default_review_report_schema().allowed_verdicts:
                    line = form.replace("<value>", verdict.value).replace(
                        "<one move>", "record the result"
                    )
                    parsed = parse_review_report_verdict_text(f"# Review\n\n## Verdict\n\n{line}\n")
                    self.assertTrue(parsed.valid, (name, verdict, parsed.errors))
                    self.assertEqual(parsed.verdict, verdict)

    def test_all_four_shapes_carry_every_contract_line_in_the_owned_section(self) -> None:
        cases = (
            ("legacy_coding", _make_legacy_project, build_coding_prompt_plan),
            ("configured_coding", _make_v2_project, build_coding_prompt_plan),
            ("legacy_review", _simple_review_project, build_review_prompt_plan),
            ("configured_review", _make_v2_review_project, build_review_prompt_plan),
        )
        for name, maker, builder in cases:
            with self.subTest(shape=name), TemporaryDirectory() as tmp:
                root = Path(tmp)
                maker(root)
                plan = builder(root)
                self.assertTrue(plan.valid, plan.errors)
                content = plan.render.content
                if "coding" in name:
                    contract = self_report_format_contract(
                        self_report_schema_for_profile(build_status(root).layout.profile)
                    )
                    section = _section(
                        content,
                        "Required Self-Report" if name == "legacy_coding" else "Definition Of Done",
                    )
                else:
                    contract = review_report_format_contract()
                    section = _section(
                        content,
                        "Verdict Requirements" if name == "legacy_review" else "Definition Of Done",
                    )
                for line in contract:
                    self.assertIn(f"- {line}", section)
                if name == "configured_review":
                    self.assertLess(
                        section.index("- Write the review report at "),
                        section.index(f"- {contract[0]}"),
                    )
                if name == "legacy_review":
                    self.assertLess(
                        section.index("State the verdict after the severity-ordered findings."),
                        section.index(f"- {contract[0]}"),
                    )


class NegativeReaderMatrixTests(unittest.TestCase):
    def test_review_contract_counterexamples_are_rejected(self) -> None:
        cases = {
            "em_dash": "Verdict: pass — next: move",
            "en_dash": "Verdict: pass – next: move",
            "capital_next": "Verdict: pass - Next: move",
            "no_spaces": "Verdict: pass -next: move",
            "extra_token": "Verdict: needs_work P1 - next: move",
            "empty_move": "Verdict: pass - next:",
            "choice_menu": "Verdict: pass | needs_work | blocked | override - next: move",
        }
        for name, line in cases.items():
            with self.subTest(case=name):
                result = parse_review_report_verdict_text(f"## Verdict\n\n{line}\n")
                self.assertFalse(result.valid)
        self.assertFalse(
            parse_review_report_verdict_text(
                "## Verdict\n\n## Notes\n\nVerdict: pass - next: move\n"
            ).valid
        )

    def test_hyphen_form_clears_heading_and_inline_fallback(self) -> None:
        heading = parse_review_report_verdict_text("## Verdict\n\nVerdict: pass - next: record\n")
        inline = parse_review_report_verdict_text("Findings\n\nVerdict: needs_work - next: fix\n")
        self.assertEqual((heading.valid, heading.verdict), (True, ReviewVerdict.PASS))
        self.assertEqual((inline.valid, inline.verdict), (True, ReviewVerdict.NEEDS_WORK))

    def test_broader_historical_verdict_forms_remain_accepted(self) -> None:
        # The emitted form is canonical guidance, not a parser narrowing.
        for content in (
            "## Verdict\n\npass\n",
            "# verdict\n\nVerdict: pass - next: record\n",
            "## Verdict\n\nVerdict: pass - next: record\n\n## Notes\n\nextra\n",
        ):
            with self.subTest(content=content):
                self.assertTrue(parse_review_report_verdict_text(content).valid)

    def test_self_report_contract_counterexamples_are_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            plan = build_coding_prompt_plan(root)
            schema = self_report_schema_for_profile(build_status(root).layout.profile)
            headings = list(_contract_headings(plan.render.content))
            valid = _report_from_emitted_headings(plan.render.content)
            cases = {
                "missing": valid.replace(f"## {headings[0]}\n\nnone\n\n", "", 1),
                "empty": valid.replace(f"## {headings[0]}\n\nnone", f"## {headings[0]}", 1),
                "bullets": "\n".join(f"- {heading}: none" for heading in headings),
                "table": "\n".join(f"| {heading}: | none |" for heading in headings),
                "renamed": valid.replace(
                    "## Known Limits / Follow-Up", "## Known Limits and Follow-Up"
                ),
                "fenced": f"```markdown\n{valid}```\n",
            }
            for name, content in cases.items():
                with self.subTest(case=name):
                    result = _validate_report(root, plan.template, schema, content)
                    self.assertFalse(result.valid)
            preamble = _validate_report(
                root,
                plan.template,
                schema,
                "ignored preamble\n\n" + valid,
            )
            self.assertTrue(preamble.valid, preamble.errors)


class PresencePostconditionTests(unittest.TestCase):
    def _assert_cli_refusal_is_read_only(self, root: Path, owner: str) -> None:
        before = _snapshot(root)
        command = "make-coding-prompt" if owner == "coding" else "make-review-prompt"
        for extra in (("--dry-run",), ()):
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(main([command, str(root), *extra]), 1)
            self.assertEqual(_snapshot(root), before)

    def test_indented_definition_slot_clears_for_both_owners(self) -> None:
        def column_zero_guard(content: str, contract: tuple[str, ...]) -> bool:
            if not contract:
                return False
            lines = set(content.splitlines())
            return all(f"- {line}" in lines for line in contract)

        for owner, maker, builder, writer, relative in (
            (
                "coding",
                _make_v2_project,
                build_coding_prompt_plan,
                _write_coding_plan,
                "coding_prompt.md",
            ),
            (
                "review",
                _make_v2_review_project,
                build_review_prompt_plan,
                _write_review_plan,
                "review_prompt.md",
            ),
        ):
            for width in (2, 5):
                with self.subTest(owner=owner, width=width), TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    maker(root)
                    _indent_definition_slot(root / "prompts/templates" / relative, width)
                    plan = builder(root)
                    self.assertTrue(plan.valid, plan.errors)
                    profile = build_status(root).layout.profile
                    contract = (
                        self_report_format_contract(self_report_schema_for_profile(profile))
                        if owner == "coding"
                        else review_report_format_contract()
                    )

                    with mock.patch.object(
                        project_module,
                        "_report_format_contract_present",
                        column_zero_guard,
                    ):
                        reverted = builder(root)
                    expected_error = (
                        _CODING_CONTRACT_ERROR if owner == "coding" else _REVIEW_CONTRACT_ERROR
                    )
                    self.assertEqual(reverted.errors, (expected_error,))

                    written_path = writer(root, plan)
                    written_lines = written_path.read_text(encoding="utf-8").splitlines()
                    for line in contract:
                        self.assertIn(" " * width + f"- {line}", written_lines)

    def test_cooccurring_required_section_diagnostics_survive(self) -> None:
        for owner, maker, builder, relative, other_heading, contract_error in (
            (
                "coding",
                _make_v2_project,
                build_coding_prompt_plan,
                "coding_prompt.md",
                "Current State",
                _CODING_CONTRACT_ERROR,
            ),
            (
                "review",
                _make_v2_review_project,
                build_review_prompt_plan,
                "review_prompt.md",
                "Output",
                _REVIEW_CONTRACT_ERROR,
            ),
        ):
            with self.subTest(owner=owner), TemporaryDirectory() as tmp:
                root = Path(tmp)
                maker(root)
                path = root / "prompts/templates" / relative
                _remove_section(path, other_heading)
                _remove_section(path, "Definition Of Done")
                profile = build_status(root).layout.profile
                required = (
                    profile.required_coding_prompt_sections
                    if owner == "coding"
                    else profile.required_review_prompt_sections
                )
                other_error = f"required section {required.index(other_heading) + 1} is missing"
                plan = builder(root)
                self.assertEqual(plan.errors, (other_error, contract_error))

                with mock.patch.object(
                    project_module,
                    "_replace_definition_of_done_missing_error",
                    lambda _errors, _required, replacement: (replacement,),
                ):
                    reverted = builder(root)
                self.assertEqual(reverted.errors, (contract_error,))
                self.assertNotEqual(reverted.errors, plan.errors)

    def test_plain_heading_round_trip_clears_from_emitted_enumeration(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            plan = build_coding_prompt_plan(root)
            self.assertTrue(plan.valid, plan.errors)
            schema = self_report_schema_for_profile(build_status(root).layout.profile)
            report = _report_from_emitted_headings(plan.render.content, plain=True)
            for heading in _contract_headings(plan.render.content):
                self.assertIn(f"{heading}:\n\n", report)
                self.assertNotIn(f"## {heading}", report)
            result = _validate_report(root, plan.template, schema, report)
            self.assertTrue(result.valid, result.errors)

    def test_missing_coding_definition_of_done_has_one_owned_error_and_no_write(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            _remove_definition_of_done(root / "prompts/templates/coding_prompt.md")
            plan = build_coding_prompt_plan(root)
            self.assertEqual(plan.errors, (_CODING_CONTRACT_ERROR,))
            self.assertFalse(plan.valid)
            self.assertFalse(plan.preview.would_write)
            self.assertEqual(plan.render.content, "")
            self._assert_cli_refusal_is_read_only(root, "coding")

    def test_missing_review_definition_of_done_has_one_owned_error_and_no_write(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_review_project(root)
            _remove_definition_of_done(root / "prompts/templates/review_prompt.md")
            plan = build_review_prompt_plan(root)
            self.assertEqual(plan.errors, (_REVIEW_CONTRACT_ERROR,))
            self.assertFalse(plan.valid)
            self.assertFalse(plan.preview.would_write)
            self.assertEqual(plan.render.content, "")
            self._assert_cli_refusal_is_read_only(root, "review")

    def test_heading_present_but_slot_missing_keeps_existing_diagnostic(self) -> None:
        for owner, maker, builder, relative in (
            ("coding", _make_v2_project, build_coding_prompt_plan, "coding_prompt.md"),
            ("review", _make_v2_review_project, build_review_prompt_plan, "review_prompt.md"),
        ):
            with self.subTest(owner=owner), TemporaryDirectory() as tmp:
                root = Path(tmp)
                maker(root)
                _remove_definition_slot(root / "prompts/templates" / relative)
                plan = builder(root)
                self.assertEqual(
                    plan.errors,
                    ("expected slot missing in section 'definition of done'",),
                )
                self.assertFalse(plan.preview.would_write)

    def test_postcondition_rejects_a_reverted_definition_append(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            real = project_module._coding_scaffold_slots

            def without_contract(profile, template, _contract=()):
                return real(profile, template, ())

            with mock.patch.object(project_module, "_coding_scaffold_slots", without_contract):
                plan = build_coding_prompt_plan(root)
            self.assertEqual(plan.errors, (_CODING_CONTRACT_ERROR,))
            self.assertFalse(plan.preview.would_write)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_review_project(root)
            real = project_module._review_scaffold_slots

            def without_contract(profile, template, _contract=()):
                return real(profile, template, ())

            with mock.patch.object(project_module, "_review_scaffold_slots", without_contract):
                plan = build_review_prompt_plan(root)
            self.assertEqual(plan.errors, (_REVIEW_CONTRACT_ERROR,))
            self.assertFalse(plan.preview.would_write)


class DerivationAndDriftTests(unittest.TestCase):
    def test_custom_self_report_headings_change_emission_and_clear(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            path = root / "frutlups.layout.yaml"
            config = yaml.safe_load(path.read_text(encoding="utf-8"))
            config["reports"] = {"self_report_required_headings": ["Alpha", "Beta"]}
            path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
            plan = build_coding_prompt_plan(root)
            self.assertTrue(plan.valid, plan.errors)
            self.assertEqual(_contract_headings(plan.render.content), ("Alpha", "Beta"))
            schema = self_report_schema_for_profile(build_status(root).layout.profile)
            result = _validate_report(
                root,
                plan.template,
                schema,
                _report_from_emitted_headings(plan.render.content),
            )
            self.assertTrue(result.valid, result.errors)

    def test_profile_verdict_values_do_not_control_reader_vocabulary(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_review_project(root)
            path = root / "frutlups.layout.yaml"
            config = yaml.safe_load(path.read_text(encoding="utf-8"))
            config["reports"] = {"verdict_values": ["green", "red"]}
            path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
            plan = build_review_prompt_plan(root)
            self.assertTrue(plan.valid, plan.errors)
            # The parser schema, not the currently unused layout field, owns this vocabulary.
            expected = tuple(v.value for v in default_review_report_schema().allowed_verdicts)
            self.assertEqual(plan.template.verdict_choices, expected)
            self.assertIn('"pass", "needs_work", "blocked", "override"', plan.render.content)
            self.assertNotIn('"green"', plan.render.content)

    def test_all_verdict_constants_agree(self) -> None:
        expected = tuple(v.value for v in ReviewVerdict)
        self.assertEqual(REVIEW_VERDICT_CHOICES, expected)
        self.assertEqual(handoff_module._VERDICT_LABELS, expected)
        self.assertEqual(default_review_report_schema().allowed_verdicts, tuple(ReviewVerdict))

    def test_legacy_renderer_fallback_matches_reader_default(self) -> None:
        self.assertEqual(prompt_template._SELF_REPORT_FALLBACK_FIELDS, SELF_REPORT_REQUIRED_FIELDS)

    def test_contract_builders_do_not_expand_the_documented_export_surface(self) -> None:
        self.assertEqual(len(frutlups.__all__), 152)
        self.assertNotIn("self_report_format_contract", frutlups.__all__)
        self.assertNotIn("review_report_format_contract", frutlups.__all__)


class CompatibilityTests(unittest.TestCase):
    def test_direct_public_renderer_bytes_match_installed_0_1_5(self) -> None:
        coding = render_coding_prompt(_fixed_coding_template())
        review = render_review_prompt(_fixed_review_template())
        self.assertTrue(coding.valid, coding.errors)
        self.assertTrue(review.valid, review.errors)
        self.assertEqual(
            hashlib.sha256(coding.content.encode()).hexdigest(),
            "be614186fc486b91863fb86b2d84340ef934c8e1a26544a332cd164ce834787c",
        )
        self.assertEqual(
            hashlib.sha256(review.content.encode()).hexdigest(),
            "700e8637ca82a60ae7569cdb0eeb0d29c0c3b371dbf2c96a663ffafec580faa6",
        )

    def test_malformed_reports_keep_typed_repair_steps_and_public_shapes(self) -> None:
        expected_status_keys = {
            "accepted_slice_ids",
            "active_roadmap",
            "detailed_roadmap",
            "diagnostics",
            "layout",
            "memory",
            "memory_mode",
            "milestones",
            "missing_required_directories",
            "next_milestone",
            "next_slice",
            "ok",
            "prompt_artifacts",
            "prompt_health",
            "prompts",
            "root",
            "slices",
        }
        expected_resume_keys = {
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
        }
        expected_frontier_keys = {
            "action",
            "actor",
            "block_citation",
            "block_owner",
            "completion_evidence",
            "contract_id",
            "contract_version",
            "diagnostics",
            "outcome",
        }
        for owner in ("self", "review"):
            with self.subTest(owner=owner), TemporaryDirectory() as tmp:
                root = Path(tmp)
                _make_template(root)
                _write_active_roadmap(root, _active_roadmap())
                _write_detailed_roadmap(root, _detailed_roadmap())
                _write_coding_prompt(
                    root, "001_frutlups_m001_s01_first_slice.md", _minimal_coding_prompt(1)
                )
                if owner == "self":
                    _write_self_report(
                        root,
                        "05_governance/reviews/m001_s01_first_slice_self_report.md",
                        "# Self-Report\n\nmissing fields\n",
                    )
                    expected_step = "fix_self_report"
                else:
                    _write_self_report(
                        root, "05_governance/reviews/m001_s01_first_slice_self_report.md"
                    )
                    _write_review_prompt(root, "001_review_m001_s01_first_slice.md")
                    (
                        root / "05_governance/reviews/m001_s01_first_slice_review_report.md"
                    ).write_text("# Review\n\nNo verdict.\n", encoding="utf-8")
                    expected_step = "fix_review_report"
                status = build_status(root)
                resume = build_loop_resume_status(status)
                frontier = build_planning_frontier_status(root)
                self.assertEqual(resume.step.value, expected_step)
                self.assertEqual(set(status.to_dict()), expected_status_keys)
                self.assertEqual(set(resume.to_dict()), expected_resume_keys)
                self.assertEqual(set(frontier.to_dict()), expected_frontier_keys)
                self.assertEqual(frontier.contract_id, "frutlups.planning_frontier")
                self.assertEqual(frontier.contract_version, "1")

    def test_fixture_manifests_reproduce_all_recorded_digests(self) -> None:
        for bundle in ("front_repo_contract", "template_v3_driver_contract"):
            root = _FIXTURE_ROOT / bundle
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            for entry in manifest["fixtures"]:
                relative = entry.get("destination", entry.get("path"))
                with self.subTest(bundle=bundle, path=relative):
                    self.assertEqual(
                        hashlib.sha256((root / relative).read_bytes()).hexdigest(),
                        entry["sha256"],
                    )


class EndToEndConfiguredLoopTests(unittest.TestCase):
    def test_emitted_contracts_reach_acceptance_without_report_repairs(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_v2_project(root)
            observed = [build_loop_resume_status(build_status(root)).step.value]

            coding = build_coding_prompt_plan(root)
            self.assertTrue(coding.valid, coding.errors)
            coding_path = _write_coding_plan(root, coding)
            observed.append(build_loop_resume_status(build_status(root)).step.value)

            schema = self_report_schema_for_profile(build_status(root).layout.profile)
            self_report = _report_from_emitted_headings(coding_path.read_text(encoding="utf-8"))
            validation = _validate_report(root, coding.template, schema, self_report)
            self.assertTrue(validation.valid, validation.errors)
            observed.append(build_loop_resume_status(build_status(root)).step.value)

            review = build_review_prompt_plan(root)
            self.assertTrue(review.valid, review.errors)
            review_path = _write_review_plan(root, review)
            observed.append(build_loop_resume_status(build_status(root)).step.value)

            form = _canonical_verdict_form(review_path.read_text(encoding="utf-8"))
            verdict_line = form.replace("<value>", "pass").replace(
                "<one move>", "record the verdict"
            )
            report_path = root / review.template.review_output_path
            report_path.write_text(f"# Review\n\n## Verdict\n\n{verdict_line}\n", encoding="utf-8")
            observed.append(build_loop_resume_status(build_status(root)).step.value)

            record_plan = build_verdict_record_plan(root, report_path)
            self.assertTrue(record_plan.valid, record_plan.errors)
            write = write_verdict_record(
                VerdictRecordWriteCommand(project_root=root, plan=record_plan)
            )
            self.assertTrue(write.wrote, write.errors)
            observed.append(build_loop_resume_status(build_status(root)).step.value)

            self.assertEqual(
                observed,
                [
                    "make_coding_prompt",
                    "execute_coding_prompt",
                    "make_review_prompt",
                    "execute_review_prompt",
                    "record_verdict",
                    "no_frontier",
                ],
            )
            self.assertNotIn("fix_self_report", observed)
            self.assertNotIn("fix_review_report", observed)


if __name__ == "__main__":
    unittest.main()
