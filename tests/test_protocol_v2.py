"""Base /2 event grammar and lifecycle reader qualification."""
import copy
import hashlib
import json
import re
from datetime import datetime
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from frutlups import ledger, roadmap

FIXTURES = Path(__file__).parent / "fixtures"
CORPUS = json.loads((FIXTURES / "protocol_v2.json").read_bytes())
# Final same-commit T corpus and export roadmap; never the development copies.
FINAL = FIXTURES / "v5_final"
FINAL_ROADMAP = FINAL / "project/roadmap.yaml"
NATIVE_CASES = {case["name"]: case for case in json.loads(
    (FINAL / "protocol_v2.json").read_bytes())["cases"]}


def test_final_native_corpus_is_pinned_and_covers_every_predecessor():
    raw = (FINAL / "protocol_v2.json").read_bytes()
    pin = next(row for row in json.loads((FIXTURES / "v5_final.manifest.json").read_bytes())["corpora"]
               if row["path"] == "protocol_v2.json")
    assert (len(raw), hashlib.sha256(raw).hexdigest()) == (pin["bytes"], pin["raw_sha256"])
    assert json.loads(raw)["schema"] == "template.conformance/3"
    assert len(json.loads(raw)["cases"]) == len(NATIVE_CASES)
    assert NATIVE_CASES.keys() == {case["name"] for case in CORPUS["cases"]}


@pytest.mark.parametrize("case", CORPUS["cases"], ids=lambda case: case["name"])
def test_base_event_corpus(case, tmp_path):
    case = NATIVE_CASES[case["name"]]
    model = roadmap.load(FINAL_ROADMAP)
    if "holistic_review" in case:
        model = replace(model, milestones=(replace(model.milestones[0],
                        holistic_review=case["holistic_review"]),))
    path = tmp_path / "ledger.jsonl"
    path.write_bytes(b"\n".join(json.dumps(row).encode() for row in case["events"]) + b"\n")
    before = path.read_bytes()
    def fold():
        return ledger.fold(ledger.read(path), model)
    if not case["valid"]:
        diagnostics = {
            "legacy-prefix-new-suffix": "line 1: unsupported legacy history; use pinned v4",
            "illegal-version-reversal": "line 2: unsupported legacy history; use pinned v4",
            "unknown-output-contract": "line 1: event: unknown=['capability'] missing=[]",
            "nonhuman-retains-acceptance": "line 1: only human retains acceptance",
            "v2-unbound-unblocked-refusal": "line 1: /2 resolution requires an authority-bound resolved event",
            "unknown-ledger-v3": "line 1: invalid schema or event: unknown ledger schema; expected frutlups.ledger/2",
            "noncanonical-v2-extension-timestamp": "line 1: timestamp must be canonical UTC seconds",
        }
        with pytest.raises(ledger.LedgerError) as caught:
            fold()
        expected = diagnostics.get(case["name"], case["error"])
        assert case["error"] in expected
        assert str(caught.value) == expected
    else:
        state = fold()
        current = state.slices["M001-S01"]
        for key, value in case["expected"].items():
            actual = (len(getattr(state, key)) if key in ("checkpoints", "intents") else
                      state.holistic["M001"]["step"] if key == "holistic" else
                      "M001" in state.milestones_done if key == "done" else
                      getattr(current, key))
            assert actual == value, key
    assert path.read_bytes() == before


def test_base_corpus_has_all_sixteen_event_cases():
    assert len(CORPUS["cases"]) == 16
    assert len({case["name"] for case in CORPUS["cases"]}) == 16


def test_legacy_prefix_and_writer_preserve_crlf(tmp_path):
    path = tmp_path / "ledger.jsonl"
    model = roadmap.load(FINAL_ROADMAP)
    row = {"schema": "frutlups.ledger/1", "ev": "note", "by": "human",
           "t": "2026-09-14T00:00:00Z", "text": "legacy"}
    prefix = json.dumps(row).encode() + b"\r\n"
    suffix = json.dumps({**row, "schema": ledger.SCHEMA_V2}).encode() + b"\n"
    for raw in (prefix, prefix + suffix, suffix + prefix):
        path.write_bytes(raw)
        with pytest.raises(ledger.LedgerError, match="unsupported legacy history"):
            ledger.read(path)
        with pytest.raises(ledger.LedgerError, match="unsupported legacy history"):
            ledger.append(path, row, model)
        assert path.read_bytes() == raw


@pytest.mark.parametrize("count", [ledger.EVENT_LIMIT + 1, 16 * 1024 * 1024])
def test_oversized_record_uses_bounded_read(tmp_path, monkeypatch, count):
    path = tmp_path / "ledger.jsonl"
    path.write_bytes(b" " * count)
    original = Path.open
    reads = []
    class Bounded:
        def __enter__(self): return self
        def __exit__(self, *args): self.stream.close()
        def readline(self, size):
            reads.append(size)
            return self.stream.readline(size)
    def tracked(self, *args, **kwargs):
        stream = original(self, *args, **kwargs)
        if self == path:
            wrapped = Bounded()
            wrapped.stream = stream
            return wrapped
        return stream
    monkeypatch.setattr(Path, "open", tracked)
    with pytest.raises(ledger.LedgerError, match="exceeds 2 MiB"):
        ledger.read(path)
    assert reads == [ledger.EVENT_LIMIT + 1]


@pytest.mark.parametrize("changes,message", [
    ({"t": "2026-02-30T00:00:00Z"}, "day"),
    ({"t": "2026-09-14T00:00:00+00:00"}, "canonical UTC"),
    ({"envelope": {"path": "../outside", "sha": "a" * 64}}, "unsafe"),
    ({"envelope": {"path": "safe", "sha": "A" * 64}}, "reference hash"),
    ({"envelope": {"path": "safe", "sha": "a" * 64, "extra": True}}, "reference"),
    ({"extra": True}, "unknown"),
])
def test_v2_strict_timestamp_fields_and_references(changes, message):
    row = CORPUS["cases"][1]["events"][0]
    with pytest.raises(ledger.LedgerError, match=message):
        ledger._parse_event({**row, **changes})


def test_ledger_requires_utf8(tmp_path):
    path = tmp_path / "ledger.jsonl"
    path.write_bytes(json.dumps(CORPUS["cases"][1]["events"][0]).encode("utf-16"))
    with pytest.raises(ledger.LedgerError, match="cannot read"):
        ledger.read(path)


# CPython versions differ in calendar-error wording; preserve their exact diagnostic.
try:
    datetime.fromisoformat("2026-02-30T00:00:00+00:00")
except ValueError as exc:
    _INVALID_DAY = str(exc)


_SHA = "a" * 64
_REF = {"path": "evidence.json", "sha": _SHA}
_CHANGE = {"path": "product.txt", "sha": _SHA, "kind": "modified"}
NATIVE_BASE_FIELDS = {
    "prompt": dict(ev="prompt", slice="M001-S01", round=1, path="prompt.md",
                   sha=_SHA, baseline=[_CHANGE], envelope=_REF),
    "coded": dict(ev="coded", slice="M001-S01", round=1, changed=[_CHANGE],
                  manifest=_REF, result="implemented", notes_path="notes.md",
                  notes_sha=_SHA, seat="coder", secs=0.5, tokens_in=0,
                  tokens_out=2, cost_usd=0),
    "coded-blocked": dict(ev="coded", slice="M001-S01", round=1, changed=[],
                          manifest=_REF, result="blocked_authority", outcome=_REF),
    "verified": dict(ev="verified", slice="M001-S01", round=1,
                     receipt="receipt.json", sha=_SHA, ok=True),
    "reviewed": dict(ev="reviewed", slice="M001-S01", round=1, report="review.md",
                     sha=_SHA, verdict="needs_work", open=["F1"], seat="reviewer",
                     secs=1, tokens_in=2, tokens_out=0, cost_usd=0.25),
    "accepted": dict(ev="accepted", slice="M001-S01", round=1, commit="b" * 40,
                     commit_intent=dict(id="c" * 32, parent="d" * 40,
                                        manifest=_REF, message="accept")),
    "reopened": dict(ev="reopened", slice="M001-S01", round=2, reason="F1"),
    "review-prompt": dict(ev="artifact", scope="M001-S01", round=1,
                          role="review_prompt", path="review-prompt.md", sha=_SHA),
    "holistic-prompt": dict(ev="artifact", scope="M001", round="holistic",
                            role="holistic_prompt", path="holistic.md", sha=_SHA),
    "holistic-report": dict(ev="artifact", scope="M001", round="holistic",
                            role="holistic_report", path="review.md", sha=_SHA, by="human"),
    "evidence": dict(ev="artifact", scope="M001-S01", round=1,
                     role="evidence", path="evidence.json", sha=_SHA),
    "milestone-done": dict(ev="milestone_done", milestone="M001", holistic_report="review.md"),
    "note": dict(ev="note", text="status", slice="M001-S01"),
    "stop": dict(ev="stop", by="frutlups", reason="boundary", detail="finished"),
}


def _native_base_event(name):
    return copy.deepcopy({"schema": ledger.SCHEMA_V2, "t": "2026-09-14T00:00:00Z",
                          "by": "architect", **NATIVE_BASE_FIELDS[name]})


@pytest.fixture
def native_validation_trace(monkeypatch):
    parse, validate = ledger._parse_event, ledger._validate_base
    calls = {"parse": [], "base": []}

    def native_parse(value, line="event"):
        # Also catches recursive schema coercion through the real read route.
        assert value["schema"] == ledger.SCHEMA_V2
        calls["parse"].append(copy.deepcopy(value))
        return parse(value, line)

    def neutral_validate(value, line="event"):
        assert value["schema"] == ledger.SCHEMA_V2
        calls["base"].append(copy.deepcopy(value))
        return validate(value, line)

    monkeypatch.setattr(ledger, "_parse_event", native_parse)
    monkeypatch.setattr(ledger, "_validate_base", neutral_validate)
    return calls


@pytest.mark.parametrize("name", NATIVE_BASE_FIELDS)
def test_native_base_parser_and_reader_do_not_reenter_legacy(name, tmp_path, native_validation_trace):
    row = _native_base_event(name)
    before = copy.deepcopy(row)
    parsed = ledger._parse_event(row)
    assert ledger._event_dict(parsed) == before
    assert type(parsed.ev) is ledger.Ev
    assert row == before
    assert native_validation_trace["parse"] == [before]
    shared = name != "evidence"
    expected_base = {k: v for k, v in before.items()
                     if k not in ledger.V2_EXTRA.get(row["ev"], set())}
    assert native_validation_trace["base"] == ([expected_base] if shared else [])
    path = tmp_path / "ledger.jsonl"
    raw = b"\n" + json.dumps(row).encode() + b"\r\n"
    path.write_bytes(raw)
    assert ledger.read(path) == (parsed,)
    assert path.read_bytes() == raw
    assert native_validation_trace["parse"] == [before, before]
    assert native_validation_trace["base"] == ([expected_base, expected_base] if shared else [])


@pytest.mark.parametrize("name,changes,missing,message", [
    ("prompt", {}, "path", "event: unknown=[] missing=['path']"),
    ("prompt", {"extra": True}, None, "event: unknown=['extra'] missing=[]"),
    ("prompt", {}, "envelope", "/2 prompt requires a frozen envelope"),
    ("prompt", {"ev": "unknown"}, None, "event: invalid schema or event"),
    ("prompt", {"ev": 1}, None, "event name must be text"),
    ("prompt", {"by": "coder"}, None, "invalid actor"),
    ("prompt", {"t": None}, None, "invalid timestamp"),
    ("prompt", {"t": "2026-09-14T00:00:00+00:00"}, None, "timestamp must be canonical UTC seconds"),
    ("prompt", {"t": "2026-02-30T00:00:00Z"}, None, _INVALID_DAY),
    ("prompt", {"round": True}, None, "event: invalid round"),
    ("prompt", {"round": 0}, None, "event: invalid round"),
    ("prompt", {"round": 1.0}, None, "event: invalid round"),
    ("prompt", {"slice": "M001"}, None, "event: invalid slice"),
    ("prompt", {"path": "../outside"}, None, "event: unsafe repository-relative path: '../outside'"),
    ("prompt", {"sha": "A" * 64}, None, "event: invalid sha"),
    ("prompt", {"envelope": []}, None, "invalid evidence reference"),
    ("prompt", {"envelope": {**_REF, "extra": True}}, None, "invalid evidence reference"),
    ("prompt", {"envelope": {**_REF, "sha": "A" * 64}}, None, "invalid evidence reference hash"),
    ("prompt", {"envelope": {**_REF, "path": "../outside"}}, None, "unsafe repository-relative path: '../outside'"),
    ("prompt", {"baseline": None}, None, "event: baseline must be a list"),
    ("prompt", {"baseline": [{**_CHANGE, "sha": "bad"}]}, None, "event: invalid baseline[0]"),
    ("coded", {"changed": {}}, None, "event: changed must be a list"),
    ("coded", {"changed": [{**_CHANGE, "kind": "copied"}]}, None, "event: invalid changed[0]"),
    ("coded", {"changed": [{**_CHANGE, "extra": 1}]}, None, "event: invalid changed[0]"),
    ("coded", {"changed": [{**_CHANGE, "path": "../outside"}]}, None, "event: unsafe repository-relative path: '../outside'"),
    ("coded", {"secs": True}, None, "event: invalid text, usage, or actor field"),
    ("coded", {"tokens_in": 0.5}, None, "event: invalid text, usage, or actor field"),
    ("coded", {"tokens_out": -1}, None, "event: invalid text, usage, or actor field"),
    ("coded", {"cost_usd": "1"}, None, "event: invalid text, usage, or actor field"),
    ("coded", {"seat": " "}, None, "event: invalid text, usage, or actor field"),
    ("coded", {}, "notes_sha", "notes require a hash"),
    ("coded", {}, "manifest", "/2 coded needs manifest/result"),
    ("coded-blocked", {}, "outcome", "blocker requires outcome"),
    ("verified", {"ok": 1}, None, "event: ok must be boolean"),
    ("reviewed", {"verdict": "override"}, None, "event: invalid review fields"),
    ("reviewed", {"open": [""]}, None, "event: invalid review fields"),
    ("accepted", {"commit": "bad!"}, None, "event: invalid commit"),
    ("accepted", {"commit_intent": {}}, None, "invalid commit intent"),
    ("reopened", {"by": "frutlups"}, None, "explicit reopen requires human or architect"),
    ("review-prompt", {"by": "human"}, None, "event: invalid artifact scope, round, role, or actor"),
    ("holistic-prompt", {"round": 1}, None, "event: invalid round"),
    ("evidence", {"scope": "M001"}, None, "evidence scope/round mismatch"),
    ("milestone-done", {"milestone": "M001-S01"}, None, "event: invalid milestone"),
    ("note", {"text": " "}, None, "event: invalid text, usage, or actor field"),
    ("stop", {"by": "human"}, None, "event: invalid text, usage, or actor field"),
])
def test_native_grammar_refusal_preserves_input_and_line_context(
    name, changes, missing, message, tmp_path, native_validation_trace,
):
    row = _native_base_event(name)
    row.update(copy.deepcopy(changes))
    if missing is not None:
        del row[missing]
    before = copy.deepcopy(row)
    with pytest.raises(ledger.LedgerError) as caught:
        ledger._parse_event(row)
    assert str(caught.value) == "event: " + message
    assert row == before
    assert native_validation_trace["parse"] == [before]
    path = tmp_path / "ledger.jsonl"
    raw = b"\n" + json.dumps(row).encode() + b"\n"
    path.write_bytes(raw)
    with pytest.raises(ledger.LedgerError) as caught:
        ledger.read(path)
    assert str(caught.value) == "line 2: " + message
    assert path.read_bytes() == raw
    assert native_validation_trace["parse"] == [before, before]


def test_native_event_freezes_nested_data_without_mutating_input():
    row = _native_base_event("prompt")
    before = copy.deepcopy(row)
    event = ledger._parse_event(row)
    assert row == before
    with pytest.raises(FrozenInstanceError):
        event.schema = "frutlups.ledger/1"
    with pytest.raises(TypeError):
        event.data["envelope"]["path"] = "changed.json"
    with pytest.raises(TypeError):
        event.data["baseline"][0]["sha"] = "b" * 64
    assert isinstance(event.data["baseline"], tuple)
    row["envelope"]["path"] = "later.json"
    row["baseline"][0]["path"] = "later.txt"
    assert ledger._event_dict(event) == before
