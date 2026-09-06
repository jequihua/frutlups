"""Immutable roadmap model with template-compatible validation and rendering."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from ._paths import safe_rel

TOP = {
    "schema", "project", "brief", "verification", "allowed_prefixes", "forbidden",
    "review", "memory", "milestones", "ruled_out", "not_yet_specified",
}
MILESTONE_KEYS = {"id", "title", "status", "risk", "holistic_review", "slices"}
SLICE_KEYS = {
    "id", "title", "objective", "acceptance", "non_goals", "read_first",
    "allowed_prefixes", "focused", "verification", "risk", "kind", "memory_pages",
    "notes",
}
MEMORY_KEYS = {"kind", "root", "manual", "read_verbs", "read_first_pages"}
MID, SID = re.compile(r"M\d{3}$"), re.compile(r"M\d{3}-S\d{2}$")


class RoadmapError(ValueError):
    def __init__(self, errors: list[str] | tuple[str, ...]):
        self.errors = tuple(errors)
        super().__init__("\n".join(self.errors))


@dataclass(frozen=True)
class Slice:
    id: str; milestone_id: str; title: str; objective: str
    acceptance: tuple[str, ...]; non_goals: tuple[str, ...]; read_first: tuple[str, ...]
    allowed_prefixes: tuple[str, ...] | None; focused: tuple[tuple[str, ...], ...]
    verification: tuple[str, ...] | None; risk: str
    kind: Literal["code", "memory_update", "docs"]; memory_pages: tuple[str, ...]
    notes: str | None


@dataclass(frozen=True)
class Milestone:
    id: str; title: str; status: Literal["planned", "active"]; risk: str
    holistic_review: bool; slices: tuple[Slice, ...]


@dataclass(frozen=True)
class Memory:
    kind: Literal["llloom"]; root: str; manual: str
    read_verbs: tuple[str, ...]; read_first_pages: tuple[str, ...]


@dataclass(frozen=True)
class Roadmap:
    project: str; verification_full: tuple[str, ...]
    focused_default: tuple[tuple[str, ...], ...]; allowed_prefixes: tuple[str, ...]
    forbidden: tuple[str, ...]; review_routing: Mapping[str, tuple[str, ...]]
    memory: Memory | None; milestones: tuple[Milestone, ...]
    ruled_out: tuple[tuple[str, str], ...]
    not_yet_specified: tuple[tuple[str, str], ...]


def _map(value: object, allowed: set[str], where: str, errors: list[str]) -> bool:
    if not isinstance(value, dict):
        errors.append(f"{where} must be a mapping")
        return False
    errors += [f"{where}: unknown key {key!r}" for key in value if key not in allowed]
    return True


def _strings(
    value: object, where: str, errors: list[str], required: bool = False,
) -> list[str]:
    invalid = not isinstance(value, list) or required and not value
    invalid = invalid or isinstance(value, list) and any(
        not isinstance(item, str) or not item.strip() for item in value
    )
    if invalid:
        article = "a non-empty " if required else "a "
        errors.append(f"{where} must be {article}list of non-empty strings")
        return []
    return value


def _paths(
    value: object, where: str, errors: list[str], prefixes: bool = False,
) -> list[str]:
    out = _strings(value, where, errors)
    for item in out:
        try:
            safe_rel(item, directory=prefixes and item.endswith("/"))
        except ValueError as exc:
            errors.append(f"{where}: {exc}")
    return out


def _commands(
    value: object, where: str, errors: list[str], required: bool = False,
) -> None:
    if not isinstance(value, list) or required and not value:
        article = "a non-empty " if required else "a "
        errors.append(f"{where} must be {article}list of argv lists")
        return
    for index, command in enumerate(value):
        _strings(command, f"{where}[{index}]", errors, required=True)


def _validate(data: dict) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    ids: set[str] = set()
    _map(data, TOP, "roadmap", errors)
    if data.get("schema") != "frutlups.roadmap/1":
        errors.append("schema must be frutlups.roadmap/1")
    if not isinstance(data.get("project"), str) or not data.get("project", "").strip():
        errors.append("project must be a non-empty string")
    try:
        safe_rel(data.get("brief", ""), directory=True)
    except ValueError as exc:
        errors.append(f"brief: {exc}")
    verification = data.get("verification")
    if _map(verification, {"full", "focused_default"}, "verification", errors):
        _strings(verification.get("full"), "verification.full argv", errors, required=True)
        _commands(
            verification.get("focused_default", []), "verification.focused_default", errors,
        )
    defaults = _paths(data.get("allowed_prefixes"), "allowed_prefixes", errors, True)
    forbidden = _paths(data.get("forbidden"), "forbidden", errors, True)
    review = data.get("review")
    if _map(review, {"ordinary", "high", "release"}, "review", errors):
        for risk in ("ordinary", "high", "release"):
            seats = review.get(risk)
            value = [seats] if isinstance(seats, str) else seats
            _strings(value, f"review.{risk}", errors, True)
    memory, memory_root = data.get("memory"), None
    if memory is not None and _map(memory, MEMORY_KEYS, "memory", errors):
        if memory.get("kind") != "llloom":
            errors.append("memory.kind must be llloom")
        for key in ("root", "manual"):
            try:
                safe_rel(memory.get(key, ""), directory=key == "root")
            except ValueError as exc:
                errors.append(f"memory.{key}: {exc}")
        if isinstance(memory.get("root"), str):
            memory_root = memory.get("root", "").rstrip("/") + "/"
        _strings(memory.get("read_verbs"), "memory.read_verbs", errors, True)
        _paths(memory.get("read_first_pages"), "memory.read_first_pages", errors)
    milestones, active = data.get("milestones"), 0
    if not isinstance(milestones, list) or not milestones:
        errors.append("milestones must be a non-empty list")
        milestones = []
    for mi, milestone in enumerate(milestones):
        where = f"milestones[{mi}]"
        mid = milestone.get("id") if isinstance(milestone, dict) else None
        if not _map(milestone, MILESTONE_KEYS, where, errors):
            continue
        if not isinstance(mid, str) or not MID.fullmatch(mid):
            errors.append(f"{where}.id must match Mnnn")
        elif mid in ids:
            errors.append(f"duplicate id {mid}")
        else:
            ids.add(mid)
        if not isinstance(milestone.get("title"), str) or not milestone.get("title", "").strip():
            errors.append(f"{where}.title must be non-empty")
        if milestone.get("status") not in ("planned", "active"):
            errors.append(f"{where}.status must be planned or active")
        if milestone.get("risk") not in ("ordinary", "high", "release"):
            errors.append(f"{where}.risk is invalid")
        if not isinstance(milestone.get("holistic_review"), bool):
            errors.append(f"{where}.holistic_review must be boolean")
        items = milestone.get("slices")
        active += milestone.get("status") == "active"
        if not isinstance(items, list) or not items:
            errors.append(f"{where}.slices must be non-empty")
            continue
        for si, item in enumerate(items):
            sw = f"{where}.slices[{si}]"
            sid = item.get("id") if isinstance(item, dict) else None
            if not _map(item, SLICE_KEYS, sw, errors):
                continue
            invalid = not isinstance(sid, str) or not SID.fullmatch(sid)
            invalid = invalid or isinstance(mid, str) and not sid.startswith(mid + "-")
            if invalid:
                errors.append(f"{sw}.id must match {mid}-Snn")
            elif sid in ids:
                errors.append(f"duplicate id {sid}")
            else:
                ids.add(sid)
            errors += [
                f"{sw}.{key} must be non-empty"
                for key in ("title", "objective")
                if not isinstance(item.get(key), str) or not item.get(key, "").strip()
            ]
            _strings(item.get("acceptance"), f"{sw}.acceptance", errors, True)
            _strings(item.get("non_goals", []), f"{sw}.non_goals", errors)
            _paths(item.get("read_first", []), f"{sw}.read_first", errors)
            effective = item.get("allowed_prefixes", defaults)
            kind = item.get("kind", "code")
            _paths(effective, f"{sw}.allowed_prefixes", errors, True)
            if kind not in ("code", "memory_update", "docs"):
                errors.append(f"{sw}.kind is invalid")
            for prefix in effective if isinstance(effective, list) else []:
                broadens = defaults and not any(prefix.startswith(base) for base in defaults)
                if kind != "memory_update" and broadens:
                    errors.append(f"{sw}.allowed_prefixes broadens project defaults: {prefix}")
                if any(prefix.startswith(path) or path.startswith(prefix) for path in forbidden):
                    errors.append(f"{sw}.allowed_prefixes overlaps forbidden path: {prefix}")
            if kind == "memory_update" and (not memory_root or memory_root not in effective):
                errors.append(f"{sw}: memory_update must allow the memory root")
            overlaps = memory_root and any(
                prefix.startswith(memory_root) or memory_root.startswith(prefix)
                for prefix in effective
            )
            if kind == "code" and overlaps:
                errors.append(f"{sw}: code slice may not allow the memory root")
            _commands(item.get("focused", []), f"{sw}.focused", errors)
            if "verification" in item:
                _strings(item["verification"], f"{sw}.verification argv", errors, True)
            risk = item.get("risk", milestone.get("risk"))
            if risk not in ("ordinary", "high", "release"):
                errors.append(f"{sw}.risk is invalid")
            if "notes" in item and not isinstance(item["notes"], str):
                errors.append(f"{sw}.notes must be a string")
            if item.get("memory_pages") and memory is None:
                errors.append(f"{sw}.memory_pages requires a memory block")
            _paths(item.get("memory_pages", []), f"{sw}.memory_pages", errors)
            if len(yaml.safe_dump(item, sort_keys=False).encode()) > 2048:
                warnings.append(f"{sid}: slice entry exceeds 2 KB")
    if milestones and not active:
        errors.append("at least one milestone must be active")
    for key, prefix in (("ruled_out", "R"), ("not_yet_specified", "N")):
        rows = data.get(key, [])
        if not isinstance(rows, list):
            errors.append(f"{key} must be a list")
            continue
        for row in rows:
            valid = isinstance(row, dict) and set(row) == {"id", "text"}
            valid = valid and bool(re.fullmatch(prefix + r"\d+", str(row.get("id", ""))))
            valid = valid and bool(str(row.get("text", "")).strip())
            if not valid:
                errors.append(f"{key} entries require {prefix}<number> id and text")
    return errors, warnings


def load(path: Path) -> Roadmap:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise RoadmapError([f"cannot load {path.name}: {exc}"]) from exc
    if not isinstance(data, dict):
        raise RoadmapError([f"{path.name} must contain a YAML mapping"])
    errors, _ = _validate(data)
    if errors:
        raise RoadmapError(errors)
    memory = data.get("memory")
    memory_model = (
        Memory(
            memory["kind"], memory["root"], memory["manual"],
            tuple(memory["read_verbs"]), tuple(memory["read_first_pages"]),
        )
        if memory else None
    )
    milestones = tuple(
        Milestone(
            model["id"], model["title"], model["status"], model["risk"],
            model["holistic_review"],
            tuple(
                Slice(
                    item["id"], model["id"], item["title"], item["objective"],
                    tuple(item["acceptance"]), tuple(item.get("non_goals", [])),
                    tuple(item.get("read_first", [])),
                    tuple(item["allowed_prefixes"]) if "allowed_prefixes" in item else None,
                    tuple(tuple(command) for command in item.get("focused", [])),
                    tuple(item["verification"]) if "verification" in item else None,
                    item.get("risk", model["risk"]), item.get("kind", "code"),
                    tuple(item.get("memory_pages", [])), item.get("notes"),
                )
                for item in model["slices"]
            ),
        )
        for model in data["milestones"]
    )
    review = {
        key: tuple([value] if isinstance(value, str) else value)
        for key, value in data["review"].items()
    }
    horizons = lambda key: tuple((row["id"], row["text"]) for row in data.get(key, []))
    return Roadmap(
        data["project"], tuple(data["verification"]["full"]),
        tuple(tuple(command) for command in data["verification"].get("focused_default", [])),
        tuple(data["allowed_prefixes"]), tuple(data["forbidden"]), review, memory_model,
        milestones, horizons("ruled_out"), horizons("not_yet_specified"),
    )


def slice_by_id(roadmap: Roadmap, slice_id: str) -> Slice:
    for milestone in roadmap.milestones:
        for item in milestone.slices:
            if item.id == slice_id:
                return item
    raise RoadmapError([f"unknown slice: {slice_id}"])


def effective_prefixes(roadmap: Roadmap, item: Slice) -> tuple[str, ...]:
    prefixes = roadmap.allowed_prefixes if item.allowed_prefixes is None else item.allowed_prefixes
    return tuple(
        prefix for prefix in prefixes
        if not any(
            prefix.startswith(path) or path.startswith(prefix) for path in roadmap.forbidden
        )
    )


def next_slice(roadmap: Roadmap, state: object) -> Slice | None:
    states = getattr(state, "slices", {})
    step = lambda item: getattr(states.get(item.id), "step", "unstarted")
    for milestone in roadmap.milestones:
        for item in milestone.slices:
            if getattr(states.get(item.id), "reopened", False):
                return item
    active = next(
        (model for model in roadmap.milestones if model.status == "active"), None,
    )
    if active:
        return next((item for item in active.slices if step(item) != "accepted"), None)
    return None


def render_markdown(roadmap: Roadmap) -> str:
    lines = [
        "# Roadmap", "", "> Generated from `roadmap.yaml`; do not edit.", "",
        f"Project: `{roadmap.project}`", "",
    ]
    for milestone in roadmap.milestones:
        lines += [
            f"## {milestone.id} — {milestone.title}", "", f"Status: {milestone.status}",
            f"Risk: {milestone.risk}",
            f"Holistic review: {str(milestone.holistic_review).lower()}", "",
        ]
        for item in milestone.slices:
            lines += [
                f"### {item.id} — {item.title}", "", item.objective.strip(), "",
                "Acceptance:",
            ] + [f"- {value}" for value in item.acceptance]
            if item.non_goals:
                lines += ["", "Non-goals:"] + [f"- {value}" for value in item.non_goals]
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
