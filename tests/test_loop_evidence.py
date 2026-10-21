import json
import os
import subprocess
from itertools import combinations, permutations
from pathlib import Path
from types import SimpleNamespace

import pytest

from frutlups import gitws, ledger, loop, render, verdict
from frutlups._loop_evidence import (
    _diff, combined_review, holistic_changes, prior_finding_ids, review_changes,
)
from test_gitws import _git, _repository
from test_loop import canonical_project, conform, events_of, plan
from _native_support import grant_project, run_transitions
from test_loop import project as project


def test_native_pages_keep_deleted_hashes_provenance_and_bound_body_reads(tmp_path, monkeypatch):
    from frutlups import _loop_evidence as evidence
    root = _repository(tmp_path)
    huge = root / "huge.txt"
    huge.write_bytes(b"x" * (2 * 1024 * 1024))
    (root / "late.txt").write_bytes(b"late small defect\n")
    (root / "unrelated.txt").write_bytes(b"PRIVATE UNRELATED MARKER")
    original = dict(path="gone.txt", sha="a" * 64, kind="added", round=1)
    latest = [dict(original, kind="deleted", round=2),
              dict(path="huge.txt", sha=ledger.evidence_sha(huge.read_bytes()), kind="added", round=2),
              dict(path="late.txt", sha=ledger.evidence_sha(b"late small defect\n"), kind="added", round=2)]
    refs = [evidence.immutable_text(root, f"reviews/manifest-{number}.json", json.dumps(dict(
        schema="frutlups.manifest/2", slice="M001-S01", round=number, changed=rows)), 128 * 1024)
        for number, rows in ((1, [original]), (2, latest))]
    opened = Path.open
    reads = []
    class BoundedRead:
        def __enter__(self):
            self.stream = opened(huge, "rb")
            return self
        def __exit__(self, *args):
            self.stream.close()
        def read(self, size=-1):
            reads.append(size)
            assert 0 < size <= 257
            return self.stream.read(size)
    def guard(path, *args, **kwargs):
        assert path != root / "gone.txt", "never open an absent historical/deleted path"
        assert path != root / "unrelated.txt", "never read an unbound neighbor"
        return BoundedRead() if path == huge else opened(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guard)
    index_ref, artifacts = evidence.evidence_package(root, SimpleNamespace(git="git"),
        "reviews/M001-S01", refs, "HEAD")
    index = json.loads(artifacts[-1][1])
    assert index["sources"] == refs
    assert [row["source_row"] for row in index["paths"]] == [0, 0, 1, 2]
    assert [row["source"] for row in index["paths"]] == [0, 1, 1, 1]
    body = artifacts[0][1]
    assert "a" * 64 in body
    assert "late small defect" in body
    assert "PRIVATE UNRELATED MARKER" not in body
    assert reads == [257]
    assert all(len(text.encode("utf-8")) <= limit for _, text, limit in artifacts)
    assert index_ref == artifacts[-1][0]


def report(rows, result="pass", status="achieved", evidence="Fixture evidence."):
    text = (
        "# Review: M001-S01 round 2\n\n## Findings\n\n"
        "| id | severity | disposition | summary |\n| --- | --- | --- | --- |\n"
        + "\n".join(rows)
        + f"\n\n## Closure Decision\nObjective status: {status}\n"
        f"Objective evidence: {evidence}\n\n## Verdict\n"
        f"Verdict: {result} - next: Follow {result}.\n"
    )
    return verdict.parse(text), text


def test_native_merge_keeps_exact_updates_and_refuses_conflicts():
    from test_verdict import _native_update_report
    body = _native_update_report()
    parsed = verdict.parse(body, native=True)
    merged = combined_review([(parsed, body), (parsed, body)], seat_names=("a", "b"), native=True)
    assert verdict.parse(merged, native=True).updates == parsed.updates
    assert merged.count("| reviews/original.md |") == 1
    conflicting = body.replace("closed_by_review", "open")
    with pytest.raises(ValueError, match="contradictory finding updates across seats"):
        combined_review([(parsed, body), (verdict.parse(conflicting, native=True), conflicting)],
                        seat_names=("a", "b"), native=True)
    rows = "| RELATED | P3 | open | colliding new ID |\n"
    colliding = body.replace("## Finding updates", rows + "\n## Finding updates")
    collision = verdict.parse(colliding, native=True)
    with pytest.raises(ValueError, match="ambiguous related finding after merge renaming"):
        combined_review([(collision, colliding)] * 2, seat_names=("a", "b"), native=True)


@pytest.mark.parametrize(("related_by_seat", "expected"), [
    (((), ()), ()),
    (((), ("B", "A")), ("A", "B")),
    ((("B", "A"), ("B", "C")), ("A", "B", "C")),
    ((("B",), ("A",)), ("A", "B")),
    ((("B", "A"), ("B", "A")), ("A", "B")),
    ((("B",), ("C", "B"), ("A", "C")), ("A", "B", "C")),
], ids=("empty", "empty-nonempty", "overlap", "disjoint", "identical", "three-seats"))
def test_native_merge_unions_compatible_related_ids(related_by_seat, expected):
    from test_verdict import _native_update_report
    for related_order in permutations(related_by_seat):
        bodies = [_native_update_report().replace(
            "| RELATED |", "| " + (", ".join(links) or "-") + " |")
            for links in related_order]
        reviews = [(verdict.parse(body, native=True), body) for body in bodies]
        text = combined_review(reviews, seat_names=tuple("abc"[:len(reviews)]), native=True)
        parsed = verdict.parse(text, native=True)
        assert parsed.updates == (verdict.FindingUpdate(
            "reviews/original.md", "a" * 64, "OLD", "closed_by_review", expected),)
        assert text.count("| reviews/original.md |") == 1


def test_native_merge_four_compatible_source_bound_closures():
    from test_verdict import _native_update_report
    pairs = (("M001-S01-F01", "M001-S01-F1"), ("M001-S01-F02", "M001-S01-F2"),
             ("M001-S01-F1", "M001-S01-F01"), ("M001-S01-F2", "M001-S01-F02"))
    template = _native_update_report()
    original = next(line for line in template.splitlines() if line.startswith("| reviews/"))
    bodies = [template.replace(original, "\n".join(
        f"| reviews/original.md | {'a' * 64} | {identity} | closed_by_review | "
        + (related if linked else "-") + " |" for identity, related in pairs))
        for linked in (False, True)]
    for order in permutations(bodies):
        text = combined_review([(verdict.parse(body, native=True), body) for body in order],
                               seat_names=("a", "b"), native=True)
        updates = verdict.parse(text, native=True).updates
        assert len(updates) == 4
        for update, (identity, related) in zip(updates, pairs):
            assert update.source == "reviews/original.md"
            assert update.sha == "a" * 64
            assert update.id == identity
            assert update.disposition == "closed_by_review"
            assert update.related == (related,)


@pytest.mark.parametrize(("source", "sha", "identity"), [
    ("reviews/other.md", "a" * 64, "OLD"),
    ("reviews/original.md", "b" * 64, "OLD"),
    ("reviews/original.md", "a" * 64, "OTHER"),
], ids=("different-source", "different-sha", "different-id"))
def test_native_merge_keeps_distinct_source_hash_id_keys(source, sha, identity):
    from test_verdict import _native_update_report
    body = _native_update_report()
    changed = body.replace("reviews/original.md", source).replace("a" * 64, sha).replace(
        "| OLD |", f"| {identity} |").replace("closed_by_review", "open").replace(
        "| RELATED |", "| OTHER_LINK |")
    expected = (verdict.FindingUpdate("reviews/original.md", "a" * 64, "OLD",
                                      "closed_by_review", ("RELATED",)),
                verdict.FindingUpdate(source, sha, identity, "open", ("OTHER_LINK",)))
    for order in permutations((body, changed)):
        text = combined_review([(verdict.parse(text, native=True), text) for text in order],
                               seat_names=("a", "b"), native=True)
        updates = verdict.parse(text, native=True).updates
        assert len(updates) == 2
        assert {(u.source, u.sha, u.id): u for u in updates} == {
            (u.source, u.sha, u.id): u for u in expected}


def test_native_merge_refuses_conflicting_dispositions_with_different_links():
    from test_verdict import _native_update_report
    body = _native_update_report()
    changed = body.replace("closed_by_review", "open").replace("| RELATED |", "| - |")
    for order in permutations((body, changed)):
        with pytest.raises(ValueError, match="^contradictory finding updates across seats$"):
            combined_review([(verdict.parse(text, native=True), text) for text in order],
                            seat_names=("a", "b"), native=True)


def test_native_merge_refuses_ambiguous_links_from_an_agreeing_seat():
    from test_verdict import _native_update_report
    body = _native_update_report().replace("## Finding updates", (
        "| RELATED | P3 | open | Colliding new ID. |\n\n## Finding updates"))
    unlinked = body.replace("| RELATED |\n", "| - |\n")
    for order in permutations((body, unlinked)):
        with pytest.raises(ValueError, match="^ambiguous related finding after merge renaming$"):
            combined_review([(verdict.parse(text, native=True), text) for text in order],
                            seat_names=("a", "b"), native=True)


def test_combined_review_merges_recorded_corrective_reports():
    # Exact copies of the three M004-S01 round 2 reports under 05_governance/reviews/m004/.
    fixtures = Path(__file__).parent / "fixtures" / "reviews"
    reviews = []
    for seat in ("reviewer", "claude", "gpt6"):
        text = (fixtures / f"M004-S01_r2_review_{seat}.md").read_text(encoding="utf-8")
        reviews.append((verdict.parse(text), text))

    text = combined_review(reviews)
    merged = verdict.parse(text)
    assert len(merged.findings) == 10
    assert [(f.id, f.severity, f.disposition) for f in merged.findings] == [
        ("M004-S01-01", "P2", "closed_by_review"),
        ("M004-S01-11", "P2", "closed_by_review"),
        ("M004-S01-21", "P2", "closed_by_review"),
        ("M004-S01-03", "P3", "closed_by_review"),
        ("M004-S01-22", "P3", "closed_by_review"),
        ("M004-S01-23", "P3", "carried"),
        ("M004-S01-04", "P3", "open"),
        ("M004-S01-12", "P3", "carried"),
        ("M004-S01-13", "P3", "carried"),
        ("M004-S01-25", "P3", "carried"),
    ]
    expected_rows = (
        verdict.finding_rows(reviews[0][1], tuple(f.id for f in reviews[0][0].findings))
        + verdict.finding_rows(reviews[1][1], ("M004-S01-12", "M004-S01-13"))
        + verdict.finding_rows(reviews[2][1], ("M004-S01-25",))
    )
    assert verdict.finding_rows(text, tuple(f.id for f in merged.findings)) == expected_rows
    assert merged.verdict == "pass"
    assert merged.objective_status == "achieved"
    assert merged.objective_evidence == reviews[0][0].objective_evidence
    assert merged.next_move == reviews[0][0].next_move


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    ("stricter", "weaker"),
    combinations(("open", "carried", "closed_by_review", "waived_by_human"), 2),
)
def test_combined_review_prioritizes_disposition_over_severity(stricter, weaker, reverse):
    winning = "| M001-S01-F1 | P3 | " + stricter + " | Keep this seat's summary. |"
    other = "| M001-S01-F1 | P0 | " + weaker + " | Other seat's summary. |"
    reviews = [report([winning]), report([other])]
    text = combined_review(
        reviews[::-1] if reverse else reviews,
        seat_names=("reviewer", "claude_reviewer"),
        prior_ids=("M001-S01-F1",),
    )
    merged = verdict.parse(text)
    assert len(merged.findings) == 1
    assert merged.findings[0].severity == "P3"
    assert merged.findings[0].disposition == stricter
    assert merged.findings[0].summary == "Keep this seat's summary."
    assert verdict.finding_rows(text, ("M001-S01-F1",)) == (winning,)


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("finding_id", ["M001-S01-F1", "M001-S01-F1-reviewer"])
@pytest.mark.parametrize(("stricter", "weaker"), [("P0", "P1"), ("P1", "P2"), ("P2", "P3")])
def test_combined_review_keeps_stricter_severity_for_prior_id(
    stricter, weaker, finding_id, reverse,
):
    winning = f"| {finding_id} | {stricter} | open | Stricter prior finding. |"
    other = f"| {finding_id} | {weaker} | open | Weaker prior finding. |"
    reviews = [report([winning], "needs_work"), report([other], "needs_work")]
    text = combined_review(
        reviews[::-1] if reverse else reviews,
        seat_names=("reviewer", "claude_reviewer"),
        prior_ids=(finding_id,),
    )
    merged = verdict.parse(text)
    assert len(merged.findings) == 1
    assert merged.findings[0].severity == stricter
    assert merged.findings[0].disposition == "open"
    assert merged.findings[0].id == finding_id
    assert merged.findings[0].summary == "Stricter prior finding."
    assert verdict.finding_rows(text, (finding_id,)) == (winning,)


@pytest.mark.parametrize("order", permutations(("pass", "needs_work", "blocked")))
def test_combined_review_keeps_strictest_verdict_and_its_closure(order):
    statuses = {"pass": "achieved", "needs_work": "not_achieved", "blocked": "indeterminate"}
    reviews = [report([], result, statuses[result], f"Evidence for {result}.") for result in order]
    merged = verdict.parse(combined_review(reviews))
    assert merged.verdict == "blocked"
    assert merged.objective_status == "indeterminate"
    assert merged.objective_evidence == "Evidence for blocked."
    assert merged.next_move == "Follow blocked."


def test_combined_review_preserves_distinct_id_order_when_replacing_shared_row():
    first = "| M001-S01-F1 | P3 | carried | First finding. |"
    shared = "| M001-S01-F2 | P3 | carried | Earlier summary. |"
    last = "| M001-S01-F3 | P3 | carried | Last finding. |"
    winning = "| M001-S01-F2 | P3 | open | Later stricter summary. |"
    text = combined_review([report([first, shared]), report([last, winning])])
    merged = verdict.parse(text)
    assert [f.id for f in merged.findings] == ["M001-S01-F1", "M001-S01-F2", "M001-S01-F3"]
    assert verdict.finding_rows(text, tuple(f.id for f in merged.findings)) == (
        first, winning, last,
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_combined_review_matches_shared_id_with_pipes_in_summary(reverse):
    shared_id = "M001-S01-F1"
    winning = r"|M001-S01-F1|P2|open|Keep `left|right`, left\|right, and final \||"
    other = "| M001-S01-F1 | P2 | closed_by_review | Other summary. |"
    unrelated = "| M001-S01-F2 | P3 | carried | Mentions M001-S01-F1 | in its summary. |"
    reviews = [report([winning], "needs_work"), report([other, unrelated])]

    text = combined_review(
        reviews[::-1] if reverse else reviews,
        seat_names=("reviewer", "claude_reviewer"), prior_ids=(shared_id,),
    )

    merged = verdict.parse(text)
    assert len(merged.findings) == 2
    finding = next(f for f in merged.findings if f.id == shared_id)
    assert finding.id == shared_id
    assert finding.severity == "P2"
    assert finding.disposition == "open"
    assert finding.summary == "Keep `left | right`, left|right, and final |"
    assert verdict.finding_rows(text, (shared_id,)) == (winning,)
    assert verdict.finding_rows(text, ("M001-S01-F2",)) == (unrelated,)
    assert verdict.open_blocking(merged) == (shared_id,)
    assert merged.verdict == "needs_work"


@pytest.mark.parametrize("prior_ids", [(), ("M001-S01-F1",)])
def test_combined_review_returns_single_seat_text_unchanged(prior_ids):
    _, text = report(["| M001-S01-F1 | P3 | carried | Keep exact formatting. |"])
    text = "```markdown\n" + text + "```\n"
    assert combined_review(
        [(verdict.parse(text), text)], seat_names=("reviewer",), prior_ids=prior_ids,
    ) == text


@pytest.mark.parametrize("reverse", [False, True])
def test_combined_review_keeps_each_new_collision_and_single_seat_id(reverse):
    first = "  |  M001-S01-F1\t| P1 | open | Keep M001-S01-F1 in this summary. |  "
    second = "|M001-S01-F1 | P2 | open | Different finding, same invented id. |"
    unique = "| M001-S01-F2 | P3 | carried | Only one seat reports this. |"
    reviews = [report([first, unique], "needs_work"), report([second], "needs_work")]
    seats = ("reviewer", "claude_reviewer")
    if reverse:
        reviews.reverse()
        seats = seats[::-1]

    text = combined_review(reviews, seat_names=seats, prior_ids=())

    merged = verdict.parse(text)
    assert len(merged.findings) == 3
    findings = {f.id: f for f in merged.findings}
    assert findings["M001-S01-F1-reviewer"].severity == "P1"
    assert findings["M001-S01-F1-reviewer"].disposition == "open"
    assert findings["M001-S01-F1-claude_reviewer"].severity == "P2"
    assert findings["M001-S01-F1-claude_reviewer"].disposition == "open"
    assert verdict.finding_rows(text, ("M001-S01-F1-reviewer",)) == (
        "  |  M001-S01-F1-reviewer\t| P1 | open | Keep M001-S01-F1 in this summary. |  ",
    )
    assert verdict.finding_rows(text, ("M001-S01-F1-claude_reviewer",)) == (
        "|M001-S01-F1-claude_reviewer | P2 | open | Different finding, same invented id. |",
    )
    assert verdict.finding_rows(text, ("M001-S01-F2",)) == (unique,)
    assert set(verdict.open_blocking(merged)) == {
        "M001-S01-F1-reviewer", "M001-S01-F1-claude_reviewer",
    }


def test_combined_review_refuses_suffix_that_would_overwrite_another_finding():
    shared = "| M001-S01-F1 | P2 | open | New finding. |"
    existing = "| M001-S01-F1-reviewer | P3 | carried | Preserve this prior finding. |"
    reviews = [report([shared, existing], "needs_work"), report([shared], "needs_work")]

    with pytest.raises(ValueError, match="seat-suffixed finding id already exists: M001-S01-F1"):
        combined_review(
            reviews,
            seat_names=("reviewer", "claude_reviewer"),
            prior_ids=("M001-S01-F1-reviewer",),
        )


@pytest.mark.parametrize("with_report", [True, False])
def test_prior_ids_include_every_previous_row_and_reopen_reason(tmp_path, with_report):
    rows = [
        "| M001-S01-F1 | P0 | open | Open previous finding. |",
        "| M001-S01-F2 | P1 | closed_by_review | Closed previous finding. |",
        "| M001-S01-F3 | P2 | waived_by_human | Human-waived previous finding. |",
        "| M001-S01-F4-reviewer | P3 | carried | Carried suffixed previous finding. |",
    ]
    if with_report:
        (tmp_path / "prior.md").write_text(report(rows, "needs_work")[1], encoding="utf-8")
    current = SimpleNamespace(
        id="M001-S01",
        last_report="prior.md" if with_report else None,
        reopen_reason="holistic findings M001-S01-H1, M001-S01-H10",
    )

    ids = prior_finding_ids(tmp_path, current)

    expected = {"M001-S01-H1", "M001-S01-H10"}
    if with_report:
        expected.update({"M001-S01-F1", "M001-S01-F2", "M001-S01-F3", "M001-S01-F4-reviewer"})
    assert ids == expected
    current.last_report = None
    current.reopen_reason = None
    assert prior_finding_ids(tmp_path, current) == set()


def test_routed_new_findings_reach_corrective_prompt_and_merge_as_prior(project):
    root, cfg, roadmap = grant_project(canonical_project(
        project, ordinary_reviewers=("reviewer", "claude_reviewer")))
    first = "| M001-S01-F1 | P1 | open | First seat's new finding. |"
    second = "| M001-S01-F1 | P2 | open | Second seat's different finding. |"
    suffixed = (
        "| M001-S01-F1-reviewer | P1 | open | First seat's new finding. |",
        "| M001-S01-F1-claude_reviewer | P2 | open | Second seat's different finding. |",
    )
    closed = [row.replace("| open |", "| closed_by_review |") for row in suffixed]
    plan(root, reviewer=[
        {"verdict": "needs_work", "rows": [first]},
        {"verdict": "needs_work", "rows": [second]},
        {"close_ids": ["M001-S01-F1-reviewer", "M001-S01-F1-claude_reviewer"]},
        {"close_ids": ["M001-S01-F1-reviewer", "M001-S01-F1-claude_reviewer"]},
    ])

    assert run_transitions(root, cfg, roadmap) == loop.StopReason.done

    reviewed = events_of(root, cfg, "reviewed")
    assert len(reviewed) == 2
    assert reviewed[0].data["open"] == ("M001-S01-F1-reviewer", "M001-S01-F1-claude_reviewer")
    assert reviewed[1].data["open"] == ()
    prompts = events_of(root, cfg, "prompt")
    assert len(prompts) == 2
    corrective = (root / prompts[1].data["path"]).read_text(encoding="utf-8")
    assert suffixed[0] in corrective
    assert suffixed[1] in corrective
    final = (root / reviewed[1].data["report"]).read_text(encoding="utf-8")
    parsed = verdict.parse(final)
    assert tuple(f.id for f in parsed.updates) == (
        "M001-S01-F1-reviewer", "M001-S01-F1-claude_reviewer",
    )
    assert parsed.findings == ()
    for update in parsed.updates:
        assert update.source == reviewed[0].data["report"]
        assert update.sha == reviewed[0].data["sha"]
        assert update.disposition == "closed_by_review"
    artifacts = [e for e in events_of(root, cfg, "artifact") if e.data["role"] == "review_prompt"]
    assert [(e.round, e.data["role"]) for e in artifacts] == [
        (1, "review_prompt"), (2, "review_prompt"),
    ]
    conform(root, cfg, roadmap)


def test_large_literal_selection_staging_and_review_preparation(tmp_path, monkeypatch):
    root = _repository(tmp_path)
    assert not root.resolve().is_relative_to(Path(__file__).resolve().parents[3])
    cfg = SimpleNamespace(git="git")
    paths = [f"- file [{index:03}] Ω " + "x" * 50 + ".txt" for index in range(700)]
    decoy = paths[0].replace("[000]", "0")
    (root / decoy).write_bytes(b"unrelated base\n")
    for path in paths:
        (root / path).write_bytes(b"selected base\n")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "large fixture base")
    base = gitws.head(root)
    for path in paths:
        (root / path).write_bytes(b"selected new\n")
    (root / decoy).write_bytes(b"UNRELATED SECRET MARKER\n")
    original_entry = _git(root, "ls-files", "--stage", "-z", "--", "modified.txt").stdout
    original_blob = _git(root, "show", ":modified.txt").stdout
    (root / "modified.txt").write_bytes(b"UNRELATED STAGED EDIT\n")
    _git(root, "add", "--", "modified.txt")
    staged_entry = _git(root, "ls-files", "--stage", "-z", "--", "modified.txt").stdout
    staged_blob = _git(root, "show", ":modified.txt").stdout
    assert staged_entry != original_entry
    assert staged_blob == b"UNRELATED STAGED EDIT\n"
    assert staged_blob != _git(root, "show", "HEAD:modified.txt").stdout
    assert _git(root, "diff", "--cached", "--name-only", "-z").stdout == b"modified.txt\0"
    assert len(subprocess.list2cmdline(["git", "diff", "HEAD", "--", *paths])) >= 40000
    launched = []
    original = gitws.run_process

    def observe(argv, *args, **kwargs):
        encoding = "utf-16-le" if os.name == "nt" else "utf-8"
        assert len(subprocess.list2cmdline(argv).encode(encoding)) <= gitws.ARGV_LIMIT
        assert "--literal-pathspecs" in argv
        if "--" in argv:
            assert "--pathspec-from-file=-" not in argv
            launched.append(argv)
        return original(argv, *args, **kwargs)

    monkeypatch.setattr(gitws, "run_process", observe)
    before = (gitws.head(root), _git(root, "write-tree").stdout, gitws.status(root))
    # A larger explicit allowance proves batches reach every selected file, not
    # merely that an early prompt omission avoids the formerly oversized launch.
    complete = gitws.bounded_diff(root, "HEAD", paths, 1024 * 1024)
    selected_argv = [path for argv in launched for path in argv[argv.index("--") + 1:]]
    assert selected_argv == paths
    assert len(launched) > 1
    assert complete.count("+selected new") == 700
    assert gitws.DIFF_NOTICE not in complete
    assert "UNRELATED SECRET MARKER" not in complete
    changed = tuple({"path": path, "kind": "modified", "sha": ledger.evidence_sha(b"selected new\n")}
                    for path in paths)
    coded = SimpleNamespace(slice="M001-S01", ev=ledger.Ev.coded, data={"changed": changed})
    current = SimpleNamespace(id="M001-S01", changed=changed)
    ordinary = review_changes(root, cfg, [coded], current)
    assert ordinary.cumulative == changed
    assert len(ordinary.current_diff.encode("utf-8")) <= render.DIFF_LIMIT
    assert "Diff truncated" in ordinary.current_diff
    assert "UNRELATED SECRET MARKER" not in ordinary.current_diff
    assert _git(root, "ls-files", "--stage", "-z", "--", "modified.txt").stdout == staged_entry
    assert _git(root, "show", ":modified.txt").stdout == staged_blob
    (root / "receipt.json").write_text(json.dumps({"base_commit": base}), encoding="utf-8")
    events = [coded,
              SimpleNamespace(slice=current.id, ev=ledger.Ev.verified,
                              data={"ok": True, "receipt": "receipt.json"}),
              SimpleNamespace(slice=current.id, ev=ledger.Ev.accepted, data={})]
    milestone = SimpleNamespace(slices=[SimpleNamespace(id=current.id)])
    holistic = holistic_changes(root, cfg, events, milestone)
    assert holistic.cumulative == changed
    assert len(holistic.current_diff.encode("utf-8")) <= render.DIFF_LIMIT
    assert "separate batch summaries" in holistic.current_diff
    assert "Per-slice diffs omitted" in holistic.current_diff
    assert "UNRELATED SECRET MARKER" not in holistic.current_diff
    # The only additional dirty file is the explicitly owned synthetic receipt.
    assert gitws.head(root) == before[0]
    assert _git(root, "write-tree").stdout == before[1]
    assert tuple(item for item in gitws.status(root) if item.path != "receipt.json") == before[2]
    assert _git(root, "ls-files", "--stage", "-z", "--", "modified.txt").stdout == staged_entry
    assert _git(root, "show", ":modified.txt").stdout == staged_blob
    working_paths = [*paths, decoy, "modified.txt", "deleted.txt", "renamed.txt", ".gitignore", "receipt.json"]
    working_bytes = {path: (root / path).read_bytes() for path in working_paths}
    index_bytes = (root / ".git/index").read_bytes()
    with pytest.raises(ValueError, match="unrelated staged paths: modified.txt"):
        gitws.commit(root, paths, "must refuse unrelated staged edit")
    assert gitws.head(root) == base
    assert (root / ".git/index").read_bytes() == index_bytes
    assert _git(root, "write-tree").stdout == before[1]
    assert _git(root, "ls-files", "--stage", "-z", "--", "modified.txt").stdout == staged_entry
    assert _git(root, "show", ":modified.txt").stdout == staged_blob
    assert {path: (root / path).read_bytes() for path in working_paths} == working_bytes
    # Restore only this owned fixture's unrelated index entry, preserving its edit.
    _git(root, "restore", "--staged", "--source=HEAD", "--", "modified.txt")
    assert _git(root, "ls-files", "--stage", "-z", "--", "modified.txt").stdout == original_entry
    assert _git(root, "show", ":modified.txt").stdout == original_blob
    assert (root / "modified.txt").read_bytes() == staged_blob
    committed = gitws.commit(root, paths, "large literal selection")
    assert committed == gitws.head(root)
    assert _git(root, "rev-parse", "HEAD^").stdout.decode().strip() == base
    delta = _git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "HEAD")
    assert set(delta.stdout.decode().strip("\0").split("\0")) == set(paths)
    assert _git(root, "diff", "--cached", "--name-only").stdout == b""
    assert {item.path for item in gitws.status(root)} == {decoy, "receipt.json", "modified.txt"}
    assert (root / decoy).read_bytes() == b"UNRELATED SECRET MARKER\n"


@pytest.mark.parametrize("committed", [False, True])
def test_diff_retains_rename_origin_deletion_and_unrelated_index(tmp_path, committed):
    root = _repository(tmp_path)
    base = gitws.head(root)
    _git(root, "mv", "renamed.txt", "destination [1].txt")
    (root / "deleted.txt").unlink()
    _git(root, "add", "-A", "--", "deleted.txt")
    if committed:
        _git(root, "commit", "-qm", "fixture rename and deletion")
    (root / "modified.txt").write_bytes(b"UNRELATED INDEX\n")
    _git(root, "add", "modified.txt")
    before = (gitws.head(root), _git(root, "write-tree").stdout, gitws.status(root))
    text = _diff(root, SimpleNamespace(git="git"), base, [
        {"path": "destination [1].txt", "kind": "renamed"},
        {"path": "deleted.txt", "kind": "deleted"},
    ])
    assert "rename from renamed.txt" in text
    assert "rename to destination [1].txt" in text
    assert "deleted file mode" in text
    assert "-deleted" in text
    assert "UNRELATED INDEX" not in text
    assert (gitws.head(root), _git(root, "write-tree").stdout, gitws.status(root)) == before
    assert _git(root, "show", ":modified.txt").stdout == b"UNRELATED INDEX\n"


def test_added_literal_brackets_do_not_match_a_tracked_neighbor(tmp_path):
    root = _repository(tmp_path)
    (root / "file1.txt").write_bytes(b"tracked unrelated\n")
    _git(root, "add", "file1.txt")
    _git(root, "commit", "-qm", "neighbor")
    (root / "file1.txt").write_bytes(b"DO NOT INCLUDE\n")
    (root / "file[1].txt").write_bytes(b"literal added body\n")
    text = _diff(root, SimpleNamespace(git="git"), "HEAD", [
        {"path": "file[1].txt", "kind": "added"},
    ])
    assert "Added file: file[1].txt\nliteral added body\n" in text
    assert "DO NOT INCLUDE" not in text


def test_added_body_read_is_bounded_before_allocation(tmp_path, monkeypatch):
    root = _repository(tmp_path)
    target = root / "large.txt"
    target.write_bytes(b"START\n" + b"x" * (2 * 1024 * 1024) + b"END\n")
    opened = Path.open
    reads = []

    class LimitedReader:
        def __enter__(self):
            self.stream = opened(target, "rb")
            return self

        def read(self, size=-1):
            assert 0 < size <= render.DIFF_LIMIT + 1
            reads.append(size)
            return self.stream.read(size)

        def __exit__(self, *args):
            self.stream.close()

    def observe(path, *args, **kwargs):
        if path == target:
            assert args == ("rb",)
            return LimitedReader()
        return opened(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", observe)
    text = _diff(root, SimpleNamespace(git="git"), "HEAD", [{"path": "large.txt", "kind": "added"}])
    assert len(reads) == 1
    assert "START\n" in text
    assert "END\n" not in text
    assert "Added-file evidence omitted or truncated" in text
    assert len(text.encode()) <= render.DIFF_LIMIT


def test_diff_late_oversized_path_refuses_before_patch_even_when_body_would_truncate(tmp_path, monkeypatch):
    root = _repository(tmp_path)
    (root / "modified.txt").write_bytes(b"x" * 65536)
    monkeypatch.setattr(gitws, "ARGV_LIMIT", 1024)
    launched = []
    original = gitws.run_process
    def observe(argv, *args, **kwargs):
        if "--" in argv:
            launched.append(argv)
        return original(argv, *args, **kwargs)
    monkeypatch.setattr(gitws, "run_process", observe)
    before = (gitws.head(root), _git(root, "write-tree").stdout, gitws.status(root))
    with pytest.raises(ValueError, match="bounded argument allowance"):
        _diff(root, SimpleNamespace(git="git"), "HEAD", [
            {"path": "modified.txt", "kind": "modified"},
            {"path": "/".join(["x" * 180] * 4), "kind": "added"},
        ])
    assert launched == []
    assert (gitws.head(root), _git(root, "write-tree").stdout, gitws.status(root)) == before


def test_diff_over_stream_cap_retains_bounded_truthful_notice(tmp_path):
    root = _repository(tmp_path)
    (root / "modified.txt").write_bytes(b"x" * (9 * 1024 * 1024))
    text = gitws.bounded_diff(root, "HEAD", ["modified.txt"], 1024)
    assert len(text.encode()) <= 1024
    assert "diff --git" in text
    assert text.endswith(gitws.DIFF_NOTICE)


def test_non_utf8_diff_replacement_stays_within_encoded_allowance(tmp_path):
    root = _repository(tmp_path)
    (root / "modified.txt").write_bytes(b"\xff" * 400 + b"\n")
    text = gitws.bounded_diff(root, "HEAD", ["modified.txt"], 1024)
    assert len(text.encode("utf-8")) <= 1024
    assert "\ufffd" in text
    assert text.endswith(gitws.DIFF_NOTICE)


def test_empty_diff_selection_never_reads_broad_product_diff(tmp_path, monkeypatch):
    root = _repository(tmp_path)
    (root / "modified.txt").write_bytes(b"unrelated\n")
    before = (gitws.head(root), _git(root, "write-tree").stdout, gitws.status(root))

    def forbidden(*args, **kwargs):
        pytest.fail("empty selection launched Git")

    with monkeypatch.context() as patch:
        patch.setattr(gitws, "_git", forbidden)
        assert gitws.bounded_diff(root, "HEAD", []) == ""
        assert _diff(root, SimpleNamespace(git="git"), "HEAD", []) == "(no product changes)"
    assert (gitws.head(root), _git(root, "write-tree").stdout, gitws.status(root)) == before
