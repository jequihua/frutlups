"""Interpret the immutable autonomy corpus using the pinned adapter semantics."""
import copy
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from frutlups import ledger, roadmap

FIXTURES = Path(__file__).parent / "fixtures"
CORPUS = json.loads((FIXTURES / "autonomy_v1.json").read_bytes())
# Final same-commit T corpus and export roadmap; never the development copies.
FINAL = FIXTURES / "v5_final"
NATIVE_CASES = {case["name"]: case for case in json.loads(
    (FINAL / "autonomy_v1.json").read_bytes())["cases"]}
PROFILE = {"schema": "frutlups.autonomy/1"}

IDLE_NAMES = [
    "no-active-planned-admitted-history-refuses",
    "completed-run-cannot-become-planned",
    "overrun-run-cannot-become-planned",
    "superseded-run-cannot-become-planned",
    "done-history-requires-holistic-closure",
    "completed-done-plus-never-admitted-planned",
]
IDLE_CASES = [NATIVE_CASES[name] for name in IDLE_NAMES]


def case_roadmap(case, tmp_path):
    data = yaml.safe_load((FINAL / "project/roadmap.yaml").read_text("utf-8"))
    if case.get("declared", True):
        data["autonomy"] = dict(PROFILE)
    options = case.get("roadmap", {})
    milestone = data["milestones"][0]
    milestone["status"] = options.get("status", "active")
    milestone["holistic_review"] = options.get("holistic_review", True)
    original = milestone["slices"][0]
    milestone["slices"] = [
        {**copy.deepcopy(original), "id": sid} for sid in options.get("slices", ["M001-S01"])
    ]
    if options.get("second_milestone"):
        second = copy.deepcopy(milestone)
        second["id"] = "M002"
        second["status"] = options.get("second_status", "active")
        second["slices"] = [{**copy.deepcopy(original), "id": "M002-S01"}]
        data["milestones"].append(second)
    assert roadmap._validate(data)[0] == []
    path = tmp_path / "roadmap.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return roadmap.load(path)


def test_corpus_identity_and_case_counts():
    assert CORPUS["schema"] == "template.autonomy-conformance/1"
    assert len(CORPUS["cases"]) == 128
    assert len({case["name"] for case in CORPUS["cases"]}) == 128
    assert sum(case["valid"] for case in CORPUS["cases"]) == 40


def test_final_native_corpus_is_pinned_and_covers_predecessors_and_additions():
    raw = (FINAL / "autonomy_v1.json").read_bytes()
    pin = next(row for row in json.loads((FIXTURES / "v5_final.manifest.json").read_bytes())["corpora"]
               if row["path"] == "autonomy_v1.json")
    assert (len(raw), hashlib.sha256(raw).hexdigest()) == (pin["bytes"], pin["raw_sha256"])
    assert json.loads(raw)["schema"] == "template.autonomy-conformance/2"
    assert len(json.loads(raw)["cases"]) == len(NATIVE_CASES)
    old = {case["name"] for case in CORPUS["cases"]}
    assert not old & set(IDLE_NAMES)
    assert NATIVE_CASES.keys() == old | set(IDLE_NAMES)


@pytest.mark.parametrize("case", CORPUS["cases"] + IDLE_CASES, ids=lambda case: case["name"])
def test_profile_case(case, tmp_path):
    case = NATIVE_CASES[case["name"]]
    model = case_roadmap(case, tmp_path)
    def fold():
        events = tuple(ledger._parse_event(row) for row in case["events"])
        return ledger.fold(events, model)
    if not case["valid"]:
        assert case["error"]
        with pytest.raises(ledger.LedgerError) as caught:
            fold()
        # The pinned corpus supplies the semantic diagnostic. Assert the full
        # native consumer diagnostic, including its stable parser context.
        expanded = {
            "profile attempts require preceding admission": "profile attempts require preceding admission; historical cutover unsupported",
            "verification requires current verifying round": "verification requires current verifying round and no retry",
            "autonomy history requires declared": "autonomy history requires declared frutlups.autonomy/1 profile",
            "grantors must contain human": "event: grantors must contain human and optionally architect",
        }
        parser_errors = {
            "run grant requires human or architect", "run needs bound authority",
            "duplicate run authority reference", "unsupported legacy history; use pinned v4",
            "invalid run slices", "invalid allowance units", "invalid corrective ceiling",
            "invalid previous run head", "invalid run_admitted fields",
            "extension needs positive allowance", "invalid run allowance", "attempt role/scope mismatch",
        }
        expected = expanded.get(case["error"], ("event: " if case["error"] in parser_errors else "") + case["error"])
        assert str(caught.value) == expected
        return
    state = fold()
    for run_id, expected in case.get("expected", {}).items():
        actual = state.runs[run_id]
        for key, value in expected.items():
            assert ledger._thaw(actual[key]) == value, (run_id, key)
    if "head" in case:
        assert ledger._thaw(state.run_head) == case["head"]
    if "membership" in case:
        assert ledger._thaw(state.run_membership) == case["membership"]


@pytest.mark.parametrize("name,closed,jobs,seconds", [
    ("no-active-planned-admitted-history-refuses", None, 1, 40),
    ("completed-run-cannot-become-planned", "boundary", 0, 0),
    ("overrun-run-cannot-become-planned", "overrun", 1, 106),
    ("superseded-run-cannot-become-planned", "superseded", 0, 0),
])
def test_idle_relabelling_preserves_original_run_accounting(tmp_path, name, closed, jobs, seconds):
    case = copy.deepcopy(next(c for c in IDLE_CASES if c["name"] == name))
    case["roadmap"]["status"] = "active"
    model = case_roadmap(case, tmp_path)
    path = tmp_path / "ledger.jsonl"
    path.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in case["events"]))
    before = path.read_bytes()
    state = ledger.fold(ledger.read(path), model)
    assert state.runs["R1"]["closed"] == closed
    assert state.runs["R1"]["consumed"]["jobs"] == jobs
    assert state.runs["R1"]["consumed"]["seconds"] == seconds
    assert state.runs["R1"]["consumed"]["transport_retries"] == 0
    assert state.runs["R1"]["consumed"]["format_retries"] == 0
    assert state.runs["R1"]["remaining"]["seconds"] == (130 if closed == "superseded" else 100) - seconds
    if closed == "superseded":
        assert ledger._thaw(state.run_head) == {"run": "R2", "revision": 0}
        assert state.runs["R1"]["revision"] == 1
        assert state.runs["R1"]["remaining"]["jobs"] == 6
        assert state.runs["R2"]["consumed"]["jobs"] == 0
    if closed is None:
        assert state.attempts["I1"]["finish"] is None
    case["roadmap"]["status"] = "planned"
    model = case_roadmap(case, tmp_path)
    with pytest.raises(ledger.LedgerError, match="^run admission requires active milestone$"):
        ledger.fold(ledger.read(path), model)
    assert path.read_bytes() == before
