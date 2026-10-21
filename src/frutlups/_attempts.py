"""Durable operation boundaries for admitted probes, seats and verification."""

import hashlib
import json
import math
import time
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from . import gitws, ledger
from .ownership import writer_lock


def _data(value):
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    if is_dataclass(value):
        return {f.name: _data(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {key: _data(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_data(item) for item in value]
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(_data(value), sort_keys=True,
                                    separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def event(ev, **values):
    return dict(schema=ledger.SCHEMA_V2, t=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                by="frutlups", ev=ev, **values)


def review_product(root, cfg, events):
    from ._loop_evidence import evidence_paths
    excluded = evidence_paths(events, root) | {cfg.ledger}
    witness = gitws.workspace_witness(root, executable=cfg.git)
    return _digest(dict(head=witness["head"], index=witness["index"],
                        product={p: v for p, v in witness["files"].items() if p not in excluded}))


def _binding(root, cfg, model, events, state, scope, round_no, role, job, prompt, runtime_identity=None):
    issued, excluded = [], {cfg.ledger}
    for entry in events:
        row = ledger._event_dict(entry)
        excluded.update(row[key] for key in ("path", "receipt", "report", "notes_path") if key in row)
        refs = row.get("authority", []) + row.get("evidence", [])
        refs += [row[key] for key in ("envelope", "manifest", "outcome") if key in row]
        if "commit_intent" in row:
            refs.append(row["commit_intent"]["manifest"])
        if "outcome" in row:
            refs += ledger.read_reference(root, row["outcome"], 16 * 1024)["evidence"]
        excluded.update(ref["path"] for ref in refs)
        if entry.ev == ledger.Ev.prompt and (entry.slice == scope or entry.slice.startswith(scope + "-S")):
            issued.append({key: row[key] for key in ("slice", "round", "envelope", "baseline", "path", "sha")
                           if key in row})
    witness = gitws.workspace_witness(root, executable=cfg.git)
    product = {path: identity for path, identity in witness["files"].items() if path not in excluded}
    seat = None
    if job is not None:
        from .seats import Role, claude, pi
        adapter = pi if job.adapter == "pi" else claude
        configured = cfg.seats[job.seat_name]
        effort = (configured.corrective_effort if role == "coder" and round_no > 1
                  and configured.corrective_effort else configured.effort)
        ledger._need((job.adapter, job.provider, job.model, job.effort) ==
                     (configured.adapter, configured.provider, configured.model, effort),
                     "job differs from configured seat")
        expected_role = Role.reviewer if role == "probe" else Role(role)
        ledger._need(job.role == expected_role, "job differs from operation role")
        ledger._need(job.tools == adapter.tools_for(expected_role), "job tools differ from role")
        from ._preflight import _routes
        routes, _ = _routes(model, state)
        if role == "coder":
            routes = {"coder"}
        elif role == "holistic":
            routes = {"holistic"}
        elif role == "reviewer":
            item = next(s for m in model.milestones for s in m.slices if s.id == scope)
            routes = model.review_routing[item.risk]
        ledger._need(job.seat_name in routes, "seat outside effective route")
        # Bind the installed parser implementation, not a caller-supplied label.
        from . import verdict, render
        parser = _digest([hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                          for module in (adapter, verdict)])
        seat = dict(name=job.seat_name, adapter=job.adapter, provider=job.provider, model=job.model,
                    effort=job.effort, tools=job.tools, parser=parser,
                    framing=ledger.evidence_sha(render.dispatch_framing(job.adapter).encode()))
        if job.adapter == "pi":
            seat["pi_record_bytes"] = pi.record_limit(job, cfg)
            seat["extension"] = hashlib.sha256(pi.extension_path().read_bytes()).hexdigest()
    from ._loop_evidence import holistic_cycle
    return _digest(dict(scope=scope, round=round_no, role=role, issued=issued,
                        **({"cycle": holistic_cycle(events, scope)} if role == "holistic" else {}),
                        roadmap=model, product=product, head=witness["head"], index=witness["index"],
                        original_prompt=ledger.evidence_sha(prompt), seat=seat,
                        **({"runtime": runtime_identity} if runtime_identity is not None else {})))


class Operation:
    """Hold ownership from validated reservation through explicit durable settlement.

    Exceptions deliberately leave the reservation unfinished. Reacquiring ownership
    is not evidence of child termination and cannot authorize recovery.
    """
    def __init__(self, root, cfg, model, role, cap, *, scope=None, round_no=None,
                 job=None, prompt=b"", retry="initial", parent=None, clock=time.monotonic,
                 review=False, runtime_identity=None):
        self.root, self.cfg, self.model = root, cfg, model
        self.role, self.timeout = str(role), cap
        self.scope, self.round = scope, round_no
        self.job, self.prompt, self.retry, self.parent = job, prompt, retry, parent
        self.clock, self.invocation = clock, job.id if job else uuid4().hex
        ledger._need(not review or model.schema == ledger.ROADMAP_V2 and role in ("reviewer", "holistic"),
                     "durable review requires a /2 reviewer or holistic seat")
        self.review, self.runtime_identity = review, runtime_identity
        self.enabled = model.autonomy is not None or review
        self.finished = False
        self.stack = ExitStack()

    def __enter__(self):
        try:
            if self.model.schema == ledger.ROADMAP_V2:
                from . import commit
                self.stack.enter_context(writer_lock(self.root, self.model, executable=self.cfg.git))
                commit.require_idle(self.root, ledger.read(self.root / self.cfg.ledger), self.model, executable=self.cfg.git)
            if self.enabled:
                self.stack.enter_context(writer_lock(self.root, self.model, executable=self.cfg.git))
                events = ledger.read(self.root / self.cfg.ledger)
                state = ledger.validate_history(self.root, events, self.model, executable=self.cfg.git)
                ledger._need(not (self.root / "STOP").exists(), "STOP exists")
                run = None
                if self.model.autonomy is not None:
                    ledger._need(state.run_head is not None, "profile requires a current run")
                    run = state.runs[state.run_head["run"]]
                    grant = run["admission"]
                    milestone = next(m for m in self.model.milestones if m.id == grant["milestone"])
                    ledger._need(milestone.status == "active", "new operation requires active milestone")
                if self.scope is None:
                    if self.role in ("probe", "holistic"):
                        self.scope, self.round = grant["milestone"], "holistic"
                    else:
                        from .roadmap import next_slice
                        current = next_slice(self.model, state)
                        ledger._need(current is not None, "no current operation scope")
                        self.scope, self.round = current.id, state.slices[current.id].round
                current = state.slices.get(self.scope)
                if current is not None:
                    from .roadmap import next_slice
                    selected = next_slice(self.model, state)
                    ledger._need(selected is not None and selected.id == self.scope,
                                 "operation outside current lifecycle selection")
                    ledger._need(self.round == current.round, "stale operation round")
                    issued = next((e for e in reversed(events) if e.ev == ledger.Ev.prompt
                                   and e.slice == self.scope and e.round == self.round), None)
                    if issued is not None and "envelope" in issued.data:
                        envelope = ledger.envelope(self.root, ledger._thaw(issued.data["envelope"]),
                                                   self.scope, self.round)
                        if self.role == "verification":
                            self.timeout = min(self.timeout, envelope["timeout_seconds"])
                self.contract = _binding(self.root, self.cfg, self.model, events, state,
                                         self.scope, self.round, self.role, self.job, self.prompt,
                                         self.runtime_identity)
                if self.parent:
                    previous = state.attempts.get(self.parent)
                    ledger._need(previous is not None and previous["start"]["contract"] == self.contract,
                                 "stale retry contract")
                self.timeout = math.floor(self.timeout)
                if run is not None:
                    self.timeout = min(self.timeout, run["remaining"]["seconds"])
                ledger._need(type(self.timeout) is int and self.timeout > 0, "no positive time reservation")
                values = dict(invocation=self.invocation, scope=self.scope, round=self.round,
                              role=self.role, contract=self.contract, allowance_seconds=self.timeout,
                              retry=self.retry)
                if self.parent:
                    values["parent"] = self.parent
                self._append(event("attempt_started", **values))
            self.started = self.clock()
            return self
        except BaseException:
            self.stack.close()
            raise

    def _append(self, raw):
        path = self.root / self.cfg.ledger
        if self.review:
            ledger._append_review(self.root, path, self.model, raw, path.read_bytes(),
                                  executable=self.cfg.git)
        else:
            ledger._append_operation(self.root, path, self.model, raw, executable=self.cfg.git)

    def finish(self, completed, result=None, *, evidence=None):
        ledger._need(not self.finished, "operation already finished")
        if self.enabled:
            usage = {"secs": max(0, self.clock() - self.started),
                     "completeness": getattr(result, "usage_completeness", "unknown")}
            for key in ("tokens_in", "tokens_out", "cost_usd"):
                value = getattr(result, key, None)
                if value is not None:
                    usage[key] = value
            self._append(event("attempt_finished", invocation=self.invocation,
                               status="completed" if completed else "failed", usage=usage,
                               **({"evidence": evidence} if evidence is not None else {})))
        self.finished = True

    def __exit__(self, *exc):
        return self.stack.__exit__(*exc)
