"""Pure autonomy/1 grammar and fold, adapted from template 169a74c. No writers."""

from __future__ import annotations

import math

SCHEMA = "frutlups.autonomy/1"
EVENTS = {"run_admitted", "run_extended"}
UNITS = ("jobs", "transport_retries", "format_retries", "seconds")
FIELDS = {
    "run_admitted": {
        "run",
        "milestone",
        "slices",
        "stop",
        "allowance",
        "max_corrective_rounds",
        "grantors",
        "previous",
        "reason",
        "authority",
    },
    "run_extended": {"run", "milestone", "revision", "allowance", "reason", "authority"},
}


def validate(event):
    from . import ledger as p

    ev = event["ev"]
    p._need(set(event) - {"schema", "t", "ev", "by"} == FIELDS[ev], f"invalid {ev} fields")
    p._need(event["by"] in ("human", "architect"), "run grant requires human or architect")
    p._need(p.identifier(event["run"]), "invalid run identity")
    p._need(p.scope(event["milestone"]) and "-S" not in event["milestone"], "invalid run milestone")
    p._need(isinstance(event["reason"], str) and bool(event["reason"].strip()), "run needs reason")
    p.references(event["authority"])
    p._need(bool(event["authority"]), "run needs bound authority")
    p._need(
        len({(ref["path"], ref["sha"]) for ref in event["authority"]}) == len(event["authority"]),
        "duplicate run authority reference",
    )
    allowance = event["allowance"]
    p._need(isinstance(allowance, dict) and set(allowance) == set(UNITS), "invalid run allowance")
    p._need(all(type(x) is int and x >= 0 for x in allowance.values()), "invalid allowance units")
    if ev == "run_extended":
        p._need(type(event["revision"]) is int and event["revision"] > 0, "invalid run revision")
        p._need(any(allowance.values()), "extension needs positive allowance")
        return event
    p._need(allowance["jobs"] > 0 and allowance["seconds"] > 0, "admission needs jobs and seconds")
    selected = event["slices"]
    p._need(
        isinstance(selected, list)
        and bool(selected)
        and all(p.scope(x) and "-S" in x for x in selected)
        and len(set(selected)) == len(selected),
        "invalid run slices",
    )
    p._need(p.scope(event["stop"]), "invalid run stop")
    p._need(
        type(event["max_corrective_rounds"]) is int and event["max_corrective_rounds"] >= 0,
        "invalid corrective ceiling",
    )
    grantors = event["grantors"]
    p._need(
        isinstance(grantors, list)
        and all(x in ("human", "architect") for x in grantors)
        and "human" in grantors
        and len(set(grantors)) == len(grantors),
        "grantors must contain human and optionally architect",
    )
    previous = event["previous"]
    p._need(
        previous is None
        or isinstance(previous, dict)
        and set(previous) == {"run", "revision"}
        and p.identifier(previous["run"])
        and type(previous["revision"]) is int
        and previous["revision"] >= 0,
        "invalid previous run head",
    )
    return event


def _remaining(run):
    run["remaining"] = {key: run["allowance"][key] - run["consumed"][key] for key in UNITS}


def _grant(event, rm, states, done, extras):
    from . import ledger as p

    runs, head = extras["runs"], extras["run_head"]
    authority = frozenset((ref["path"], ref["sha"]) for ref in event["authority"])
    grants = extras.setdefault("run_authorities", set())
    p._need(authority not in grants, "run authority already granted")
    p._need(
        not any(x.get("finish") is None for x in extras["attempts"].values()),
        "unresolved invocation prevents run grant",
    )
    if event["ev"] == "run_admitted":
        p._need(event["run"] not in runs, "duplicate run identity")
        p._need(event["previous"] == head, "previous run head mismatch")
        milestone = next((x for x in rm["milestones"] if x["id"] == event["milestone"]), None)
        p._need(milestone is not None, "unknown run milestone")
        p._need(milestone["status"] != "planned", "run admission requires active milestone")
        selected = event["slices"]
        p._need(
            [x["id"] for x in milestone["slices"] if x["id"] in selected] == selected,
            "run slices must belong to milestone in roadmap order",
        )
        stop = event["stop"]
        p._need(stop in (selected[-1], milestone["id"]), "run stop must be final slice or milestone")
        if stop == milestone["id"]:
            p._need(milestone["holistic_review"] and stop not in done, "invalid or reached run stop")
        else:
            p._need(states[stop]["step"] != "accepted", "run stop already reached")
        if head and runs[head["run"]]["closed"] is None:
            runs[head["run"]]["closed"] = "superseded"
        run = {
            "admission": event,
            "revision": 0,
            "allowance": dict(event["allowance"]),
            "consumed": dict.fromkeys(UNITS, 0),
            "closed": None,
            "initial_contexts": set(),
            "roots": {},
            "chain_retries": {},
        }
        runs[event["run"]] = run
    else:
        p._need(head is not None and head["run"] == event["run"], "extension needs current run")
        run = runs[head["run"]]
        p._need(run["closed"] is None, "cannot extend closed run")
        p._need(event["milestone"] == run["admission"]["milestone"], "extension milestone mismatch")
        p._need(event["revision"] == run["revision"] + 1, "extension revision mismatch")
        p._need(event["by"] in run["admission"]["grantors"], "extension actor is not delegated")
        run["revision"] = event["revision"]
        for key in UNITS:
            run["allowance"][key] += event["allowance"][key]
    grants.add(authority)
    extras["run_head"] = {"run": event["run"], "revision": run["revision"]}
    _remaining(run)


def _start(event, rm, states, extras):
    from . import ledger as p

    head = extras["run_head"]
    p._need(
        head is not None,
        "profile attempts require preceding admission; historical cutover unsupported",
    )
    run = extras["runs"][head["run"]]
    admission = run["admission"]
    p._need(run["closed"] is None, "run is closed")
    p._need(
        event["scope"] in admission["slices"] or event["scope"] == admission["milestone"],
        "attempt outside admitted selection",
    )
    p._need(
        event["role"] != "holistic" or admission["stop"] == admission["milestone"],
        "holistic attempt exceeds slice stop",
    )
    current = states.get(event["scope"])
    p._need(current is None or current["step"] != "blocked", "slice blocker needs resolution")
    p._need(
        extras["holistic"].get(admission["milestone"], {}).get("step") != "blocked",
        "holistic blocker needs resolution",
    )
    if event["role"] == "verification":
        p._need(
            current is not None
            and current["step"] == "verifying"
            and current["round"] == event["round"]
            and event["retry"] == "initial"
            and "parent" not in event,
            "verification requires current verifying round and no retry",
        )
    if event["role"] == "reviewer":
        p._need(
            current is not None
            and current["step"] == "reviewing"
            and current["round"] == event["round"],
            "reviewer requires current reviewing round",
        )
    if event["role"] == "holistic":
        milestone = next(x for x in rm["milestones"] if x["id"] == admission["milestone"])
        p._need(
            event["scope"] == milestone["id"]
            and event["round"] == "holistic"
            and milestone["holistic_review"]
            and all(states[x["id"]]["step"] == "accepted" for x in milestone["slices"])
            and extras["holistic"].get(milestone["id"], {}).get("step")
            not in ("blocked", "close_pending", "done"),
            "holistic requires accepted milestone and no pending decision",
        )
    if event["role"] == "coder":
        p._need(
            current is not None
            and current["step"] == "coding"
            and current["round"] == event["round"],
            "coder requires current issued coding round",
        )
        p._need(
            current is not None
            and current["corrective_rounds_used"] <= admission["max_corrective_rounds"],
            "pinned corrective ceiling exhausted",
        )
    invocation = event["invocation"]
    parent = event.get("parent")
    if parent:
        p._need(
            extras["run_membership"].get(parent) == head["run"],
            "retry parent belongs to another run",
        )
        root = run["roots"][parent]
        p._need(
            extras["attempts"][root]["start"]["role"] != "verification",
            "verification cannot be retried",
        )
        p._need(event["retry"] not in run["chain_retries"][root], "chain retry allowance consumed")
    else:
        root = invocation
        context = tuple(event[key] for key in ("scope", "round", "role", "contract"))
        p._need(
            event["role"] == "verification" or context not in run["initial_contexts"],
            "initial model context already attempted",
        )
    charges = {
        "jobs": int(event["role"] != "verification"),
        "transport_retries": int(event["retry"] == "transport"),
        "format_retries": int(event["retry"] == "format"),
        "seconds": event["allowance_seconds"],
    }
    for key in UNITS:
        p._need(charges[key] <= run["remaining"][key], f"run {key} allowance exhausted")
    if parent:
        run["chain_retries"][root].add(event["retry"])
    else:
        run["initial_contexts"].add(context)
        run["chain_retries"][root] = set()
    run["roots"][invocation] = root
    extras["run_membership"][invocation] = head["run"]
    for key in UNITS:
        run["consumed"][key] += charges[key]
    _remaining(run)


def apply(event, rm, states, done, extras):
    """Fold profile effects before ordinary attempt/lifecycle state changes."""
    from . import ledger as p

    ev = event["ev"]
    enabled = rm.get("autonomy") == {"schema": SCHEMA}
    p._need(
        enabled
        or ev not in EVENTS
        and not (ev == "attempt_started" and event.get("role") == "verification"),
        "autonomy history requires declared frutlups.autonomy/1 profile",
    )
    if not enabled:
        return False
    extras.setdefault("runs", {})
    extras.setdefault("run_head", None)
    extras.setdefault("run_membership", {})
    if ev in EVENTS:
        p._need(event.get("schema") == p.SCHEMA_V2, "run grants require /2 ledger")
        _grant(event, rm, states, done, extras)
        return True
    if ev == "attempt_started":
        _start(event, rm, states, extras)
    elif ev == "attempt_finished":
        owner = extras["run_membership"].get(event["invocation"])
        p._need(owner is not None, "completion has no admitted run")
        run = extras["runs"][owner]
        reservation = extras["attempts"][event["invocation"]]["start"]["allowance_seconds"]
        secs = event["usage"].get("secs")
        measured = math.ceil(secs) if secs is not None else reservation
        charge = measured if event["status"] == "completed" else max(reservation, measured)
        run["consumed"]["seconds"] += charge - reservation
        if measured > reservation:
            run["closed"] = "overrun"
        _remaining(run)
    elif extras["run_head"]:
        run = extras["runs"][extras["run_head"]["run"]]
        stop = run["admission"]["stop"]
        if run["closed"] is None and (
            ev == "accepted"
            and event["slice"] == stop
            or ev == "milestone_done"
            and event["milestone"] == stop
        ):
            run["closed"] = "boundary"
    return False


def validate_history(rm, states, done, extras):
    """A done roadmap label may replay completed history, never grant new work."""
    from . import ledger as p

    runs = extras.get("runs", {})
    admitted = {x["admission"]["milestone"] for x in runs.values()}
    unsettled = {
        runs[owner]["admission"]["milestone"]
        for invocation, owner in extras.get("run_membership", {}).items()
        if extras["attempts"][invocation]["finish"] is None
    }
    for milestone in rm["milestones"]:
        mid = milestone["id"]
        if mid in admitted and milestone["status"] != "active":
            p._need(
                milestone["status"] == "done"
                and mid not in unsettled
                and all(states[x["id"]]["step"] == "accepted" for x in milestone["slices"])
                and (not milestone["holistic_review"] or mid in done),
                "inactive run history requires completed milestone and settled attempts",
            )

