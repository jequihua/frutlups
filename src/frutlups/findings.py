"""Explicit report-bound finding updates and a disposable backlog projection."""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import asdict

from . import ledger, verdict

# Native implementation of the accepted source-bound finding contract.

BEGIN = "<!-- findings:begin -->"
END = "<!-- findings:end -->"


def updates(text):
    return [dict(asdict(row), related=list(row.related))
            for row in verdict.parse(text, native=True).updates]


def report_events(events):
    """Include checkpoints and decisions independently, even for the same artifact."""
    for event in events:
        event = ledger._event_dict(event) if isinstance(event, ledger.Event) else event
        ledger._need(
            event.get("schema") == ledger.SCHEMA_V2,
            "unsupported legacy history; use pinned v4",
        )
        if event["ev"] in ("reviewed", "holistic_reviewed", "review_checkpoint"):
            ref = (event["report"], event["sha"])
        elif event["ev"] == "artifact" and event.get("role") == "holistic_report":
            ref = (event["path"], event["sha"])
        else:
            continue
        yield ref, event


def read_report(root, ref):
    """Bound and re-prove source bytes and file identity on every read."""
    from ._loop_evidence import bound_text

    text = bound_text(root, ref, 1024 * 1024)
    parsed = verdict.parse(text, native=True)
    report = asdict(parsed)
    report.pop("updates")
    report["findings"] = [asdict(row) for row in parsed.findings]
    report["open"] = list(verdict.open_blocking(parsed))
    return text, report


def source_label(key):
    path, sha, identity = key
    return f"{identity} (source {path}, sha {sha})"


class Projection:
    """One chronological pass; original finding identity is never rewritten."""

    def __init__(self, root):
        self.root = root
        self.findings = {}
        self.ids = {}
        self.seen = set()
        self.diagnostics = []
        self.open = set()
        self.open_by_scope = {}

    def _index(self, key):
        item = self.findings[key]
        scopes = {item["owner"], item["owner"].split("-")[0]}
        prefix = re.match(r"^(M\d{3})-(?:(S\d{2})-)?", item["id"])
        if prefix:
            scopes.add(prefix[1])
            if prefix[2]:
                scopes.add(prefix[1] + "-" + prefix[2])
        unresolved = item["severity"] in ("P0", "P1", "P2") and item["disposition"] == "open"
        self.open.discard(key)
        if unresolved:
            self.open.add(key)
        for scope in scopes:
            keys = self.open_by_scope.setdefault(scope, set())
            keys.discard(key)
            if unresolved:
                keys.add(key)

    def add(self, ref, event):
        ledger._need(
            event.get("schema") == ledger.SCHEMA_V2,
            "unsupported legacy history; use pinned v4",
        )
        path, sha = ref
        text, report = read_report(self.root, {"path": path, "sha": sha})
        changes = updates(text)
        waived = report["verdict"] == "override" or any(
            item["disposition"] == "waived_by_human" for item in report["findings"] + changes
        )
        if waived and event["by"] != "human":
            raise ValueError("waiver requires --by human")
        if event["ev"] == "review_checkpoint" or ref in self.seen:
            return report
        self.seen.add(ref)
        owner = report["identity"]
        for item in report["findings"]:
            key = (path, sha, item["id"])
            ledger._need(
                item["severity"] == "P3" or item["disposition"] != "carried",
                "only P3 findings may be carried",
            )
            ledger._need(
                item["id"] not in self.ids,
                f"finding ID collision: {item['id']}; use source-bound update",
            )
            self.ids.setdefault(item["id"], set()).add(key)
            self.findings[key] = {
                **item,
                "source": {"path": path, "sha": sha},
                "related": [],
                "last_report": path,
                "owner": owner,
            }
            self._index(key)
        for change in changes:
            key = (change["source"], change["sha"], change["id"])
            ledger._need(
                key in self.findings, "unresolved finding reference: " + source_label(key)
            )
            ledger._need(
                self.findings[key]["severity"] == "P3" or change["disposition"] != "carried",
                "only P3 findings may be carried",
            )
            for linked in change["related"]:
                ledger._need(linked in self.ids, f"unknown related finding: {linked}")
                ledger._need(len(self.ids[linked]) == 1, f"ambiguous related finding: {linked}")
            self.findings[key].update(
                disposition=change["disposition"], related=change["related"], last_report=path
            )
            self._index(key)
        count = re.search(r"^Finding count: (\d+)\s*$", text, re.MULTILINE)
        if count and int(count[1]) != len(report["findings"]):
            self.diagnostics.append(f"Finding count differs from parsed IDs in {path}")
        return report

    def require_closure(self, report, current=None):
        if report["verdict"] not in ("pass", "override"):
            return
        scope = report["identity"]
        pending = set(self.open_by_scope.get(scope, ()))
        for identity in (current or {}).get("open", []):
            pending.update(self.ids.get(identity, set()) & self.open)
        unresolved = [source_label(key) for key in sorted(pending)]
        ledger._need(
            not unresolved,
            "prior findings need explicit source-bound closure: " + ", ".join(unresolved),
        )


def project(root, events, extra=None):
    projection = Projection(root)
    for ref, event in report_events(events):
        projection.add(ref, event)
    if extra:
        projection.add(*extra)
    return projection.findings, projection.diagnostics


def validate_record(root, events, path, sha, by, current=None):
    event = {"ev": "reviewed", "schema": ledger.SCHEMA_V2, "by": by}
    projection = Projection(root)
    for ref, previous in report_events(events):
        projection.add(ref, previous)
    report = projection.add((path, sha), event)
    projection.require_closure(report, current)
    return projection.diagnostics


def _backlog_text(old, findings):
    marker_lines = [line.strip() for line in old.splitlines() if "<!-- findings:" in line]
    ledger._need(
        marker_lines in ([], [BEGIN, END])
        and old.count(BEGIN) == old.count(END)
        and old.count(BEGIN) <= 1,
        "backlog generated markers are malformed",
    )
    rows = [
        BEGIN,
        "Source digests use normalized evidence SHA-256; see [hash rules](../docs/ledger.md).",
        "",
        "| ID | Severity | Disposition | Source | Summary |",
        "| --- | --- | --- | --- | --- |",
    ]
    for key in sorted(findings):
        item = findings[key]
        summary = item["summary"].replace("|", r"\|")
        rows.append(
            f"| {item['id']} | {item['severity']} | {item['disposition']} | "
            f"{item['source']['path']} ({item['source']['sha']}) | {summary} |"
        )
    rows += [END]
    generated = "\n".join(rows)
    if BEGIN in old:
        start, end = old.index(BEGIN), old.index(END) + len(END)
        ledger._need(start < end, "backlog generated markers out of order")
        text = old[:start] + generated + old[end:]
    else:
        text = old.rstrip() + "\n\n" + generated + "\n"
    return text


def matches_backlog(root, events):
    projected, _ = project(root, events)
    path = ledger._regular_reference(root, {"path": "05_governance/backlog.md", "sha": "0" * 64})
    old = path.read_text(encoding="utf-8")
    return old == _backlog_text(old, projected)


def reconcile(root, events):
    findings, diagnostics = project(root, events)
    path = ledger._regular_reference(root, {"path": "05_governance/backlog.md", "sha": "0" * 64})
    old = path.read_text(encoding="utf-8")
    text = _backlog_text(old, findings)
    if text != old:
        # Replace only the derived file; a failed replacement leaves durable
        # history available for the next reconciliation under writer ownership.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".findings-", delete=False) as stream:
                temporary = stream.name
                stream.write(text.encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None and os.path.exists(temporary):
                os.unlink(temporary)
    return diagnostics
