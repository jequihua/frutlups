"""The three autonomous CLI verbs and their text/JSON presentation."""

import argparse
import json
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
        return f"{scope} usage " + " ".join(
            f"{key}={row[key] if row[key] is not None else '?'}"
            for key in ("secs", "tokens_in", "tokens_out", "cost_usd")
        )
    return f"{prefix} {action}"


def _emit(row, root, json_mode):
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
    print(json.dumps(row, ensure_ascii=True) if json_mode else " ".join(_text(row).splitlines()))


def _usage(root, model, events):
    keys = ("secs", "tokens_in", "tokens_out", "cost_usd")
    values = {s.id: {key: [] for key in keys} for m in model.milestones for s in m.slices}
    prompts, coded = {}, {}
    for event in events:
        if event.ev == ledger.Ev.prompt:
            prompts[event.slice] = event.data["sha"]
        if event.ev in (ledger.Ev.coded, ledger.Ev.reviewed):
            for key in keys:
                if key in event.data:
                    values[event.slice][key].append(event.data[key])
            if event.ev == ledger.Ev.coded and event.slice in prompts:
                coded[prompts[event.slice]] = event.slice
    jobs = root / "local_state" / "frutlups" / "jobs"
    # One level only; no recursive scan of job streams or run state.
    for directory in jobs.iterdir() if jobs.is_dir() else ():
        path = directory / "result.json"
        if directory.is_symlink() or path.is_symlink() or not path.is_file():
            continue
        result = json.loads(path.read_text(encoding="utf-8"))
        sid = coded.get(result.get("prompt_sha"))
        cost = result.get("cost_usd")
        if sid and result.get("role") == "coder" and cost is not None:
            values[sid]["cost_usd"].append(cost)
    for milestone in model.milestones:
        for item in milestone.slices:
            yield {
                "action": "usage",
                "slice": item.id,
                **{
                    key: sum(numbers) if numbers else None
                    for key, numbers in values[item.id].items()
                },
            }
        totals = {}
        for key in keys:
            numbers = [n for s in milestone.slices for n in values[s.id][key]]
            totals[key] = sum(numbers) if numbers else None
        yield {"action": "usage", "milestone": milestone.id, **totals}


def _status(root, usage, json_mode):
    cfg = Config.load(root)
    model = roadmap.load(root / "roadmap.yaml")
    events = ledger.read(root / cfg.ledger)
    state = ledger.fold(events, model)
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
                )
        following = roadmap.next_slice(model, state)
        _emit({"action": "next", "slice": following.id if following else None}, root, True)
    else:
        print(ledger.status_text(model, state))
    if usage:
        for row in _usage(root, model, events):
            _emit(row, root, json_mode)


def main(argv: Sequence[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in VERBS:
        print(USAGE)
        raise SystemExit(2)
    parser = argparse.ArgumentParser(prog=f"frutlups {args[0]}")
    parser.add_argument("root", nargs="?", default=".", type=Path)
    if args[0] == "run":
        parser.add_argument("--until", choices=("slice", "milestone", "roadmap"))
        parser.add_argument("--once", action="store_true")
    if args[0] == "status":
        parser.add_argument("--usage", action="store_true")
    if args[0] != "preflight":
        parser.add_argument("--json", action="store_true")
    options = parser.parse_args(args[1:])
    root = options.root.resolve()
    json_mode = getattr(options, "json", False)
    emit = lambda row: _emit(row, root, json_mode)
    code = 0
    try:
        if args[0] == "preflight":
            errors = loop.preflight(root)
            for detail in errors:
                emit({"action": "preflight", "ok": False, "detail": detail})
            if not errors:
                emit({"action": "preflight", "ok": True})
            code = 2 if errors else 0
        elif args[0] == "status":
            _status(root, options.usage, json_mode)
        else:
            reason = loop.run(root, until=options.until, once=options.once, emit=emit)
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
