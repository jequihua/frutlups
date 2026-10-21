"""Explicit, bounded historical deadline recognition; never execution authority.

Only the owned private admission stage constructs this context. Human adoption
owns permission meaning and consumption; this reader checks exact applicability.
"""

import hashlib
import json
import math
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import gitws, ledger
from ._paths import safe_rel

JSON_LIMIT = 32768
FILE_LIMIT = PREFIX_LIMIT = 1048576
LINE_LIMIT = 4096
LEDGER = "05_governance/ledger.jsonl"
KINDS = ("prompt", "coded", "verified", "reviewed", "accepted")


def _need(ok, message):
    if not ok:
        raise ValueError("historical assessment: " + message)


def _keys(value, keys):
    _need(type(value) is dict and set(value) == set(keys), "invalid fields")


def _integer(value, maximum):
    _need(type(value) is int and 0 < value <= maximum, "integer outside bound")


def _path(value):
    _need(type(value) is str and 0 < len(value.encode("utf-8", "strict")) <= 512,
          "path outside byte bound")
    safe_rel(value)
    # Windows aliases/streams are not distinct project-relative evidence paths.
    _need(all(":" not in p and not p.endswith((".", " ")) for p in value.split("/")),
          "aliased path")
    _need(not any(p.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
                  *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
                  for p in value.split("/")), "aliased path")
    return value


def _sha(value):
    _need(type(value) is str and ledger.digest(value), "invalid SHA-256")
    return value


def _json(raw):
    _need(len(raw) <= JSON_LIMIT, "JSON exceeds byte bound")
    text = raw.decode("utf-8", "strict")
    # Bound nesting before json.loads allocates recursive containers. Strings
    # (including escaped quotes and brackets) do not contribute to depth.
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            _need(depth <= 6, "JSON nesting exceeds bound")
        elif char in "]}":
            depth -= 1

    def unique(pairs):
        value = {}
        for key, item in pairs:
            _need(key not in value, "duplicate JSON key")
            value[key] = item
        return value

    def constant(_):
        _need(False, "non-finite JSON number")

    def number(value):
        result = float(value)
        _need(math.isfinite(result), "non-finite JSON number")
        return result

    return json.loads(text, object_pairs_hook=unique, parse_constant=constant, parse_float=number)


def _read(root, relative, limit, *, prefix=False):
    path = ledger._regular_reference(root, dict(path=_path(relative), sha="0" * 64))
    before = ledger._file_key(path)
    info = path.lstat()
    _need(prefix or before[2] <= limit, "file exceeds byte bound")
    with gitws._open_regular(path) as stream:
        _need(gitws._node_identity(os.fstat(stream.fileno()), path_mode=info.st_mode)
              == gitws._node_identity(info), "file changed while opening")
        raw = stream.read(limit if prefix else limit + 1)
    _need(len(raw) <= limit and ledger._file_key(path) == before, "file changed during bounded read")
    ledger._regular_reference(root, dict(path=relative, sha="0" * 64))
    return raw


def _visibility(root, executable, paths):
    result = gitws._git(root, "--no-literal-pathspecs", "check-ignore", "--no-index", "-z", "--stdin",
                        executable=executable, check=False,
                        input=b"".join(p.encode("utf-8") + b"\0" for p in sorted(paths)))
    _need(result.returncode in (0, 1) and not result.stdout, "references must be Git-visible")


def _recorded_inputs(root, cfg, events, inputs):
    recorded, products = set(), {}
    for event in events:
        row = ledger._event_dict(event)
        for key in ("path", "receipt", "report", "notes_path"):
            digest_key = "notes_sha" if key == "notes_path" else "sha"
            if key in row and digest_key in row:
                recorded.add((row[key], row[digest_key]))
        for ref in row.get("authority", []) + row.get("evidence", []):
            recorded.add((ref["path"], ref["sha"]))
        for key in ("baseline", "changed"):
            products.update({ref["path"]: ref for ref in row.get(key, [])})
    for relative, digest in inputs:
        product = products.get(relative, {})
        _need((relative, digest) in recorded
              or product.get("kind") != "deleted" and product.get("sha") == digest
              or ledger._matches_head(root, relative, executable=cfg.git), "unrecorded selection or basis")


@dataclass(frozen=True)
class _Entry:
    chain: tuple[int, ...]  # Native event indices, mapped from raw line numbers.
    cap: int


@dataclass(frozen=True)
class Context:
    root: Path
    selection: tuple[str, str]
    prefix_bytes: int
    prefix_sha: str
    references: tuple[tuple[str, str, int], ...]  # raw identities for fresh checks
    inputs: tuple[tuple[str, str], ...]  # native ownership identities, not raw pins
    events: tuple
    entries: tuple[_Entry, ...]

    def recheck(self, root, cfg, events):
        try:
            _need(Path(root).resolve(strict=True) == self.root and cfg.ledger == LEDGER,
                  "canonical root or ledger changed")
            raw = _read(root, LEDGER, self.prefix_bytes, prefix=True)
            _need(hashlib.sha256(raw).hexdigest() == self.prefix_sha, "raw ledger prefix drift")
            _need(tuple(events[:len(self.events)]) == self.events, "native prefix differs from owned history")
            _visibility(root, cfg.git, {p for p, _, _ in self.references})
            for path, sha, limit in self.references:
                _need(hashlib.sha256(_read(root, path, limit)).hexdigest() == sha, "raw reference drift")
            _recorded_inputs(root, cfg, events, self.inputs)
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            _refusal(exc)

    def cap_for(self, root, events, accepted_index):
        from ._loop_evidence import accepted_chain
        _need(Path(root).resolve() == self.root, "context belongs to another root")
        _need(tuple(events[:len(self.events)]) == self.events, "native prefix differs from owned history")
        for entry in self.entries:
            if entry.chain[-1] == accepted_index:
                _need(accepted_chain(events, accepted_index) == entry.chain, "accepted chain changed")
                return entry.cap
        return None


def _refusal(exc):
    # Never expose arbitrary JSON strings, machine paths or Git child output.
    if type(exc) is ValueError and str(exc).startswith("historical assessment: "):
        raise exc
    raise ValueError("historical assessment: invalid or unavailable bound evidence") from None


def load(root, cfg, events, selection):
    """Read all entries after normal owned history validation, without effects.

    This reader can also inspect an exact partial evidence fixture; that does
    not qualify admission or replace the caller's complete native history gate.
    """
    try:
        return _load(root, cfg, events, selection)
    except (ValueError, OSError, KeyError, TypeError, StopIteration, subprocess.SubprocessError) as exc:
        _refusal(exc)


def _load(root, cfg, events, selection):
    from . import _loop_evidence as evidence
    _need(type(selection) is tuple and len(selection) == 2
          and all(type(v) is str for v in selection), "selection must be one (path, raw_sha256) pair")
    path, sha = _path(selection[0]), _sha(selection[1])
    root = Path(root).resolve(strict=True)
    _need(cfg.ledger == LEDGER, "canonical ledger required")
    refs = {}

    def bound(path, sha, limit, raw_domain=False):
        _path(path)
        _sha(sha)
        raw = _read(root, path, limit)
        actual = hashlib.sha256(raw).hexdigest() if raw_domain else ledger.evidence_sha(raw)
        _need(actual == sha, "raw hash mismatch" if raw_domain else "native evidence hash mismatch")
        identity = (hashlib.sha256(raw).hexdigest(), limit)
        _need(path not in refs or refs[path][0] == identity[0], "reference changed during validation")
        refs[path] = identity
        return raw

    value = _json(bound(path, sha, JSON_LIMIT, True))
    _keys(value, ("schema", "ledger_prefix", "assessment", "entries"))
    _need(value["schema"] == "frutlups.historical-eligibility/1", "unsupported schema")
    prefix = value["ledger_prefix"]
    _keys(prefix, ("bytes", "raw_sha256"))
    _integer(prefix["bytes"], PREFIX_LIMIT)
    _sha(prefix["raw_sha256"])
    raw = _read(root, LEDGER, prefix["bytes"], prefix=True)
    _need(len(raw) == prefix["bytes"] and raw.endswith(b"\n")
          and hashlib.sha256(raw).hexdigest() == prefix["raw_sha256"], "raw ledger prefix mismatch")
    # JSONL records split on LF only, not other whitespace inside JSON strings.
    lines = [line + b"\n" for line in raw[:-1].split(b"\n")]
    _need(len(lines) <= LINE_LIMIT, "prefix line count exceeds bound")
    native, indices = [], {}
    for number, line in enumerate(lines, 1):
        if line.rstrip(b"\r\n"):
            indices[number] = len(native)
            native.append(ledger._parse_event(json.loads(line.decode("utf-8"))))
    _need(tuple(native) == tuple(events[:len(native)]), "native prefix differs from owned history")
    basis = value["assessment"]
    _keys(basis, ("path", "raw_sha256"))
    basis_raw = bound(basis["path"], basis["raw_sha256"], FILE_LIMIT, True)
    _need(type(value["entries"]) is list and 1 <= len(value["entries"]) <= 8, "entry count outside bound")

    def occurrence(ref, kind):
        _keys(ref, ("line", "raw_sha256"))
        _integer(ref["line"], LINE_LIMIT)
        _sha(ref["raw_sha256"])
        line = ref["line"]
        _need(line in indices and hashlib.sha256(lines[line - 1]).hexdigest() == ref["raw_sha256"],
              "raw occurrence mismatch")
        index = indices[line]
        _need(events[index].ev == kind, "occurrence kind mismatch")
        return index

    entries, verified_seen, receipt_paths, receipt_hashes = [], set(), set(), set()
    for row in value["entries"]:
        _keys(row, ("resolution", "target", "cap_seconds", "authority"))
        _integer(row["cap_seconds"], 2147483647)
        _keys(row["target"], KINDS)
        resolution = occurrence(row["resolution"], "resolved")
        chain = tuple(occurrence(row["target"][kind], kind) for kind in KINDS)
        _need(all(a < b for a, b in zip((resolution, *chain), chain)), "occurrence order mismatch")
        _need(not entries or entries[-1].chain[-1] < chain[-1], "entries must have increasing accepted occurrences")
        prompt, coded, verified, reviewed, accepted = (events[i] for i in chain)
        _need(all(e.slice == accepted.slice and e.round == accepted.round
                  for e in (prompt, coded, verified, reviewed)), "target scope or round mismatch")
        _need(evidence.accepted_chain(events, chain[-1]) == chain, "superseded accepted chain")
        resolved = events[resolution]
        _need(resolved.by in ("human", "architect") and resolved.data["scope"] == accepted.slice,
              "resolution actor or scope mismatch")
        latest = next(i for i in range(chain[0] - 1, -1, -1)
                      if events[i].ev == ledger.Ev.resolved and events[i].data["scope"] == accepted.slice)
        _need(latest == resolution, "superseded resolution")
        _need(coded.data["result"] == "implemented" and verified.data["ok"] is True
              and reviewed.data["verdict"] == "pass" and not reviewed.data["open"], "target is not successful")
        receipt_id = (verified.data["receipt"], verified.data["sha"])
        _need(chain[2] not in verified_seen and receipt_id[0] not in receipt_paths
              and receipt_id[1] not in receipt_hashes, "duplicate verified occurrence or receipt")
        verified_seen.add(chain[2])
        receipt_paths.add(receipt_id[0])
        receipt_hashes.add(receipt_id[1])
        authority = row["authority"]
        _need(type(authority) is list and 1 <= len(authority) <= 4, "authority count outside bound")
        native_refs = ledger._thaw(resolved.data["authority"])
        positions = []
        for ref in authority:
            _keys(ref, ("path", "sha"))
            _path(ref["path"])
            _sha(ref["sha"])
            _need(ref in native_refs, "authority is not a resolution member")
            positions.append(native_refs.index(ref))
        _need(positions == sorted(set(positions)), "authority order or duplicate mismatch")
        direct = [*authority, dict(path=coded.data["notes_path"], sha=coded.data["notes_sha"]),
                  dict(path=prompt.data["path"], sha=prompt.data["sha"])]
        for ref in direct:
            bound(ref["path"], ref["sha"], FILE_LIMIT)
        for ref, limit in ((prompt.data["envelope"], 128 * 1024),
                           (coded.data["manifest"], 2 * 1024 * 1024),
                           (dict(path=receipt_id[0], sha=receipt_id[1]), 128 * 1024),
                           (dict(path=reviewed.data["report"], sha=reviewed.data["sha"]), FILE_LIMIT)):
            bound(ref["path"], ref["sha"], limit)
        entries.append(_Entry(chain, row["cap_seconds"]))

    _visibility(root, cfg.git, refs)
    # Selection/basis get no workspace exemption. They must already be recorded
    # native evidence/product bytes or match HEAD, not merely exist in the index.
    inputs = ((path, ledger.evidence_sha(_read(root, path, JSON_LIMIT))),
              (basis["path"], ledger.evidence_sha(basis_raw)))
    _recorded_inputs(root, cfg, events, inputs)
    context = Context(root, selection, prefix["bytes"], prefix["raw_sha256"],
                      tuple((p, h, limit) for p, (h, limit) in sorted(refs.items())), inputs,
                      tuple(native), tuple(entries))
    for entry in entries:
        support = evidence.accepted_evidence(root, cfg, events, entry.chain[-1], historical_context=context)
        result = ledger.read_reference(root, support["receipt"], 128 * 1024)
        _need(support["contract"]["timeout_seconds"] < result["commands"][0]["secs"] <= entry.cap,
              "target must exceed frozen deadline and fit assessed cap")
    context.recheck(root, cfg, events)
    return context
