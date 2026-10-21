"""Ledger-backed workspace admission and review evidence for the loop."""

import codecs
import hashlib
import json
import logging
import math
import os
import re
import stat
from collections import Counter

from . import gitws, ledger, receipt, render, verdict
from ._paths import repo_path


def changes(root, cfg):
    return tuple(
        {"path": row.path, "sha": row.sha256, "kind": row.kind}
        for row in gitws.changed_files(root, executable=cfg.git)
        if not row.path.startswith("local_state/")
    )


def evidence_paths(events, root=None):
    paths = set()
    if any(event.schema == ledger.SCHEMA_V2 for event in events):
        paths.add("05_governance/backlog.md")
    for event in events:
        for key in ("path", "receipt", "report", "notes_path"):
            if event.data.get(key):
                paths.add(event.data[key])
        row = ledger._event_dict(event)
        refs = row.get("authority", []) + row.get("evidence", [])
        refs += [row[key] for key in ("envelope", "manifest", "outcome") if key in row]
        if "commit_intent" in row:
            refs.append(row["commit_intent"]["manifest"])
        if root is not None and "outcome" in row:
            refs += ledger.outcome(root, row["outcome"], event.slice, event.round)["evidence"]
        paths.update(ref["path"] for ref in refs)
    return paths


def workspace_errors(root, cfg, events):
    """Admit only immutable evidence and exact recorded product/baseline identities."""
    errors = [str(error) for error in ledger.check(root / cfg.ledger, root, executable=cfg.git)]
    products = {}
    for event in events:
        for key in ("baseline", "changed"):
            products.update({row["path"]: dict(row) for row in event.data.get(key, ())})
    from .roadmap import load
    ledger.validate_history(root, events, load(root / "roadmap.yaml"), executable=cfg.git)
    artifacts = evidence_paths(events, root) | {cfg.ledger}
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


def immutable_text(root, path, text, limit):
    from .commit import repo_path as regular_path
    raw = text.encode("utf-8", "strict")
    if len(raw) > limit:
        raise ValueError("immutable coding artifact exceeds byte bound")
    target = regular_path(root, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return {"path": path, "sha": ledger.evidence_sha(raw)}


def bound_text(root, ref, limit):
    path = ledger._regular_reference(root, ref)
    identity = ledger._file_key(path)
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    ledger._need(len(raw) <= limit and ledger.evidence_sha(raw) == ref["sha"]
                 and ledger._file_key(path) == identity, "bounded text evidence drift: " + ref["path"])
    return raw.decode("utf-8", "strict")


def review_receipt(root, cfg, events, current, frozen, value, *, historical=False,
                   historical_context=None, accepted_index=None):
    """Admit the active pinned /2 receipt without enabling a verifier producer."""
    from ._attempts import _digest
    fields = {"schema", "slice", "round", "t", "base_commit", "tree_dirty_before", "commands",
              "tree_clean_after", "ok", "manifest", "witness", "runtime", "observation"}
    ledger._need(set(value) == fields and value["schema"] == "frutlups.receipt/2"
                 and value["slice"] == current.id and type(value["round"]) is int
                 and value["round"] == current.round and value["ok"] is True
                 and value["manifest"] == ledger._thaw(current.manifest)
                 and value["observation"] == frozen["observation"]
                 and all(type(value[k]) is bool for k in ("tree_dirty_before", "tree_clean_after"))
                 and isinstance(value["t"], str)
                 and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value["t"]),
                 "invalid successful review receipt")
    witness, commands, runtime = value["witness"], value["commands"], value["runtime"]
    ledger._need(isinstance(witness, dict) and set(witness) == {"before", "after", "stable", "head", "index", "product"}
                 and all(ledger.digest(witness[k]) for k in ("before", "after", "index", "product"))
                 and witness["stable"] is True and witness["before"] == witness["after"]
                 and witness["head"] == value["base_commit"], "invalid successful receipt witness")
    ledger._need(isinstance(commands, list) and len(commands) == 1, "receipt needs one full command")
    command = commands[0]
    ledger._need(isinstance(command, dict) and set(command) == {
        "label", "argv", "exit", "secs", "stdout_tail", "stderr_tail", "timed_out"}
        and command["label"] == "full" and command["argv"] == list(receipt._public_argv(frozen["full"], root))
        and type(command["exit"]) is int and command["exit"] == 0 and command["timed_out"] is False
        and type(command["secs"]) in (int, float) and math.isfinite(command["secs"]) and command["secs"] >= 0
        and all(isinstance(command[k], str) and len(command[k].encode("utf-8")) <= 4096
                for k in ("stdout_tail", "stderr_tail")), "receipt command differs from successful frozen verification")
    ledger._need(isinstance(runtime, dict) and set(runtime) == {"python", "implementation", "source", "sha"}
                 and isinstance(runtime["python"], str) and re.fullmatch(r"3\.\d+\.\d+[A-Za-z0-9.+-]*", runtime["python"])
                 and isinstance(runtime["implementation"], str) and re.fullmatch(r"[A-Za-z0-9_. -]{1,80}", runtime["implementation"])
                 and runtime["source"] in ("invoking_python", "project.local")
                 and runtime["sha"] == _digest(dict(runtime={k:v for k,v in runtime.items() if k != "sha"},
                                                   declaration=frozen["runtime"])), "invalid receipt runtime")
    version = frozen["runtime"].get("python")
    ledger._need(not version or runtime["python"] == version or runtime["python"].startswith(version + "."),
                 "receipt runtime differs from frozen envelope")
    if historical:
        cap = (historical_context.cap_for(root, events, accepted_index)
               if historical_context is not None else None)
        ledger._need(command["secs"] <= (cap if cap is not None else frozen["timeout_seconds"]),
                     "accepted receipt exceeded frozen deadline")
        return
    present = gitws.workspace_witness(root, executable=cfg.git)
    index = gitws._git(root, "ls-files", "--stage", "-v", "-z", executable=cfg.git).stdout
    ledger._need(witness["head"] == present["head"] and witness["index"] == hashlib.sha256(index).hexdigest(),
                 "HEAD/index changed after verification")
    excluded = evidence_paths(events, root) | {cfg.ledger, "05_governance/backlog.md", "roadmap.yaml", "docs/roadmap.md"}
    excluded -= {r["path"] for e in events if e.ev == ledger.Ev.coded for r in e.data["changed"]}
    files = {}
    for path, identity in present["files"].items():
        if path in excluded:
            continue
        ledger._need(identity[0] in ("file", "missing"), "review product must contain regular files")
        files[path] = (dict(kind="file", mode=stat.S_IMODE(identity[2][2]), sha=identity[3])
                       if identity[0] == "file" else dict(kind="missing"))
    ledger._need(witness["product"] == _digest(files), "raw product changed after verification")


def retain_review_text(root, path, text, limit):
    """Recover an artifact only when its complete expected bytes are already proven."""
    ref = dict(path=path, sha=ledger.evidence_sha(text.encode("utf-8", "strict")))
    if repo_path(root, path).exists():
        ledger._need(bound_text(root, ref, limit) == text, "review artifact collision")
        return ref
    return immutable_text(root, path, text, limit)


def text_artifact(path, text, limit):
    raw = text.encode("utf-8", "strict")
    ledger._need(len(raw) <= limit, "evidence page/support exceeds byte bound")
    return (dict(path=path, sha=ledger.evidence_sha(raw)), text, limit)


def evidence_package(root, cfg, stem, sources, base):
    """Pure bounded presentation plan; manifests own identities and row provenance.

    Every source row appears in a page, including earlier/deleted rows. Excerpts
    are explicitly incomplete. Added files share a per-file allowance so the
    first large body cannot suppress later small additions.
    """
    stem = stem.rsplit("/", 1)[0] + "/evidence_" + ledger.evidence_sha(stem.encode("utf-8"))[:16]
    rows = []
    for number, source in enumerate(sources):
        manifest = ledger.manifest(root, source)
        rows.extend(dict(row=row, source=number, source_row=i)
                    for i, row in enumerate(manifest["changed"]))
    artifacts, pages, index = [], [], []
    indexed = set(gitws._git(root, "ls-files", "-z", executable=cfg.git).stdout.split(b"\0"))
    for offset in range(0, len(rows), 32):
        group = rows[offset:offset + 32]
        selected = list({entry["row"]["path"]: entry["row"] for entry in group}.values())
        tracked = [row["path"] for row in selected if row["path"].encode("utf-8") in indexed
                   or row["kind"] == "deleted"]
        patch = gitws.bounded_diff(root, base, tracked, 8192, executable=cfg.git) if tracked else ""
        body = ("Bounded excerpts, NOT the complete patch. Exact source rows and stored identities below. "
                "Read canonical manifests for original provenance; inspect each named path locally. "
                "Deleted-path hashes are stored identities, not hashes of absent files.\n"
                + json.dumps(group, ensure_ascii=False, sort_keys=True) + "\n"
                + render.HASH_EXPLANATION + "\nTracked diff excerpt against " + base + ":\n" + patch)
        for row in selected:
            path = row["path"]
            if row["kind"] == "deleted" or path.encode("utf-8") in indexed:
                continue
            if not repo_path(root, path).exists():
                body += "\nHistorical path now absent; stored source identity retained: " + path + "\n"
                continue
            target = ledger._regular_reference(root, dict(path=path, sha=row["sha"]))
            with target.open("rb") as stream:
                raw = stream.read(257)
            excerpt = raw[:256].decode("utf-8", "replace")
            if b"\0" in raw:
                excerpt = "[Binary excerpt omitted.]"
            body += ("\nCurrent file excerpt: " + path + "\n" + excerpt
                     + "\n[Bounded current-file excerpt; canonical manifest retains the exact stored identity.]\n")
        digest = ledger.evidence_sha(body.encode("utf-8"))
        ref, body, limit = text_artifact(f"{stem}_page_{offset // 32:04}_{digest[:16]}.md", body, render.DIFF_LIMIT)
        artifacts.append((ref, body, limit))
        pages.append(ref)
        index.extend(dict(path=entry["row"]["path"], page=len(pages)-1,
                          source=entry["source"], source_row=entry["source_row"], page_row=i) for i, entry in enumerate(group))
    text = json.dumps(dict(sources=sources, pages=pages, paths=index,
        notice="Complete path index; pages are bounded excerpts, not the complete patch. "
               "Each page retains original source rows, stored hashes, kinds and rounds."),
        ensure_ascii=False, sort_keys=True)
    digest = ledger.evidence_sha(text.encode("utf-8"))
    artifact = text_artifact(f"{stem}_{digest[:16]}_index.json", text, render.SUPPORT_LIMIT)
    artifacts.append(artifact)
    return artifact[0], artifacts


def holistic_cycle(events, mid):
    """Authority transitions distinguish new cycles even when product bytes match."""
    return [ledger._event_dict(e) for e in events
            if (e.ev in (ledger.Ev.accepted, ledger.Ev.reopened) and e.slice.startswith(mid + "-S"))
            or (e.ev == ledger.Ev.holistic_reviewed and e.milestone == mid)
            or (e.ev == ledger.Ev.resolved and (e.data["scope"] == mid
                or e.data["scope"].startswith(mid + "-S")))
            or e.ev == ledger.Ev.commit_cancelled]


def accepted_chain(events, index):
    """The latest exact-scope chain at this acceptance, shared with selection."""
    approval = events[index]
    ledger._need(approval.ev == ledger.Ev.accepted, "historical target is not accepted")
    return tuple(next(i for i in range(index - 1, -1, -1)
                      if events[i].ev == kind and events[i].slice == approval.slice
                      and events[i].round == approval.round)
                 for kind in (ledger.Ev.prompt, ledger.Ev.coded, ledger.Ev.verified, ledger.Ev.reviewed)) + (index,)


def accepted_evidence(root, cfg, events, index, *, historical_context=None):
    """Read one exact accepted occurrence without weakening native integrity."""
    approval = events[index]
    issued, coded, verified, reviewed, _ = (events[i] for i in accepted_chain(events, index))
    envelope_ref = ledger._thaw(issued.data["envelope"])
    frozen = ledger.envelope(root, envelope_ref, approval.slice, approval.round)
    manifest_ref = ledger._thaw(coded.data["manifest"])
    ledger.manifest(root, manifest_ref)
    receipt_ref = dict(path=verified.data["receipt"], sha=verified.data["sha"])
    value = ledger.read_reference(root, receipt_ref, 128 * 1024)
    current = ledger.SliceState(approval.slice, round=approval.round, manifest=manifest_ref)
    review_receipt(root, cfg, events, current, frozen, value, historical=True,
                   historical_context=historical_context, accepted_index=index)
    report_ref = dict(path=reviewed.data["report"], sha=reviewed.data["sha"])
    parsed = verdict.parse(bound_text(root, report_ref, 1024 * 1024))
    ledger._need(parsed.identity == approval.slice and parsed.round == approval.round
                 and parsed.verdict == reviewed.data["verdict"]
                 and list(verdict.open_blocking(parsed)) == list(reviewed.data["open"]),
                 "accepted review identity/verdict mismatch")
    return dict(envelope=envelope_ref, contract=frozen, manifest=manifest_ref,
                receipt=receipt_ref, report=report_ref)


def holistic_support(root, cfg, model, events, milestone, state, *, historical_context=None):
    """Freeze every accepted contract and canonical reference, never live acceptance."""
    if historical_context is not None:
        historical_context.recheck(root, cfg, events)
    ids = {s.id for s in milestone.slices}
    ledger._need(all(state.slices[sid].step == "accepted" for sid in ids), "holistic requires accepted slices")
    accepted = []
    for index, approval in enumerate(events):
        if approval.ev != ledger.Ev.accepted or approval.slice not in ids:
            continue
        accepted.append(accepted_evidence(root, cfg, events, index,
                                          historical_context=historical_context))
    paths = {row["path"] for sid in ids for row in ledger.manifest(
        root, ledger._thaw(state.slices[sid].manifest))["changed"]}
    latest = {row["path"]: dict(row) for e in events if e.ev == ledger.Ev.coded
              for row in e.data["changed"] if row["path"] in paths}
    for relative, row in latest.items():
        target = repo_path(root, relative)
        if row["kind"] == "deleted":
            ledger._need(not target.exists() and not target.is_symlink(), "holistic product drift")
        else:
            ledger._check_reference(root, dict(path=relative, sha=row["sha"]))
    return dict(milestone=milestone.id, accepted=accepted, latest=[latest[p] for p in sorted(latest)],
                cycle=holistic_cycle(events, milestone.id))


def parse_outcome(root, text, sid, round_no, invocation):
    if not isinstance(text, str) or len(text.encode("utf-8", "strict")) > 16 * 1024:
        raise ValueError("coder outcome exceeds 16 KiB or is not UTF-8 text")
    # Exactly one complete JSON fence; no prose search, retry, or field repair.
    match = re.fullmatch(r"\s*```json[ \t]*\r?\n(.*?)\r?\n```\s*", text, re.DOTALL)
    payload = match[1] if match else text
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("ambiguous duplicate outcome field: " + key)
            value[key] = item
        return value
    value = json.loads(payload, object_pairs_hook=unique,
                       parse_constant=lambda token: (_ for _ in ()).throw(ValueError("invalid JSON constant")))
    if len(json.dumps(value, ensure_ascii=False).encode("utf-8", "strict")) > 16 * 1024:
        raise ValueError("canonical outcome exceeds 16 KiB")
    return ledger.validate_outcome(root, value, sid, round_no, invocation=invocation)


def no_progress(root, events, model, sid, *, executable="git"):
    """Only complete, bound same-slice failure proofs can establish equality."""
    ledger.validate_history(root, events, model, executable=executable)
    verified = [e for e in events if e.ev == ledger.Ev.verified and e.slice == sid]
    if len(verified) < 2 or any(e.data["ok"] for e in verified[-2:]):
        return False
    previous, latest = verified[-2:]
    if latest.round != previous.round + 1:
        return False
    keys = []
    for event in (previous, latest):
        issued = next((e for e in events if e.ev == ledger.Ev.prompt
                       and e.slice == sid and e.round == event.round), None)
        if issued is None or "envelope" not in issued.data:
            return False
        envelope = ledger.envelope(root, ledger._thaw(issued.data["envelope"]), sid, event.round)
        value = ledger.read_reference(root, {"path": event.data["receipt"], "sha": event.data["sha"]}, 128 * 1024)
        fields = {"schema", "slice", "round", "t", "base_commit", "tree_dirty_before", "commands",
                  "tree_clean_after", "ok", "manifest", "witness", "runtime", "observation"}
        if not fields <= set(value):
            return False
        ledger._need(set(value) == fields and type(value["round"]) is int
                     and all(type(value[k]) is bool for k in ("tree_dirty_before", "tree_clean_after", "ok"))
                     and isinstance(value["t"], str)
                     and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value["t"]),
                     "invalid comparable receipt fields")
        witness, commands = value.get("witness"), value.get("commands")
        if not isinstance(witness, dict) or not isinstance(commands, list) or len(commands) != 1:
            return False
        if set(witness) != {"before", "after", "stable", "head", "index", "product"}:
            return False
        ledger._need(all(ledger.digest(witness[k]) for k in ("before", "after", "index", "product"))
                     and type(witness["stable"]) is bool
                     and witness["stable"] == (witness["before"] == witness["after"])
                     and isinstance(witness["head"], str) and re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", witness["head"])
                     and witness["head"] == value.get("base_commit"), "invalid no-progress witness")
        command = commands[0]
        if not isinstance(command, dict) or set(command) != {
                "label", "argv", "exit", "secs", "stdout_tail", "stderr_tail", "timed_out"}:
            return False
        # Canonical receipts retain the frozen portable argv. Missing runtime or
        # truncated diagnostics cannot prove identical bound failure content.
        ledger._need(command["label"] == "full" and command["argv"] == list(receipt._public_argv(envelope["full"], root)),
                     "no-progress command differs from frozen envelope")
        ledger._need(type(command["timed_out"]) is bool and
                     (command["exit"] is None or type(command["exit"]) is int), "invalid failure status")
        ledger._need(all(isinstance(command[k], str) for k in ("stdout_tail", "stderr_tail")),
                     "invalid failure diagnostics")
        ledger._need(type(command["secs"]) in (int, float) and math.isfinite(command["secs"])
                     and command["secs"] >= 0 and all(len(command[k].encode("utf-8")) <= 4096
                     for k in ("stdout_tail", "stderr_tail")), "invalid bounded failure evidence")
        ledger._need(value["ok"] == (command["exit"] == 0 and not command["timed_out"] and witness["stable"]),
                     "receipt failure contradicts command or witness")
        if not witness["stable"] or command["exit"] == 0 and not command["timed_out"]:
            return False
        if any("[Earlier diagnostic output omitted.]" in command[k] for k in ("stdout_tail", "stderr_tail")):
            return False
        if not isinstance(value.get("runtime"), dict) or value.get("observation") != envelope["observation"]:
            return False
        runtime = value["runtime"]
        if set(runtime) != {"python", "implementation", "source", "sha"}:
            return False
        declaration = envelope["runtime"]
        identity = {k: v for k, v in runtime.items() if k != "sha"}
        expected = hashlib.sha256(json.dumps(dict(runtime=identity, declaration=declaration),
                                  ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        ledger._need(runtime["sha"] == expected and isinstance(runtime["python"], str)
                     and re.fullmatch(r"3\.\d+\.\d+[A-Za-z0-9.+-]*", runtime["python"])
                     and isinstance(runtime["implementation"], str)
                     and runtime["source"] in ("invoking_python", "project.local"), "invalid failure runtime identity")
        version = declaration.get("python")
        ledger._need(not version or runtime["python"] == version or runtime["python"].startswith(version + "."),
                     "failure runtime differs from frozen envelope")
        keys.append((witness["product"], envelope["full"], envelope["runtime"],
                     value["runtime"], value["observation"],
                     *(command[k] for k in ("argv", "exit", "timed_out", "stdout_tail", "stderr_tail"))))
    return keys[0] == keys[1]


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


def _diff(root, cfg, base, changed, limit=render.DIFF_LIMIT):
    """Bound Git argv and file reads before rendering the evidence window."""
    paths = [row["path"] for row in changed]
    if not paths:
        return "(no product changes)"
    notice = "\n[Added-file evidence omitted or truncated; inspect the complete manifest locally.]"
    heading = f"Diff against {base}, filtered to selected paths (batched, not a prior-round delta).\n"
    remaining = limit - len(heading.encode()) - len(notice.encode())
    if remaining < len(gitws.DIFF_NOTICE.encode()):
        return "[Evidence omitted by the bounded evidence gate; inspect locally.]"[:limit]
    patch = gitws.bounded_diff(root, base, paths, remaining, executable=cfg.git)
    blocks = [heading, patch]
    remaining -= len(patch.encode("utf-8"))
    indexed = set(gitws._git(root, "ls-files", "-z", executable=cfg.git).stdout.split(b"\0"))
    for row in changed:
        if row["kind"] != "added" or row["path"].encode("utf-8") in indexed:
            continue
        path = row["path"]
        label = f"\nAdded file: {path}\n"
        room = remaining - len(label.encode("utf-8"))
        if room <= 0:
            blocks.append(notice)
            break
        target = repo_path(root, path)
        if target.is_symlink() or not target.is_file():
            raise ValueError("not a regular added file: " + path)
        with target.open("rb") as stream:
            raw = stream.read(room + 1)
        truncated = len(raw) > room
        try:
            body = codecs.getincrementaldecoder("utf-8")().decode(raw[:room], final=not truncated)
            if "\0" in body:
                body = "[Binary file; inspect locally.]"
        except UnicodeError:
            body = "[Not UTF-8 text; inspect locally.]"
        body = body.encode("utf-8")[:room].decode("utf-8", "ignore")
        blocks.append(label + body)
        remaining -= len((label + body).encode("utf-8"))
        if truncated:
            blocks.append(notice)
            break
    return "".join(blocks)


def prior_finding_ids(root, state, *, native=False):
    ids = set(re.findall(rf"\b{re.escape(state.id)}-[^\s,]+", state.reopen_reason or ""))
    if state.last_report:
        text = repo_path(root, state.last_report).read_text(encoding="utf-8")
        ids.update(f.id for f in verdict.parse(text).findings)
    return ids


def combined_review(reviews, *, seat_names=(), prior_ids=(), native=False):
    """Suffix shared new ids for named seats; merge prior or unnamed ids by strictness."""
    if seat_names and len(seat_names) != len(reviews):
        raise ValueError("each routed review must have a seat name")
    if len(reviews) == 1:
        return reviews[0][1]
    counts = Counter(f.id for review, _ in reviews for f in review.findings)
    collisions = {id for id, count in counts.items() if count > 1} - set(prior_ids)
    updates = {}
    for review, _ in reviews:
        for update in review.updates:
            key = update.source, update.sha, update.id
            if key in updates:
                previous = updates[key]
                if previous.disposition != update.disposition:
                    raise ValueError("contradictory finding updates across seats")
                update = verdict.FindingUpdate(*key, update.disposition,
                    tuple(sorted(set(previous.related) | set(update.related))))
            if seat_names and set(update.related) & collisions:
                raise ValueError("ambiguous related finding after merge renaming")
            updates[key] = update
    update_text = ""
    if updates:
        update_text = ("\n\n## Finding updates\n\n"
            "| source | sha | id | disposition | related |\n| --- | --- | --- | --- | --- |\n")
        for update in updates.values():
            cells = (update.source, update.sha, update.id, update.disposition,
                     ", ".join(update.related) or "-")
            update_text += "| " + " | ".join(cell.replace("|", r"\|") for cell in cells) + " |\n"
    priority = {"pass": 0, "needs_work": 1, "blocked": 2}
    selected = max((review for review, _ in reviews), key=lambda r: priority[r.verdict])
    dispositions = ("open", "carried", "closed_by_review", "waived_by_human")
    rows = {}
    for index, (review, text) in enumerate(reviews):
        source_rows = verdict.finding_rows(text, tuple(f.id for f in review.findings))
        for finding, row in zip(review.findings, source_rows):
            finding_id = finding.id
            if seat_names and finding_id in collisions:
                finding_id = f"{finding.id}-{seat_names[index]}"
                if finding_id in counts or finding_id in rows:
                    raise ValueError(f"seat-suffixed finding id already exists: {finding_id}")
                row = row.replace(finding.id, finding_id, 1)
            rank = (dispositions.index(finding.disposition), finding.severity)
            if finding_id not in rows or rank < rows[finding_id][0]:
                rows[finding_id] = (rank, row)
    return (
        f"# Review: {selected.identity} round {selected.round}\n\n## Findings\n\n"
        "| id | severity | disposition | summary |\n| --- | --- | --- | --- |\n"
        + "\n".join(row for _, row in rows.values())
        + update_text
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
        stat = gitws.bounded_diff(root, base, paths, 4096, stat=True, executable=cfg.git)
        heading = f"Base commit: {base}\nSelected-path stats (separate batch summaries):\n{stat}"
        omitted = (
            heading + "\nPer-slice diffs omitted by the bounded evidence gate; inspect locally."
        )
        if len(paths) > 64 or gitws.DIFF_NOTICE in stat:
            text = omitted
        else:
            text = heading
            seen = set()
            for sid, rows in paths_by_slice.items():
                unique = [row for path, row in rows.items() if path not in seen]
                seen.update(rows)
                if unique:
                    label = f"\n### {sid}\n"
                    remaining = render.DIFF_LIMIT - len((text + label).encode("utf-8"))
                    if remaining < 256:
                        text = omitted
                        break
                    text += label + _diff(root, cfg, base, unique, remaining)
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
