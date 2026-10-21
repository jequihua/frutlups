import inspect
import json
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from frutlups.ledger import evidence_sha, fold
from frutlups.render import (
    DIFF_LIMIT,
    RenderError,
    ReviewChanges,
    coding_envelope,
    coding_v2,
    holistic_v2,
    render_template,
    review_v2,
)
from frutlups.roadmap import load

FIXTURE = Path(__file__).parent / "fixtures" / "v5_development"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True,
    )


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    shutil.copytree(FIXTURE, root)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "core.autocrlf", "false")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "fixture")
    return root


def _prior_report(root: Path) -> str:
    relative = "reviews/prior.md"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """# Review: M001-S01 round 1

## Findings
| id | severity | disposition | summary |
| --- | --- | --- | --- |
| M001-S01-F1 | P2 | open | Preserve this complete row. |
| M001-S01-F2 | P3 | carried | Do not carry this row forward. |

## Closure Decision
Objective status: not_achieved
Objective evidence: One finding remains.

## Verdict
Verdict: needs_work - next: fix it
""",
        encoding="utf-8",
    )
    return relative


@pytest.mark.parametrize(
    ("template", "values", "message"),
    [
        ("{{unknown_value}}", {}, "unknown placeholders"),
        ("{{objective}", {"objective": "value"}, "unresolved placeholder"),
        ("objective}}", {"objective": "value"}, "unresolved placeholder"),
    ],
    ids=("unknown", "malformed-open", "malformed-close"),
)
def test_render_template_refuses_unknown_or_malformed_braces(
    tmp_path: Path, template: str, values: dict, message: str,
) -> None:
    path = tmp_path / "template.md"
    path.write_text(template, encoding="utf-8")

    with pytest.raises(RenderError, match=message):
        render_template(path, values)


def test_render_template_ignores_braces_in_values_and_drops_empty_heading(
    tmp_path: Path,
) -> None:
    path = tmp_path / "template.md"
    path.write_text(
        "{{objective}}\n\n## Memory\n{{memory}}\n\n## Finish\ndone\n",
        encoding="utf-8",
    )

    output = render_template(
        path,
        {"objective": "literal {{value}}", "memory": ""},
        (("Memory", "memory"),),
    )

    assert output == "literal {{value}}\n\n## Finish\ndone\n"


def test_round_one_coding_prompt_matches_template_script(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    result = subprocess.run(
        [sys.executable, str(root / "scripts" / "prompt.py"),
         "M001-S01", "--root", str(root)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    generated = root / result.stdout.strip()
    roadmap = load(root / "roadmap.yaml")
    state = fold((), roadmap).slices["M001-S01"]
    item = roadmap.milestones[0].slices[0]

    from frutlups import ledger
    frozen = coding_envelope(root, roadmap, item, state)
    actual = coding_v2(root, frozen, state)
    issued = ledger.read(root / "05_governance/ledger.jsonl")[-1]
    expected = ledger.read_reference(root, ledger._thaw(issued.data["envelope"]))
    assert frozen == expected
    assert json.dumps(frozen, ensure_ascii=False, sort_keys=True) in actual
    assert generated.is_file()
    assert "Return the typed outcome" in actual


def test_coding_keeps_finding_rows_unblock_reason_and_failed_receipt_tail(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    roadmap = load(root / "roadmap.yaml")
    item = roadmap.milestones[0].slices[0]
    state = replace(
        fold((), roadmap).slices[item.id],
        last_report=_prior_report(root),
        open_findings=("M001-S01-F1",),
        unblock_reason="Human approved a retry.",
    )

    frozen = coding_envelope(root, roadmap, item, state)
    # Native correction prompts bind the complete failed receipt for inspection.
    relative = "reviews/failed-receipt.json"
    raw = json.dumps({"schema": "frutlups.receipt/2", "ok": False,
                      "commands": [{"exit": 1, "stderr_tail": "Failed command stderr tail."}]}).encode()
    (root / relative).write_bytes(raw)
    state = replace(state, last_receipt=relative)
    output = coding_v2(root, frozen, state)

    assert "| M001-S01-F1 | P2 | open | Preserve this complete row. |" in output
    assert "M001-S01-F2" not in output
    assert "Unblock reason: Human approved a retry." in output
    assert f"Prior verification receipt: `{relative}` (normalized evidence SHA-256 `{evidence_sha(raw)}`)" in output
    assert json.loads((root / relative).read_bytes())["commands"][0]["exit"] == 1
    assert json.loads((root / relative).read_bytes())["commands"][0]["stderr_tail"] == "Failed command stderr tail."


def test_review_carries_cumulative_evidence_and_bounds_current_diff(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    roadmap = load(root / "roadmap.yaml")
    item = roadmap.milestones[0].slices[0]
    state = fold((), roadmap).slices[item.id]
    state = replace(
        state,
        round=2,
        last_receipt="reviews/receipt.json",
        last_report=_prior_report(root),
        open_findings=("M001-S01-F1",),
        changed=({"path": "07_app/current.py", "sha": "b" * 64, "kind": "modified"},),
    )
    from frutlups import render, _loop_evidence as evidence
    changes = [
        {"path": "07_app/earlier.py", "sha": "a" * 64, "kind": "added"},
        {"path": "07_app/current.py", "sha": "b" * 64, "kind": "modified"},
    ]
    manifest = evidence.immutable_text(root, "reviews/manifest.json", json.dumps(dict(
        schema="frutlups.manifest/2", slice=item.id, round=2, changed=changes)), render.SUPPORT_LIMIT)
    receipt_ref = evidence.immutable_text(root, "reviews/receipt.json", json.dumps(dict(
        schema="frutlups.receipt/2", manifest=manifest, ok=True)), render.SUPPORT_LIMIT)
    notes = evidence.immutable_text(root, "reviews/notes.md", "Coder notes here.", render.SUPPORT_LIMIT)
    report_path = "05_governance/reviews/m001/M001-S01_r2_review.md"
    frozen = coding_envelope(root, roadmap, item, state)
    support = dict(manifest=manifest, receipt=receipt_ref, notes=notes)
    output = review_v2(root, frozen, support, report_path)
    for ref in (manifest, receipt_ref, notes):
        assert render.reference_text(ref) in output
    assert json.loads((root / manifest["path"]).read_bytes())["changed"] == changes
    assert "| M001-S01-F1 | P2 | open | Preserve this complete row. |" in output
    assert report_path in output
    assert "Start every finding ID with `M001-S01-`." in output
    assert render.prompt_size(output, "pi") <= render.REVIEW_LIMIT
    # Shared bounded diff rendering still preserves the old byte-limit invariant.
    block = render._diff_block("+" + "x" * (DIFF_LIMIT + 1000), [row["path"] for row in changes])
    assert "Diff truncated at 32 KB" in block
    assert len(block.split("```diff\n", 1)[1].split("\n```", 1)[0].encode()) <= DIFF_LIMIT


def test_holistic_render_includes_slice_scoped_finding_rule() -> None:
    roadmap = load(FIXTURE / "roadmap.yaml")
    milestone = roadmap.milestones[0]

    output = holistic_v2(FIXTURE, milestone, {"accepted": [], "latest": [], "cycle": []})
    assert "# Review prompt: M001" in output
    assert "Every P0-P2 finding ID must start with its exact affected slice ID" in output


def test_substitution_does_not_rescan_known_placeholder_tokens(tmp_path: Path) -> None:
    path = tmp_path / "template.md"
    path.write_text("{{coder_notes}}\n{{title}}\n", encoding="utf-8")

    output = render_template(path, {"coder_notes": "keep {{title}}", "title": "changed"})

    assert output == "keep {{title}}\nchanged\n"


def test_repository_renderers_take_root_first() -> None:
    for renderer in (coding_envelope, coding_v2, review_v2, holistic_v2):
        assert next(iter(inspect.signature(renderer).parameters)) == "root"


@pytest.mark.parametrize("label", ["sha256", "normalized evidence SHA-256"])
@pytest.mark.parametrize("limit", [16384, 49152])
def test_native_fallback_preserves_whole_references_and_hash_rule(label, limit):
    from frutlups import render
    ref = f"`reviews/original.md` ({label} `{'a' * 64}`; original row)"
    output = render._native_prompt("界" * limit + "\n" + ref, "Mandatory scope and instructions.\n", limit)
    assert ref in output
    assert render.HASH_RULE in output
    assert "Mandatory scope and instructions." in output
    assert "omitted" in output
    assert render.prompt_size(output, "pi") <= limit
    with pytest.raises(RenderError, match="mandatory instructions"):
        render._native_prompt("", "界" * limit, limit)


@pytest.mark.parametrize("adapter", ["pi", "claude"])
@pytest.mark.parametrize("limit", [16384, 49152])
def test_exact_dispatched_utf8_boundary_counts_adapter_framing(adapter, limit):
    from frutlups import render
    framing = len(render.dispatch_framing(adapter).encode())
    text = "界" * ((limit - framing) // 3) + "x" * ((limit - framing) % 3)
    assert render.prompt_size(text, adapter) == limit
    assert render.require_prompt(text, limit, adapter) == text
    with pytest.raises(RenderError, match="byte bound"):
        render.require_prompt(text + "x", limit, adapter)


def test_native_custom_input_and_expansion_are_bounded(tmp_path):
    from frutlups import render
    path = tmp_path / "custom.md"
    path.write_bytes(b"x" * (render.SUPPORT_LIMIT + 1))
    with pytest.raises(RenderError, match="template exceeds"):
        render._render_template(path, {}, (), coding_v2=True)
    ref = "`reviews/old.md` (sha256 `" + "b" * 64 + "`)"
    path.write_text("{{notes}}" * 1000, encoding="utf-8")
    text = render._render_template(path, {"notes": "界" * 10000 + "\n" + ref}, (), coding_v2=True)
    assert ref in text
    assert len(text.encode()) < 1000


def test_native_selected_findings_keep_source_identity(tmp_path):
    from frutlups import render, ledger
    root = _repository(tmp_path)
    model = load(root / "roadmap.yaml")
    item = model.milestones[0].slices[0]
    state = replace(fold((), model).slices[item.id], last_report=_prior_report(root),
                    open_findings=("M001-S01-F1",))
    frozen = render.coding_envelope(root, model, item, state)
    ref = dict(path=state.last_report, sha=ledger.evidence_sha((root / state.last_report).read_bytes()))
    assert "| M001-S01-F1 | P2 | open | Preserve this complete row. |" in frozen["findings"]
    assert render.reference_text(ref) in frozen["findings"]
    (root / state.last_report).write_bytes(b"x" * (render.SUPPORT_LIMIT + 1))
    with pytest.raises(RenderError, match="finding support"):
        render.coding_envelope(root, model, item, state)


@pytest.mark.parametrize("kind", ["routed", "holistic"])
@pytest.mark.parametrize("fallback", [False, True])
def test_native_review_guidance_matches_parser_contract(kind, fallback, tmp_path):
    from frutlups import render, verdict

    model = load(FIXTURE / "roadmap.yaml")
    milestone = model.milestones[0]
    item = milestone.slices[0]
    state = replace(fold((), model).slices[item.id], round=1)
    frozen = render.coding_envelope(FIXTURE, model, item, state)
    root = FIXTURE
    if fallback:
        root = tmp_path
        template_path = root / "prompts/templates/review_prompt.md"
        template_path.parent.mkdir(parents=True)
        template_path.write_text("界" * (render.REVIEW_LIMIT // 2), encoding="utf-8")
    if kind == "routed":
        output = render.review_v2(root, frozen, {}, "reviews/report.md")
        identity, round_text = item.id, "1"
    else:
        output = render.holistic_v2(
            root, milestone, {"accepted": [], "latest": [], "cycle": []},
        )
        identity, round_text = milestone.id, "holistic"

    choices = output.rsplit("Objective status: ", 1)[1].split(" and ", 1)[0].split("|")
    template = (FIXTURE / "prompts/templates/review_prompt.md").read_text(encoding="utf-8")
    pinned = template.split("Objective status: ", 1)[1].splitlines()[0].split(" | ")
    assert choices == pinned
    assert choices == ["achieved", "not_achieved", "indeterminate"]
    assert "Verdict: pass|needs_work|blocked - next:" in output
    assert render.FINDING_UPDATES in output
    assert "| source | sha | id | disposition | related |" in output
    assert "Autonomous seats cannot use waived_by_human" in output
    assert "unsupported" not in render.REVIEW_GUIDANCE
    assert render.prompt_size(output, "pi") <= render.REVIEW_LIMIT
    if fallback:
        assert "omitted" in output

    for status in choices:
        report = f"""# Review: {identity} round {round_text}

## Findings
| id | severity | disposition | summary |
| --- | --- | --- | --- |

## Closure Decision
Objective status: {status}
Objective evidence: External evidence is required.

## Verdict
Verdict: blocked - next: obtain external evidence
"""
        parsed = (verdict.autonomous_slice(report, item.id, 1) if kind == "routed"
                  else verdict.autonomous_holistic(report, milestone))
        assert parsed.objective_status == status
        assert parsed.verdict == "blocked"
        result = subprocess.run(
            [sys.executable, "-B", "-c",
             "import json, sys; from _evidence import parse_review; "
             "print(json.dumps(parse_review(sys.stdin.read())))"],
            cwd=FIXTURE / "scripts", input=report, capture_output=True, text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        pinned_report = json.loads(result.stdout)
        assert pinned_report["objective_status"] == status
        assert pinned_report["verdict"] == "blocked"
