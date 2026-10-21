"""Exact archive reader proofs and real owned private lifecycle qualification."""
import copy
import hashlib
import json
import math
import os
import shutil
import sys
import tarfile
import time
from dataclasses import replace
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

from frutlups import _historical as historical, _loop_evidence as evidence
from frutlups import _preflight, _attempts, commit, gitws, ledger, loop, receipt, roadmap
from frutlups.ownership import writer_lock
from test_checker_v2 import checker_pin, checker_project, managed

FIXTURES = Path(__file__).parent / "fixtures"
SELECTION_PATH = "02_analysis/dec03_i_design/r4/example_assessment.json"
SELECTION_SHA = "edb9ae279d75af1d3a6b8acb15ec044dd685a55f0861befc835ac24afd2b0683"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture(scope="module")
def historical_archive():
    raw = (FIXTURES / "historical_eligibility.manifest.json").read_bytes()
    assert sha(raw) == "30537fc5c976b3dda4a6e5f3775f8d5c4e0c01e7de866e53c06bf66f4d7f32cc"
    manifest = json.loads(raw)
    archive = FIXTURES / "historical_eligibility.tar"
    assert archive.stat().st_size == manifest["archive"]["bytes"] == 419840
    assert sha(archive.read_bytes()) == manifest["archive"]["raw_sha256"] == "ac9d88476b016822d71fe7d08ef6370083ccc33426d156ffc77ea4423c8d4030"
    expected = {m["path"]: m for m in manifest["members"]}
    assert len(expected) == len(manifest["members"]) == 25
    with tarfile.open(archive) as source:
        members = source.getmembers()
        assert len(members) == len({m.name for m in members}) == 25
        assert {m.name for m in members} == set(expected)
        for member in members:
            path = PurePosixPath(member.name)
            assert member.isfile() and member.mode == 0o644
            assert expected[member.name]["mode"] == "100644"
            assert not path.is_absolute() and not {"..", "."} & set(path.parts)
            assert "\\" not in member.name and ":" not in member.name
        files = {m.name: source.extractfile(m).read() for m in members}
    for name, data in files.items():
        assert len(data) == expected[name]["bytes"]
        assert sha(data) == expected[name]["raw_sha256"]
    assert len(files[historical.LEDGER].splitlines()) == 201
    assert len(files[historical.LEDGER]) == 235650
    assert sha(files[historical.LEDGER]) == "35b1ad778cf2df5de42145b96decab31408f64856663c5685febf02159e65932"
    return files


@pytest.fixture(scope="module")
def archive_project(historical_archive, tmp_path_factory):
    root = tmp_path_factory.mktemp("historical-reader")
    for name, raw in historical_archive.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    executable = gitws.selected_executable(shutil.which("git"))
    for args in (("init", "-q"), ("config", "user.name", "Owned"),
                 ("config", "user.email", "owned@example.invalid"),
                 ("config", "core.autocrlf", "false"), ("add", "."), ("commit", "-qm", "exact historical reader data")):
        gitws._git(root, *args, executable=executable)
    return root, SimpleNamespace(git=executable, ledger=historical.LEDGER)


def load_archive(project, value=None):
    root, cfg = project
    events = ledger.read(root / cfg.ledger)
    if value is None:
        return historical.load(root, cfg, events, (SELECTION_PATH, SELECTION_SHA))
    # A bound native artifact records a variant's selected bytes in this reader
    # fixture. This partial history is never passed off as executable admission.
    raw = json.dumps(value).encode()
    path = root / "variant.json"
    path.write_bytes(raw)
    event = ledger._parse_event(dict(_attempts.event("artifact", scope="M002-S03", round=3,
        role="evidence", path=path.name, sha=ledger.evidence_sha(raw)), by="architect"))
    try:
        return historical.load(root, cfg, (*events, event), (path.name, sha(raw)))
    finally:
        path.unlink()


def test_exact_archive_three_entries_and_five_chronology_rows(archive_project, historical_archive):
    root, cfg = archive_project
    before = gitws.workspace_witness(root, executable=cfg.git)
    context = load_archive(archive_project)
    assert [e.cap for e in context.entries] == [900, 1200, 2400]
    assert [e.chain[-1] + 1 for e in context.entries] == [66, 131, 161]
    assert len(context.events) == 201
    events = ledger.read(root / cfg.ledger)
    assert [events[e.chain[-1]].slice for e in context.entries] == ["M002-S03", "M004-S01", "M004-S03"]
    for entry, elapsed in zip(context.entries, (632.037, 906.514, 1486.229)):
        support = evidence.accepted_evidence(root, cfg, events, entry.chain[-1], historical_context=context)
        assert ledger.read_reference(root, support["receipt"])["commands"][0]["secs"] == elapsed
        with pytest.raises(ValueError, match="^accepted receipt exceeded frozen deadline$"):
            evidence.accepted_evidence(root, cfg, events, entry.chain[-1])
    original = json.loads(historical_archive[SELECTION_PATH])
    # The five admitted r4 rows: three exact resolutions and both superseded
    # one-shots. These locators supplement the archive's raw occurrence hashes.
    for entry_index, line, expected in ((0, 58, True), (1, 120, True), (1, 113, False),
                                         (2, 153, True), (2, 141, False)):
        value = copy.deepcopy(original)
        row = value["entries"][entry_index]
        raw_line = historical_archive[cfg.ledger].splitlines(keepends=True)[line - 1]
        row["resolution"] = dict(line=line, raw_sha256=sha(raw_line))
        if not expected:
            row["authority"] = json.loads(raw_line)["authority"][:1]
            with pytest.raises(ValueError, match="^historical assessment: superseded resolution$"):
                load_archive(archive_project, value)
        else:
            assert len(load_archive(archive_project, value).entries) == 3
    assert load_archive(archive_project) == context
    assert gitws.workspace_witness(root, executable=cfg.git) == before
    assert all((root / name).read_bytes() == raw for name, raw in historical_archive.items())


@pytest.mark.parametrize("raw", [b'\xef\xbb\xbf{}', b'\xff', b'{} trailing', b'{"a":1,"a":2}',
    b'{"a":NaN}', b'{"a":Infinity}', b'{"a":-Infinity}', b'[' * 7 + b']' * 7,
    b' ' * historical.JSON_LIMIT + b'{}'], ids=['bom','utf8','trailing','duplicate','nan','inf','negative-inf','depth','bytes'])
def test_json_refusals_are_bounded(raw):
    with pytest.raises(ValueError):
        historical._json(raw)


def test_json_exact_bounds_and_string_escapes():
    assert historical._json(b' ' * (historical.JSON_LIMIT - 2) + b'{}') == {}
    assert historical._json(b'[' * 6 + b'0' + b']' * 6) == [[[[[[0]]]]]]
    text = json.dumps({'a': '[{\\"' * 40}).encode()
    assert historical._json(text) == json.loads(text)


@pytest.mark.parametrize("selection", [False, "file", (), ("file",), ("file", "0" * 64, "extra"),
    ["file", "0" * 64], (Path("file"), "0" * 64), ("file", None), ("../file", "0" * 64),
    ("/file", "0" * 64), ("C:/file", "0" * 64), ("file\\a", "0" * 64),
    ("a/./b", "0" * 64), ("a//b", "0" * 64), ("a/stream:x", "0" * 64),
    ("a. ", "0" * 64), ("a" * 513, "0" * 64), ("é" * 257, "0" * 64),
    ("a", "A" * 64), ("a", "0" * 63)])
def test_selection_grammar_refuses_before_read(archive_project, monkeypatch, selection):
    root, cfg = archive_project
    monkeypatch.setattr(historical, "_read", lambda *a, **k: pytest.fail("invalid pair read a file"))
    with pytest.raises(ValueError, match="historical assessment"):
        historical.load(root, cfg, (), selection)


def changed(value, keys, replacement):
    current = value
    for key in keys[:-1]:
        current = current[key]
    if replacement is ...:
        del current[keys[-1]]
    else:
        current[keys[-1]] = replacement


@pytest.mark.parametrize("keys,replacement", [
    (("schema",), "frutlups.historical-eligibility/2"), (("extra",), 1), (("assessment",), {}),
    (("ledger_prefix", "bytes"), True), (("ledger_prefix", "bytes"), 0),
    (("ledger_prefix", "bytes"), historical.PREFIX_LIMIT + 1), (("ledger_prefix", "bytes"), 235649),
    (("ledger_prefix", "raw_sha256"), "0" * 64), (("entries",), []),
    (("entries", 0, "cap_seconds"), True), (("entries", 0, "cap_seconds"), 0),
    (("entries", 0, "cap_seconds"), 900.0), (("entries", 0, "cap_seconds"), 2147483648),
    (("entries", 0, "cap_seconds"), 631), (("entries", 0, "instructions"), {}),
    (("entries", 0, "resolution", "line"), 0), (("entries", 0, "resolution", "line"), 4097),
    (("entries", 0, "resolution", "line"), 58.0), (("entries", 0, "resolution", "line"), True),
    (("entries", 0, "target", "coded"), ...),
    (("entries", 0, "target", "accepted", "raw_sha256"), "0" * 64),
    (("entries", 2, "authority"), []), (("entries", 2, "authority", 0, "sha"), "0" * 64),
    (("assessment", "raw_sha256"), "0" * 64), (("assessment", "path"), "../outside"),
])
def test_all_entry_grammar_and_binding_refusals(archive_project, historical_archive, keys, replacement):
    value = json.loads(historical_archive[SELECTION_PATH])
    changed(value, keys, replacement)
    with pytest.raises(ValueError, match="historical assessment"):
        load_archive(archive_project, value)


@pytest.mark.parametrize("case", ["entries9", "duplicate", "reverse", "authority5", "authority_reverse", "authority_duplicate"])
def test_array_bounds_order_and_duplicates(archive_project, historical_archive, case):
    value = json.loads(historical_archive[SELECTION_PATH])
    if case == "entries9":
        value["entries"] *= 3
    elif case == "duplicate":
        value["entries"].insert(1, value["entries"][0])
    elif case == "reverse":
        value["entries"].reverse()
    elif case == "authority5":
        value["entries"][0]["authority"] *= 5
    elif case == "authority_reverse":
        value["entries"][2]["authority"].reverse()
    else:
        value["entries"][2]["authority"] *= 2
    with pytest.raises(ValueError, match="historical assessment"):
        load_archive(archive_project, value)


def test_raw_and_native_hash_domains(archive_project, historical_archive):
    root, cfg = archive_project
    value = json.loads(historical_archive[SELECTION_PATH])
    ref = value["entries"][0]["authority"][0]
    target = root / ref["path"]
    original = target.read_bytes()
    try:
        target.write_bytes(original.replace(b"\n", b"\r\n"))
        assert ledger.evidence_sha(target.read_bytes()) == ref["sha"]
        assert sha(target.read_bytes()) != ref["sha"]
        context = load_archive(archive_project)
        target.write_bytes(original)
        with pytest.raises(ValueError, match="raw reference drift"):
            context.recheck(root, cfg, ledger.read(root / cfg.ledger))
        target.write_bytes(original + b'\0')
        with pytest.raises(ValueError, match="native evidence hash mismatch"):
            load_archive(archive_project)
    finally:
        target.write_bytes(original)


def test_prefix_and_reference_drift_refuse_without_substitution(archive_project):
    root, cfg = archive_project
    context = load_archive(archive_project)
    events = ledger.read(root / cfg.ledger)
    for relative in (SELECTION_PATH, cfg.ledger, context.references[-1][0]):
        target = root / relative
        raw = target.read_bytes()
        try:
            target.write_bytes(b" " + raw)
            with pytest.raises(ValueError, match="historical assessment"):
                context.recheck(root, cfg, events)
        finally:
            target.write_bytes(raw)
    context.recheck(root, cfg, events)


def test_unrecorded_and_ignored_selection(archive_project, historical_archive):
    root, cfg = archive_project
    target = root / "unrecorded.json"
    target.write_bytes(historical_archive[SELECTION_PATH])
    ignored = root / ".gitignore"
    try:
        with pytest.raises(ValueError, match="unrecorded selection or basis"):
            historical.load(root, cfg, ledger.read(root / cfg.ledger), (target.name, SELECTION_SHA))
        ignored.write_bytes(b"unrecorded.json\n")
        with pytest.raises(ValueError, match="references must be Git-visible"):
            historical.load(root, cfg, ledger.read(root / cfg.ledger), (target.name, SELECTION_SHA))
    finally:
        target.unlink()
        ignored.unlink(missing_ok=True)


def test_native_file_and_path_bounds(archive_project, historical_archive):
    root, cfg = archive_project
    value = json.loads(historical_archive[SELECTION_PATH])
    ref = value["entries"][0]["authority"][0]
    path = root / ref["path"]
    original = path.read_bytes()
    try:
        path.write_bytes(b"x" * historical.FILE_LIMIT)
        assert len(historical._read(root, ref["path"], historical.FILE_LIMIT)) == historical.FILE_LIMIT
        path.write_bytes(b"x" * (historical.FILE_LIMIT + 1))
        with pytest.raises(ValueError, match="file exceeds byte bound"):
            historical._read(root, ref["path"], historical.FILE_LIMIT)
    finally:
        path.write_bytes(original)
    assert historical._path("a/" * 255 + "ab") == "a/" * 255 + "ab"
    assert historical._path("é" * 256) == "é" * 256
    historical._integer(2147483647, 2147483647)


def test_active_receipt_does_not_consult_historical_context(archive_project):
    root, cfg = archive_project
    events = ledger.read(root / cfg.ledger)
    context = load_archive(archive_project)
    support = evidence.accepted_evidence(root, cfg, events, 65, historical_context=context)
    value = ledger.read_reference(root, support["receipt"])
    current = ledger.SliceState("M002-S03", round=3, manifest=support["manifest"])
    class Unusable:
        def cap_for(self, *args):
            pytest.fail("active receipt consulted historical eligibility")
    with pytest.raises(ValueError, match="HEAD/index changed after verification"):
        evidence.review_receipt(root, cfg, events, current, support["contract"], value,
                                historical_context=Unusable(), accepted_index=65)


def append_rows(root, cfg, *rows):
    with (root / cfg.ledger).open("ab") as stream:
        for row in rows:
            stream.write(json.dumps(row).encode() + b"\n")


def owner_event(ev, **values):
    return dict(_attempts.event(ev, **values), by="human")


def materialize_owned(project, *, profile=True):
    """Complete native blocked/resolved/accepted history, using the pinned checker.

    Synthetic historical receipts are assembled before admission, with exact
    native references. No validator, history fold or checker is replaced.
    """
    import test_checkpoints_v2 as checkpoints
    import test_attempts_v2 as attempts
    root, cfg, model, rows, write = checkpoints.materialize(project, profile, seats=1)
    blocked = write("05_governance/blocked-review.md", checkpoints.report(result="blocked").encode())
    append_rows(root, cfg, owner_event("reviewed", slice="M001-S01", round=1,
        report=blocked["path"], sha=blocked["sha"], verdict="blocked", open=[]))
    authority = write("00_brief/historical-authority.md", b"Independently adopted historical comparison only.\n")
    append_rows(root, cfg, owner_event("resolved", scope="M001-S01", reason="Owned reassessment",
                                     authority=[authority]))
    issued = next(row for row in rows if row["ev"] == "prompt")
    verified = next(row for row in rows if row["ev"] == "verified")
    frozen = ledger.envelope(root, issued["envelope"], "M001-S01", 1)
    frozen["round"] = 2
    envelope = write("05_governance/old-envelope.json", frozen)
    manifest = write("05_governance/old-manifest.json", dict(schema="frutlups.manifest/2",
        slice="M001-S01", round=2, changed=[]))
    prompt = write("prompts/for_coding_agent/old.md", b"Owned historical instructions.\n")
    notes = write("05_governance/old-notes.md", b"Bound historical action, with no new-work allowance.\n")
    value = ledger.read_reference(root, dict(path=verified["receipt"], sha=verified["sha"]))
    value.update(round=2, manifest=manifest)
    value["commands"][0]["secs"] = frozen["timeout_seconds"] + 1
    verification = write("05_governance/old-receipt.json", value)
    review = write("05_governance/old-review.md", checkpoints.report().replace("round 1", "round 2").encode())
    append_rows(root, cfg,
        owner_event("prompt", slice="M001-S01", round=2, **prompt, envelope=envelope),
        owner_event("coded", slice="M001-S01", round=2, changed=[], manifest=manifest,
                    notes_path=notes["path"], notes_sha=notes["sha"], result="implemented"),
        owner_event("verified", slice="M001-S01", round=2, receipt=verification["path"], sha=verification["sha"], ok=True),
        owner_event("reviewed", slice="M001-S01", round=2, report=review["path"], sha=review["sha"], verdict="pass", open=[]))
    commit.approve(root, model, _attempts.event("accepted", slice="M001-S01", round=2), "Owned acceptance", False,
                   executable=cfg.git)
    prefix = (root / cfg.ledger).read_bytes()
    lines = prefix.splitlines(keepends=True)
    def occurrence(kind):
        number = next(i for i in range(len(lines) - 1, -1, -1) if json.loads(lines[i])["ev"] == kind)
        return dict(line=number + 1, raw_sha256=sha(lines[number]))
    basis = write("00_brief/historical-basis.md", b"Human permission and consumption judgement; not executable policy.\n")
    assessment = dict(schema="frutlups.historical-eligibility/1",
        ledger_prefix=dict(bytes=len(prefix), raw_sha256=sha(prefix)),
        assessment=dict(path=basis["path"], raw_sha256=sha((root / basis["path"]).read_bytes())),
        entries=[dict(resolution=occurrence("resolved"), target={kind: occurrence(kind) for kind in historical.KINDS},
                      cap_seconds=frozen["timeout_seconds"] + 10, authority=[authority])])
    selected = write("00_brief/historical-selection.json", assessment)
    append_rows(root, cfg, *(owner_event("artifact", scope="M001-S01", round=2, role="evidence", **ref)
                             for ref in (basis, selected)))
    state = ledger.validate_history(root, ledger.read(root / cfg.ledger), model, executable=cfg.git)
    assert state.slices["M001-S01"].step == "accepted"
    assert state.intents == {}
    pair = (selected["path"], sha((root / selected["path"]).read_bytes()))
    return root, cfg, model, pair, write


@pytest.fixture
def owned_history(checker_project):
    return materialize_owned(checker_project)


def controller(root, cfg, model, **kwargs):
    from _native_support import Controller
    return Controller(root, cfg, model, **kwargs)


def test_real_owned_holistic_strict_selected_and_t_consumer(owned_history, monkeypatch):
    import test_checkpoints_v2 as checkpoints
    import test_milestones_v2 as milestones
    root, cfg, model, pair, _ = owned_history
    calls, notices, checks, loads = [], [], [], []
    checkpoints.adapter(monkeypatch, calls, [milestones.report()])
    original_checker, original_load = _preflight._checker, historical.load
    def check(*args, **kwargs):
        result = original_checker(*args, **kwargs)
        checks.append("pinned T checker passed")
        return result
    def load(*args, **kwargs):
        result = original_load(*args, **kwargs)
        loads.append(result)
        return result
    monkeypatch.setattr(_preflight, "_checker", check)
    monkeypatch.setattr(historical, "load", load)
    saved = (root / cfg.ledger).read_bytes()
    immutable = {p: (root / p).read_bytes() for p in evidence.evidence_paths(ledger.read(root / cfg.ledger), root)
                 if (root / p).is_file() and p != "05_governance/backlog.md"}
    obj = controller(root, cfg, model, emit=notices.append)
    assert obj._iteration_owned() == loop.StopReason.internal
    assert "accepted receipt exceeded frozen deadline" in notices[-1]["detail"]
    assert checks == ["pinned T checker passed"]
    assert calls == []
    assert loads == []
    assert (root / cfg.ledger).read_bytes() == saved
    assert obj._iteration_owned(historical_assessment=pair) is None
    assert len(loads) == 1  # same ownership interval, no redundant parser
    assert len(checks) == 2
    assert len(calls) == 1
    state = ledger.validate_history(root, ledger.read(root / cfg.ledger), model, executable=cfg.git)
    assert state.milestones_done == {"M001"}
    assert state.runs["R1"]["consumed"]["jobs"] == 1
    assert len(state.attempts) == 1
    assert not hasattr(obj, "_historical_boundary")
    assert all((root / p).read_bytes() == raw for p, raw in immutable.items())
    # Direct admission runs the real pinned T consumer on unchanged native
    # artifacts; T needs neither a schema change nor the F assessment.
    with _preflight._owned_admission(root, cfg, model, deadline=time.monotonic() + 60):
        pass
    assert len(checks) == 3


def no_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("checker/recovery/provider/reservation/lifecycle effect after refusal")
    from frutlups.seats import claude, pi
    for module, name in ((_preflight, "_checker"), (commit, "recover"),
                         (_attempts.Operation, "__enter__"), (claude.ClaudeSeat, "run"), (pi.PiSeat, "run"),
                         (ledger, "_append_operation"), (ledger, "_append_review"), (ledger, "_append_coding"),
                         (ledger, "_append_approval")):
        monkeypatch.setattr(module, name, forbidden)


def test_selected_no_intent_early_stop_table(checker_project, monkeypatch):
    import yaml
    root, cfg, model, pair, _ = materialize_owned(checker_project, profile=False)
    original_model = (root / "roadmap.yaml").read_bytes()
    original_ledger = (root / cfg.ledger).read_bytes()
    no_effects(monkeypatch)
    loads = []
    original = historical.load
    def load(*args, **kwargs):
        value = original(*args, **kwargs)
        loads.append(value)
        return value
    monkeypatch.setattr(historical, "load", load)
    # All ordinary families run real selection validation. No-intent history
    # ensures the old eager commit-recovery branch cannot mask an early return.
    for family in ("done", "slice", "milestone", "needs_specification"):
        data = yaml.safe_load(original_model)
        if family in ("done", "milestone"):
            data["milestones"][0]["holistic_review"] = False
        elif family == "needs_specification":
            data["milestones"][0]["status"] = "planned"
        (root / "roadmap.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
        current_model = roadmap.load(root / "roadmap.yaml")
        notices = []
        obj = controller(root, cfg, current_model, emit=notices.append)
        if family == "slice":
            obj.until, obj.accepted_this_run = "slice", True
        elif family == "milestone":
            obj.until, obj.target_milestone = "milestone", "M001"
        expected = {"done": loop.StopReason.done, "slice": loop.StopReason.boundary,
                    "milestone": loop.StopReason.boundary, "grant": loop.StopReason.boundary,
                    "needs_specification": loop.StopReason.needs_specification}[family]
        before = gitws.workspace_witness(root, executable=cfg.git)
        prefix = (root / cfg.ledger).read_bytes()
        for selection in (None, pair, (pair[0], "0" * 64)):
            count = len(loads)
            actual = obj._iteration_owned(historical_assessment=selection)
            if selection is None or selection == pair:
                assert actual == expected, (family, notices)
                assert len(loads) == count + (selection is not None)
            else:
                assert actual == loop.StopReason.internal
                assert notices[-1]["detail"] == "ValueError: historical assessment: raw hash mismatch; owner must inspect"
            assert gitws.workspace_witness(root, executable=cfg.git) == before
            assert (root / cfg.ledger).read_bytes() == prefix
        (root / "roadmap.yaml").write_bytes(original_model)
        (root / cfg.ledger).write_bytes(original_ledger)


@pytest.mark.parametrize("safety", ["stop", "unresolved", "deadline", "history", "ownership"])
def test_safety_precedes_invalid_assessment_at_both_private_entries(owned_history, monkeypatch, safety):
    root, cfg, model, pair, _ = owned_history
    no_effects(monkeypatch)
    if safety != "deadline":
        monkeypatch.setattr(historical, "load", lambda *a, **k: pytest.fail("safety refusal inspected assessment"))
    obj = controller(root, cfg, model)
    deadline = time.monotonic() + 60
    if safety == "stop":
        (root / "STOP").write_bytes(b"")
        reason, detail = loop.StopReason.kill_switch, "STOP exists; owner must remove it to resume"
        direct = "STOP exists; the owner must remove it to run"
    elif safety == "unresolved":
        append_rows(root, cfg, _attempts.event("attempt_started", invocation="unresolved",
            scope="M001", round="holistic", role="probe", contract="a" * 64,
            allowance_seconds=1, retry="initial"))
        reason, detail, direct = loop.StopReason.internal, "LedgerError: unresolved invocation prevents admission; owner must inspect", "unresolved invocation prevents admission"
    elif safety == "deadline":
        obj.started = obj.clock() - cfg.max_wall_minutes * 60 - 1
        deadline = time.monotonic() - 1
        # Expiry belongs to the checker, after selection; it is not a read-only
        # precondition. Keep this node as the invalid-selection/expired control.
        reason, detail, direct = loop.StopReason.internal, "ValueError: historical assessment: raw hash mismatch; owner must inspect", "historical assessment: raw hash mismatch"
    elif safety == "history":
        (root / cfg.ledger).write_bytes(b"{malformed}\n")
        reason, detail, direct = loop.StopReason.internal, None, "malformed JSON"
    else:
        from contextlib import contextmanager
        @contextmanager
        def unavailable(*args, **kwargs):
            raise ValueError("another writer owns the repository; status remains available")
            yield
        monkeypatch.setattr(_preflight, "writer_lock", unavailable)
        reason, detail, direct = loop.StopReason.internal, "ValueError: another writer owns the repository; status remains available; owner must inspect", "another writer owns the repository"
    notices = []
    obj.emit = notices.append
    before = gitws.workspace_witness(root, executable=cfg.git)
    prefix = (root / cfg.ledger).read_bytes()
    assert obj._iteration_owned(historical_assessment=(pair[0], "0" * 64)) == reason
    if detail:
        assert notices[-1]["detail"] == detail
    else:
        assert direct in notices[-1]["detail"]
    assert ("historical assessment" in notices[-1]["detail"]) == (safety == "deadline")
    with pytest.raises(ValueError, match=direct):
        with _preflight._owned_admission(root, cfg, model, deadline=deadline,
                                        historical_assessment=(pair[0], "0" * 64)):
            pytest.fail("unsafe admission yielded")
    assert gitws.workspace_witness(root, executable=cfg.git) == before
    assert (root / cfg.ledger).read_bytes() == prefix


def test_grant_boundary_and_exhausted_allowance_order(owned_history, monkeypatch):
    root, cfg, model, pair, _ = owned_history
    prefix = (root / cfg.ledger).read_bytes()
    for exhausted in (False, "jobs", "seconds"):
        (root / cfg.ledger).write_bytes(prefix)
        # A settled overdraw closes the grant. An exhausted open grant refuses
        # only at actual dispatch, after selection and the real checker.
        jobs = 20 if exhausted == "jobs" else 1
        for number in range(jobs):
            append_rows(root, cfg, _attempts.event("attempt_started", invocation=f"used-{number}",
                scope="M001", round="holistic", role="probe", contract=f"{number:064x}",
                allowance_seconds=300 if exhausted == "seconds" else 1, retry="initial"),
                _attempts.event("attempt_finished", invocation=f"used-{number}", status="completed",
                               usage=dict(secs=300 if exhausted == "seconds" else 0 if exhausted else 2, completeness="unknown")))
        state = ledger.validate_history(root, ledger.read(root / cfg.ledger), model, executable=cfg.git)
        assert state.runs["R1"]["remaining"]["jobs"] == (0 if exhausted == "jobs" else 19)
        if exhausted == "seconds":
            assert state.runs["R1"]["remaining"]["seconds"] == 0
        assert state.runs["R1"]["closed"] == (None if exhausted else "overrun")
        before = gitws.workspace_witness(root, executable=cfg.git)
        for selection in (None, pair, (pair[0], "0" * 64)):
            notices = []
            obj = controller(root, cfg, model, emit=notices.append)
            checks = []
            checker = _preflight._checker
            def checked(*args, **kwargs):
                result = checker(*args, **kwargs)
                checks.append(True)
                return result
            with monkeypatch.context() as patch:
                no_effects(patch)
                if exhausted and selection != (pair[0], "0" * 64):
                    patch.setattr(_preflight, "_checker", checked)
                result = obj._iteration_owned(historical_assessment=selection)
            if selection == (pair[0], "0" * 64):
                assert result == loop.StopReason.internal
                assert notices[-1]["detail"] == "ValueError: historical assessment: raw hash mismatch; owner must inspect"
                assert checks == []
            elif exhausted:
                assert result == loop.StopReason.internal
                assert "run allowance exhausted" in notices[-1]["detail"]
                assert "historical assessment" not in notices[-1]["detail"]
                assert checks == [True]
            else:
                assert result == loop.StopReason.boundary
                assert notices[-1]["detail"] == "current grant boundary completed"
                assert checks == []
            assert gitws.workspace_witness(root, executable=cfg.git) == before


def pending_close(project, monkeypatch):
    import test_milestones_v2 as milestones
    root, cfg, model, pair, write = materialize_owned(project, profile=False)
    gitws._git(root, "add", ".", executable=cfg.git)
    gitws._git(root, "commit", "-qm", "owned adopted history", executable=cfg.git)
    report = write("05_governance/close-review.md", milestones.report().encode())
    append_rows(root, cfg,
        owner_event("artifact", scope="M001", round="holistic", role="holistic_report", **report),
        owner_event("holistic_reviewed", milestone="M001", report=report["path"], sha=report["sha"], verdict="pass", open=[]))
    with monkeypatch.context() as patch:
        def interrupted(*args, **kwargs):
            raise RuntimeError("owned interruption before pending recovery")
        patch.setattr(commit, "_recover", interrupted)
        with pytest.raises(RuntimeError, match="owned interruption"):
            commit.approve(root, model, _attempts.event("milestone_done", milestone="M001", holistic_report=report["path"]),
                           "Owned close", True, executable=cfg.git)
    assert commit.status(root, ledger.read(root / cfg.ledger), model, executable=cfg.git)[-1]["state"] == "pending"
    return root, cfg, model, pair


def test_pending_recovery_invalid_selected_then_valid_exact_recovery(checker_project, monkeypatch):
    root, cfg, model, pair = pending_close(checker_project, monkeypatch)
    saved = (root / cfg.ledger).read_bytes()
    before = gitws.workspace_witness(root, executable=cfg.git)
    with monkeypatch.context() as patch:
        no_effects(patch)
        with pytest.raises(ValueError, match="^historical assessment: raw hash mismatch$"):
            with _preflight._owned_admission(root, cfg, model, deadline=time.monotonic() + 60,
                                            historical_assessment=(pair[0], "0" * 64)):
                pytest.fail("invalid pending admission yielded")
        notices = []
        obj = controller(root, cfg, model, emit=notices.append)
        assert obj._iteration_owned(historical_assessment=(pair[0], "0" * 64)) == loop.StopReason.internal
        assert notices[-1]["detail"] == "ValueError: historical assessment: raw hash mismatch; owner must inspect"
        assert gitws.workspace_witness(root, executable=cfg.git) == before
        assert (root / cfg.ledger).read_bytes() == saved
    order = []
    checker, recover = _preflight._checker, commit.recover
    def checked(*args, **kwargs):
        result = checker(*args, **kwargs)
        order.append("checker")
        return result
    def recovered(*args, **kwargs):
        assert order[-1] == "checker"
        result = recover(*args, **kwargs)
        order.append("recovery")
        return result
    monkeypatch.setattr(_preflight, "_checker", checked)
    monkeypatch.setattr(commit, "recover", recovered)
    with _preflight._owned_admission(root, cfg, model, deadline=time.monotonic() + 60,
                                    historical_assessment=pair) as (_, loaded, events):
        assert loaded.autonomy is None
        assert not ledger.fold(events, loaded).attempts
    assert order == ["checker", "recovery"]
    head = gitws.head(root, executable=cfg.git)
    assert head != before["head"]
    with _preflight._owned_admission(root, cfg, model, deadline=time.monotonic() + 60,
                                    historical_assessment=pair):
        pass
    assert order == ["checker", "recovery", "checker"]
    assert gitws.head(root, executable=cfg.git) == head
    assert (root / cfg.ledger).read_bytes() == saved


def test_fresh_interpreter_requires_pair_for_historical_consumer(owned_history, tmp_path):
    root, cfg, model, pair, _ = owned_history
    prefix = (root / cfg.ledger).read_bytes()
    before = gitws.workspace_witness(root, executable=cfg.git)
    code = r'''
import json,sys,time
from pathlib import Path
from frutlups import _preflight,_historical,ledger,roadmap,_loop_evidence as evidence
from frutlups.config import Config
root=Path(sys.argv[1]); pair=tuple(json.loads(sys.argv[2])) if sys.argv[2]!='null' else None
cfg=Config.load(root); model=roadmap.load(root/'roadmap.yaml')
with _preflight._owned_history(root,cfg,model,deadline=time.monotonic()+60,selected=pair is not None) as owned:
 context=_preflight._select_historical(owned,pair) if pair else None
 cfg,model,events=_preflight._admit_checked(owned,context)
 state=ledger.validate_history(root,events,model,executable=cfg.git)
 try:
  support=evidence.holistic_support(root,cfg,model,events,model.milestones[0],state,historical_context=context)
 except ValueError as exc:
  assert pair is None and str(exc)=='accepted receipt exceeded frozen deadline'
  print('strict')
 else:
  assert pair is not None and len(support['accepted'])==1
  print('selected')
'''
    env = gitws.controlled_environment(os.environ)
    env["TEMPLATE_GIT_EXECUTABLE"] = cfg.git
    for index, selection in enumerate((pair, None, pair)):
        result = managed([sys.executable, "-B", "-c", code, str(root), json.dumps(selection)],
                         root, env, tmp_path / f"restart-{index}")
        assert result.exit == 0, result.stderr_path.read_text()
        assert result.stdout_path.read_text().strip() == ("selected" if selection else "strict")
        assert (root / cfg.ledger).read_bytes() == prefix
        assert gitws.workspace_witness(root, executable=cfg.git) == before


def test_checker_boundary_drift_refuses_before_pending_recovery(checker_project, monkeypatch):
    root, cfg, model, pair = pending_close(checker_project, monkeypatch)
    events = ledger.read(root / cfg.ledger)
    selection = json.loads((root / pair[0]).read_bytes())
    paths = [pair[0], selection["assessment"]["path"], cfg.ledger, "append", "STOP"]
    checker = _preflight._checker
    for relative in paths:
        before = gitws.workspace_witness(root, executable=cfg.git)
        original = (root / relative).read_bytes() if relative not in ("append", "STOP") else None
        prefix = (root / cfg.ledger).read_bytes()
        calls = []
        with monkeypatch.context() as patch:
            def drift(*args, **kwargs):
                result = checker(*args, **kwargs)
                calls.append("checker")
                if relative == "append":
                    append_rows(root, cfg, owner_event("note", text="Owned boundary drift"))
                elif relative == "STOP":
                    (root / "STOP").write_bytes(b"")
                else:
                    (root / relative).write_bytes(b" " + original)
                return result
            patch.setattr(_preflight, "_checker", drift)
            patch.setattr(commit, "recover", lambda *a, **k: pytest.fail("recovery after historical drift"))
            try:
                message = ("admission history changed" if relative == "append" else
                           "STOP exists" if relative == "STOP" else
                           "historical assessment: raw (reference|ledger prefix) drift")
                with pytest.raises(ValueError, match=message):
                    with _preflight._owned_admission(root, cfg, model, deadline=time.monotonic() + 60,
                                                    historical_assessment=pair):
                        pytest.fail("drifting admission yielded")
                assert calls == ["checker"]
            finally:
                if relative == "append":
                    (root / cfg.ledger).write_bytes(prefix)
                elif relative == "STOP":
                    (root / "STOP").unlink()
                else:
                    (root / relative).write_bytes(original)
        assert gitws.workspace_witness(root, executable=cfg.git) == before
        assert commit.status(root, events, model, executable=cfg.git)[-1]["state"] == "pending"


def reopen_new_round(root, cfg, model, write, *, elapsed=None, accept=True, timeout=1):
    import test_checkpoints_v2 as checkpoints
    import test_milestones_v2 as milestones
    events = ledger.read(root / cfg.ledger)
    old_prefix = (root / cfg.ledger).read_bytes()
    close = write("05_governance/first-holistic.md", milestones.report().encode())
    append_rows(root, cfg,
        owner_event("artifact", scope="M001", round="holistic", role="holistic_report", **close),
        owner_event("holistic_reviewed", milestone="M001", report=close["path"], sha=close["sha"], verdict="pass", open=[]))
    commit.approve(root, model, _attempts.event("milestone_done", milestone="M001", holistic_report=close["path"]),
                   "Close old cycle", False, executable=cfg.git)
    state = ledger.validate_history(root, ledger.read(root / cfg.ledger), model, executable=cfg.git)
    assert state.milestones_done == {"M001"}
    assert state.runs["R1"]["closed"] == "boundary"
    append_rows(root, cfg, owner_event("reopened", slice="M001-S01", round=3, reason="Owned new round"))
    authority = write("00_brief/new-work.md", b"Fresh strict work under a new finite grant.\n")
    grant = ledger._event_dict(events[0])
    grant.update(run="R2", previous=ledger._thaw(state.run_head), authority=[authority])
    append_rows(root, cfg, grant)
    issued = next(e for e in reversed(events) if e.ev == ledger.Ev.prompt)
    frozen = ledger.envelope(root, ledger._thaw(issued.data["envelope"]), "M001-S01", 2)
    frozen.update(round=3, timeout_seconds=timeout)
    envelope = write("05_governance/new-envelope.json", frozen)
    manifest = write("05_governance/new-manifest.json", dict(schema="frutlups.manifest/2", slice="M001-S01", round=3, changed=[]))
    prompt = write("prompts/for_coding_agent/new.md", b"New strict work.\n")
    notes = write("05_governance/new-notes.md", b"New round, no historical permission.\n")
    append_rows(root, cfg,
        owner_event("prompt", slice="M001-S01", round=3, **prompt, envelope=envelope),
        owner_event("coded", slice="M001-S01", round=3, changed=[], manifest=manifest,
                    notes_path=notes["path"], notes_sha=notes["sha"], result="implemented"))
    if accept:
        # Genuine fresh verification, or a separately bound over-cap historical
        # candidate for the negative consumer case. Never tamper after selection.
        if elapsed is None:
            item = model.milestones[0].slices[0]
            result = receipt.run(root, item, 3, model, cfg, False)
            assert result.ok
            from dataclasses import asdict
            value = json.loads(json.dumps(asdict(result)))
        else:
            old = next(e for e in reversed(events) if e.ev == ledger.Ev.verified)
            value = ledger.read_reference(root, dict(path=old.data["receipt"], sha=old.data["sha"]))
            value.update(round=3, manifest=manifest)
            value["commands"][0]["secs"] = elapsed
        verification = write("05_governance/new-receipt.json", value)
        review = write("05_governance/new-review.md", checkpoints.report().replace("round 1", "round 3").encode())
        append_rows(root, cfg,
            owner_event("verified", slice="M001-S01", round=3, receipt=verification["path"], sha=verification["sha"], ok=True),
            owner_event("reviewed", slice="M001-S01", round=3, report=review["path"], sha=review["sha"], verdict="pass", open=[]))
        commit.approve(root, model, _attempts.event("accepted", slice="M001-S01", round=3), "Accept new round", False,
                       executable=cfg.git)
    assert (root / cfg.ledger).read_bytes().startswith(old_prefix)
    return ledger.validate_history(root, ledger.read(root / cfg.ledger), model, executable=cfg.git)


@pytest.mark.parametrize("new_elapsed", [None, 2])
def test_reopened_old_and_new_round_visit_exact_occurrences(owned_history, monkeypatch, new_elapsed):
    import test_checkpoints_v2 as checkpoints
    import test_milestones_v2 as milestones
    root, cfg, model, pair, write = owned_history
    prefix = (root / cfg.ledger).read_bytes()
    reopen_new_round(root, cfg, model, write, elapsed=new_elapsed)
    calls, visits, notices = [], [], []
    checkpoints.adapter(monkeypatch, calls, [milestones.report()])
    original = evidence.review_receipt
    def reviewed(*args, **kwargs):
        if kwargs.get("historical"):
            visits.append((args[3].round, kwargs["accepted_index"], kwargs.get("historical_context") is not None))
        return original(*args, **kwargs)
    monkeypatch.setattr(evidence, "review_receipt", reviewed)
    obj = controller(root, cfg, model, emit=notices.append)
    result = obj._iteration_owned(historical_assessment=pair)
    # Initial full selection validation visits the old entry, then actual
    # holistic support visits both old and new accepted occurrences.
    assert [v[0] for v in visits] == [2, 2, 3]
    assert visits[0][1] == visits[1][1]
    assert visits[2][1] > visits[1][1]
    assert all(v[2] for v in visits)
    if new_elapsed is None:
        assert result is None
        assert len(calls) == 1
        state = ledger.validate_history(root, ledger.read(root / cfg.ledger), model, executable=cfg.git)
        assert state.milestones_done == {"M001"}
        assert state.runs["R2"]["consumed"]["jobs"] == 1
    else:
        assert result == loop.StopReason.internal
        assert notices[-1]["detail"] == "M001: LedgerError: accepted receipt exceeded frozen deadline; owner must inspect"
        assert calls == []
    assert (root / cfg.ledger).read_bytes().startswith(prefix)


def test_new_active_reservation_and_settlement_stay_strict(owned_history):
    root, cfg, model, pair, write = owned_history
    state = reopen_new_round(root, cfg, model, write, accept=False, timeout=5)
    obj = controller(root, cfg, model)
    assert obj._iteration_owned(historical_assessment=pair) is None
    verified_state = ledger.validate_history(root, ledger.read(root / cfg.ledger), model, executable=cfg.git)
    assert verified_state.slices["M001-S01"].step == "reviewing"
    verification = list(verified_state.attempts.values())[-1]
    assert verification["start"]["role"] == "verification"
    assert verification["start"]["allowance_seconds"] == 5
    assert verification["finish"]["status"] == "completed"
    assert verification["finish"]["usage"]["secs"] <= 5
    # Selection grants no reservation or refund; a later settled overdraw closes
    # the grant through the unchanged operation accounting.
    before = ledger.read(root / cfg.ledger)
    with _preflight._owned_history(root, cfg, model, deadline=time.monotonic() + 60, selected=True) as owned:
        context = _preflight._select_historical(owned, pair)
        assert context.entries[0].cap == 20  # fixture cap_seconds: frozen 10 + 10
        ticks = iter([0, 21])
        with _attempts.Operation(root, cfg, model, "probe", context.entries[0].cap,
                scope="M001-S01", round_no=3, clock=lambda: next(ticks)) as operation:
            # The 5-second envelope caps verification only; the probe keeps its own 20.
            assert operation.timeout == 20
            operation.finish(True)  # 21 seconds: a genuine overrun of the real reservation
    after = ledger.validate_history(root, ledger.read(root / cfg.ledger), model, executable=cfg.git)
    assert after.runs["R2"]["closed"] == "overrun"
    assert after.runs["R2"]["consumed"]["seconds"] == verified_state.runs["R2"]["consumed"]["seconds"] + 21
    assert len(after.attempts) == len(state.attempts) + 2
    assert ledger.read(root / cfg.ledger)[:len(before)] == before


@pytest.mark.parametrize("case", ["round", "scope", "actor", "late_resolution", "superseded_coded",
    "failed", "partial", "notes_missing", "notes_hash", "command", "runtime", "witness", "manifest",
    "elapsed_bool", "elapsed_negative", "elapsed_nan", "elapsed_inf", "in_cap", "over_cap", "report"])
def test_native_binding_and_integrity_table(archive_project, historical_archive, case):
    root, cfg = archive_project
    value = json.loads(historical_archive[SELECTION_PATH])
    lines = historical_archive[cfg.ledger].splitlines(keepends=True)
    rows = [json.loads(line) for line in lines]
    changed_files = {}
    def write(path, raw):
        changed_files.setdefault(path, (root / path).read_bytes())
        (root / path).write_bytes(raw)
    if case == "round":
        rows[59]["round"] = 4
    elif case == "scope":
        rows[57]["scope"] = "M002"
    elif case == "actor":
        rows[57]["by"] = "frutlups"
    elif case == "late_resolution":
        rows[63] = rows[57]
        value["entries"][0]["resolution"]["line"] = 64
    elif case == "superseded_coded":
        rows[63] = rows[59]
    elif case == "failed":
        rows[60]["ok"] = False
    elif case == "partial":
        rows[59]["result"] = "partial"
    elif case == "notes_missing":
        rows[59].pop("notes_sha")
    elif case == "notes_hash":
        rows[59]["notes_sha"] = "0" * 64
    elif case == "report":
        target = rows[64]["report"]
        raw = (root / target).read_bytes().replace(b"Verdict: pass", b"Verdict: needs_work")
        write(target, raw)
        rows[64]["sha"] = ledger.evidence_sha(raw)
    else:
        target = rows[60]["receipt"]
        result = json.loads((root / target).read_bytes())
        if case == "command":
            result["commands"][0]["argv"] = ["wrong"]
        elif case == "runtime":
            result["runtime"]["sha"] = "0" * 64
        elif case == "witness":
            result["witness"]["stable"] = False
        elif case == "manifest":
            result["manifest"]["sha"] = "0" * 64
        else:
            result["commands"][0]["secs"] = {"elapsed_bool": True, "elapsed_negative": -1,
                "elapsed_nan": float("nan"), "elapsed_inf": float("inf"), "in_cap": 600, "over_cap": 901}[case]
        raw = json.dumps(result).encode()
        write(target, raw)
        rows[60]["sha"] = ledger.evidence_sha(raw)
    # Rebind deliberate variants completely, so refusals test semantic checks,
    # not an incidental stale digest. All original archive bytes are restored.
    updated = [json.dumps(row).encode() + b"\n" for row in rows]
    for entry in value["entries"]:
        for ref in (entry["resolution"], *entry["target"].values()):
            ref["raw_sha256"] = sha(updated[ref["line"] - 1])
    raw = b"".join(updated)
    value["ledger_prefix"] = dict(bytes=len(raw), raw_sha256=sha(raw))
    write(cfg.ledger, raw)
    try:
        with pytest.raises(ValueError):
            load_archive(archive_project, value)
    finally:
        for relative, raw in changed_files.items():
            (root / relative).write_bytes(raw)


def test_prefix_byte_line_and_cap_exact_limits(archive_project, historical_archive):
    root, cfg = archive_project
    original = historical_archive[cfg.ledger]
    value = json.loads(historical_archive[SELECTION_PATH])
    try:
        for raw, valid in ((original + b"\n" * (4096 - 201), True),
                           (original + b"\n" * (4097 - 201), False),
                           (b" " * (historical.PREFIX_LIMIT - len(original)) + original, True)):
            (root / cfg.ledger).write_bytes(raw)
            value["ledger_prefix"] = dict(bytes=len(raw), raw_sha256=sha(raw))
            value["entries"][0]["cap_seconds"] = 2147483647
            if valid:
                assert load_archive(archive_project, value).entries[0].cap == 2147483647
            else:
                with pytest.raises(ValueError, match="prefix line count exceeds bound"):
                    load_archive(archive_project, value)
    finally:
        (root / cfg.ledger).write_bytes(original)


def test_public_and_caller_context_refusals(owned_history):
    root, cfg, model, pair, _ = owned_history
    events = ledger.read(root / cfg.ledger)
    context = historical.load(root, cfg, events, pair)
    saved = (root / cfg.ledger).read_bytes()
    witness = gitws.workspace_witness(root, executable=cfg.git)
    shape = ("historical assessment: selection must be one (path, raw_sha256) pair",)
    for value in (context, {"path": pair[0], "raw_sha256": pair[1]}, (pair, pair)):
        with pytest.raises(ValueError, match="selection must be one"):
            with _preflight._owned_admission(root, cfg, model, deadline=time.monotonic() + 60,
                                            historical_assessment=value):
                pytest.fail("caller context accepted")
        # The public entry points accept only the same raw pair.
        assert loop.preflight(root, cfg, model, historical_assessment=value) == shape
        assert loop.run(root, cfg, model, emit=lambda _: None,
                        historical_assessment=value) == loop.StopReason.preflight_failed
        with pytest.raises(ValueError, match="selection must be one"):
            loop.Loop(root, cfg, model, historical_assessment=value)
    with pytest.raises(TypeError):
        _preflight._owned_admission(root, cfg, model, deadline=time.monotonic() + 60, historical_context=context)
    assert loop.preflight(root, cfg, model, historical_assessment=(pair[0], "0" * 64)) == (
        "historical assessment: raw hash mismatch",)
    obj = controller(root, cfg, model)
    with pytest.raises(ValueError, match="ledger append is not enabled"):
        obj.append("note")
    # Without a selection the public run stays strict: no probe, seat or history effect.
    notices = []
    assert loop.run(root, cfg, model, emit=notices.append) == loop.StopReason.internal
    assert "accepted receipt exceeded frozen deadline" in notices[-1]["detail"]
    assert (root / cfg.ledger).read_bytes() == saved
    after = gitws.workspace_witness(root, executable=cfg.git)
    assert (after["head"], after["index"]) == (witness["head"], witness["index"])
    # Only the generated findings projection is reconciled, as in the private story.
    from frutlups import findings
    changed = {path for path in set(after["files"]) | set(witness["files"])
               if after["files"].get(path) != witness["files"].get(path)}
    assert changed <= {"05_governance/backlog.md"}
    assert findings.matches_backlog(root, ledger.read(root / cfg.ledger))
    assert not (root / "local_state/frutlups/jobs").exists()


def test_obsolete_product_record_is_not_selection_ownership(archive_project, historical_archive):
    root, cfg = archive_project
    raw = historical_archive[SELECTION_PATH]
    relative = "obsolete-selection.json"
    target = root / relative
    target.write_bytes(raw)
    events = ledger.read(root / cfg.ledger)
    def record(kind, digest):
        return ledger._parse_event(owner_event("prompt", slice="M002-S03", round=4,
            path="prompts/for_coding_agent/018_M002-S03_r3.md", sha="a" * 64,
            envelope=dict(path="envelope.json", sha="b" * 64),
            baseline=[dict(path=relative, sha=digest, kind=kind)]))
    try:
        for last in (record("deleted", ledger.evidence_sha(raw)), record("modified", "0" * 64)):
            with pytest.raises(ValueError, match="unrecorded selection or basis"):
                historical.load(root, cfg, (*events, record("added", ledger.evidence_sha(raw)), last),
                                (relative, sha(raw)))
    finally:
        target.unlink()


def test_stop_arriving_after_checker_keeps_loop_safety_classification(owned_history, monkeypatch):
    root, cfg, model, pair, _ = owned_history
    original = _preflight._checker
    checks = []
    def checked(*args, **kwargs):
        original(*args, **kwargs)
        checks.append(True)
        (root / "STOP").write_bytes(b"")
    monkeypatch.setattr(_preflight, "_checker", checked)
    monkeypatch.setattr(commit, "recover", lambda *a, **k: pytest.fail("recovery after STOP"))
    monkeypatch.setattr(loop.Loop, "_call", lambda *a, **k: pytest.fail("provider after STOP"))
    prefix = (root / cfg.ledger).read_bytes()
    notices = []
    obj = controller(root, cfg, model, emit=notices.append)
    assert obj._iteration_owned(historical_assessment=pair) == loop.StopReason.kill_switch
    assert notices[-1]["detail"] == "STOP exists; owner must remove it to resume"
    assert checks == [True]
    assert (root / cfg.ledger).read_bytes() == prefix


def test_seat_boundary_preserves_drift_and_unresolved_current_invocation(owned_history, monkeypatch):
    import test_checkpoints_v2 as checkpoints
    import test_milestones_v2 as milestones
    root, cfg, model, pair, _ = owned_history
    calls, notices = [], []
    prefix = (root / cfg.ledger).read_bytes()
    def mutate(job, config):
        (root / pair[0]).write_bytes((root / pair[0]).read_bytes() + b"\n")
    checkpoints.adapter(monkeypatch, calls, [milestones.report()], mutate=mutate)
    obj = controller(root, cfg, model, emit=notices.append)
    assert obj._iteration_owned(historical_assessment=pair) == loop.StopReason.path_violation
    assert len(calls) == 1
    assert (root / pair[0]).read_bytes().endswith(b"\n")
    assert (root / cfg.ledger).read_bytes().startswith(prefix)
    state = ledger.fold(ledger.read(root / cfg.ledger), model)
    assert len(state.attempts) == 1
    assert next(iter(state.attempts.values()))["finish"] is None
    assert state.checkpoints == {}
    assert state.milestones_done == set()


def test_real_common_directory_ownership_precedes_selection(owned_history, monkeypatch):
    import threading
    root, cfg, model, pair, _ = owned_history
    entered, release = threading.Event(), threading.Event()
    errors = []
    def hold():
        try:
            with writer_lock(root, model, executable=cfg.git):
                entered.set()
                assert release.wait(30)
        except BaseException as exc:
            errors.append(exc)
    worker = threading.Thread(target=hold)
    worker.start()
    try:
        assert entered.wait(5)
        no_effects(monkeypatch)
        monkeypatch.setattr(historical, "load", lambda *a, **k: pytest.fail("selection before ownership"))
        notices = []
        obj = controller(root, cfg, model, emit=notices.append)
        assert obj._iteration_owned(historical_assessment=pair) == loop.StopReason.internal
        assert notices[-1]["detail"] == "ValueError: another writer owns the repository; status remains available; owner must inspect"
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert errors == []


@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize("stage", ["seat", "verification", "verification_overrun"])
def test_profile_completion_past_admission_clock(owned_history, monkeypatch, selected, stage):
    import test_checkpoints_v2 as checkpoints
    root, cfg, model, pair, write = owned_history
    reopen_new_round(root, cfg, model, write, accept=False, timeout=5)
    if stage == "seat":
        assert controller(root, cfg, model)._iteration_owned() is None
    before = ledger.read(root / cfg.ledger)
    previous = ledger.validate_history(root, before, model, executable=cfg.git)
    admission_clock, operation_clock = [100], [0]
    init = _attempts.Operation.__init__
    def timed_operation(self, *args, **kwargs):
        init(self, *args, **dict(kwargs, clock=lambda: operation_clock[0]))
    monkeypatch.setattr(_attempts.Operation, "__init__", timed_operation)
    def completed():
        admission_clock[0] = 111  # checker deadline is 110, operation has its own clock
        operation_clock[0] = 6 if stage == "verification_overrun" else 2
    calls = []
    if stage == "seat":
        checkpoints.adapter(monkeypatch, calls, [checkpoints.report().replace("round 1", "round 3")],
                            mutate=lambda *args: completed())
    else:
        run = receipt.run
        def verified(*args, **kwargs):
            result = run(*args, **kwargs)
            assert result.ok
            completed()
            return result
        monkeypatch.setattr(receipt, "run", verified)
    notices = []
    obj = controller(root, cfg, model, clock=lambda: admission_clock[0], emit=notices.append)
    obj.started = 110 - cfg.max_wall_minutes * 60
    result = obj._iteration_owned(historical_assessment=pair if selected else None)
    events = ledger.read(root / cfg.ledger)
    state = ledger.validate_history(root, events, model, executable=cfg.git)
    new = [a for key, a in state.attempts.items() if key not in previous.attempts]
    assert len(new) == 1
    attempt = new[0]
    # Verification keeps the frozen 5-second envelope; the seat keeps the fixture's 30-second reviewer limit.
    assert attempt["start"]["allowance_seconds"] == (30 if stage == "seat" else 5)
    assert admission_clock[0] > obj.started + cfg.max_wall_minutes * 60
    assert events[:len(before)] == before
    if stage == "verification_overrun":
        assert result == loop.StopReason.verification_error
        assert "verification completion exceeded reserved deadline" in notices[-1]["detail"]
        assert attempt["finish"] is None
        assert state.slices["M001-S01"].step == "verifying"
        assert not any(e.ev == ledger.Ev.verified for e in events[len(before):])
    else:
        assert result is None, notices
        assert attempt["finish"]["status"] == "completed"
        assert attempt["finish"]["usage"]["secs"] == 2
        assert all(a["finish"] is not None for a in state.attempts.values())
        assert sum(e.ev == ledger.Ev.attempt_finished for e in events[len(before):]) == 1
        refs = attempt["finish"]["evidence"]
        assert len(refs) == (2 if stage == "seat" else 1)
        for ref in refs:
            assert ledger.evidence_sha((root / ref["path"]).read_bytes()) == ref["sha"]
        assert state.runs["R2"]["consumed"]["jobs"] == previous.runs["R2"]["consumed"]["jobs"] + (stage == "seat")
        assert state.runs["R2"]["consumed"]["seconds"] == previous.runs["R2"]["consumed"]["seconds"] + 2
        assert state.slices["M001-S01"].step == ("accept_pending" if stage == "seat" else "reviewing")
        if stage == "seat":
            assert len(calls) == 1
            assert len(state.checkpoints) == 1
        else:
            verified = [e for e in events[len(before):] if e.ev == ledger.Ev.verified]
            assert len(verified) == 1
            assert verified[0].data["ok"] is True
            assert verified[0].data["receipt"] == refs[0]["path"]
            assert verified[0].data["sha"] == refs[0]["sha"]


def exhaust_open_run(root, cfg, model):
    state = ledger.validate_history(root, ledger.read(root / cfg.ledger), model, executable=cfg.git)
    remaining = state.runs[state.run_head["run"]]["remaining"]["seconds"]
    ticks = iter([0, remaining])
    with _attempts.Operation(root, cfg, model, "probe", remaining,
            scope="M001", round_no="holistic", clock=lambda: next(ticks)) as operation:
        assert operation.timeout == remaining
        operation.finish(True)
    state = ledger.validate_history(root, ledger.read(root / cfg.ledger), model, executable=cfg.git)
    run = state.runs[state.run_head["run"]]
    assert run["remaining"]["seconds"] == 0
    assert run["closed"] is None
    assert all(a["finish"] is not None for a in state.attempts.values())
    return state


@pytest.mark.parametrize("route", ["direct", "loop"])
@pytest.mark.parametrize("selection", ["absent", "valid", "invalid"])
def test_exhausted_profile_pending_recovery(owned_history, monkeypatch, route, selection):
    root, cfg, model, pair, write = owned_history
    gitws._git(root, "add", ".", executable=cfg.git)
    gitws._git(root, "commit", "-qm", "owned adopted history", executable=cfg.git)
    approve = commit.approve
    with monkeypatch.context() as patch:
        def pending(root, model, event, message, want_commit, **kwargs):
            if event["ev"] == "accepted" and event["round"] == 3:
                exhaust_open_run(root, cfg, model)
                def interrupted(*args, **kwargs):
                    raise RuntimeError("owned interruption before recovery")
                patch.setattr(commit, "_recover", interrupted)
                want_commit = True
            return approve(root, model, event, message, want_commit, **kwargs)
        patch.setattr(commit, "approve", pending)
        with pytest.raises(RuntimeError, match="owned interruption"):
            reopen_new_round(root, cfg, model, write, timeout=5)
    saved = (root / cfg.ledger).read_bytes()
    before = gitws.workspace_witness(root, executable=cfg.git)
    state = ledger.validate_history(root, ledger.read(root / cfg.ledger), model, executable=cfg.git)
    assert state.runs["R2"]["remaining"]["seconds"] == 0
    assert state.runs["R2"]["closed"] is None
    assert commit.status(root, ledger.read(root / cfg.ledger), model, executable=cfg.git)[-1]["state"] == "pending"
    supplied = {"absent": None, "valid": pair, "invalid": (pair[0], "0" * 64)}[selection]
    order, notices = [], []
    checker, recover = _preflight._checker, commit.recover
    def checked(*args, **kwargs):
        result = checker(*args, **kwargs)
        order.append("checker")
        return result
    def recovered(*args, **kwargs):
        assert order[-1] == "checker"
        result = recover(*args, **kwargs)
        order.append("recovery")
        return result
    no_effects(monkeypatch)
    if selection != "invalid":
        monkeypatch.setattr(_preflight, "_checker", checked)
        monkeypatch.setattr(commit, "recover", recovered)
    def invoke():
        if route == "direct":
            with _preflight._owned_admission(root, cfg, model, deadline=time.monotonic() + 60,
                                            historical_assessment=supplied):
                pass
        else:
            obj = controller(root, cfg, model, emit=notices.append)
            obj.until, obj.accepted_this_run = "slice", True
            return obj._iteration_owned(historical_assessment=supplied)
    if selection == "invalid":
        if route == "direct":
            with pytest.raises(ValueError, match="^historical assessment: raw hash mismatch$"):
                invoke()
        else:
            assert invoke() == loop.StopReason.internal
            assert notices[-1]["detail"] == "ValueError: historical assessment: raw hash mismatch; owner must inspect"
        assert order == []
        assert gitws.workspace_witness(root, executable=cfg.git) == before
        assert commit.status(root, ledger.read(root / cfg.ledger), model, executable=cfg.git)[-1]["state"] == "pending"
    else:
        assert invoke() == (loop.StopReason.boundary if route == "loop" else None)
        assert order == ["checker", "recovery"]
        head = gitws.head(root, executable=cfg.git)
        assert head != before["head"]
        assert commit.status(root, ledger.read(root / cfg.ledger), model, executable=cfg.git)[-1]["state"] == "completed"
        assert invoke() == (loop.StopReason.boundary if route == "loop" else None)
        assert order == ["checker", "recovery", "checker"]
        assert gitws.head(root, executable=cfg.git) == head
    assert (root / cfg.ledger).read_bytes() == saved
    assert ledger.validate_history(root, ledger.read(root / cfg.ledger), model, executable=cfg.git).runs == state.runs


@pytest.mark.parametrize("selection", ["absent", "valid", "invalid"])
@pytest.mark.parametrize("boundary", ["slice", "done"])
def test_exhausted_profile_early_boundary_after_admission_expiry(owned_history, monkeypatch, selection, boundary):
    root, cfg, model, pair, write = owned_history
    exhaust_open_run(root, cfg, model)
    if boundary == "done":
        import test_milestones_v2 as milestones
        report = write("05_governance/exhausted-close.md", milestones.report().encode())
        append_rows(root, cfg,
            owner_event("artifact", scope="M001", round="holistic", role="holistic_report", **report),
            owner_event("holistic_reviewed", milestone="M001", report=report["path"], sha=report["sha"], verdict="pass", open=[]))
        commit.approve(root, model, _attempts.event("milestone_done", milestone="M001", holistic_report=report["path"]),
                       "Owned completed milestone", False, executable=cfg.git)
    state = ledger.validate_history(root, ledger.read(root / cfg.ledger), model, executable=cfg.git)
    assert state.intents == {}
    assert state.runs["R1"]["remaining"]["seconds"] == 0
    assert state.runs["R1"]["closed"] == (None if boundary == "slice" else "boundary")
    notices = []
    obj = controller(root, cfg, model, clock=lambda: 100, emit=notices.append)
    obj.started = 99 - cfg.max_wall_minutes * 60
    if boundary == "slice":
        obj.until, obj.accepted_this_run = "slice", True
    no_effects(monkeypatch)
    before = gitws.workspace_witness(root, executable=cfg.git)
    saved = (root / cfg.ledger).read_bytes()
    supplied = {"absent": None, "valid": pair, "invalid": (pair[0], "0" * 64)}[selection]
    result = obj._iteration_owned(historical_assessment=supplied)
    expected = loop.StopReason.boundary if boundary == "slice" else loop.StopReason.done
    assert result == (loop.StopReason.internal if selection == "invalid" else expected)
    assert notices[-1]["detail"] == ("ValueError: historical assessment: raw hash mismatch; owner must inspect"
                                    if selection == "invalid" else "one slice accepted" if boundary == "slice"
                                    else "all admitted roadmap milestones completed")
    assert gitws.workspace_witness(root, executable=cfg.git) == before
    assert (root / cfg.ledger).read_bytes() == saved


@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize("phase", ["launch", "completion"])
def test_checker_expiry_remains_at_checker_boundary(owned_history, monkeypatch, selected, phase):
    root, cfg, model, pair, _ = owned_history
    checks = []
    checker = _preflight._checker
    def checked(*args, **kwargs):
        checks.append(True)
        return checker(*args, **kwargs)
    no_effects(monkeypatch)
    monkeypatch.setattr(_preflight, "_checker", checked)
    clock = [100]
    calls = []
    run = _preflight.procs.run_process
    def completed(*args, **kwargs):
        result = run(*args, **kwargs)
        calls.append(True)
        clock[0] = 111
        return result
    monkeypatch.setattr(_preflight.procs, "run_process", completed)
    before = gitws.workspace_witness(root, executable=cfg.git)
    saved = (root / cfg.ledger).read_bytes()
    error = "checker deadline exhausted" if phase == "launch" else "project checker exceeded its deadline"
    with pytest.raises(ValueError, match="^" + error + "$"):
        with _preflight._owned_admission(root, cfg, model, deadline=99 if phase == "launch" else 110,
                                        clock=lambda: clock[0],
                                        historical_assessment=pair if selected else None):
            pytest.fail("expired checker admission yielded")
    assert checks == [True]
    assert calls == ([] if phase == "launch" else [True])
    assert gitws.workspace_witness(root, executable=cfg.git) == before
    assert (root / cfg.ledger).read_bytes() == saved
