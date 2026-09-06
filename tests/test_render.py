import inspect
import json
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from frutlups.ledger import fold
from frutlups.render import (
    DIFF_LIMIT,
    RenderError,
    ReviewChanges,
    coding,
    holistic,
    render_template,
    review,
)
from frutlups.roadmap import load

FIXTURE = Path(__file__).parent / "fixtures" / "v4_project"


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

    actual = coding(root, roadmap, item, state, None)

    assert actual == generated.read_text(encoding="utf-8")


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

    output = coding(
        root, roadmap, item, state, "Failed command stderr tail."
    )

    assert "| M001-S01-F1 | P2 | open | Preserve this complete row. |" in output
    assert "M001-S01-F2" not in output
    assert "Unblock reason: Human approved a retry." in output
    assert "Failed command stderr tail." in output


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
    changes = ReviewChanges(
        cumulative=(
            {"path": "07_app/earlier.py", "sha": "a" * 64, "kind": "added"},
            {"path": "07_app/current.py", "sha": "b" * 64, "kind": "modified"},
        ),
        current_diff="+" + "x" * (DIFF_LIMIT + 1000),
    )
    receipt = {"schema": "frutlups.receipt/1", "ok": True}
    report_path = "05_governance/reviews/m001/M001-S01_r2_review.md"

    output = review(root, roadmap, item, state, changes, "Coder notes here.", receipt, report_path)

    assert "`07_app/earlier.py` (added, sha256 `" in output
    assert "earlier round" in output
    assert "`07_app/current.py` (modified, sha256 `" in output
    assert "current round" in output
    assert "Diff truncated at 32 KB" in output
    assert "Coder notes here." in output
    assert json.dumps(receipt, separators=(",", ":")) in output
    assert "| M001-S01-F1 | P2 | open | Preserve this complete row. |" in output
    assert report_path in output
    assert "Start every finding ID with `M001-S01-`." in output
    diff = output.split("```diff\n", 1)[1].split("\n```", 1)[0]
    assert len(diff.encode()) <= DIFF_LIMIT


def test_holistic_render_includes_slice_scoped_finding_rule() -> None:
    roadmap = load(FIXTURE / "roadmap.yaml")
    milestone = roadmap.milestones[0]

    output = holistic(
        FIXTURE,
        roadmap,
        milestone,
        {"M001-S01": '{"ok":true}'},
        {"M001-S01": "reviews/M001-S01.md"},
    )

    assert "# Review prompt: M001" in output
    assert "Every P0-P2 finding ID must start with the affected slice ID" in output


def test_substitution_does_not_rescan_known_placeholder_tokens(tmp_path: Path) -> None:
    path = tmp_path / "template.md"
    path.write_text("{{coder_notes}}\n{{title}}\n", encoding="utf-8")

    output = render_template(path, {"coder_notes": "keep {{title}}", "title": "changed"})

    assert output == "keep {{title}}\nchanged\n"


def test_repository_renderers_take_root_first() -> None:
    for renderer in (coding, review, holistic):
        assert next(iter(inspect.signature(renderer).parameters)) == "root"
