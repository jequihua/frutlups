"""Single-iteration autonomous loop over the append-only project ledger."""

import json
import os
import subprocess
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from . import _loop_evidence as evidence
from . import gitws, ledger, receipt, render, verdict
from . import roadmap as roadmap_module
from ._paths import repo_path
from ._preflight import preflight as preflight  # noqa: PLC0414 - Retain the public loop API.
from ._preflight import prepare
from .seats import FailureClass, Job, Role, claude, pi


class StopReason(StrEnum):
    done = "done"
    boundary = "boundary"
    needs_specification = "needs_specification"
    blocked_verdict = "blocked_verdict"
    rounds_exhausted = "rounds_exhausted"
    path_violation = "path_violation"
    seat_transport = "seat_transport"
    seat_auth = "seat_auth"
    seat_capacity = "seat_capacity"
    seat_output = "seat_output"
    verification_error = "verification_error"
    budget_exhausted = "budget_exhausted"
    kill_switch = "kill_switch"
    preflight_failed = "preflight_failed"
    internal = "internal"


class _Stop(Exception):
    def __init__(self, reason, detail):
        self.reason, self.detail = reason, detail


class Loop:
    def __init__(self, root, cfg, roadmap, until=None, clock=time.monotonic, *, emit=None):
        self.root, self.cfg, self.roadmap = Path(root).resolve(), cfg, roadmap
        self.until, self.clock = until or cfg.until, clock
        if self.until not in ("slice", "milestone", "roadmap"):
            raise ValueError("until must be slice, milestone, or roadmap")
        self.emit = emit
        self.started, self.jobs = clock(), 0
        self.accepted_this_run = False
        self.target_milestone = None

    def notify(self, action, **data):
        if self.emit is not None:
            self.emit({"action": action, **data})
        elif action == "stop":
            print(f"{data['reason']}: {data['detail']}")

    def append(self, ev, **data):
        ledger.append(
            self.root / self.cfg.ledger,
            {
                "schema": ledger.SCHEMA,
                "t": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "ev": ev,
                "by": "frutlups",
                **data,
            },
            self.roadmap,
        )

        if ev not in ("verified", "accepted", "stop"):
            if ev in ("coded", "reviewed"):
                data["seats"] = []
                for name in data["seat"].split(","):
                    seat = self.cfg.seats[name]
                    effort = (
                        seat.corrective_effort
                        if ev == "coded" and data["round"] > 1 and seat.corrective_effort
                        else seat.effort
                    )
                    data["seats"].append(
                        {
                            "name": name,
                            "adapter": seat.adapter,
                            "model": seat.model,
                            "effort": effort,
                        }
                    )
            self.notify(ev, **data)

    def _guard(self, *, job=False):
        if (self.root / "STOP").exists():
            raise _Stop(StopReason.kill_switch, "STOP exists; owner must remove it to resume")
        if self.clock() - self.started >= self.cfg.max_wall_minutes * 60:
            raise _Stop(StopReason.budget_exhausted, "wall budget reached; inspect and resume")
        if job and self.jobs >= self.cfg.max_jobs:
            raise _Stop(StopReason.budget_exhausted, "job budget reached; inspect and resume")

    def _clean(self):
        events = ledger.read(self.root / self.cfg.ledger)
        errors = evidence.workspace_errors(self.root, self.cfg, events)
        if errors:
            raise _Stop(StopReason.path_violation, "; ".join(errors) + "; owner must inspect")

    def _artifact(self, scope, round_no, role, path, sha):
        self.append("artifact", scope=scope, round=round_no, role=role, path=path, sha=sha)

    def _job(self, name, role, path, corrective=False):
        seat = self.cfg.seats[name]
        module = pi if seat.adapter == "pi" else claude
        effort = seat.corrective_effort if corrective and seat.corrective_effort else seat.effort
        prompt = repo_path(self.root, path)
        return Job(
            uuid4().hex,
            role,
            name,
            seat.adapter,
            seat.provider,
            seat.model,
            effort,
            prompt,
            ledger.evidence_sha(prompt.read_bytes()),
            self.root,
            module.tools_for(role),
            self.cfg.timeouts.coder_seconds
            if role == Role.coder
            else self.cfg.timeouts.reviewer_seconds,
            None,
            self.roadmap.memory is not None,
        )

    def _call(self, name, role, path, corrective=False, retry=None):
        results = []
        retry = [1] if retry is None else retry
        while True:
            self._guard(job=True)
            self._clean()
            job = self._job(name, role, path, corrective)
            adapter = pi.PiSeat() if job.adapter == "pi" else claude.ClaudeSeat()
            before = evidence.changes(self.root, self.cfg)
            ledger_before = (self.root / self.cfg.ledger).read_bytes()
            self.jobs += 1
            result = adapter.run(job, self.cfg)
            directory = self.root / "local_state" / "frutlups" / "jobs" / job.id
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "result.json").write_text(
                json.dumps(
                    {**asdict(result), "role": role.value, "prompt_sha": job.prompt_sha},
                    default=str,
                ),
                encoding="utf-8",
            )
            results.append(result)
            if (self.root / self.cfg.ledger).read_bytes() != ledger_before:
                raise _Stop(
                    StopReason.path_violation, "seat changed the ledger; owner must inspect"
                )
            if role != Role.coder and evidence.changes(self.root, self.cfg) != before:
                raise _Stop(
                    StopReason.path_violation, "read-only seat changed files; owner must inspect"
                )
            if result.status == "completed":
                return result, results
            if result.failure_class in (FailureClass.auth, FailureClass.capacity):
                reason = (
                    StopReason.seat_auth
                    if result.failure_class == FailureClass.auth
                    else StopReason.seat_capacity
                )
                raise _Stop(reason, f"{name}: {result.diagnostic}; resolve seat access and resume")
            if role == Role.coder:
                after = evidence.changes(self.root, self.cfg)
                if after != before:
                    paths = sorted(
                        {
                            row["path"]
                            for row in (*before, *after)
                            if row not in before or row not in after
                        }
                    )
                    # The coding transition fences these edits before stopping.
                    return replace(
                        result,
                        diagnostic=(
                            f"{result.diagnostic}; files left by failed attempt: {', '.join(paths)}"
                        ),
                    ), results
            if not retry[0]:
                raise _Stop(
                    StopReason.seat_transport,
                    f"{name}: {result.diagnostic}; inspect job streams and resume",
                )
            retry[0] -= 1

    def _review(self, name, role, path, scope, round_no):
        results = []
        retry = [1]
        for attempt in range(2):
            result, attempts = self._call(name, role, path, retry=retry)
            results.extend(attempts)
            try:
                text = receipt.scrub_text(result.final_text or "", self.root, dict(os.environ))
                parsed = verdict.parse(text)
                if parsed.identity != scope or parsed.round != round_no:
                    raise verdict.VerdictError("review identity or round mismatch")
                if parsed.verdict == "override" or any(
                    f.disposition == "waived_by_human" for f in parsed.findings
                ):
                    raise verdict.VerdictError("autonomous seats cannot grant human waivers")
                if role == Role.holistic:
                    known = {
                        s.id for m in self.roadmap.milestones if m.id == scope for s in m.slices
                    }
                    if any(f[:8] not in known for f in verdict.open_blocking(parsed)):
                        raise verdict.VerdictError("holistic finding names an unknown slice")
                    if parsed.verdict != "pass" and not verdict.open_blocking(parsed):
                        raise verdict.VerdictError("non-pass holistic review needs open P0-P2")
                return parsed, text, results
            except verdict.VerdictError as exc:
                if attempt:
                    raise _Stop(
                        StopReason.seat_output, f"{exc}; inspect review and resume"
                    ) from exc
                text = repo_path(self.root, path).read_text(encoding="utf-8")
                text += f"\nFormat-only retry: {exc}\nCorrect the report format.\n"
                path, sha = evidence.write_text(
                    self.root,
                    self.cfg.review_prompt_dir,
                    f"{scope}_format.md",
                    text,
                    24576,
                )
                self._artifact(
                    scope,
                    round_no or "holistic",
                    "review_prompt" if round_no else "holistic_prompt",
                    path,
                    sha,
                )

    def _slice(self, item, current, events):
        scope = {"slice": item.id, "round": current.round}
        directory = f"{self.cfg.reviews_dir}/{item.milestone_id.lower()}"
        stem = f"{item.id}_r{current.round}"
        if current.step in ("unstarted", "fix"):
            self._guard(job=True)
            if (
                current.step == "fix"
                and current.corrective_rounds_used >= self.cfg.max_corrective_rounds
            ):
                raise _Stop(
                    StopReason.rounds_exhausted, f"{item.id}: owner must revise the round budget"
                )
            self._clean()
            baseline = evidence.changes(self.root, self.cfg)
            text = render.coding(
                self.root, self.roadmap, item, current, evidence.receipt_tail(self.root, current)
            )
            path, sha = evidence.write_text(
                self.root,
                self.cfg.prompt_dir,
                f"{stem}.md",
                text,
                8192,
            )
            self.append("prompt", **scope, path=path, sha=sha, baseline=list(baseline))
        elif current.step == "coding":
            result, attempts = self._call(
                "coder",
                Role.coder,
                current.last_prompt,
                current.round > 1,
            )
            changed = [
                row
                for row in evidence.changes(self.root, self.cfg)
                if row not in current.baseline
                and row["path"] != self.cfg.ledger
                and row["path"] != current.last_prompt
            ]
            violations = gitws.fence(
                changed,
                roadmap_module.effective_prefixes(self.roadmap, item),
                self.roadmap.forbidden,
            )
            destinations = {row["path"] for row in changed}
            origins = [
                {"path": row.original_path}
                for row in gitws.status(self.root, executable=self.cfg.git)
                if row.path in destinations and row.original_path
            ]
            violations += gitws.fence(
                origins,
                roadmap_module.effective_prefixes(self.roadmap, item),
                self.roadmap.forbidden,
            )
            drifts = [
                d
                for d in ledger.check(self.root / self.cfg.ledger, self.root)
                if d.path in evidence.evidence_paths(events)
            ]
            if violations or drifts:
                paths = [v.path for v in violations] + [d.path for d in drifts]
                raise _Stop(StopReason.path_violation, ", ".join(paths) + "; owner must inspect")
            if result.status != "completed":
                raise _Stop(
                    StopReason.seat_transport,
                    f"coder: {result.diagnostic}; "
                    "owner must inspect and record or restore the edits",
                )
            notes, _ = evidence.write_text(
                self.root,
                directory,
                f"{stem}_coder.md",
                receipt.scrub_text(
                    result.final_text or "(No coder notes.)\n",
                    self.root,
                    dict(os.environ),
                ),
            )
            self.append(
                "coded",
                **scope,
                changed=changed,
                notes_path=notes,
                seat="coder",
                **evidence.usage(attempts, coder=True),
            )
        elif current.step == "verifying":
            self._clean()
            try:
                result = receipt.run(self.root, item, current.round, self.roadmap, self.cfg, False)
                path = evidence.next_path(self.root, directory, f"{stem}_verification.json")
                sha = receipt.write(result, repo_path(self.root, path))
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                raise _Stop(StopReason.verification_error, f"{exc}; inspect verification") from exc
            self.append("verified", **scope, receipt=path, sha=sha, ok=result.ok)
            self.notify(
                "verified",
                **scope,
                receipt=path,
                ok=result.ok,
                secs=sum(command.secs for command in result.commands),
            )
        elif current.step == "reviewing":
            self._guard(job=True)
            self._clean()
            report_name = f"{stem}_review.md"
            report_path = evidence.next_path(self.root, directory, report_name)
            text = render.review(
                self.root,
                self.roadmap,
                item,
                current,
                evidence.review_changes(self.root, self.cfg, events, current),
                repo_path(self.root, current.notes_path).read_text(encoding="utf-8")
                if current.notes_path
                else "",
                repo_path(self.root, current.last_receipt).read_text(encoding="utf-8"),
                report_path,
            )
            path, sha = evidence.write_text(
                self.root, self.cfg.review_prompt_dir, report_name, text, 24576
            )
            self._artifact(item.id, current.round, "review_prompt", path, sha)
            reviews, results = [], []
            route = self.roadmap.review_routing[item.risk]
            for name in route:
                parsed, report, attempts = self._review(
                    name,
                    Role.reviewer,
                    path,
                    item.id,
                    current.round,
                )
                reviews.append((parsed, report))
                results.extend(attempts)
            report = evidence.combined_review(reviews)
            parsed = verdict.parse(report)
            path, sha = evidence.write_text(
                self.root,
                directory,
                report_name,
                report,
                path=report_path,
            )
            self.append(
                "reviewed",
                **scope,
                report=path,
                sha=sha,
                verdict=parsed.verdict,
                open=list(verdict.open_blocking(parsed)),
                seat=",".join(route),
                **evidence.usage(results),
            )
        elif current.step == "blocked":
            raise _Stop(
                StopReason.blocked_verdict, f"{item.id}: human or architect must record unblock"
            )
        elif current.step == "accept_pending":
            self._clean()
            relevant = [
                e
                for e in events
                if e.slice == item.id
                or e.ev == ledger.Ev.artifact
                and e.data["scope"] in (item.id, item.milestone_id)
            ]
            eligible = evidence.evidence_paths(relevant) | {self.cfg.ledger}
            eligible.update(row["path"] for e in relevant for row in e.data.get("changed", ()))
            paths = [
                row["path"]
                for row in evidence.changes(self.root, self.cfg)
                if row["path"] in eligible
            ]
            if self.cfg.commit_on_accept:
                unrelated = [
                    row.path
                    for row in gitws.status(self.root, executable=self.cfg.git)
                    if row.code[0] not in (" ", "?") and row.path not in paths
                ]
                if unrelated:
                    raise _Stop(
                        StopReason.path_violation, "unrelated staged paths: " + ", ".join(unrelated)
                    )
            self.append("accepted", **scope)
            commit = None
            if self.cfg.commit_on_accept:
                commit = gitws.commit(
                    self.root,
                    sorted(set(paths) | {self.cfg.ledger}),
                    f"Accept {item.id} round {current.round}",
                    executable=self.cfg.git,
                )
            self.notify("accepted", **scope, commit=commit)
            self.accepted_this_run = True

    def _holistic(self, milestone, state):
        self._guard(job=True)
        self._clean()
        receipts = {
            s.id: repo_path(self.root, state.slices[s.id].last_receipt).read_text(encoding="utf-8")
            for s in milestone.slices
        }
        reports = {s.id: state.slices[s.id].last_report for s in milestone.slices}
        directory = f"{self.cfg.reviews_dir}/{milestone.id.lower()}"
        report_name = f"{milestone.id}_holistic_review.md"
        report_path = evidence.next_path(self.root, directory, report_name)
        text = render.holistic(
            self.root,
            self.roadmap,
            milestone,
            receipts,
            reports,
            report_path=report_path,
            changed=evidence.holistic_changes(
                self.root,
                self.cfg,
                ledger.read(self.root / self.cfg.ledger),
                milestone,
            ),
        )
        path, sha = evidence.write_text(
            self.root, self.cfg.review_prompt_dir, f"{milestone.id}_holistic.md", text, 24576
        )
        self._artifact(milestone.id, "holistic", "holistic_prompt", path, sha)
        parsed, text, _ = self._review("holistic", Role.holistic, path, milestone.id, None)
        path, sha = evidence.write_text(
            self.root,
            directory,
            report_name,
            text,
            path=report_path,
        )
        self._artifact(milestone.id, "holistic", "holistic_report", path, sha)
        if parsed.verdict == "pass":
            self.append("milestone_done", milestone=milestone.id, holistic_report=path)
        else:
            for item in milestone.slices:
                ids = [f for f in verdict.open_blocking(parsed) if f.startswith(item.id + "-")]
                if ids:
                    self.append(
                        "reopened",
                        slice=item.id,
                        round=state.slices[item.id].round + 1,
                        reason="holistic findings " + ", ".join(ids),
                    )

    def _complete(self, milestone, state):
        return all(state.slices[s.id].step == "accepted" for s in milestone.slices) and (
            not milestone.holistic_review or milestone.id in state.milestones_done
        )

    def iteration(self):
        """Perform one transition (including its bounded retries); return a stop or None."""
        item = None
        try:
            events = ledger.read(self.root / self.cfg.ledger)
            state = ledger.fold(events, self.roadmap)
            self._guard()
            if self.until == "slice" and self.accepted_this_run:
                raise _Stop(StopReason.boundary, "one slice accepted")
            complete = {m.id for m in self.roadmap.milestones if self._complete(m, state)}
            if self.until == "milestone" and self.target_milestone in complete:
                raise _Stop(StopReason.boundary, "milestone completed")
            if len(complete) == len(self.roadmap.milestones):
                raise _Stop(StopReason.done, "all admitted roadmap milestones completed")
            remaining = tuple(m for m in self.roadmap.milestones if m.id not in complete)
            item = roadmap_module.next_slice(replace(self.roadmap, milestones=remaining), state)
            active = next((m for m in remaining if m.status == "active"), None)
            if self.target_milestone is None:
                self.target_milestone = item.milestone_id if item else active.id if active else None
            if item:
                self._slice(item, state.slices[item.id], events)
            elif active and all(state.slices[s.id].step == "accepted" for s in active.slices):
                self._holistic(active, state)
            else:
                raise _Stop(
                    StopReason.needs_specification, "architect must admit the next milestone"
                )
            return None
        except _Stop as exc:
            reason, detail = exc.reason, exc.detail
        except Exception as exc:  # noqa: BLE001 - Preserve the tree and record internal stops.
            scope = f"{item.id}: " if item else ""
            reason = StopReason.internal
            detail = f"{scope}{type(exc).__name__}: {exc}; owner must inspect"
        detail = receipt._scrub(detail.encode("utf-8"), self.root, dict(os.environ))
        try:
            self.append("stop", reason=reason.value, detail=detail)
        except (OSError, ValueError):
            # A seat may have damaged the ledger. Never repair or replace its bytes.
            pass
        self.notify("stop", reason=reason.value, detail=detail)
        return reason


def run(root, cfg=None, roadmap=None, until=None, clock=time.monotonic, *, once=False, emit=None):
    cfg, roadmap, errors = prepare(root, cfg, roadmap)
    if errors:
        for detail in errors:
            if emit is None:
                print(detail)
            else:
                emit({"action": "preflight", "ok": False, "detail": detail})
        return StopReason.preflight_failed
    runner = Loop(root, cfg, roadmap, until, clock, emit=emit)
    while True:
        result = runner.iteration()
        if result is not None:
            return result
        if once:
            return None
