"""M006-S02: the exact installed native pair, offline (F 0.4.0 wheel, final T 711bce0).

Every lifecycle decision runs in a fresh process of the frozen wheel installed in
one isolated session venv, with real Git and scripted fake seats. The final T
export's own scripts grant authority and consume the produced history. Rows
P01-P14 of 02_analysis/m006_s02_admission/r1/matrix.md are mapped per test in
docs/paired_qualification.md.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

import _installed_pair as pair
from _installed_pair import (DONE, GRANT_DONE, MILESTONE_BOUNDARY, SLICE_BOUNDARY, attempts, evs,
                             make_project)


@pytest.fixture(scope="session")
def installed(tmp_path_factory):
    scratch = tmp_path_factory.mktemp("ip")
    return pair.install(scratch / "s", tmp_path_factory.getbasetemp() / "installed_pair_timings.jsonl")


def outside(path):
    assert not Path(path).resolve().is_relative_to(pair.WORKSPACE)
    return path


def source_payload():
    """The frutlups runtime files at the wheel's source commit, from committed Git blobs."""
    prefix = "08_pkg/frutlups/src/"
    names = subprocess.run(["git", "-C", str(pair.WORKSPACE), "ls-tree", "-r", "--name-only",
                            pair.SOURCE_COMMIT, "--", prefix + "frutlups"],
                           capture_output=True, check=True).stdout.decode().splitlines()
    return {name[len(prefix):]: subprocess.run(["git", "-C", str(pair.WORKSPACE), "show",
                                                f"{pair.SOURCE_COMMIT}:{name}"],
                                               capture_output=True, check=True).stdout
            for name in names}


# ------------------------------------------------------------------ P01

def test_p01_frozen_artifacts_and_isolated_installation(installed, tmp_path):
    wheel, dependency = installed.wheel, installed.dependency
    assert wheel["console_scripts"] == {"frutlups": "frutlups.cli:main"}
    assert wheel["requires"] == ["PyYAML<7,>=6.0.3"] and wheel["requires_python"] == ">=3.11"
    assert wheel["tags"] == ["py3-none-any"]
    assert dependency["tags"] == ["cp314-cp314-win_amd64"]
    # The shipped payload is exactly the committed source of the frozen candidate.
    assert wheel["payload"] == source_payload()
    assert len(wheel["payload"]) == 25
    assert len(installed.template) == 69 and installed.manifest["commit"] == pair.T_COMMIT
    info = pair.inspect_installed(installed, outside(tmp_path))
    assert info["version"] == "0.4.0" and info["yaml"] == "6.0.3"
    assert info["isolated"] == 1 and info["pyvenv_isolated"] is True
    assert {k: v["version"] for k, v in info["distributions"].items()} == {"frutlups": "0.4.0", "PyYAML": "6.0.3"}
    assert all(v["record_hashes_checked"] > 0 for v in info["distributions"].values())
    assert info["entry_points"] == [["console_scripts", "frutlups", "frutlups.cli:main"]]
    assert info["pi_extension_sha"] == wheel["members"]["frutlups/seats/pi_delegate/index.ts"]["sha"]
    # The real console launcher, from outside any checkout, with an empty argument list.
    usage = installed.run([installed.console], outside(tmp_path), tmp_path / "usage", kind="public")
    assert (usage.exit, usage.text) == (2, "usage: frutlups {preflight,run,status}\n")
    assert installed.rehash() == installed.installed_files
    installed.synthetic("install-total", overlaps=["venv", "install", "pip-check"],
                        elapsed=installed.install_seconds)


def test_p01_wrong_artifact_identity_refuses_before_install(installed, tmp_path):
    tampered = tmp_path / pair.WHEEL["path"].name
    raw = bytearray(pair.WHEEL["path"].read_bytes())
    raw[-100] ^= 0xFF
    tampered.write_bytes(bytes(raw))
    with pytest.raises(pair.ArtifactError, match="^pinned artifact identity mismatch: frutlups-0.4.0"):
        pair.require(pair.WHEEL, tampered)
    with pytest.raises(pair.ArtifactError, match="^missing pinned artifact: absent.whl$"):
        pair.require(pair.WHEEL, tmp_path / "absent.whl")
    archive = tmp_path / "template-5.0.0.tar"
    archive.write_bytes(pair.ARCHIVE["path"].read_bytes() + b"\0")
    with pytest.raises(pair.ArtifactError, match="^pinned artifact identity mismatch: template"):
        pair.require(pair.ARCHIVE, archive)
    # pip's own hash enforcement also refuses the altered bytes before installing anything.
    venv = tmp_path / "v"
    created = installed.run([sys.executable, "-I", "-B", "-m", "venv", venv], tmp_path, tmp_path / "venv",
                            kind="install")
    assert created.exit == 0
    requirements = tmp_path / "install.txt"
    requirements.write_text(f"{tampered.as_uri()} --hash=sha256:{pair.WHEEL['sha']}\n", encoding="utf-8")
    python = venv / "Scripts" / "python.exe"
    refused = installed.run([python, "-I", "-B", "-m", "pip", "--isolated", "--disable-pip-version-check",
                             "install", "--no-index", "--no-deps", "--no-cache-dir", "--require-hashes",
                             "-r", requirements], tmp_path, tmp_path / "refused", kind="install")
    assert refused.exit != 0
    assert b"THESE PACKAGES DO NOT MATCH THE HASHES" in refused.stdout + refused.stderr
    assert not (venv / "Lib/site-packages/frutlups").exists()
    assert not (venv / "Scripts/frutlups.exe").exists()


# ------------------------------------------------------------------ P03

def balances(project, label):
    """F status --json and the final T status report the same durable run balances."""
    result = project.f("status", project.root, "--json", label=f"{label}-status-json")
    assert result.exit == 0, result.err
    runs = [row for row in result.rows if row["action"] == "run_status"]
    text = project.status_pair(label)
    for run in runs:
        remaining = " ".join(f"{k}={run['remaining'][k]}" for k in
                             ("jobs", "transport_retries", "format_retries", "seconds"))
        assert (f"run {run['run']} revision={run['revision']} stop={run['stop']} "
                f"state={run['state']} remaining {remaining}") in text
    project.check(f"{label}-check")
    return {run["run"]: run for run in runs}


def review_report(sid, round_no, *, verdict="pass", rows=(), updates=()):
    """A manual reviewer report in the final T grammar (architect saves and records it)."""
    table = "".join(row + "\n" for row in rows)
    update = ""
    if updates:
        update = ("\n## Finding updates\n| source | sha | id | disposition | related |\n"
                  "| --- | --- | --- | --- | --- |\n" + "".join(u + "\n" for u in updates))
    status = "achieved" if verdict == "pass" else "not_achieved"
    return (f"# Review: {sid} round {round_no}\n## Findings\n| id | severity | disposition | summary |\n"
            f"| --- | --- | --- | --- |\n{table}{update}\n## Closure Decision\nObjective status: {status}\n"
            f"Objective evidence: Manual installed-pair review of the receipt and diff.\n## Verdict\n"
            f"Verdict: {verdict} - next: {'accept slice' if verdict == 'pass' else 'correct'}\n")


def manual_slice(project, sid, path, text, *, issue=True, round_no=1):
    """The final T manual route: prompt, coded, verify, review prompt, record, accept/commit."""
    if issue:
        result = project.t("prompt", sid, "--root", project.root, label=f"t-prompt-{sid}")
        assert result.exit == 0, result.stdout + result.stderr
    target = project.root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    for args, label in ((("coded", sid), "coded"),):
        result = project.tledger(*args, label=f"t-{label}-{sid}")
        assert result.exit == 0, result.stdout + result.stderr
    result = project.t("verify", sid, "--root", project.root, label=f"t-verify-{sid}")
    assert result.exit == 0, result.stdout + result.stderr
    result = project.t("prompt", sid, "--review", "--root", project.root, label=f"t-review-prompt-{sid}")
    assert result.exit == 0, result.stdout + result.stderr
    report = f"05_governance/reviews/m001/{sid}_r{round_no}_review.md"
    (project.root / report).write_text(review_report(sid, round_no), encoding="utf-8")
    result = project.tledger("record", report, label=f"t-record-{sid}")
    assert result.exit == 0, result.stdout + result.stderr
    result = project.tledger("accept", sid, "--commit", label=f"t-accept-{sid}")
    assert result.exit == 0, result.stdout + result.stderr
    return report


def charged_jobs(project):
    return sum(1 for row in project.events() if row["ev"] == "attempt_started" and row["role"] != "verification")


def test_p03_installed_lifecycle_both_handoffs(installed, tmp_path):
    project = make_project(installed, outside(tmp_path), autonomy=False,
                           milestones=[("M001", "active", ["M001-S01", "M001-S02", "M001-S03"])],
                           plan={"coder": [{"writes": {"07_app/runner-s03.txt": "runner output for S03\n"}}]})
    # Manual-only T operation, with no profile and no frutlups dispatch.
    manual_slice(project, "M001-S01", "07_app/manual-s01.txt", "manual output for S01\n")
    project.check("manual-s01-check")
    project.status_pair("manual-s01")
    before = project.snapshot()
    refused = project.run_json(label="no-profile")
    assert refused.exit == 1
    assert refused.rows == [{"action": "stop", "reason": "internal", "detail":
                             "M001-S02: LedgerError: fresh dispatch requires an autonomy profile; owner must inspect"}]
    assert project.snapshot() == before
    # The owner declares the profile only at this settled history; no bridging event.
    manual_events = project.events()
    data = pair.yaml.safe_load((project.root / "roadmap.yaml").read_text("utf-8"))
    data["autonomy"] = {"schema": "frutlups.autonomy/1"}
    (project.root / "roadmap.yaml").write_text(pair.yaml.safe_dump(data), encoding="utf-8")
    project.commit_all("owner declares the autonomy profile at settled history")
    project.admit(slices=("M001-S02", "M001-S03"))
    assert project.events()[:len(manual_events)] == manual_events
    assert [row["ev"] for row in project.events()[len(manual_events):]] == ["run_admitted"]
    result = project.f("preflight", project.root)
    assert (result.exit, result.text) == (0, "preflight: ok\n"), result.err
    assert attempts(project) == {"probe": 2}
    result = project.run_json("--once", label="f-issue-s02")
    assert (result.exit, result.text) == (0, ""), result.err
    issued = [row for row in project.events() if row["ev"] == "prompt" and row["slice"] == "M001-S02"]
    assert len(issued) == 1 and issued[0]["by"] == "frutlups"
    handoff = balances(project, "f-to-t")["R001"]
    # Runner to manual: T completes the F-issued round from the same files.
    manual_slice(project, "M001-S02", "07_app/manual-s02.txt", "manual output for S02\n", issue=False)
    assert balances(project, "t-done-s02")["R001"] == handoff
    assert attempts(project) == {"probe": 2} and project.counts().get("coder") is None
    # Manual to runner: F consumes the T artifacts and finishes S03 and the milestone.
    result = project.run_json("--until", "slice", label="f-s03")
    assert result.exit == 0, result.text
    assert result.rows[-1] == SLICE_BOUNDARY
    assert [r["action"] for r in result.rows if r["action"] in ("verified", "accepted")] == ["verified", "accepted"]
    head = project.head()
    assert project.git("show", f"{head}:07_app/runner-s03.txt") == b"runner output for S03\n"
    assert project.git("show", f"{head}:07_app/manual-s02.txt") == b"manual output for S02\n"
    # R001 named only S02/S03: closing M001 needs a grant covering every milestone slice.
    before = project.snapshot()
    result = project.run_json(label="f-close-outside-grant")
    assert result.exit == 1
    assert result.rows == [{"action": "stop", "reason": "internal", "detail":
                            "M001: LedgerError: holistic outside current grant boundary; owner must inspect"}]
    assert project.snapshot() == before
    r001 = balances(project, "before-successor")["R001"]
    charged = charged_jobs(project)
    assert r001["remaining"]["jobs"] == 20 - charged
    project.admit(run="R002", slices=("M001-S01", "M001-S02", "M001-S03"))
    successor = balances(project, "successor")
    assert successor["R001"]["state"] == "superseded"
    assert successor["R001"]["remaining"] == r001["remaining"]
    result = project.run_json(label="f-close")
    assert result.exit == 0, result.text
    assert result.rows[-1] == DONE
    assert evs(project).count("milestone_done") == 1
    roles = attempts(project)
    assert roles["coder"] == 1 and roles["verification"] == 1 and roles["reviewer"] == 1
    assert roles["holistic"] == 1
    final = balances(project, "closed")
    assert final["R001"] == successor["R001"]
    assert final["R002"]["state"] == "boundary"
    assert final["R002"]["remaining"]["jobs"] == 20 - (charged_jobs(project) - charged)
    # A fresh restart is idle, non-writing and dispatch-free.
    before = project.snapshot()
    result = project.run_json(label="restart")
    assert (result.exit, result.rows) == (0, [DONE])
    assert project.snapshot() == before
    usage = project.f("status", project.root, "--usage", "--json", label="usage")
    assert usage.exit == 0
    rows = [r for r in usage.rows if r["action"] == "usage"]
    assert [(r.get("slice") or r["milestone"], r["unresolved_attempts"]) for r in rows] == [
        ("M001-S01", 0), ("M001-S02", 0), ("M001-S03", 0), ("M001", 0)]
    assert rows[0]["completeness"] == rows[1]["completeness"] == "unknown"  # manual work has no usage
    assert project.counts()["coder"] == 1
    project.check("final-check")


# ------------------------------------------------------------------ P05

def commit_rows(project, label):
    """The final T reader's read-only commit status for the actual history."""
    result = project.tledger("recover", label=label)
    assert result.exit == 0, result.stdout + result.stderr
    return json.loads(result.text)


def subjects(project):
    return project.git("log", "--format=%s").decode().splitlines()


INFLIGHT = ".git/template-commit-inflight"
P05_CASES = [
    ("M001-S01", "before-recovery", "exception"),
    ("M001-S01", "before-staging", "death"),
    ("M001-S01", "after-staging", "exception"),
    ("M001-S01", "after-commit", "death"),
    ("M001", "before-recovery", "death"),
    ("M001", "before-staging", "exception"),
    ("M001", "after-staging", "death"),
    ("M001", "after-commit", "exception"),
]


def fault_run(project, scope, boundary, mode):
    reached = project.out / f"reached-{boundary}.json"
    argv = [project.installed.python, "-I", "-B", "-c", pair.FAULT, project.root, scope, boundary, mode, reached]
    result = project.f(argv=argv, label=f"fault-{boundary}-{mode}")
    assert json.loads(reached.read_text("utf-8")) == {"boundary": boundary, "scope": scope, "mode": mode}
    if mode == "death":
        assert result.exit == 137  # real owner-process death, no handler or cleanup ran
    else:
        assert result.exit == 1
        assert result.rows[-1]["reason"] == "internal"
        assert result.rows[-1]["detail"].endswith(f"RuntimeError: owned fault at {boundary}; owner must inspect")
    return result


def unrelated_dirt(project):
    """Ignored owner content that F must neither stage nor disturb."""
    values = {}
    for rel in ("local_state/owner-notes.txt", "tmp/owner-scratch.txt"):
        path = project.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"owner bytes for " + rel.encode() + b"\r\nkept\n")
        values[rel] = path.read_bytes()
    return values


@pytest.mark.parametrize("scope,boundary,mode", P05_CASES, ids=["-".join(c) for c in P05_CASES])
def test_p05_exact_recovery_after_interrupted_approval(installed, tmp_path, scope, boundary, mode):
    milestone = scope == "M001"
    project = make_project(installed, outside(tmp_path), holistic=milestone)
    project.admit()
    dirt = unrelated_dirt(project)
    if milestone:
        assert project.run_json("--until", "slice", label="slice").rows[-1] == SLICE_BOUNDARY
    head = project.head()
    fault_run(project, scope, boundary, mode)
    assert (project.root / INFLIGHT).exists() is (boundary != "before-recovery")
    if boundary in ("before-staging", "after-staging"):
        # A pending intent with an unarchived in-flight Git marker: the final T reader
        # refuses to report state until the Git process exit is attributed.
        refused = project.tledger("recover", label="after-fault")
        assert (refused.exit, refused.err) == (2, "error: prior Git dispatch is unresolved; architect must attribute its exit\n")
    else:
        (row,) = [r for r in commit_rows(project, "after-fault") if r["scope"] == scope]
        assert row["state"] == ("completed" if boundary == "after-commit" else "pending")
    status = project.f("status", project.root, "--json", label="after-fault-status")
    assert status.exit == 0, status.text
    target = next(row for row in status.rows if row["action"] == "commit" and row["scope"] == scope)
    assert target["state"] == ("completed" if boundary == "after-commit" else "pending"), status.text
    crashed_head, roles, counts = project.head(), attempts(project), project.counts()
    assert (crashed_head != head) is (boundary == "after-commit")
    prefix = project.ledger.read_bytes()
    if boundary in ("before-staging", "after-staging"):
        # Unattributed Git ownership: a fresh public process refuses before any effect.
        before = project.snapshot()
        refused = project.run_json(label="unattributed")
        assert (refused.exit, refused.rows) == (1, [{"action": "stop", "reason": "internal", "detail":
            "ValueError: prior Git dispatch is unresolved; architect must attribute its exit; owner must inspect"}]), refused.text
        assert project.snapshot() == before
        result = project.tledger("recover", "--git-resolved", target["id"], "--reason",
                                 "Owner confirmed the interrupted Git process exited", "--by", "human",
                                 label="attribute")
        assert result.exit == 0, result.stdout + result.stderr
        assert not (project.root / INFLIGHT).exists()
    result = project.run_json(label="restart")
    assert result.exit == 0, result.text
    assert result.rows[-1] == DONE
    rows = commit_rows(project, "after-restart")
    assert [row["state"] for row in rows] == ["completed"] * len(rows)
    assert len(rows) == (2 if milestone else 1)
    assert not (project.root / INFLIGHT).exists()
    # One approval and one completed intent each; no repeated seat, verifier or charge.
    assert project.ledger.read_bytes().startswith(prefix) if boundary != "after-commit" else True
    assert evs(project).count("accepted") == 1
    assert evs(project).count("milestone_done") == (1 if milestone else 0)
    assert attempts(project) == roles and project.counts() == counts
    log = subjects(project)
    assert len(log) == len(set(log)) and log[0].startswith(("Accept M001", "Close M001"))
    if boundary == "after-commit":
        assert project.head() == crashed_head  # the completed commit was recognized, not redone
    approved = target.get("commit") or next(row["commit"] for row in rows if row["scope"] == scope)
    changed = project.git("diff", "--name-only", f"{approved}^", approved).decode().split()
    assert changed and not any(path.startswith(("local_state/", "tmp/")) for path in changed)
    # Unrelated owner content and the index are exactly preserved; only F's derived
    # findings projection remains, and the final T reconciler agrees with its bytes.
    assert {rel: (project.root / rel).read_bytes() for rel in dirt} == dirt
    assert project.git("status", "--porcelain=v1", "-z") == b" M 05_governance/backlog.md\0"
    backlog_agrees(project, "reconcile")
    before = project.snapshot()
    assert project.run_json(label="replay").rows == [DONE]
    assert project.snapshot() == before
    # A later legitimate owner commit keeps every earlier witness completed.
    (project.root / "docs/owner-hotfix.md").write_text("Owner hotfix after approval.\n", encoding="utf-8")
    project.commit_all("owner commits a later hotfix")
    assert commit_rows(project, "after-owner-commit") == rows
    assert project.run_json(label="after-owner-commit").rows == [DONE]
    project.check()


def test_p05_recovery_preserves_unrelated_tracked_staged_and_unstaged_content(installed, tmp_path):
    """Owner tracked edits made at a pending-recovery boundary; dispatch admission stayed clean.

    F's public restart refuses unrecorded tracked changes before any recovery. The final
    T owner recovery command is the supported boundary: it refuses unrelated staged paths
    with the index preserved and, once they are unstaged, commits only the approved paths.
    """
    project = make_project(installed, outside(tmp_path), holistic=False)
    project.admit()
    head = project.head()
    fault_run(project, "M001-S01", "before-recovery", "exception")
    (pending,) = commit_rows(project, "pending")
    assert pending["state"] == "pending" and not (project.root / INFLIGHT).exists()
    assert project.head() == head
    roles, counts = attempts(project), project.counts()
    unstaged, staged = "docs/glossary.md", "docs/front_repo.md"
    original = {rel: (project.root / rel).read_bytes() for rel in (unstaged, staged)}
    (project.root / unstaged).write_bytes(original[unstaged] + b"\nOwner unstaged tracked edit.\r\n")
    (project.root / staged).write_bytes(original[staged] + b"\nOwner staged tracked edit.\n")
    project.git("add", staged)
    owner = {rel: (project.root / rel).read_bytes() for rel in (unstaged, staged)}
    staged_entry = project.git("ls-files", "--stage", "-z", "--", staged)
    before = project.snapshot()
    # Public restart: exact preflight refusal for both unrecorded tracked changes, no effects.
    refused = project.run_json(label="public-restart-owner-edits")
    assert (refused.exit, refused.rows, refused.err) == (2, [
        {"action": "preflight", "ok": False, "detail": f"unrecorded change: {staged}"},
        {"action": "preflight", "ok": False, "detail": f"unrecorded change: {unstaged}"}], ""), refused.text
    assert project.snapshot() == before
    # T owner recovery refuses the unrelated staged path and preserves the index exactly.
    staged_refusal = (2, "", f"error: unrelated staged paths; index preserved: {staged}\n")
    for args, label in ((("--execute",), "t-recover-staged-unrelated"), ((), "t-recover-read-staged-unrelated")):
        t_refused = project.tledger("recover", *args, label=label)
        assert (t_refused.exit, t_refused.text, t_refused.err) == staged_refusal, (t_refused.text, t_refused.err)
        assert project.snapshot() == before
        assert project.git("ls-files", "--stage", "-z", "--", staged) == staged_entry
    # The owner unstages; the working-tree bytes stay their own and the intent is still pending.
    project.git("restore", "--staged", staged)
    assert project.git("diff", "--cached", "--name-only", "-z") == b""
    assert {rel: (project.root / rel).read_bytes() for rel in owner} == owner
    assert commit_rows(project, "pending-after-unstage") == [pending]
    recovered = project.tledger("recover", "--execute", label="t-recover-execute")
    approved = project.head()
    done = {"id": pending["id"], "scope": pending["scope"], "state": "completed", "commit": approved}
    assert (recovered.exit, json.loads(recovered.text), recovered.err) == (0, [done], ""), (recovered.text,
                                                                                         recovered.err)
    assert commit_rows(project, "recovered") == [done]
    assert project.git("rev-list", "--count", f"{head}..{approved}") == b"1\n"
    changed = project.git("diff", "--name-only", head, approved).decode().splitlines()
    assert "07_app/result.txt" in changed and not any(p.startswith("docs/") for p in changed)
    # Unrelated tracked content: exact bytes, unstaged, index entries equal to HEAD.
    assert {rel: (project.root / rel).read_bytes() for rel in owner} == owner
    assert project.git("status", "--porcelain=v1", "-z") == (
        b" M 05_governance/backlog.md\0 M docs/front_repo.md\0 M docs/glossary.md\0")
    for rel in owner:
        assert project.git("show", f"{approved}:{rel}") == original[rel]
    assert project.git("diff", "--cached", "--name-only", "-z") == b""
    assert attempts(project) == roles and project.counts() == counts
    backlog_agrees(project, "reconcile")
    text = project.status_pair("after-t-recovery")
    assert f"commit {pending['id']}: completed\n" in text
    # F still refuses unrecorded owner edits; once the owner commits them, replay is idle.
    before = project.snapshot()
    still = project.run_json(label="public-restart-after-recovery")
    assert (still.exit, still.rows, still.err) == (2, refused.rows, ""), still.text
    assert project.snapshot() == before
    project.commit_all("owner commits the unrelated edits")
    assert commit_rows(project, "after-owner-commit") == [done]
    before = project.snapshot()
    assert project.run_json(label="replay").rows == [DONE]
    assert project.snapshot() == before
    assert attempts(project) == roles and project.counts() == counts
    project.check()


def test_p05_cancellation_resolves_only_the_named_intent(installed, tmp_path):
    project = make_project(installed, outside(tmp_path), holistic=False)
    project.admit()
    head = project.head()
    fault_run(project, "M001-S01", "before-recovery", "exception")
    (pending,) = commit_rows(project, "pending")
    assert pending["state"] == "pending"
    roles, counts = attempts(project), project.counts()
    result = project.tledger("cancel", pending["id"], "--reason", "Owner withdraws this approval commit",
                             "--by", "human", label="cancel")
    assert (result.exit, result.text) == (0, f"commit {pending['id']} cancelled; files and index preserved\n")
    assert evs(project)[-1] == "commit_cancelled"
    after_cancel = project.snapshot()
    result = project.run_json(label="after-cancel")
    # No fresh approval or commit is manufactured for the cancelled intent.
    assert project.head() == head
    assert evs(project).count("accepted") == 1 and evs(project).count("commit_cancelled") == 1
    assert attempts(project) == roles and project.counts() == counts
    assert project.git("diff", "--cached", "--name-only") == b""
    assert (project.root / "07_app/result.txt").read_text("utf-8") == "useful installed output\n"
    assert project.snapshot()["ledger"] == after_cancel["ledger"]
    assert (result.exit, result.rows) == (0, [GRANT_DONE]), result.text


# ------------------------------------------------------------------ P06

FROZEN_FIELDS = ("acceptance", "read_first", "allowed_prefixes", "forbidden", "full", "focused", "runtime")


def envelope_of(project, sid, round_no):
    issued = next(row for row in project.events() if row["ev"] == "prompt" and row["slice"] == sid
                  and row["round"] == round_no)
    return json.loads((project.root / issued["envelope"]["path"]).read_bytes()), issued


def test_p06_typed_blocker_retains_edits_and_resolves_once(installed, tmp_path):
    blocker = {"outcome": "blocked_authority", "requirement": "Scope decision",
               "reason": "The requested change needs an owner decision", "actor": "human",
               "action": "Record the scope decision"}
    project = make_project(installed, outside(tmp_path), holistic=False, plan={"coder": [
        {"writes": {"07_app/partial.txt": "retained partial work\n"}, "pair": {"outcome": blocker}},
        {"writes": {"07_app/partial.txt": "completed after resolution\n"}}]})
    project.admit()
    result = project.run_json(label="blocked")
    stop = result.rows[-1]
    assert (result.exit, stop["action"]) == (3, "stop"), result.text
    assert (project.root / "07_app/partial.txt").read_text("utf-8") == "retained partial work\n"
    coded = [row for row in project.events() if row["ev"] == "coded"]
    assert len(coded) == 1 and coded[0]["result"] == "blocked_authority"
    outcome = json.loads((project.root / coded[0]["outcome"]["path"]).read_bytes())
    assert {k: outcome[k] for k in blocker} == blocker
    stops = evs(project).count("stop")
    # One durable stop: a fresh repeat is non-writing and dispatches nothing.
    before = project.snapshot()
    again = project.run_json(label="blocked-again")
    assert (again.exit, again.rows[-1]) == (result.exit, stop)
    assert project.snapshot() == before and evs(project).count("stop") == stops
    assert project.counts()["coder"] == 1
    # Authority-bound resolution through the final T CLI, committed owner authority.
    decision = project.root / "questions/answered/scope-decision.md"
    decision.parent.mkdir(parents=True, exist_ok=True)
    decision.write_text("Owner decision: complete the partial work inside 07_app/.\n", encoding="utf-8")
    project.commit_all("owner records the scope decision")
    resolved = project.tledger("resolve", "M001-S01", "--reason", "Owner recorded the scope decision",
                               "--authority", "questions/answered/scope-decision.md", "--by", "human",
                               label="resolve")
    assert (resolved.exit, resolved.text) == (0, "M001-S01 resolved\n"), resolved.err
    result = project.run_json(label="continued")
    assert (result.exit, result.rows[-1]) == (0, DONE), result.text
    first, _ = envelope_of(project, "M001-S01", 1)
    second, issued = envelope_of(project, "M001-S01", 2)
    assert {k: second[k] for k in FROZEN_FIELDS} == {k: first[k] for k in FROZEN_FIELDS}
    assert "questions/answered/scope-decision.md" in (project.root / issued["path"]).read_text("utf-8")
    assert (project.root / "07_app/partial.txt").read_text("utf-8") == "completed after resolution\n"
    assert project.counts()["coder"] == 2 and evs(project).count("resolved") == 1
    status = balances(project, "resolved")["R001"]
    assert status["remaining"]["jobs"] == 20 - charged_jobs(project)


def test_p06_identical_no_change_failures_stop_without_extra_experiment(installed, tmp_path):
    failing = ["python", "-c", "raise SystemExit('owned identical failure')"]
    project = make_project(installed, outside(tmp_path), holistic=False, verification=failing, plan={
        "coder": [{"writes": {"07_app/result.txt": "attempted output\n"}}, {}]})
    project.admit(rounds=3)
    result = project.run_json(label="no-progress")
    assert result.rows[-1]["action"] == "stop", result.text
    assert "identical product and verification failure" in result.rows[-1]["detail"]
    verified = [row for row in project.events() if row["ev"] == "verified"]
    assert [(row["round"], row["ok"]) for row in verified] == [(1, False), (2, False)]
    assert project.counts()["coder"] == 2 and attempts(project)["verification"] == 2
    prompts = [row["round"] for row in project.events() if row["ev"] == "prompt"]
    assert prompts == [1, 2]  # no automatic third experiment
    before = project.snapshot()
    again = project.run_json(label="no-progress-again")
    assert again.rows[-1] == result.rows[-1]
    assert project.snapshot() == before
    project.check()


# ------------------------------------------------------------------ P04

def relabel(project, **statuses):
    data = pair.yaml.safe_load((project.root / "roadmap.yaml").read_text("utf-8"))
    for milestone in data["milestones"]:
        milestone["status"] = statuses.get(milestone["id"], milestone["status"])
    (project.root / "roadmap.yaml").write_text(pair.yaml.safe_dump(data), encoding="utf-8")
    project.commit_all("owner relabels " + ", ".join(f"{k}={v}" for k, v in statuses.items()))


NO_HISTORY = {
    "all-planned": [("M001", "planned", ["M001-S01"])],
    "all-done": [("M001", "done", ["M001-S01"])],
    "done-plus-planned": [("M001", "done", ["M001-S01"]), ("M002", "planned", ["M002-S01"])],
}


@pytest.mark.parametrize("shape", list(NO_HISTORY))
def test_p04_no_active_shapes_are_idle_and_non_writing(installed, tmp_path, shape):
    project = make_project(installed, outside(tmp_path), milestones=NO_HISTORY[shape])
    text = project.status_pair("idle")
    assert "no active milestone\nnext: none\n" in text
    # A done label without ledger closure is reported, never treated as completion.
    assert ("warning: M001 declared done without ledger closure" in text) is (shape != "all-planned")
    before = project.snapshot()
    refusal = (1, '{"action": "stop", "reason": "internal", "detail": '
                  '"LedgerError: profile requires current grant; owner must inspect"}\n')
    # Exact result per public command: no grant funds a fresh run, and a profile-only
    # preflight has no active route to probe, so it passes without any probe.
    for verb, expected in (("run", refusal), ("preflight", (0, "preflight: ok\n")), ("run", refusal)):
        result = project.f(verb, project.root, *(["--json"] if verb == "run" else []), label=f"idle-{verb}")
        assert (result.exit, result.text, result.err) == (*expected, ""), (verb, result.text)
        assert project.snapshot() == before
    assert project.snapshot() == before
    assert attempts(project) == {} and not (project.root / "local_state/frutlups").exists()
    project.check()


def changed_files(before, after):
    return sorted(p for p in before["files"].keys() | after["files"].keys()
                  if before["files"].get(p) != after["files"].get(p))


def test_p04_last_active_completion_preserves_earlier_planned_blocked_history(installed, tmp_path):
    """Idle row 01: last-active completion, earlier planned blocked history and a separate planning edit."""
    project = make_project(installed, outside(tmp_path), holistic=False,
                           milestones=[("M001", "planned", ["M001-S01"]), ("M002", "active", ["M002-S01"])],
                           plan={"coder": [{"writes": {"07_app/m002.txt": "last active work\n"}}]})
    project.admit(milestone="M002", slices=("M002-S01",))
    result = project.run_json("--until", "milestone", label="last-active")
    assert (result.exit, result.rows[-1]) == (0, MILESTONE_BOUNDARY), result.text
    # The owner records manual blocked history on the earlier, never-admitted milestone.
    sid = "M001-S01"
    relabel(project, M001="active")
    assert project.t("prompt", sid, "--root", project.root, label="t-prompt-blocked").exit == 0
    (project.root / "07_app/partial.txt").write_text("partial earlier work\n", encoding="utf-8")
    blocked = project.tledger("blocked", sid, "--requirement", "Earlier scope decision", "--reason",
                              "Needs a human decision", "--actor", "human", "--action", "Decide later",
                              label="t-blocked")
    assert blocked.exit == 0, blocked.text + blocked.err
    relabel(project, M001="planned")
    earlier = [r for r in project.events() if r.get("slice") == sid]
    assert [r["ev"] for r in earlier] == ["prompt", "coded"] and earlier[1]["result"] == "blocked_authority"
    assert {r["by"] for r in earlier} == {"architect"}
    text = project.status_pair("blocked-earlier")
    assert "M001-S01 r1 blocked\nM002-S01 r1 accepted\n" in text
    before = project.snapshot()
    result = project.run_json(label="after-blocked-history")
    assert (result.exit, result.rows) == (0, [GRANT_DONE]), result.text
    assert project.snapshot() == before
    # The blocked history is preserved and never dispatched by F.
    assert [r for r in project.events() if r.get("slice") == sid] == earlier
    assert project.counts()["coder"] == 1
    # A separate owner planning edit changes exactly the two planning files.
    data = pair.yaml.safe_load((project.root / "roadmap.yaml").read_text("utf-8"))
    data["milestones"][1]["status"] = "done"
    (project.root / "roadmap.yaml").write_text(pair.yaml.safe_dump(data), encoding="utf-8")
    rendered = project.t("roadmap", "render", "--root", project.root, label="t-render")
    assert rendered.exit == 0, rendered.text + rendered.err
    after = project.snapshot()
    assert changed_files(before, after) == ["docs/roadmap.md", "roadmap.yaml"]
    assert (after["ledger"], after["head"], after["index"]) == (before["ledger"], before["head"], before["index"])
    project.commit_all("owner marks M002 done separately")
    assert [r for r in project.events() if r.get("slice") == sid] == earlier
    before = project.snapshot()
    result = project.run_json(label="after-planning-edit")
    assert (result.exit, result.rows) == (0, [GRANT_DONE]), result.text
    assert project.snapshot() == before
    project.check()


def test_p04_manual_completion_confirms_independently_of_label(installed, tmp_path):
    """Idle rows 15 and 08 on accepted non-holistic manual work: label-independent and non-writing."""
    project = make_project(installed, outside(tmp_path), holistic=False, autonomy=False)
    manual_slice(project, "M001-S01", "07_app/manual.txt", "manual output\n")
    (intent,) = commit_rows(project, "manual-commit")
    assert intent["state"] == "completed"
    for label in ("active", "planned", "done"):
        if label != "active":
            relabel(project, M001=label)
        before = project.snapshot()
        for args, suffix in (((), ""), ((), "-again"), (("--until", "milestone"), "-until")):
            result = project.run_json(*args, label=f"confirm-{label}{suffix}")
            assert (result.exit, result.rows, result.err) == (0, [DONE], ""), result.text
            assert project.snapshot() == before and attempts(project) == {}
        text = project.status_pair(f"confirm-{label}")
        frontier = "" if label == "active" else "no active milestone\n"
        assert text == f"M001-S01 r1 accepted\n{frontier}next: none\ncommit {intent['id']}: completed\n"
        assert project.snapshot() == before
    project.check()


def test_p04_frontier_reopen_relabel_and_planned_authority(installed, tmp_path):
    project = make_project(installed, outside(tmp_path),
                           milestones=[("M001", "active", ["M001-S01"]), ("M002", "active", ["M002-S01"])],
                           plan={"coder": [{"writes": {"07_app/result.txt": "first\n"}},
                                           {"writes": {"07_app/result.txt": "reopened correction\n"}}]})
    project.admit()
    result = project.run_json(label="m001")
    assert (result.exit, result.rows[-1]) == (0, GRANT_DONE), result.text
    assert evs(project).count("milestone_done") == 1
    assert not [row for row in project.events() if row.get("slice") == "M002-S01"]  # no implicit authority
    before = project.snapshot()
    result = project.run_json(label="completed-grant-restart")
    assert (result.exit, result.rows) == (0, [GRANT_DONE])
    assert project.snapshot() == before
    # Reopened M001 keeps precedence over the active, ungranted M002.
    reopened = project.tledger("reopen", "M001-S01", "--reason", "Owner reopens the first slice",
                               "--by", "human", label="reopen")
    assert (reopened.exit, reopened.err) == (0, ""), reopened.text
    text = project.status_pair("reopened")
    assert "next: M001-S01" in text
    before = project.snapshot()
    result = project.run_json(label="reopened-without-grant")
    # The completed R001 boundary funds nothing new for the reopened slice.
    assert (result.exit, result.rows) == (0, [GRANT_DONE]), result.text
    assert project.snapshot() == before
    # A done label cannot complete the reopened, unaccepted slice.
    relabel(project, M001="done")
    refused = project.f("status", project.root, label="done-label-status")
    assert refused.exit == 1, refused.text
    assert "inactive run history requires completed milestone and settled attempts" in refused.text + refused.err
    relabel(project, M001="active")
    project.admit(run="R002")
    result = project.run_json(label="reopened-work")
    assert (result.exit, result.rows[-1]) == (0, GRANT_DONE), result.text
    assert evs(project).count("milestone_done") == 2
    assert (project.root / "07_app/result.txt").read_text("utf-8") == "reopened correction\n"
    assert not [row for row in project.events() if row.get("slice") == "M002-S01"]
    # Completed done history plus never-admitted planned work is idle and unfunded.
    relabel(project, M001="done", M002="planned")
    text = project.status_pair("done-plus-planned")
    before = project.snapshot()
    result = project.run_json(label="done-plus-planned")
    assert (result.exit, result.rows) == (0, [GRANT_DONE]), result.text
    assert project.snapshot() == before
    # An ever-admitted milestone relabelled planned refuses in both readers.
    relabel(project, M001="planned")
    before = project.snapshot()
    refused = project.f("status", project.root, label="planned-relabel-F")
    assert refused.exit == 1 and "run admission requires active milestone" in refused.text + refused.err
    t = project.tledger("check", label="planned-relabel-T")
    assert t.exit != 0 and "run admission requires active milestone" in t.text + t.err
    assert project.snapshot() == before


# ------------------------------------------------------------------ P04 round 3

def relabel_planning(project, **statuses):
    """Owner label edit committed alone; recorded but uncommitted round evidence stays in place."""
    data = pair.yaml.safe_load((project.root / "roadmap.yaml").read_text("utf-8"))
    for milestone in data["milestones"]:
        milestone["status"] = statuses.get(milestone["id"], milestone["status"])
    (project.root / "roadmap.yaml").write_text(pair.yaml.safe_dump(data), encoding="utf-8")
    project.git("add", "roadmap.yaml")
    project.git("commit", "-qm", "owner relabels " + ", ".join(f"{k}={v}" for k, v in statuses.items()))


def idle_observations(project, label):
    """Exact public results; each observation leaves the complete snapshot unchanged."""
    before = project.snapshot()
    results = {}
    for verb, args in (("run", ("--json",)), ("preflight", ()), ("run-again", ("--json",))):
        result = project.f(verb.split("-")[0], project.root, *args, label=f"{label}-{verb}")
        results[verb] = (result.exit, result.text, result.err)
        assert project.snapshot() == before, verb
    assert attempts(project) == {} and not (project.root / "local_state/frutlups").exists()
    return results


NEEDS_SPECIFICATION = (3, '{"action": "stop", "reason": "needs_specification", "detail": '
                          '"architect must admit the next milestone"}\n', "")
# No active milestone and no profile: run stops without effects; preflight has no route to probe.
NO_ACTIVE_RESULTS = {"run": NEEDS_SPECIFICATION, "preflight": (0, "preflight: ok\n", ""),
                     "run-again": NEEDS_SPECIFICATION}
DONE_WARNING = "warning: M001 declared done without ledger closure\n"

STRICT_SHAPES = {  # malformed idle roadmap: mutation and exact load errors (the source message first)
    "no-milestones": (lambda data: data.update(milestones=[]), ("milestones must be a non-empty list",)),
    "no-slices": (lambda data: data["milestones"][0].update(slices=[]), ("milestones[0].slices must be non-empty",)),
    # Renaming the milestone also makes its unchanged slice ID mismatch the new prefix.
    "milestone-id": (lambda data: data["milestones"][0].update(id="invalid"),
                     ("milestones[0].id must match Mnnn", "milestones[0].slices[0].id must match invalid-Snn")),
    "slice-id": (lambda data: data["milestones"][0]["slices"][0].update(id="invalid"),
                 ("milestones[0].slices[0].id must match M001-Snn",)),
}


@pytest.mark.parametrize("shape", list(STRICT_SHAPES))
def test_p04_r3_idle_strict_structure(installed, tmp_path, shape):
    """Idle row 02: an idle planned roadmap keeps its strict nonempty structure at load."""
    project = make_project(installed, outside(tmp_path), milestones=[("M001", "planned", ["M001-S01"])])
    mutate, errors = STRICT_SHAPES[shape]
    data = pair.yaml.safe_load((project.root / "roadmap.yaml").read_text("utf-8"))
    mutate(data)
    (project.root / "roadmap.yaml").write_text(pair.yaml.safe_dump(data), encoding="utf-8")
    project.commit_all(f"owner commits a malformed idle roadmap ({shape})")
    before = project.snapshot()
    status = project.f("status", project.root, label="strict-status")
    assert (status.exit, status.text, status.err) == (
        1, f"internal: RoadmapError: {' '.join(errors)}; owner must inspect\n", ""), (status.text, status.err)
    rows = "".join(json.dumps({"action": "preflight", "ok": False, "detail": error}) + "\n" for error in errors)
    assert idle_observations(project, "strict") == {
        "run": (2, rows, ""), "preflight": (2, "".join(f"preflight: {error}\n" for error in errors), ""),
        "run-again": (2, rows, "")}
    expected = {"roadmap": "".join(f"error: {error}\n" for error in errors),
                "ledger": "error: " + "\n".join(errors) + "\n"}
    for script, args in (("roadmap", ("check", "--root", project.root)), ("ledger", ("--root", project.root, "check"))):
        result = project.t(script, *args, label=f"strict-t-{script}")
        assert (result.exit, result.text, result.err) == (2, "", expected[script]), (result.text, result.err)
    assert project.snapshot() == before


UNFINISHED_DONE = {  # step: the exact supported manual T events recorded before the done label
    "verifying": ["prompt", "coded"],
    "reviewing": ["prompt", "coded", "verified"],
    "accept_pending": ["prompt", "coded", "verified", "artifact", "artifact", "reviewed"],
    "blocked": ["prompt", "coded"],
}


@pytest.mark.parametrize("step", list(UNFINISHED_DONE))
def test_p04_r3_idle_unfinished_done(installed, tmp_path, step):
    """Idle row 04: a done label never completes verifying, reviewing, accept_pending or blocked work.

    Authentic manual unprofiled T history stops at the named step; only the label is committed.
    Unstarted, coding and fix are covered by the no-history, P11 and frontier tests.
    """
    project = make_project(installed, outside(tmp_path), holistic=False, autonomy=False)
    sid = "M001-S01"
    assert project.t("prompt", sid, "--root", project.root, label="t-prompt").exit == 0
    (project.root / "07_app/result.txt").write_text("manual output\n", encoding="utf-8")
    if step == "blocked":
        result = project.tledger("blocked", sid, "--requirement", "Owner evidence", "--reason", "Evidence pending",
                                 "--actor", "architect", "--action", "Bind evidence", label="t-blocked")
    else:
        result = project.tledger("coded", sid, label="t-coded")
    assert result.exit == 0, result.text + result.err
    if step in ("reviewing", "accept_pending"):
        result = project.t("verify", sid, "--root", project.root, label="t-verify")
        assert result.exit == 0, result.text + result.err
    if step == "accept_pending":
        result = project.t("prompt", sid, "--review", "--root", project.root, label="t-review-prompt")
        assert result.exit == 0, result.text + result.err
        report = "05_governance/reviews/m001/M001-S01_r1_review.md"
        (project.root / report).write_text(review_report(sid, 1), encoding="utf-8")
        result = project.tledger("record", report, label="t-record")
        assert result.exit == 0, result.text + result.err
    events = project.events()
    assert [e["ev"] for e in events] == UNFINISHED_DONE[step]
    assert (events[1].get("result") == "blocked_authority") is (step == "blocked")
    relabel_planning(project, M001="done")
    assert project.events() == events
    text = project.status_pair(f"done-{step}")
    assert text == f"M001-S01 r1 {step}\nno active milestone\nnext: none\n{DONE_WARNING}", text
    assert idle_observations(project, f"done-{step}") == NO_ACTIVE_RESULTS
    assert project.events() == events
    project.check()


def test_p04_r3_idle_holistic_pending(installed, tmp_path):
    """Idle row 09: an accepted slice and a passing holistic review await closure with no active milestone."""
    project = make_project(installed, outside(tmp_path), autonomy=False)
    manual_slice(project, "M001-S01", "07_app/manual.txt", "manual output\n")
    prompt = project.t("prompt", "--holistic", "M001", "--root", project.root, label="t-holistic-prompt")
    assert prompt.exit == 0, prompt.text + prompt.err
    report = "05_governance/reviews/m001/M001_holistic_review.md"
    (project.root / report).write_text(review_report("M001", "holistic"), encoding="utf-8")
    recorded = project.tledger("record", report, "--milestone", "M001", label="t-record-holistic")
    assert (recorded.exit, recorded.text, recorded.err) == (0, "M001 holistic pass\n", ""), recorded.err
    events = project.events()
    assert events[-1]["ev"] == "holistic_reviewed" and events[-1]["verdict"] == "pass"
    assert "milestone_done" not in [e["ev"] for e in events]
    relabel_planning(project, M001="done")
    (intent,) = commit_rows(project, "slice-intent")
    assert intent["state"] == "completed"
    text = project.status_pair("holistic-pending")
    assert text == (f"M001-S01 r1 accepted\nno active milestone\nnext: none\ncommit {intent['id']}: completed\n"
                    f"M001 holistic: close_pending\n{DONE_WARNING}"), text
    assert idle_observations(project, "holistic-pending") == NO_ACTIVE_RESULTS
    assert project.events() == events
    project.check()


RELABEL_ACCOUNTING = {  # pinned idle case: active-form R1 closed, consumed jobs/seconds, membership, slice step
    "no-active-planned-admitted-history-refuses": (None, 1, 40, {"I1": "R1"}, "unstarted"),
    "completed-run-cannot-become-planned": ("boundary", 0, 0, {}, "accepted"),
    "overrun-run-cannot-become-planned": ("overrun", 1, 106, {"I1": "R1"}, "unstarted"),
    "superseded-run-cannot-become-planned": ("superseded", 0, 0, {}, "unstarted"),
}


def test_p04_r3_idle_relabel_accounting(installed, tmp_path):
    """Idle row 12: the four pinned planned-refusal cases keep their active-form accounting.

    The isolated installed reader folds each unchanged corpus ledger from a real file, first
    under an active label, then under its pinned planned label; the ledger bytes never change.
    """
    import copy
    directory = tmp_path / "roadmaps"
    directory.mkdir()
    cases = corpus_cases(installed, directory)
    request = {}
    for name in RELABEL_ACCOUNTING:
        item = cases[f"autonomy_v1.json/{name}"]
        case = item["case"]
        assert (case["valid"], case["roadmap"]["status"], case["error"]) == (
            False, "planned", "run admission requires active milestone"), name
        planned = pair.yaml.safe_load(Path(item["roadmap"]).read_text("utf-8"))
        assert planned["milestones"][0]["status"] == "planned"
        active = copy.deepcopy(planned)
        active["milestones"][0]["status"] = "active"
        active_path = directory / f"active-{name}.yaml"
        active_path.write_text(pair.yaml.safe_dump(active), encoding="utf-8")
        for form, roadmap_path in (("active", str(active_path)), ("planned", item["roadmap"])):
            request[f"{form}/{name}"] = {"corpus": item["corpus"], "events": case["events"], "roadmap": roadmap_path,
                                         "ledger_file": True}
    source = tmp_path / "relabel-cases.json"
    source.write_text(json.dumps(request), encoding="utf-8")
    source_bytes = source.read_bytes()
    scratch = tmp_path / "ledgers"
    scratch.mkdir()
    f_run = installed.run([installed.python, "-I", "-B", "-c", pair.CORPUS_F, source, scratch], tmp_path,
                          tmp_path / "f-relabel-reader", kind="public")
    assert f_run.exit == 0, f_run.err
    out = json.loads(f_run.stdout)
    assert source.read_bytes() == source_bytes and set(out) == set(request)
    fresh = {"jobs": 4, "transport_retries": 1, "format_retries": 1, "seconds": 100}
    for name, (closed, jobs, seconds, membership, step) in RELABEL_ACCOUNTING.items():
        active = out[f"active/{name}"]
        superseded = closed == "superseded"
        # Includes the source test's exact closed, consumed, remaining, revision and head values.
        allowance = {"jobs": 6, "transport_retries": 1, "format_retries": 2, "seconds": 130} if superseded else fresh
        consumed = {"jobs": jobs, "transport_retries": 0, "format_retries": 0, "seconds": seconds}
        assert active["runs"]["R1"] == {"revision": 1 if superseded else 0, "allowance": allowance,
                                        "consumed": consumed, "closed": closed,
                                        "remaining": {k: allowance[k] - consumed[k] for k in allowance}}, name
        if superseded:
            assert active["runs"]["R2"] == {"revision": 0, "allowance": fresh, "remaining": fresh, "closed": None,
                                            "consumed": dict.fromkeys(fresh, 0)}
        else:
            assert set(active["runs"]) == {"R1"}, name
        assert (active["head"], active["membership"], active["step"], active["unfinished"]) == (
            {"run": "R2" if superseded else "R1", "revision": 0}, membership, step,
            ["I1"] if closed is None else []), name
        assert active["input_unchanged"] is True, name
        # The same unchanged ledger bytes under the pinned planned label refuse exactly.
        assert out[f"planned/{name}"] == {"error": "run admission requires active milestone",
                                          "input_unchanged": True}, name
    installed.synthetic("relabel-reader-total", overlaps=["f-relabel-reader"], elapsed=f_run.elapsed)


# ------------------------------------------------------------------ P02

# The native consumer's stable parser context around each corpus diagnostic, exactly as
# asserted by the private reader nodes; the corpus bytes themselves are unchanged.
AUTONOMY_EXPANDED = {
    "profile attempts require preceding admission": "profile attempts require preceding admission; historical cutover unsupported",
    "verification requires current verifying round": "verification requires current verifying round and no retry",
    "autonomy history requires declared": "autonomy history requires declared frutlups.autonomy/1 profile",
    "grantors must contain human": "event: grantors must contain human and optionally architect",
}
AUTONOMY_PARSER = {
    "run grant requires human or architect", "run needs bound authority",
    "duplicate run authority reference", "unsupported legacy history; use pinned v4",
    "invalid run slices", "invalid allowance units", "invalid corrective ceiling",
    "invalid previous run head", "invalid run_admitted fields",
    "extension needs positive allowance", "invalid run allowance", "attempt role/scope mismatch",
}
PROTOCOL_DIAGNOSTICS = {
    "legacy-prefix-new-suffix": "line 1: unsupported legacy history; use pinned v4",
    "illegal-version-reversal": "line 2: unsupported legacy history; use pinned v4",
    "unknown-output-contract": "line 1: event: unknown=['capability'] missing=[]",
    "nonhuman-retains-acceptance": "line 1: only human retains acceptance",
    "v2-unbound-unblocked-refusal": "line 1: /2 resolution requires an authority-bound resolved event",
    "unknown-ledger-v3": "line 1: invalid schema or event: unknown ledger schema; expected frutlups.ledger/2",
    "noncanonical-v2-extension-timestamp": "line 1: timestamp must be canonical UTC seconds",
}


def corpus_cases(installed, directory):
    import copy
    base = pair.yaml.safe_load(installed.template["roadmap.yaml"])
    cases = {}
    for corpus in ("protocol_v2.json", "autonomy_v1.json"):
        for case in json.loads(pair.require(pair.CORPORA[corpus]))["cases"]:
            data = copy.deepcopy(base)
            milestone = data["milestones"][0]
            if corpus == "autonomy_v1.json":
                if case.get("declared", True):
                    data["autonomy"] = {"schema": "frutlups.autonomy/1"}
                options = case.get("roadmap", {})
                milestone["status"] = options.get("status", "active")
                milestone["holistic_review"] = options.get("holistic_review", True)
                original = milestone["slices"][0]
                milestone["slices"] = [{**copy.deepcopy(original), "id": sid}
                                       for sid in options.get("slices", ["M001-S01"])]
                if options.get("second_milestone"):
                    second = copy.deepcopy(milestone)
                    second.update(id="M002", status=options.get("second_status", "active"))
                    second["slices"] = [{**copy.deepcopy(original), "id": "M002-S01"}]
                    data["milestones"].append(second)
            elif "holistic_review" in case:
                milestone["holistic_review"] = case["holistic_review"]
            path = directory / f"{len(cases):03d}.yaml"
            path.write_text(pair.yaml.safe_dump(data), encoding="utf-8")
            cases[f"{corpus}/{case['name']}"] = {"corpus": corpus, "case": case, "events": case["events"],
                                                 "roadmap": str(path)}
    return cases


def expected_f_error(corpus, case):
    if corpus == "protocol_v2.json":
        return PROTOCOL_DIAGNOSTICS.get(case["name"], case["error"])
    error = case["error"]
    return AUTONOMY_EXPANDED.get(error, ("event: " if error in AUTONOMY_PARSER else "") + error)


def test_p02_all_150_cases_through_installed_reader_and_final_template(installed, tmp_path):
    directory = tmp_path / "roadmaps"
    directory.mkdir()
    cases = corpus_cases(installed, directory)
    assert len(cases) == 150
    request = tmp_path / "cases.json"
    request.write_text(json.dumps({k: {x: v[x] for x in ("corpus", "events", "roadmap")} for k, v in cases.items()}),
                       encoding="utf-8")
    request_bytes = request.read_bytes()
    scratch = tmp_path / "ledgers"
    scratch.mkdir()
    f_run = installed.run([installed.python, "-I", "-B", "-c", pair.CORPUS_F, request, scratch], tmp_path,
                          tmp_path / "f-reader", kind="public")
    assert f_run.exit == 0, f_run.err
    project = make_project(installed, tmp_path, name="t")
    t_run = installed.run([installed.python, "-B", "-c", pair.CORPUS_T, request, project.root], tmp_path,
                          tmp_path / "t-reader", kind="template")
    assert t_run.exit == 0, t_run.err
    f_out, t_out = json.loads(f_run.stdout), json.loads(t_run.stdout)
    # Neither reader mutates its inputs: the case request bytes and every ledger file.
    assert request.read_bytes() == request_bytes
    assert all(value.pop("input_unchanged") is True for value in f_out.values())
    coverage = {f"{row['corpus']}/{row['case']}": row for row in installed.manifest["coverage"]["cases"]}
    assert coverage.keys() == cases.keys()
    outcomes = {"valid": 0, "refused": 0, "template_policy": 0}
    for key, item in cases.items():
        corpus, case, row = item["corpus"], item["case"], coverage[key]
        canonical = json.dumps(case, sort_keys=True, separators=(",", ":")).encode()
        assert pair.sha(canonical) == row["source_case_sha256"], key
        assert (row["valid"], row["target"]) == (case["valid"], case["target"]), key
        f, t = f_out[key], t_out[key]
        if not case["valid"]:
            assert f == {"error": expected_f_error(corpus, case)}, (key, f)
            assert set(t) == {"error"} and case["error"] in t["error"], (key, t)
            outcomes["template_policy" if case["target"] == "template_policy" else "refused"] += 1
            continue
        outcomes["valid"] += 1
        if corpus == "protocol_v2.json":
            for field, value in case["expected"].items():
                assert f[field] == value, (key, field, f[field])
                assert t[field] == value, (key, field, t[field])
            continue
        for run, fields in case.get("expected", {}).items():
            for field, value in fields.items():
                assert f["runs"][run][field] == value, (key, run, field)
                assert t["runs"][run][field] == value, (key, run, field)
        for field in ("head", "membership"):
            if field in case:
                assert f[field] == case[field], (key, field)
                assert t[field] == case[field], (key, field)
    assert outcomes == {"valid": 46, "refused": 101, "template_policy": 3}
    by_corpus = {}
    for item in cases.values():
        key = (item["corpus"], item["case"]["valid"])
        by_corpus[key] = by_corpus.get(key, 0) + 1
    assert by_corpus == {("protocol_v2.json", True): 6, ("protocol_v2.json", False): 10,
                         ("autonomy_v1.json", True): 40, ("autonomy_v1.json", False): 94}
    # Original D023 corpus invariants, from the pinned predecessor corpora themselves.
    original = {n: json.loads((pair.FIXTURES / n).read_bytes()) for n in ("protocol_v2.json", "autonomy_v1.json")}
    assert original["protocol_v2.json"]["schema"] == "template.conformance/2"
    assert original["autonomy_v1.json"]["schema"] == "template.autonomy-conformance/1"
    assert [len(original[n]["cases"]) for n in ("protocol_v2.json", "autonomy_v1.json")] == [16, 128]
    assert len({c["name"] for c in original["autonomy_v1.json"]["cases"]}) == 128
    assert len({c["name"] for c in original["protocol_v2.json"]["cases"]}) == 16
    assert sum(c["valid"] for c in original["autonomy_v1.json"]["cases"]) == 40
    native = {n: json.loads(pair.require(pair.CORPORA[n])) for n in original}
    assert native["protocol_v2.json"]["schema"] == "template.conformance/3"
    assert native["autonomy_v1.json"]["schema"] == "template.autonomy-conformance/2"
    old_names = {n: {c["name"] for c in original[n]["cases"]} for n in original}
    new_names = {n: {c["name"] for c in native[n]["cases"]} for n in native}
    assert new_names["protocol_v2.json"] == old_names["protocol_v2.json"]
    added = new_names["autonomy_v1.json"] - old_names["autonomy_v1.json"]
    assert old_names["autonomy_v1.json"] <= new_names["autonomy_v1.json"] and len(added) == 6
    # Predecessor accounting through the preserved mapping, never regenerated history.
    old = {f"{n}/{name}" for n, names in old_names.items() for name in names}
    prior = {k for k, row in coverage.items() if row["prior_D023_case"]}
    assert len(old) == len(prior) == installed.manifest["coverage"]["D023_predecessors"] == 144
    assert prior == old
    assert {k.split("/", 1)[1] for k in coverage.keys() - prior} == added

    def tally(keys):
        counts = {}
        for key in keys:
            counts[coverage[key]["development_disposition"]] = counts.get(coverage[key]["development_disposition"], 0) + 1
        return counts

    # The 144-predecessor subset and the 150-case totals are separate accounts.
    assert tally(prior) == {"retained_native": 129, "converted_native": 12, "new_native_refusal": 3}
    assert tally(coverage.keys() - prior) == {"new_native_refusal": 5, "new_native_positive": 1}
    assert tally(coverage) == {"retained_native": 129, "converted_native": 12, "new_native_refusal": 8,
                               "new_native_positive": 1}
    assert {coverage[k]["target"] for k in prior if coverage[k]["target"] == "template_policy"} == {"template_policy"}
    assert sum(coverage[k]["target"] == "template_policy" for k in prior) == 3
    installed.synthetic("corpus-reader-total", overlaps=["f-reader", "t-reader"],
                        elapsed=round(f_run.elapsed + t_run.elapsed, 3), outcomes=outcomes)


TEMPLATE_POLICY = ["legacy-prefix-new-suffix", "illegal-version-reversal", "legacy-nonattempt-prefix-is-preserved"]


@pytest.mark.parametrize("name", TEMPLATE_POLICY)
def test_p02_template_policy_history_refuses_installed_execution(installed, tmp_path, name):
    corpus = "autonomy_v1.json" if name.startswith("legacy-nonattempt") else "protocol_v2.json"
    case = next(c for c in json.loads(pair.require(pair.CORPORA[corpus]))["cases"] if c["name"] == name)
    project = make_project(installed, outside(tmp_path), autonomy=corpus == "autonomy_v1.json")
    project.ledger.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in case["events"]))
    project.commit_all("owned historical bytes")
    before = project.snapshot()
    for verb in ("run", "preflight", "status"):
        result = project.f(verb, project.root, label=f"{verb}-refused")
        assert result.exit in (1, 2), result.text
        assert "unsupported legacy history; use pinned v4" in result.text + result.err, result.text
    check = project.tledger("check", label="t-check")
    assert check.exit != 0 and case["error"] in check.text + check.err, check.text + check.err
    assert project.snapshot() == before
    assert attempts(project) == {} and not (project.root / "local_state/frutlups").exists()


# ------------------------------------------------------------------ P11

def wait_for(path, seconds=120):
    import time
    deadline = time.monotonic() + seconds
    while not path.exists():
        assert time.monotonic() < deadline, f"timed out waiting for {path.name}"
        time.sleep(0.2)


def process_alive(pid):
    import ctypes
    kernel = ctypes.windll.kernel32
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        kernel.CloseHandle(handle)


def test_p11_live_owned_child_writer_exclusion_then_owner_death(installed, tmp_path):
    import threading
    project = make_project(installed, outside(tmp_path), holistic=False)
    pid_file = project.root / "local_state/coder.pid"
    project.plan({"coder": [{"pair": {"record_pid": str(pid_file), "sleep": 600}}]})
    project.admit()
    holder = {}
    owner = threading.Thread(target=lambda: holder.update(
        result=project.run_json(label="owner-killed", timeout=45)))
    owner.start()
    wait_for(pid_file)
    child = int(pid_file.read_text())
    assert process_alive(child)  # the fake coder seat is genuinely still running
    # A second public writer and a T mutation both refuse while the first owns the repository.
    second = project.run_json(label="second-writer")
    assert (second.exit, second.rows[-1]["detail"]) == (
        2, "another writer owns the repository; status remains available"), second.text
    assert project.f("status", project.root, label="status-during").exit == 0
    t_write = project.tledger("reconcile", label="t-during")
    assert t_write.exit != 0, t_write.text
    owner.join()
    killed = holder["result"]
    assert killed.timed_out and killed.exception is None  # the owner process tree was terminated
    import time
    for _ in range(50):
        if not process_alive(child):
            break
        time.sleep(0.1)
    assert not process_alive(child)  # the owned seat died with its owner
    started = [row for row in project.events() if row["ev"] == "attempt_started" and row["role"] == "coder"]
    finished = {row["invocation"] for row in project.events() if row["ev"] == "attempt_finished"}
    assert len(started) == 1 and started[0]["invocation"] not in finished
    before = project.snapshot()
    for verb in ("run", "preflight"):
        result = project.f(verb, project.root, label=f"after-death-{verb}")
        assert (result.exit, result.text) == (2, "preflight: unresolved invocation prevents admission\n"), result.text
    assert project.snapshot() == before
    # Idle row 13: a done label cannot settle history holding an unresolved attempt.
    relabel(project, M001="done")
    labelled = project.snapshot()
    refused = project.f("status", project.root, label="done-label-unsettled")
    assert (refused.exit, refused.text, refused.err) == (1, "internal: LedgerError: inactive run history requires "
                                                          "completed milestone and settled attempts; owner must "
                                                          "inspect\n", ""), refused.text
    assert project.snapshot() == labelled
    relabel(project, M001="active")
    status = project.f("status", project.root, label="after-death-status")
    assert f"unresolved invocation: {started[0]['invocation']}" in status.text
    assert project.counts()["coder"] == 1  # never redispatched
    usage = project.f("status", project.root, "--usage", "--json", label="after-death-usage")
    slice_row = next(r for r in usage.rows if r["action"] == "usage" and r.get("slice") == "M001-S01")
    assert slice_row["unresolved_attempts"] == 1 and slice_row["completeness"] != "complete"


def shadow_dir(tmp_path, installed):
    """A PATH directory whose git.exe and python.exe are decoys that always fail."""
    import shutil
    folder = tmp_path / "shadow"
    folder.mkdir()
    for name in ("git", "python"):
        shutil.copyfile(pair.toolchain()["fake_console_launcher"], folder / f"{name}.exe")
        (folder / f"{name}-script.py").write_text(f'#!"{installed.python}" -I -B\nraise SystemExit(86)\n',
                                                  encoding="utf-8")
    return folder


@pytest.mark.parametrize("path", ["shadowed", "missing"])
def test_p11_selected_python_and_configured_git_despite_hostile_path(installed, tmp_path, path):
    project = make_project(installed, outside(tmp_path), holistic=False)
    project.admit()
    system = Path(installed.env()["PATH"].split(";")[-1])
    value = str(system) if path == "missing" else f"{shadow_dir(tmp_path, installed)};{installed.env()['PATH']}"
    env = installed.env(PATH=value)
    result = project.f("preflight", project.root, env=env, label="preflight")
    assert (result.exit, result.text) == (0, "preflight: ok\n"), result.text + result.err
    result = project.run_json(env=env, label="run")
    assert (result.exit, result.rows[-1]) == (0, DONE), result.text
    receipt = next(row for row in project.events() if row["ev"] == "verified")
    value = json.loads((project.root / receipt["receipt"]).read_bytes())
    assert value["runtime"]["python"] == "3.14.3" and value["runtime"]["source"] == "project.local"
    project.check()


INVALID_SELECTIONS = {
    "missing-git": ("frutlups.local.toml", "git", "configured Git must name an existing absolute executable file"),
    "missing-python": ("project.local.toml", "python", "project.local.toml python must name an existing absolute executable"),
    "custom-ledger": ("frutlups.toml", "ledger", "runner admission requires the canonical ledger"),
    "runtime-drift": ("roadmap.yaml", "runtime", "project Python 3.99 required"),
}


@pytest.mark.parametrize("case", list(INVALID_SELECTIONS))
def test_p11_invalid_selection_refuses_without_fallback(installed, tmp_path, case):
    project = make_project(installed, outside(tmp_path), holistic=False)
    project.admit()
    name, key, message = INVALID_SELECTIONS[case]
    target = project.root / name
    text = target.read_text("utf-8")
    if case == "missing-git":
        text = text.replace(json.dumps(str(installed.git)), json.dumps(str(tmp_path / "absent" / "git.exe")))
    elif case == "missing-python":
        text = text.replace(json.dumps(str(installed.python)), json.dumps(str(tmp_path / "absent" / "python.exe")))
    elif case == "custom-ledger":
        # The same valid grant history under a non-canonical configured path.
        (project.root / "05_governance/other.jsonl").write_bytes(project.ledger.read_bytes())
        text = text.replace('ledger = "05_governance/ledger.jsonl"', 'ledger = "05_governance/other.jsonl"')
    else:
        data = pair.yaml.safe_load(text)
        data["runtime"] = {"python": "3.99"}
        text = pair.yaml.safe_dump(data)
    target.write_text(text, encoding="utf-8")
    if name in ("frutlups.toml", "roadmap.yaml"):
        project.commit_all(f"owner changes {key}")
    before = project.snapshot()
    result = project.f("run", project.root, label=f"{case}-run")
    assert result.exit in (1, 2) and message in result.text + result.err, (case, result.text + result.err)
    preflight = project.f("preflight", project.root, label=f"{case}-preflight")
    if case != "custom-ledger":
        assert preflight.exit == 2 and message in preflight.text, (case, preflight.text)
    assert project.snapshot() == before
    assert attempts(project) == {} and not (project.root / "local_state/frutlups").exists()


def test_p11_stop_precedes_pending_recovery_and_checker_never_dispatches(installed, tmp_path):
    project = make_project(installed, outside(tmp_path), holistic=False)
    project.admit()
    fault_run(project, "M001-S01", "before-recovery", "exception")
    head, roles = project.head(), attempts(project)
    (project.root / "STOP").write_text("owner stop\n", encoding="utf-8")
    before = project.snapshot()
    for verb in ("run", "preflight"):
        result = project.f(verb, project.root, label=f"stop-{verb}")
        assert result.exit == 2, result.text
        assert "STOP exists; the owner must remove it to run" in result.text, result.text
    assert project.snapshot() == before and project.head() == head
    (project.root / "STOP").unlink()
    # A pending intent with a successful integrity check is still not permission to dispatch.
    result = project.run_json(label="recovered")
    assert (result.exit, result.rows[-1]) == (0, DONE), result.text
    assert attempts(project) == roles and project.head() != head
    assert [row["state"] for row in commit_rows(project, "rows")] == ["completed"]


# ------------------------------------------------------------------ P12

GRANT_REFUSALS = {
    "missing-grant": (1, {"action": "stop", "reason": "internal",
                          "detail": "M001: LedgerError: profile requires current grant; owner must inspect"}),
    "exhausted-grant": (1, {"action": "stop", "reason": "internal",
                            "detail": "M001-S01: ValueError: run allowance exhausted; owner must inspect"}),
    "wrong-scope": (1, {"action": "stop", "reason": "internal",
                        "detail": "M001-S01: LedgerError: slice outside current grant; owner must inspect"}),
    "changed-authority": (2, {"action": "preflight", "ok": False,
                              "detail": "run_admitted: immutable evidence drift: 05_governance/approval-R001.md"}),
    "legacy-roadmap": (2, {"action": "preflight", "ok": False, "detail":
                           "unsupported roadmap schema; expected frutlups.roadmap/2; use pinned v4 for legacy projects"}),
    "unknown-ledger": (2, {"action": "preflight", "ok": False, "detail":
                           "line 2: invalid schema or event: unknown ledger schema; expected frutlups.ledger/2"}),
}


@pytest.mark.parametrize("case", list(GRANT_REFUSALS))
def test_p12_grant_and_schema_refusals_precede_probes_jobs_and_verifier(installed, tmp_path, case):
    milestones = [("M001", "active", ["M001-S01", "M001-S02"])]
    project = make_project(installed, outside(tmp_path), holistic=False, milestones=milestones)
    if case == "exhausted-grant":
        project.admit(slices=("M001-S01", "M001-S02"), jobs=2)
        assert project.f("preflight", project.root, label="spend").exit == 0  # two probes spend both jobs
    elif case == "wrong-scope":
        project.admit(slices=("M001-S02",))
    elif case in ("changed-authority", "unknown-ledger"):
        project.admit(slices=("M001-S01", "M001-S02"))
        if case == "changed-authority":
            path = project.root / "05_governance/approval-R001.md"
            path.write_text(path.read_text("utf-8") + "Edited after the grant.\n", encoding="utf-8")
        else:
            row = dict(project.events()[-1], schema="frutlups.ledger/3")
            with project.ledger.open("ab") as stream:
                stream.write(json.dumps(row).encode() + b"\n")
    elif case == "legacy-roadmap":
        data = pair.yaml.safe_load((project.root / "roadmap.yaml").read_text("utf-8"))
        data["schema"] = "frutlups.roadmap/1"
        data.pop("autonomy")
        (project.root / "roadmap.yaml").write_text(pair.yaml.safe_dump(data), encoding="utf-8")
        project.commit_all("owner declares an unsupported schema")
    roles, before = attempts(project), project.snapshot()
    result = project.run_json(label=case)
    assert (result.exit, result.rows) == (GRANT_REFUSALS[case][0], [GRANT_REFUSALS[case][1]]), result.text
    # No probe, seat, verifier, job or ledger effect after the refusal.
    assert project.snapshot() == before and attempts(project) == roles
    assert project.counts().get("coder") is None and "verification" not in roles
    if case != "exhausted-grant":
        assert not (project.root / "local_state/frutlups").exists()


def test_p12_old_receipt_schema_refuses_before_probe_job_or_verifier(installed, tmp_path):
    """Controlled bad input only: a correctly hashed receipt/1 is never promoted to history.

    Two read-only controls show the refusal is the schema check rather than a hash failure:
    the same bytes under a wrong recorded hash give a different diagnostic, and only the
    schema value differs between the refused receipt and the receipt/2-labelled control.
    """
    project = make_project(installed, outside(tmp_path), holistic=False)
    project.admit()
    sid = "M001-S01"
    assert project.t("prompt", sid, "--root", project.root, label="t-prompt").exit == 0
    (project.root / "07_app/result.txt").write_text("manual output\n", encoding="utf-8")
    assert project.tledger("coded", sid, label="t-coded").exit == 0
    coded = next(r for r in project.events() if r["ev"] == "coded")
    rel = "05_governance/reviews/m001/M001-S01_r1_verification.json"
    ledger_before = project.ledger.read_bytes()

    def place(schema, recorded_sha=None):
        receipt = {"schema": schema, "slice": sid, "round": 1, "t": "2026-10-18T00:00:00Z",
                   "base_commit": project.head(), "tree_dirty_before": True, "tree_clean_after": False,
                   "ok": True, "manifest": coded["manifest"],
                   "commands": [{"label": "full", "argv": ["python", "-c", "print('verified')"], "exit": 0,
                                 "secs": 0.1, "stdout_tail": "verified\n", "stderr_tail": "", "timed_out": False}]}
        raw = json.dumps(receipt).encode() + b"\n"
        (project.root / rel).write_bytes(raw)
        event = {"schema": "frutlups.ledger/2", "t": "2026-10-18T00:00:01Z", "by": "architect", "ev": "verified",
                 "slice": sid, "round": 1, "receipt": rel, "sha": recorded_sha or pair.sha(raw), "ok": True}
        project.ledger.write_bytes(ledger_before + json.dumps(event).encode() + b"\n")
        return raw

    raw = place("frutlups.receipt/1")
    roles, before = attempts(project), project.snapshot()
    expected = {"run": (2, '{"action": "preflight", "ok": false, "detail": "verified: receipt identity mismatch"}\n', ""),
                "preflight": (2, "preflight: verified: receipt identity mismatch\n", ""),
                "status": (1, "internal: LedgerError: verified: receipt identity mismatch; owner must inspect\n", "")}
    for verb, result_expected in expected.items():
        result = project.f(verb, project.root, *(["--json"] if verb == "run" else []), label=f"old-receipt-{verb}")
        assert (result.exit, result.text, result.err) == result_expected, (verb, result.text, result.err)
        assert project.snapshot() == before
    assert attempts(project) == roles and "verification" not in roles
    assert not (project.root / "local_state/frutlups").exists()
    check = project.tledger("check", label="t-check-old-receipt")
    # The final T consumer names the legacy receipt schema itself.
    assert (check.exit, check.text, check.err) == (
        2, "", "error: verified M001-S01: unsupported legacy receipt; use pinned v4\n"), (check.text, check.err)
    # Control 1: identical receipt/1 bytes under a wrong recorded hash refuse differently.
    place("frutlups.receipt/1", recorded_sha="0" * 64)
    assert pair.sha((project.root / rel).read_bytes()) == pair.sha(raw)
    before = project.snapshot()
    wrong_hash = project.f("status", project.root, label="wrong-hash-status")
    assert (wrong_hash.exit, wrong_hash.text, wrong_hash.err) == (
        1, f"internal: LedgerError: verified: immutable evidence drift: {rel}; owner must inspect\n", ""), (
        wrong_hash.text, wrong_hash.err)
    assert project.snapshot() == before
    # Control 2: only the schema value differs. F's status fold no longer refuses the identity,
    # while the final T checker rejects the field set and F's public run refuses through that
    # checker with no effects. Negative evidence only, never producer history.
    place("frutlups.receipt/2")
    before = project.snapshot()
    relabelled = project.f("status", project.root, label="schema-control-status")
    assert (relabelled.exit, relabelled.text, relabelled.err) == (
        0, "M001-S01 r1 reviewing\nnext: M001-S01\nrun R001 revision=0 stop=M001-S01 state=open remaining "
           "jobs=20 transport_retries=1 format_retries=1 seconds=900\n", ""), (relabelled.text, relabelled.err)
    t_control = project.tledger("check", label="t-check-schema-control")
    assert (t_control.exit, t_control.text, t_control.err) == (
        2, "", "error: verified M001-S01: invalid receipt fields\n"), (t_control.text, t_control.err)
    # F's public run consults that project checker before any probe or dispatch.
    run = project.run_json(label="schema-control-run")
    assert (run.exit, run.rows, run.err) == (1, [{"action": "stop", "reason": "internal", "detail":
                                                  "M001-S01: LedgerError: project checker refused the project; "
                                                  "owner must inspect"}], ""), run.text
    assert project.snapshot() == before
    assert attempts(project) == roles and not (project.root / "local_state/frutlups").exists()


FSYNC_FAULT = r'''
"""Named fsync fault: the first durable write after the real coder seat returns fails."""
import json, os, sys
from pathlib import Path
from frutlups.cli import main
from frutlups.seats import pi

reached, armed = Path(sys.argv[2]), {"on": False}
run = pi.PiSeat.run
def seat(self, job, config):
    result = run(self, job, config)
    if b"Reply with OK only." not in job.prompt_path.read_bytes():
        armed["on"] = True  # the coder seat, not a readiness probe
    return result
pi.PiSeat.run = seat
fsync = os.fsync
def guarded(fd):
    if armed["on"] and not reached.exists():
        reached.write_text(json.dumps({"boundary": "first fsync after coder seat"}), encoding="utf-8")
        raise OSError(5, "owned fsync fault")
    return fsync(fd)
os.fsync = guarded
main(["run", sys.argv[1], "--json"])
'''


def test_p12_reservation_precedes_seat_and_fsync_fault_is_not_refunded(installed, tmp_path):
    project = make_project(installed, outside(tmp_path), holistic=False)
    observed = project.root / "local_state/reservation.json"
    project.plan({"coder": [{"pair": {"observe_reservation": str(observed)}}]})
    project.admit()
    reached = project.out / "fsync-reached.json"
    result = project.f(argv=[installed.python, "-I", "-B", "-c", FSYNC_FAULT, project.root, reached],
                       label="fsync-fault")
    assert reached.exists(), result.text
    assert result.exit == 1 and "owned fsync fault" in result.rows[-1]["detail"], result.text
    # The seat itself saw its own durable, unfinished reservation before acting.
    seen = json.loads(observed.read_text("utf-8"))
    assert seen["role"] == "coder" and seen["unfinished"] is True
    starts = [row for row in project.events() if row["ev"] == "attempt_started" and row["role"] == "coder"]
    finished = {row["invocation"] for row in project.events() if row["ev"] == "attempt_finished"}
    assert len(starts) == 1 and seen["invocation"] == starts[0]["invocation"]
    assert starts[0]["invocation"] not in finished
    status = project.f("status", project.root, "--json", label="status")
    run = next(row for row in status.rows if row["action"] == "run_status")
    assert run["remaining"]["jobs"] == 20 - charged_jobs(project)  # reservation stays charged
    before = project.snapshot()
    refused = project.run_json(label="restart")
    assert refused.exit == 2 and "unresolved invocation prevents admission" in refused.text
    assert project.snapshot() == before and project.counts()["coder"] == 1


def test_p12_transport_retry_extension_and_unknown_metrics_keep_charges(installed, tmp_path):
    project = make_project(installed, outside(tmp_path), holistic=False, plan={"coder": [
        {"failure": "transport"},
        {"writes": {"07_app/result.txt": "after retry\n"}, "pair": {"usage": "unknown"}}]})
    project.admit(jobs=4, transport=1)
    result = project.run_json(label="exhausting")
    first = balances(project, "after-retry")["R001"]
    assert project.counts()["coder"] == 2, result.text
    assert first["remaining"]["transport_retries"] == 0
    # The owner extends the same run; prior consumption is retained exactly.
    rel = project.authority("approval-R001-ext")
    project.commit_all("owner extension authority")
    extended = project.tledger("run", "extend", "R001", "--revision", 1, "--jobs", 6, "--seconds", 300,
                               "--authority", rel, "--reason", "Owner extends R001", label="extend")
    assert (extended.exit, extended.text) == (0, "run_extended R001\n"), extended.err
    after = balances(project, "extended")["R001"]
    assert after["remaining"]["jobs"] == first["remaining"]["jobs"] + 6
    assert after["revision"] == 1
    result = project.run_json(label="finish")
    assert (result.exit, result.rows[-1]) == (0, DONE), result.text
    assert project.counts()["coder"] == 2  # the completed retry is not repeated
    # Missing job logs do not change durable accounting; unknown metrics stay unknown.
    # Bound job evidence is never silently treated as optional: its absence refuses.
    jobs = project.root / "local_state/frutlups/jobs"
    aside = project.root / "local_state/jobs-aside"
    jobs.rename(aside)
    missing = project.f("status", project.root, "--usage", "--json", label="usage-missing-logs")
    assert missing.exit == 1 and "missing regular evidence: local_state/frutlups/jobs/" in missing.text
    aside.rename(jobs)
    usage = project.f("status", project.root, "--usage", "--json", label="usage")
    row = next(r for r in usage.rows if r["action"] == "usage" and r.get("slice") == "M001-S01")
    assert row["completeness"] == "partial" and row["unresolved_attempts"] == 0
    final = balances(project, "final")["R001"]
    assert final["remaining"]["jobs"] == 4 + 6 - charged_jobs(project)


# ------------------------------------------------------------------ P13

SLOW = ["python", "-c", "import time; time.sleep(2.5); print('verified slowly')"]


def occurrence(raw_lines, ev, round_no=None, scope=None):
    for number in range(len(raw_lines) - 1, -1, -1):
        row = json.loads(raw_lines[number])
        if row["ev"] == ev and (round_no is None or row.get("round") == round_no):
            return {"line": number + 1, "raw_sha256": pair.sha(raw_lines[number])}
    raise AssertionError(ev)


def historical_history(project):
    """A real final-T history whose accepted round-2 receipt exceeds its frozen deadline."""
    sid = "M001-S01"
    project.t("prompt", sid, "--root", project.root, label="t-prompt-r1")
    (project.root / "07_app/history.txt").write_text("first attempt\n", encoding="utf-8")
    assert project.tledger("coded", sid, label="t-coded-r1").exit == 0
    verify = project.t("verify", sid, "--root", project.root, "--timeout", "60", label="t-verify-r1")
    assert verify.exit == 0, verify.text + verify.err
    assert project.t("prompt", sid, "--review", "--root", project.root, label="t-review-r1").exit == 0
    report = f"05_governance/reviews/m001/{sid}_r1_review.md"
    (project.root / report).write_text(review_report(sid, 1, verdict="blocked"), encoding="utf-8")
    recorded = project.tledger("record", report, label="t-record-blocked")
    assert recorded.exit == 0, recorded.text + recorded.err
    authority = "00_brief/historical-authority.md"
    (project.root / authority).write_text("Owner authority for the historical round.\n", encoding="utf-8")
    project.commit_all("owner historical authority")
    resolved = project.tledger("resolve", sid, "--reason", "Owner resolved the historical block",
                               "--authority", authority, "--by", "human", label="t-resolve")
    assert resolved.exit == 0, resolved.text + resolved.err
    assert project.t("prompt", sid, "--root", project.root, label="t-prompt-r2").exit == 0
    (project.root / "07_app/history.txt").write_text("historical accepted work\n", encoding="utf-8")
    notes = "05_governance/reviews/m001/M001-S01_r2_notes.md"
    (project.root / notes).write_text("Coder notes: bounded historical action, no new allowance.\n", encoding="utf-8")
    coded = project.tledger("coded", sid, "--notes", notes, label="t-coded-r2")
    assert coded.exit == 0, coded.text + coded.err
    # T's actual verifier, with an owner-chosen longer allowance than the frozen deadline.
    verify = project.t("verify", sid, "--root", project.root, "--timeout", "60", label="t-verify-r2")
    assert verify.exit == 0, verify.text + verify.err
    receipt = next(r for r in reversed(project.events()) if r["ev"] == "verified")
    value = json.loads((project.root / receipt["receipt"]).read_bytes())
    frozen, _ = envelope_of(project, sid, 2)
    assert value["ok"] and value["commands"][0]["secs"] > frozen["timeout_seconds"]
    assert project.t("prompt", sid, "--review", "--root", project.root, label="t-review-r2").exit == 0
    report = f"05_governance/reviews/m001/{sid}_r2_review.md"
    (project.root / report).write_text(review_report(sid, 2), encoding="utf-8")
    assert project.tledger("record", report, label="t-record-pass").exit == 0
    accepted = project.tledger("accept", sid, "--commit", label="t-accept")
    assert accepted.exit == 0, accepted.text + accepted.err
    return authority


def assessment(project, authority, *, wrong_authority=False):
    prefix = project.ledger.read_bytes()
    lines = prefix.splitlines(keepends=True)
    resolved = next(json.loads(line) for line in lines if json.loads(line)["ev"] == "resolved")
    basis = "00_brief/historical-basis.md"
    (project.root / basis).write_text("Human judgement: the round-2 overrun was permitted.\n", encoding="utf-8")
    refs = [dict(ref) for ref in resolved["authority"]]
    if wrong_authority:
        refs = [{"path": "00_brief/CONTEXT.md", "sha": pair.sha((project.root / "00_brief/CONTEXT.md").read_bytes())}]
    value = {"schema": "frutlups.historical-eligibility/1",
             "ledger_prefix": {"bytes": len(prefix), "raw_sha256": pair.sha(prefix)},
             "assessment": {"path": basis, "raw_sha256": pair.sha((project.root / basis).read_bytes())},
             "entries": [{"resolution": occurrence(lines, "resolved"),
                          "target": {ev: occurrence(lines, ev, 2 if ev != "accepted" else None)
                                     for ev in ("prompt", "coded", "verified", "reviewed", "accepted")},
                          "cap_seconds": 60, "authority": refs}]}
    rel = "00_brief/historical-selection-wrong.json" if wrong_authority else "00_brief/historical-selection.json"
    (project.root / rel).write_text(json.dumps(value), encoding="utf-8")
    return rel, pair.sha((project.root / rel).read_bytes())


def test_p13_historical_assessment_pair_on_supported_final_template_history(installed, tmp_path):
    project = make_project(installed, outside(tmp_path), autonomy=False, verification=SLOW)
    data = pair.yaml.safe_load((project.root / "roadmap.yaml").read_text("utf-8"))
    data["verification"]["timeout_seconds"] = 1
    (project.root / "roadmap.yaml").write_text(pair.yaml.safe_dump(data), encoding="utf-8")
    project.commit_all("owner sets a one-second verification deadline")
    authority = historical_history(project)
    project.check("history-check")
    path, digest = assessment(project, authority)
    wrong, wrong_digest = assessment(project, authority, wrong_authority=True)
    data = pair.yaml.safe_load((project.root / "roadmap.yaml").read_text("utf-8"))
    data["autonomy"] = {"schema": "frutlups.autonomy/1"}
    (project.root / "roadmap.yaml").write_text(pair.yaml.safe_dump(data), encoding="utf-8")
    project.commit_all("owner adopts the historical assessment and declares the profile")
    project.admit()
    evidence = {p: (project.root / p).read_bytes() for p in project.git("ls-files").decode().splitlines()
                if p.startswith("05_governance/reviews/")}
    before = project.snapshot()
    strict = project.run_json(label="strict")
    assert strict.exit == 1 and "accepted receipt exceeded frozen deadline" in strict.rows[-1]["detail"], strict.text
    for label, flags, detail in [
            ("half", ["--historical-assessment", path],
             "--historical-assessment and --historical-assessment-sha must be given together"),
            ("mismatch", ["--historical-assessment", path, "--historical-assessment-sha", "0" * 64],
             "historical assessment: raw hash mismatch"),
            ("wrong-authority", ["--historical-assessment", wrong, "--historical-assessment-sha", wrong_digest],
             "historical assessment: authority is not a resolution member")]:
        result = project.run_json(*flags, label=label)
        assert (result.exit, result.rows) == (2, [{"action": "preflight", "ok": False, "detail": detail}]), result.text
    assert project.snapshot() == before
    flags = ["--historical-assessment", path, "--historical-assessment-sha", digest]
    result = project.f("preflight", project.root, *flags, label="preflight-selected")
    assert (result.exit, result.text) == (0, "preflight: ok\n"), result.text
    result = project.run_json(*flags, label="selected")
    assert (result.exit, result.rows[-1]) == (0, DONE), result.text
    assert evs(project).count("milestone_done") == 1
    # Eligibility changes no historical evidence bytes and grants no allowance.
    assert all((project.root / p).read_bytes() == raw for p, raw in evidence.items())
    run = balances(project, "after-selected")["R001"]
    assert run["remaining"]["jobs"] == 20 - charged_jobs(project)
    # The same selection never excuses a new overrun: F's own verification still times out.
    after = project.snapshot()
    again = project.run_json(*flags, label="replay")
    assert again.rows == [DONE] and project.snapshot() == after
    project.check("final-check")
    data = pair.yaml.safe_load((project.root / "roadmap.yaml").read_text("utf-8"))
    later = json.loads(json.dumps(data["milestones"][0]))
    later.update(id="M002", holistic_review=False)
    later["slices"][0]["id"] = "M002-S01"
    data["milestones"][0]["status"] = "done"
    data["milestones"].append(later)
    (project.root / "roadmap.yaml").write_text(pair.yaml.safe_dump(data), encoding="utf-8")
    project.holistic = False
    project.admit(milestone="M002", run="R002", slices=("M002-S01",), rounds=0)
    result = project.run_json(*flags, label="new-work")
    verified = [r for r in project.events() if r["ev"] == "verified" and r["slice"] == "M002-S01"]
    assert len(verified) == 1 and verified[0]["ok"] is False, result.text
    value = json.loads((project.root / verified[0]["receipt"]).read_bytes())
    assert value["commands"][0]["timed_out"] is True
    assert "M002-S01" not in [r.get("slice") for r in project.events() if r["ev"] == "accepted"]


# ------------------------------------------------------------------ P07

def test_p07_partial_routed_review_resumes_from_exact_checkpoint(installed, tmp_path):
    project = make_project(installed, outside(tmp_path), holistic=False, risk="release",
                           plan={"coder": [{"writes": {"07_app/result.txt": "reviewed twice\n"}}],
                                 # Both routed seats report FRUTLUPS_SEAT=reviewer: the
                                 # second dispatch fails, its retry passes.
                                 "reviewer": [{}, {"failure": "transport"}, {}]})
    project.admit(transport=0)
    result = project.run_json(label="partial")
    assert (result.exit, result.rows[-1]) == (1, {"action": "stop", "reason": "internal", "detail":
        "M001-S01: LedgerError: run transport_retries allowance exhausted; owner must inspect"}), result.text
    checkpoints = [row for row in project.events() if row["ev"] == "review_checkpoint"]
    assert len(checkpoints) == 1 and project.counts()["reviewer"] == 2 and project.counts()["coder"] == 1
    assert "reviewed" not in evs(project)
    report = project.root / checkpoints[0]["report"]
    original = report.read_bytes()
    # Changed and missing checkpoint evidence each refuse before any dispatch.
    for label, mutate in (("changed", lambda: report.write_bytes(original + b"tampered\n")),
                          ("missing", report.unlink)):
        mutate()
        before = project.snapshot()
        refused = project.run_json(label=f"checkpoint-{label}")
        assert refused.exit == 2, refused.text
        assert checkpoints[0]["report"] in refused.text, refused.text
        assert project.snapshot() == before
        report.write_bytes(original)
    # Recorded by reference in the extension event; committing it now would move HEAD
    # after verification, which F correctly refuses.
    rel = project.authority("approval-R001-retry")
    extended = project.tledger("run", "extend", "R001", "--revision", 1, "--transport-retries", 1,
                               "--authority", rel, "--reason", "Owner allows one retry", label="extend")
    assert extended.exit == 0, extended.err
    result = project.run_json(label="resumed")
    assert (result.exit, result.rows[-1]) == (0, DONE), result.text
    counts = project.counts()
    assert counts["reviewer"] == 3  # the completed first review is reused, only the failed one reruns
    assert evs(project).count("review_checkpoint") == 2 and evs(project).count("reviewed") == 1
    project.check()


def backlog_agrees(project, label):
    """F's generated projection is exactly what the final T reconciler derives."""
    backlog = project.root / "05_governance/backlog.md"
    before = backlog.read_bytes()
    result = project.tledger("reconcile", label=label)
    assert (result.exit, result.text) == (0, "backlog reconciled\n"), result.text + result.err
    assert backlog.read_bytes() == before
    return before.decode("utf-8")


def test_p07_findings_closure_carry_and_unrelated_pass(installed, tmp_path):
    rows = ["| M001-S01-R1-F1 | P2 | open | Missing edge case. |",
            "| M001-S01-R1-F2 | P3 | carried | Style follow-up closed by a later slice. |",
            "| M001-S01-R1-F3 | P3 | carried | Style follow-up nobody closes. |"]
    slices = ("M001-S01", "M001-S02", "M001-S03")
    project = make_project(installed, outside(tmp_path), holistic=False,
                           milestones=[("M001", "active", list(slices))],
                           plan={"coder": [{"writes": {"07_app/a.txt": "first\n"}},
                                           {"writes": {"07_app/a.txt": "edge case handled\n"}},
                                           {"writes": {"07_app/b.txt": "second slice\n"}},
                                           {"writes": {"07_app/a.txt": "later accepted owner\n"}}],
                                 "reviewer": [{"verdict": "needs_work", "rows": rows},
                                              {"close_ids": ["M001-S01-R1-F1"]},
                                              {"close_ids": ["M001-S01-R1-F2"]}, {}]})
    project.admit(slices=slices)
    result = project.run_json(label="findings")
    assert (result.exit, result.rows[-1]) == (0, DONE), result.text
    text = backlog_agrees(project, "reconcile")
    reviewed = [r for r in project.events() if r["ev"] == "reviewed"]
    source = reviewed[0]
    assert (source["slice"], source["round"]) == ("M001-S01", 1)
    # A later slice (S02) closes S01's finding by its exact original source, hash and ID.
    later = next(r for r in reviewed if r["slice"] == "M001-S02")
    update = f"| {source['report']} | {source['sha']} | M001-S01-R1-F2 | closed_by_review | - |"
    assert update in (project.root / later["report"]).read_text("utf-8")
    for ident in ("M001-S01-R1-F1", "M001-S01-R1-F2"):
        assert f"| {ident} | {'P2' if ident.endswith('F1') else 'P3'} | closed_by_review | " \
               f"{source['report']} ({source['sha']}) |" in text
    # The unrelated S03 pass carries no update rows, so it closes nothing.
    s03 = next(r for r in reviewed if r["slice"] == "M001-S03")
    assert "## Finding updates" not in (project.root / s03["report"]).read_text("utf-8")
    assert f"| M001-S01-R1-F3 | P3 | carried | {source['report']} ({source['sha']}) |" in text
    assert text.count("| carried |") == 1
    # Idle row 06: S03 is the later accepted owner of S01's product path. Housekeeping and a
    # committed owner hotfix remain valid; confirmation is non-writing.
    assert project.git("show", "HEAD:07_app/a.txt") == b"later accepted owner\n"
    project.commit_all("owner housekeeping commit of the derived projection")
    (project.root / "07_app/a.txt").write_text("committed owner hotfix\n", encoding="utf-8")
    project.commit_all("owner hotfix")
    before = project.snapshot()
    assert project.run_json(label="after-hotfix").rows == [DONE]
    assert project.snapshot() == before
    project.status_pair("after-hotfix")
    project.check()


WAIVER = "| M001-S01-R1-F1 | P2 | waived_by_human | Accepted risk recorded by the owner. |"


def test_p07_human_waiver_recorded_and_nonhuman_waiver_refused(installed, tmp_path):
    project = make_project(installed, outside(tmp_path / "manual"), autonomy=False, holistic=False)
    sid = "M001-S01"
    project.t("prompt", sid, "--root", project.root, label="prompt")
    (project.root / "07_app/manual.txt").write_text("manual\n", encoding="utf-8")
    assert project.tledger("coded", sid, label="coded").exit == 0
    assert project.t("verify", sid, "--root", project.root, label="verify").exit == 0
    assert project.t("prompt", sid, "--review", "--root", project.root, label="review").exit == 0
    report = f"05_governance/reviews/m001/{sid}_r1_review.md"
    (project.root / report).write_text(review_report(sid, 1, rows=[WAIVER]), encoding="utf-8")
    refused = project.tledger("record", report, "--by", "architect", label="architect-waiver")
    assert refused.exit != 0 and "waiver requires --by human" in refused.text + refused.err
    recorded = project.tledger("record", report, "--by", "human", label="human-waiver")
    assert recorded.exit == 0, recorded.text + recorded.err
    assert project.tledger("accept", sid, "--commit", label="accept").exit == 0
    assert "| waived_by_human |" in backlog_agrees(project, "reconcile")
    assert "M001-S01 r1 accepted" in project.status_pair("waived")
    # A frutlups seat cannot waive: the same grammar from a reviewer seat refuses.
    seat = make_project(installed, outside(tmp_path / "seat"), holistic=False,
                        plan={"coder": [{"writes": {"07_app/result.txt": "work\n"}}],
                              "reviewer": [{"verdict": "pass", "rows": [WAIVER]}]})
    seat.admit()
    result = seat.run_json(label="seat-waiver")
    assert (result.exit, result.rows[-1]) == (1, {"action": "stop", "reason": "internal", "detail":
        "M001-S01: LedgerError: review format retry allowance exhausted; owner must inspect"}), result.text
    # The seat was told why: its report was refused, never recorded as a waiver.
    repair = (seat.root / "local_state/reviewer-1.md").read_text("utf-8")
    assert repair.startswith("Format repair only.") and "Error: autonomous seats cannot grant human waivers" in repair
    assert "accepted" not in evs(seat) and "reviewed" not in evs(seat)


def test_p07_holistic_block_resolution_and_correction(installed, tmp_path):
    row = "| M001-S01-H1 | P1 | open | Holistic correction needed. |"
    project = make_project(installed, outside(tmp_path), plan={
        "coder": [{"writes": {"07_app/result.txt": "first\n"}},
                  {"writes": {"07_app/result.txt": "corrected after holistic\n"}}],
        "reviewer": [{}, {"close_ids": ["M001-S01-H1"]}],
        "holistic": [{"verdict": "blocked"}, {"verdict": "needs_work", "rows": [row]}, {}]})
    project.admit(jobs=40)
    result = project.run_json(label="blocked")
    assert result.rows[-1]["action"] == "stop" and result.exit == 3, result.text
    assert project.counts()["holistic"] == 1
    before = project.snapshot()
    assert project.run_json(label="blocked-again").rows[-1] == result.rows[-1]
    assert project.snapshot() == before
    decision = project.root / "questions/answered/holistic-decision.md"
    decision.parent.mkdir(parents=True, exist_ok=True)
    decision.write_text("Owner resolves the holistic block.\n", encoding="utf-8")
    project.commit_all("owner holistic decision")
    resolved = project.tledger("resolve", "M001", "--reason", "Owner resolved the holistic block",
                               "--authority", "questions/answered/holistic-decision.md", "--by", "human",
                               label="resolve")
    assert resolved.exit == 0, resolved.err
    result = project.run_json(label="corrected")
    assert (result.exit, result.rows[-1]) == (0, DONE), result.text
    counts = project.counts()
    assert (counts["holistic"], counts["coder"], counts["reviewer"]) == (3, 2, 2)
    text = backlog_agrees(project, "reconcile")
    assert "| M001-S01-H1 | P1 | closed_by_review |" in text
    assert (project.root / "07_app/result.txt").read_text("utf-8") == "corrected after holistic\n"
    assert evs(project).count("milestone_done") == 1
    project.check()


# ------------------------------------------------------------------ P08

TOKEN = "LATE-DEFECT-91c4"
HUGE = "07_app/huge evidence.txt"
LATE = "07_app/zz late small defect.txt"


def test_p08_large_literal_evidence_late_defect_and_exact_staging(installed, tmp_path):
    project = make_project(installed, outside(tmp_path), holistic=False, commit=False)
    for rel in ("07_app/old-name.txt", "07_app/obsolete.txt"):
        (project.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (project.root / rel).write_text(f"tracked {rel}\n", encoding="utf-8")
    project.commit_all("owned tracked inputs")
    # The 12 MiB file is a defect-free large-evidence regression; a separate small file
    # after it in canonical order carries the late defect.
    first = {"generate": {"dir": "07_app/ev", "count": 720},
             "huge": {"path": HUGE, "mib": 12},
             "raw_writes": {LATE: f"late small value {TOKEN}\n".encode().hex()},
             "deletes": ["07_app/obsolete.txt"],
             "moves": {"07_app/old-name.txt": "07_app/new name [renamed].txt"}}
    corrected = b"late small value corrected\n"
    project.plan({
        "coder": [{"pair": first}, {"pair": {"raw_writes": {LATE: corrected.hex()}}}],
        "reviewer": [{"pair": {"inspect": TOKEN}}, {"close_ids": ["M001-S01-R1-F1"], "pair": {"inspect": TOKEN}}]})
    project.admit(jobs=30)
    result = project.run_json(label="large")
    assert (result.exit, result.rows[-1]) == (0, DONE), result.text
    inspected = [json.loads(line) for line in
                 (project.root / "local_state/pair-inspected.jsonl").read_text("utf-8").splitlines()]
    assert len(inspected) == 2
    # Round 1: the reviewer read every bound path, the huge file in full, and only then
    # reached the late small file, where it found the defect.
    assert inspected[0]["found"] == [LATE] and inspected[1]["found"] == []
    for record in inspected:
        assert record["paths"] >= 723 and record["verified"] >= 723 and record["bytes"] > 12 * 10 ** 6
        assert record["prompt_bytes"] <= 49152
        assert record["order"].index(HUGE) < record["order"].index(LATE)
    first_review = next(r for r in project.events() if r["ev"] == "reviewed" and r["round"] == 1)
    assert first_review["verdict"] == "needs_work" and first_review["open"] == ["M001-S01-R1-F1"]
    manifest = json.loads(next(
        (project.root / r["manifest"]["path"]).read_bytes() for r in reversed(project.events()) if r["ev"] == "coded"))
    paths = [row["path"] for row in manifest["changed"]]
    assert paths == sorted(paths) and paths.index(HUGE) < paths.index(LATE)
    assert len(paths) >= 723 and sum(len(p) + 1 for p in paths) > 40000
    kinds = {row["path"]: row["kind"] for row in manifest["changed"]}
    assert kinds["07_app/obsolete.txt"] == kinds["07_app/old-name.txt"] == "deleted"
    assert kinds["07_app/new name [renamed].txt"] == kinds[LATE] == "added"
    late_row = next(row for row in manifest["changed"] if row["path"] == LATE)
    assert late_row["round"] == 2 and late_row["sha"] == pair.sha(corrected)
    assert any(p.startswith("07_app/ev/") and "-leading dash" in p for p in paths)
    assert any("ünïcödé ✓" in p for p in paths) and any("[bracket]" in p for p in paths)
    (approved,) = [r["commit"] for r in commit_rows(project, "rows")]
    committed = set(project.git("diff", "--name-only", "-z", "--no-renames", f"{approved}^", approved)
                    .decode("utf-8").split("\0")) - {""}
    assert set(paths) <= committed
    assert all(p.startswith(("07_app/", "05_governance/", "prompts/")) for p in committed)
    # The approved commit carries the correction of the late file, and the full huge file.
    assert project.git("show", f"{approved}:{LATE}") == corrected
    control = project.installed.run([installed.git, "-C", project.root, "grep", "-c", "-F", "evidence line", approved,
                                     "--", HUGE], project.root, project.out / "grep-control", kind="git")
    assert control.exit == 0 and int(control.stdout.rsplit(b":", 1)[1]) > 800000
    search = project.installed.run([installed.git, "-C", project.root, "grep", "-c", "-F", TOKEN, approved, "--", "07_app"],
                                   project.root, project.out / "grep-token", kind="git")
    assert (search.exit, search.stdout) == (1, b"")
    assert project.git("status", "--porcelain=v1", "-z") == b" M 05_governance/backlog.md\0"
    backlog_agrees(project, "reconcile")
    for prompt in (project.root / "prompts/for_review_agent").glob("M001-S01_r*_review.md"):
        assert len(prompt.read_bytes()) <= 49152
    project.check()


def test_p08_empty_product_selection_approves_only_bound_evidence(installed, tmp_path):
    """A coder seat that changes nothing: the empty manifest is verified and approved alone."""
    project = make_project(installed, outside(tmp_path), holistic=False, plan={"coder": [{}]})
    project.admit()
    head = project.head()
    result = project.run_json(label="empty")
    assert (result.exit, len(result.rows), result.err) == (0, 3, ""), result.text
    verified, accepted, stop = result.rows
    assert {k: verified[k] for k in ("action", "slice", "round", "ok")} == {
        "action": "verified", "slice": "M001-S01", "round": 1, "ok": True}
    assert accepted == {"action": "accepted", "slice": "M001-S01", "round": 1, "commit": project.head()}
    assert stop == DONE
    coded = next(r for r in project.events() if r["ev"] == "coded")
    manifest = json.loads((project.root / coded["manifest"]["path"]).read_bytes())
    assert (manifest["schema"], manifest["slice"], manifest["changed"]) == ("frutlups.manifest/2", "M001-S01", [])
    changed = project.git("diff", "--name-only", head, accepted["commit"]).decode().splitlines()
    assert changed and all(p.startswith(("05_governance/", "prompts/")) for p in changed)
    assert project.git("status", "--porcelain=v1", "-z") == b" M 05_governance/backlog.md\0"
    backlog_agrees(project, "reconcile")
    before = project.snapshot()
    assert project.run_json(label="replay").rows == [DONE]
    assert project.snapshot() == before
    project.check()


# ------------------------------------------------------------------ P09

CANARY = "pair-canary-secret-7d1f93"


def test_p09_pi_beyond_capture_completes_with_truthful_usage(installed, tmp_path):
    project = make_project(installed, outside(tmp_path), holistic=False, plan={
        "coder": [{"writes": {"07_app/result.txt": "flooded but settled\n"},
                   "pair": {"flood": 9 * 1024 * 1024, "usage": "complete"}}],
        "reviewer": [{"pair": {"usage": "complete"}}]})
    project.admit()
    result = project.run_json(label="flood")
    assert (result.exit, result.rows[-1]) == (0, DONE), result.text
    jobs = project.root / "local_state/frutlups/jobs"
    sizes = [p.stat().st_size for p in jobs.rglob("stdout.jsonl")]
    # The retained diagnostic file stops at the 8 MiB cap while the full stream settled.
    assert 8 * 1024 * 1024 in sizes and max(sizes) == 8 * 1024 * 1024
    usage = project.f("status", project.root, "--usage", "--json", label="usage")
    slice_row = next(r for r in usage.rows if r["action"] == "usage" and r.get("slice") == "M001-S01")
    assert slice_row["completeness"] == "complete" and slice_row["unresolved_attempts"] == 0
    milestone_row = next(r for r in usage.rows if r["action"] == "usage" and r.get("milestone") == "M001")
    assert milestone_row["completeness"] == "partial"  # the shared fake probes report partial usage
    project.check()


def git_visible_bytes(project):
    names = project.git("ls-files", "-z", "--cached", "--others", "--exclude-standard").decode().split("\0")
    return b"".join((project.root / n).read_bytes() for n in names if n and (project.root / n).is_file())


@pytest.mark.parametrize("canary", ["secret", "path"])
def test_p09_report_canaries_are_never_disclosed(installed, tmp_path, canary):
    text = CANARY if canary == "secret" else str(tmp_path / "private" / "location.txt")
    project = make_project(installed, outside(tmp_path), holistic=False, plan={
        "coder": [{"writes": {"07_app/result.txt": "reviewed\n"}}],
        "reviewer": [{"pair": {"canary": text}}]})
    project.admit()
    env = installed.env(PAIR_CANARY_TOKEN=CANARY)
    result = project.run_json(env=env, label="canary")
    assert text not in result.text and text.encode() not in git_visible_bytes(project)
    for row in project.events():
        assert text not in json.dumps(row)
    # The private text is refused, not rewritten into an accepted report; the raw output
    # stays only in ignored local job streams for the owner, and the attempt is unresolved.
    assert (result.exit, result.rows[-1]) == (1, {"action": "stop", "reason": "internal", "detail":
        "M001-S01: LedgerError: review output contains private text; inspect retained streams; owner must inspect"}), result.text
    assert "reviewed" not in evs(project) and "accepted" not in evs(project)
    started = [r["invocation"] for r in project.events() if r["ev"] == "attempt_started" and r["role"] == "reviewer"]
    finished = {r["invocation"] for r in project.events() if r["ev"] == "attempt_finished"}
    assert len(started) == 1 and started[0] not in finished


def test_p09_installed_scrub_utility_preserves_report_grammar(installed, tmp_path):
    """Utility evidence only: the public review boundary still refuses private text (above)."""
    private = tmp_path / "private" / "location.txt"
    report = review_report("M001-S01", 1, verdict="needs_work",
                           rows=[f"| M001-S01-R1-F1 | P2 | open | Leak {CANARY} at {private}. |"])
    source = tmp_path / "synthetic-report.md"
    source.write_text(report, encoding="utf-8")
    root = tmp_path / "utility-root"
    root.mkdir()
    result = installed.run([installed.python, "-I", "-B", "-c", pair.SCRUB_UTILITY, source, root, CANARY],
                           tmp_path, tmp_path / "scrub-utility", kind="utility")
    assert result.exit == 0, result.err
    value = json.loads(result.stdout)
    assert CANARY not in value["scrubbed"] and str(private) not in value["scrubbed"]
    assert "<redacted>" in value["scrubbed"] and "<absolute-path>" in value["scrubbed"]
    review = value["review"]
    assert (review["identity"], review["round"], review["verdict"]) == ("M001-S01", 1, "needs_work")
    assert [(f["id"], f["severity"], f["disposition"]) for f in review["findings"]] == [
        ("M001-S01-R1-F1", "P2", "open")]
    assert review["objective_status"] == "not_achieved"


LIMITS = {
    "pi-record": ({"coder": [{"pair": {"final_bytes": 3 * 1024 * 1024}}]}, 3,
                  {"action": "stop", "reason": "seat_output", "detail":
                   "coder: Pi JSONL record exceeds 2 MiB limit (2097152 bytes; limits.pi_record_mib); "
                   "inspect job streams before resuming"}, None),
    "claude-raw": ({"coder": [{"writes": {"07_app/result.txt": "work\n"}}],
                    "reviewer": [{"pair": {"raw_pad": 9 * 1024 * 1024}}]}, 1,
                   {"action": "stop", "reason": "internal", "detail":
                    "M001-S01: LedgerError: review failure needs external inspection; owner must inspect"},
                   "Claude raw stdout exceeds 8 MiB capture limit"),
}


@pytest.mark.parametrize("limit", list(LIMITS))
def test_p09_output_and_record_limits_are_genuine_refusals(installed, tmp_path, limit):
    plan, code, row, bound = LIMITS[limit]
    project = make_project(installed, outside(tmp_path), holistic=False, plan=plan)
    project.admit()
    result = project.run_json(label=limit)
    finished = [r for r in project.events() if r["ev"] == "attempt_finished" and not r["invocation"].startswith("probe-")]
    assert finished and finished[-1]["status"] == "failed" and finished[-1]["usage"]["completeness"] == "unknown"
    assert (result.exit, result.rows[-1]) == (code, row), result.text
    if bound:
        # The bound job result records the genuine capture-limit classification.
        ref = next(e for e in finished[-1]["evidence"] if e["path"].endswith("result.json"))
        value = json.loads((project.root / ref["path"]).read_bytes())
        assert (value["failure_class"], value["diagnostic"]) == ("output", bound)
        assert value["result"]["tokens_in"] is None and value["result"]["tokens_out"] is None
    before = project.snapshot()
    again = project.run_json(label=f"{limit}-again")
    assert project.snapshot() == before and again.rows[-1]["action"] == "stop"
    assert "accepted" not in evs(project)


# ------------------------------------------------------------------ P10

RAW = {
    "07_app/lf.txt": b"alpha\nbeta\n",
    "07_app/crlf.txt": b"alpha\r\nbeta\r\n",
    "07_app/lone-cr.txt": b"alpha\rbeta\r",
    "07_app/embedded-zero.bin": b"alpha\r\n\0beta\r\n",
    "07_app/chunk.txt": b"x" * 65535 + b"\r\n" + b"y" * 10 + b"\r\n",
}


def normalized(raw):
    return pair.sha(raw if b"\0" in raw else raw.replace(b"\r\n", b"\n"))


def test_p10_raw_and_normalized_identities_custom_argv_and_prompt_fallback(installed, tmp_path):
    project = make_project(installed, outside(tmp_path), holistic=False,
                           verification=["git", "rev-parse", "--is-inside-work-tree"],
                           plan={"coder": [{"pair": {"raw_writes": {k: v.hex() for k, v in RAW.items()}}}]},
                           commit=False)
    template = project.root / "prompts/templates/coding_prompt.md"
    template.write_text("# Custom owner coding prompt\n\nObjective: {{objective}}\n", encoding="utf-8")
    project.commit_all("owned custom template and non-Python verification")
    project.admit()
    result = project.run_json(label="raw")
    assert (result.exit, result.rows[-1]) == (0, DONE), result.text
    issued = next(r for r in project.events() if r["ev"] == "prompt")
    prompt = (project.root / issued["path"]).read_bytes()
    assert prompt.startswith(b"# Custom owner coding prompt") and len(prompt) <= 16384
    # The custom template omitted most placeholders; mandatory binding survives by reference.
    envelope = issued["envelope"]
    assert b"## Complete frozen coding envelope" in prompt
    assert envelope["path"].encode() in prompt and envelope["sha"].encode() in prompt
    assert b"Return the typed outcome bound to the invocation supplied at dispatch." in prompt
    frozen = json.loads((project.root / envelope["path"]).read_bytes())
    assert frozen["acceptance"] == ["Replace this example with observable acceptance criteria."]
    assert frozen["allowed_prefixes"] == ["07_app/"]
    dispatched = (project.root / "local_state/coder-0.md").read_bytes()  # what the fake seat received
    assert b"## Autonomous outcome" in dispatched and len(dispatched) <= 16384
    receipt = json.loads((project.root / next(r for r in project.events() if r["ev"] == "verified")["receipt"]).read_bytes())
    assert receipt["ok"] and receipt["commands"][0]["argv"][1:] == ["rev-parse", "--is-inside-work-tree"]
    assert receipt["commands"][0]["stdout_tail"].strip() == "true"
    manifest = json.loads(next((project.root / r["manifest"]["path"]).read_bytes()
                               for r in project.events() if r["ev"] == "coded"))
    stored = {row["path"]: row["sha"] for row in manifest["changed"]}
    for rel, raw in RAW.items():
        assert (project.root / rel).read_bytes() == raw
        assert stored[rel] == normalized(raw)
        assert (stored[rel] == pair.sha(raw)) is (raw == raw.replace(b"\r\n", b"\n") or b"\0" in raw)
    # A clean Git materialization of the approved commit reproduces the evidence identities.
    clone = tmp_path / "clone"
    installed.run([installed.git, "clone", "-q", "--no-local", project.root, clone], tmp_path, tmp_path / "clone-log",
                  kind="git")
    for rel, raw in RAW.items():
        assert normalized((clone / rel).read_bytes()) == stored[rel], rel
    assert (clone / "07_app/crlf.txt").read_bytes() == b"alpha\nbeta\n"
    assert (clone / "07_app/embedded-zero.bin").read_bytes() == RAW["07_app/embedded-zero.bin"]
    project.check()


def until_reviewing(project):
    for number in range(8):
        result = project.run_json("--once", label=f"once-{number}")
        assert result.exit == 0, result.text
        status = project.f("status", project.root, "--json", label=f"once-status-{number}")
        if next(r for r in status.rows if r["action"] == "status")["step"] == "reviewing":
            return
    raise AssertionError("slice never reached review")


DRIFT = {"content": "raw product changed after verification",
         "flags": "HEAD/index changed after verification",
         "head": "HEAD/index changed after verification"}


@pytest.mark.parametrize("drift", list(DRIFT))
def test_p10_same_status_drift_refuses_despite_normalized_equality(installed, tmp_path, drift):
    project = make_project(installed, outside(tmp_path), holistic=False,
                           plan={"coder": [{"pair": {"raw_writes": {"07_app/drift.txt": b"one\r\n".hex()}}}]})
    project.admit()
    until_reviewing(project)
    status = project.git("status", "--porcelain=v1", "-z")
    if drift == "content":
        (project.root / "07_app/drift.txt").write_bytes(b"one\n")  # same normalized evidence hash
        assert normalized(b"one\n") == normalized(b"one\r\n")
    elif drift == "flags":
        project.git("update-index", "--skip-worktree", "00_brief/CONTEXT.md")
    else:
        project.git("commit", "--allow-empty", "-qm", "owner moves HEAD without changing files")
    if drift != "head":
        assert project.git("status", "--porcelain=v1", "-z") == status
    before = project.snapshot()
    result = project.run_json(label="drift")
    detail = DRIFT[drift]
    assert (result.exit, result.rows) == (1, [{"action": "stop", "reason": "internal",
                                               "detail": f"M001-S01: LedgerError: {detail}; owner must inspect"}]), result.text
    assert project.snapshot() == before
    assert "accepted" not in evs(project) and project.counts().get("reviewer") is None


# ------------------------------------------------------------------ P14

INTENDED_TIMEOUTS = {"owner-killed"}  # P11's deliberate owner-process termination


def test_p14_continuity_rehash_and_measured_usefulness(installed, tmp_path):
    assert installed.rehash() == installed.installed_files
    assert pair.inspect_installed(installed, outside(tmp_path))["version"] == "0.4.0"
    rows = [json.loads(line) for line in installed.timings.read_text("utf-8").splitlines()]
    commands = [r for r in rows if r["record"] == "command"]
    synthetic = [r for r in rows if r["record"] == "synthetic"]
    projects = [r for r in rows if r["record"] == "project"]
    assert len(commands) + len(synthetic) + len(projects) == len(rows)
    by_kind = {}
    for row in commands:
        bucket = by_kind.setdefault(row["kind"], {"commands": 0, "elapsed": 0.0})
        bucket["commands"] += 1
        bucket["elapsed"] = round(bucket["elapsed"] + row["elapsed"], 3)
    # Durable attempt seconds from each registered project's own ledger: fake-seat work and
    # F's frozen verification. They run inside public F commands, so they overlap that time.
    seats, verification, attempts_seen = 0.0, 0.0, 0
    for row in projects:
        ledger_path = Path(row["root"]) / "05_governance/ledger.jsonl"
        if not ledger_path.is_file():
            continue
        events = [json.loads(line) for line in ledger_path.read_text("utf-8").splitlines() if line.strip()]
        roles = {e["invocation"]: e["role"] for e in events if e["ev"] == "attempt_started"}
        for event in events:
            if event["ev"] == "attempt_finished" and event.get("usage", {}).get("secs") is not None:
                attempts_seen += 1
                if roles[event["invocation"]] == "verification":
                    verification += event["usage"]["secs"]
                else:
                    seats += event["usage"]["secs"]
    public = by_kind.get("public", {}).get("elapsed", 0.0)
    summary = {
        "actual_commands": {"count": len(commands), "by_kind": by_kind,
                            "note": "each actual owned child once; kinds are disjoint processes"},
        "within_public_F": {"fake_seat_seconds": round(seats, 3), "f_verification_seconds": round(verification, 3),
                            "durable_attempts": attempts_seen,
                            "residual_seconds": round(public - seats - verification, 3),
                            "note": "residual is combined product, checker, Git and process overhead, "
                                    "not a causal profile"},
        "synthetic_totals": [{k: r[k] for k in ("label", "elapsed", "overlaps")} for r in synthetic],
        "projects": len(projects),
        "unexpected_timeouts": sorted(r["label"] for r in commands
                                      if r["timed_out"] and r["label"] not in INTENDED_TIMEOUTS),
        "unexpected_exceptions": sorted(r["label"] for r in commands if r["exception"]),
    }
    (installed.timings.parent / "installed_pair_summary.json").write_text(json.dumps(summary, indent=2),
                                                                           encoding="utf-8")
    assert all(set(r) >= {"argv", "portable_argv", "cwd", "streams", "exit", "elapsed", "utc_start"} for r in commands)
    assert by_kind["install"]["commands"] >= 3 and len(projects) >= 1
