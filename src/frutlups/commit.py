"""Optional approval-bound Git completion, without a second mutable history."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from . import gitws as g, ledger, runtime, procs
from ._paths import safe_rel, repo_path as _repo_path
from .ownership import writer_lock

SCHEMA = "frutlups.commit-manifest/1"
LEDGER = "05_governance/ledger.jsonl"
BLOB_LIMIT = g.BLOB_LIMIT
OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})$")
SHA = re.compile(r"[0-9a-f]{64}$")
OPERATION = re.compile(r"[0-9a-f]{32}$")


def _json(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def _hash(data, storage="normalized"):
    return hashlib.sha256(data).hexdigest() if storage == "raw" else ledger.evidence_sha(data)


def repo_path(root, relative):
    target = _repo_path(root, relative)
    node = Path(root)
    for part in safe_rel(relative).split("/"):
        node /= part
        if node.is_symlink() or node.is_junction():
            raise ValueError("commit path traverses a link: " + relative)
    return target


def _read(path, limit=BLOB_LIMIT):
    if path.is_symlink() or path.is_junction() or not path.is_file():
        raise ValueError("commit evidence must be a regular file: " + path.name)
    with g._open_regular(path) as stream:
        if os.fstat(stream.fileno()).st_size > limit:
            raise ValueError("commit evidence exceeds the bounded file allowance: " + path.name)
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("commit evidence grew beyond the bounded allowance")
    return data


def _immutable(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _head(root, *, executable="git"):
    return g._git(root, "rev-parse", "--verify", "HEAD", executable=executable).stdout.decode().strip()


def _tree(root, revision, *, executable="git"):
    if not isinstance(revision, str) or not OID.fullmatch(revision):
        raise ValueError("invalid Git tree identity")
    data = g._git(root, "ls-tree", "-r", "-z", "--full-tree", revision, executable=executable).stdout
    output = {}
    for row in g._nul_records(data):
        info, separator, path = row.partition(b"\t")
        if not separator or not re.fullmatch(
                rb"(?:100644|100755|120000|160000) (?:blob|commit) (?:[0-9a-f]{40}|[0-9a-f]{64})", info):
            raise ValueError("malformed required Git tree metadata")
        mode, kind, oid = info.decode("ascii").split()
        rel = safe_rel(path.decode("utf-8"))
        if rel in output:
            raise ValueError("duplicate Git tree path")
        output[rel] = (mode, kind, oid)
    return output


def _at(root, tree, path, *, executable="git"):
    row = tree.get(path)
    if row is None or row[1] != "blob":
        raise ValueError("missing committed evidence: " + path)
    ((_, data),) = g.read_blobs(root, [row[2]], executable=executable)
    return data


def _storage(root, paths, *, executable="git"):
    raw = g._git(root, "check-attr", "-z", "--stdin", "text",
                 input=b"".join(p.encode("utf-8") + b"\0" for p in paths),
                 executable=executable).stdout
    fields = g._nul_records(raw)
    if len(fields) != 3 * len(paths):
        raise ValueError("incomplete required Git attributes")
    output = {}
    for index, path in enumerate(paths):
        name, attribute, value = fields[index * 3:index * 3 + 3]
        if name.decode("utf-8") != path or attribute != b"text":
            raise ValueError("malformed required Git attributes")
        output[path] = "raw" if value == b"unset" else "normalized"
    return output


def _mode(root, path, parent_tree, *, executable="git"):
    if os.name == "nt":
        return parent_tree.get(path, ("100644",))[0]
    value = repo_path(root, path).stat().st_mode
    filemode = g._git(root, "config", "--bool", "core.filemode", check=False, executable=executable)
    if filemode.stdout.strip() == b"false" and path in parent_tree:
        return parent_tree[path][0]
    return "100755" if value & 0o111 else "100644"


def _boundary_policy(rm):
    if rm.verification_git_boundary is None:
        return None
    policy = {"argv": list(rm.verification_git_boundary),
              "timeout_seconds": rm.verification_timeout_seconds, "runtime": dict(rm.runtime)}
    _validate_policy(policy)
    return policy


def _validate_policy(value):
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != {"argv", "timeout_seconds", "runtime"}:
        raise ValueError("invalid frozen Git-boundary policy")
    argv, timeout, runtime = value["argv"], value["timeout_seconds"], value["runtime"]
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(item, str) and item for item in argv)
        or type(timeout) is not int
        or timeout <= 0
    ):
        raise ValueError("invalid frozen Git-boundary command or timeout")
    if not isinstance(runtime, dict) or set(runtime) - {"python"}:
        raise ValueError("invalid frozen project runtime")
    if runtime and not re.fullmatch(r"3\.\d+(?:\.\d+)?", str(runtime.get("python", ""))):
        raise ValueError("Git-boundary runtime must be a portable Python version")


def _request_intent(root, paths, scope, message, rm, artifact_dir, *, executable="git"):
    """Create the immutable payload manifest before the caller appends approval."""
    root = Path(root)
    if rm.schema != "frutlups.roadmap/2":
        raise ValueError("commit intent requires frutlups.roadmap/2")
    if not re.fullmatch(r"M\d{3}(?:-S\d{2})?", scope):
        raise ValueError("invalid commit operation scope")
    if not isinstance(message, str) or not message.strip() or len(message.encode()) > 4096:
        raise ValueError("commit message must contain 1..4096 UTF-8 bytes")
    if "Template-Operation:" in message:
        raise ValueError("commit message cannot supply an operation trailer")
    paths = g.approved_paths(root, paths, executable=executable)
    paths = [path for path in paths if path != LEDGER]
    g.guard_index(root, [*paths, LEDGER], executable=executable)
    parent = _head(root, executable=executable)
    parent_tree = _tree(root, parent, executable=executable)
    index_tree = g._git(root, "write-tree", executable=executable).stdout.decode().strip()
    initial_index = _tree(root, index_tree, executable=executable)
    storage = _storage(root, paths, executable=executable) if paths else {}
    deleted = [path for path in paths if not repo_path(root, path).exists()]
    old_blobs = g.blob_hashes(root, [parent_tree[p][2] for p in deleted if p in parent_tree], executable=executable)
    entries = []
    for path in paths:
        target = repo_path(root, path)
        mode = target.lstat().st_mode if target.exists() or target.is_symlink() else None
        if mode is not None and not stat.S_ISREG(mode):
            raise ValueError("commit payload must be a regular file: " + path)
        kind = storage[path]
        if mode is None:
            old = parent_tree.get(path)
            if old is None:
                # A prior-round untracked addition can end as a cumulative tombstone.
                # Its evidence manifest persists, but it has no Git payload or delta.
                continue
            if old[1] != "blob":
                raise ValueError("approved deletion has no parent blob: " + path)
            digest = old_blobs[old[2]][kind]
            git_mode = old[0]
        else:
            digest = _hash(_read(target), kind)
            git_mode = _mode(root, path, initial_index, executable=executable)
        entries.append(
            {
                "path": path,
                "state": "deleted" if mode is None else "file",
                "sha": digest,
                "storage": kind,
                "mode": git_mode,
            }
        )
    operation = uuid4().hex
    directory = safe_rel(str(artifact_dir).replace("\\", "/").rstrip("/"))
    rel = directory + "/commit-" + operation + ".json"
    target = repo_path(root, rel)
    ledger_path = repo_path(root, LEDGER)
    if ledger_path.is_symlink() or ledger_path.exists() and not ledger_path.is_file():
        raise ValueError("commit ledger must be a regular file")
    prefix = _read(ledger_path) if ledger_path.exists() else b""
    if prefix and not prefix.endswith(b"\n"):
        raise ValueError("ledger must end in a newline before commit intent")
    manifest = {
        "schema": SCHEMA,
        "id": operation,
        "scope": scope,
        "parent": parent,
        "index_tree": index_tree,
        "entries": entries,
        "ledger": {"path": LEDGER, "prefix_sha": ledger.evidence_sha(prefix)},
        "git_boundary": _boundary_policy(rm),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = _json(manifest).encode("utf-8")
    _immutable(target, encoded)
    return {
        "id": operation,
        "parent": parent,
        "manifest": {"path": rel, "sha": ledger.evidence_sha(encoded)},
        "message": message.strip(),
    }


def _intent(value):
    if not isinstance(value, dict) or set(value) != {"id", "parent", "manifest", "message"}:
        raise ValueError("invalid commit intent fields")
    if not OPERATION.fullmatch(str(value["id"])) or not OID.fullmatch(str(value["parent"])):
        raise ValueError("invalid commit operation or parent identity")
    ref = value["manifest"]
    if not isinstance(ref, dict) or set(ref) != {"path", "sha"}:
        raise ValueError("invalid commit manifest reference")
    safe_rel(ref["path"])
    if not SHA.fullmatch(str(ref["sha"])):
        raise ValueError("invalid commit manifest hash")
    message = value["message"]
    if not isinstance(message, str) or not message.strip() or len(message.encode()) > 4096:
        raise ValueError("invalid commit message")
    if "Template-Operation:" in message:
        raise ValueError("duplicate operation trailer in commit message")
    return value


def _manifest(data, intent, scope):
    if ledger.evidence_sha(data) != intent["manifest"]["sha"]:
        raise ValueError("commit manifest hash mismatch")
    manifest = json.loads(data)
    keys = {"schema", "id", "scope", "parent", "index_tree", "entries", "ledger", "git_boundary"}
    if not isinstance(manifest, dict) or set(manifest) != keys:
        raise ValueError("invalid commit manifest fields")
    if (manifest["schema"], manifest["id"], manifest["scope"], manifest["parent"]) != (
        SCHEMA,
        intent["id"],
        scope,
        intent["parent"],
    ):
        raise ValueError("commit manifest operation does not match approval")
    if not OID.fullmatch(str(manifest["index_tree"])):
        raise ValueError("invalid initial index identity")
    _validate_policy(manifest["git_boundary"])
    ledger_meta = manifest["ledger"]
    if (
        not isinstance(ledger_meta, dict)
        or set(ledger_meta) != {"path", "prefix_sha"}
        or ledger_meta["path"] != LEDGER
        or not SHA.fullmatch(str(ledger_meta["prefix_sha"]))
    ):
        raise ValueError("invalid commit ledger identity")
    entries = manifest["entries"]
    if not isinstance(entries, list) or len(entries) > 100_000:
        raise ValueError("invalid commit payload entries")
    seen = {LEDGER, intent["manifest"]["path"]}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "path",
            "state",
            "sha",
            "storage",
            "mode",
        }:
            raise ValueError("invalid commit payload entry")
        path = safe_rel(entry["path"])
        if (
            path in seen
            or entry["state"] not in ("file", "deleted")
            or not SHA.fullmatch(str(entry["sha"]))
            or entry["storage"] not in ("raw", "normalized")
            or entry["mode"] not in ("100644", "100755")
        ):
            raise ValueError("invalid or duplicate commit payload identity")
        seen.add(path)
    return manifest


def _ledger_prefix(root, event, manifest, *, executable="git"):
    data = _read(repo_path(Path(root), LEDGER))
    prefix = b""
    for line in data.splitlines(keepends=True):
        if line.strip():
            parsed = json.loads(line)
            if parsed.get("commit_intent", {}).get("id") == event["commit_intent"]["id"]:
                if parsed != event:
                    raise ValueError("commit approval differs from supplied ledger history")
                if ledger.evidence_sha(prefix) != manifest["ledger"]["prefix_sha"]:
                    raise ValueError("ledger prefix preceding approval has changed")
                return prefix + line
        prefix += line
    raise ValueError("commit approval is missing from the working ledger")


def _candidates(root, intent, *, executable="git"):
    result = g._git(
        root,
        "log",
        "--all",
        "--ancestry-path",
        intent["parent"] + "^{commit}..",
        "--max-count=10001",
        "--encoding=none",
        "--format=%H%x00%P%x00%B%x00",
        executable=executable,
    )
    fields = result.stdout.split(b"\0")
    if (len(fields) - 1) % 3 or fields[-1].strip():
        raise ValueError("malformed required Git commit metadata")
    count = (len(fields) - 1) // 3
    if count > 10_000:
        raise ValueError("commit witness search exceeds 10000 descendant commits")
    found = []
    for index in range(0, len(fields) - 2, 3):
        oid = fields[index].decode("ascii").strip()
        parents = fields[index + 1].decode("ascii").split()
        if not OID.fullmatch(oid) or any(not OID.fullmatch(parent) for parent in parents):
            raise ValueError("malformed required Git commit identity")
        message = fields[index + 2].decode("utf-8", "strict")
        if "Template-Operation: " + intent["id"] in message:
            found.append((oid, parents, message))
    return found


def _candidate(root, tree, parent_tree, manifest, intent, prefix, *, executable="git"):
    allowed = {e["path"] for e in manifest["entries"]} | {LEDGER, intent["manifest"]["path"]}
    changed = {p for p in tree.keys() | parent_tree.keys() if tree.get(p) != parent_tree.get(p)}
    if changed - allowed:
        raise ValueError("commit candidate contains unrelated changed paths")
    ledger_mode = parent_tree.get(LEDGER, ("100644",))[0]
    for path, mode in ((LEDGER, ledger_mode), (intent["manifest"]["path"], "100644")):
        if tree.get(path, ())[:2] != (mode, "blob"):
            raise ValueError("framework commit evidence type or mode differs: " + path)
    framework = [LEDGER, intent["manifest"]["path"]]
    objects = [tree[path][2] for path in framework]
    for entry in manifest["entries"]:
        path = entry["path"]
        row = tree.get(path)
        if entry["state"] == "deleted":
            if row is not None or path not in parent_tree:
                raise ValueError("approved deletion differs: " + path)
            row = parent_tree[path]
        if row is None or row[:2] != (entry["mode"], "blob"):
            raise ValueError("approved file type or mode differs: " + path)
        objects.append(row[2])
    blobs = g.blob_hashes(root, objects, executable=executable)
    for entry in manifest["entries"]:
        source = parent_tree if entry["state"] == "deleted" else tree
        if blobs[source[entry["path"]][2]][entry["storage"]] != entry["sha"]:
            raise ValueError("approved Git blob content differs: " + entry["path"])
    if blobs[tree[LEDGER][2]]["normalized"] != ledger.evidence_sha(prefix):
        raise ValueError("committed ledger is not the intended ordered approval prefix")
    manifest_hash = blobs[tree[intent["manifest"]["path"]][2]]["normalized"]
    if manifest_hash != intent["manifest"]["sha"]:
        raise ValueError("committed manifest differs from approval")


def _load(root, event, candidate=None, *, executable="git"):
    intent = _intent(event["commit_intent"])
    scope = event.get("slice", event.get("milestone"))
    tree = _tree(root, candidate[0], executable=executable) if candidate else None
    data = (
        _at(root, tree, intent["manifest"]["path"], executable=executable)
        if tree is not None
        else _read(repo_path(Path(root), intent["manifest"]["path"]))
    )
    manifest = _manifest(data, intent, scope)
    prefix = _ledger_prefix(root, event, manifest, executable=executable)
    if candidate:
        expected_message = intent["message"] + "\n\nTemplate-Operation: " + intent["id"]
        if candidate[1] != [intent["parent"]] or candidate[2] != expected_message + "\n":
            raise ValueError("commit witness has the wrong parent or message")
        _candidate(root, tree, _tree(root, intent["parent"], executable=executable), manifest, intent, prefix, executable=executable)
    return intent, manifest, prefix


def status(root, events, rm, *, executable="git"):
    """Derive Git completion without looking at today's payload/index for witnesses."""
    events = [ledger._event_dict(e) if isinstance(e, ledger.Event) else e for e in events]
    cancelled = {e["operation"] for e in events if e.get("ev") == "commit_cancelled"}
    output = []
    seen = set()
    for event in events:
        if "commit_intent" not in event:
            continue
        intent = _intent(event["commit_intent"])
        if intent["id"] in seen:
            raise ValueError("duplicate commit operation identity")
        seen.add(intent["id"])
        state = "cancelled" if intent["id"] in cancelled else "pending"
        row = {"id": intent["id"], "scope": event.get("slice", event.get("milestone"))}
        candidates = _candidates(root, intent, executable=executable)
        valid, failures = [], []
        for candidate in candidates:
            try:
                _load(root, event, candidate, executable=executable)
            except ValueError as exc:
                failures.append(exc)
                continue
            valid.append(candidate)
        if len(valid) > 1 and state != "cancelled":
            raise ValueError("duplicate commit witnesses for operation " + intent["id"])
        if state == "cancelled":
            _load(root, event, executable=executable)
            if valid:
                raise ValueError("cancelled intent has an unexpected completion witness")
            row["state"] = state
            output.append(row)
            continue
        if valid:
            ancestry = g._git(root, "merge-base", "--is-ancestor", valid[0][0], "HEAD", check=False, executable=executable)
            if ancestry.returncode:
                raise ValueError("completed commit refuses unexplained HEAD movement")
            row.update(state="completed", commit=valid[0][0])
        else:
            if failures:
                raise failures[0]
            _load(root, event, executable=executable)
            if _head(root, executable=executable) != intent["parent"]:
                raise ValueError("pending commit refuses unexplained HEAD movement")
            row["state"] = state
        output.append(row)
    if sum(row["state"] == "pending" for row in output) > 1:
        raise ValueError("more than one pending commit operation")
    return output


def _marker(root, *, executable="git"):
    directory = g._git(root, "rev-parse", "--git-common-dir", executable=executable).stdout.decode().strip()
    return (Path(root) / directory).resolve() / "template-commit-inflight"


def _marker_data(marker):
    data = json.loads(_read(marker, 4096))
    if (not isinstance(data, dict) or set(data) != {"operation", "parent"}
            or not OPERATION.fullmatch(str(data["operation"]))
            or not OID.fullmatch(str(data["parent"]))):
        raise ValueError("malformed in-flight Git marker")
    return data


def _archive_marker(marker, data):
    encoded = _json(data).encode("utf-8")
    name = "template-commit-resolved-" + data["operation"] + "-" + hashlib.sha256(encoded).hexdigest()[:16]
    resolved = marker.with_name(name)
    if os.path.lexists(resolved):
        if _read(resolved, 8192) != encoded:
            raise ValueError("Git marker archive collision; preserve ownership evidence")
    else:
        _immutable(resolved, encoded)
    # Idempotent after interruption between archive fsync and removal.
    marker.unlink()


def _resolve_inflight(root, operation_id, reason, by, *, executable="git"):
    """Caller must first obtain explicit human/architect attribution of Git exit."""
    valid = by in ("human", "architect") and isinstance(reason, str) and reason.strip()
    if not valid:
        raise ValueError("Git attribution requires human/architect and a reason")
    if not OPERATION.fullmatch(str(operation_id)):
        raise ValueError("invalid commit operation identity")
    marker = _marker(root, executable=executable)
    if marker.exists():
        if marker.is_symlink() or not marker.is_file():
            raise ValueError("in-flight Git marker must remain a regular file")
        data = _marker_data(marker)
        if data.get("operation") != operation_id:
            raise ValueError("in-flight Git marker belongs to another operation")
        data["resolution"] = {"by": by, "reason": reason}
        # Preserve diagnostics; resolution does not manufacture an approval or Git witness.
        _archive_marker(marker, data)


def _index_idle(root, *, executable="git"):
    directory = g._git(root, "rev-parse", "--absolute-git-dir", executable=executable).stdout.decode().strip()
    if os.path.lexists(Path(directory) / "index.lock"):
        raise ValueError("Git index.lock exists; resolve the in-flight Git operation first")


def _idle(root, *, executable="git"):
    if os.path.lexists(_marker(root, executable=executable)):
        raise ValueError("prior Git dispatch is unresolved; architect must attribute its exit")
    _index_idle(root, executable=executable)


def _resolve_completed_marker(root, rows, events, *, executable="git"):
    marker = _marker(root, executable=executable)
    if not os.path.lexists(marker):
        return
    if marker.is_symlink() or not marker.is_file():
        raise ValueError("in-flight Git marker must remain a regular file")
    data = _marker_data(marker)
    row = next((item for item in rows if item["id"] == data.get("operation")), None)
    if row is None or row["state"] != "completed":
        return  # Pending/unknown ownership still needs explicit exit attribution.
    event = next(item for item in events if item.get("commit_intent", {}).get("id") == row["id"])
    if data.get("parent") != event["commit_intent"]["parent"]:
        raise ValueError("in-flight Git marker parent differs from the witnessed operation")
    _index_idle(root, executable=executable)
    # status already proved the exact immutable commit; this archives completion,
    # not an inferred PID exit or permission to terminate a process.
    data["completion"] = {"commit": row["commit"]}
    _archive_marker(marker, data)


def _cancel_check(root, events, rm, operation_id, *, executable="git"):
    events_for_id = [e for e in events if e.get("commit_intent", {}).get("id") == operation_id]
    cancelled = any(
        e.get("ev") == "commit_cancelled" and e.get("operation") == operation_id for e in events
    )
    if len(events_for_id) != 1 or cancelled:
        raise ValueError("only an unwitnessed pending commit intent can be cancelled")
    event = events_for_id[0]
    intent = _intent(event["commit_intent"])
    _load(root, event, executable=executable)
    for candidate in _candidates(root, intent, executable=executable):
        try:
            _load(root, event, candidate, executable=executable)
        except ValueError:
            continue
        raise ValueError("completed commit intent cannot be cancelled")
    _idle(root, executable=executable)


def _worktree(root, manifest, intent, prefix, *, executable="git"):
    if _head(root, executable=executable) != intent["parent"]:
        raise ValueError("pending commit refuses unexplained HEAD movement")
    ledger_path = repo_path(Path(root), LEDGER)
    if ledger_path.is_symlink() or not ledger_path.is_file():
        raise ValueError("pending ledger must remain a regular file")
    if _read(ledger_path) != prefix:
        raise ValueError("ledger writes are frozen while commit intent is pending")
    if ledger.evidence_sha(_read(repo_path(Path(root), intent["manifest"]["path"]))) != intent["manifest"]["sha"]:
        raise ValueError("pending commit manifest changed")
    parent_tree = _tree(root, intent["parent"], executable=executable)
    initial_index = _tree(root, manifest["index_tree"], executable=executable)
    for entry in manifest["entries"]:
        target = repo_path(Path(root), entry["path"])
        if entry["state"] == "deleted":
            if target.exists() or target.is_symlink():
                raise ValueError("approved deletion was replaced: " + entry["path"])
            continue
        if not target.is_file() or target.is_symlink():
            raise ValueError("approved payload type changed: " + entry["path"])
        if _hash(_read(target), entry["storage"]) != entry["sha"]:
            raise ValueError("approved payload changed: " + entry["path"])
        if _mode(Path(root), entry["path"], initial_index, executable=executable) != entry["mode"]:
            raise ValueError("approved payload mode changed: " + entry["path"])
    return parent_tree


def _boundary(root, policy, tree, *, executable="git"):
    if policy is None:
        return
    before = g.workspace_witness(root, executable=executable)
    selected = runtime.resolve_runtime(root, SimpleNamespace(runtime=policy["runtime"]), git=executable)
    argv = runtime.runtime_argv(policy["argv"], selected.executable)
    with tempfile.TemporaryDirectory(prefix="frutlups-boundary-") as directory:
        output = Path(directory)
        token = procs.stdout_observer.set(None)
        try:
            result = procs.run_process(argv, root, dict(os.environ, TEMPLATE_CANDIDATE_TREE=tree),
                                       policy["timeout_seconds"], output / "stdout", output / "stderr")
        finally:
            procs.stdout_observer.reset(token)
            if before != g.workspace_witness(root, executable=executable):
                raise ValueError("Git-boundary check mutated approved worktree/index/HEAD")
    if result.exit != 0 or result.timed_out or result.exception:
        raise ValueError("Git-boundary check rejected the exact candidate")


def _recover(root, events, rm, execute=False, *, executable="git"):
    """Recognize completion first; otherwise perform only exact missing Git work."""
    rows = status(root, events, rm, executable=executable)
    if execute:
        _resolve_completed_marker(root, rows, events, executable=executable)
    pending = next((r for r in rows if r["state"] == "pending"), None)
    if pending is None:
        return rows
    event = next(e for e in events if e.get("commit_intent", {}).get("id") == pending["id"])
    intent, manifest, prefix = _load(root, event, executable=executable)
    if _boundary_policy(rm) != manifest["git_boundary"]:
        raise ValueError(
            "Git-boundary policy changed after approval; cancel and review the new policy"
        )
    _idle(root, executable=executable)
    parent_tree = _worktree(root, manifest, intent, prefix, executable=executable)
    paths = [e["path"] for e in manifest["entries"]] + [LEDGER, intent["manifest"]["path"]]
    g.guard_index(root, paths, executable=executable)
    unchanged_index = g._git(
        root,
        "diff",
        "--cached",
        "--quiet",
        "--no-ext-diff",
        manifest["index_tree"],
        "--",
        check=False,
        executable=executable,
    )
    if unchanged_index.returncode not in (0, 1):
        raise ValueError("cannot inspect the pending Git index")
    if unchanged_index.returncode == 1:
        data = g._git(root, "ls-files", "--stage", "-z", executable=executable).stdout
        index = {}
        for row in data.split(b"\0"):
            if row:
                info, path = row.split(b"\t", 1)
                mode, oid, stage = info.decode("ascii").split()
                if stage != "0":
                    raise ValueError("unmerged entries in pending commit index")
                index[path.decode("utf-8")] = (mode, "blob", oid)
        _candidate(root, index, parent_tree, manifest, intent, prefix, executable=executable)
    pending["detail"] = "stage the exact approved paths and commit once"
    if not execute:
        return rows
    marker = _marker(root, executable=executable)
    with marker.open("x", encoding="utf-8") as stream:
        stream.write(_json({"operation": intent["id"], "parent": intent["parent"]}))
        stream.flush()
        os.fsync(stream.fileno())
    # Keep ownership uncertainty on every failure, including failed validation.
    if unchanged_index.returncode == 0:
        g.stage_exact(root, paths, executable=executable)
    _worktree(root, manifest, intent, prefix, executable=executable)
    tree = g._git(root, "write-tree", executable=executable).stdout.decode().strip()
    _candidate(root, _tree(root, tree, executable=executable), parent_tree, manifest, intent, prefix, executable=executable)
    _boundary(root, manifest["git_boundary"], tree, executable=executable)
    message = intent["message"] + "\n\nTemplate-Operation: " + intent["id"]
    g._git(root, "commit", "--cleanup=verbatim", "-m", message, executable=executable)
    result = status(root, events, rm, executable=executable)
    if not any(r["id"] == intent["id"] and r["state"] == "completed" for r in result):
        raise ValueError("Git returned without a valid completion witness")
    _resolve_completed_marker(root, result, events, executable=executable)
    return result


def require_idle(root, events, rm, *, allow_finish=None, executable="git"):
    """A mutation guard, separate from pure folding and evidence validation."""
    with writer_lock(root, rm, executable=executable):
        state = ledger.fold(events, rm)
        unfinished = [key for key, attempt in state.attempts.items() if attempt["finish"] is None]
        if unfinished and unfinished != [allow_finish]:
            raise ValueError("unresolved invocation prevents new work")
        rows = status(root, events, rm, executable=executable)
        if any(row["state"] == "pending" for row in rows):
            raise ValueError("commit pending; explicit recovery or cancellation required")
        if any(row["state"] == "completed" for row in rows):
            _resolve_completed_marker(root, rows, [ledger._event_dict(e) for e in events], executable=executable)
        _idle(root, executable=executable)
        return state


def recover(root, rm, *, executable="git"):
    """Internal recovery only; read current authority under shared writer ownership."""
    with writer_lock(root, rm, executable=executable):
        events = ledger.read(root / LEDGER)
        state = ledger.validate_history(root, events, rm, executable=executable)
        if any(attempt["finish"] is None for attempt in state.attempts.values()):
            raise ValueError("unresolved invocation prevents commit recovery")
        return _recover(root, [ledger._event_dict(e) for e in events], rm, True, executable=executable)


def resolve_inflight(root, rm, operation_id, reason, by, *, executable="git"):
    with writer_lock(root, rm, executable=executable):
        events = ledger.read(root / LEDGER)
        state = ledger.validate_history(root, events, rm, executable=executable)
        if any(attempt["finish"] is None for attempt in state.attempts.values()):
            raise ValueError("unresolved invocation prevents Git attribution")
        event = state.intents.get(operation_id)
        if event is None:
            raise ValueError("unknown commit operation")
        marker = _marker(root, executable=executable)
        if os.path.lexists(marker):
            data = _marker_data(marker)
            if data["parent"] != event["commit_intent"]["parent"]:
                raise ValueError("in-flight Git marker parent differs")
        _index_idle(root, executable=executable)
        _resolve_inflight(root, operation_id, reason, by, executable=executable)


def cancel(root, rm, operation_id, reason, by, retain_acceptance=False, *, executable="git"):
    from ._attempts import event
    with writer_lock(root, rm, executable=executable):
        events = ledger.read(root / LEDGER)
        state = ledger.validate_history(root, events, rm, executable=executable)
        if any(attempt["finish"] is None for attempt in state.attempts.values()):
            raise ValueError("unresolved invocation prevents cancellation")
        raw = event("commit_cancelled", operation=operation_id, reason=reason,
                    retain_acceptance=retain_acceptance)
        raw["by"] = by
        candidate = ledger._parse_event(raw)
        _cancel_check(root, [ledger._event_dict(e) for e in events], rm, operation_id, executable=executable)
        ledger.validate_history(root, (*events, candidate), rm, executable=executable)
        ledger._write_line(root / LEDGER, raw)


def _eligible(root, events, scope, *, executable="git"):
    paths = {LEDGER}
    for event in events:
        row = ledger._event_dict(event)
        related = (event.slice == scope or event.milestone == scope
                   or row.get("scope") == scope or event.slice and event.slice.startswith(scope + "-S"))
        # Grant and extension snapshots remain part of the exact approval payload.
        refs = row.get("authority", [])
        if related:
            paths.update(row[key] for key in ("path", "receipt", "report", "notes_path") if key in row)
            refs += row.get("evidence", [])
            refs += [row[key] for key in ("envelope", "manifest", "outcome") if key in row]
            if "commit_intent" in row:
                refs.append(row["commit_intent"]["manifest"])
            if "manifest" in row:
                paths.update(e["path"] for e in ledger.manifest(root, row["manifest"])["changed"])
            if "outcome" in row:
                refs += ledger.read_reference(root, row["outcome"], 16 * 1024)["evidence"]
        paths.update(ref["path"] for ref in refs)
    return paths


def approve(root, rm, raw, message, commit_on_accept, *, executable="git"):
    """One shared slice/close boundary; replay performs only missing Git work."""
    with writer_lock(root, rm, executable=executable):
        events = ledger.read(root / LEDGER)
        state = ledger.validate_history(root, events, rm, executable=executable)
        if any(attempt["finish"] is None for attempt in state.attempts.values()):
            raise ValueError("unresolved invocation prevents approval or recovery")
        if raw.get("ev") not in ("accepted", "milestone_done") or raw.get("by") != "frutlups":
            raise ValueError("not an internal approval event")
        scope = raw.get("slice", raw.get("milestone"))
        already_approved = (state.slices.get(scope) is not None and state.slices[scope].step == "accepted"
                            if raw["ev"] == "accepted" else scope in state.milestones_done)
        previous = next((e for e in reversed(events) if already_approved and e.ev == raw["ev"]
                         and (e.slice or e.milestone) == scope and e.round == raw.get("round")), None)
        if previous is not None:
            if "commit_intent" not in previous.data:
                return None  # Legacy/ledger-only approvals never acquire an inferred intent.
            identity = previous.data["commit_intent"]["id"]
            if identity in state.cancelled:
                raise ValueError("cancelled approval requires new review")
            rows = _recover(root, [ledger._event_dict(e) for e in events], rm, True, executable=executable)
            return next(row["commit"] for row in rows if row["id"] == identity)
        require_idle(root, events, rm, executable=executable)
        ledger.validate_history(root, (*events, ledger._parse_event(raw)), rm, executable=executable)
        selected = [s for sid, s in state.slices.items() if sid == scope or sid.startswith(scope + "-S")]
        products = {}
        for current in selected:
            if current.manifest:
                products.update({e["path"]: e for e in ledger.manifest(
                    root, ledger._thaw(current.manifest))["changed"]})
        if raw["ev"] == "milestone_done":
            # Keep the closure path set, but use recorded changes in ledger order.
            # Replaying cumulative manifests would promote unchanged older rows.
            for event in events:
                if event.ev == ledger.Ev.coded:
                    products.update({row["path"]: row for row in event.data["changed"]
                                     if row["path"] in products})
        for path, entry in products.items():
            target = repo_path(root, path)
            matches = (not os.path.lexists(target) if entry["kind"] == "deleted"
                       else ledger.evidence_sha(_read(target)) == entry["sha"])
            if not matches:
                raise ValueError("active product drift: " + path)
        if commit_on_accept:
            raw = dict(raw, commit_intent=_request_intent(
                root, sorted(_eligible(root, events, scope, executable=executable)), scope, message, rm,
                "05_governance/reviews/" + scope.split("-")[0].lower(), executable=executable))
        ledger._append_approval(root, rm, raw, executable=executable)
        if not commit_on_accept:
            return None
        rows = _recover(root, [ledger._event_dict(e) for e in ledger.read(root / LEDGER)], rm, True, executable=executable)
        return next(row["commit"] for row in rows if row["id"] == raw["commit_intent"]["id"])
