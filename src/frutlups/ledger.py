"""Strict append-only ledger parsing, folding, and evidence checks."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType

from ._paths import repo_path, safe_rel
from .roadmap import Roadmap
from . import _autonomy

SCHEMA_V2 = "frutlups.ledger/2"
SHA = re.compile(r"[0-9a-f]{64}$")
COMMIT = re.compile(r"[0-9a-f]{7,64}$")
TIME = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
ACTORS = ("human", "architect", "frutlups")
KINDS = ("added", "modified", "deleted", "renamed")


class Ev(StrEnum):
    prompt = "prompt"
    artifact = "artifact"
    coded = "coded"
    verified = "verified"
    reviewed = "reviewed"
    accepted = "accepted"
    reopened = "reopened"
    milestone_done = "milestone_done"
    note = "note"
    stop = "stop"
    resolved = "resolved"
    commit_cancelled = "commit_cancelled"
    holistic_reviewed = "holistic_reviewed"
    review_checkpoint = "review_checkpoint"
    attempt_started = "attempt_started"
    attempt_finished = "attempt_finished"
    run_admitted = "run_admitted"
    run_extended = "run_extended"


FIELDS = {
    Ev.prompt: {"slice", "round", "path", "sha", "baseline"},
    Ev.artifact: {"scope", "round", "role", "path", "sha"},
    Ev.coded: {
        "slice", "round", "changed", "notes_path", "seat", "secs", "tokens_in",
        "tokens_out", "cost_usd",
    },
    Ev.verified: {"slice", "round", "receipt", "sha", "ok"},
    Ev.reviewed: {
        "slice", "round", "report", "sha", "verdict", "open", "seat", "secs",
        "tokens_in", "tokens_out", "cost_usd",
    },
    Ev.accepted: {"slice", "round", "commit"},
    Ev.reopened: {"slice", "round", "reason"},
    Ev.milestone_done: {"milestone", "holistic_report"},
    Ev.note: {"text", "slice"},
    Ev.stop: {"reason", "detail"},
}
OPTIONAL = {
    Ev.prompt: {"baseline"},
    Ev.coded: {"notes_path", "seat", "secs", "tokens_in", "tokens_out", "cost_usd"},
    Ev.reviewed: {"seat", "secs", "tokens_in", "tokens_out", "cost_usd"},
    Ev.accepted: {"commit"},
    Ev.milestone_done: {"holistic_report"},
    Ev.note: {"slice"},
}
COMMON = {"schema", "t", "ev", "by"}


class LedgerError(ValueError):
    """Ledger syntax, lifecycle, or evidence is invalid."""


@dataclass(frozen=True)
class Event:
    t: str
    ev: Ev
    by: str
    slice: str | None = None
    milestone: str | None = None
    round: int | str | None = None
    data: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))
    schema: str = SCHEMA_V2


@dataclass(frozen=True)
class SliceState:
    id: str
    step: str = "unstarted"
    round: int = 1
    open_findings: tuple[str, ...] = ()
    last_prompt: str | None = None
    baseline: tuple[Mapping[str, object], ...] = ()
    last_receipt: str | None = None
    last_report: str | None = None
    changed: tuple[Mapping[str, object], ...] = ()
    notes_path: str | None = None
    reopened: bool = False
    unblock_reason: str | None = None
    reopen_reason: str | None = None
    reopen_report: str | None = None
    corrective_rounds_used: int = 0
    envelope: Mapping | None = None
    manifest: Mapping | None = None
    outcome: Mapping | None = None
    blocker: Mapping | None = None
    resolution: str | None = None
    notes_sha: str | None = None


@dataclass(frozen=True)
class ProjectState:
    slices: Mapping[str, SliceState]
    milestones_done: frozenset[str]
    events: int
    holistic: Mapping = field(default_factory=lambda: MappingProxyType({}))
    attempts: Mapping = field(default_factory=lambda: MappingProxyType({}))
    checkpoints: Mapping = field(default_factory=lambda: MappingProxyType({}))
    intents: Mapping = field(default_factory=lambda: MappingProxyType({}))
    runs: Mapping = field(default_factory=lambda: MappingProxyType({}))
    run_membership: Mapping = field(default_factory=lambda: MappingProxyType({}))
    cancelled: frozenset[str] = frozenset()
    run_head: Mapping | None = None


@dataclass(frozen=True)
class Drift:
    path: str
    kind: str = "drift"

    def __str__(self) -> str:
        return f"{self.kind}: {self.path}"


def _need(ok: object, message: str) -> None:
    if not ok:
        raise LedgerError(message)


def _changes(value: object, line: str, field: str) -> tuple[Mapping[str, object], ...]:
    _need(isinstance(value, list), f"{line}: {field} must be a list")
    output = []
    for number, item in enumerate(value):
        valid = (
            isinstance(item, dict) and set(item) == {"path", "sha", "kind"}
            and item.get("kind") in KINDS and bool(SHA.fullmatch(str(item.get("sha", ""))))
        )
        _need(valid, f"{line}: invalid {field}[{number}]")
        try:
            path = safe_rel(item["path"], error=LedgerError)
        except LedgerError as exc:
            raise LedgerError(f"{line}: {exc}") from exc
        output.append(MappingProxyType({"path": path, "sha": item["sha"], "kind": item["kind"]}))
    return tuple(output)


def _parse_event(value: object, line: str = "event") -> Event:
    _need(isinstance(value, dict), f"{line}: event must be an object")
    _need(value.get("schema") != "frutlups.ledger/1",
          f"{line}: unsupported legacy history; use pinned v4")
    _need(value.get("schema") == SCHEMA_V2, f"{line}: invalid schema or event: unknown ledger schema; expected frutlups.ledger/2")
    try:
        validate_v2(value)
    except ValueError as exc:
        raise LedgerError(f"{line}: {exc}") from exc
    data = {key: item for key, item in value.items()
            if key not in COMMON | {"slice", "milestone", "round"}}
    return Event(value["t"], Ev(value["ev"]), value["by"], value.get("slice"),
                 value.get("milestone"), value.get("round"), _freeze(data), SCHEMA_V2)


def _validate_base(value: dict, line: str = "event") -> dict:
    """Validate shared fields and prepare data without admitting or rewriting a schema."""
    ev = value.get("ev")
    _need(ev in FIELDS, f"{line}: invalid schema or event")
    _need(value.get("by") in ACTORS, f"{line}: invalid by")
    _need(isinstance(value.get("t"), str) and TIME.fullmatch(value["t"]),
          f"{line}: invalid UTC timestamp")
    unknown = set(value) - COMMON - FIELDS[ev]
    missing = FIELDS[ev] - OPTIONAL.get(ev, set()) - set(value)
    _need(not unknown and not missing,
          f"{line}: unknown={sorted(unknown)} missing={sorted(missing)}")
    if "round" in value:
        valid_round = type(value["round"]) is int and value["round"] >= 1
        if ev == Ev.artifact and value.get("role") in ("holistic_prompt", "holistic_report"):
            valid_round = value["round"] == "holistic"
        _need(valid_round, f"{line}: invalid round")
    if "slice" in value:
        _need(bool(re.fullmatch(r"M\d{3}-S\d{2}", str(value["slice"]))),
              f"{line}: invalid slice")
    if "milestone" in value:
        _need(bool(re.fullmatch(r"M\d{3}", str(value["milestone"]))),
              f"{line}: invalid milestone")
    if ev == Ev.artifact:
        scope, role = str(value.get("scope", "")), value.get("role")
        valid = (
            bool(re.fullmatch(r"M\d{3}-S\d{2}", scope)) and role == "review_prompt"
            and type(value["round"]) is int
            or bool(re.fullmatch(r"M\d{3}", scope))
            and role in ("holistic_prompt", "holistic_report")
            and value["round"] == "holistic"
        )
        actor = value["by"] in ("architect", "frutlups")
        actor = actor or role == "holistic_report" and value["by"] == "human"
        _need(valid and actor, f"{line}: invalid artifact scope, round, role, or actor")
    for key in ("path", "receipt", "report", "notes_path", "holistic_report"):
        if key in value:
            try:
                safe_rel(value[key], error=LedgerError)
            except LedgerError as exc:
                raise LedgerError(f"{line}: {exc}") from exc
    if "sha" in value:
        _need(isinstance(value["sha"], str) and SHA.fullmatch(value["sha"]),
              f"{line}: invalid sha")
    if "commit" in value:
        _need(isinstance(value["commit"], str) and COMMIT.fullmatch(value["commit"]),
              f"{line}: invalid commit")
    if ev == Ev.verified:
        _need(isinstance(value["ok"], bool), f"{line}: ok must be boolean")
    if ev == Ev.reviewed:
        valid = (
            value["verdict"] in ("pass", "needs_work", "blocked", "override")
            and (value["verdict"] != "override" or value["by"] == "human")
            and isinstance(value["open"], list)
            and all(isinstance(item, str) and item for item in value["open"])
        )
        _need(valid, f"{line}: invalid review fields")
    text = ("reason", "detail", "text", "seat")
    numeric, counts = ("secs", "cost_usd"), ("tokens_in", "tokens_out")
    valid = all(key not in value or isinstance(value[key], str) and value[key].strip()
                for key in text)
    valid = valid and all(key not in value or type(value[key]) in (int, float)
                          and value[key] >= 0 for key in numeric)
    valid = valid and all(key not in value or type(value[key]) is int and value[key] >= 0
                          for key in counts)
    valid = valid and (ev != Ev.stop or value["by"] == "frutlups")
    _need(valid, f"{line}: invalid text, usage, or actor field")
    data = {key: item for key, item in value.items()
            if key not in COMMON | {"slice", "milestone", "round"}}
    if ev == Ev.coded:
        data["changed"] = _changes(value["changed"], line, "changed")
    if ev == Ev.prompt and "baseline" in value:
        data["baseline"] = _changes(value["baseline"], line, "baseline")
    if ev == Ev.reviewed:
        data["open"] = tuple(value["open"])
    return data


def read(path: Path) -> tuple[Event, ...]:
    if not path.exists():
        return ()
    events = []
    try:
        with path.open("rb") as stream:
            number = 0
            while raw := stream.readline(EVENT_LIMIT + 1):
                number += 1
                _need(len(raw) <= EVENT_LIMIT, f"line {number}: ledger event exceeds 2 MiB")
                if not raw.rstrip(b"\r\n"):
                    continue
                try:
                    event = _parse_event(json.loads(raw.decode("utf-8")), f"line {number}")
                except json.JSONDecodeError as exc:
                    raise LedgerError(f"line {number}: malformed JSON: {exc.msg}") from exc
                events.append(event)
    except (OSError, UnicodeError) as exc:
        raise LedgerError(f"cannot read {path.name}: {exc}") from exc
    return tuple(events)


def _mutable_state(roadmap: Roadmap) -> dict[str, dict]:
    return {
        item.id: {
            "step": "unstarted", "round": 1, "open_findings": (), "last_prompt": None,
            "baseline": (), "last_receipt": None, "last_report": None, "changed": (),
            "notes_path": None, "reopened": False, "unblock_reason": None,
            "reopen_reason": None, "reopen_report": None,
            "corrective_rounds_used": 0,
        }
        for milestone in roadmap.milestones for item in milestone.slices
    }


def fold(events: Sequence[Event], roadmap: Roadmap) -> ProjectState:
    _need(roadmap.schema == ROADMAP_V2, "unsupported roadmap schema; use native /2 or pinned v4")
    states, done = _mutable_state(roadmap), set()
    holistic_reports = {}
    milestones = {item.id: item for item in roadmap.milestones}
    extras = {"holistic": {}, "attempts": {}, "checkpoints": {}, "intents": {}, "cancelled": set()}
    rm = {"schema": roadmap.schema, "milestones": [
        {"id": m.id, "status": m.status, "holistic_review": m.holistic_review,
         "slices": [{"id": item.id} for item in m.slices]} for m in roadmap.milestones]}
    if roadmap.autonomy is not None:
        rm["autonomy"] = dict(roadmap.autonomy)
    profile_milestones = {m["id"]: m for m in rm["milestones"]}
    for event in events:
        raw = _event_dict(event)
        _parse_event(raw)
        if _autonomy.apply(raw, rm, states, done, extras):
            continue
        if apply_v2(raw, states, profile_milestones, done, extras):
            continue
        ev = event.ev
        if ev in (Ev.note, Ev.stop):
            continue
        if ev == Ev.artifact:
            scope, role = event.data["scope"], event.data["role"]
            if role == "review_prompt":
                _need(scope in states, f"unknown slice in ledger: {scope}")
                current = states[scope]
                ready = current["step"] == "reviewing" and event.round == current["round"]
                _need(ready, f"{scope}: review prompt artifact out of order")
            else:
                _need(scope in milestones, f"unknown milestone in ledger: {scope}")
                milestone = milestones[scope]
                ready = all(states[item.id]["step"] == "accepted"
                            for item in milestone.slices)
                _need(milestone.holistic_review and scope not in done and ready,
                      f"{scope}: holistic prompt artifact out of order")
                if role == "holistic_report":
                    holistic_reports[scope] = event.data["path"]
            continue
        if ev == Ev.milestone_done:
            mid = event.milestone
            _need(mid in milestones, f"unknown milestone in ledger: {mid}")
            milestone = milestones[mid]
            ready = all(states[item.id]["step"] == "accepted" for item in milestone.slices)
            _need(milestone.holistic_review and mid not in done and ready,
                  f"{mid}: invalid or premature milestone_done")
            _need(extras["holistic"].get(mid, {}).get("step") == "close_pending",
                      "milestone close requires recorded passing holistic review")
            extras["holistic"].setdefault(mid, {})["step"] = "done"
            done.add(mid)
            continue
        sid = event.slice
        _need(sid in states, f"unknown slice in ledger: {sid}")
        current, round_no = states[sid], event.round
        if ev == Ev.prompt:
            ready = current["step"] in ("unstarted", "fix") and round_no == current["round"]
            _need(ready, f"{sid}: prompt out of order")
            current.update(step="coding", last_prompt=event.data["path"],
                           baseline=event.data.get("baseline", ()))
            current["envelope"] = _freeze(event.data.get("envelope"))
            current["corrective_rounds_used"] += round_no > 1
        elif ev == Ev.coded:
            _need(current["step"] == "coding" and round_no == current["round"],
                  f"{sid}: coded out of order")
            current.update(step="verifying", changed=event.data["changed"],
                           notes_path=event.data.get("notes_path"),
                           manifest=event.data.get("manifest"), outcome=event.data.get("outcome"),
                           notes_sha=event.data.get("notes_sha"))
            if event.data["result"] != "implemented":
                current.update(step="blocked", blocker=event.data["outcome"])
        elif ev == Ev.verified:
            _need(current["step"] == "verifying" and round_no == current["round"],
                  f"{sid}: verified out of order")
            ok = event.data["ok"]
            current.update(step="reviewing" if ok else "fix",
                           last_receipt=event.data["receipt"],
                           round=round_no if ok else round_no + 1)
        elif ev == Ev.reviewed:
            _need(current["step"] == "reviewing" and round_no == current["round"],
                  f"{sid}: reviewed out of order")
            step = {"pass": "accept_pending", "override": "accept_pending",
                    "needs_work": "fix", "blocked": "blocked"}[event.data["verdict"]]
            current.update(step=step, last_report=event.data["report"],
                           open_findings=event.data["open"],
                           round=round_no + (step == "fix"), unblock_reason=None)
            if step == "blocked":
                current["blocker"] = {"path": event.data["report"], "sha": event.data["sha"]}
        elif ev == Ev.accepted:
            _need(current["step"] == "accept_pending" and round_no == current["round"],
                  f"{sid}: accepted out of order")
            current.update(step="accepted", open_findings=(), reopened=False,
                           unblock_reason=None, reopen_reason=None, reopen_report=None)
        elif ev == Ev.reopened:
            _need(current["step"] == "accepted" and round_no == current["round"] + 1,
                  f"{sid}: reopened out of order")
            current.update(
                step="fix", round=round_no, open_findings=(), reopened=True,
                reopen_reason=event.data["reason"],
                reopen_report=holistic_reports.get(sid.split("-")[0]),
            )
            done.discard(sid.split("-")[0])
            if sid.split("-")[0] in extras["holistic"]:
                extras["holistic"][sid.split("-")[0]].update(
                    step="reviewing", resolution="Slice reopened: " + event.data["reason"])
    _autonomy.validate_history(rm, states, done, extras)
    frozen = {sid: SliceState(id=sid, **{k: _freeze(v) for k, v in value.items()})
              for sid, value in states.items()}
    return ProjectState(MappingProxyType(frozen), frozenset(done), len(events),
                        **{k: _freeze(v) for k, v in extras.items() if k != "run_authorities"})


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(value)
    return value


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _event_dict(event: Event) -> dict:
    output = {"schema": event.schema, "t": event.t, "ev": event.ev.value, "by": event.by}
    for key in ("slice", "milestone", "round"):
        if (value := getattr(event, key)) is not None:
            output[key] = value
    output.update(_thaw(event.data))
    return output


def append(
    path: Path,
    event: Event | Mapping[str, object],
    roadmap: Roadmap | None = None,
) -> None:
    if roadmap is not None:
        _need(roadmap.schema == ROADMAP_V2,
              "unsupported roadmap schema; use native /2 or pinned v4")
    raw = _event_dict(event) if isinstance(event, Event) else dict(event)
    _parse_event(raw)
    raise LedgerError("/2 ledger append is not enabled")


def _write_line(path, raw):
    line = json.dumps(raw, ensure_ascii=False, separators=(",", ":")) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        offset = stream.tell()
        try:
            stream.write(line.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            # Roll back only this uncommitted append while ownership is held.
            stream.truncate(offset)
            stream.flush()
            os.fsync(stream.fileno())
            raise


def _append_operation(root, path, roadmap, raw, *, executable="git"):
    """Only adopted operation events; never a grant or public /2 writer."""
    from .ownership import writer_lock
    _need(roadmap.schema == ROADMAP_V2 and roadmap.autonomy is not None, "profile required")
    _need(raw.get("schema") == SCHEMA_V2 and raw.get("by") == "frutlups"
          and raw.get("ev") in ("attempt_started", "attempt_finished", "verified"),
          "not an internal operation event")
    _need(raw.get("status") != "resolved", "resolution requires external human authority")
    with writer_lock(root, roadmap, executable=executable):
        events = read(path)
        state = validate_history(root, events, roadmap, executable=executable)
        from . import commit
        commit.require_idle(root, events, roadmap,
                            allow_finish=raw.get("invocation") if raw["ev"] == "attempt_finished" else None,
                            executable=executable)
        unfinished = [key for key, attempt in state.attempts.items() if attempt["finish"] is None]
        _need(not unfinished or raw["ev"] == "attempt_finished"
              and unfinished == [raw.get("invocation")], "unresolved invocation")
        candidate = _parse_event(raw)
        if raw["ev"] == "attempt_finished":
            start = state.attempts[raw["invocation"]]["start"]
            if start["role"] == "verification":
                from . import receipt
                refs = raw.get("evidence", [])
                _need(len(refs) == 1, "verification finish requires immutable receipt")
                current = state.slices[start["scope"]]
                frozen = envelope(root, _thaw(current.envelope), current.id, current.round)
                value = read_reference(root, refs[0], 128 * 1024)
                receipt.validate_native(root, value, current, frozen)
                _need((raw["status"] == "completed") == value["ok"], "verification finish contradicts receipt")
                _need(not value["ok"] or raw["usage"]["secs"] <= start["allowance_seconds"],
                      "successful verification exceeded reservation")
        if raw["ev"] == "verified":
            last = events[-1] if events else None
            _need(last is not None and last.ev == Ev.attempt_finished,
                  "verification must immediately follow durable finish")
            start = state.attempts[last.data["invocation"]]["start"]
            _need(start["role"] == "verification" and start["scope"] == raw["slice"]
                  and start["round"] == raw["round"], "verification attempt mismatch")
            from . import receipt
            finish = state.attempts[last.data["invocation"]]["finish"]
            ref = dict(path=raw["receipt"], sha=raw["sha"])
            _need(_thaw(finish.get("evidence")) == [ref] and finish["status"] != "resolved",
                  "verified lacks exact settled receipt")
            current = state.slices[raw["slice"]]
            frozen = envelope(root, _thaw(current.envelope), current.id, current.round)
            value = read_reference(root, ref, 128 * 1024)
            receipt.validate_native(root, value, current, frozen)
            _need(raw["ok"] == value["ok"] == (finish["status"] == "completed"), "verified result mismatch")
        validate_history(root, (*events, candidate), roadmap, executable=executable)
        _write_line(path, raw)


def _append_review(root, path, roadmap, raw, prefix, *, executable="git"):
    """Private review writer; no approval, resolution or grant authority."""
    from . import commit, verdict
    from . import _loop_evidence as evidence
    from .ownership import writer_lock
    _need(roadmap.schema == ROADMAP_V2 and raw.get("schema") == SCHEMA_V2
          and raw.get("by") == "frutlups", "internal review requires /2")
    ev = raw.get("ev")
    _need(ev in ("attempt_started", "attempt_finished", "review_checkpoint", "reviewed", "holistic_reviewed", "artifact"),
          "not an internal review transition")
    _need(raw.get("status") != "resolved", "resolution requires external human authority")
    with writer_lock(root, roadmap, executable=executable):
        _need(path.read_bytes() == prefix, "ledger prefix changed before review transition")
        events = read(path)
        state = validate_history(root, events, roadmap, executable=executable)
        commit.require_idle(root, events, roadmap, executable=executable,
                            allow_finish=raw.get("invocation") if ev == "attempt_finished" else None)
        start = raw
        if ev in ("attempt_finished", "review_checkpoint"):
            attempt = state.attempts.get(raw.get("invocation"))
            _need(attempt is not None, "unknown review invocation")
            start = attempt["start"]
        sid = start.get("scope", raw.get("slice", raw.get("milestone")))
        current = state.slices.get(sid)
        from .roadmap import next_slice, next_milestone
        selected = next_slice(roadmap, state)
        milestone = next((m for m in roadmap.milestones if m.id == sid), None)
        holistic = milestone is not None
        if holistic:
            frontier = next_milestone(roadmap, state)
            _need(frontier is not None and frontier.id == sid and milestone.holistic_review
                  and all(state.slices[s.id].step == "accepted" for s in milestone.slices)
                  and state.holistic.get(sid, {}).get("step") not in ("blocked", "close_pending", "done")
                  and start.get("round", "holistic") == "holistic", "stale holistic scope/round")
            if roadmap.autonomy is not None:
                _need(state.run_head is not None, "holistic requires current grant")
                grant = state.runs[state.run_head["run"]]["admission"]
                _need(grant["milestone"] == sid and grant["stop"] == sid
                      and {s.id for s in milestone.slices} <= set(grant["slices"]),
                      "holistic outside current grant boundary")
        else:
            _need(current is not None and current.step == "reviewing" and selected is not None
                  and selected.id == sid and current.round == start.get("round"), "stale review scope/round")
        role = "holistic" if holistic else "reviewer"
        if ev in ("attempt_started", "attempt_finished", "review_checkpoint"):
            _need(start.get("role") == role, "review attempt role mismatch")
        if ev == "attempt_started":
            if raw.get("retry") == "initial":
                _need(not any(a["start"]["contract"] == raw.get("contract")
                              and a["start"]["role"] == role for a in state.attempts.values()),
                      "matching review invocation already exists")
            chain, parent = [], raw.get("parent")
            while parent:
                prior = state.attempts[parent]["start"]
                chain.append(prior)
                parent = prior.get("parent")
            _need(not any(row["retry"] == raw["retry"] for row in chain), "review retry allowance exhausted")
        if ev == "attempt_finished":
            refs = raw.get("evidence", [])
            _need(len(refs) == 2, "review finish requires original result and output evidence")
            value = read_reference(root, refs[0])
            text = evidence.bound_text(root, refs[1], 1024 * 1024)
            _need(value.get("invocation") == start["invocation"] and value.get("contract") == start["contract"]
                  and value.get("role") == role and value.get("output") == refs[1]
                  and (value.get("status") == "completed") == (raw["status"] == "completed"),
                  "review result binding mismatch")
            _need(isinstance(text, str), "invalid original review output")
        if ev == "artifact":
            _need(raw.get("role") in (("holistic_prompt", "holistic_report", "evidence") if holistic
                                      else ("review_prompt", "evidence")), "not a review artifact")
        if ev in ("review_checkpoint", "reviewed", "holistic_reviewed"):
            ref = dict(path=raw["report"], sha=raw["sha"])
            text = evidence.bound_text(root, ref, 1024 * 1024)
            parsed = (verdict.autonomous_holistic(text, milestone) if holistic
                      else verdict.autonomous_slice(text, sid, current.round))
            if ev == "review_checkpoint":
                _need(attempt["finish"] is not None and attempt["finish"]["status"] == "completed",
                      "checkpoint requires completed reviewer")
                refs = _thaw(attempt["finish"]).get("evidence", [])
                _need(len(refs) == 2 and ref == refs[1], "checkpoint differs from retained source")
                value = read_reference(root, refs[0])
                _need(value.get("seat") == raw.get("seat"), "checkpoint seat mismatch")
            elif ev == "holistic_reviewed":
                _need(holistic and raw["verdict"] == parsed.verdict
                      and raw["open"] == list(verdict.open_blocking(parsed)), "holistic decision differs from report")
                source = next((c for c in reversed(list(state.checkpoints.values()))
                               if c["scope"] == sid and c["round"] == "holistic"
                               and c["report"] == ref["path"] and c["sha"] == ref["sha"]), None)
                _need(source is not None and source["seat"] == "holistic", "holistic decision lacks source checkpoint")
                _need(events[-1].ev == Ev.artifact and events[-1].data.get("role") == "holistic_report"
                      and events[-1].data.get("path") == ref["path"] and events[-1].data.get("sha") == ref["sha"],
                      "holistic decision lacks original report artifact")
            else:
                _need(not holistic, "slice decision requires slice scope")
                _need(raw["verdict"] == parsed.verdict and raw["open"] == list(verdict.open_blocking(parsed)),
                      "reviewed differs from report")
                last = events[-1]
                _need(last.ev == Ev.artifact and last.data.get("role") == "evidence",
                      "reviewed requires bound merge proof")
                proof = read_reference(root, dict(path=last.data["path"], sha=last.data["sha"]))
                _need(proof.get("report") == ref and proof.get("sources"), "missing merge sources")
                sources = [state.checkpoints.get(i) for i in proof["sources"]]
                _need(all(s and s["scope"] == sid and s["round"] == current.round for s in sources),
                      "invalid merge checkpoints")
                names = list(roadmap.review_routing[selected.risk])
                _need([s["seat"] for s in sources] == names and raw.get("seat") == ",".join(names),
                      "merge route mismatch")
                reviews = []
                for source in sources:
                    body = evidence.bound_text(root, dict(path=source["report"], sha=source["sha"]), 1024 * 1024)
                    reviews.append((verdict.autonomous_slice(body, sid, current.round), body))
                _need(text == evidence.combined_review(reviews, seat_names=names, native=True,
                      prior_ids=evidence.prior_finding_ids(root, current, native=True)), "merge lost source findings")
        candidate = _parse_event(raw)
        validate_history(root, (*events, candidate), roadmap, executable=executable)
        _need(path.read_bytes() == prefix, "ledger prefix changed during review validation")
        _write_line(path, raw)
        if ev in ("reviewed", "holistic_reviewed"):
            from . import findings
            findings.reconcile(root, (*events, candidate))


def _append_coding(root, path, roadmap, raw, prefix, *, executable="git", invocation=None):
    """Only prompt/coded/evidence transitions under the caller's exact prefix."""
    from . import commit
    from .ownership import writer_lock
    _need(roadmap.schema == ROADMAP_V2 and raw.get("schema") == SCHEMA_V2
          and raw.get("by") == "frutlups", "internal coding requires /2")
    _need(raw.get("ev") in ("prompt", "coded") or
          raw.get("ev") == "artifact" and raw.get("role") == "evidence",
          "not an internal coding transition")
    with writer_lock(root, roadmap, executable=executable):
        _need(path.read_bytes() == prefix, "ledger prefix changed before coding transition")
        events = read(path)
        state = validate_history(root, events, roadmap, executable=executable)
        commit.require_idle(root, events, roadmap, executable=executable)
        sid = raw.get("slice", raw.get("scope"))
        current = state.slices.get(sid)
        _need(current is not None and current.round == raw.get("round"), "stale coding scope/round")
        if raw["ev"] == "coded":
            _need(invocation is not None and "outcome" in raw, "coded requires completed invocation")
            value = outcome(root, raw["outcome"], sid, current.round)
            validate_outcome(root, value, sid, current.round, invocation=invocation)
            last = events[-1] if events else None
            _need(last is not None and last.ev == Ev.artifact and last.data.get("role") == "evidence"
                  and last.data["scope"] == sid and last.round == current.round,
                  "coded requires retained dispatch evidence")
            dispatch = read_reference(root, {"path": last.data["path"], "sha": last.data["sha"]}, 16384)
            _need(dispatch.get("invocation") == invocation and dispatch.get("status") == "completed"
                  and dispatch.get("notes_sha") == raw.get("notes_sha")
                  and dispatch.get("envelope") == _thaw(current.envelope), "coded dispatch binding mismatch")
            if roadmap.autonomy is not None:
                attempt = state.attempts.get(invocation)
                _need(attempt is not None and attempt["finish"] is not None
                      and attempt["finish"]["status"] == "completed"
                      and attempt["start"]["role"] == "coder"
                      and attempt["start"]["scope"] == sid
                      and attempt["start"]["round"] == current.round,
                      "outcome requires the completed coder attempt")
        elif raw["ev"] == "artifact":
            _need(current.step in ("coding", "verifying", "blocked"), "evidence outside coding transition")
        candidate = _parse_event(raw)
        validate_history(root, (*events, candidate), roadmap, executable=executable)
        _need(path.read_bytes() == prefix, "ledger prefix changed during coding validation")
        _write_line(path, raw)


def _append_approval(root, roadmap, raw, *, executable="git"):
    """Bounded lifecycle write; no general /2 append or grant capability."""
    from . import commit
    from .ownership import writer_lock
    _need(roadmap.schema == ROADMAP_V2 and raw.get("schema") == SCHEMA_V2,
          "internal approval requires /2")
    _need(raw.get("by") == "frutlups" and raw.get("ev") in ("accepted", "milestone_done"),
          "not an internal approval event")
    with writer_lock(root, roadmap, executable=executable):
        path = root / commit.LEDGER
        events = read(path)
        commit.require_idle(root, events, roadmap, executable=executable)
        candidate = _parse_event(raw)
        validate_history(root, (*events, candidate), roadmap, executable=executable)
        if "commit_intent" in raw:
            intent = commit._intent(raw["commit_intent"])
            manifest_data = commit._read(repo_path(root, intent["manifest"]["path"]))
            manifest_value = commit._manifest(manifest_data, intent, raw.get("slice", raw.get("milestone")))
            _need(manifest_value["ledger"]["prefix_sha"] == evidence_sha(commit._read(path)),
                  "ledger changed before approval")
            _need(commit._head(root, executable=executable) == intent["parent"], "HEAD changed before approval")
        _write_line(path, raw)


def evidence_sha(data: bytes) -> str:
    if b"\0" not in data:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def _file_sha(path: Path) -> str:
    return evidence_sha(path.read_bytes())


def _head_sha(root: Path, relative: str, *, executable="git") -> str | None:
    from . import gitws
    result = gitws._git(root, "show", f"HEAD:{relative}", executable=executable, check=False)
    return evidence_sha(result.stdout) if result.returncode == 0 else None


def _matches_head(root: Path, relative: str, *, executable="git") -> bool:
    path = repo_path(root, relative, error=LedgerError)
    digest = _head_sha(root, relative, executable=executable)
    if path.is_symlink():
        return False
    if digest is None:
        return not path.exists()
    return path.is_file() and _file_sha(path) == digest


def check(path: Path, root: Path, *, executable="git") -> tuple[Drift, ...]:
    events, errors = read(path), []
    for event in events:
        for key in ("path", "receipt", "report"):
            if key in event.data:
                relative = event.data[key]
                target = repo_path(root, relative, error=LedgerError)
                changed = (
                    target.is_symlink() or not target.is_file()
                    or _file_sha(target) != event.data["sha"]
                )
                if changed:
                    errors.append(Drift(relative))
        if event.data.get("notes_path"):
            relative = event.data["notes_path"]
            target = repo_path(root, relative, error=LedgerError)
            if target.is_symlink() or not target.is_file():
                errors.append(Drift(relative, "missing"))
    latest = {}
    for event in events:
        if event.ev == Ev.coded:
            latest.update({item["path"]: item for item in event.data["changed"]})
    for relative, item in latest.items():
        target = repo_path(root, relative, error=LedgerError)
        matches = (
            item["kind"] == "deleted" and not target.exists() and not target.is_symlink()
            or item["kind"] != "deleted" and target.is_file() and not target.is_symlink()
            and _file_sha(target) == item["sha"]
        )
        if not matches and not _matches_head(root, relative, executable=executable):
            suffix = " was deleted" if item["kind"] == "deleted" else ""
            errors.append(Drift(relative + suffix))
    return tuple(errors)


def status_text(roadmap: Roadmap, state: ProjectState) -> str:
    lines = [
        f"{item.id} r{state.slices[item.id].round} {state.slices[item.id].step}"
        for milestone in roadmap.milestones for item in milestone.slices
    ]
    from .roadmap import next_slice

    if not any(m.status == "active" for m in roadmap.milestones):
        lines.append("no active milestone")
    following = next_slice(roadmap, state)
    return "\n".join(lines + [f"next: {following.id if following else 'none'}"])


# Base /2 grammar and pure transitions, adapted from template 169a74c.
EVENT_LIMIT = 2 * 1024 * 1024
ROADMAP_V2 = "frutlups.roadmap/2"
RESULTS = {"implemented", "blocked_authority", "blocked_environment"}
V2_EXTRA = {
    "prompt": {"envelope"},
    "coded": {"manifest", "result", "notes_sha", "outcome"},
    "accepted": {"commit_intent"},
    "milestone_done": {"commit_intent"},
}
V2_FIELDS = {
    "resolved": {"scope", "reason", "authority"},
    "commit_cancelled": {"operation", "reason", "retain_acceptance"},
    "holistic_reviewed": {"milestone", "report", "sha", "verdict", "open"},
    "review_checkpoint": {"invocation", "scope", "round", "contract", "report", "sha", "seat"},
    "attempt_started": {
        "invocation",
        "scope",
        "round",
        "role",
        "contract",
        "allowance_seconds",
        "retry",
        "parent",
    },
    "attempt_finished": {"invocation", "status", "usage", "reason", "evidence"},
}
V2_OPTIONAL = {"attempt_started": {"parent"}, "attempt_finished": {"reason", "evidence"}}


def digest(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))


def identifier(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value))


def scope(value):
    return isinstance(value, str) and bool(re.fullmatch(r"M\d{3}(?:-S\d{2})?", value))


def reference(value):
    _need(isinstance(value, dict) and set(value) == {"path", "sha"}, "invalid evidence reference")
    safe_rel(value["path"])
    _need(digest(value["sha"]), "invalid evidence reference hash")
    return value


def references(values):
    _need(isinstance(values, list), "evidence references must be a list")
    for value in values:
        reference(value)


def validate_v2(event):
    _need(event.get("schema") == SCHEMA_V2, "unknown ledger schema")
    _need(len(json.dumps(event).encode()) <= EVENT_LIMIT, "ledger event exceeds 2 MiB")
    ev = event.get("ev")
    _need(isinstance(ev, str), "event name must be text")
    _need(event.get("by") in ("human", "architect", "frutlups"), "invalid actor")
    _need(isinstance(event.get("t"), str), "invalid timestamp")
    _need(
        bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", event["t"])),
        "timestamp must be canonical UTC seconds",
    )
    datetime.fromisoformat(event["t"].replace("Z", "+00:00"))
    _need(ev != "unblocked", "/2 resolution requires an authority-bound resolved event")
    if ev in _autonomy.EVENTS:
        _autonomy.validate(event)
    elif ev in V2_FIELDS:
        keys = set(event) - {"schema", "t", "ev", "by"}
        required = V2_FIELDS[ev] - V2_OPTIONAL.get(ev, set())
        _need(required <= keys <= V2_FIELDS[ev], f"invalid {ev} fields")
    else:
        base = {key: value for key, value in event.items() if key not in V2_EXTRA.get(ev, set())}
        if ev == "artifact" and event.get("role") == "evidence":
            _need(
                set(base) == {"schema", "t", "ev", "by", "scope", "round", "role", "path", "sha"},
                "invalid evidence artifact fields",
            )
            reference({"path": event["path"], "sha": event["sha"]})
            _need(scope(event["scope"]), "invalid evidence scope")
            _need(
                ("-S" in event["scope"] and type(event["round"]) is int and event["round"] > 0)
                or ("-S" not in event["scope"] and event["round"] == "holistic"),
                "evidence scope/round mismatch",
            )
        else:
            _validate_base(base)
    for key in ("scope", "milestone"):
        if key in event:
            _need(scope(event[key]), f"invalid {key}")
    for key in ("reason", "seat"):
        if key in event:
            _need(isinstance(event[key], str) and bool(event[key].strip()), f"invalid {key}")
    for key in ("invocation", "operation", "parent"):
        if key in event:
            _need(identifier(event[key]), f"invalid {key}")
    for key in ("contract", "sha", "notes_sha"):
        if key in event:
            _need(digest(event[key]), f"invalid {key}")
    for key in ("envelope", "manifest", "outcome"):
        if key in event:
            reference(event[key])
    if ev == "prompt":
        _need("envelope" in event, "/2 prompt requires a frozen envelope")
    if ev == "coded":
        _need(
            "manifest" in event and event.get("result") in RESULTS, "/2 coded needs manifest/result"
        )
        _need(("notes_path" in event) == ("notes_sha" in event), "notes require a hash")
        _need(event["result"] == "implemented" or "outcome" in event, "blocker requires outcome")
    if "commit_intent" in event:
        intent = event["commit_intent"]
        _need(
            isinstance(intent, dict) and set(intent) == {"id", "parent", "manifest", "message"},
            "invalid commit intent",
        )
        _need(bool(re.fullmatch(r"[0-9a-f]{32}", str(intent["id"]))), "invalid commit operation id")
        _need(
            bool(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", str(intent["parent"]))),
            "invalid parent",
        )
        _need(
            isinstance(intent["message"], str) and bool(intent["message"].strip()),
            "invalid message",
        )
        reference(intent["manifest"])
    if ev == "resolved":
        _need(event["by"] in ("human", "architect"), "resolution requires human or architect")
        references(event["authority"])
        _need(bool(event["authority"]), "resolution needs a bound authority/environment reference")
    if ev == "commit_cancelled":
        _need(event["by"] in ("human", "architect"), "cancellation requires human or architect")
        _need(type(event["retain_acceptance"]) is bool, "retain_acceptance must be boolean")
        _need(
            not event["retain_acceptance"] or event["by"] == "human",
            "only human retains acceptance",
        )
    if ev == "reopened":
        _need(event["by"] in ("human", "architect"), "explicit reopen requires human or architect")
    if ev in ("holistic_reviewed", "review_checkpoint"):
        safe_rel(event["report"])
    if ev == "holistic_reviewed":
        _need(event["verdict"] in ("pass", "needs_work", "blocked", "override"), "invalid verdict")
        _need(event["verdict"] != "override" or event["by"] == "human", "override requires human")
        _need(
            isinstance(event["open"], list) and all(identifier(x) for x in event["open"]),
            "invalid open findings",
        )
        _need(
            event["verdict"] not in ("pass", "override") or not event["open"],
            "pass has open findings",
        )
    if ev in ("attempt_started", "review_checkpoint"):
        _need(
            event["round"] == "holistic" or type(event["round"]) is int and event["round"] > 0,
            "invalid round",
        )
    if ev == "attempt_started":
        _need(
            event["role"] in ("coder", "reviewer", "holistic", "probe", "verification"),
            "invalid attempt role",
        )
        _need(event["retry"] in ("initial", "transport", "format"), "invalid retry")
        _need(
            type(event["allowance_seconds"]) is int and event["allowance_seconds"] > 0,
            "invalid allowance",
        )
        _need((event["retry"] == "initial") == ("parent" not in event), "retry requires parent")
        if event["role"] != "probe":
            holistic = event["role"] == "holistic"
            _need(
                holistic == (event["round"] == "holistic")
                and holistic == ("-S" not in event["scope"]),
                "attempt role/scope mismatch",
            )
    if ev == "attempt_finished":
        _need(event["status"] in ("completed", "failed", "resolved"), "invalid attempt status")
        usage = event["usage"]
        _need(
            isinstance(usage, dict)
            and set(usage) <= {"secs", "tokens_in", "tokens_out", "cost_usd", "completeness"},
            "invalid usage fields",
        )
        _need(
            usage.get("completeness") in ("complete", "partial", "unknown"), "invalid completeness"
        )
        for key, value in usage.items():
            if key == "completeness":
                continue
            _need(type(value) in (int, float) and 0 <= value < float("inf"), "invalid usage value")
            if key.startswith("tokens_"):
                _need(type(value) is int, "tokens must be integer")
        if "evidence" in event:
            references(event["evidence"])
        if event["status"] == "resolved":
            _need(
                event["by"] in ("human", "architect")
                and event.get("reason")
                and event.get("evidence"),
                "unknown attempt needs explicit human/architect attribution",
            )
    return event


def apply_v2(event, states, milestones, done, extras):
    """Apply native special transitions before the shared base fold."""
    ev = event["ev"]
    _need(
        event.get("schema") != SCHEMA_V2 or ev != "unblocked",
        "/2 resolution requires an authority-bound resolved event",
    )
    attempts = extras["attempts"]
    holistic = extras["holistic"]
    if ev == "attempt_started":
        _need(event["invocation"] not in attempts, "duplicate invocation")
        _need(not any(x.get("finish") is None for x in attempts.values()), "unresolved invocation")
        _need(event["scope"] in states or event["scope"] in milestones, "unknown attempt scope")
        if event.get("parent"):
            prior = attempts.get(event["parent"])
            _need(prior is not None and prior.get("finish") is not None, "unknown retry parent")
            _need(
                all(event[key] == prior["start"][key] for key in ("scope", "round", "contract")),
                "retry binding mismatch",
            )
            _need(
                event["retry"] != "transport" or event["role"] == prior["start"]["role"],
                "transport retry role mismatch",
            )
            if event["retry"] == "format":
                previous_role = prior["start"]["role"]
                expected_role = (
                    previous_role if previous_role in ("reviewer", "holistic") else "probe"
                )
                _need(event["role"] == expected_role, "format repair role must remain read-only")
            _need(
                not any(
                    x["start"].get("parent") == event["parent"]
                    and x["start"]["retry"] == event["retry"]
                    for x in attempts.values()
                ),
                "retry allowance already consumed",
            )
            _need(
                event["retry"] != "format" or event["role"] != "coder",
                "format repair must be read-only",
            )
        attempts[event["invocation"]] = {"start": event, "finish": None}
        return True
    if ev == "attempt_finished":
        prior = attempts.get(event["invocation"])
        _need(prior is not None and prior["finish"] is None, "attempt completion out of order")
        prior["finish"] = event
        return True
    if ev == "review_checkpoint":
        prior = attempts.get(event["invocation"])
        _need(
            prior and prior["finish"] and prior["finish"]["status"] == "completed",
            "checkpoint needs a completed invocation",
        )
        _need(prior["start"]["contract"] == event["contract"], "checkpoint contract mismatch")
        _need(
            prior["start"]["role"] in ("reviewer", "holistic")
            and all(prior["start"][key] == event[key] for key in ("scope", "round")),
            "checkpoint scope, round or role mismatch",
        )
        _need(event["invocation"] not in extras["checkpoints"], "duplicate checkpoint")
        extras["checkpoints"][event["invocation"]] = event
        return True
    if ev == "holistic_reviewed":
        mid = event["milestone"]
        _need(mid in milestones, "unknown holistic milestone")
        _need(milestones[mid]["holistic_review"], "milestone does not require holistic review")
        _need(
            mid not in done
            and all(states[x["id"]]["step"] == "accepted" for x in milestones[mid]["slices"]),
            "holistic review out of order",
        )
        _need(
            holistic.get(mid, {}).get("step") not in ("blocked", "close_pending"),
            "holistic decision pending",
        )
        verdict = event["verdict"]
        step = {
            "pass": "close_pending",
            "override": "close_pending",
            "blocked": "blocked",
            "needs_work": "fix",
        }[verdict]
        holistic[mid] = {"step": step, "event": event}
        groups = {}
        for finding in event["open"]:
            sid = next(
                (x["id"] for x in milestones[mid]["slices"] if finding.startswith(x["id"] + "-")),
                None,
            )
            _need(sid, "holistic finding must name its affected slice")
            groups.setdefault(sid, []).append(finding)
        if verdict == "needs_work":
            _need(groups, "needs_work holistic review needs affected findings")
            for sid, ids in groups.items():
                current = states[sid]
                current.update(
                    step="fix",
                    round=current["round"] + 1,
                    reopened=True,
                    open_findings=ids,
                    last_report=event["report"],
                    unblock_reason="Holistic findings: " + ", ".join(ids),
                )
        return True
    if ev == "resolved":
        sid = event["scope"]
        if sid in states:
            current = states[sid]
            _need(current["step"] == "blocked", "resolution requires blocked work")
            current.update(
                step="fix",
                round=current["round"] + 1,
                resolution=event["reason"],
                unblock_reason=event["reason"],
            )
        else:
            _need(sid in holistic and holistic[sid]["step"] == "blocked", "milestone is not blocked")
            holistic[sid]["step"] = "reviewing"
            holistic[sid]["resolution"] = event["reason"]
        return True
    if ev == "commit_cancelled":
        intent = extras["intents"].get(event["operation"])
        _need(
            intent is not None and event["operation"] not in extras["cancelled"],
            "unknown/cancelled intent",
        )
        extras["cancelled"].add(event["operation"])
        if not event["retain_acceptance"]:
            sid = intent.get("slice", intent.get("milestone"))
            if sid in states:
                current = states[sid]
                current.update(
                    step="fix",
                    round=current["round"] + 1,
                    reopened=True,
                    unblock_reason="Commit cancelled: " + event["reason"],
                )
                done.discard(sid.split("-")[0])
                if sid.split("-")[0] in holistic:
                    holistic[sid.split("-")[0]].update(
                        step="reviewing", resolution="Commit cancelled: " + event["reason"]
                    )
            else:
                done.discard(sid)
                holistic[sid] = {"step": "reviewing", "resolution": event["reason"]}
        return True
    if ev == "artifact" and event.get("role") == "evidence":
        _need(event["scope"] in states or event["scope"] in milestones, "unknown artifact scope")
        return True
    if event.get("commit_intent"):
        identity = event["commit_intent"]["id"]
        _need(identity not in extras["intents"], "duplicate commit operation")
        extras["intents"][identity] = event
    return False


OBSERVATIONS = {"process", "in_process", "fresh_process", "clean_checkout", "inspection"}
ENVELOPE_FIELDS = {
    "schema",
    "slice",
    "round",
    "title",
    "objective",
    "acceptance",
    "non_goals",
    "read_first",
    "allowed_prefixes",
    "forbidden",
    "focused",
    "full",
    "runtime",
    "timeout_seconds",
    "observation",
    "notes",
    "findings",
    "memory",
}


def _evidence_strings(value, label, required=False):
    _need(
        isinstance(value, list)
        and (not required or bool(value))
        and all(
            isinstance(item, str) and bool(item.strip()) and "\0" not in item for item in value
        ),
        "invalid " + label,
    )


def envelope(root, ref, sid, round_no):
    value = read_reference(root, ref, 128 * 1024)
    _need(isinstance(value, dict) and set(value) == ENVELOPE_FIELDS, "invalid envelope fields")
    _need(
        value["schema"] == "frutlups.envelope/2"
        and value["slice"] == sid
        and value["round"] == round_no,
        "envelope identity mismatch",
    )
    for key in ("title", "objective", "notes", "findings", "memory"):
        _need(isinstance(value[key], str), "invalid envelope " + key)
    _need(value["title"].strip() and value["objective"].strip(), "empty envelope objective/title")
    for key in ("acceptance", "non_goals", "read_first", "allowed_prefixes", "forbidden", "full"):
        _evidence_strings(value[key], "envelope " + key, key in ("acceptance", "full"))
    for key in ("read_first", "allowed_prefixes", "forbidden"):
        for rel in value[key]:
            safe_rel(rel, directory=key != "read_first" and rel.endswith("/"))
    _need(isinstance(value["focused"], list), "invalid envelope focused commands")
    for argv in value["focused"]:
        _evidence_strings(argv, "focused argv", True)
    runtime = value["runtime"]
    _need(isinstance(runtime, dict) and set(runtime) <= {"python"}, "invalid envelope runtime")
    if runtime:
        _need(
            isinstance(runtime["python"], str)
            and re.fullmatch(r"3\.\d+(?:\.\d+)?", runtime["python"]),
            "invalid envelope Python version",
        )
    _need(
        type(value["timeout_seconds"]) is int and value["timeout_seconds"] > 0,
        "invalid envelope timeout",
    )
    _need(value["observation"] in OBSERVATIONS, "invalid envelope observation")
    return value



def outcome(root, ref, sid, round_no):
    value = read_reference(root, ref, 16 * 1024)
    return validate_outcome(root, value, sid, round_no)


def validate_outcome(root, value, sid, round_no, *, invocation=None):
    """The same grammar for a retained reference and a just-completed coder."""
    fields = {
        "schema",
        "slice",
        "round",
        "invocation",
        "outcome",
        "requirement",
        "reason",
        "actor",
        "action",
        "remaining",
        "evidence",
        "authority",
    }
    _need(isinstance(value, dict) and set(value) == fields, "invalid outcome fields")
    _need(
        value["schema"] == "frutlups.outcome/2"
        and value["slice"] == sid
        and type(value["round"]) is int
        and value["round"] == round_no,
        "outcome identity mismatch",
    )
    _need(identifier(value["invocation"]) and isinstance(value["outcome"], str)
          and value["outcome"] in RESULTS, "invalid outcome")
    _need(invocation is None or value["invocation"] == invocation,
          "outcome invocation differs from completed coder")
    for key in ("requirement", "reason", "actor", "action"):
        _need(isinstance(value[key], str), f"invalid outcome {key}")
        if key in ("requirement", "reason") or value["outcome"] != "implemented":
            _need(bool(value[key].strip()), f"outcome needs {key}")
    _need(
        isinstance(value["remaining"], list)
        and all(isinstance(x, str) and x for x in value["remaining"]),
        "invalid remaining work",
    )
    references(value["evidence"])
    _need(isinstance(value["authority"], list), "invalid authority list")
    for path in value["authority"]:
        safe_rel(path)
    for item in value["evidence"]:
        _check_reference(root, item)
    return value


def manifest(root, ref):
    value = read_reference(root, ref)
    _need(
        isinstance(value, dict) and set(value) == {"schema", "slice", "round", "changed"},
        "invalid manifest fields",
    )
    _need(
        value["schema"] == "frutlups.manifest/2"
        and scope(value["slice"])
        and type(value["round"]) is int
        and value["round"] > 0,
        "invalid manifest identity",
    )
    _need(isinstance(value["changed"], list), "invalid manifest changes")
    seen = set()
    for row in value["changed"]:
        _need(
            isinstance(row, dict) and set(row) == {"path", "sha", "kind", "round"},
            "invalid manifest row",
        )
        safe_rel(row["path"])
        _need(
            row["path"] not in seen
            and digest(row["sha"])
            and row["kind"] in ("added", "modified", "deleted", "renamed")
            and type(row["round"]) is int
            and 1 <= row["round"] <= value["round"],
            "invalid manifest row identity",
        )
        seen.add(row["path"])
    return value



def _regular_reference(root, ref):
    reference(ref)
    root = Path(root).resolve()
    path = repo_path(root, ref["path"], error=LedgerError)
    for ancestor in (path, *path.parents):
        if ancestor == root:
            break
        _need(not ancestor.is_symlink() and not ancestor.is_junction(),
              "symlink evidence refused: " + ref["path"])
    _need(path.is_file(), "missing regular evidence: " + ref["path"])
    return path


def _file_key(path):
    value = path.stat()
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def _check_reference(root, ref):
    """Rehash every reference on every call, with bounded working memory."""
    path = _regular_reference(root, ref)
    identity = _file_key(path)
    with path.open("rb") as stream:
        binary = any(b"\0" in chunk for chunk in iter(lambda: stream.read(65536), b""))
    sha, pending = hashlib.sha256(), b""
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            if binary:
                sha.update(chunk)
                continue
            data = pending + chunk
            data, pending = (data[:-1], b"\r") if data.endswith(b"\r") else (data, b"")
            sha.update(data.replace(b"\r\n", b"\n"))
    sha.update(pending)
    _need(sha.hexdigest() == ref["sha"] and _file_key(path) == identity,
          "immutable evidence drift: " + ref["path"])


def read_reference(root, ref, limit=2 * 1024 * 1024):
    path = _regular_reference(root, ref)
    identity = _file_key(path)
    _need(path.stat().st_size <= limit, "evidence exceeds read bound: " + ref["path"])
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    _need(len(data) <= limit, "evidence exceeds read bound: " + ref["path"])
    _need(evidence_sha(data) == ref["sha"] and _file_key(path) == identity,
          "immutable evidence drift: " + ref["path"])
    return json.loads(data.decode("utf-8"))


def validate_history(root, events, roadmap, *, executable="git"):
    """Validate all historical bindings without treating old products as current."""
    events = tuple(events)
    state = fold(events, roadmap)
    from . import findings, verdict
    projection = findings.Projection(root)
    current_open = {}
    coded, cumulative = {}, {}
    for event in events:
        row = _event_dict(event)
        try:
            if event.ev == Ev.coded:
                cumulative.setdefault(event.slice, {}).update(
                    {item["path"]: {**item, "round": event.round} for item in row["changed"]})
            for key in ("path", "receipt", "report"):
                if key in row:
                    _check_reference(root, {"path": row[key], "sha": row["sha"]})
            for ref in row.get("authority", []) + row.get("evidence", []):
                _check_reference(root, ref)
            if "notes_sha" in row:
                _check_reference(root, {"path": row["notes_path"], "sha": row["notes_sha"]})
            elif "notes_path" in row:
                _regular_reference(root, {"path": row["notes_path"], "sha": "0" * 64})
            if "envelope" in row:
                envelope(root, row["envelope"], event.slice, event.round)
            if "manifest" in row:
                value = manifest(root, row["manifest"])
                _need(value["slice"] == event.slice and value["round"] == event.round,
                      "coded manifest identity mismatch")
                _need(value["changed"] == [cumulative[event.slice][path]
                                           for path in sorted(cumulative[event.slice])],
                      "coded manifest is not the cumulative recorded change set")
                coded[event.slice, event.round] = row["manifest"]
            if "outcome" in row:
                value = outcome(root, row["outcome"], event.slice, event.round)
                _need(value["outcome"] == row["result"], "coded outcome mismatch")
            if event.ev == Ev.verified and event.schema == SCHEMA_V2:
                value = read_reference(root, {"path": row["receipt"], "sha": row["sha"]}, 128 * 1024)
                _need(isinstance(value, dict) and value.get("schema") == "frutlups.receipt/2"
                      and value.get("slice") == event.slice and value.get("round") == event.round,
                      "receipt identity mismatch")
                _need(value.get("manifest") == coded.get((event.slice, event.round))
                      and value.get("ok") is row["ok"], "receipt manifest or result mismatch")
            if event.schema == SCHEMA_V2 and event.ev in (
                    Ev.reviewed, Ev.holistic_reviewed, Ev.review_checkpoint):
                ref = dict(path=row["report"], sha=row["sha"])
                text, report = findings.read_report(root, ref)
                scope_id = row.get("slice", row.get("milestone", row.get("scope")))
                round_no = row.get("round", "holistic")
                round_no = None if round_no == "holistic" else round_no
                _need(report["identity"] == scope_id and report["round"] == round_no,
                      "recorded review identity mismatch")
                if round_no is None:
                    milestone = next(m for m in roadmap.milestones if m.id == scope_id)
                    prefixes = tuple(item.id + "-" for item in milestone.slices)
                    _need(all(item["severity"] == "P3" or item["id"].startswith(prefixes)
                              for item in report["findings"]),
                          "holistic finding must identify its exact affected slice")
                if event.by == "frutlups":
                    if round_no is None:
                        verdict.autonomous_holistic(text, milestone)
                    else:
                        verdict.autonomous_slice(text, scope_id, round_no)
                projection.add((ref["path"], ref["sha"]), row)
                if event.ev != Ev.review_checkpoint:
                    _need(report["verdict"] == row["verdict"] and report["open"] == row["open"],
                          "recorded review verdict/open findings mismatch")
                    projection.require_closure(report, {"open": current_open.get(scope_id, ())})
                    current_open[scope_id] = row["open"]
                    if event.ev == Ev.holistic_reviewed:
                        for milestone in roadmap.milestones:
                            if milestone.id == scope_id:
                                for item in milestone.slices:
                                    current_open[item.id] = [id for id in row["open"]
                                                             if id.startswith(item.id + "-")]
            elif event.schema == SCHEMA_V2 and event.ev == Ev.artifact and row.get("role") == "holistic_report":
                projection.add((row["path"], row["sha"]), row)
            if "commit_intent" in row:
                intent = row["commit_intent"]
                try:
                    value = read_reference(root, intent["manifest"], 64 * 1024 * 1024)
                except (ValueError, OSError) as original:
                    # Historical witnesses own their immutable manifest. Do not
                    # make later worktree contents a substitute for Git proof.
                    from . import commit
                    try:
                        witnessed = next((r for r in commit.status(root, events, roadmap, executable=executable)
                                          if r["id"] == intent["id"] and r["state"] == "completed"), None)
                        if witnessed is None:
                            raise original
                        value = json.loads(commit._at(root, commit._tree(root, witnessed["commit"], executable=executable),
                                                      intent["manifest"]["path"], executable=executable))
                    except (ValueError, OSError, subprocess.SubprocessError) as exc:
                        raise LedgerError("commit manifest drift without a valid historical witness") from exc
                _need(isinstance(value, dict) and value.get("schema") == "frutlups.commit-manifest/1"
                      and value.get("id") == intent["id"] and value.get("parent") == intent["parent"]
                      and value.get("scope") == (event.slice or event.milestone),
                      "commit manifest identity mismatch")
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise LedgerError(f"{event.ev}: {exc}") from exc
    return state
