"""Single-iteration autonomous loop over the append-only project ledger."""

import json
import os
import subprocess
import time
from contextlib import ExitStack
from dataclasses import asdict, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from . import _loop_evidence as evidence
from . import gitws, ledger, receipt, render, verdict, findings, _attempts, commit
from . import roadmap as roadmap_module
from ._paths import repo_path
from ._preflight import preflight as preflight  # noqa: PLC0414 - Retain the public loop API.
from ._preflight import prepare as prepare  # noqa: PLC0414 - Retain the public prepare triple.
from ._preflight import admission, _static, _owned_admission, _profile_gate, until_error, selection_error
from ._preflight import _owned_history, _select_historical, _admit_checked
from ._preflight import _errors, _probe_plan, _probed, _probes, canonical_errors, _admitted_item
from .ownership import writer_lock
from .seats import FINAL_TEXT_CAP, FailureClass, Job, Role, claude, pi


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
    def __init__(self, root, cfg, roadmap, until=None, clock=time.monotonic, *, emit=None,
                 selected_runtime=None, historical_assessment=None):
        """Construction has no effects; every iteration acquires owned admission."""
        if roadmap.schema != ledger.ROADMAP_V2:
            raise ValueError("unsupported roadmap schema; use native /2 or pinned v4")
        until = cfg.until if until is None else until
        for error in (until_error(until), selection_error(historical_assessment)):
            if error:
                raise ValueError(error)
        self.root = Path(root).resolve()
        self.cfg = replace(cfg, git=gitws.selected_executable(cfg.git))
        self.roadmap, self.until, self.clock, self.emit = roadmap, until, clock, emit
        self.historical_assessment = historical_assessment
        if selected_runtime is None:
            # The admitted milestone-first frontier, as in static and checked admission:
            # a later or ungranted milestone's frozen runtime cannot select or veto
            # a pending earlier closure, its recovery or a completed grant's stop.
            from . import runtime
            events = ledger.read(self.root / self.cfg.ledger)
            state = ledger.fold(events, roadmap)
            item = _admitted_item(roadmap, state)
            selected_runtime = (receipt.native_runtime(self.root, roadmap, render.task_contract(
                self.root, roadmap, item, state.slices[item.id], events, executable=self.cfg.git), self.cfg)
                if item else runtime.resolve_runtime(self.root, roadmap, git=self.cfg.git))
        self.runtime = selected_runtime
        self.started, self.jobs = clock(), 0
        self.accepted_this_run, self.target_milestone = False, None
        self._probe_pending = True

    def notify(self, action, **data):
        if self.emit is not None:
            self.emit({"action": action, **data})
        elif action == "stop":
            print(f"{data['reason']}: {data['detail']}")

    def append(self, ev, **data):
        raise ValueError("/2 ledger append is not enabled")

    def _historical_recheck(self):
        boundary = getattr(self, "_historical_boundary", None)
        if boundary is not None:
            if (self.root / "STOP").exists():
                raise _Stop(StopReason.kill_switch, "STOP exists; owner must remove it to resume")
            owned, context = boundary
            ledger._need(Path(self.root).resolve() == owned.root, "admission canonical root changed")
            owned.recheck(context, self.cfg, ledger.read(self.root / self.cfg.ledger))

    def _guard(self, *, job=False):
        self._historical_recheck()
        from . import commit
        commit.require_idle(self.root, ledger.read(self.root / self.cfg.ledger), self.roadmap, executable=self.cfg.git)
        if (self.root / "STOP").exists():
            raise _Stop(StopReason.kill_switch, "STOP exists; owner must remove it to resume")
        if self.roadmap.autonomy is not None:
            return
        if self.clock() - self.started >= self.cfg.max_wall_minutes * 60:
            raise _Stop(StopReason.budget_exhausted, "wall budget reached; inspect and resume")
        if job and self.jobs >= self.cfg.max_jobs:
            raise _Stop(StopReason.budget_exhausted, "job budget reached; inspect and resume")

    def _clean(self):
        events = ledger.read(self.root / self.cfg.ledger)
        from . import commit
        commit.require_idle(self.root, events, self.roadmap, executable=self.cfg.git)
        if self.roadmap.autonomy is not None:
            from ._preflight import _profile_workspace_errors
            ledger.validate_history(self.root, events, self.roadmap, executable=self.cfg.git)
            errors = _profile_workspace_errors(self.root, self.cfg, events)
        else:
            errors = evidence.workspace_errors(self.root, self.cfg, events)
        if errors:
            raise _Stop(StopReason.path_violation, "; ".join(errors) + "; owner must inspect")

    def _refuse_unrelated_staged(self, paths):
        unrelated = [
            row.path
            for row in gitws.status(self.root, executable=self.cfg.git)
            if row.code[0] not in (" ", "?") and row.path not in paths
        ]
        if unrelated:
            raise _Stop(
                StopReason.path_violation, "unrelated staged paths: " + ", ".join(unrelated)
            )

    def _job(self, name, role, path, corrective=False):
        seat = self.cfg.seats[name]
        module = pi if seat.adapter == "pi" else claude
        effort = seat.corrective_effort if corrective and seat.corrective_effort else seat.effort
        prompt = repo_path(self.root, path)
        limit = render.CODING_LIMIT if role == Role.coder else render.REVIEW_LIMIT
        with prompt.open("rb") as stream:
            raw = stream.read(limit + 1)
        ledger._need(len(raw) + len(render.dispatch_framing(seat.adapter).encode()) <= limit,
                     "prompt exceeds bounded job input")
        return Job(
            uuid4().hex,
            role,
            name,
            seat.adapter,
            seat.provider,
            seat.model,
            effort,
            prompt,
            ledger.evidence_sha(raw),
            self.root,
            module.tools_for(role),
            self.cfg.timeouts.coder_seconds
            if role == Role.coder
            else self.cfg.timeouts.reviewer_seconds,
            None,
            self.roadmap.memory is not None,
            self.cfg.pi_record_bytes,
        )

    def _seat_witness(self):
        try:
            return gitws.workspace_witness(self.root, executable=self.cfg.git)
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            raise _Stop(StopReason.path_violation,
                        f"seat workspace witness unavailable: {exc}; owner must inspect") from exc

    def _seat_drift(self, role, before, after, events, *, frozen_coding=False):
        if before["head"] != after["head"] or before["index"] != after["index"]:
            raise _Stop(StopReason.path_violation, "seat changed HEAD or index; owner must inspect")
        delta = sorted(p for p in before["files"].keys() | after["files"].keys()
                       if before["files"].get(p) != after["files"].get(p))
        if role != Role.coder:
            if delta:
                raise _Stop(StopReason.path_violation,
                            "read-only seat changed files: " + ", ".join(delta) + "; owner must inspect")
            return delta
        state = ledger.fold(events, self.roadmap)
        current = next((s for s in state.slices.values() if s.step == "coding"), None)
        if current is None:
            raise _Stop(StopReason.path_violation, "coder has no active coding scope")
        item = next(s for m in self.roadmap.milestones for s in m.slices if s.id == current.id)
        frozen = (ledger.envelope(self.root, ledger._thaw(current.envelope), current.id, current.round)
                  if frozen_coding and current.envelope else None)
        violations = gitws.fence(({"path": p} for p in delta),
                                frozen["allowed_prefixes"] if frozen else roadmap_module.effective_prefixes(self.roadmap, item),
                                frozen["forbidden"] if frozen else self.roadmap.forbidden)
        own = {row["path"]: row for e in events if e.slice == current.id and e.ev == ledger.Ev.coded
               for row in e.data["changed"]}
        protected = {row["path"] for row in current.baseline if own.get(row["path"]) != row}
        forbidden = {v.path for v in violations} | (set(delta) & protected)
        if frozen_coding:
            forbidden.update(set(delta) & evidence.evidence_paths(events, self.root))
        forbidden.update(p for p in delta if p in after["files"]
                         and after["files"][p][0] not in ("file", "missing"))
        if forbidden:
            raise _Stop(StopReason.path_violation,
                        "seat changed forbidden or prior-baseline paths: " +
                        ", ".join(sorted(forbidden)) + "; owner must inspect")
        return delta

    def _require_holistic_visibility(self, relative):
        repo_path(self.root, relative)
        # check-ignore rejects literal pathspec magic; keep the override local.
        result = gitws._git(self.root, "--no-literal-pathspecs", "check-ignore", "--no-index", "-q", "--",
                            relative, executable=self.cfg.git, check=False)
        ledger._need(result.returncode in (0, 1),
                     f"holistic visibility query failed (exit {result.returncode})")
        ledger._need(result.returncode == 1, "holistic evidence must be Git-visible: ignored destination")

    def _presentation_events(self, events, scope, round_no, paths):
        """Provisional exact paths only; callers prove bytes/binding before adoption."""
        recorded = evidence.evidence_paths(events, self.root)
        pending = []
        for relative in paths:
            self._require_holistic_visibility(relative)
            target = repo_path(self.root, relative)
            if relative not in recorded and (target.exists() or target.is_symlink()):
                # Read at the support cap, never an unbounded neighboring body.
                ledger._regular_reference(self.root, dict(path=relative, sha="0" * 64))
                with target.open("rb") as stream:
                    raw = stream.read(render.SUPPORT_LIMIT + 1)
                ledger._need(len(raw) <= render.SUPPORT_LIMIT, "presentation collision exceeds bound")
                pending.append(ledger._parse_event(_attempts.event("artifact", scope=scope,
                    round=round_no, role="evidence", path=relative, sha=ledger.evidence_sha(raw))))
        return (*events, *pending)

    def _retain_presentation(self, events, scope, round_no, artifacts):
        """Validate the whole package before any immutable write or ledger adoption."""
        self._historical_recheck()
        for role, ref, body, limit in artifacts:
            self._require_holistic_visibility(ref["path"])
            target = repo_path(self.root, ref["path"])
            if target.exists() or target.is_symlink():
                ledger._need(evidence.bound_text(self.root, ref, limit) == body,
                             "presentation artifact collision")
        candidate = self._presentation_events(events, scope, round_no,
                                              [ref["path"] for _, ref, _, _ in artifacts])
        errors = evidence.workspace_errors(self.root, self.cfg, candidate)
        ledger._need(not errors, "; ".join(errors))
        for role, ref, body, limit in artifacts:
            evidence.retain_review_text(self.root, ref["path"], body, limit)
            if not any(e.ev == ledger.Ev.artifact and e.data.get("path") == ref["path"] for e in events):
                self._review_append("artifact", scope=scope, round=round_no, role=role, **ref)

    def _call(self, name, role, path, corrective=False, retry=None, *,
              _chain=None, _original_path=None, _format_text=None, _scope=None, _round_no=None,
              _typed=False, _durable=False):
        self._probe_before_dispatch()
        results = []
        retry = [1] if retry is None else retry
        chain = {} if _chain is None else _chain
        original_path = _original_path or path
        if _durable and _format_text is not None:
            _format_text += render.HASH_EXPLANATION
            render.require_prompt(_format_text, render.REVIEW_LIMIT, self.cfg.seats[name].adapter)
        while True:
            self._guard(job=True)
            self._clean()
            job = self._job(name, role, path, corrective)
            if _durable and role == Role.holistic:
                for suffix in ("source.md", "result.json"):
                    relative = f"{self.cfg.reviews_dir}/{_scope.lower()}/{job.id}_{suffix}"
                    self._require_holistic_visibility(relative)
            if _typed or _durable:
                text = evidence.bound_text(self.root, dict(path=path, sha=job.prompt_sha),
                                           render.CODING_LIMIT if _typed else render.REVIEW_LIMIT)
                if _typed:
                    text += render.outcome_contract(_scope, _round_no, job.id)
                render.require_prompt(_format_text if _format_text is not None else text,
                                      render.CODING_LIMIT if _typed else render.REVIEW_LIMIT, job.adapter)
            if _typed:
                dispatched = f"local_state/frutlups/jobs/{job.id}/dispatch.md"
                ref = evidence.immutable_text(self.root, dispatched, text, 16 * 1024)
                job = replace(job, prompt_path=repo_path(self.root, dispatched), prompt_sha=ref["sha"])
            adapter = pi.PiSeat() if job.adapter == "pi" else claude.ClaudeSeat()
            with _attempts.Operation(
                self.root, self.cfg, self.roadmap, role.value, job.timeout,
                scope=_scope, round_no=_round_no, job=job,
                prompt=repo_path(self.root, original_path).read_bytes(),
                retry=chain.get("retry", "initial"), parent=chain.get("parent"),
                review=_durable, runtime_identity=dict(self.runtime.identity) if _typed or _durable else None,
            ) as operation:
                job = replace(job, timeout=operation.timeout)
                if _format_text is not None:
                    correction = self.root / "local_state/frutlups/jobs" / job.id / "format.md"
                    correction.parent.mkdir(parents=True, exist_ok=True)
                    if _durable:
                        evidence.immutable_text(self.root, correction.relative_to(self.root).as_posix(),
                                                _format_text, 48 * 1024)
                    else:
                        correction.write_text(_format_text, encoding="utf-8", newline="\n")
                    job = replace(job, prompt_path=correction,
                                  prompt_sha=ledger.evidence_sha(correction.read_bytes()))
                before = self._seat_witness()
                events_before = ledger.read(self.root / self.cfg.ledger)
                ledger_before = (self.root / self.cfg.ledger).read_bytes()
                self.jobs += 1
                try:
                    result = adapter.run(job, self.cfg)
                finally:
                    after = self._seat_witness()
                    try:
                        ledger_unchanged = (gitws._file_identity(self.root, self.cfg.ledger)[0] == "file"
                                            and (self.root / self.cfg.ledger).read_bytes() == ledger_before)
                    except OSError:
                        ledger_unchanged = False
                    if not ledger_unchanged:
                        raise _Stop(StopReason.path_violation,
                                    "seat changed the ledger; owner must inspect")
                    if _durable:
                        evidence.bound_text(self.root, dict(path=job.prompt_path.relative_to(self.root).as_posix(),
                                                            sha=job.prompt_sha), 48 * 1024)
                    delta = self._seat_drift(role, before, after, events_before, frozen_coding=_typed)
                    self._historical_recheck()
                result = replace(
                    result,
                    final_text=result.final_text if _typed or _durable else receipt.scrub_text(result.final_text, self.root, dict(os.environ))
                               if result.final_text is not None else None,
                    diagnostic=result.diagnostic if _durable else receipt.scrub_text(result.diagnostic, self.root, dict(os.environ))
                               if result.diagnostic is not None else None,
                )
                if not _typed and result.final_text is not None and len(result.final_text.encode("utf-8")) > FINAL_TEXT_CAP:
                    raise _Stop(StopReason.seat_output, "scrubbed seat report exceeds 1 MiB; inspect retained streams")
                directory = self.root / "local_state" / "frutlups" / "jobs" / job.id
                directory.mkdir(parents=True, exist_ok=True)
                result_path = directory / "result.json"
                if _durable and role == Role.holistic:
                    result_path = repo_path(self.root,
                        f"{self.cfg.reviews_dir}/{_scope.lower()}/{job.id}_result.json")
                saved = json.dumps({**asdict(result), "role": role.value, "prompt_sha": job.prompt_sha,
                                    **({"invocation": job.id} if _typed else {})}, default=str)
                refs = None
                if _durable:
                    original = result.final_text or ""
                    ledger._need(receipt.scrub_text(original, self.root, dict(os.environ)) == original,
                                 "review output contains private text; inspect retained streams")
                    output = evidence.immutable_text(self.root,
                        f"{self.cfg.reviews_dir}/{_scope.split('-')[0].lower()}/{job.id}_source.md",
                        original, FINAL_TEXT_CAP)
                    saved = json.dumps(dict(invocation=job.id, role=role.value, seat=name,
                        contract=operation.contract, runtime=dict(self.runtime.identity),
                        prompt=dict(path=original_path, sha=ledger.evidence_sha(repo_path(self.root, original_path).read_bytes())),
                        output=output, status=result.status, failure_class=result.failure_class,
                        diagnostic=result.diagnostic, result={k:v for k,v in asdict(result).items()
                            if k not in ("final_text", "events_path", "stdout_path", "stderr_path")}), ensure_ascii=False)
                    result_ref = evidence.immutable_text(self.root, result_path.relative_to(self.root).as_posix(),
                                                         saved, 2 * 1024 * 1024)
                    refs = [result_ref, output]
                elif _typed:
                    evidence.immutable_text(self.root, result_path.relative_to(self.root).as_posix(),
                                            saved, 2 * 1024 * 1024)
                else:
                    result_path.write_text(saved, encoding="utf-8")
                operation.finish(result.status == "completed", result, evidence=refs)
                chain["parent"] = operation.invocation
                if _typed:
                    chain["invocation"] = operation.invocation
                    chain["witness"] = after
            results.append(result)
            if _durable:
                return result, results, result_path.relative_to(self.root).as_posix()
            if result.status == "completed":
                return result, results, result_path.relative_to(self.root).as_posix()
            if result.failure_class in (FailureClass.auth, FailureClass.capacity):
                reason = (
                    StopReason.seat_auth
                    if result.failure_class == FailureClass.auth
                    else StopReason.seat_capacity
                )
                raise _Stop(reason, f"{name}: {result.diagnostic}; resolve seat access and resume")
            if role == Role.coder and delta:
                # Preserve every failed-attempt edit; no writable retry can hide it.
                return replace(
                    result, diagnostic=(f"{result.diagnostic}; files left by failed attempt: "
                                        + ", ".join(delta)),
                ), results, result_path.relative_to(self.root).as_posix()
            if result.failure_class == FailureClass.output:
                raise _Stop(
                    StopReason.seat_output,
                    f"{name}: {result.diagnostic}; inspect job streams before resuming",
                )
            if not retry[0]:
                raise _Stop(
                    StopReason.seat_transport,
                    f"{name}: {result.diagnostic}; inspect job streams and resume",
                )
            retry[0] -= 1
            chain["retry"] = "transport"

    def _review(self, name, role, path, scope, round_no, *, _durable=False):
        return self._checkpoint_review(name, path, scope, round_no, role=role)

    def _slice(self, item, current, events):
        with writer_lock(self.root, self.roadmap, executable=self.cfg.git):
            self._reconcile_findings()
        if current.step == "verifying":
            with writer_lock(self.root, self.roadmap, executable=self.cfg.git):
                try:
                    return self._verifying_slice(item, current, events)
                except (ValueError, OSError, subprocess.SubprocessError) as exc:
                    raise _Stop(StopReason.verification_error, f"{exc}; inspect verification") from exc
        if current.step == "reviewing":
            with writer_lock(self.root, self.roadmap, executable=self.cfg.git):
                return self._reviewing_slice(item, current, events)
        if current.step in ("unstarted", "fix", "coding", "blocked"):
            with writer_lock(self.root, self.roadmap, executable=self.cfg.git):
                return self._coding_slice(item, current, events)
        scope = {"slice": item.id, "round": current.round}
        if current.step in ("accept_pending", "accepted"):
            # A fresh controller can resume here without running a slice seat.
            frozen = ledger.envelope(self.root, ledger._thaw(current.envelope), item.id, current.round)
            self._slice_runtime_declaration = dict(frozen["runtime"])
            witness = self._approval("accepted", [], f"Accept {item.id} round {current.round}", **scope)
            self.notify("accepted", **scope, commit=witness)
            self.accepted_this_run = True
            return
        raise ValueError("unsupported native slice state")

    def _verifying_slice(self, item, current, events):
        ledger._need(self.roadmap.autonomy is not None, "native verification requires a granted profile")
        path = self.root / self.cfg.ledger
        prefix = path.read_bytes()
        state = ledger.validate_history(self.root, events, self.roadmap, executable=self.cfg.git)
        ledger._need(state.slices[item.id] == current and ledger.read(path) == events, "stale verification state")
        self._guard()
        self._clean()
        frozen = ledger.envelope(self.root, ledger._thaw(current.envelope), item.id, current.round)
        selected = receipt.native_runtime(self.root, self.roadmap, frozen, self.cfg)
        contract = _attempts._binding(self.root, self.cfg, self.roadmap, events, state,
            item.id, current.round, "verification", None, b"", dict(selected.identity))
        prior = [a for a in state.attempts.values() if a["start"]["role"] == "verification"
                 and a["start"]["scope"] == item.id and a["start"]["round"] == current.round]
        if prior:
            ledger._need(len(prior) == 1 and prior[0]["start"]["contract"] == contract,
                         "verification contract drift or duplicate attempt")
            attempt = ledger._thaw(prior[0])
            finish = attempt["finish"]
            ledger._need(finish is not None and finish["status"] != "resolved"
                         and len(finish.get("evidence", [])) == 1, "verification lacks durable completion proof")
            ref = finish["evidence"][0]
            value = ledger.read_reference(self.root, ref, 128 * 1024)
            receipt.validate_native(self.root, value, current, frozen)
            ledger._need(value["runtime"] == dict(selected.identity), "verification runtime drift")
            present = receipt.native_snapshot(self.root, self.cfg)
            ledger._need(value["witness"]["head"] == present["head"]
                         and value["witness"]["index"] == present["index"]
                         and value["witness"]["product"] == receipt.native_product(self.root, self.cfg, present, events),
                         "verification HEAD/index/product drift")
        else:
            operation = _attempts.Operation(self.root, self.cfg, self.roadmap, "verification",
                    self.cfg.timeouts.verification_seconds, scope=item.id, round_no=current.round,
                    runtime_identity=dict(selected.identity))
            relative = f"{self.cfg.reviews_dir}/{item.milestone_id.lower()}/{operation.invocation}_verification.json"
            self._require_holistic_visibility(relative)
            with operation:
                reserved = path.read_bytes()
                result = receipt.run(self.root, item, current.round, self.roadmap, self.cfg, False,
                    selected_runtime=selected, timeout=max(0, operation.timeout - (operation.clock() - operation.started)))
                ledger._need(gitws._file_identity(self.root, self.cfg.ledger)[0] == "file"
                             and path.read_bytes() == reserved, "verification changed the ledger; owner must inspect")
                self._historical_recheck()
                value = json.loads(json.dumps(asdict(result)))
                receipt.validate_native(self.root, value, current, frozen)
                ref = dict(path=relative, sha=receipt.write(result, repo_path(self.root, relative)))
                ledger._need(not result.ok or operation.clock() - operation.started <= operation.timeout,
                             "verification completion exceeded reserved deadline")
                operation.finish(result.ok, evidence=[ref])
            prefix = path.read_bytes()
        ledger._need(path.read_bytes() == prefix, "verification ledger prefix changed")
        ledger._append_operation(self.root, path, self.roadmap,
            _attempts.event("verified", slice=item.id, round=current.round,
                            receipt=ref["path"], sha=ref["sha"], ok=value["ok"]), executable=self.cfg.git)
        self.notify("verified", slice=item.id, round=current.round, receipt=ref["path"], ok=value["ok"],
                    secs=value["commands"][0]["secs"])

    def _review_append(self, ev, **values):
        self._historical_recheck()
        path = self.root / self.cfg.ledger
        ledger._append_review(self.root, path, self.roadmap, _attempts.event(ev, **values),
                              path.read_bytes(), executable=self.cfg.git)

    def _checkpoint_review(self, name, path, scope, round_no, *, role=Role.reviewer):
        """Resume a single durable logical chain; local retry counters carry no authority."""
        while True:
            self._guard()
            self._clean()
            events = ledger.read(self.root / self.cfg.ledger)
            state = ledger.validate_history(self.root, events, self.roadmap, executable=self.cfg.git)
            job = self._job(name, role, path)
            contract = _attempts._binding(self.root, self.cfg, self.roadmap, events, state,
                scope, round_no, role.value, job, repo_path(self.root, path).read_bytes(), dict(self.runtime.identity))
            chain = [ledger._thaw(a) for a in state.attempts.values() if a["start"]["contract"] == contract
                     and a["start"]["role"] == role.value and a["start"]["scope"] == scope
                     and a["start"]["round"] == round_no]
            retry, parent, format_text = "initial", None, None
            if chain:
                ledger._need(sum(a["start"]["retry"] == "initial" for a in chain) == 1,
                             "ambiguous review invocation chain")
                for previous, following in zip(chain, chain[1:]):
                    ledger._need(following["start"].get("parent") == previous["start"]["invocation"],
                                 "forked review invocation chain")
                last = chain[-1]
                parent = last["start"]["invocation"]
                finish = last["finish"]
                ledger._need(finish is not None and finish["status"] != "resolved",
                             "unfinished or externally resolved review requires attribution; no inferred completion")
                refs = finish.get("evidence", [])
                ledger._need(len(refs) == 2, "review completion lacks bound result/output")
                result = ledger.read_reference(self.root, refs[0])
                text = evidence.bound_text(self.root, refs[1], FINAL_TEXT_CAP)
                ledger._need(result.get("invocation") == parent and result.get("contract") == contract
                             and result.get("runtime") == dict(self.runtime.identity)
                             and result.get("seat") == name and result.get("output") == refs[1]
                             and result.get("prompt") == dict(path=path, sha=job.prompt_sha),
                             "retained review completion binding mismatch")
                if finish["status"] == "completed":
                    ledger._need(result.get("status") == "completed", "inconsistent review completion")
                    try:
                        parsed = (verdict.autonomous_holistic(text,
                            next(m for m in self.roadmap.milestones if m.id == scope))
                            if role == Role.holistic else verdict.autonomous_slice(text, scope, round_no))
                    except verdict.VerdictError as exc:
                        ledger._need(not any(a["start"]["retry"] == "format" for a in chain),
                                     "review format retry allowance exhausted")
                        retry = "format"
                        format_text = ("Format repair only. Read the retained malformed output at "
                            + render.reference_text(refs[1]) + " and original contract at "
                            + render.reference_text(result["prompt"]) + ".\n"
                            "Correct only the report grammar and return the report. Preserve all findings and meaning. "
                            "Do not repeat the review, inspect product, run commands or change files.\nError: " + str(exc))
                    else:
                        checkpoint = state.checkpoints.get(parent)
                        expected = dict(invocation=parent, scope=scope, round=round_no, contract=contract,
                                        report=refs[1]["path"], sha=refs[1]["sha"], seat=name)
                        if checkpoint:
                            ledger._need(all(checkpoint.get(k) == v for k, v in expected.items()),
                                         "retained checkpoint mismatch")
                        else:
                            self._review_append("review_checkpoint", **expected)
                        return parsed, text, parent
                else:
                    ledger._need(result.get("status") != "completed", "inconsistent failed review")
                    failure = result.get("failure_class")
                    ledger._need(failure in ("transport", "timeout"), "review failure needs external inspection")
                    ledger._need(not any(a["start"]["retry"] == "transport" for a in chain),
                                 "review transport retry allowance exhausted")
                    retry = "transport"
                    # A transport retry after format remains a repair, never the original review.
                    if any(a["start"]["retry"] == "format" for a in chain):
                        malformed = next(a for a in chain if a["finish"]["status"] == "completed")
                        original = ledger.read_reference(self.root, malformed["finish"]["evidence"][0])
                        format_text = ("Format repair only. Correct grammar without repeating review, commands or writes. "
                            "Preserve all findings. Read malformed output " + render.reference_text(original["output"])
                            + " and original contract " + render.reference_text(original["prompt"]) + ". Return report only.")
            self._call(name, role, path, _chain=dict(retry=retry, parent=parent),
                       _original_path=path, _format_text=format_text, _scope=scope,
                       _round_no=round_no, _durable=True)

    def _merge_artifacts(self, item, current, reviews, sources):
        names = self.roadmap.review_routing[item.risk]
        merged = evidence.combined_review(reviews, seat_names=names, native=True,
                                          prior_ids=evidence.prior_finding_ids(self.root, current, native=True))
        directory = (f"{self.cfg.reviews_dir}/{item.milestone_id.lower()}/"
                     f"{item.id}_r{current.round}_{_attempts._digest(sources)}")
        report = dict(path=f"{directory}/review.md", sha=ledger.evidence_sha(merged.encode("utf-8")))
        proof = json.dumps(dict(report=report, sources=sources), sort_keys=True)
        return ((report, merged, FINAL_TEXT_CAP),
                (dict(path=f"{directory}/merge.json", sha=ledger.evidence_sha(proof.encode("utf-8"))),
                 proof, 2 * 1024 * 1024))

    def _recover_merge_artifacts(self, item, current, events, state):
        """Adopt only exact interrupted merge bytes backed by current completed bindings."""
        checkpoints = []
        for name in self.roadmap.review_routing[item.risk]:
            matches = [c for c in state.checkpoints.values() if c["scope"] == item.id
                       and c["round"] == current.round and c["seat"] == name]
            if not matches:
                return events
            checkpoints.append(matches[-1])
        reviews, sources = [], []
        for checkpoint in checkpoints:
            body = evidence.bound_text(self.root,
                dict(path=checkpoint["report"], sha=checkpoint["sha"]), FINAL_TEXT_CAP)
            reviews.append((verdict.autonomous_slice(body, item.id, current.round), body))
            sources.append(checkpoint["invocation"])
        pending = []
        for ref, body, limit in self._merge_artifacts(item, current, reviews, sources):
            target = repo_path(self.root, ref["path"])
            if not target.exists() and not target.is_symlink():
                continue
            ledger._need(evidence.bound_text(self.root, ref, limit) == body, "merge artifact collision")
            if not any(e.ev == ledger.Ev.artifact and e.data.get("path") == ref["path"] for e in events):
                pending.append(_attempts.event("artifact", scope=item.id, round=current.round,
                                               role="evidence", **ref))
        if not pending:
            return events
        # These provisional exclusions are never persisted before every source's
        # original binding has been revalidated. No directory receives an exemption.
        candidate = (*events, *(ledger._parse_event(row) for row in pending))
        candidate_state = ledger.validate_history(self.root, candidate, self.roadmap, executable=self.cfg.git)
        frozen = ledger.envelope(self.root, ledger._thaw(current.envelope), item.id, current.round)
        selected = receipt.native_runtime(self.root, self.roadmap, frozen, self.cfg)
        if self.runtime:
            ledger._need(dict(self.runtime.identity) == dict(selected.identity), "selected review runtime drift")
        for checkpoint in checkpoints:
            invocation = checkpoint["invocation"]
            attempt = ledger._thaw(state.attempts[invocation])
            finish = attempt["finish"]
            ledger._need(finish is not None and finish["status"] == "completed",
                         "merge recovery requires completed review")
            refs = finish.get("evidence", [])
            ledger._need(len(refs) == 2, "merge recovery lacks result/output")
            result = ledger.read_reference(self.root, refs[0])
            prompt = result["prompt"]
            text = evidence.bound_text(self.root, prompt, 48 * 1024)
            job = self._job(checkpoint["seat"], Role.reviewer, prompt["path"])
            binding = _attempts._binding(self.root, self.cfg, self.roadmap, candidate, candidate_state,
                item.id, current.round, "reviewer", job, text.encode("utf-8"), dict(selected.identity))
            ledger._need(checkpoint["contract"] == attempt["start"]["contract"] == binding
                         and result.get("contract") == binding and result.get("invocation") == invocation
                         and result.get("runtime") == dict(selected.identity)
                         and result.get("status") == "completed" and result.get("seat") == checkpoint["seat"]
                         and result.get("output") == refs[1]
                         and refs[1] == dict(path=checkpoint["report"], sha=checkpoint["sha"]),
                         "merge recovery binding mismatch")
        for row in pending:
            self._review_append("artifact", scope=item.id, round=current.round, role="evidence",
                                path=row["path"], sha=row["sha"])
        return ledger.read(self.root / self.cfg.ledger)

    def _reviewing_slice(self, item, current, events):
        path = self.root / self.cfg.ledger
        fresh = ledger.read(path)
        state = ledger.validate_history(self.root, fresh, self.roadmap, executable=self.cfg.git)
        ledger._need(tuple(events) == fresh and state.slices[item.id] == current, "stale review state")
        self._guard()
        events = self._recover_merge_artifacts(item, current, fresh, state)
        frozen = ledger.envelope(self.root, ledger._thaw(current.envelope), item.id, current.round)
        manifest_ref = ledger._thaw(current.manifest)
        manifest = ledger.manifest(self.root, manifest_ref)
        for row in manifest["changed"]:
            target = repo_path(self.root, row["path"])
            if row["kind"] == "deleted":
                ledger._need(not target.exists() and not target.is_symlink(), "active product drift")
            else:
                ledger._check_reference(self.root, dict(path=row["path"], sha=row["sha"]))
        verified = next(e for e in reversed(events) if e.ev == ledger.Ev.verified
                        and e.slice == item.id and e.round == current.round)
        receipt_ref = dict(path=verified.data["receipt"], sha=verified.data["sha"])
        value = ledger.read_reference(self.root, receipt_ref, 128 * 1024)
        frozen = ledger.envelope(self.root, ledger._thaw(current.envelope), item.id, current.round)
        selected = receipt.native_runtime(self.root, self.roadmap, frozen, self.cfg)
        if self.runtime:
            ledger._need(dict(self.runtime.identity) == dict(selected.identity), "selected review runtime drift")
        self.runtime = selected
        self._slice_runtime_declaration = dict(frozen["runtime"])
        names = self.roadmap.review_routing[item.risk]
        stem = f"{item.id}_r{current.round}"
        directory = f"{self.cfg.reviews_dir}/{item.milestone_id.lower()}/{stem}"
        index, pages = evidence.evidence_package(self.root, self.cfg, directory,
                                               [manifest_ref], value["base_commit"])
        support_path = directory + "_support.json"
        prompt_path = f"{self.cfg.review_prompt_dir}/{stem}_review.md"
        candidate = self._presentation_events(events, item.id, current.round,
            [ref["path"] for ref, _, _ in pages] + [support_path, prompt_path])
        evidence.review_receipt(self.root, self.cfg, candidate, current, frozen, value)
        product = _attempts.review_product(self.root, self.cfg, candidate)
        for event in events:
            if (event.ev == ledger.Ev.artifact and event.data.get("scope") == item.id
                    and event.round == current.round and event.data["path"].endswith("_support.json")):
                prior = ledger.read_reference(self.root, dict(path=event.data["path"], sha=event.data["sha"]), render.SUPPORT_LIMIT)
                ledger._need(prior.get("product") == product, "review candidate product/HEAD/index drift")
        support = dict(envelope=ledger._thaw(current.envelope), manifest=manifest_ref, receipt=receipt_ref,
                       runtime=dict(selected.identity), route=list(names), notes=None, product=product, index=index)
        if current.notes_path:
            support["notes"] = dict(path=current.notes_path,
                sha=ledger._file_sha(repo_path(self.root, current.notes_path)))
        support_ref, support_text, support_limit = evidence.text_artifact(support_path,
            json.dumps(support, sort_keys=True), render.SUPPORT_LIMIT)
        text = render.review_v2(self.root, frozen, support, f"{stem}_review.md", support_ref)
        for name in names:
            render.require_prompt(text, render.REVIEW_LIMIT, self.cfg.seats[name].adapter)
        prompt_ref, text, prompt_limit = evidence.text_artifact(prompt_path, text, render.REVIEW_LIMIT)
        artifacts = [("evidence", *page) for page in pages]
        artifacts += [("evidence", support_ref, support_text, support_limit),
                      ("review_prompt", prompt_ref, text, prompt_limit)]
        self._retain_presentation(events, item.id, current.round, artifacts)
        self._clean()
        reviews, sources = [], []
        for name in names:
            parsed, body, invocation = self._review(name, Role.reviewer, prompt_path, item.id, current.round,
                                                   _durable=True)
            reviews.append((parsed, body))
            sources.append(invocation)
        self._clean()
        latest = ledger.read(path)
        state = ledger.validate_history(self.root, latest, self.roadmap, executable=self.cfg.git)
        ledger._need(_attempts.review_product(self.root, self.cfg, latest) == product,
                     "review candidate changed before merge")
        for name, invocation in zip(names, sources):
            job = self._job(name, Role.reviewer, prompt_path)
            binding = _attempts._binding(self.root, self.cfg, self.roadmap, latest, state, item.id,
                current.round, "reviewer", job, text.encode("utf-8"), dict(selected.identity))
            ledger._need(state.checkpoints[invocation]["contract"] == binding,
                         "review binding changed before merge")
        artifacts = self._merge_artifacts(item, current, reviews, sources)
        report, merged, _ = artifacts[0]
        parsed = verdict.autonomous_slice(merged, item.id, current.round)
        for ref, body, limit in artifacts:
            evidence.retain_review_text(self.root, ref["path"], body, limit)
            fresh = ledger.read(path)
            if not any(e.ev == ledger.Ev.artifact and e.data.get("path") == ref["path"] for e in fresh):
                self._review_append("artifact", scope=item.id, round=current.round, role="evidence", **ref)
        self._review_append("reviewed", slice=item.id, round=current.round, report=report["path"],
                            sha=report["sha"], verdict=parsed.verdict, open=list(verdict.open_blocking(parsed)),
                            seat=",".join(names))

    def _coding_slice(self, item, current, events):
        path = self.root / self.cfg.ledger
        fresh = ledger.read(path)
        state = ledger.validate_history(self.root, fresh, self.roadmap, executable=self.cfg.git)
        ledger._need(tuple(events) == fresh and state.slices[item.id] == current, "stale coding state")
        if current.step == "blocked":
            raise _Stop(StopReason.blocked_verdict, f"{item.id}: human/architect resolved authority required")
        self._guard(job=True)
        self._clean()
        if current.step == "fix":
            if evidence.no_progress(self.root, events, self.roadmap, item.id, executable=self.cfg.git):
                raise _Stop(StopReason.verification_error,
                            f"{item.id}: identical product and verification failure after correction; owner must inspect")
            if current.corrective_rounds_used >= self.cfg.max_corrective_rounds:
                raise _Stop(StopReason.rounds_exhausted, f"{item.id}: corrective round budget exhausted")
        directory = f"{self.cfg.reviews_dir}/{item.milestone_id.lower()}"
        stem = f"{item.id}_r{current.round}"
        prefix = path.read_bytes()
        scope = dict(slice=item.id, round=current.round)
        if current.step in ("unstarted", "fix"):
            frozen = render.coding_envelope(self.root, self.roadmap, item, current, events, executable=self.cfg.git)
            if current.last_receipt:
                frozen["findings"] += "\nLast verification receipt: " + json.dumps(dict(
                    path=current.last_receipt, sha=ledger.evidence_sha(repo_path(self.root, current.last_receipt).read_bytes())))
            envelope, envelope_text, _ = evidence.text_artifact(f"{directory}/{stem}_envelope.json",
                json.dumps(frozen, ensure_ascii=False), render.SUPPORT_LIMIT)
            text = render.coding_v2(self.root, frozen, current, envelope)
            render.require_prompt(text + render.outcome_contract(item.id, current.round, "0" * 32),
                                  render.CODING_LIMIT, self.cfg.seats["coder"].adapter)
            for relative in (envelope["path"], f"{self.cfg.prompt_dir}/{stem}.md"):
                self._require_holistic_visibility(relative)
            baseline = list(evidence.changes(self.root, self.cfg))
            envelope = evidence.immutable_text(self.root, f"{directory}/{stem}_envelope.json",
                                               envelope_text, 128 * 1024)
            prompt = evidence.immutable_text(self.root, f"{self.cfg.prompt_dir}/{stem}.md", text, 16 * 1024)
            ledger._append_coding(self.root, path, self.roadmap,
                _attempts.event("prompt", **scope, **prompt, envelope=envelope, baseline=baseline),
                prefix, executable=self.cfg.git)
            return
        frozen = ledger.envelope(self.root, ledger._thaw(current.envelope), item.id, current.round)
        selected = receipt.native_runtime(self.root, self.roadmap, frozen, self.cfg)
        self.runtime = selected
        self._slice_runtime_declaration = dict(frozen["runtime"])
        runtime_policy = dict(selected.identity)
        marker = f"local_state/frutlups/coding/{stem}.json"
        seat_policy = asdict(self.cfg.seats["coder"])
        started_path = f"{directory}/{stem}_started.json"
        ledger._need(not any(e.ev == ledger.Ev.artifact and e.data.get("path") == started_path for e in events),
                     "coding dispatch already started; retained uncertainty requires external disposition")
        issued_index = next(i for i in range(len(events) - 1, -1, -1)
                            if events[i].ev == ledger.Ev.prompt and events[i].slice == item.id
                            and events[i].round == current.round)
        prior_coded = next((e for e in reversed(events[:issued_index])
                            if e.ev == ledger.Ev.coded and e.slice == item.id), None)
        if (render.resumes_contract(events[:issued_index], item.id) and prior_coded
                and prior_coded.data.get("result") in ("blocked_authority", "blocked_environment")
                and ledger._thaw(prior_coded.data.get("outcome")) == ledger._thaw(current.blocker)):
            # Manual blocks have no runner policy to replay. Actor labels and
            # invocation spelling are not provenance: any coder start, attempt or
            # dispatch in that round keeps the exact runner-policy requirement.
            # Walk the consecutive blocked rounds so a later manual block cannot
            # erase an earlier runner's still-frozen configuration.
            for coded in reversed(events[:issued_index]):
                if coded.ev != ledger.Ev.coded or coded.slice != item.id:
                    continue
                if coded.data.get("result") not in ("blocked_authority", "blocked_environment"):
                    break
                prior_stem = f"{item.id}_r{coded.round}"
                prior_path = f"{directory}/{prior_stem}_dispatch.json"
                prior = next((e for e in events if e.ev == ledger.Ev.artifact
                              and e.data.get("path") == prior_path), None)
                runner = any(
                    e.round == coded.round and e.data.get("scope") == item.id and (
                        e.ev == ledger.Ev.attempt_started and e.data.get("role") == "coder"
                        or e.ev == ledger.Ev.artifact and e.data.get("path", "").rsplit("/", 1)[-1] in
                            (f"{prior_stem}_started.json", f"{prior_stem}_dispatch.json")) for e in events)
                runner = runner or any((self.root / p).exists() for p in (
                    prior_path, f"{directory}/{prior_stem}_started.json",
                    f"local_state/frutlups/coding/{prior_stem}.json"))
                if not runner:
                    continue
                blocked = ledger.outcome(self.root, ledger._thaw(coded.data["outcome"]),
                                         item.id, coded.round)
                policy = (ledger.read_reference(self.root, {"path": prior_path, "sha": prior.data["sha"]})
                          if prior else {})
                ledger._need(policy.get("seat") == seat_policy and policy.get("runtime") == runtime_policy
                             and policy.get("invocation") == blocked["invocation"],
                             "blocked continuation lacks unchanged coder configuration")
        issued = next(e for e in reversed(events) if e.ev == ledger.Ev.prompt
                      and e.slice == item.id and e.round == current.round)
        original = evidence.bound_text(self.root, dict(path=current.last_prompt, sha=issued.data["sha"]),
                                       render.CODING_LIMIT)
        render.require_prompt(original + render.outcome_contract(item.id, current.round, "0" * 32),
                              render.CODING_LIMIT, self.cfg.seats["coder"].adapter)
        # Readiness completes before any coder-start uncertainty is recorded; a failed
        # probe stops here with no marker or started artifact. Probe attempts extend
        # history, so the state and append prefix are refreshed under ownership.
        self._probe_before_dispatch()
        fresh = ledger.read(path)
        if tuple(fresh) != tuple(events):
            state = ledger.validate_history(self.root, fresh, self.roadmap, executable=self.cfg.git)
            ledger._need(state.slices[item.id] == current, "stale coding state after readiness")
            ledger._need(not any(e.ev == ledger.Ev.artifact and e.data.get("path") == started_path
                                 for e in fresh), "coding dispatch already started; retained uncertainty "
                                 "requires external disposition")
            events = fresh
        prefix = path.read_bytes()
        # A persisted start is uncertainty, not permission to repeat writable work.
        evidence.immutable_text(self.root, marker, json.dumps(dict(seat=seat_policy,
                                envelope=ledger._thaw(current.envelope))), 16384)
        started = evidence.immutable_text(self.root, started_path,
            json.dumps(dict(seat=seat_policy, runtime=runtime_policy, envelope=ledger._thaw(current.envelope))), 16384)
        ledger._append_coding(self.root, path, self.roadmap,
            _attempts.event("artifact", scope=item.id, round=current.round, role="evidence", **started),
            prefix, executable=self.cfg.git)
        chain = {}
        result, attempts, result_path = self._call("coder", Role.coder, current.last_prompt,
            current.round > 1, _chain=chain, _scope=item.id, _round_no=current.round, _typed=True)
        if result.status != "completed":
            raise _Stop(StopReason.seat_output if result.failure_class == FailureClass.output else StopReason.seat_transport,
                        f"coder transport incomplete: {result.diagnostic}; preserve {result_path} and edits")
        # Every historical reference and the same raw/write fence are checked
        # before the seat can legitimize retained evidence through an outcome.
        events = ledger.read(path)
        ledger.validate_history(self.root, events, self.roadmap, executable=self.cfg.git)
        changed = [row for row in evidence.changes(self.root, self.cfg)
                   if row not in current.baseline and row["path"] not in
                   evidence.evidence_paths(events, self.root) | {self.cfg.ledger}]
        origins = [{"path": row.original_path} for row in gitws.status(self.root, executable=self.cfg.git)
                   if row.original_path and row.path in {r["path"] for r in changed}]
        ledger._need(not gitws.fence(changed + origins, frozen["allowed_prefixes"], frozen["forbidden"]),
                     "coder changed forbidden paths or rename origins")
        value = evidence.parse_outcome(self.root, result.final_text, item.id, current.round, chain["invocation"])
        ledger._need(receipt.scrub_text(result.final_text, self.root, dict(os.environ)) == result.final_text,
                     "outcome contains private text; original retained in job output")
        cumulative = {r["path"]: {**dict(r), "round": e.round} for e in events
                      if e.ev == ledger.Ev.coded and e.slice == item.id for r in e.data["changed"]}
        observed = {r["path"] for r in changed}
        for relative, old in cumulative.items():
            if relative in observed:
                continue
            target = repo_path(self.root, relative)
            if old["kind"] != "deleted" and not target.exists():
                changed.append(dict(path=relative, kind="deleted", sha=old["sha"]))
            elif target.is_file() and (old["kind"] == "deleted" or ledger._file_sha(target) != old["sha"]):
                changed.append(dict(path=relative, kind="modified", sha=ledger._file_sha(target)))
        ledger._need(not gitws.fence(changed, frozen["allowed_prefixes"], frozen["forbidden"]),
                     "retained product differs outside frozen write boundary")
        cumulative.update({r["path"]: {**r, "round": current.round} for r in changed})
        prefix = path.read_bytes()
        before = chain["witness"]
        notes = evidence.immutable_text(self.root, f"{directory}/{stem}_coder.md", result.final_text, 16384)
        outcome = evidence.immutable_text(self.root, f"{directory}/{stem}_outcome.json",
                                          json.dumps(value, ensure_ascii=False), 16384)
        manifest = evidence.immutable_text(self.root, f"{directory}/{stem}_manifest.json",
            json.dumps(dict(schema="frutlups.manifest/2", **scope,
                            changed=[cumulative[p] for p in sorted(cumulative)])), 2 * 1024 * 1024)
        dispatch = evidence.immutable_text(self.root, f"{directory}/{stem}_dispatch.json",
            json.dumps(dict(invocation=chain["invocation"], seat=seat_policy, runtime=runtime_policy, status=result.status,
                            envelope=ledger._thaw(current.envelope), notes_sha=notes["sha"],
                            usage=evidence.usage(attempts))), 16384)
        after = self._seat_witness()
        added = {notes["path"], outcome["path"], manifest["path"], dispatch["path"]}
        ledger._need(before["head"] == after["head"] and before["index"] == after["index"]
                     and {p: v for p, v in before["files"].items() if p != self.cfg.ledger}
                     == {p: v for p, v in after["files"].items() if p not in added | {self.cfg.ledger}},
                     "workspace changed during coding persistence")
        ledger._append_coding(self.root, path, self.roadmap,
            _attempts.event("artifact", scope=item.id, round=current.round, role="evidence", **dispatch),
            prefix, executable=self.cfg.git)
        prefix = path.read_bytes()
        ledger._append_coding(self.root, path, self.roadmap,
            _attempts.event("coded", **scope, changed=changed, seat="coder", notes_path=notes["path"],
                            notes_sha=notes["sha"], outcome=outcome, manifest=manifest,
                            result=value["outcome"], **evidence.usage(attempts)),
            prefix, executable=self.cfg.git, invocation=chain["invocation"])

    def _holistic(self, milestone, state):
        with writer_lock(self.root, self.roadmap, executable=self.cfg.git):
            self._reconcile_findings()
        if (state.holistic.get(milestone.id, {}).get("step") == "close_pending"
                or milestone.id in state.milestones_done):
            decision = state.holistic[milestone.id]["event"]
            self._approval("milestone_done", [], f"Close {milestone.id}",
                           milestone=milestone.id, holistic_report=decision["report"])
            return
        with writer_lock(self.root, self.roadmap, executable=self.cfg.git):
            return self._holistic_v2(milestone, state)

    def _holistic_runtime(self):
        """Validate the preceding context before adopting the project declaration."""
        from . import runtime
        selected = runtime.resolve_runtime(self.root, self.roadmap, git=self.cfg.git)
        declaration = getattr(self, "_slice_runtime_declaration", None)
        if self.runtime:
            expected = (selected if declaration is None else receipt.native_runtime(
                self.root, self.roadmap, {"runtime": declaration}, self.cfg))
            ledger._need(self.runtime.executable == expected.executable
                         and dict(self.runtime.identity) == dict(expected.identity),
                         "selected holistic runtime drift")
            # Both digests were resolved under their own declaration rules. A
            # context change must still select the same physical runtime.
            ledger._need(selected.executable == expected.executable
                         and all(selected.identity[k] == expected.identity[k]
                                 for k in ("python", "implementation", "source")),
                         "selected holistic runtime drift")
        self.runtime = selected
        self._slice_runtime_declaration = None
        return selected

    def _holistic_v2(self, milestone, state):
        path = self.root / self.cfg.ledger
        events = ledger.read(path)
        current = ledger.validate_history(self.root, events, self.roadmap, executable=self.cfg.git)
        ledger._need(current == state, "stale holistic state")
        if current.holistic.get(milestone.id, {}).get("step") == "blocked":
            raise _Stop(StopReason.blocked_verdict, f"{milestone.id}: external resolved authority required")
        frontier = roadmap_module.next_milestone(self.roadmap, current)
        ledger._need(frontier is not None and frontier.id == milestone.id, "holistic outside current milestone")
        if self.roadmap.autonomy is not None:
            ledger._need(current.run_head is not None, "holistic requires current grant")
            run = current.runs[current.run_head["run"]]
            grant = run["admission"]
            ledger._need(run["closed"] is None and grant["milestone"] == milestone.id
                         and grant["stop"] == milestone.id
                         and {s.id for s in milestone.slices} <= set(grant["slices"]),
                         "holistic outside current grant boundary")
        self._guard()
        selected = self._holistic_runtime()
        self._historical_recheck()
        boundary = getattr(self, "_historical_boundary", None)
        support = evidence.holistic_support(self.root, self.cfg, self.roadmap, events, milestone, current,
            **({"historical_context": boundary[1]} if boundary is not None else {}))
        cycle = _attempts._digest(support["cycle"])
        stem = f"{milestone.id}_{cycle}_holistic"
        directory = f"{self.cfg.reviews_dir}/{milestone.id.lower()}/{stem}"
        sources = [row["manifest"] for row in support["accepted"]]
        base = ledger.read_reference(self.root, support["accepted"][0]["receipt"], render.SUPPORT_LIMIT)["base_commit"]
        index, pages = evidence.evidence_package(self.root, self.cfg, directory, sources, base)
        support_path = directory + "_support.json"
        prompt_path = f"{self.cfg.review_prompt_dir}/{stem}.md"
        candidate = self._presentation_events(events, milestone.id, "holistic",
            [ref["path"] for ref, _, _ in pages] + [support_path, prompt_path])
        support.update(product=_attempts.review_product(self.root, self.cfg, candidate),
                       runtime=dict(selected.identity), seat=asdict(self.cfg.seats["holistic"]), index=index)
        prior = next((e for e in events if e.ev == ledger.Ev.artifact
                      and e.data.get("path") == support_path), None)
        if prior:
            ledger._need(ledger.read_reference(self.root,
                dict(path=support_path, sha=prior.data["sha"]), render.SUPPORT_LIMIT) == support,
                "holistic candidate product/HEAD/index/runtime or authority drift")
        support_ref, support_text, support_limit = evidence.text_artifact(support_path,
            json.dumps(support, sort_keys=True), render.SUPPORT_LIMIT)
        text = render.holistic_v2(self.root, milestone, support, support_ref)
        render.require_prompt(text, render.REVIEW_LIMIT, self.cfg.seats["holistic"].adapter)
        prompt_ref, text, prompt_limit = evidence.text_artifact(prompt_path, text, render.REVIEW_LIMIT)
        artifacts = [("evidence", *page) for page in pages]
        artifacts += [("evidence", support_ref, support_text, support_limit),
                      ("holistic_prompt", prompt_ref, text, prompt_limit)]
        self._retain_presentation(events, milestone.id, "holistic", artifacts)
        self._clean()
        parsed, body, invocation = self._review("holistic", Role.holistic, prompt_path,
                                               milestone.id, "holistic", _durable=True)
        events = ledger.read(path)
        current = ledger.validate_history(self.root, events, self.roadmap, executable=self.cfg.git)
        checkpoint = current.checkpoints[invocation]
        # Finishes lack a scope field in the pinned grammar. Explicit artifact
        # references make every original result/output eligible for the close commit.
        for attempt in current.attempts.values():
            if attempt["start"]["contract"] != checkpoint["contract"]:
                continue
            for ref in attempt["finish"]["evidence"]:
                if not any(e.ev == ledger.Ev.artifact and e.data.get("path") == ref["path"] for e in events):
                    self._review_append("artifact", scope=milestone.id, round="holistic", role="evidence", **ref)
        report = dict(path=checkpoint["report"], sha=checkpoint["sha"])
        events = ledger.read(path)
        if not any(e.ev == ledger.Ev.artifact and e.data.get("role") == "holistic_report"
                   and e.data.get("path") == report["path"] for e in events):
            self._review_append("artifact", scope=milestone.id, round="holistic", role="holistic_report", **report)
        self._review_append("holistic_reviewed", milestone=milestone.id, report=report["path"],
                            sha=report["sha"], verdict=parsed.verdict, open=list(verdict.open_blocking(parsed)))
        if parsed.verdict == "pass":
            self._approval("milestone_done", [], f"Close {milestone.id}",
                           milestone=milestone.id, holistic_report=report["path"])

    def _approval(self, ev, paths, message, **scope):
        self._historical_recheck()
        from . import commit
        if self.cfg.ledger != commit.LEDGER:
            raise ValueError("/2 approval requires the protocol ledger location")
        return commit.approve(self.root, self.roadmap, _attempts.event(ev, **scope),
                              message, self.cfg.commit_on_accept, executable=self.cfg.git)

    def _reconcile_findings(self):
        """Recover only the projection, after history and pending ownership checks."""
        self._historical_recheck()
        from . import commit
        events = ledger.read(self.root / self.cfg.ledger)
        if any("commit_intent" in event.data for event in events):
            commit.recover(self.root, self.roadmap, executable=self.cfg.git)
            events = ledger.read(self.root / self.cfg.ledger)
            self._historical_recheck()
        commit.require_idle(self.root, events, self.roadmap, executable=self.cfg.git)
        ledger.validate_history(self.root, events, self.roadmap, executable=self.cfg.git)
        if any(event.ev in (ledger.Ev.reviewed, ledger.Ev.holistic_reviewed) for event in events):
            findings.reconcile(self.root, events)

    def _complete(self, milestone, state):
        return all(state.slices[s.id].step == "accepted" for s in milestone.slices) and (
            not milestone.holistic_review or milestone.id in state.milestones_done
        )

    def _probe_before_dispatch(self):
        """A public run probes once, immediately before its first model seat dispatch.

        Recovery, early stops, evidence checks and prompt issuance all precede it;
        a seat context already probed in the current run is not repeated.
        """
        if not getattr(self, "_probe_pending", False):
            return
        state = ledger.validate_history(self.root, ledger.read(self.root / self.cfg.ledger),
                                        self.roadmap, executable=self.cfg.git)
        memory = self.roadmap.memory is not None
        names = [name for name in _probe_plan(self.cfg, self.roadmap, state)
                 if not _probed(self.root, self.cfg, self.roadmap, name, memory)]
        errors = _errors(self.root, _probes(self.root, self.cfg, self.roadmap, names))
        if errors:
            raise _Stop(StopReason.preflight_failed, "; ".join(errors))
        self._probe_pending = False

    def iteration(self):
        """Perform one owned transition (including its bounded retries); return a stop or None."""
        if self.roadmap.schema != ledger.ROADMAP_V2:
            raise ValueError("unsupported roadmap schema; use native /2 or pinned v4")
        with writer_lock(self.root, self.roadmap, executable=self.cfg.git):
            # run() already checked authority in this same ownership interval.
            errors = [] if getattr(self, "_admitted", False) else canonical_errors(
                self.root, self.cfg, self.roadmap)
            if errors:
                detail = _errors(self.root, errors)[0]
                self.notify("stop", reason=StopReason.preflight_failed.value, detail=detail)
                return StopReason.preflight_failed
            return self._iteration_owned(historical_assessment=self.historical_assessment)

    def _iteration_owned(self, *, historical_assessment=None):
        with ExitStack() as admissions:
            item = active = None
            try:
                admitted = False
                deadline = self.started + self.cfg.max_wall_minutes * 60
                owned = context = None
                if historical_assessment is not None:
                    if (Path(self.root).resolve() / "STOP").exists():
                        raise _Stop(StopReason.kill_switch, "STOP exists; owner must remove it to resume")
                    owned = admissions.enter_context(_owned_history(self.root, self.cfg, self.roadmap,
                        deadline=deadline, clock=self.clock, selected=True))
                    self.cfg, self.roadmap, events = owned.cfg, owned.model, owned.events
                    context = _select_historical(owned, historical_assessment)
                    self._historical_boundary = (owned, context)
                else:
                    # Owned STOP precedence before any checker or pending recovery; the
                    # rest of _guard (require_idle) must follow valid recovery.
                    if (Path(self.root).resolve() / "STOP").exists():
                        raise _Stop(StopReason.kill_switch, "STOP exists; owner must remove it to resume")
                    events = ledger.read(self.root / self.cfg.ledger)
                if any("commit_intent" in e.data for e in events):
                    if owned is not None:
                        self.cfg, self.roadmap, events = _admit_checked(owned, context)
                    else:
                        self.cfg, self.roadmap, events = admissions.enter_context(_owned_admission(
                            self.root, self.cfg, self.roadmap, deadline=deadline, clock=self.clock))
                    admitted = True
                state = ledger.validate_history(self.root, events, self.roadmap, executable=self.cfg.git)
                self._guard()
                target = next((m for m in self.roadmap.milestones if m.id == self.target_milestone), None)
                ledger._need(self.target_milestone is None or target is not None,
                             f"unknown milestone: {self.target_milestone}; choose a roadmap milestone")
                complete = {m.id for m in self.roadmap.milestones if self._complete(m, state)}
                if (self.until == "slice" and self.accepted_this_run
                        or self.until == "milestone" and self.target_milestone in complete
                        or len(complete) == len(self.roadmap.milestones)):
                    drift = ledger.check(self.root / self.cfg.ledger, self.root, executable=self.cfg.git)
                    ledger._need(not drift, "; ".join(map(str, drift)))
                if self.until == "slice" and self.accepted_this_run:
                    raise _Stop(StopReason.boundary, "one slice accepted")
                if self.until == "milestone" and self.target_milestone in complete:
                    label = ("roadmap already done" if target.status == "done"
                             else "mark roadmap milestone done separately")
                    raise _Stop(StopReason.boundary, f"milestone completed; {label}")
                if len(complete) == len(self.roadmap.milestones):
                    raise _Stop(StopReason.done, "all admitted roadmap milestones completed")
                active = roadmap_module.next_milestone(self.roadmap, state)
                if self.target_milestone and self.target_milestone not in complete:
                    reopened = active and any(state.slices[s.id].reopened for s in active.slices)
                    if not reopened:
                        active = target
                if self.roadmap.autonomy is not None:
                    ledger._need(state.run_head is not None, "profile requires current grant")
                    run = state.runs[state.run_head["run"]]
                    grant = run["admission"]
                    if run["closed"] is not None or grant["milestone"] in complete:
                        drift = ledger.check(self.root / self.cfg.ledger, self.root, executable=self.cfg.git)
                        ledger._need(not drift, "; ".join(map(str, drift)))
                        raise _Stop(StopReason.boundary, "current grant boundary completed")
                    ledger._need(active is not None and active.id == grant["milestone"],
                                 "milestone outside current grant")
                if active:
                    self.target_milestone = active.id
                    item = roadmap_module.next_slice(replace(self.roadmap, milestones=(active,)), state)
                    if self.roadmap.autonomy is not None:
                        ledger._need(item is None or item.id in grant["slices"], "slice outside current grant")
                if item or active and all(state.slices[s.id].step == "accepted" for s in active.slices):
                    if not admitted:
                        if owned is not None:
                            self.cfg, self.roadmap, events = _admit_checked(owned, context)
                        else:
                            self.cfg, self.roadmap, events = admissions.enter_context(_owned_admission(
                                self.root, self.cfg, self.roadmap, deadline=deadline, clock=self.clock))
                        state = ledger.validate_history(self.root, events, self.roadmap, executable=self.cfg.git)
                if item:
                    if state.slices[item.id].step == "blocked":
                        raise _Stop(StopReason.blocked_verdict,
                                    f"{item.id}: human/architect resolved authority required")
                    ledger._need(self.roadmap.autonomy is not None, "fresh dispatch requires an autonomy profile")
                    _profile_gate(self.roadmap, state, commit.status(
                        self.root, events, self.roadmap, executable=self.cfg.git) if state.intents else ())
                    self._slice(item, state.slices[item.id], events)
                elif active and all(state.slices[s.id].step == "accepted" for s in active.slices):
                    if state.holistic.get(active.id, {}).get("step") == "blocked":
                        raise _Stop(StopReason.blocked_verdict,
                                    f"{active.id}: external resolved authority required")
                    ledger._need(self.roadmap.autonomy is not None, "fresh dispatch requires an autonomy profile")
                    _profile_gate(self.roadmap, state, commit.status(
                        self.root, events, self.roadmap, executable=self.cfg.git) if state.intents else ())
                    self._holistic(active, state)
                else:
                    raise _Stop(
                        StopReason.needs_specification, "architect must admit the next milestone"
                    )
                return None
            except _Stop as exc:
                reason, detail = exc.reason, exc.detail
            except Exception as exc:  # noqa: BLE001 - Preserve the tree and record internal stops.
                scope = item or active
                scope = f"{scope.id}: " if scope else ""
                reason = StopReason.internal
                detail = f"{scope}{type(exc).__name__}: {exc}; owner must inspect"
                if (isinstance(exc, ledger.LedgerError)
                        and str(exc) == "STOP exists; the owner must remove it to run"):
                    reason = StopReason.kill_switch
                    detail = "STOP exists; owner must remove it to resume"
            finally:
                self.__dict__.pop("_historical_boundary", None)
            detail = receipt._scrub(detail.encode("utf-8"), self.root, dict(os.environ))
            self.notify("stop", reason=reason.value, detail=detail)
            return reason


def run(root, cfg=None, roadmap=None, until=None, clock=time.monotonic, *, once=False, emit=None,
        historical_assessment=None):
    with admission(root, cfg, roadmap, until=until, historical_assessment=historical_assessment,
                   probe=False) as admitted:
        if admitted.errors:
            for detail in admitted.errors:
                if emit is None:
                    print(detail)
                else:
                    emit({"action": "preflight", "ok": False, "detail": detail})
            return StopReason.preflight_failed
        runner = Loop(root, admitted.cfg, admitted.roadmap, until, clock, emit=emit,
                      selected_runtime=admitted.runtime, historical_assessment=historical_assessment)
        runner._admitted = True
        while True:
            result = runner.iteration()
            if result is not None:
                return result
            if once:
                return None
