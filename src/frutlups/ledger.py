"""Strict append-only ledger parsing, folding, and evidence checks."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType

from ._paths import repo_path, safe_rel
from .roadmap import Roadmap

SCHEMA = "frutlups.ledger/1"
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
    unblocked = "unblocked"
    milestone_done = "milestone_done"
    note = "note"
    stop = "stop"


FIELDS = {
    Ev.prompt: {"slice", "round", "path", "sha", "baseline"},
    Ev.artifact: {"scope", "round", "role", "path", "sha"},
    Ev.coded: {
        "slice", "round", "changed", "notes_path", "seat", "secs", "tokens_in",
        "tokens_out",
    },
    Ev.verified: {"slice", "round", "receipt", "sha", "ok"},
    Ev.reviewed: {
        "slice", "round", "report", "sha", "verdict", "open", "seat", "secs",
        "tokens_in", "tokens_out", "cost_usd",
    },
    Ev.accepted: {"slice", "round", "commit"},
    Ev.reopened: {"slice", "round", "reason"},
    Ev.unblocked: {"slice", "round", "reason"},
    Ev.milestone_done: {"milestone", "holistic_report"},
    Ev.note: {"text", "slice"},
    Ev.stop: {"reason", "detail"},
}
OPTIONAL = {
    Ev.prompt: {"baseline"},
    Ev.coded: {"notes_path", "seat", "secs", "tokens_in", "tokens_out"},
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


@dataclass(frozen=True)
class ProjectState:
    slices: Mapping[str, SliceState]
    milestones_done: frozenset[str]
    events: int


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
    try:
        ev = Ev(value.get("ev"))
    except (TypeError, ValueError) as exc:
        raise LedgerError(f"{line}: invalid schema or event") from exc
    _need(value.get("schema") == SCHEMA, f"{line}: invalid schema or event")
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
    valid = valid and (ev != Ev.unblocked or value["by"] in ("human", "architect"))
    _need(valid, f"{line}: invalid text, usage, or actor field")
    data = {key: item for key, item in value.items()
            if key not in COMMON | {"slice", "milestone", "round"}}
    if ev == Ev.coded:
        data["changed"] = _changes(value["changed"], line, "changed")
    if ev == Ev.prompt and "baseline" in value:
        data["baseline"] = _changes(value["baseline"], line, "baseline")
    if ev == Ev.reviewed:
        data["open"] = tuple(value["open"])
    return Event(value["t"], ev, value["by"], value.get("slice"), value.get("milestone"),
                 value.get("round"), MappingProxyType(data))


def read(path: Path) -> tuple[Event, ...]:
    if not path.exists():
        return ()
    events = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise LedgerError(f"cannot read {path.name}: {exc}") from exc
    for number, raw in enumerate(lines, 1):
        if not raw:
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LedgerError(f"line {number}: malformed JSON: {exc.msg}") from exc
        events.append(_parse_event(value, f"line {number}"))
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
    states, done = _mutable_state(roadmap), set()
    holistic_reports = {}
    milestones = {item.id: item for item in roadmap.milestones}
    for event in events:
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
            current["corrective_rounds_used"] += round_no > 1
        elif ev == Ev.coded:
            _need(current["step"] == "coding" and round_no == current["round"],
                  f"{sid}: coded out of order")
            current.update(step="verifying", changed=event.data["changed"],
                           notes_path=event.data.get("notes_path"))
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
        elif ev == Ev.unblocked:
            _need(current["step"] == "blocked" and round_no == current["round"] + 1,
                  f"{sid}: unblocked out of order")
            current.update(step="fix", round=round_no, unblock_reason=event.data["reason"])
    frozen = {sid: SliceState(id=sid, **value) for sid, value in states.items()}
    return ProjectState(MappingProxyType(frozen), frozenset(done), len(events))


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _event_dict(event: Event) -> dict:
    output = {"schema": SCHEMA, "t": event.t, "ev": event.ev.value, "by": event.by}
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
    if roadmap is None:
        from .roadmap import load

        roadmap = load(path.parent.parent / "roadmap.yaml")
    raw = _event_dict(event) if isinstance(event, Event) else dict(event)
    candidate = _parse_event(raw)
    fold((*read(path), candidate), roadmap)
    line = json.dumps(raw, ensure_ascii=False, separators=(",", ":")) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(line)
        stream.flush()
        os.fsync(stream.fileno())


def evidence_sha(data: bytes) -> str:
    if b"\0" not in data:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def _file_sha(path: Path) -> str:
    return evidence_sha(path.read_bytes())


def _head_sha(root: Path, relative: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(root), "show", f"HEAD:{relative}"], capture_output=True, check=False,
    )
    return evidence_sha(result.stdout) if result.returncode == 0 else None


def _matches_head(root: Path, relative: str) -> bool:
    path = repo_path(root, relative, error=LedgerError)
    digest = _head_sha(root, relative)
    if path.is_symlink():
        return False
    if digest is None:
        return not path.exists()
    return path.is_file() and _file_sha(path) == digest


def check(path: Path, root: Path) -> tuple[Drift, ...]:
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
        if not matches and not _matches_head(root, relative):
            suffix = " was deleted" if item["kind"] == "deleted" else ""
            errors.append(Drift(relative + suffix))
    return tuple(errors)


def status_text(roadmap: Roadmap, state: ProjectState) -> str:
    lines = [
        f"{item.id} r{state.slices[item.id].round} {state.slices[item.id].step}"
        for milestone in roadmap.milestones for item in milestone.slices
    ]
    from .roadmap import next_slice

    following = next_slice(roadmap, state)
    return "\n".join(lines + [f"next: {following.id if following else 'none'}"])
