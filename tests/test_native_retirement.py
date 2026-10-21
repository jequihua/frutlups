"""Native-only lifecycle boundaries, including direct construction attempts."""
import json
import hashlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from frutlups import _preflight, gitws, ledger, loop, ownership, receipt, roadmap
from test_loop import project


@pytest.mark.parametrize("schema", ["frutlups.roadmap/1", "frutlups.roadmap/3", "unknown", None])
def test_roadmap_refuses_unsupported_schema_before_admission(project, monkeypatch, schema):
    root, cfg, model = project
    original = (root / "roadmap.yaml").read_bytes()
    data = yaml.safe_load(original)
    data["schema"] = schema
    (root / "roadmap.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    witness = gitws.workspace_witness(root, executable=cfg.git)
    before = (root / cfg.ledger).read_bytes()
    monkeypatch.setattr(_preflight, "_probe", lambda *a, **k: pytest.fail("refusal launched probe"))
    with pytest.raises(roadmap.RoadmapError, match="unsupported roadmap schema"):
        roadmap.load(root / "roadmap.yaml")
    direct = replace(model, schema=schema)
    with pytest.raises(ValueError, match="unsupported roadmap schema"):
        ledger.fold((), direct)
    with pytest.raises(ValueError, match="unsupported roadmap schema"):
        loop.Loop(root, cfg, direct)
    with pytest.raises(ValueError, match="unsupported roadmap schema"):
        with ownership.writer_lock(root, direct, executable=cfg.git):
            pytest.fail("unsupported model acquired ownership")
    assert loop.run(root, cfg, direct) == loop.StopReason.preflight_failed
    assert (root / cfg.ledger).read_bytes() == before
    assert gitws.workspace_witness(root, executable=cfg.git) == witness
    assert not (root / "local_state/frutlups/jobs").exists()


@pytest.mark.parametrize("schemas", [
    ["frutlups.ledger/1"], ["frutlups.ledger/3"], [None],
    ["frutlups.ledger/1", "frutlups.ledger/2"],
    ["frutlups.ledger/2", "frutlups.ledger/1"],
])
def test_ledger_refuses_legacy_mixed_unknown_and_constructed_events(tmp_path, schemas):
    model = roadmap.load(Path(__file__).parent / "fixtures/v5_development/roadmap.yaml")
    rows = [dict(schema=schema, t="2026-09-21T00:00:00Z", ev="note", by="human", text="preserve")
            for schema in schemas]
    path = tmp_path / "ledger.jsonl"
    raw = b"".join(json.dumps(row).encode() + b"\r\n" for row in rows)
    path.write_bytes(raw)
    with pytest.raises(ledger.LedgerError, match="unsupported legacy history|unknown ledger schema"):
        ledger.read(path)
    direct = ledger.Event(schema=schemas[-1] if schemas[-1] != ledger.SCHEMA_V2 else schemas[0],
                          t=rows[0]["t"], ev=ledger.Ev.note, by="human", data={"text": "preserve"})
    with pytest.raises(ledger.LedgerError, match="unsupported legacy history|unknown ledger schema"):
        ledger.fold((direct,), model)
    assert path.read_bytes() == raw


def test_public_native_execution_and_append_have_no_effects(project, monkeypatch):
    root, cfg, model = project
    before = (root / cfg.ledger).read_bytes()
    witness = gitws.workspace_witness(root, executable=cfg.git)
    monkeypatch.setattr(_preflight, "_probe", lambda *a, **k: pytest.fail("public probe"))
    monkeypatch.setattr(receipt, "run", lambda *a, **k: pytest.fail("public verifier"))
    # The fixture's programmatic configuration is not the project's on-disk authority.
    refusal = "admission configuration differs from canonical project configuration"
    assert _preflight.prepare(root, cfg, model)[2] == (refusal,)
    assert loop.run(root, cfg, model) == loop.StopReason.preflight_failed
    direct = loop.Loop(root, cfg, model, emit=lambda _: None)  # construction alone has no effects
    with pytest.raises(ValueError, match="^until: must be one of milestone, roadmap, slice$"):
        loop.Loop(root, replace(cfg, until="never"), model)
    assert direct.iteration() == loop.StopReason.preflight_failed
    row = dict(schema=ledger.SCHEMA_V2, t="2026-09-21T00:00:00Z", ev="note", by="human", text="refuse")
    with pytest.raises(ValueError, match="append is not enabled"):
        ledger.append(root / cfg.ledger, row, model)
    assert (root / cfg.ledger).read_bytes() == before
    assert gitws.workspace_witness(root, executable=cfg.git) == witness
    assert not (root / "local_state/frutlups/jobs").exists()


@pytest.mark.parametrize("schema", ["frutlups.receipt/1", "frutlups.receipt/3", None])
def test_receipt_writer_refuses_unsupported_wire_without_creating_file(tmp_path, schema):
    path = tmp_path / "new/receipt.json"
    with pytest.raises(ValueError, match="unsupported receipt schema"):
        receipt.write(SimpleNamespace(schema=schema), path)
    assert not path.parent.exists()


def test_retirement_accounts_for_each_observed_baseline_node():
    package = Path(__file__).resolve().parents[1]
    report_raw = (package / "docs/native_retirement_coverage.json").read_bytes()
    assert hashlib.sha256(report_raw).hexdigest() == "e935ac38f54b37f304d10f3aa42d423072415c1685156c4011edb20b34210941"
    report = json.loads(report_raw)
    baseline = report["baseline_nodes"]
    assert hashlib.sha256(json.dumps(baseline, separators=(",", ":")).encode()).hexdigest() == (
        "c69db78c4bd6c0da495d0681e6e1aa283bb8769f1c0e58ad65050db39fd53c1f")
    frozen_raw = (package / "tests/fixtures/native_retirement_definitions.json").read_bytes()
    assert len(frozen_raw) == 577807
    assert hashlib.sha256(frozen_raw).hexdigest() == "ea8dea14ab5ec2340fb5603d0f73164aed0d5bb2c04305f95e86d0039c8983e7"
    _assert_retirement_definitions(report, json.loads(frozen_raw))
    entries = report["node_accounting"]
    assert len(baseline) == 1317
    assert len(set(baseline)) == 1317
    assert set(entries) == set(baseline)
    collected = set(report["collection"]["nodes"])
    for node in baseline:
        item = entries[node]
        assert item["disposition"] in ("retained", "converted_to_native", "retired_legacy_positive")
        assert item["replacement_nodes"]
        assert set(item["replacement_nodes"]) <= collected
        invariant = report["invariants"][item["invariant"]]
        assert invariant["baseline_definition_sha256"]
        assert invariant["assertions_before"] or invariant["calls_before"]
        assert invariant["assertions_after"] or invariant["calls_after"]
        assert item["case"] == node.partition("[")[2].removesuffix("]")
        if item["disposition"] == "retired_legacy_positive":
            assert item["refusal_node"] in collected
            assert item["justification"]
    cases = report["development_cases"]
    assert len(cases) == 150
    assert len({(case["corpus"], case["case"]) for case in cases}) == 150
    assert sum(case["target"] == "template_policy" for case in cases) == 3
    pending = [case for case in cases if case["qualification"] == "pending"]
    assert len(pending) == 6
    assert {case["consumer_owner"] for case in pending} == {"M008-S03"}
    assert all(case["replacement_node"] in collected for case in cases if case["qualification"] != "pending")


def _assert_retirement_definitions(report, snapshot):
    """Historical S08 reconciliation only; current behavior has separate tests."""
    assert snapshot["commit"] == "5da6ec674931b8c79ec2dd218be5c751641e366c"
    assert snapshot["report_raw_sha256"] == "e935ac38f54b37f304d10f3aa42d423072415c1685156c4011edb20b34210941"
    definitions = snapshot["definitions"]
    assert len(definitions) == 413
    assert set(definitions) == set(report["invariants"])
    for key, invariant in report["invariants"].items():
        frozen = definitions[key]
        assert hashlib.sha256(frozen["source"].encode()).hexdigest() == invariant["candidate_definition_sha256"]
        assert frozen["decorators"] == invariant["decorators_after"]
        if (invariant["baseline_utf8_definition_sha256"] != invariant["candidate_definition_sha256"]
                or invariant["decorators_before"] != invariant["decorators_after"]):
            assert invariant["native_mapping"]


@pytest.mark.parametrize("change", ["source", "decorators", "missing", "renamed"])
def test_historical_definitions_refuse_tampering(change):
    package = Path(__file__).resolve().parents[1]
    report = json.loads((package / "docs/native_retirement_coverage.json").read_bytes())
    snapshot = json.loads((package / "tests/fixtures/native_retirement_definitions.json").read_bytes())
    definitions = snapshot["definitions"]
    key = next(iter(definitions))
    if change == "source":
        definitions[key]["source"] += "\n# tampered"
    elif change == "decorators":
        definitions[key]["decorators"].append("pytest.mark.skip")
    elif change == "missing":
        del definitions[key]
    else:
        definitions["tests/unknown.py::unknown"] = definitions.pop(key)
    with pytest.raises(AssertionError):
        _assert_retirement_definitions(report, snapshot)
