import copy
import shutil
import subprocess
import sys
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from frutlups.roadmap import (
    RoadmapError,
    effective_prefixes,
    load,
    next_slice,
    render_markdown,
    slice_by_id,
)

FIXTURE = Path(__file__).parent / "fixtures" / "v5_development"
ROADMAP = FIXTURE / "roadmap.yaml"
MEMORY = {
    "kind": "llloom",
    "root": "memory/",
    "manual": "docs/memory.md",
    "read_verbs": ["search"],
    "read_first_pages": ["overview"],
}


def _base() -> dict:
    return yaml.safe_load(ROADMAP.read_text(encoding="utf-8"))


def _target(data: object, path: tuple[object, ...]) -> object:
    for part in path:
        data = data[part]  # type: ignore[index]
    return data


def _set(path: tuple[object, ...], value: object):
    def mutate(data: dict) -> None:
        _target(data, path[:-1])[path[-1]] = copy.deepcopy(value)  # type: ignore[index]

    return mutate


def _delete(path: tuple[object, ...]):
    def mutate(data: dict) -> None:
        del _target(data, path[:-1])[path[-1]]  # type: ignore[index]

    return mutate


def _duplicate(path: tuple[object, ...]):
    def mutate(data: dict) -> None:
        items = _target(data, path)
        items.append(copy.deepcopy(items[0]))  # type: ignore[attr-defined,index]

    return mutate


def _combine(*mutations):
    def mutate(data: dict) -> None:
        for change in mutations:
            change(data)

    return mutate


M = ("milestones", 0)
S = M + ("slices", 0)


INVALID_CASES = [
    pytest.param(_set(("extra",), True), "roadmap: unknown key", id="top-unknown"),
    pytest.param(_set(("schema",), "wrong"), "unsupported roadmap schema", id="schema"),
    pytest.param(_set(("project",), ""), "project must be", id="project"),
    pytest.param(_set(("brief",), "../brief"), "brief: unsafe", id="brief-path"),
    pytest.param(
        _set(("verification",), []), "verification must be a mapping", id="verification-map"
    ),
    pytest.param(
        _set(("verification", "extra"), True),
        "verification: unknown key",
        id="verification-unknown",
    ),
    pytest.param(
        _set(("verification", "full"), []), "verification.full argv", id="verification-full"
    ),
    pytest.param(
        _set(("verification", "focused_default"), "bad"),
        "focused_default must be a list",
        id="focused-default-list",
    ),
    pytest.param(
        _set(("verification", "focused_default"), [[]]),
        "focused_default[0]",
        id="focused-default-command",
    ),
    pytest.param(
        _set(("allowed_prefixes",), None), "allowed_prefixes must be a list", id="allowed-list"
    ),
    pytest.param(
        _set(("allowed_prefixes",), ["../"]), "allowed_prefixes: unsafe", id="allowed-path"
    ),
    pytest.param(_set(("forbidden",), None), "forbidden must be a list", id="forbidden-list"),
    pytest.param(_set(("forbidden",), ["../"]), "forbidden: unsafe", id="forbidden-path"),
    pytest.param(_set(("review",), []), "review must be a mapping", id="review-map"),
    pytest.param(_set(("review", "extra"), []), "review: unknown key", id="review-unknown"),
    pytest.param(_set(("review", "ordinary"), []), "review.ordinary", id="review-seat"),
    pytest.param(_set(("memory",), []), "memory must be a mapping", id="memory-map"),
    pytest.param(
        _set(("memory",), {**MEMORY, "extra": True}), "memory: unknown key", id="memory-unknown"
    ),
    pytest.param(_set(("memory",), {**MEMORY, "kind": "other"}), "memory.kind", id="memory-kind"),
    pytest.param(
        _set(("memory",), {**MEMORY, "root": "../memory"}), "memory.root", id="memory-root"
    ),
    pytest.param(
        _set(("memory",), {**MEMORY, "manual": "../manual"}), "memory.manual", id="memory-manual"
    ),
    pytest.param(
        _set(("memory",), {**MEMORY, "read_verbs": []}), "memory.read_verbs", id="memory-verbs"
    ),
    pytest.param(
        _set(("memory",), {**MEMORY, "read_first_pages": ["../"]}),
        "memory.read_first_pages",
        id="memory-pages",
    ),
    pytest.param(_set(("milestones",), []), "milestones must be a non-empty list", id="milestones"),
    pytest.param(
        _set(("milestones", 0), []), "milestones[0] must be a mapping", id="milestone-map"
    ),
    pytest.param(_set(M + ("extra",), True), "milestones[0]: unknown key", id="milestone-unknown"),
    pytest.param(_set(M + ("id",), "bad"), ".id must match Mnnn", id="milestone-id"),
    pytest.param(_duplicate(("milestones",)), "duplicate id M001", id="milestone-duplicate"),
    pytest.param(_set(M + ("title",), ""), ".title must be non-empty", id="milestone-title"),
    pytest.param(
        _set(M + ("status",), "bad"),
        ".status must be planned, active, or done", id="milestone-status"
    ),
    pytest.param(_set(M + ("risk",), "bad"), ".risk is invalid", id="milestone-risk"),
    pytest.param(
        _set(M + ("holistic_review",), "yes"),
        ".holistic_review must be boolean",
        id="holistic-review",
    ),
    pytest.param(_set(M + ("slices",), []), ".slices must be non-empty", id="slices"),
    pytest.param(
        _set(("milestones", 0, "slices", 0), []), ".slices[0] must be a mapping", id="slice-map"
    ),
    pytest.param(_set(S + ("extra",), True), ".slices[0]: unknown key", id="slice-unknown"),
    pytest.param(_set(S + ("id",), "M999-S01"), ".id must match M001-Snn", id="slice-id"),
    pytest.param(_duplicate(M + ("slices",)), "duplicate id M001-S01", id="slice-duplicate"),
    pytest.param(_set(S + ("title",), ""), ".title must be non-empty", id="slice-title"),
    pytest.param(
        _set(S + ("objective",), ""), ".objective must be non-empty", id="slice-objective"
    ),
    pytest.param(_set(S + ("acceptance",), []), ".acceptance must be a non-empty", id="acceptance"),
    pytest.param(_set(S + ("non_goals",), "bad"), ".non_goals must be a list", id="non-goals"),
    pytest.param(_set(S + ("read_first",), ["../"]), ".read_first: unsafe", id="read-first"),
    pytest.param(
        _set(S + ("allowed_prefixes",), "bad"),
        ".allowed_prefixes must be a list",
        id="slice-allowed-list",
    ),
    pytest.param(
        _set(S + ("allowed_prefixes",), ["../"]),
        ".allowed_prefixes: unsafe",
        id="slice-allowed-path",
    ),
    pytest.param(_set(S + ("kind",), "bad"), ".kind is invalid", id="slice-kind"),
    pytest.param(
        _set(S + ("allowed_prefixes",), ["08_pkg/"]),
        "broadens project defaults",
        id="slice-broadens",
    ),
    pytest.param(
        _set(S + ("allowed_prefixes",), ["00_brief/"]),
        "overlaps forbidden path",
        id="slice-forbidden",
    ),
    pytest.param(
        _combine(_set(("memory",), MEMORY), _set(S + ("kind",), "memory_update")),
        "memory_update must allow",
        id="memory-update-root",
    ),
    pytest.param(
        _combine(
            _set(("memory",), {**MEMORY, "root": "07_app/memory/"}),
            _set(S + ("allowed_prefixes",), ["07_app/"]),
        ),
        "code slice may not allow",
        id="code-memory-root",
    ),
    pytest.param(_set(S + ("focused",), "bad"), ".focused must be a list", id="focused-list"),
    pytest.param(_set(S + ("focused",), [[]]), ".focused[0]", id="focused-command"),
    pytest.param(_set(S + ("verification",), []), ".verification argv", id="slice-verification"),
    pytest.param(_set(S + ("risk",), "bad"), ".risk is invalid", id="slice-risk"),
    pytest.param(_set(S + ("notes",), []), ".notes must be a string", id="slice-notes"),
    pytest.param(
        _set(S + ("memory_pages",), ["page"]),
        ".memory_pages requires a memory block",
        id="pages-need-memory",
    ),
    pytest.param(
        _combine(_set(("memory",), MEMORY), _set(S + ("memory_pages",), ["../"])),
        ".memory_pages: unsafe",
        id="slice-memory-page-path",
    ),
    pytest.param(_set(M + ("status",), "planned"), None, id="active-milestone"),
    pytest.param(_set(("ruled_out",), "bad"), "ruled_out must be a list", id="ruled-out-list"),
    pytest.param(
        _set(("ruled_out",), [{"id": "bad", "text": "x"}]),
        "ruled_out entries require",
        id="ruled-out-entry",
    ),
    pytest.param(
        _set(("not_yet_specified",), "bad"), "not_yet_specified must be a list", id="not-yet-list"
    ),
    pytest.param(
        _set(("not_yet_specified",), [{"id": "bad", "text": "x"}]),
        "not_yet_specified entries require",
        id="not-yet-entry",
    ),
]


@pytest.mark.parametrize(("mutate", "message"), INVALID_CASES)
def test_load_refuses_each_template_validation_error(tmp_path: Path, mutate, message: str) -> None:
    data = _base()
    mutate(data)
    path = tmp_path / "roadmap.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    if message is None:
        # Retain the original node; the accepted idle contract supersedes its refusal.
        model = load(path)
        assert tuple(m.status for m in model.milestones) == ("planned",)
        assert model.milestones[0].slices[0].id == "M001-S01"
    else:
        with pytest.raises(RoadmapError) as error:
            load(path)
        assert message in str(error.value)


def test_loads_fixture_roadmap_as_frozen_model() -> None:
    roadmap = load(ROADMAP)

    assert roadmap.project == "replace-with-project-slug"
    assert roadmap.verification_full == ("python", "scripts/hermetic_verification.py")
    assert roadmap.review_routing["release"] == ("reviewer", "claude_reviewer")
    assert slice_by_id(roadmap, "M001-S01").milestone_id == "M001"
    assert effective_prefixes(roadmap, slice_by_id(roadmap, "M001-S01")) == ("07_app/",)
    with pytest.raises(FrozenInstanceError):
        roadmap.project = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("mutate,message", [
    (_set(("milestones",), []), "milestones must be a non-empty list"),
    (_set(M + ("slices",), []), "milestones[0].slices must be non-empty"),
    (_set(M + ("id",), "invalid"), "milestones[0].id must match Mnnn"),
    (_set(S + ("id",), "invalid"), "milestones[0].slices[0].id must match M001-Snn"),
])
def test_idle_shape_keeps_strict_nonempty_structure(tmp_path, mutate, message):
    data = _base()
    data["milestones"][0]["status"] = "planned"
    mutate(data)
    path = tmp_path / "roadmap.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(RoadmapError) as caught:
        load(path)
    assert message in caught.value.errors


@pytest.mark.parametrize(
    ("statuses", "errors"),
    [
        (("active", "planned", "done"), ()),
        (("done", "done"), ()),
        (("active", "bad"), ("milestones[1].status must be planned, active, or done",)),
        (("planned",), ()),
        (("done", "planned"), ()),
    ],
    ids=("all-statuses", "all-done", "invalid-status", "planned-only", "done-and-planned"),
)
def test_milestone_status_rules_match_template(tmp_path, statuses, errors):
    project = tmp_path / "project"
    shutil.copytree(FIXTURE, project)
    data = _base()
    base = data["milestones"][0]
    data["milestones"] = []
    for index, status in enumerate(statuses, 1):
        milestone = copy.deepcopy(base)
        milestone.update(id=f"M{index:03}", status=status)
        milestone["slices"][0]["id"] = f"M{index:03}-S01"
        data["milestones"].append(milestone)
    path = project / "roadmap.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-B", str(project / "scripts/roadmap.py"), "check",
         "--root", str(project)],
        capture_output=True, text=True, check=False,
    )

    template_errors = errors
    assert result.returncode == (2 if template_errors else 0)
    assert result.stdout == ("" if template_errors else "roadmap: ok\n")
    assert result.stderr == "".join(f"error: {message}\n" for message in template_errors)
    if errors:
        with pytest.raises(RoadmapError) as caught:
            load(path)
        assert caught.value.errors == errors
    else:
        assert tuple(m.status for m in load(path).milestones) == statuses


def test_next_slice_prioritizes_sticky_reopens_then_active_order_then_none() -> None:
    roadmap = load(ROADMAP)
    first = roadmap.milestones[0].slices[0]
    second = replace(first, id="M001-S02")
    third = replace(first, id="M001-S03")
    milestone = replace(roadmap.milestones[0], slices=(first, second, third))
    roadmap = replace(roadmap, milestones=(milestone,))

    state = SimpleNamespace(
        slices={
            first.id: SimpleNamespace(step="verifying", reopened=True),
            third.id: SimpleNamespace(step="fix", reopened=True),
        }
    )
    assert next_slice(roadmap, state) == first
    state.slices[first.id] = SimpleNamespace(step="accepted", reopened=True)
    assert next_slice(roadmap, state) == third
    state.slices[third.id] = SimpleNamespace(step="accepted", reopened=False)
    assert next_slice(roadmap, state) == second
    state.slices[second.id] = SimpleNamespace(step="accepted")
    assert next_slice(roadmap, state) is None


def test_render_markdown_matches_template_script(tmp_path: Path) -> None:
    project = tmp_path / "project"
    shutil.copytree(FIXTURE, project)
    from test_ledger import _git
    _git(project, "init", "-q")
    _git(project, "config", "user.email", "test@example.invalid")
    _git(project, "config", "user.name", "Test")
    _git(project, "add", ".")
    _git(project, "commit", "-qm", "native roadmap")
    result = subprocess.run(
        [sys.executable, str(project / "scripts" / "roadmap.py"), "render", "--root", str(project)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert render_markdown(load(ROADMAP)) == (project / "docs" / "roadmap.md").read_text(
        encoding="utf-8"
    )


def test_slice_size_warning_does_not_refuse_load(tmp_path: Path) -> None:
    data = _base()
    data["milestones"][0]["slices"][0]["notes"] = "x" * 3000
    path = tmp_path / "roadmap.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    assert load(path).milestones[0].slices[0].notes == "x" * 3000


def test_v2_retains_runtime_profile_and_verification_declarations(tmp_path):
    data = _base()
    data.update(schema="frutlups.roadmap/2", runtime={"python": "3.14.3"},
                autonomy={"schema": "frutlups.autonomy/1"})
    data["verification"].update(git_boundary=["python", "check.py"],
                                observation="clean_checkout", timeout_seconds=900)
    path = tmp_path / "roadmap.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    model = load(path)
    assert model.schema == data["schema"]
    assert model.runtime == data["runtime"]
    assert model.autonomy == data["autonomy"]
    assert model.verification_git_boundary == ("python", "check.py")
    assert model.verification_observation == "clean_checkout"
    assert model.verification_timeout_seconds == 900
    with pytest.raises(TypeError):
        model.autonomy["extra"] = True
    baseline = load(ROADMAP)
    assert baseline.schema == "frutlups.roadmap/2"
    assert baseline.runtime == {}
    assert baseline.autonomy is None
    assert baseline.verification_git_boundary is None
    assert baseline.verification_observation == "process"
    assert baseline.verification_timeout_seconds == 600
    with pytest.raises(RoadmapError, match="unsupported roadmap schema"):
        load(FIXTURE.parent / "v4_project/roadmap.yaml")


@pytest.mark.parametrize("schema,key,value", [
    ("frutlups.roadmap/1", "autonomy", {"schema": "frutlups.autonomy/1"}),
    ("frutlups.roadmap/1", "runtime", {"python": "3.14"}),
    *[("frutlups.roadmap/2", "autonomy", value) for value in
      (None, {}, {"schema": "frutlups.autonomy/2"},
       {"schema": "frutlups.autonomy/1", "reset": True})],
    ("frutlups.roadmap/2", "runtime", {"python": "2.7"}),
    ("frutlups.roadmap/2", "runtime", {"python": "3.14", "extra": True}),
])
def test_strict_v2_declarations(tmp_path, schema, key, value):
    data = _base()
    data.update(schema=schema)
    data[key] = value
    path = tmp_path / "roadmap.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(RoadmapError):
        load(path)
