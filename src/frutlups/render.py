"""Pure rendering for coding, review, and holistic prompts."""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ._paths import repo_path, value
from .ledger import SliceState, evidence_sha
from .roadmap import Milestone, Roadmap, Slice, effective_prefixes
from .verdict import finding_rows

KNOWN = {
    "slice_id", "title", "round", "objective", "acceptance", "non_goals",
    "read_first", "allowed_prefixes", "forbidden", "focused", "full",
    "open_findings", "memory", "diff_manifest", "diff_evidence", "coder_notes",
    "receipt", "prior_findings", "report_path", "finding_id_rule",
}
DIFF_LIMIT = 32 * 1024
REVIEW_OPTIONAL = (("Coder notes", "coder_notes"), ("Prior findings", "prior_findings"))


class RenderError(ValueError):
    """A prompt template has an invalid or unresolved placeholder."""


@dataclass(frozen=True)
class ReviewChanges:
    cumulative: Sequence[object]
    current_diff: str


def _template(root: Path, name: str) -> Path:
    return repo_path(root, f"prompts/templates/{name}")


def _bullets(values: Sequence[str], empty: str = "- None declared.") -> str:
    return "\n".join(f"- {value}" for value in values) if values else empty


def _argv(values: Sequence[Sequence[str]] | Sequence[str]) -> str:
    if not values:
        return "- None declared."
    commands = [values] if isinstance(values[0], str) else values
    return "\n\n".join("```text\n" + " ".join(command) + "\n```" for command in commands)


def render_template(
    path: Path, values: Mapping[str, object], optional: Sequence[tuple[str, str]] = (),
) -> str:
    text = path.read_text(encoding="utf-8")
    found = set(re.findall(r"{{([a-z_]+)}}", text))
    unknown = found - KNOWN
    if unknown:
        raise RenderError(f"unknown placeholders: {sorted(unknown)}")
    bare = re.sub(r"{{[a-z_]+}}", "", text)
    if "{{" in bare or "}}" in bare:
        raise RenderError("unresolved placeholder")
    for heading, key in optional:
        if not values.get(key):
            pattern = rf"\n## {re.escape(heading)}\n.*?(?=\n## |\Z)"
            text = re.sub(pattern, "", text, flags=re.DOTALL)
    text = re.sub(
        r"{{([a-z_]+)}}", lambda match: str(values.get(match.group(1), "")), text,
    )
    return text.rstrip() + "\n"


def _memory(roadmap: Roadmap, item: Slice) -> str:
    memory = roadmap.memory
    if not memory:
        return ""
    root = memory.root.rstrip("/")
    lines = [f"Root: `{root}`", "", "Allowed read commands:"]
    lines += [f"- `llloom --root {root} {verb}`" for verb in memory.read_verbs]
    pages = item.memory_pages or memory.read_first_pages
    if pages:
        lines += ["", "Read for this slice:"] + [f"- `{page}`" for page in pages]
    lines += ["", (
        "Cite claim or page ids used. Report stale or contradicted claims; "
        "do not hand-edit memory."
    )]
    return "\n".join(lines)


def _findings_context(root: Path, state: SliceState) -> str:
    rows = ()
    if state.last_report and state.open_findings:
        path = repo_path(root, state.last_report)
        rows = finding_rows(path.read_text(encoding="utf-8"), state.open_findings)
    if state.reopen_reason:
        rows += (f"Reopen reason: {state.reopen_reason}",)
        if state.reopen_report:
            ids = tuple(re.findall(rf"{state.id}-[\w-]+", state.reopen_reason))
            report = repo_path(root, state.reopen_report).read_text(encoding="utf-8")
            rows += finding_rows(report, ids)
    rows += (f"Unblock reason: {state.unblock_reason}",) if state.unblock_reason else ()
    return "\n".join(rows)


def coding(
    root: Path, roadmap: Roadmap, item: Slice, state: SliceState, receipt_tail: str | None,
) -> str:
    focused = item.focused or roadmap.focused_default
    context = _findings_context(root, state)
    findings = "\n\n".join(part for part in (context, receipt_tail) if part)
    values = {
        "slice_id": item.id, "title": item.title, "round": state.round,
        "objective": item.objective.strip(), "acceptance": _bullets(item.acceptance),
        "non_goals": _bullets(item.non_goals),
        "read_first": _bullets([f"`{path}`" for path in item.read_first]),
        "allowed_prefixes": ", ".join(
            f"`{path}`" for path in effective_prefixes(roadmap, item)
        ),
        "forbidden": ", ".join(f"`{path}`" for path in roadmap.forbidden),
        "focused": _argv(focused), "full": _argv(item.verification or roadmap.verification_full),
        "open_findings": findings, "memory": _memory(roadmap, item),
    }
    return render_template(
        _template(root, "coding_prompt.md"), values,
        (("Findings to resolve", "open_findings"), ("Memory", "memory")),
    )


def _diff_block(text: str, paths: Sequence[str]) -> str:
    notice = (
        "\n[Diff truncated at 32 KB. Inspect the listed changed paths locally with "
        "`git diff HEAD -- <path>`.]"
    )
    raw = text.encode("utf-8")
    if len(raw) > DIFF_LIMIT:
        room = DIFF_LIMIT - len(notice.encode("utf-8"))
        text = raw[:room].decode("utf-8", "ignore").rstrip() + notice
    if not text.strip():
        text = "(no textual diff; inspect " + ", ".join(paths) + ")"
    return "```diff\n" + text.rstrip() + "\n```"


def review(
    root: Path, roadmap: Roadmap, item: Slice, state: SliceState, changed: ReviewChanges,
    notes_text: str, receipt_json: str | Mapping[str, object], report_path: str,
) -> str:
    current_paths = {str(value(entry, "path")) for entry in state.changed}
    manifest = []
    paths = []
    for entry in changed.cumulative:
        path = str(value(entry, "path"))
        digest = value(entry, "sha", value(entry, "sha256"))
        label = "current round" if path in current_paths else "earlier round"
        manifest.append(f"`{path}` ({value(entry, 'kind')}, sha256 `{digest}`, {label})")
        paths.append(path)
    receipt_text = (
        receipt_json if isinstance(receipt_json, str)
        else json.dumps(receipt_json, ensure_ascii=False, separators=(",", ":"))
    )
    receipt_path = state.last_receipt or "<not recorded>"
    prior = _findings_context(root, state)
    values = {
        "slice_id": item.id, "title": item.title, "round": state.round,
        "objective": item.objective.strip(), "acceptance": _bullets(item.acceptance),
        "diff_manifest": _bullets(manifest),
        "diff_evidence": _diff_block(changed.current_diff, paths),
        "coder_notes": notes_text,
        "receipt": (
            f"Path: `{receipt_path}`\n\n"
            f"SHA-256: `{evidence_sha(receipt_text.encode())}`\n\n"
            f"```json\n{receipt_text.rstrip()}\n```"
        ),
        "prior_findings": prior, "report_path": report_path,
        "finding_id_rule": f"Start every finding ID with `{item.id}-`.",
    }
    return render_template(_template(root, "review_prompt.md"), values, REVIEW_OPTIONAL)


def holistic(
    root: Path, roadmap: Roadmap, milestone: Milestone, receipts: Mapping[str, str],
    reports: Mapping[str, str], *, report_path: str | None = None,
    changed: ReviewChanges | None = None,
) -> str:
    receipt_blocks = [
        f"### {item.id}\n\n```json\n{receipts[item.id].rstrip()}\n```\n\n"
        f"Review: `{reports[item.id]}`"
        for item in milestone.slices
    ]
    values = {
        "slice_id": milestone.id, "title": milestone.title, "round": "holistic",
        "objective": f"Judge holistic closure of {milestone.id} across its accepted slices.",
        "acceptance": _bullets([
            f"{item.id}: {value}" for item in milestone.slices for value in item.acceptance
        ]),
        "diff_manifest": _bullets([
            f"`{path}` ({slice_id} review)" for slice_id, path in reports.items()
        ]),
        "diff_evidence": "```diff\n(Inspect cumulative accepted-slice diffs locally.)\n```",
        "coder_notes": "", "receipt": "\n\n".join(receipt_blocks), "prior_findings": "",
        "report_path": report_path or (
            f"05_governance/reviews/{milestone.id.lower()}/{milestone.id}_holistic_review.md"
        ),
        "finding_id_rule": (
            "Every P0-P2 finding ID must start with the affected slice ID, "
            "for example `M001-S02-H1-F1`."
        ),
    }
    if changed is not None:
        paths = [str(value(row, "path")) for row in changed.cumulative]
        values["diff_manifest"] = _bullets([
            f"{value(row, 'path')} ({value(row, 'kind')}, sha256 {value(row, 'sha')})"
            for row in changed.cumulative
        ])
        values["diff_evidence"] = _diff_block(changed.current_diff, paths)
    return render_template(_template(root, "review_prompt.md"), values, REVIEW_OPTIONAL)
