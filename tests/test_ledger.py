import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import frutlups.ledger as ledger_module
import pytest
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
from frutlups.roadmap import load

FIXTURE = Path(__file__).parent / "fixtures" / "v4_project"
STAMP = "2026-09-03T10:00:00Z"
DIGEST = "0" * 64


def _event(ev: str, *, by: str = "architect", **data: object) -> dict:
    return {"schema": "frutlups.ledger/1", "t": STAMP, "ev": ev, "by": by, **data}


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
    if events and events[-1]["ev"] == "unblocked":
        assert current.open_findings == ("F1",)
        assert current.unblock_reason == "approved"
    if events and events[-1]["ev"] == "reopened":
        assert current.reopened is True


def test_all_eleven_event_types_are_declared() -> None:
    assert {event.value for event in Ev} == {
        "prompt", "artifact", "coded", "verified", "reviewed", "accepted",
        "reopened", "unblocked", "milestone_done", "note", "stop",
    }


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("{", "line 1: malformed JSON"),
        (json.dumps(_event("note", text="ok", extra=True)), "line 1: unknown"),
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
        append(path, candidate, roadmap)

    assert path.read_bytes() == before


def test_append_writes_lf_and_fsyncs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roadmap = load(FIXTURE / "roadmap.yaml")
    path = tmp_path / "ledger.jsonl"
    candidate = _parsed(tmp_path / "candidate", [_prompt()])[0]
    calls = []
    monkeypatch.setattr(ledger_module.os, "fsync", calls.append)

    append(path, candidate, roadmap)

    assert calls
    assert path.read_bytes().endswith(b"\n")
    assert fold(read(path), roadmap).slices["M001-S01"].step == "coding"


def test_append_loads_adjacent_roadmap_by_default(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    shutil.copyfile(FIXTURE / "roadmap.yaml", root / "roadmap.yaml")
    path = root / "05_governance" / "ledger.jsonl"
    candidate = _parsed(tmp_path / "candidate", [_prompt()])[0]

    append(path, candidate)

    assert read(path) == (candidate,)


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
