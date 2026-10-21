"""Native presentation at actual private issuance and durable review boundaries."""
import hashlib
import json
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

import pytest
import test_loop
import test_attempts_v2 as attempts
import test_outcomes_v2 as outcomes
import test_checkpoints_v2 as checkpoints
import test_milestones_v2 as milestones
from test_verification_v2 import consumer
from frutlups import _attempts, _loop_evidence as evidence, gitws, ledger, loop, receipt, render
from frutlups.seats import pi

project = test_loop.project
# The real argv builder, captured before the project fixture installs the fake CLI.
PI_ARGV = pi._argv


@pytest.mark.parametrize("profile", [False, True])
def test_actual_issuance_native_receipt_and_700_path_review(project, monkeypatch, profile):
    root, cfg, model, rows, write = outcomes.materialize(project, profile)
    item = model.milestones[0].slices[0]
    # The fixture's selected command is portable and short. Issue it normally.
    attempts.save(root, cfg, rows[:1] if profile else [])
    obj = attempts.internal_loop(root, cfg, model)
    issued = outcomes.step(obj).slices[item.id]
    prompt = (root / issued.last_prompt).read_text("utf-8")
    assert render.HASH_RULE in prompt
    assert render.reference_text(ledger._thaw(issued.envelope)) in prompt
    calls = []
    count = 700 if profile else 3
    paths = [f"07_app/file-{i:04}.txt" for i in range(count)]
    def populate(root, cfg, value):
        for i, relative in enumerate(paths):
            (root / relative).write_bytes(b"x" * (2 * 1024 * 1024) if i == 0
                                         else b"late defect: expected false, got true\n" if i == count - 1
                                         else b"small\r\n")
    outcomes.adapter(monkeypatch, calls, change=False, mutate=populate)
    assert outcomes.step(obj).slices[item.id].step == "verifying"
    assert render.prompt_size(calls[0].prompt_path.read_text("utf-8"), "pi") <= 16384
    # The actual Pi argv sends the bound prompt plus exactly the shared framing bytes.
    argv = PI_ARGV(calls[0], cfg)
    framing = render.dispatch_framing("pi")
    assert argv[-2:] == (f"@{calls[0].prompt_path}", framing)
    assert framing.endswith(render.PI_FRAMING) and render.MAIN_FRAMING in framing
    text = calls[0].prompt_path.read_text("utf-8")
    assert render.prompt_size(text, "pi") == len(text.encode("utf-8")) + len(argv[-1].encode("utf-8"))
    if profile:
        assert outcomes.step(obj).slices[item.id].step == "reviewing"
    else:
        with pytest.raises(loop._Stop, match="granted profile"):
            outcomes.step(obj)
        # Ordinary history receives a manual native receipt, not a runner grant.
        value = receipt._run_native(root, item, 1, model, cfg, None, time.monotonic() + 30)
        ref = write("05_governance/reviews/m001/manual-native.json", asdict(value))
        rows = [ledger._event_dict(e) for e in ledger.read(root / cfg.ledger)]
        rows.append(dict(_attempts.event("verified", slice=item.id, round=1,
                         receipt=ref["path"], sha=ref["sha"], ok=True), by="human"))
        attempts.save(root, cfg, rows)
    events = ledger.read(root / cfg.ledger)
    verified = next(e for e in reversed(events) if e.ev == ledger.Ev.verified)
    receipt_ref = dict(path=verified.data["receipt"], sha=verified.data["sha"])
    native = ledger.read_reference(root, receipt_ref, render.SUPPORT_LIMIT)
    assert len(native) == 13
    assert native["schema"] == "frutlups.receipt/2"
    original_receipt = (root / receipt_ref["path"]).read_bytes()
    original_manifest = (root / native["manifest"]["path"]).read_bytes()
    review_calls = []
    checkpoints.adapter(monkeypatch, review_calls)
    assert outcomes.step(obj).slices[item.id].step == "accept_pending"
    assert review_calls
    prompt = review_calls[0].prompt_path.read_text("utf-8")
    assert render.HASH_RULE in prompt
    assert render.reference_text(receipt_ref) in prompt
    assert render.reference_text(native["manifest"]) in prompt
    assert render.prompt_size(prompt, review_calls[0].adapter) <= 49152
    events = ledger.read(root / cfg.ledger)
    support_event = next(e for e in events if e.ev == ledger.Ev.artifact
                         and e.data["path"].endswith("_support.json"))
    support = ledger.read_reference(root, dict(path=support_event.data["path"], sha=support_event.data["sha"]), render.SUPPORT_LIMIT)
    index = json.loads(evidence.bound_text(root, support["index"], render.SUPPORT_LIMIT))
    assert [r["path"] for r in index["paths"]] == paths
    assert index["sources"] == [native["manifest"]]
    stored = []
    for ref in index["pages"]:
        body = evidence.bound_text(root, ref, render.DIFF_LIMIT)
        assert "NOT the complete patch" in body
        stored += json.loads(body.splitlines()[1])
    assert [entry["row"] for entry in stored] == json.loads(original_manifest)["changed"]
    last_page = evidence.bound_text(root, index["pages"][index["paths"][-1]["page"]], render.DIFF_LIMIT)
    assert "late defect: expected false, got true" in last_page
    assert (root / receipt_ref["path"]).read_bytes() == original_receipt
    assert (root / native["manifest"]["path"]).read_bytes() == original_manifest
    assert outcomes.step(obj).slices[item.id].step == "accepted"
    holistic_calls = []
    checkpoints.adapter(monkeypatch, holistic_calls, [milestones.report()])
    assert milestones.step(milestones.runner(root, cfg, model)).milestones_done == {"M001"}
    text = holistic_calls[0].prompt_path.read_text("utf-8")
    assert render.HASH_RULE in text
    assert render.prompt_size(text, holistic_calls[0].adapter) <= 49152


@pytest.mark.parametrize("role,profile,phase", [
    ("reviewer", False, "support"), ("reviewer", True, "checkpoint"),
    ("holistic", False, "support"), ("holistic", True, "checkpoint"),
])
def test_fresh_process_exact_package_reuse(project, monkeypatch, role, profile, phase):
    if role == "reviewer":
        root, cfg, model, _, _ = checkpoints.materialize(project, profile, seats=1)
        step = lambda: checkpoints.step(attempts.internal_loop(root, cfg, model))
        report = checkpoints.report()
    else:
        root, cfg, model, _ = milestones.materialize(project, profile)
        step = lambda: milestones.step(milestones.runner(root, cfg, model))
        report = milestones.report()
    calls = []
    checkpoints.adapter(monkeypatch, calls, [report])
    append = ledger._append_review
    def interrupt(root, path, model, raw, *args, **kwargs):
        if ((phase == "support" and raw["ev"] == "artifact" and raw["path"].endswith("_support.json"))
                or (phase == "checkpoint" and raw["ev"] == "review_checkpoint")):
            raise RuntimeError("owned presentation interruption")
        return append(root, path, model, raw, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(ledger, "_append_review", interrupt)
        with pytest.raises(RuntimeError, match="owned presentation"):
            step()
    prefix = (root / cfg.ledger).read_bytes()
    files = {r.path: (root / r.path).read_bytes() for r in gitws.changed_files(root, executable=cfg.git)
             if r.path.startswith(cfg.reviews_dir + "/") or r.path.startswith(cfg.review_prompt_dir + "/")}
    code = '''
import sys
from pathlib import Path
sys.path.insert(0,sys.argv[2])
import conftest,test_attempts_v2 as a,test_checkpoints_v2 as c,test_milestones_v2 as m
from frutlups import ledger,roadmap
from frutlups.seats import claude,JobResult
r=Path(sys.argv[1]);cfg=conftest.offline_config();model=roadmap.load(r/'roadmap.yaml');calls=[]
role=sys.argv[3]
def run(self,j,cfg):
    calls.append(j.id)
    return JobResult('completed',None,c.report() if role=='reviewer' else m.report(),0,.01,7,9,None,None,r/'local_state/out',r/'local_state/err',None)
claude.ClaudeSeat.run=run
s=c.step(a.internal_loop(r,cfg,model)) if role=='reviewer' else m.step(m.runner(r,cfg,model))
assert len(s.attempts)==1
assert len(s.checkpoints)==1
assert sum(x['finish']['usage']['tokens_in'] for x in s.attempts.values())==7
assert len(calls)==int(sys.argv[4])
if s.runs: assert s.runs['R1']['consumed']['jobs']==1
print('recovered without duplicate dispatch or charge')
'''
    result = subprocess.run([sys.executable, "-B", "-c", code, str(root), str(Path(__file__).parent), role,
                             "1" if phase == "support" else "0"], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert (root / cfg.ledger).read_bytes().startswith(prefix)
    for path, body in files.items():
        assert (root / path).read_bytes() == body
    assert len(calls) == (phase == "checkpoint")


@pytest.mark.parametrize("case", ["tamper", "missing", "neighbor", "ignored", "query"])
def test_review_evidence_refuses_without_dispatch_or_overwrite(project, monkeypatch, case):
    root, cfg, model, _, _ = checkpoints.materialize(project, False, seats=1)
    obj = attempts.internal_loop(root, cfg, model)
    calls = []
    checkpoints.adapter(monkeypatch, calls)
    append = ledger._append_review
    def interrupt(root, path, model, raw, *args, **kwargs):
        if raw["ev"] == "artifact" and raw["path"].endswith("_support.json"):
            raise RuntimeError("owned stop")
        return append(root, path, model, raw, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(ledger, "_append_review", interrupt)
        with pytest.raises(RuntimeError):
            checkpoints.step(obj)
    events = ledger.read(root / cfg.ledger)
    index = next(e.data["path"] for e in events if e.ev == ledger.Ev.artifact
                 and e.data["path"].endswith("_index.json"))
    if case == "tamper":
        (root / index).write_bytes(b"tampered retained bytes")
    elif case == "missing":
        (root / index).unlink()
    elif case == "neighbor":
        (root / index).with_name("unbound-neighbor.md").write_bytes(b"not evidence")
    elif case == "ignored":
        with (root / ".git/info/exclude").open("a", encoding="utf-8") as stream:
            stream.write("\n*_support.json\n")
    else:
        original = gitws._git
        def query(root, *args, **kwargs):
            if "check-ignore" in args:
                return subprocess.CompletedProcess(args, 128, b"", b"unqueryable")
            return original(root, *args, **kwargs)
        monkeypatch.setattr(gitws, "_git", query)
    prefix = (root / cfg.ledger).read_bytes()
    with pytest.raises((ValueError, loop._Stop, FileNotFoundError)):
        checkpoints.step(attempts.internal_loop(root, cfg, model))
    assert not calls
    assert (root / cfg.ledger).read_bytes() == prefix
    if case == "tamper":
        assert (root / index).read_bytes() == b"tampered retained bytes"


@pytest.mark.parametrize("raw", [b"a\nb", b"a\r\nb", b"a\rb", b"a\r\n\0b",
                               b"a" * 65535 + b"\r\nb", b"a\r\n" + b"x" * 65536 + b"\0"],
                         ids=["lf", "crlf", "lone-cr", "nul", "split-boundary", "late-nul"])
def test_pinned_hash_domains_and_presentation(consumer, tmp_path, raw):
    expected = hashlib.sha256(raw if b"\0" in raw else raw.replace(b"\r\n", b"\n")).hexdigest()
    target = tmp_path / "bytes.txt"
    target.write_bytes(raw)
    (tmp_path / "custom.md").write_text("Custom {{objective}}", encoding="utf-8")
    code = '''
import sys,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import _common as c,prompt
r=Path(sys.argv[2]); ref=prompt._ref(r,'bytes.txt')
text=prompt._render(r/'custom.md',{'objective':'bounded task'},complete=True,diagnose=False)
print(json.dumps([c.sha(r/'bytes.txt'),ref,text]))
'''
    result = subprocess.run([sys.executable, "-B", "-c", code, str(consumer), str(tmp_path)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    digest, label, opposite = json.loads(result.stdout)
    assert digest == expected
    assert ledger.evidence_sha(raw) == expected
    assert label == render.reference_text(dict(path="bytes.txt", sha=expected))
    assert opposite.split("## Evidence hash rule\n\n")[1].strip() == render.HASH_RULE
