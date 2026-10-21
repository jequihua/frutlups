"""The three autonomous CLI verbs and their text/JSON presentation."""

import argparse
import json
import math
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from . import ledger, loop, receipt, roadmap
from .config import Config

USAGE = "usage: frutlups {preflight,run,status}"
VERBS = {"preflight", "run", "status"}


def _text(row):
    action = row["action"]
    scope = row.get("slice", row.get("scope", row.get("milestone", "")))
    prefix = f"{scope} r{row['round']}" if row.get("round") else scope
    if row.get("round") == "holistic":
        prefix = f"{scope} holistic"
    if action == "stop":
        return f"{row['reason']}: {row['detail']}"
    if action in ("preflight", "internal"):
        return f"{action}: {row.get('detail', 'ok')}"
    if action in ("prompt", "artifact"):
        return f"{prefix} {row.get('role', action)} -> {row['path']}"
    if action in ("coded", "reviewed"):
        seats = ",".join(f"{s['adapter']}/{s['model']}/{s['effort']}" for s in row["seats"])
        usage = (
            f"{row.get('secs', '?')}s in={row.get('tokens_in', '?')} "
            f"out={row.get('tokens_out', '?')}"
        )
        if action == "coded":
            return f"{prefix} coder {seats} {usage} changed={len(row['changed'])}"
        return f"{prefix} review {seats} {row['verdict']} open={len(row['open'])} {usage}"
    if action == "verified":
        return f"{prefix} verify {'ok' if row['ok'] else 'failed'} {row['secs']}s"
    if action == "accepted":
        commit = f" commit {row['commit'][:7]}" if row.get("commit") else ""
        return f"{scope} accepted{commit}"
    if action == "reopened":
        return f"{prefix} reopened: {row['reason']}"
    if action == "usage":
        if row.get("note") == "unreadable":
            return f"usage note unreadable={row['count']}"
        return f"{scope} usage " + " ".join(
            f"{key}={row[key] if row[key] is not None else '?'}"
            for key in ("secs", "tokens_in", "tokens_out", "cost_usd")
        ) + f" completeness={row['completeness']} unresolved_attempts={row['unresolved_attempts']}"
    return f"{prefix} {action}"


def _emit(row, root, json_mode, write=print):
    # Scrub before serializing so JSON escaping cannot hide a secret or local path.
    def scrub(value):
        if isinstance(value, str):
            return receipt.scrub_text(value, root, dict(os.environ))
        if isinstance(value, dict):
            return {key: scrub(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [scrub(item) for item in value]
        return value

    row = scrub(row)
    write(json.dumps(row, ensure_ascii=True) if json_mode else " ".join(_text(row).splitlines()))


USAGE_KEYS = ("secs", "tokens_in", "tokens_out", "cost_usd")


def _usage_row(contributions, unresolved):
    """Sum known values; complete needs every observation covered and nothing unfinished."""
    totals, overflow = {}, False
    for key in USAGE_KEYS:
        numbers = [usage[key] for usage, _ in contributions if key in usage]
        total = sum(numbers) if numbers else None
        if isinstance(total, float) and not math.isfinite(total):
            total, overflow = None, True  # Never emit Infinity or a wrapped total.
        totals[key] = total
    if all(value is None for value in totals.values()):
        completeness = "unknown"
    elif (unresolved or overflow or not all(covered for _, covered in contributions)
          or any(value is None for value in totals.values())):
        completeness = "partial"
    else:
        completeness = "complete"
    return {**totals, "completeness": completeness, "unresolved_attempts": unresolved}


def _usage(root, model, events):
    scopes = {
        item.id: {"contributions": [], "unresolved": 0}
        for milestone in model.milestones for item in (milestone, *milestone.slices)
    }
    # Native durable finishes own attempt usage, including retries and failures.
    # Unprofiled native histories retain usage on their lifecycle events.
    starts = {e.data["invocation"]: {**e.data, "round": e.round} for e in events if e.ev == ledger.Ev.attempt_started}
    attempt_scopes = {(s["scope"], s["round"], s["role"]) for s in starts.values()}
    finished = set()
    for event in events:
        if event.ev == ledger.Ev.attempt_finished:
            start = starts[event.data["invocation"]]
            finished.add(event.data["invocation"])
            scope, usage = start["scope"], event.data.get("usage", {})
            if start["role"] == "verification":
                # Measured seconds only; no provider tokens or cost apply.
                covered = "secs" in usage
            else:
                covered = (usage.get("completeness") == "complete"
                           and all(key in usage for key in USAGE_KEYS))
        elif event.ev in (ledger.Ev.coded, ledger.Ev.reviewed):
            role = "coder" if event.ev == ledger.Ev.coded else "reviewer"
            if (event.slice, event.round, role) in attempt_scopes:
                continue
            # Lifecycle copies record no coverage, so their known values stay partial.
            scope, usage, covered = event.slice, event.data, False
        else:
            continue
        scopes[scope]["contributions"].append(
            ({key: usage[key] for key in USAGE_KEYS if key in usage}, covered))
    for invocation, start in starts.items():
        if invocation not in finished:
            scopes[start["scope"]]["unresolved"] += 1
    for milestone in model.milestones:
        for item in milestone.slices:
            current = scopes[item.id]
            yield {"action": "usage", "slice": item.id,
                   **_usage_row(current["contributions"], current["unresolved"])}
        members = [scopes[milestone.id], *(scopes[item.id] for item in milestone.slices)]
        yield {"action": "usage", "milestone": milestone.id,
               **_usage_row([c for m in members for c in m["contributions"]],
                            sum(m["unresolved"] for m in members))}


def _status(root, usage, json_mode, write=print):
    cfg = Config.load(root)
    model = roadmap.load(root / "roadmap.yaml")
    events = ledger.read(root / cfg.ledger)
    state = (ledger.validate_history(root, events, model, executable=cfg.git) if model.autonomy is not None
             else ledger.fold(events, model))
    from . import commit
    commits = commit.status(root, events, model, executable=cfg.git) if model.schema == ledger.ROADMAP_V2 and state.intents else []
    if json_mode:
        for milestone in model.milestones:
            for item in milestone.slices:
                current = state.slices[item.id]
                _emit(
                    {
                        "action": "status",
                        "slice": item.id,
                        "round": current.round,
                        "step": current.step,
                    },
                    root,
                    True,
                    write,
                )
        following = roadmap.next_slice(model, state)
        _emit({"action": "next", "slice": following.id if following else None}, root, True, write)
    else:
        write(ledger.status_text(model, state))
    for name, run in state.runs.items():
        if json_mode:
            _emit({"action": "run_status", "run": name, "revision": run["revision"],
                   "stop": run["admission"]["stop"], "state": run["closed"] or "open",
                   "remaining": dict(run["remaining"])}, root, True, write)
        else:
            balances = " ".join(f"{key}={run['remaining'][key]}" for key in
                                ("jobs", "transport_retries", "format_retries", "seconds"))
            write(f"run {name} revision={run['revision']} stop={run['admission']['stop']} "
                  f"state={run['closed'] or 'open'} remaining {balances}")
    for row in commits:
        if json_mode:
            _emit({"action": "commit", **row}, root, True, write)
        else:
            write(f"commit {row['id']}: {row['state']}")
    for mid, value in state.holistic.items():
        if json_mode:
            _emit({"action": "holistic", "milestone": mid, "step": value["step"]}, root, True, write)
        else:
            write(f"{mid} holistic: {value['step']}")
    for key, value in state.attempts.items():
        if value["finish"] is None:
            if json_mode:
                _emit({"action": "unresolved_invocation", "invocation": key}, root, True, write)
            else:
                write(f"unresolved invocation: {key}")
    for milestone in model.milestones:
        complete = all(state.slices[item.id].step == "accepted" for item in milestone.slices)
        complete = complete and (not milestone.holistic_review or milestone.id in state.milestones_done)
        if milestone.status == "done" and not complete:
            detail = f"{milestone.id} declared done without ledger closure"
            if json_mode:
                _emit({"action": "warning", "milestone": milestone.id, "detail": detail}, root, True, write)
            else:
                write(f"warning: {detail}")
    if usage:
        for row in _usage(root, model, events):
            _emit(row, root, json_mode, write)


def main(argv: Sequence[str] | None = None) -> None:
    def write(text):
        try:
            print(text)
        except OSError:
            pass  # A closed output stream must not interrupt the verb.

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in VERBS:
        write(USAGE)
        raise SystemExit(2)
    parser = argparse.ArgumentParser(prog=f"frutlups {args[0]}")
    parser.add_argument("root", nargs="?", default=".", type=Path)
    if args[0] == "run":
        parser.add_argument("--until", choices=("slice", "milestone", "roadmap"))
        parser.add_argument("--once", action="store_true")
    if args[0] == "status":
        parser.add_argument("--usage", action="store_true")
    else:
        # Explicit historical selection: the raw (path, SHA-256) pair, both or neither.
        parser.add_argument("--historical-assessment", metavar="PATH")
        parser.add_argument("--historical-assessment-sha", metavar="SHA256")
    if args[0] != "preflight":
        parser.add_argument("--json", action="store_true")
    options = parser.parse_args(args[1:])
    root = options.root.resolve()
    json_mode = getattr(options, "json", False)
    emit = lambda row: _emit(row, root, json_mode, write)
    pair = None
    if args[0] != "status":
        values = (options.historical_assessment, options.historical_assessment_sha)
        if (values[0] is None) != (values[1] is None):
            emit({"action": "preflight", "ok": False, "detail":
                  "--historical-assessment and --historical-assessment-sha must be given together"})
            raise SystemExit(2)
        pair = values if values[0] is not None else None
    code = 0
    try:
        if args[0] == "preflight":
            errors = loop.preflight(root, historical_assessment=pair)
            for detail in errors:
                emit({"action": "preflight", "ok": False, "detail": detail})
            if not errors:
                emit({"action": "preflight", "ok": True})
            code = 2 if errors else 0
        elif args[0] == "status":
            _status(root, options.usage, json_mode, write)
        else:
            reason = loop.run(root, until=options.until, once=options.once, emit=emit,
                              historical_assessment=pair)
            if reason == loop.StopReason.preflight_failed:
                code = 2
            elif reason == loop.StopReason.internal:
                code = 1
            elif reason not in (None, loop.StopReason.done, loop.StopReason.boundary):
                code = 3
    except Exception as exc:  # noqa: BLE001 - CLI errors have a stable exit and diagnostic.
        emit({"action": "internal", "detail": f"{type(exc).__name__}: {exc}; owner must inspect"})
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
