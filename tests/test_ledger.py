import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import frutlups.ledger as ledger_module
import pytest
import yaml
from frutlups.ledger import (
    Ev,
    LedgerError,
    append,
    check,
    evidence_sha,
    fold,
    read,
    status_text,
)
from frutlups.roadmap import load, next_slice

FIXTURE = Path(__file__).parent / "fixtures" / "v5_development"
STAMP = "2026-09-03T10:00:00Z"
DIGEST = "0" * 64


def _event(ev: str, *, by: str = "architect", **data: object) -> dict:
    if ev == "prompt":
        data = {"envelope": {"path": "envelope.json", "sha": DIGEST}, **data}
    elif ev == "coded":
        data = {"manifest": {"path": "manifest.json", "sha": DIGEST}, "result": "implemented", **data}
        if "notes_path" in data:
            data["notes_sha"] = DIGEST
    elif ev == "unblocked":
        ev = "resolved"
        data.pop("round", None)
        data = {"scope": data.pop("slice"), "authority": [{"path": "decision.md", "sha": DIGEST}], **data}
    return {"schema": "frutlups.ledger/2", "t": STAMP, "ev": ev, "by": by, **data}


def _prompt(round_no: int = 1) -> dict:
    return _event("prompt", slice="M001-S01", round=round_no, path="prompt.md", sha=DIGEST)


def _coded(round_no: int = 1, **extra: object) -> dict:
    return _event(
        "coded", slice="M001-S01", round=round_no, changed=[], **extra,
    )


def _verified(ok: bool, round_no: int = 1) -> dict:
    return _event(
        "verified", slice="M001-S01", round=round_no, receipt="receipt.json",
        sha=DIGEST, ok=ok,
    )


def _reviewed(verdict: str, round_no: int = 1, open_ids: list[str] | None = None) -> dict:
    return _event(
        "reviewed", slice="M001-S01", round=round_no, report="review.md", sha=DIGEST,
        verdict=verdict, open=[] if open_ids is None else open_ids,
    )


def _accepted(round_no: int = 1) -> dict:
    return _event("accepted", slice="M001-S01", round=round_no)


def _through_verified(ok: bool = True, round_no: int = 1) -> list[dict]:
    return [_prompt(round_no), _coded(round_no), _verified(ok, round_no)]


def _through_review(verdict: str, open_ids: list[str] | None = None) -> list[dict]:
    return [*_through_verified(), _reviewed(verdict, open_ids=open_ids)]


def _accepted_events() -> list[dict]:
    return [*_through_review("pass"), _accepted()]


def _write(path: Path, events: list[dict], *, trailing_newline: bool = True) -> None:
    text = "\n".join(json.dumps(event, separators=(",", ":")) for event in events)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + ("\n" if trailing_newline and text else ""), encoding="utf-8")


def _parsed(tmp_path: Path, events: list[dict]):
    path = tmp_path / "ledger.jsonl"
    _write(path, events)
    return read(path)


FOLD_CASES = [
    pytest.param([], "unstarted", 1, id="none"),
    pytest.param([_prompt()], "coding", 1, id="prompt"),
    pytest.param([_prompt(), _coded()], "verifying", 1, id="coded"),
    pytest.param([_prompt(), _coded(cost_usd=0.25)], "verifying", 1, id="coded-cost"),
    pytest.param(_through_verified(False), "fix", 2, id="verified-failed"),
    pytest.param(_through_verified(), "reviewing", 1, id="verified-passed"),
    pytest.param(
        [
            *_through_verified(),
            _event(
                "artifact", scope="M001-S01", round=1, role="review_prompt",
                path="review-prompt.md", sha=DIGEST,
            ),
        ],
        "reviewing",
        1,
        id="artifact",
    ),
    pytest.param(_through_review("needs_work", ["F1"]), "fix", 2, id="needs-work"),
    pytest.param(_through_review("blocked", ["F1"]), "blocked", 1, id="blocked"),
    pytest.param(
        [
            *_through_review("blocked", ["F1"]),
            _event(
                "unblocked", by="human", slice="M001-S01", round=2, reason="approved",
            ),
        ],
        "fix",
        2,
        id="unblocked",
    ),
    pytest.param(_through_review("pass"), "accept_pending", 1, id="passed"),
    pytest.param(_accepted_events(), "accepted", 1, id="accepted"),
    pytest.param(
        [
            *_accepted_events(),
            _event("reopened", slice="M001-S01", round=2, reason="holistic F1"),
        ],
        "fix",
        2,
        id="reopened",
    ),
]


@pytest.mark.parametrize(("events", "step", "round_no"), FOLD_CASES)
def test_fold_table_rows(
    tmp_path: Path, events: list[dict], step: str, round_no: int,
) -> None:
    roadmap = load(FIXTURE / "roadmap.yaml")

    state = fold(_parsed(tmp_path, events), roadmap)

    current = state.slices["M001-S01"]
    assert current.step == step
    assert current.round == round_no
    if events and events[-1]["ev"] == "resolved":
        assert current.open_findings == ("F1",)
        assert current.resolution == "approved"
    if events and events[-1]["ev"] == "reopened":
        assert current.reopened is True


def test_legacy_and_v2_event_types_are_declared() -> None:
    assert {event.value for event in Ev} == {
        "prompt", "artifact", "coded", "verified", "reviewed", "accepted",
        "reopened", "milestone_done", "note", "stop",
        "resolved", "commit_cancelled", "holistic_reviewed", "review_checkpoint",
        "attempt_started", "attempt_finished", "run_admitted", "run_extended",
    }


@pytest.mark.parametrize("kind", ["coded", "reviewed"])
@pytest.mark.parametrize("cost", [0, 1, 0.0, 0.25])
def test_optional_cost_preserves_numeric_value(tmp_path, kind, cost):
    event = _coded(cost_usd=cost) if kind == "coded" else {**_reviewed("pass"), "cost_usd": cost}
    events = [_prompt(), event] if kind == "coded" else [*_through_verified(), event]
    parsed = _parsed(tmp_path, events)

    assert parsed[-1].data["cost_usd"] == cost
    assert type(parsed[-1].data["cost_usd"]) is type(cost)
    current = fold(parsed, load(FIXTURE / "roadmap.yaml")).slices["M001-S01"]
    assert current.step == ("verifying" if kind == "coded" else "accept_pending")


@pytest.mark.parametrize("kind", ["coded", "reviewed"])
@pytest.mark.parametrize("cost", [-1, -0.25, True, False, "1", None])
def test_cost_refuses_invalid_numeric_values(tmp_path, kind, cost):
    event = _coded(cost_usd=cost) if kind == "coded" else {**_reviewed("pass"), "cost_usd": cost}
    with pytest.raises(LedgerError) as caught:
        _parsed(tmp_path, [event])
    assert str(caught.value) == "line 1: event: invalid text, usage, or actor field"


@pytest.mark.parametrize(
    ("statuses", "accepted", "reopened", "done", "expected"),
    [
        (("active", "active"), (1,), (), (), "M002-S01"),
        (("active", "planned"), (1,), (), (), None),
        (("active", "done"), (1,), (), (), None),
        (("active", "done", "done"), (2, 3), (3, 2), (), "M002-S01"),
        (("active", "active"), (1, 2), (), (1, 2), None),
        (("active", "active"), (1,), (), (1,), "M002-S01"),
    ],
    ids=("skip-accepted-active", "skip-planned", "skip-done", "reopen-roadmap-order",
         "all-accepted", "milestone-done-does-not-gate"),
)
def test_next_slice_matches_template_on_same_ledger(
    tmp_path, statuses, accepted, reopened, done, expected,
):
    project = tmp_path / "project"
    shutil.copytree(FIXTURE, project)
    path = project / "roadmap.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    base = data["milestones"][0]
    data["milestones"] = [
        {**base, "id": f"M{index:03}", "status": status, "holistic_review": True,
         "slices": [{**base["slices"][0], "id": f"M{index:03}-S01"}]}
        for index, status in enumerate(statuses, 1)
    ]
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    events = [
        {**event, "slice": f"M{index:03}-S01"}
        for index in accepted for event in _accepted_events()
    ]
    events += [row for index in done for row in (
        _event("holistic_reviewed", milestone=f"M{index:03}", report="holistic.md", sha=DIGEST, verdict="pass", open=[]),
        _event("milestone_done", milestone=f"M{index:03}", holistic_report="holistic.md"))]
    events += [
        _event("reopened", slice=f"M{index:03}-S01", round=2, reason="holistic finding")
        for index in reopened
    ]
    ledger_path = project / "05_governance/ledger.jsonl"
    _write(ledger_path, events)
    model = load(path)
    state = fold(read(ledger_path), model)
    following = next_slice(model, state)

    assert (following.id if following else None) == expected
    # Selection depends on slice state and roadmap status, even if this set is stale.
    all_done = frozenset(m.id for m in model.milestones)
    assert next_slice(model, replace(state, milestones_done=all_done)) == following
    for script, command, output in (
        ("roadmap.py", "next", f"{expected or 'none'}\n"),
        ("ledger.py", "status", status_text(model, state) + "\n"),
    ):
        # The template's next CLI lives in roadmap.py and delegates to ledger.next_slice.
        args = [sys.executable, "-B", str(project / "scripts" / script)]
        if script == "roadmap.py":
            args += [command, "--root", str(project)]
        else:
            args += ["--root", str(project), command]
        result = subprocess.run(args, capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""
        template_suffix = ""
        if script == "ledger.py":
            template_suffix += "".join(f"M{index:03} holistic: done\n" for index in done)
            template_suffix += "".join(f"warning: M{index:03} declared done without ledger closure\n"
                                       for index, status in enumerate(statuses, 1) if status == "done" and index not in done)
        assert result.stdout == output + template_suffix


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("{", "line 1: malformed JSON"),
        (json.dumps(_event("note", text="ok", extra=True)), "line 1: event: unknown"),
        (json.dumps({**_event("note", text="ok"), "schema": "bad"}), "line 1: invalid schema"),
    ],
    ids=("malformed", "unknown-field", "bad-schema"),
)
def test_read_names_line_for_strict_failures(tmp_path: Path, line: str, message: str) -> None:
    path = tmp_path / "ledger.jsonl"
    path.write_text(line + "\n", encoding="utf-8")

    with pytest.raises(LedgerError, match=message):
        read(path)


def test_append_folds_before_write_and_preserves_bytes_on_refusal(tmp_path: Path) -> None:
    roadmap = load(FIXTURE / "roadmap.yaml")
    path = tmp_path / "ledger.jsonl"
    _write(path, [_prompt()])
    before = path.read_bytes()
    candidate = _parsed(tmp_path / "candidate", [_accepted()])[0]

    with pytest.raises(LedgerError, match="accepted out of order"):
        fold((*read(path), candidate), roadmap)
    with pytest.raises(LedgerError, match="/2 ledger append is not enabled"):
        append(path, candidate, roadmap)

    assert path.read_bytes() == before


def test_append_writes_lf_and_fsyncs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roadmap = load(FIXTURE / "roadmap.yaml")
    path = tmp_path / "ledger.jsonl"
    candidate = _parsed(tmp_path / "candidate", [_prompt()])[0]
    calls = []
    monkeypatch.setattr(ledger_module.os, "fsync", calls.append)

    ledger_module._write_line(path, ledger_module._event_dict(candidate))

    assert calls
    assert path.read_bytes().endswith(b"\n")
    assert fold(read(path), roadmap).slices["M001-S01"].step == "coding"


def test_append_loads_adjacent_roadmap_by_default(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    shutil.copyfile(FIXTURE / "roadmap.yaml", root / "roadmap.yaml")
    path = root / "05_governance" / "ledger.jsonl"
    candidate = _parsed(tmp_path / "candidate", [_prompt()])[0]

    with pytest.raises(LedgerError, match="/2 ledger append is not enabled"):
        append(path, candidate)
    assert not path.exists()


@pytest.mark.parametrize(
    ("events", "message"),
    [
        (
            [_event(
                "artifact", scope="M001-S01", round=1, role="review_prompt",
                path="review-prompt.md", sha=DIGEST,
            )],
            "review prompt artifact out of order",
        ),
        (
            [_event(
                "artifact", scope="M999", round="holistic", role="holistic_prompt",
                path="holistic.md", sha=DIGEST,
            )],
            "unknown milestone",
        ),
        (
            [_event("milestone_done", milestone="M001")],
            "premature milestone_done",
        ),
        ([_coded()], "coded out of order"),
        ([_event("prompt", slice="M999-S01", round=1, path="p", sha=DIGEST)],
         "unknown slice"),
        ([_prompt(2)], "prompt out of order"),
    ],
    ids=("review-artifact-timing", "artifact-scope", "milestone-timing",
         "transition", "slice-id", "round"),
)
def test_fold_rejects_invalid_scope_timing_transition_and_round(
    tmp_path: Path, events: list[dict], message: str,
) -> None:
    roadmap = load(FIXTURE / "roadmap.yaml")

    with pytest.raises(LedgerError, match=message):
        fold(_parsed(tmp_path, events), roadmap)


def test_holistic_artifacts_and_milestone_done_after_acceptance(tmp_path: Path) -> None:
    roadmap = load(FIXTURE / "roadmap.yaml")
    events = [
        *_accepted_events(),
        _event(
            "artifact", scope="M001", round="holistic", role="holistic_prompt",
            path="holistic-prompt.md", sha=DIGEST,
        ),
        _event(
            "artifact", by="human", scope="M001", round="holistic",
            role="holistic_report", path="holistic-report.md", sha=DIGEST,
        ),
        _event("holistic_reviewed", milestone="M001", report="holistic-report.md", sha=DIGEST, verdict="pass", open=[]),
        _event("milestone_done", milestone="M001", holistic_report="holistic-report.md"),
    ]

    state = fold(_parsed(tmp_path, events), roadmap)

    assert state.milestones_done == frozenset({"M001"})
    assert state.slices["M001-S01"].step == "accepted"


def test_corrective_prompt_count_and_reopen_flag_clear_on_accept(tmp_path: Path) -> None:
    roadmap = load(FIXTURE / "roadmap.yaml")
    events = [
        *_accepted_events(),
        _event("reopened", slice="M001-S01", round=2, reason="holistic F1"),
        _prompt(2), _coded(2), _verified(True, 2), _reviewed("pass", 2), _accepted(2),
    ]

    current = fold(_parsed(tmp_path, events), roadmap).slices["M001-S01"]

    assert current.corrective_rounds_used == 1
    assert current.reopened is False


def test_evidence_sha_normalizes_text_but_not_nul_bytes(tmp_path: Path) -> None:
    crlf = tmp_path / "crlf.txt"
    lf = tmp_path / "lf.txt"
    crlf.write_bytes(b"one\r\ntwo\r\n")
    lf.write_bytes(b"one\ntwo\n")
    binary = b"one\0\r\ntwo"

    assert evidence_sha(crlf.read_bytes()) == evidence_sha(lf.read_bytes())
    assert evidence_sha(binary) == hashlib.sha256(binary).hexdigest()


@pytest.mark.parametrize(
    ("data", "normalized"),
    [
        pytest.param(b"one\r\ntwo\r\n", b"one\ntwo\n", id="crlf"),
        pytest.param(b"one\ntwo\n", b"one\ntwo\n", id="lf-twin"),
        pytest.param(b"one\0\r\ntwo", b"one\0\r\ntwo", id="nul-with-crlf"),
        pytest.param(b"one\rtwo\r", b"one\rtwo\r", id="lone-cr"),
    ],
)
def test_evidence_sha_matches_template_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, data: bytes, normalized: bytes,
) -> None:
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    for name in ("_common", "_evidence"):
        spec = importlib.util.spec_from_file_location(
            name, FIXTURE / "scripts" / f"{name}.py",
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)

    # _evidence uses its imported _common for byte and file digests.
    template_evidence = module
    sample = tmp_path / "sample.bin"
    sample.write_bytes(data)
    digest = evidence_sha(data)

    assert digest == hashlib.sha256(normalized).hexdigest()
    assert template_evidence.c.evidence_sha_bytes(data) == digest
    assert template_evidence.c.sha(sample) == digest
    assert sample.read_bytes() == data


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True,
    )


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "core.autocrlf", "false")
    (root / "product.txt").write_bytes(b"base\n")
    _git(root, "add", "product.txt")
    _git(root, "commit", "-q", "-m", "base")
    return root


def test_evidence_sha_hashes_git_blob_bytes(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    blob = _git(root, "show", "HEAD:product.txt").stdout

    assert evidence_sha(blob) == evidence_sha(b"base\n")


def test_check_reports_immutable_artifact_and_notes_drift(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    artifact = root / "prompt.md"
    artifact.write_bytes(b"recorded\n")
    path = root / "ledger.jsonl"
    events = [
        _event(
            "prompt", slice="M001-S01", round=1, path="prompt.md",
            sha=evidence_sha(artifact.read_bytes()),
        ),
        _coded(notes_path="notes.md"),
    ]
    _write(path, events)
    artifact.write_bytes(b"changed\n")

    assert {str(error) for error in check(path, root)} == {
        "drift: prompt.md", "missing: notes.md",
    }


def test_check_accepts_products_restored_to_head_or_absent_from_head(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    path = root / "ledger.jsonl"
    events = [_event(
        "coded", slice="M001-S01", round=1,
        changed=[
            {"path": "product.txt", "sha": evidence_sha(b"latest\n"), "kind": "modified"},
            {"path": "new.txt", "sha": evidence_sha(b"new\n"), "kind": "added"},
        ],
    )]
    _write(path, events)

    assert check(path, root) == ()


@pytest.mark.parametrize(
    "recorded", [b"base\n", b"latest\n"], ids=("matches-latest", "matches-head"),
)
def test_check_accepts_crlf_working_copy_of_lf_blob_like_template(
    tmp_path: Path, recorded: bytes,
) -> None:
    root = _repository(tmp_path)
    shutil.copytree(FIXTURE, root, dirs_exist_ok=True)
    assert _git(root, "config", "--get", "core.autocrlf").stdout == b"false\n"
    assert _git(root, "show", "HEAD:product.txt").stdout == b"base\n"
    product = root / "product.txt"
    product.write_bytes(b"base\r\n")
    prompt = root / "prompt.md"
    prompt.write_bytes(b"implement\n")
    path = root / "05_governance" / "ledger.jsonl"
    rows = [
        {**_prompt(), "sha": hashlib.sha256(prompt.read_bytes()).hexdigest()},
        _event(
            "coded", slice="M001-S01", round=1,
            changed=[{
                "path": "product.txt", "sha": hashlib.sha256(recorded).hexdigest(),
                "kind": "modified",
            }],
        ),
    ]

    from frutlups import render
    model = load(root / "roadmap.yaml")
    item = model.milestones[0].slices[0]
    envelope = render.coding_envelope(root, model, item, fold((), model).slices[item.id])
    envelope["allowed_prefixes"] = ["product.txt"]
    manifest = dict(schema="frutlups.manifest/2", slice=item.id, round=1, changed=[dict(row, round=1) for row in rows[1]["changed"]])
    for row, key, content in ((rows[0], "envelope", envelope), (rows[1], "manifest", manifest)):
        raw = json.dumps(content).encode()
        (root / row[key]["path"]).write_bytes(raw)
        row[key]["sha"] = evidence_sha(raw)
    _write(path, rows)

    assert check(path, root) == ()
    result = subprocess.run(
        [sys.executable, "-B", str(root / "scripts" / "ledger.py"),
         "--root", str(root), "check"],
        check=False, capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "ledger: ok\n"
    assert result.stderr == ""
    assert product.read_bytes() == b"base\r\n"


def test_check_reports_product_drift_from_latest_and_head(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    path = root / "ledger.jsonl"
    _write(path, [_event(
        "coded", slice="M001-S01", round=1,
        changed=[{
            "path": "product.txt", "sha": evidence_sha(b"latest\n"), "kind": "modified",
        }],
    )])
    (root / "product.txt").write_bytes(b"unexpected\n")

    assert tuple(map(str, check(path, root))) == ("drift: product.txt",)


STATUS_CASES = [
    pytest.param([], id="empty"),
    pytest.param([_prompt(), _coded()], id="mid-slice"),
    pytest.param(
        [
            *_through_review("needs_work", ["F1"]),
            _prompt(2), _coded(2), _verified(True, 2), _reviewed("pass", 2), _accepted(2),
        ],
        id="needs-work-then-accepted",
    ),
]


@pytest.mark.parametrize("events", STATUS_CASES)
def test_status_text_matches_template_script(tmp_path: Path, events: list[dict]) -> None:
    project = tmp_path / "project"
    shutil.copytree(FIXTURE, project)
    ledger_path = project / "05_governance" / "ledger.jsonl"
    _write(ledger_path, events)
    result = subprocess.run(
        [sys.executable, str(project / "scripts" / "ledger.py"),
         "--root", str(project), "status"],
        check=False, capture_output=True, text=True,
    )
    roadmap = load(project / "roadmap.yaml")

    assert result.returncode == 0, result.stderr
    assert status_text(roadmap, fold(read(ledger_path), roadmap)) + "\n" == result.stdout
