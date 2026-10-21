"""Native source-bound projection against the immutable development consumer."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
import test_loop
import test_attempts_v2 as attempts
import test_checkpoints_v2 as checkpoints
import test_milestones_v2 as milestones
from test_verification_v2 import consumer  # Reuses the verified 68-member archive.
from frutlups import findings, ledger, verdict, _attempts
from frutlups.ownership import writer_lock

project = test_loop.project


def report(scope="M001-S01", rows="", changes=(), result="needs_work"):
    text = checkpoints.report(rows, result)
    if scope != "M001-S01":
        text = text.replace("M001-S01 round 1", scope + (" round holistic" if "-S" not in scope else " round 1"))
    if changes:
        table = ("## Finding updates\n| source | sha | id | disposition | related |\n"
                 "| --- | --- | --- | --- | --- |\n")
        table += "\n".join("| " + " | ".join(row) + " |" for row in changes) + "\n\n"
        text = text.replace("## Closure Decision", table + "## Closure Decision")
    return text


def save(root, name, text, *, ev="reviewed", by="architect"):
    path = "reviews/" + name + ".md"
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(text.encode())
    parsed = verdict.parse(text, native=True)
    event = dict(schema=ledger.SCHEMA_V2, ev=ev, by=by, report=path,
                 sha=ledger.evidence_sha(text.encode()), verdict=parsed.verdict,
                 open=list(verdict.open_blocking(parsed)))
    return event


def update(source, id, disposition="closed_by_review", related="-"):
    return source["report"], source["sha"], id, disposition, related


def opposite(consumer, root, events, *, candidate=None, current=None, text=None, reconcile=False):
    code = '''
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import _findings as f
q=json.load(sys.stdin); r=Path(sys.argv[2])
try:
    if q['text'] is not None:
        result=f.updates(q['text'])
    elif q['candidate']:
        c=q['candidate']; result=f.validate_record(r,q['events'],c['report'],c['sha'],c['by'],q['current'])
    else:
        rows,diagnostics=f.project(r,q['events'])
        result={'rows':list(rows.values()),'diagnostics':diagnostics}
        if q['reconcile']:
            f.reconcile(r,q['events'])
            result['backlog']=(r/'05_governance/backlog.md').read_bytes().hex()
    print(json.dumps({'result':result}))
except ValueError as exc:
    print(json.dumps({'error':str(exc)}))
'''
    value = subprocess.run([sys.executable, "-B", "-c", code, str(consumer), str(root)],
        input=json.dumps(dict(events=events, candidate=candidate, current=current, text=text,
                             reconcile=reconcile)), capture_output=True, text=True, timeout=30)
    assert value.returncode == 0, value.stderr
    return json.loads(value.stdout)


def test_original_d023_grammar_and_explicit_legacy_retirement(tmp_path, consumer):
    corpus = json.loads((Path(__file__).parent / "fixtures/protocol_v2.json").read_bytes())
    sample = corpus["finding_update"]
    assert findings.updates(sample["text"]) == sample["expected"]
    assert opposite(consumer, tmp_path, [], text=sample["text"])["result"] == sample["expected"]
    # These originals remain legacy evidence, not native positive qualifications.
    assert [case["name"] for case in corpus["legacy_findings"]] == [
        "legacy-architect-waiver", "legacy-same-scope-relist-closure",
        "legacy-cross-scope-relist-does-not-close"]
    for case in corpus["legacy_findings"]:
        with pytest.raises(ValueError, match="unsupported legacy history"):
            findings.project(tmp_path, case["events"])
        assert "unsupported legacy history" in opposite(consumer, tmp_path, case["events"])["error"]


def test_chronological_projection_and_exact_backlog(tmp_path, consumer):
    first = save(tmp_path, "first", report(rows=(
        "| A | P2 | open | original left\\|right |\n"
        "| B | P2 | open | untouched |\n| C | P3 | open | optional |")) + "Finding count: 99\n")
    checkpoint = dict(first, ev="review_checkpoint")
    assert findings.project(tmp_path, [checkpoint]) == ({}, [])
    second = save(tmp_path, "second", report(changes=[update(first, "A", related="B"),
                                                               update(first, "C", "carried")]))
    third = save(tmp_path, "third", report(changes=[update(first, "A", "open"), update(first, "B")]))
    events = [checkpoint, first, first, second, third]
    originals = {(tmp_path / e["report"]): (tmp_path / e["report"]).read_bytes() for e in events}
    rows, diagnostics = findings.project(tmp_path, events)
    assert list(rows.values()) == [
        dict(id="A", severity="P2", disposition="open", summary="original left|right",
             source=dict(path=first["report"], sha=first["sha"]), related=[],
             last_report=third["report"], owner="M001-S01"),
        dict(id="B", severity="P2", disposition="closed_by_review", summary="untouched",
             source=dict(path=first["report"], sha=first["sha"]), related=[],
             last_report=third["report"], owner="M001-S01"),
        dict(id="C", severity="P3", disposition="carried", summary="optional",
             source=dict(path=first["report"], sha=first["sha"]), related=[],
             last_report=second["report"], owner="M001-S01")]
    assert diagnostics == ["Finding count differs from parsed IDs in reviews/first.md"]
    backlog = tmp_path / "05_governance/backlog.md"
    backlog.parent.mkdir()
    old = b"Owner prose\r\n<!-- findings:begin -->\r\nold\r\n<!-- findings:end -->\r\nTail\r\n"
    backlog.write_bytes(old)
    expected = opposite(consumer, tmp_path, events, reconcile=True)["result"]
    assert expected["rows"] == list(rows.values())
    assert expected["diagnostics"] == diagnostics
    backlog.write_bytes(old)
    assert findings.reconcile(tmp_path, events) == diagnostics
    assert backlog.read_bytes().hex() == expected["backlog"]
    assert backlog.read_bytes().startswith(b"Owner prose\n")
    assert backlog.read_bytes().endswith(b"Tail\n")
    before = backlog.stat().st_mtime_ns
    findings.reconcile(tmp_path, events)
    assert backlog.stat().st_mtime_ns == before
    assert all(path.read_bytes() == raw for path, raw in originals.items())


@pytest.mark.parametrize("case", ["partial", "unrelated", "milestone", "current", "close", "relist",
    "unknown", "wrong_sha", "related", "carry", "waiver_row", "waiver_update", "override", "human_waiver"])
def test_record_authority_matches_pinned_consumer(tmp_path, consumer, case):
    first = save(tmp_path, "first", report(rows="| A | P2 | open | first |\n| B | P2 | open | second |"))
    scope, rows, changes, current, by = "M001-S01", "", [], None, "architect"
    if case == "partial": changes = [update(first, "A")]
    if case == "unrelated": scope = "M002-S01"
    if case == "milestone": scope = "M001"
    if case == "current": scope, current = "M002-S01", {"open": ["A"]}
    if case == "close": changes = [update(first, "A"), update(first, "B")]
    if case == "relist": rows = "| A | P2 | closed_by_review | relisted |"
    if case == "unknown": changes = [update(first, "UNKNOWN")]
    if case == "wrong_sha": changes = [update(dict(first, sha="0" * 64), "A")]
    if case == "related": changes = [update(first, "A", related="UNKNOWN")]
    if case == "carry": changes = [update(first, "A", "carried")]
    if case == "waiver_row": rows = "| W | P3 | waived_by_human | forbidden |"
    if case == "waiver_update": changes = [update(first, "A", "waived_by_human")]
    if case == "human_waiver":
        by, changes = "human", [update(first, "A", "waived_by_human"), update(first, "B", "waived_by_human")]
    candidate = save(tmp_path, "candidate", report(scope, rows, changes,
                     "override" if case == "override" else "pass"), by=by)
    expected = opposite(consumer, tmp_path, [first], candidate=candidate, current=current)
    if case in ("unrelated", "close", "human_waiver"):
        assert expected == {"result": []}
        assert findings.validate_record(tmp_path, [first], candidate["report"], candidate["sha"], by, current) == []
    else:
        with pytest.raises(ValueError) as error:
            findings.validate_record(tmp_path, [first], candidate["report"], candidate["sha"], by, current)
        assert str(error.value) == expected["error"]
        assert str(error.value)


def test_checkpoint_waiver_and_source_revalidation(tmp_path, consumer, monkeypatch):
    source = save(tmp_path, "source", report(rows="| A | P3 | waived_by_human | human decision |"), by="human")
    checkpoint = dict(source, ev="review_checkpoint")
    assert findings.project(tmp_path, [checkpoint, source])[0]
    for actor in ("architect", "frutlups"):
        bad = dict(checkpoint, by=actor)
        with pytest.raises(ValueError, match="waiver requires --by human"):
            findings.project(tmp_path, [source, bad])
        assert opposite(consumer, tmp_path, [source, bad])["error"] == "waiver requires --by human"
    projection = findings.Projection(tmp_path)
    ref = source["report"], source["sha"]
    projection.add(ref, source)
    path = tmp_path / source["report"]
    original = path.read_bytes()
    path.write_bytes(original + b"drift")
    with pytest.raises(ValueError, match="evidence drift"):
        projection.add(ref, source)
    path.write_bytes(original)
    key = ledger._file_key
    calls = []
    def changed(path):
        calls.append(path)
        return key(path) + (len(calls),)
    monkeypatch.setattr(ledger, "_file_key", changed)
    with pytest.raises(ValueError, match="evidence drift"):
        projection.add(ref, source)
    assert len(calls) == 2


@pytest.mark.parametrize("old", ["<!-- findings:begin -->", "<!-- findings:end -->\n<!-- findings:begin -->",
    "<!-- findings:other -->", "<!-- findings:begin --><!-- findings:begin --><!-- findings:end -->"])
def test_malformed_backlog_preserves_bytes(tmp_path, consumer, old):
    path = tmp_path / "05_governance/backlog.md"
    path.parent.mkdir()
    path.write_bytes(old.encode())
    expected = opposite(consumer, tmp_path, [], reconcile=True)
    with pytest.raises(ValueError) as error:
        findings.reconcile(tmp_path, [])
    assert str(error.value) == expected["error"]
    assert path.read_bytes() == old.encode()


@pytest.mark.parametrize("kind,profile", [("slice", False), ("slice", True), ("holistic", False), ("holistic", True)])
def test_real_native_updates_retain_sources_and_replay(project, monkeypatch, consumer, kind, profile):
    if kind == "slice":
        root, cfg, model, rows, write = checkpoints.materialize(project, profile, seats=2)
    else:
        root, cfg, model, write = milestones.materialize(project, profile)
    source = write("05_governance/reviews/original.md", report(
        "M001-S01" if kind == "slice" else "M001",
        "| NOTE | P3 | open | retain original |", result="needs_work" if kind == "slice" else "pass").encode())
    original = (root / source["path"]).read_bytes()
    if kind == "slice":
        milestones.append(root, cfg, dict(_attempts.event("reviewed", slice="M001-S01", round=1,
            report=source["path"], sha=source["sha"], verdict="needs_work", open=[]), by="human"))
        issued = next(row for row in rows if row["ev"] == "prompt")
        coded = next(row for row in rows if row["ev"] == "coded")
        verified = next(row for row in rows if row["ev"] == "verified")
        envelope = write("reviews/round2-envelope.json", dict(ledger.read_reference(root, issued["envelope"]), round=2))
        manifest = write("reviews/round2-manifest.json", dict(ledger.read_reference(root, coded["manifest"]), round=2))
        receipt = write("reviews/round2-receipt.json", dict(ledger.read_reference(root,
            dict(path=verified["receipt"], sha=verified["sha"])), round=2, manifest=manifest))
        milestones.append(root, cfg, dict(issued, round=2, envelope=envelope),
                          dict(coded, round=2, manifest=manifest),
                          dict(verified, round=2, receipt=receipt["path"], sha=receipt["sha"]))
    else:
        milestones.append(root, cfg, dict(_attempts.event("artifact", scope="M001", round="holistic",
                          role="holistic_report", **source), by="human"))
    change = (source["path"], source["sha"], "NOTE", "closed_by_review", "-")
    body = report("M001-S01" if kind == "slice" else "M001", changes=[change], result="pass")
    if kind == "slice":
        body = body.replace("round 1", "round 2")
    calls = []
    checkpoints.adapter(monkeypatch, calls, [body, body])
    obj = milestones.runner(root, cfg, model)
    state = checkpoints.step(obj) if kind == "slice" else milestones.step(obj)
    events = ledger.read(root / cfg.ledger)
    projected, diagnostics = findings.project(root, events)
    assert diagnostics == []
    assert len(projected) == 1
    item = next(iter(projected.values()))
    assert item["disposition"] == "closed_by_review"
    assert item["source"] == source
    assert item["summary"] == "retain original"
    assert item["related"] == []
    decision = next(e for e in reversed(events) if e.ev in (ledger.Ev.reviewed, ledger.Ev.holistic_reviewed))
    assert item["last_report"] == decision.data["report"]
    assert findings.updates((root / item["last_report"]).read_text()) == [
        dict(source=source["path"], sha=source["sha"], id="NOTE", disposition="closed_by_review", related=[])]
    assert (root / source["path"]).read_bytes() == original
    expected = opposite(consumer, root, [ledger._event_dict(e) for e in events])["result"]
    assert expected == dict(rows=list(projected.values()), diagnostics=[])
    saved = (root / cfg.ledger).read_bytes()
    with writer_lock(root, model):
        obj._reconcile_findings()
    assert (root / cfg.ledger).read_bytes() == saved
    assert ledger.validate_history(root, events, model) == state
    assert len(calls) == (2 if kind == "slice" else 1)


@pytest.mark.parametrize("after", [False, True])
def test_projection_interruption_recovers_fresh_without_new_events(project, monkeypatch, after):
    root, cfg, model, _, _ = checkpoints.materialize(project, seats=1)
    calls = []
    checkpoints.adapter(monkeypatch, calls, [checkpoints.report("| NOTE | P3 | open | retain |")])
    replace = findings.os.replace
    def interrupted(source, target):
        if after:
            replace(source, target)
        raise OSError("owned projection interruption")
    with monkeypatch.context() as patch:
        patch.setattr(findings.os, "replace", interrupted)
        with pytest.raises(OSError, match="owned projection interruption"):
            checkpoints.step(attempts.internal_loop(root, cfg, model))
    saved = (root / cfg.ledger).read_bytes()
    assert sum(e.ev == ledger.Ev.reviewed for e in ledger.read(root / cfg.ledger)) == 1
    assert len(calls) == 1
    code = '''
import sys
from pathlib import Path
sys.path.insert(0,sys.argv[2])
import conftest,test_attempts_v2 as a
from frutlups import roadmap
from frutlups.ownership import writer_lock
r=Path(sys.argv[1]); m=roadmap.load(r/'roadmap.yaml'); obj=a.internal_loop(r,conftest.offline_config(),m)
with writer_lock(r,m): obj._reconcile_findings()
'''
    result = subprocess.run([sys.executable, "-B", "-c", code, str(root), str(Path(__file__).parent)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert (root / cfg.ledger).read_bytes() == saved
    assert "| NOTE | P3 | open |" in (root / "05_governance/backlog.md").read_text()


def test_unknown_source_refuses_real_writer_without_repeated_dispatch(project, monkeypatch):
    root, cfg, model, _, _ = checkpoints.materialize(project, seats=1)
    body = report(changes=[("reviews/unknown.md", "a" * 64, "UNKNOWN", "closed_by_review", "-")],
                  result="pass")
    calls = []
    checkpoints.adapter(monkeypatch, calls, [body])
    with pytest.raises(ledger.LedgerError, match="unresolved finding reference"):
        checkpoints.step(attempts.internal_loop(root, cfg, model))
    events = ledger.read(root / cfg.ledger)
    assert sum(e.ev == ledger.Ev.review_checkpoint for e in events) == 1
    assert not any(e.ev in (ledger.Ev.reviewed, ledger.Ev.accepted) for e in events)
    saved = (root / cfg.ledger).read_bytes()
    with pytest.raises(ledger.LedgerError, match="unresolved finding reference"):
        checkpoints.step(attempts.internal_loop(root, cfg, model))
    assert (root / cfg.ledger).read_bytes() == saved
    assert len(calls) == 1


def test_historical_and_approval_reject_nonhuman_waiver(project):
    from frutlups import commit
    root, cfg, model, _, write = checkpoints.materialize(project, seats=1)
    source = write("reviews/unauthorized.md", checkpoints.report(
        "| W | P3 | waived_by_human | cannot waive |" ).encode())
    milestones.append(root, cfg, dict(_attempts.event("reviewed", slice="M001-S01", round=1,
        report=source["path"], sha=source["sha"], verdict="pass", open=[]), by="architect"))
    before = (root / cfg.ledger).read_bytes()
    with pytest.raises(ledger.LedgerError, match="waiver requires --by human"):
        ledger.validate_history(root, ledger.read(root / cfg.ledger), model)
    with pytest.raises(ledger.LedgerError, match="waiver requires --by human"):
        commit.approve(root, model, _attempts.event("accepted", slice="M001-S01", round=1), "Accept", False)
    assert (root / cfg.ledger).read_bytes() == before
