"""Ledger-backed workspace admission and review evidence for the loop."""

import json
import logging
import re

from . import gitws, ledger, render, verdict
from ._paths import repo_path


def changes(root, cfg):
    return tuple(
        {"path": row.path, "sha": row.sha256, "kind": row.kind}
        for row in gitws.changed_files(root, executable=cfg.git)
        if not row.path.startswith("local_state/")
    )


def evidence_paths(events):
    paths = set()
    for event in events:
        for key in ("path", "receipt", "report", "notes_path"):
            if event.data.get(key):
                paths.add(event.data[key])
    return paths


def workspace_errors(root, cfg, events):
    """Admit only immutable evidence and exact recorded product/baseline identities."""
    errors = [str(error) for error in ledger.check(root / cfg.ledger, root)]
    products = {}
    for event in events:
        for key in ("baseline", "changed"):
            products.update({row["path"]: dict(row) for row in event.data.get(key, ())})
    artifacts = evidence_paths(events) | {cfg.ledger}
    current = changes(root, cfg)
    errors += [
        f"unrecorded change: {row['path']}"
        for row in current
        if row["path"] not in artifacts and products.get(row["path"]) != row
    ]
    if not errors and not gitws.is_clean(
        root,
        ("local_state/", *(row["path"] for row in current)),
        executable=cfg.git,
    ):
        errors.append("workspace changed during admission")
    return errors


def next_path(root, directory, name):
    target = repo_path(root, directory)
    numbers = (
        [int(match[1]) for path in target.iterdir() if (match := re.match(r"(\d{3,})_", path.name))]
        if target.exists()
        else []
    )
    return (target / f"{max(numbers, default=0) + 1:03}_{name}").relative_to(root).as_posix()


def write_text(root, directory, name, text, limit=None, *, path=None):
    target = repo_path(root, directory)
    target.mkdir(parents=True, exist_ok=True)
    # Exclusive creation also protects immutable evidence across resumed runs.
    path = repo_path(root, path or next_path(root, directory, name))
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
    if limit and len(text.encode("utf-8")) > limit:
        logging.getLogger(__name__).warning("%s exceeds %s KB", path.name, limit // 1024)
    return path.relative_to(root).as_posix(), ledger.evidence_sha(path.read_bytes())


def receipt_tail(root, state):
    if not state.last_receipt:
        return None
    result = json.loads(repo_path(root, state.last_receipt).read_text(encoding="utf-8"))
    if result["ok"]:
        return None
    return "Verification failed:\n" + json.dumps(result, ensure_ascii=False, indent=2)


def review_changes(root, cfg, events, state):
    cumulative = {}
    for event in events:
        if event.slice == state.id and event.ev == ledger.Ev.coded:
            cumulative.update({row["path"]: row for row in event.data["changed"]})
    return render.ReviewChanges(tuple(cumulative.values()), _diff(root, cfg, "HEAD", state.changed))


def _diff(root, cfg, base, changed):
    """Follow the template: HEAD diff for current paths, plus untracked added bodies."""
    paths = [row["path"] for row in changed]
    if not paths:
        return "(no product changes)"
    result = gitws._git(
        root,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        base,
        "--",
        *paths,
        executable=cfg.git,
    )
    blocks = [result.stdout.decode("utf-8", "replace")]
    for row in changed:
        if row["kind"] != "added":
            continue
        path = row["path"]
        tracked = gitws._git(
            root,
            "ls-files",
            "--error-unmatch",
            "--",
            path,
            executable=cfg.git,
            check=False,
        )
        if tracked.returncode:
            try:
                body = repo_path(root, path).read_text(encoding="utf-8")
            except UnicodeError:
                body = "[Not UTF-8 text; inspect locally.]"
            blocks.append(f"\nAdded file: {path}\n{body}")
    return "".join(blocks)


def combined_review(reviews):
    """Merge shared ids by disposition, then severity; keep the winning row verbatim."""
    if len(reviews) == 1:
        return reviews[0][1]
    priority = {"pass": 0, "needs_work": 1, "blocked": 2}
    selected = max((review for review, _ in reviews), key=lambda r: priority[r.verdict])
    dispositions = ("open", "carried", "closed_by_review", "waived_by_human")
    rows = {}
    for review, text in reviews:
        source_rows = verdict.finding_rows(text, tuple(f.id for f in review.findings))
        for finding, row in zip(review.findings, source_rows):
            rank = (dispositions.index(finding.disposition), finding.severity)
            if finding.id not in rows or rank < rows[finding.id][0]:
                rows[finding.id] = (rank, row)
    return (
        f"# Review: {selected.identity} round {selected.round}\n\n## Findings\n\n"
        "| id | severity | disposition | summary |\n| --- | --- | --- | --- |\n"
        + "\n".join(row for _, row in rows.values())
        + "\n\n## Closure Decision\n\n"
        f"Objective status: {selected.objective_status}\n"
        f"Objective evidence: {selected.objective_evidence}\n\n## Verdict\n"
        f"Verdict: {selected.verdict} - next: {selected.next_move}\n"
    )


def holistic_changes(root, cfg, events, milestone):
    ids = {item.id for item in milestone.slices}
    cumulative = {}
    paths_by_slice = {item.id: {} for item in milestone.slices}
    base = None
    receipts = {}
    for event in events:
        if event.slice not in ids:
            continue
        if event.ev == ledger.Ev.coded:
            cumulative.update({row["path"]: row for row in event.data["changed"]})
            paths_by_slice[event.slice].update({row["path"]: row for row in event.data["changed"]})
        elif event.ev == ledger.Ev.verified and event.data["ok"]:
            receipts[event.slice] = event.data["receipt"]
        elif event.ev == ledger.Ev.accepted and base is None:
            data = json.loads(repo_path(root, receipts[event.slice]).read_text(encoding="utf-8"))
            base = data["base_commit"]
    paths = tuple(cumulative)
    text = "(No changed product paths.)"
    if paths:
        stat = gitws._git(
            root,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--stat",
            base,
            "--",
            *paths,
            executable=cfg.git,
        )
        heading = f"Base commit: {base}\n{stat.stdout.decode('utf-8', 'replace')}"
        omitted = (
            heading + "\nPer-slice diffs omitted by the bounded evidence gate; inspect locally."
        )
        if len(paths) > 64 or len(stat.stdout) > 4096:
            text = omitted
        else:
            text = heading
            seen = set()
            for sid, rows in paths_by_slice.items():
                unique = [row for path, row in rows.items() if path not in seen]
                seen.update(rows)
                if unique:
                    text += f"\n### {sid}\n" + _diff(root, cfg, base, unique)
            if len(text.encode("utf-8")) > render.DIFF_LIMIT:
                text = omitted
    return render.ReviewChanges(tuple(cumulative.values()), text)


def usage(results, *, coder=False):
    keys = ("secs", "tokens_in", "tokens_out") + (() if coder else ("cost_usd",))
    return {
        key: sum(values)
        for key in keys
        if (
            values := [
                getattr(result, key) for result in results if getattr(result, key) is not None
            ]
        )
    }
