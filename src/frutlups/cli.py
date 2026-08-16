"""Command-line interface for frutlups."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from frutlups.exceptions import FrutlupsError
from frutlups.gate import (
    FinalMilestoneHandoff,
    HumanGate,
    _build_status_resume_and_frontier,
    _final_handoff_from_status,
    _write_final_handoff_artifact_from_status,
    human_gate_from_plan,
)
from frutlups.journal import RunJournalResumeSummary, build_resume_summary
from frutlups.orchestrator import (
    OrchestratorPlan,
    OrchestratorRunResult,
    _plan_from_status,
    _run_one_step_from_status,
)
from frutlups.project import (
    CodingPromptPlan,
    LoopFrontier,
    PlanningFrontierStatus,
    LoopResumeStatus,
    ProjectLayout,
    ProjectStatus,
    ReviewPromptPlan,
    VerdictRecordPlan,
    VerdictRecordWriteCommand,
    VerdictRecordWriteResult,
    _build_coding_prompt_plan_from_status,
    _build_frontier_from_status,
    _build_review_prompt_plan_from_status,
    _build_status_with_evidence,
    _build_verdict_record_plan_from_layout,
    _layout_fallback_label_message,
    _layout_mutation_blockers,
    _layout_mutation_refusal_message,
    build_loop_resume_status,
    build_rework_declaration_plan,
    build_status,
    write_verdict_record,
)
from frutlups.rework import (
    ReworkDeclarationPlan,
    ReworkDeclarationWriteCommand,
    ReworkDeclarationWriteResult,
    write_rework_declaration,
)
from frutlups.prompt_template import (
    CodingPromptWriteCommand,
    CodingPromptWriteResult,
    write_coding_prompt,
)
from frutlups.review_prompt_template import (
    ReviewPromptWriteResult,
    _write_review_prompt_content,
    write_review_prompt,
)


def _emit_layout_fallback_label(blockers) -> None:
    """Label read-only fallback orientation on stderr when mutation is blocked."""

    if blockers:
        print(f"frutlups: {_layout_fallback_label_message(blockers)}", file=sys.stderr)


def _emit_layout_mutation_refusal(blockers) -> None:
    """Print the named, deterministic mutation refusal to stderr."""

    print(f"frutlups: {_layout_mutation_refusal_message(blockers)}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI."""

    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "status":
            # M003-S06/Prompt 031: the status, resume, and versioned planning
            # frontier derive from one selected layout and one acceptance-
            # evidence snapshot (single selection, single scan).
            status, resume, planning_frontier = _build_status_resume_and_frontier(
                args.path, layout_config=args.layout_config
            )
            _emit_layout_fallback_label(_layout_mutation_blockers(status.layout))
            if args.json:
                d = status.to_dict()
                d["loop_resume"] = resume.to_dict()
                d["planning_frontier"] = planning_frontier.to_dict()
                print(json.dumps(d, indent=2, sort_keys=True))
            else:
                print(_format_status(status, resume, planning_frontier))
            return 0
        if args.command == "next":
            status = build_status(args.path, layout_config=args.layout_config)
            _emit_layout_fallback_label(_layout_mutation_blockers(status.layout))
            frontier = _build_frontier_from_status(status)
            if args.json:
                print(json.dumps(frontier.to_dict(), indent=2, sort_keys=True))
            else:
                print(_format_next(frontier))
            return 0
        if args.command == "orchestrator-plan":
            # Prompt 031: one selected layout and one acceptance scan for the
            # whole plan/gate/resume-summary composition.
            status, evidence = _build_status_with_evidence(
                args.path, layout_config=args.layout_config
            )
            _emit_layout_fallback_label(_layout_mutation_blockers(status.layout))
            resume_out: list = []
            plan = _plan_from_status(status, evidence=evidence, resume_out=resume_out)
            # Read-only: read the run journal to summarize resume state without
            # writing a journal entry (planning is never journaled).
            resume_summary = build_resume_summary(
                args.path,
                layout_config=args.layout_config,
                resume=resume_out[0],
                root=status.root,
            )
            gate = human_gate_from_plan(plan)
            if args.json:
                payload = plan.to_dict()
                payload["resume"] = resume_summary.to_dict()
                payload["human_gate"] = gate.to_dict()
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print(_format_orchestrator_plan(plan))
                print(_format_human_gate(gate))
                print(_format_resume_summary(resume_summary))
            return 0
        if args.command == "orchestrator-run":
            # Prompt 031: one selected layout and one acceptance scan for the
            # whole run/gate/resume-summary composition.
            status, evidence = _build_status_with_evidence(
                args.path, layout_config=args.layout_config
            )
            blockers = _layout_mutation_blockers(status.layout)
            if args.dry_run:
                _emit_layout_fallback_label(blockers)
            resume_out: list = []
            run_result, policy = _run_one_step_from_status(
                status,
                dry_run=args.dry_run,
                journal=True,
                evidence=evidence,
                resume_out=resume_out,
            )
            resume_summary = build_resume_summary(
                args.path,
                layout_config=args.layout_config,
                resume=resume_out[0],
                root=status.root,
            )
            gate = human_gate_from_plan(run_result.plan)
            if args.json:
                payload = run_result.to_dict()
                payload["resume"] = resume_summary.to_dict()
                payload["human_gate"] = gate.to_dict()
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print(_format_orchestrator_run(run_result))
                print(_format_human_gate(gate))
                print(_format_resume_summary(resume_summary))
            # An error-severity selected-layout diagnostic is a named refusal of
            # mutation authority (M002-S04): non-zero, and nothing was written or
            # journaled (run_one_step applies the same policy as defense in depth).
            if blockers and not args.dry_run:
                _emit_layout_mutation_refusal(blockers)
                return 2
            # M003-S04: a runner-policy refusal (unsupported posture or matched
            # stop condition) is a named non-zero refusal.
            if policy.refusal_reason and not args.dry_run:
                return 2
            # Exit non-zero only when a non-dry-run execution was attempted but no
            # artifact was written (a genuine execution failure). Dry runs and
            # safe refusals are reported as exit 0.
            if not run_result.dry_run and run_result.attempted and not run_result.wrote:
                return 1
            return 0
        if args.command == "orchestrator-handoff":
            # Read-only by default; writes the artifact only with --write.
            if args.write:
                status, evidence = _build_status_with_evidence(
                    args.path, layout_config=args.layout_config
                )
                blockers = _layout_mutation_blockers(status.layout)
                handoff, write_result = _write_final_handoff_artifact_from_status(
                    status,
                    milestone_id=args.milestone,
                    overwrite=args.overwrite,
                    evidence=evidence,
                )
                if args.json:
                    payload = handoff.to_dict()
                    payload["write_result"] = write_result.to_dict()
                    print(json.dumps(payload, indent=2, sort_keys=True))
                else:
                    if write_result.wrote:
                        print(f"Final handoff written: {write_result.target_path}")
                    else:
                        for err in write_result.errors:
                            print(f"frutlups: {err}", file=sys.stderr)
                        print(_format_final_handoff(handoff))
                # An error-severity selected-layout diagnostic is a named refusal
                # of mutation authority (M002-S04); the composite writer refused
                # before creating anything.
                if blockers:
                    return 2
                return 0 if write_result.wrote else 1
            status, evidence = _build_status_with_evidence(
                args.path, layout_config=args.layout_config
            )
            _emit_layout_fallback_label(_layout_mutation_blockers(status.layout))
            handoff = _final_handoff_from_status(status, args.milestone, evidence=evidence)
            if args.json:
                print(json.dumps(handoff.to_dict(), indent=2, sort_keys=True))
            else:
                print(handoff.render())
            return 0
        if args.command == "make-review-prompt":
            status = build_status(args.path, layout_config=args.layout_config)
            blockers = _layout_mutation_blockers(status.layout)
            review_plan = _build_review_prompt_plan_from_status(
                status,
                sequence=args.sequence,
                slug=args.slug,
                overwrite=args.overwrite,
            )
            if blockers:
                # M002-S04: the selected-layout authority decision governs even
                # when the native plan is independently invalid; nothing is
                # written in either mode.
                if args.dry_run:
                    _emit_layout_fallback_label(blockers)
                    if not review_plan.valid:
                        for err in review_plan.errors:
                            print(f"frutlups: {err}", file=sys.stderr)
                    if args.json:
                        print(json.dumps(review_plan.to_dict(), indent=2, sort_keys=True))
                    elif review_plan.valid:
                        print(_format_review_prompt_plan(review_plan, write_result=None))
                    return 0
                _emit_layout_mutation_refusal(blockers)
                if args.json:
                    print(json.dumps(review_plan.to_dict(), indent=2, sort_keys=True))
                elif review_plan.valid:
                    print(_format_review_prompt_plan(review_plan, write_result=None))
                return 2
            if not review_plan.valid:
                for err in review_plan.errors:
                    print(f"frutlups: {err}", file=sys.stderr)
                if args.json:
                    print(json.dumps(review_plan.to_dict(), indent=2, sort_keys=True))
                return 1
            if args.dry_run:
                if args.json:
                    print(json.dumps(review_plan.to_dict(), indent=2, sort_keys=True))
                else:
                    print(_format_review_prompt_plan(review_plan, write_result=None))
                return 0
            if review_plan.template is None:
                print("frutlups: review prompt template unavailable", file=sys.stderr)
                return 1
            review_write_result = _write_review_prompt_content(
                project_root=review_plan.frontier.root,
                template=review_plan.template,
                content=review_plan.render.content,
                overwrite=args.overwrite,
                prompt_dir=review_plan.review_prompt_dir,
            )
            result_dict = review_plan.to_dict()
            result_dict["write_result"] = review_write_result.to_dict()
            if args.json:
                print(json.dumps(result_dict, indent=2, sort_keys=True))
            else:
                print(_format_review_prompt_plan(review_plan, write_result=review_write_result))
            return 0 if review_write_result.wrote else 1
        if args.command == "make-coding-prompt":
            status, evidence = _build_status_with_evidence(
                args.path, layout_config=args.layout_config
            )
            blockers = _layout_mutation_blockers(status.layout)
            coding_plan = _build_coding_prompt_plan_from_status(
                status,
                sequence=args.sequence,
                slug=args.slug,
                evidence=evidence,
            )
            if blockers:
                # M002-S04: the selected-layout authority decision governs even
                # when the native plan is independently invalid; nothing is
                # written in either mode.
                if args.dry_run:
                    _emit_layout_fallback_label(blockers)
                    if not coding_plan.valid:
                        for err in coding_plan.errors:
                            print(f"frutlups: {err}", file=sys.stderr)
                    if args.json:
                        print(json.dumps(coding_plan.to_dict(), indent=2, sort_keys=True))
                    elif coding_plan.valid:
                        print(_format_coding_prompt_plan(coding_plan, write_result=None))
                    return 0
                _emit_layout_mutation_refusal(blockers)
                if args.json:
                    print(json.dumps(coding_plan.to_dict(), indent=2, sort_keys=True))
                elif coding_plan.valid:
                    print(_format_coding_prompt_plan(coding_plan, write_result=None))
                return 2
            if not coding_plan.valid:
                for err in coding_plan.errors:
                    print(f"frutlups: {err}", file=sys.stderr)
                if args.json:
                    print(json.dumps(coding_plan.to_dict(), indent=2, sort_keys=True))
                return 1
            if args.dry_run:
                if args.json:
                    print(json.dumps(coding_plan.to_dict(), indent=2, sort_keys=True))
                else:
                    print(_format_coding_prompt_plan(coding_plan, write_result=None))
                return 0
            if coding_plan.template is None or coding_plan.render is None:
                print("frutlups: coding prompt template unavailable", file=sys.stderr)
                return 1
            coding_write_cmd = CodingPromptWriteCommand(
                project_root=coding_plan.frontier.root,
                template=coding_plan.template,
                content=coding_plan.render.content,
                overwrite=args.overwrite,
                prompt_dir=coding_plan.coding_prompt_dir,
            )
            coding_write_result = write_coding_prompt(coding_write_cmd)
            result_dict = coding_plan.to_dict()
            result_dict["write_result"] = coding_write_result.to_dict()
            if args.json:
                print(json.dumps(result_dict, indent=2, sort_keys=True))
            else:
                print(_format_coding_prompt_plan(coding_plan, write_result=coding_write_result))
            return 0 if coding_write_result.wrote else 1
        if args.command == "declare-rework":
            rework_plan = build_rework_declaration_plan(
                args.path,
                pass_id=args.pass_id,
                slice_ids=tuple(args.slice_ids),
                layout_config=args.layout_config,
            )
            if rework_plan.mutation_refused:
                if args.dry_run:
                    if rework_plan.fallback_label:
                        print(f"frutlups: {rework_plan.fallback_label}", file=sys.stderr)
                    for err in rework_plan.errors:
                        print(f"frutlups: {err}", file=sys.stderr)
                    if args.json:
                        print(json.dumps(rework_plan.to_dict(), indent=2, sort_keys=True))
                    return 0
                for err in rework_plan.errors:
                    print(f"frutlups: {err}", file=sys.stderr)
                if args.json:
                    print(json.dumps(rework_plan.to_dict(), indent=2, sort_keys=True))
                return 2
            if not rework_plan.valid:
                for err in rework_plan.errors:
                    print(f"frutlups: {err}", file=sys.stderr)
                if args.json:
                    print(json.dumps(rework_plan.to_dict(), indent=2, sort_keys=True))
                return 1
            if args.dry_run:
                if args.json:
                    print(json.dumps(rework_plan.to_dict(), indent=2, sort_keys=True))
                else:
                    print(_format_rework_declaration_plan(rework_plan, None))
                return 0
            result = write_rework_declaration(
                ReworkDeclarationWriteCommand(
                    project_root=rework_plan.root,
                    plan=rework_plan,
                )
            )
            payload = rework_plan.to_dict()
            payload["write_result"] = result.to_dict()
            if args.json:
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print(_format_rework_declaration_plan(rework_plan, result))
            return 0 if result.wrote else 1
        if args.command == "record-verdict":
            try:
                layout: ProjectLayout | None = ProjectLayout.discover(
                    args.path, layout_config=args.layout_config
                )
                discover_error: Exception | None = None
            except Exception as exc:  # preserved fail-closed invalid-plan behavior
                layout = None
                discover_error = exc
            blockers = (
                _layout_mutation_blockers(layout.loaded) if layout is not None else ()
            )
            verdict_plan = _build_verdict_record_plan_from_layout(
                layout, discover_error, args.review_report, overwrite=args.overwrite
            )
            if blockers:
                # M002-S04: the selected-layout authority decision governs even
                # when the native plan is independently invalid; nothing is
                # written in either mode.
                if args.dry_run:
                    _emit_layout_fallback_label(blockers)
                    if not verdict_plan.valid:
                        for err in verdict_plan.errors:
                            print(f"frutlups: {err}", file=sys.stderr)
                    if args.json:
                        print(json.dumps(verdict_plan.to_dict(), indent=2, sort_keys=True))
                    elif verdict_plan.valid:
                        print(_format_verdict_record_plan(verdict_plan, write_result=None))
                    return 0
                _emit_layout_mutation_refusal(blockers)
                if args.json:
                    print(json.dumps(verdict_plan.to_dict(), indent=2, sort_keys=True))
                elif verdict_plan.valid:
                    print(_format_verdict_record_plan(verdict_plan, write_result=None))
                return 2
            if not verdict_plan.valid:
                for err in verdict_plan.errors:
                    print(f"frutlups: {err}", file=sys.stderr)
                if args.json:
                    print(json.dumps(verdict_plan.to_dict(), indent=2, sort_keys=True))
                return 1
            if args.dry_run:
                if args.json:
                    print(json.dumps(verdict_plan.to_dict(), indent=2, sort_keys=True))
                else:
                    print(_format_verdict_record_plan(verdict_plan, write_result=None))
                return 0
            verdict_write_cmd = VerdictRecordWriteCommand(
                project_root=verdict_plan.root,
                plan=verdict_plan,
                overwrite=args.overwrite,
            )
            verdict_write_result = write_verdict_record(verdict_write_cmd)
            result_dict = verdict_plan.to_dict()
            result_dict["write_result"] = verdict_write_result.to_dict()
            if args.json:
                print(json.dumps(result_dict, indent=2, sort_keys=True))
            else:
                print(_format_verdict_record_plan(verdict_plan, write_result=verdict_write_result))
            return 0 if verdict_write_result.wrote else 1
        parser.print_help()
        return 0
    except FrutlupsError as exc:
        print(f"frutlups: {exc}", file=sys.stderr)
        return 2


_TOP_DESCRIPTION = """\
frutlups orchestrates an artifact-first coder/reviewer development loop.

Repository files are the source of truth. The CLI helps you read loop state and
move work through the cycle:

  roadmap slice -> coding prompt -> coder self-report -> review prompt
  -> reviewer verdict -> verdict record -> next slice

Run a command with --help for command-specific options and examples. All
commands are read-only except declare-rework, make-coding-prompt,
make-review-prompt, and record-verdict, which write a single repository artifact
(and support --dry-run).
"""

_TOP_EPILOG = """\
common workflow (PowerShell, from 08_pkg):

  # see where the loop stands and what to do next
  .\\.venv\\Scripts\\python.exe -m frutlups status ..
  .\\.venv\\Scripts\\python.exe -m frutlups next ..

  # write the next coding prompt for the inferred frontier
  .\\.venv\\Scripts\\python.exe -m frutlups make-coding-prompt .. --dry-run
  .\\.venv\\Scripts\\python.exe -m frutlups make-coding-prompt ..

  # after a completed-roadmap holistic finding, declare bounded slice rework
  .\\.venv\\Scripts\\python.exe -m frutlups declare-rework .. `
      --pass-id holistic_pass_001 --slice M003-S03 --slice M006-S01

  # after the coder writes the self-report, write the matching review prompt
  .\\.venv\\Scripts\\python.exe -m frutlups make-review-prompt ..

  # record the verdict once a review report exists
  # (--review-report is resolved from the current directory; from 08_pkg use ..\\)
  .\\.venv\\Scripts\\python.exe -m frutlups record-verdict .. `
      --review-report ..\\05_governance\\reviews\\<slice>_review_report.md

Add --json to status/next/declare-rework/make-*/record-verdict for
machine-readable output.
"""

_STATUS_DESCRIPTION = (
    "Show read-only project status: template health, active roadmap, the next "
    "milestone/slice, prompt inventory and health, memory state, diagnostics, "
    "and the current loop step with its suggested next command."
)
_STATUS_EPILOG = """\
examples (PowerShell, from 08_pkg):

  .\\.venv\\Scripts\\python.exe -m frutlups status ..
  .\\.venv\\Scripts\\python.exe -m frutlups status .. --json
"""

_NEXT_DESCRIPTION = (
    "Show the artifact-inferred loop frontier (read-only): the next slice to "
    "work on, inferred from the roadmap and recorded verdicts. Does not write "
    "or change any artifact."
)
_NEXT_EPILOG = """\
examples (PowerShell, from 08_pkg):

  .\\.venv\\Scripts\\python.exe -m frutlups next ..
  .\\.venv\\Scripts\\python.exe -m frutlups next .. --json
"""

_ORCH_DESCRIPTION = (
    "Show an advisory, read-only dry-run plan for the next step of the local "
    "loop. It reports the current loop step, the actor who must act, the "
    "recommended next command, and whether that command would be a candidate "
    "for safe automatic local execution by a future one-step executor. It never "
    "writes artifacts and never runs the recommended command."
)
_ORCH_EPILOG = """\
examples (PowerShell, from 08_pkg):

  .\\.venv\\Scripts\\python.exe -m frutlups orchestrator-plan ..
  .\\.venv\\Scripts\\python.exe -m frutlups orchestrator-plan .. --dry-run
  .\\.venv\\Scripts\\python.exe -m frutlups orchestrator-plan .. --json

The planner is advisory only: it recommends, but never executes, the next
command. --dry-run is the only supported mode and is accepted for clarity.
"""

_ORCHRUN_DESCRIPTION = (
    "Run at most one safe local artifact command for the current loop step. It "
    "composes the orchestrator plan and executes exactly one step only when the "
    "plan marks it safe for automatic local execution — limited to the local "
    "artifact writes make-coding-prompt, make-review-prompt, and record-verdict. "
    "It dispatches to internal write functions by typed loop step; it never runs "
    "a shell or the recommended-command text. Every other step (coder/reviewer "
    "execution, fixes, human stop/go, no-frontier, ambiguous) is refused with a "
    "reason. Use --dry-run to report the plan without writing a "
    "prompt/review/verdict artifact (it still appends one run-journal entry). "
    "Output includes a human_gate block describing the current stop/go state."
)
_ORCHRUN_EPILOG = """\
examples (PowerShell, from 08_pkg):

  # report what one step would do; writes no prompt/review/verdict artifact
  # (still appends one run-journal entry as resume evidence)
  .\\.venv\\Scripts\\python.exe -m frutlups orchestrator-run .. --once --dry-run

  # execute exactly one safe artifact command if the current step is safe
  .\\.venv\\Scripts\\python.exe -m frutlups orchestrator-run .. --once
  .\\.venv\\Scripts\\python.exe -m frutlups orchestrator-run .. --once --json

--once is the default and only mode in this slice (one step per invocation). The
runner never executes coder/reviewer agents, never runs unsafe or ambiguous
steps, and never uses a shell.
"""

_HANDOFF_DESCRIPTION = (
    "Show the final milestone handoff for the local orchestrator: the current "
    "human gate state, live loop step, latest slice artifacts, run-journal "
    "summary, and validation commands a human should run or inspect. Read-only by "
    "default. With --write it explicitly writes the handoff markdown under the "
    "project root (05_governance/orchestrator/m016_final_handoff.md). The handoff "
    "never commits, opens pull requests, dispatches agents, mutates memory, or "
    "bypasses a human decision."
)
_HANDOFF_EPILOG = """\
examples (PowerShell, from 08_pkg):

  # read-only: print the handoff (text or JSON), write nothing
  .\\.venv\\Scripts\\python.exe -m frutlups orchestrator-handoff .. --json

  # explicitly write the handoff artifact under the project root
  .\\.venv\\Scripts\\python.exe -m frutlups orchestrator-handoff .. --write
"""

_MCP_DESCRIPTION = (
    "Write a coding prompt for the current inferred frontier. The sequence and "
    "slug are computed from the roadmap and existing prompts unless overridden. "
    "Use --dry-run to preview the target path and rendered prompt without "
    "writing."
)
_MCP_EPILOG = """\
examples (PowerShell, from 08_pkg):

  # preview only
  .\\.venv\\Scripts\\python.exe -m frutlups make-coding-prompt .. --dry-run

  # write the prompt
  .\\.venv\\Scripts\\python.exe -m frutlups make-coding-prompt ..

  # replace an existing prompt at a chosen sequence
  .\\.venv\\Scripts\\python.exe -m frutlups make-coding-prompt .. --sequence 72 --overwrite
"""

_REWORK_DESCRIPTION = (
    "Write one immutable, versioned declaration that reopens a bounded set of "
    "already-accepted roadmap slices after a genuinely complete planning "
    "frontier. Slice identifiers are canonicalized to roadmap order and every "
    "reopened slice must earn a fresh prompt-linked accepted evidence chain."
)
_REWORK_EPILOG = """\
examples (PowerShell, from 08_pkg):

  # preview the declaration; writes nothing
  .\\.venv\\Scripts\\python.exe -m frutlups declare-rework .. `
      --pass-id holistic_pass_001 --slice M003-S03 --slice M006-S01 --dry-run

  # write exactly one append-only declaration artifact
  .\\.venv\\Scripts\\python.exe -m frutlups declare-rework .. `
      --pass-id holistic_pass_001 --slice M003-S03 --slice M006-S01
"""

_MRP_DESCRIPTION = (
    "Write a review prompt for the latest unmatched coding prompt. The coder "
    "creates this after the self-report exists; the command validates the "
    "expected self-report and derives review evidence. Use --dry-run to preview "
    "without writing, or --sequence to target a specific coding prompt."
)
_MRP_EPILOG = """\
examples (PowerShell, from 08_pkg):

  # preview the review prompt for the latest unmatched coding prompt
  .\\.venv\\Scripts\\python.exe -m frutlups make-review-prompt .. --dry-run

  # write it
  .\\.venv\\Scripts\\python.exe -m frutlups make-review-prompt ..

  # target a specific coding-prompt sequence
  .\\.venv\\Scripts\\python.exe -m frutlups make-review-prompt .. --sequence 72
"""

_RV_DESCRIPTION = (
    "Parse the verdict from a review report and write a governance verdict "
    "record next to it. The reviewed slice and target record path are derived "
    "from the review report filename. Use --dry-run to preview the parsed "
    "verdict and next action without writing."
)
_RV_EPILOG = """\
examples (PowerShell, from 08_pkg):

  # --review-report is resolved from the current directory, so from 08_pkg
  # prefix it with ..\\ to reach the project root.

  # preview the parsed verdict and next action
  .\\.venv\\Scripts\\python.exe -m frutlups record-verdict .. `
      --review-report ..\\05_governance\\reviews\\<slice>_review_report.md --dry-run

  # write the verdict record
  .\\.venv\\Scripts\\python.exe -m frutlups record-verdict .. `
      --review-report ..\\05_governance\\reviews\\<slice>_review_report.md
"""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="frutlups",
        description=_TOP_DESCRIPTION,
        epilog=_TOP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(
        dest="command",
        title="commands",
        metavar="<command>",
    )

    status_parser = subparsers.add_parser(
        "status",
        help="show read-only project and loop status",
        description=_STATUS_DESCRIPTION,
        epilog=_STATUS_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    status_parser.add_argument(
        "path",
        nargs="?",
        default=Path.cwd(),
        type=Path,
        help="project root or any path inside it (default: current directory)",
    )
    status_parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    next_parser = subparsers.add_parser(
        "next",
        help="show the artifact-inferred next slice (read-only)",
        description=_NEXT_DESCRIPTION,
        epilog=_NEXT_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    next_parser.add_argument(
        "path",
        nargs="?",
        default=Path.cwd(),
        type=Path,
        help="project root or any path inside it (default: current directory)",
    )
    next_parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    orch_parser = subparsers.add_parser(
        "orchestrator-plan",
        help="show the advisory dry-run plan for the next local loop command (read-only)",
        description=_ORCH_DESCRIPTION,
        epilog=_ORCH_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    orch_parser.add_argument(
        "path",
        nargs="?",
        default=Path.cwd(),
        type=Path,
        help="project root or any path inside it (default: current directory)",
    )
    orch_parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    orch_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="advisory only; the planner never executes the recommended command "
        "(this flag is accepted for clarity and is the only supported mode)",
    )

    orchrun_parser = subparsers.add_parser(
        "orchestrator-run",
        help="run at most one safe local artifact command for the current loop step",
        description=_ORCHRUN_DESCRIPTION,
        epilog=_ORCHRUN_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    orchrun_parser.add_argument(
        "path",
        nargs="?",
        default=Path.cwd(),
        type=Path,
        help="project root or any path inside it (default: current directory)",
    )
    orchrun_parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    orchrun_parser.add_argument(
        "--once",
        action="store_true",
        help="run at most one step (the default and only mode in this slice)",
    )
    orchrun_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report the plan without writing a prompt/review/verdict artifact "
        "(it still appends one run-journal entry as resume evidence)",
    )

    handoff_parser = subparsers.add_parser(
        "orchestrator-handoff",
        help="show (or, with --write, write) the final M016 milestone handoff",
        description=_HANDOFF_DESCRIPTION,
        epilog=_HANDOFF_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    handoff_parser.add_argument(
        "path",
        nargs="?",
        default=Path.cwd(),
        type=Path,
        help="project root or any path inside it (default: current directory)",
    )
    handoff_parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    handoff_parser.add_argument(
        "--write",
        action="store_true",
        help="explicitly write the handoff artifact under the project root (read-only by default)",
    )
    handoff_parser.add_argument(
        "--overwrite", action="store_true", help="replace an existing handoff artifact"
    )
    handoff_parser.add_argument(
        "--milestone", default="M016", help="milestone id for the handoff (default: M016)"
    )

    mrp_parser = subparsers.add_parser(
        "make-review-prompt",
        help="write a review prompt for the latest unmatched coding prompt",
        description=_MRP_DESCRIPTION,
        epilog=_MRP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mrp_parser.add_argument(
        "path",
        nargs="?",
        default=Path.cwd(),
        type=Path,
        help="project root or any path inside it (default: current directory)",
    )
    mrp_parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    mrp_parser.add_argument(
        "--dry-run", action="store_true", help="preview the prompt without writing it"
    )
    mrp_parser.add_argument(
        "--overwrite", action="store_true", help="replace an existing review prompt file"
    )
    mrp_parser.add_argument(
        "--sequence",
        type=int,
        default=None,
        help="target a specific coding-prompt sequence instead of the latest unmatched one",
    )
    mrp_parser.add_argument(
        "--slug", type=str, default=None, help="override the derived review-prompt slug"
    )

    mcp_parser = subparsers.add_parser(
        "make-coding-prompt",
        help="write a coding prompt for the current inferred frontier",
        description=_MCP_DESCRIPTION,
        epilog=_MCP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mcp_parser.add_argument(
        "path",
        nargs="?",
        default=Path.cwd(),
        type=Path,
        help="project root or any path inside it (default: current directory)",
    )
    mcp_parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    mcp_parser.add_argument(
        "--dry-run", action="store_true", help="preview the prompt without writing it"
    )
    mcp_parser.add_argument(
        "--overwrite", action="store_true", help="replace an existing prompt file"
    )
    mcp_parser.add_argument(
        "--sequence", type=int, default=None, help="override the computed prompt sequence"
    )
    mcp_parser.add_argument(
        "--slug", type=str, default=None, help="override the computed prompt slug"
    )

    rework_parser = subparsers.add_parser(
        "declare-rework",
        help="reopen accepted slices from a completed planning frontier",
        description=_REWORK_DESCRIPTION,
        epilog=_REWORK_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    rework_parser.add_argument(
        "path",
        nargs="?",
        default=Path.cwd(),
        type=Path,
        help="project root or any path inside it (default: current directory)",
    )
    rework_parser.add_argument(
        "--pass-id",
        required=True,
        help="stable lowercase pass identity, such as holistic_pass_001",
    )
    rework_parser.add_argument(
        "--slice",
        dest="slice_ids",
        action="append",
        required=True,
        metavar="<MNNN-SNN>",
        help="accepted roadmap slice to reopen; repeat for multiple slices",
    )
    rework_parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    rework_parser.add_argument(
        "--dry-run", action="store_true", help="preview the declaration without writing it"
    )

    rv_parser = subparsers.add_parser(
        "record-verdict",
        help="parse a review report verdict and write a governance record",
        description=_RV_DESCRIPTION,
        epilog=_RV_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    rv_parser.add_argument(
        "path",
        nargs="?",
        default=Path.cwd(),
        type=Path,
        help="project root or any path inside it (default: current directory)",
    )
    rv_parser.add_argument(
        "--review-report",
        required=True,
        help="path to the review report markdown file to record a verdict for",
    )
    rv_parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    rv_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="preview the parsed verdict and next action without writing",
    )
    rv_parser.add_argument(
        "--overwrite", action="store_true", help="replace an existing verdict record"
    )

    # All loop commands accept an explicit layout/profile config. When omitted, the
    # profile is auto-detected (project frutlups.layout.yaml, else a v2 default when
    # PROJECT_STATE.md is present, else the legacy compatibility fallback).
    for layout_aware in (
        status_parser,
        next_parser,
        orch_parser,
        orchrun_parser,
        handoff_parser,
        mrp_parser,
        mcp_parser,
        rework_parser,
        rv_parser,
    ):
        layout_aware.add_argument(
            "--layout-config",
            type=Path,
            default=None,
            metavar="<path>",
            help="path to a frutlups.layout.yaml profile config (default: auto-detect)",
        )

    return parser


def _format_rework_declaration_plan(
    plan: ReworkDeclarationPlan,
    write_result: ReworkDeclarationWriteResult | None,
) -> str:
    lines = [f"Project: {plan.root}"]
    if plan.declaration is not None:
        declaration = plan.declaration
        lines.append(f"Pass: {declaration.pass_id}")
        lines.append(f"Declaration sequence: {declaration.declaration_sequence:03d}")
        lines.append(f"Prompt baseline: {declaration.baseline_prompt_sequence:03d}")
        lines.append("Slices: " + ", ".join(declaration.slice_ids))
    lines.append(f"Target: {plan.target_path or 'unavailable'}")
    if write_result is None:
        lines.append(
            "Would write: yes (dry-run, not written)" if plan.valid else "Would write: no"
        )
    elif write_result.wrote:
        lines.append(f"Written: {write_result.target_path}")
    else:
        lines.append("Write failed:")
        for err in write_result.errors:
            lines.append(f"  {err}")
    if plan.errors:
        lines.append("Plan errors:")
        for err in plan.errors:
            lines.append(f"  {err}")
    return "\n".join(lines)


def _format_coding_prompt_plan(
    plan: CodingPromptPlan,
    write_result: CodingPromptWriteResult | None,
) -> str:
    lines = [f"Project: {plan.frontier.root}"]
    if plan.frontier.inferred_slice is not None:
        lines.append(
            f"Inferred slice: {plan.frontier.inferred_slice.slice_id}"
            f" - {plan.frontier.inferred_slice.title}"
        )
    else:
        lines.append("Inferred slice: none")
    lines.append(
        f"Sequence: {plan.sequence:03d}"
        if isinstance(plan.sequence, int) and plan.sequence > 0
        else f"Sequence: {plan.sequence}"
    )
    lines.append(f"Slug: {plan.slug}")
    if plan.preview is not None:
        lines.append(f"Target: {plan.preview.target_path}")
        lines.append(f"Render: {'valid' if plan.preview.valid else 'invalid'}")
        if write_result is None:
            status_str = "yes (dry-run, not written)" if plan.preview.would_write else "no"
            lines.append(f"Would write: {status_str}")
    if write_result is not None:
        if write_result.wrote:
            overwrite_note = " (overwrote existing)" if write_result.overwrote else ""
            lines.append(f"Written: {write_result.target_path}{overwrite_note}")
        else:
            lines.append("Write failed:")
            for err in write_result.errors:
                lines.append(f"  {err}")
    if plan.errors:
        lines.append("Plan errors:")
        for err in plan.errors:
            lines.append(f"  {err}")
    return "\n".join(lines)


def _format_next(frontier: LoopFrontier) -> str:
    lines = [f"Project: {frontier.root}"]
    lines.append(
        "Active roadmap: "
        + (str(frontier.active_roadmap) if frontier.active_roadmap else "not found")
    )
    if frontier.inferred_slice is not None and frontier.inferred_milestone is not None:
        lines.append(
            f"Inferred next: {frontier.inferred_slice.slice_id}"
            f" - {frontier.inferred_slice.title}"
            f" (milestone {frontier.inferred_milestone.milestone_id}:"
            f" {frontier.inferred_milestone.title})"
        )
    else:
        lines.append("Inferred next: none")
    if frontier.authored_next_milestone is not None:
        lines.append(
            f"Authored active: {frontier.authored_next_milestone.milestone_id}"
            f" ({frontier.authored_next_milestone.status.value})"
            f" - {frontier.authored_next_milestone.title}"
        )
    else:
        lines.append("Authored active: not found")
    if frontier.authored_next_slice is not None:
        lines.append(
            f"Authored next slice: {frontier.authored_next_slice.slice_id}"
            f" - {frontier.authored_next_slice.title}"
        )
    health = frontier.prompt_health
    if health.ok:
        lines.append("Prompt health: ok")
    else:
        lines.append(f"Prompt health: warnings ({len(health.findings)})")
        for finding in health.findings:
            lines.append(f"  [{finding.severity.value}] {finding.code}: {finding.message}")
    if not frontier.memory.enabled:
        lines.append("Memory: disabled")
    else:
        mem_line = frontier.memory.backend
        if frontier.memory.root:
            mem_line += f" at {frontier.memory.root}"
        if frontier.memory.message:
            mem_line += f" — {frontier.memory.message}"
        lines.append(f"Memory: {mem_line}")
        for diag in frontier.memory.diagnostics:
            lines.append(f"  {diag}")
    if frontier.diagnostics:
        lines.append("Diagnostics:")
        for diagnostic in frontier.diagnostics:
            lines.append(f"  [{diagnostic.severity.value}] {diagnostic.code}: {diagnostic.message}")
    lines.append(f"Action: {frontier.action}")
    return "\n".join(lines)


def _format_orchestrator_plan(plan: OrchestratorPlan) -> str:
    lines = ["Orchestrator plan (dry run — nothing was executed):"]
    lines.append(f"Loop step: {plan.loop_step}")
    lines.append(f"Actor: {plan.actor.value}")
    if plan.frontier_slice_id:
        title = f" - {plan.frontier_slice_title}" if plan.frontier_slice_title else ""
        lines.append(f"Frontier slice: {plan.frontier_slice_id}{title}")
    else:
        lines.append("Frontier slice: none")
    lines.append(f"Recommended command: {plan.recommended_command or 'none'}")
    lines.append(
        "Safe for automatic local execution: " + ("yes" if plan.safe_for_auto_execution else "no")
    )
    lines.append(f"Why: {plan.rationale}")
    lines.append("Executed: no (planner is advisory only)")
    if plan.diagnostics:
        lines.append("Diagnostics:")
        for diag in plan.diagnostics:
            lines.append(f"  {diag}")
    return "\n".join(lines)


def _format_orchestrator_run(result: OrchestratorRunResult) -> str:
    plan = result.plan
    if result.dry_run:
        header = "Orchestrator run (dry run — nothing was executed):"
    elif result.wrote:
        header = "Orchestrator run (one step executed):"
    elif result.refused:
        header = "Orchestrator run (refused — nothing was executed):"
    else:
        header = "Orchestrator run (execution attempted, nothing written):"
    lines = [header]
    lines.append(f"Loop step: {plan.loop_step}")
    lines.append(f"Actor: {plan.actor.value}")
    if plan.frontier_slice_id:
        title = f" - {plan.frontier_slice_title}" if plan.frontier_slice_title else ""
        lines.append(f"Frontier slice: {plan.frontier_slice_id}{title}")
    else:
        lines.append("Frontier slice: none")
    lines.append(
        "Safe for automatic local execution: " + ("yes" if plan.safe_for_auto_execution else "no")
    )
    lines.append(f"Attempted: {'yes' if result.attempted else 'no'}")
    lines.append(f"Wrote artifact: {'yes' if result.wrote else 'no'}")
    if result.artifact_path:
        lines.append(f"Artifact: {result.artifact_path}")
    if result.refused:
        lines.append(f"Refused: {result.refusal_reason}")
    if result.diagnostics:
        lines.append("Diagnostics:")
        for diag in result.diagnostics:
            lines.append(f"  {diag}")
    return "\n".join(lines)


def _format_human_gate(gate: HumanGate) -> str:
    lines = ["Human gate:"]
    lines.append(f"  State: {gate.gate_state}")
    lines.append(f"  Requires human go: {'yes' if gate.requires_human_go else 'no'}")
    if gate.gate_state == "open":
        lines.append("  One safe local artifact step may be run.")
    elif gate.gate_state == "final_handoff":
        lines.append("  Final handoff: a human must inspect accepted evidence and decide next.")
    elif gate.gate_state == "no_frontier":
        lines.append("  No actionable frontier slice.")
    else:
        lines.append(f"  Stopped: {gate.actor} must act next.")
    lines.append(f"  Reason: {gate.reason}")
    lines.append(f"  Recommended human action: {gate.recommended_human_action}")
    return "\n".join(lines)


def _format_final_handoff(handoff: FinalMilestoneHandoff) -> str:
    gate = handoff.gate
    return (
        f"Final handoff for {handoff.milestone_id} (not written):\n"
        f"  Gate: {gate.gate_state} | requires human go: "
        f"{'yes' if gate.requires_human_go else 'no'}\n"
        f"  Recommended path: {handoff.handoff_path}"
    )


def _format_resume_summary(summary: RunJournalResumeSummary) -> str:
    lines = ["Resume (from run journal):"]
    if not summary.has_journal or summary.entry_count == 0:
        lines.append("  No prior orchestrator runs journaled.")
    else:
        latest = summary.latest
        assert latest is not None  # entry_count > 0 implies a latest entry
        lines.append(f"  Last run: {latest.timestamp} ({summary.latest_event_kind})")
        lines.append(f"  Observed loop step: {summary.latest_observed_step}")
        lines.append(f"  Wrote artifact: {'yes' if summary.latest_wrote else 'no'}")
        lines.append(f"  Journal entries: {summary.entry_count}")
        if summary.malformed_count:
            lines.append(f"  Malformed journal lines skipped: {summary.malformed_count}")
        lines.append(f"  Stale vs live status: {'yes' if summary.stale else 'no'}")
    lines.append(f"  Live loop step: {summary.live_loop_step}")
    if summary.recommended_next_command:
        lines.append(f"  Recommended next: {summary.recommended_next_command}")
    return "\n".join(lines)


def _format_review_prompt_plan(
    plan: ReviewPromptPlan,
    write_result: ReviewPromptWriteResult | None,
) -> str:
    lines = [f"Project: {plan.frontier.root}"]
    if plan.selected_coding_prompt is not None:
        lines.append(f"Coding prompt: {plan.selected_coding_prompt.filename}")
    else:
        lines.append("Coding prompt: none selected")
    lines.append(
        f"Sequence: {plan.sequence:03d}"
        if isinstance(plan.sequence, int) and plan.sequence > 0
        else f"Sequence: {plan.sequence}"
    )
    lines.append(f"Slug: {plan.slug}")
    if plan.self_report is not None:
        sr_status = "valid" if plan.self_report.valid else "invalid"
        lines.append(f"Self-report: {sr_status}")
    if plan.preview is not None:
        lines.append(f"Target: {plan.preview.target_path}")
        lines.append(f"Render: {'valid' if plan.preview.valid else 'invalid'}")
        if write_result is None:
            status_str = "yes (dry-run, not written)" if plan.preview.would_write else "no"
            lines.append(f"Would write: {status_str}")
    if write_result is not None:
        if write_result.wrote:
            overwrite_note = " (overwrote existing)" if write_result.overwrote else ""
            lines.append(f"Written: {write_result.target_path}{overwrite_note}")
        else:
            lines.append("Write failed:")
            for err in write_result.errors:
                lines.append(f"  {err}")
    if plan.errors:
        lines.append("Plan errors:")
        for err in plan.errors:
            lines.append(f"  {err}")
    return "\n".join(lines)


def _format_verdict_record_plan(
    plan: VerdictRecordPlan,
    write_result: VerdictRecordWriteResult | None,
) -> str:
    lines = [f"Project: {plan.root}"]
    lines.append(f"Review report: {plan.review_report_path}")
    if plan.reviewed_slice is not None:
        lines.append(f"Slice: {plan.reviewed_slice.slice_id} - {plan.reviewed_slice.title}")
    if plan.parse_result is not None and plan.parse_result.verdict is not None:
        lines.append(f"Verdict: {plan.parse_result.verdict.value}")
    if plan.next_action is not None:
        lines.append(f"Next action: {plan.next_action.kind.value}")
        if plan.next_action.next_slice_id:
            lines.append(f"Next slice: {plan.next_action.next_slice_id}")
        lines.append(f"Message: {plan.next_action.message}")
    if plan.target_path:
        lines.append(f"Target: {plan.target_path}")
    if write_result is None:
        lines.append("Would write: yes (dry-run, not written)")
    elif write_result.wrote:
        overwrite_note = " (overwrote existing)" if write_result.overwrote else ""
        lines.append(f"Written: {write_result.target_path}{overwrite_note}")
    else:
        lines.append("Write failed:")
        for err in write_result.errors:
            lines.append(f"  {err}")
    if plan.errors:
        lines.append("Plan errors:")
        for err in plan.errors:
            lines.append(f"  {err}")
    return "\n".join(lines)


def _format_status(
    status: ProjectStatus,
    resume: LoopResumeStatus | None = None,
    planning_frontier: PlanningFrontierStatus | None = None,
) -> str:
    lines = [
        f"Project: {status.root}",
        f"Template health: {'ok' if status.ok else 'missing required directories'}",
    ]
    if status.missing_required_directories:
        lines.append("Missing: " + ", ".join(sorted(status.missing_required_directories)))
    lines.append(
        "Active roadmap: " + (str(status.active_roadmap) if status.active_roadmap else "not found")
    )
    if status.next_milestone is not None:
        lines.append(
            "Next milestone: "
            f"{status.next_milestone.milestone_id} "
            f"({status.next_milestone.status.value}) - {status.next_milestone.title}"
        )
    else:
        lines.append("Next milestone: not found")
    if status.next_slice is not None:
        lines.append(f"Next slice: {status.next_slice.slice_id} - {status.next_slice.title}")
    lines.append(
        f"Prompts: {status.prompts.coding_count} coding, {status.prompts.review_count} review"
    )
    health = status.prompt_health
    if health.ok:
        lines.append("Prompt health: ok")
    else:
        lines.append(f"Prompt health: warnings ({len(health.findings)})")
        for finding in health.findings:
            lines.append(f"  [{finding.severity.value}] {finding.code}: {finding.message}")
    if not status.memory.enabled:
        lines.append("Memory: disabled")
    else:
        mem_line = status.memory.backend
        if status.memory.root:
            mem_line += f" at {status.memory.root}"
        if status.memory.message:
            mem_line += f" — {status.memory.message}"
        lines.append(f"Memory: {mem_line}")
        for diag in status.memory.diagnostics:
            lines.append(f"  {diag}")
    if status.diagnostics:
        lines.append("Diagnostics:")
        for diagnostic in status.diagnostics:
            lines.append(f"  [{diagnostic.severity.value}] {diagnostic.code}: {diagnostic.message}")
    if resume is not None:
        lines.append(f"Loop step: {resume.step.value} — {resume.message}")
        if resume.next_command:
            lines.append(f"Next command: {resume.next_command}")
    if planning_frontier is not None:
        lines.append(
            "Planning frontier: "
            f"{planning_frontier.outcome} "
            f"(contract {planning_frontier.contract_id} "
            f"v{planning_frontier.contract_version})"
        )
        if planning_frontier.action:
            lines.append(
                f"  Frontier action ({planning_frontier.actor}): {planning_frontier.action}"
            )
        if planning_frontier.block_citation:
            lines.append(
                "  Frontier block: "
                f"{planning_frontier.block_citation} "
                f"(owner {planning_frontier.block_owner})"
            )
        if planning_frontier.completion_evidence:
            lines.append(
                f"  Frontier closure evidence: {planning_frontier.completion_evidence}"
            )
    return "\n".join(lines)
