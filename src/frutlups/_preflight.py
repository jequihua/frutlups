"""Read-only project admission, with local model probe evidence."""

import hashlib
import math
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass, replace
from uuid import uuid4

from . import _loop_evidence as evidence
from . import findings, gitws, ledger, receipt, render, runtime, _attempts
from . import procs
from ._paths import repo_path
from .ownership import writer_lock
from . import roadmap as roadmap_module
from .config import Config
from .seats import Job, Role, child_environment
from .seats import claude, pi


class _ProbeIntegrityError(ValueError):
    """Probe evidence must be preserved and no later probe may be dispatched."""


PROBE_PROMPT = b"Reply with OK only.\n"


def _probe_job(root, cfg, name, memory_enabled=False):
    seat = cfg.seats[name]
    tools = (pi if seat.adapter == "pi" else claude).tools_for(Role.reviewer)
    job_id = f"probe-{uuid4().hex}"
    prompt_path = root / "local_state" / "frutlups" / "jobs" / job_id / "prompt.md"
    return Job(
        id=job_id, role=Role.reviewer, seat_name=name, adapter=seat.adapter,
        provider=seat.provider, model=seat.model, effort=seat.effort,
        prompt_path=prompt_path, prompt_sha=hashlib.sha256(PROBE_PROMPT).hexdigest(),
        cwd=root, tools=tools, timeout=60, expected_notes_path=None,
        memory_enabled=memory_enabled,
        pi_record_bytes=cfg.pi_record_bytes,
    )


def _probed(root, cfg, model, name, memory_enabled=False):
    """A completed probe already holds this exact initial context in the current run.

    The run grammar permits one initial attempt per (scope, round, role, contract);
    changed product, history or seat identity is a new context and probes again.
    """
    events = ledger.read(root / cfg.ledger)
    state = ledger.validate_history(root, events, model, executable=cfg.git)
    if state.run_head is None:
        return False
    run = state.runs[state.run_head["run"]]
    scope = run["admission"]["milestone"]
    contract = _attempts._binding(root, cfg, model, events, state, scope, "holistic", "probe",
                                  _probe_job(root, cfg, name, memory_enabled), PROBE_PROMPT, None)
    # Only this run's invocations (folded roots, retry chains included) can satisfy it;
    # an earlier run's success with the same contract cannot mask a current failure.
    return (scope, "holistic", "probe", contract) in run["initial_contexts"] and any(
        invocation in run["roots"] and a["start"]["role"] == "probe" and a["start"]["scope"] == scope
        and a["start"]["contract"] == contract and a["finish"] is not None
        and a["finish"]["status"] == "completed" for invocation, a in state.attempts.items())


def _probe(root, cfg, name, memory_enabled=False, *, _roadmap=None):
    seat = cfg.seats[name]
    adapter = pi.PiSeat() if seat.adapter == "pi" else claude.ClaudeSeat()
    job = _probe_job(root, cfg, name, memory_enabled)
    prompt, directory = PROBE_PROMPT, job.prompt_path.parent
    prompt_path = job.prompt_path
    model = _roadmap or roadmap_module.load(root / "roadmap.yaml")
    ledger._need(model.schema == ledger.ROADMAP_V2,
                 "unsupported roadmap schema; use native /2 or pinned v4")
    with _attempts.Operation(root, cfg, model, "probe", 60, job=job, prompt=prompt) as operation:
        directory.mkdir(parents=True)
        prompt_path.write_bytes(prompt)
        job = replace(job, timeout=operation.timeout)
        result = _probe_owned(root, cfg, name, adapter, job, directory)
        operation.finish(result.status == "completed", result)
        if result.status == "completed":
            shutil.rmtree(directory)
            return None
        return _probe_failure(root, name, seat, result, directory)


def _probe_owned(root, cfg, name, adapter, job, directory):
    prefix = f"model probe integrity failed: {name}"
    streams = f"streams: {directory.relative_to(root).as_posix()}"

    def witness(phase):
        try:
            return gitws.workspace_witness(root, executable=cfg.git)
        except Exception as exc:
            # The phase and exception type identify the refusal without publishing
            # arbitrary exception text (which can contain paths, secrets or floods).
            raise _ProbeIntegrityError(
                f"{prefix}: {phase} witness unavailable ({type(exc).__name__}); {streams}"
            ) from exc

    ledger_before = (root / cfg.ledger).read_bytes()
    before = witness("before")
    try:
        result = adapter.run(job, cfg)
    finally:
        after = witness("after")
        if (gitws._file_identity(root, cfg.ledger)[0] != "file"
                or (root / cfg.ledger).read_bytes() != ledger_before):
            raise _ProbeIntegrityError(f"{prefix}: changed files; streams: {directory.relative_to(root).as_posix()}")
        if before != after:
            changed = [key for key in ("head", "index", "files") if before[key] != after[key]]
            raise _ProbeIntegrityError(f"{prefix}: changed {', '.join(changed)}; {streams}")

    return result


def _probe_failure(root, name, seat, result, directory):
    streams = f"streams: {directory.relative_to(root).as_posix()}"
    tail = result.stderr_path.read_bytes() if result.stderr_path.is_file() else b""
    detail = receipt._scrub(tail, root, dict(os.environ))
    return (
        f"model probe failed: {name}: adapter={seat.adapter} provider={seat.provider} "
        f"model={seat.model}; failure={result.failure_class}; "
        f"diagnostic: {result.diagnostic or '(empty)'}; stderr: {detail or '(empty)'}; "
        f"streams: {directory.relative_to(root).as_posix()}"
    )


def _frontier(roadmap, state):
    """Select the global lifecycle frontier before applying any grant scope."""
    milestone = roadmap_module.next_milestone(roadmap, state)
    item = (roadmap_module.next_slice(replace(roadmap, milestones=(milestone,)), state)
            if milestone is not None else None)
    return milestone, item


def _admitted_item(roadmap, state):
    """The frontier slice whose frozen runtime governs admission, if any.

    Under a profile, the next global slice is admitted work only when the current
    open grant names it; a completed grant, its recovery or a no-work restart uses
    the project runtime, so an ungranted later declaration cannot veto them.
    """
    item = _frontier(roadmap, state)[1]
    if item is None or roadmap.autonomy is None:
        return item
    head = state.run_head
    run = state.runs[head["run"]] if head is not None else None
    if run is None or run["closed"] is not None or item.id not in run["admission"]["slices"]:
        return None
    return item


def _static(root, cfg, roadmap, *, fresh=True):
    """Read-only static admission; callable for synthetic /2 reader qualification.

    ``fresh=False`` defers fresh-dispatch grant checks to the owned iteration, so
    required recovery or an evidence-qualified terminal stop is not refused first.
    """
    root = Path(root).resolve()
    errors, selected, state = [], None, None
    history_valid = False
    if (root / "STOP").exists():
        errors.append("STOP exists; the owner must remove it to run")
    try:
        agents = root / "AGENTS.md"
        if not agents.is_file() or agents.stat().st_size > 8192:
            errors.append("AGENTS.md must exist and be at most 8 KB")
    except OSError as exc:
        errors.append(f"AGENTS.md: {exc}")
    events = None
    if cfg is not None:
        try:
            cfg = replace(cfg, git=gitws.selected_executable(cfg.git))
        except ValueError as exc:
            return None, [], _errors(root, [*errors, str(exc)])
        try:
            events = ledger.read(root / cfg.ledger)
            if roadmap is not None:
                if roadmap.autonomy is not None:
                    state = ledger.validate_history(root, events, roadmap, executable=cfg.git)
                    history_valid = True
                    from . import commit
                    commits = commit.status(root, events, roadmap, executable=cfg.git) if state.intents else []
                    if fresh:
                        _profile_gate(roadmap, state, commits)
                else:
                    state = ledger.fold(events, roadmap)
                    if fresh and roadmap.schema == ledger.ROADMAP_V2 and state.intents:
                        from . import commit
                        if any(row["state"] == "pending" for row in commit.status(root, events, roadmap, executable=cfg.git)):
                            raise ValueError("commit pending; explicit recovery or cancellation required")
        except (ValueError, OSError) as exc:
            errors.append(str(exc))
        try:
            if not Path(cfg.git).is_file():
                errors.append("git executable missing")
            elif not gitws.is_repo(root, executable=cfg.git):
                errors.append("root is not a Git repository")
            else:
                gitws.head(root, executable=cfg.git)
                if roadmap is not None and roadmap.autonomy is not None:
                    if history_valid:
                        errors.extend(_profile_workspace_errors(root, cfg, events or ()))
                else:
                    errors.extend(evidence.workspace_errors(root, cfg, events or ()))
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            if str(exc) not in errors:
                errors.append(str(exc))
        if os.name == "nt":
            for name in ("SYSTEMROOT", "COMSPEC", "PATHEXT"):
                if name not in {key.upper() for key in cfg.env_passthrough}:
                    errors.append(f"env_passthrough missing required Windows variable: {name}")
    if cfg is not None and roadmap is not None:
        env = child_environment("verification", cfg, memory_enabled=roadmap.memory is not None)
        names, commands = _routes(roadmap, state)
        try:
            item = _admitted_item(roadmap, state) if state is not None else None
            if item is not None:
                frozen = render.task_contract(root, roadmap, item, state.slices[item.id], events, executable=cfg.git)
                selected = receipt.native_runtime(root, roadmap, frozen, cfg)
                commands = {frozen["full"][0]}
            elif state is not None:
                selected = runtime.resolve_runtime(root, roadmap, git=cfg.git)
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            errors.append(str(exc))
        for command in sorted(commands):
            if selected:
                command = runtime.runtime_argv((command,), selected.executable)[0]
            candidate = str(root / command) if Path(command).parent != Path(".") else command
            if not shutil.which(candidate, path=env["PATH"]):
                errors.append(f"verification executable not resolvable: {Path(command).name}")
        checked, probes = set(), []
        for name in sorted(names):
            if name not in cfg.seats:
                errors.append(f"missing seat: {name}")
                continue
            seat = cfg.seats[name]
            key = (seat.adapter, seat.provider, seat.model)
            if key in checked:
                continue
            checked.add(key)
            if not Path(getattr(cfg, seat.adapter)).is_file():
                errors.append(f"seat executable missing: {seat.adapter}")
                continue
            probes.append(name)
    else:
        probes = []
    return selected, probes, _errors(root, errors)


def _errors(root, errors):
    return tuple(" ".join(receipt.scrub_text(error, root, dict(os.environ)).splitlines())
                 for error in errors)


def _profile_gate(roadmap, state, commits=None):
    if any(attempt["finish"] is None for attempt in state.attempts.values()):
        raise ValueError("unresolved invocation prevents admission")
    if (set(state.intents) - set(state.cancelled) if commits is None
            else any(row["state"] == "pending" for row in commits)):
        raise ValueError("commit pending; explicit recovery or cancellation required")
    if any(s.step == "blocked" for s in state.slices.values()) or any(
            h.get("step") == "blocked" for h in state.holistic.values()):
        raise ValueError("blocked work requires explicit resolution")
    if state.run_head is None:
        raise ValueError("profile admission requires a current run grant")
    run = state.runs[state.run_head["run"]]
    milestone = next(m for m in roadmap.milestones if m.id == run["admission"]["milestone"])
    if milestone.status != "active":
        raise ValueError("new admission requires active milestone")
    if run["closed"] is not None:
        raise ValueError("run is closed")
    frontier, current = _frontier(roadmap, state)
    if (frontier and frontier.id != run["admission"]["milestone"]
            or current and current.id not in run["admission"]["slices"]):
        raise ValueError("current lifecycle selection is outside admitted run")
    if run["remaining"]["jobs"] <= 0 or run["remaining"]["seconds"] <= 0:
        raise ValueError("run allowance exhausted")


def _routes(roadmap, state):
    names, commands = set(), set()
    selection = None
    if roadmap.autonomy is not None and state and state.run_head:
        selection = state.runs[state.run_head["run"]]["admission"]["slices"]
    for milestone in roadmap.milestones:
        items = [item for item in milestone.slices
                 if (milestone.status == "active" or state and state.slices[item.id].reopened)
                 and (not state or state.slices[item.id].step != "accepted")
                 and (selection is None or item.id in selection)]
        for item in items:
            names.add("coder")
            names.update(roadmap.review_routing[item.risk])
            commands.add((item.verification or roadmap.verification_full)[0])
        holistic_admitted = (selection is None or
            state.runs[state.run_head["run"]]["admission"]["stop"] == milestone.id)
        if (milestone.status == "active" and milestone.holistic_review and holistic_admitted
                and (not state or milestone.id not in state.milestones_done)):
            names.add("holistic")
    return names, commands


def _profile_workspace_errors(root, cfg, events):
    artifacts, products = {cfg.ledger}, {}
    for event in events:
        row = ledger._event_dict(event)
        artifacts.update(row[key] for key in ("path", "receipt", "report", "notes_path") if key in row)
        refs = row.get("authority", []) + row.get("evidence", [])
        refs += [row[key] for key in ("envelope", "manifest", "outcome") if key in row]
        if "commit_intent" in row:
            refs.append(row["commit_intent"]["manifest"])
        if "outcome" in row:
            refs += ledger.read_reference(root, row["outcome"], 16 * 1024)["evidence"]
        artifacts.update(ref["path"] for ref in refs)
        for key in ("baseline", "changed"):
            products.update({item["path"]: item for item in row.get(key, [])})
        if "manifest" in row:
            products.update({item["path"]: {k: v for k, v in item.items() if k != "round"}
                             for item in ledger.manifest(root, row["manifest"])["changed"]})
    current = evidence.changes(root, cfg)
    errors = [f"unrecorded change: {row['path']}" for row in current
              if row["path"] not in artifacts and products.get(row["path"]) != row
              and not (row["path"] == "05_governance/backlog.md"
                       and findings.matches_backlog(root, events))]
    if not errors and not gitws.is_clean(root, ("local_state/", *(r["path"] for r in current)),
                                        executable=cfg.git):
        errors.append("workspace changed during admission")
    return errors


@dataclass(frozen=True)
class Admission:
    cfg: object
    roadmap: object
    errors: tuple[str, ...]
    runtime: runtime.Runtime | None = None


def _authority_bytes(root):
    return tuple((root / name).read_bytes() if (root / name).is_file() else None
                 for name in ("roadmap.yaml", "frutlups.toml", "frutlups.local.toml",
                              "project.local.toml"))


def _checker(root, cfg, selected, *, deadline, clock=time.monotonic):
    """Read-only checker, called only inside the common writer interval."""
    started = clock()
    cap = cfg.timeouts.verification_seconds
    ledger._need(math.isfinite(cap) and cap > 0 and math.isfinite(deadline),
                 "checker requires a finite positive deadline")
    end = min(deadline, started + cap)
    ledger._need(end > started, "checker deadline exhausted")
    script = repo_path(root, "scripts/ledger.py")
    ledger._need(script.is_file(), "project checker must be a regular file")
    for relative in (cfg.ledger, "roadmap.yaml", "frutlups.toml",
                     "frutlups.local.toml", "project.local.toml"):
        repo_path(root, relative)
    before = gitws.workspace_witness(root, executable=cfg.git)
    authority = _authority_bytes(root)
    ledger_bytes = (root / cfg.ledger).read_bytes()
    identities = tuple(hashlib.sha256(Path(path).read_bytes()).digest()
                       for path in (cfg.git, selected.executable, script))
    env = gitws.controlled_environment(child_environment("verification", cfg))
    env["TEMPLATE_GIT_EXECUTABLE"] = cfg.git
    timeout = end - clock()
    ledger._need(timeout > 0, "checker deadline exhausted")
    with tempfile.TemporaryDirectory(prefix="frutlups-checker-") as directory:
        capture = Path(directory)
        token = procs.stdout_observer.set(None)
        try:
            result = procs.run_process(
                [selected.executable, "-B", "scripts/ledger.py", "--root", str(root), "check"],
                root, env, timeout, capture / "stdout", capture / "stderr")
        except Exception:
            raise ValueError("project checker launch failed; inspect retained state") from None
        finally:
            procs.stdout_observer.reset(token)
        # Never publish arbitrary child bytes or launch exception text.
        ledger._need(not result.timed_out, "project checker timed out; inspect retained state")
        ledger._need(result.exit is not None and not result.exception,
                     "project checker terminal status unavailable; inspect retained state")
        ledger._need(not result.stdout_truncated and not result.stderr_truncated,
                     "project checker output exceeded capture allowance")
        ledger._need(result.exit == 0, "project checker refused the project")
        ledger._need(math.isfinite(result.secs) and 0 <= result.secs <= timeout,
                     "project checker exceeded its deadline")
    ledger._need(before == gitws.workspace_witness(root, executable=cfg.git)
                 and authority == _authority_bytes(root)
                 and ledger_bytes == (root / cfg.ledger).read_bytes()
                 and identities == tuple(hashlib.sha256(Path(path).read_bytes()).digest()
                                         for path in (cfg.git, selected.executable, script)),
                 "project checker changed its read-only witness")
    ledger._need(clock() <= end, "project checker exceeded its deadline")


def _ownership_identity(root, executable):
    """Bind resolved Git directories, including replacement behind the same name."""
    paths = gitws._git(root, "rev-parse", "--git-dir", "--git-common-dir", "--show-toplevel",
                       executable=executable).stdout.splitlines()
    ledger._need(len(paths) == 3, "admission ownership unavailable")
    ledger._need(Path(os.fsdecode(paths[-1])).resolve(strict=True) == root,
                 "admission canonical root changed")
    result = []
    for raw in paths:
        path = (root / os.fsdecode(raw)).resolve(strict=True)
        info = path.stat()
        ledger._need(path.is_dir(), "admission ownership unavailable")
        result.append((path, info.st_dev, info.st_ino))
    return tuple(result)


@dataclass(frozen=True)
class _OwnedHistory:
    root: Path
    cfg: object
    model: object
    events: tuple
    state: object
    authority: tuple
    topology: tuple
    deadline: float
    clock: object

    def recheck(self, context, cfg, events):
        ledger._need(not (self.root / "STOP").exists(), "STOP exists; the owner must remove it to run")
        ledger._need(self.authority == _authority_bytes(self.root)
                     and self.topology == _ownership_identity(self.root, cfg.git),
                     "admission authority or ownership changed")
        ledger._need(ledger.read(self.root / cfg.ledger) == events, "admission history changed")
        context.recheck(self.root, cfg, events)


@contextmanager
def _owned_history(root, cfg, model, *, deadline, clock=time.monotonic, selected=False):
    """Read-only canonical history stage; keep ownership through the next effect."""
    from . import commit
    root = Path(root).resolve()
    if selected:
        ledger._need(not (root / "STOP").exists(), "STOP exists; the owner must remove it to run")
    ledger._need(cfg.ledger == commit.LEDGER, "runner admission requires the canonical ledger")
    cfg = replace(cfg, git=gitws.selected_executable(cfg.git))
    authority = _authority_bytes(root)
    topology = _ownership_identity(root, cfg.git)
    with writer_lock(root, model, executable=cfg.git):
        ledger._need(authority == _authority_bytes(root)
                     and topology == _ownership_identity(root, cfg.git),
                     "admission authority or ownership changed")
        ledger._need(model == roadmap_module.load(root / "roadmap.yaml"),
                     "admission roadmap differs from canonical authority")
        events = ledger.read(root / cfg.ledger)
        state = ledger.validate_history(root, events, model, executable=cfg.git)
        ledger._need(not any(a["finish"] is None for a in state.attempts.values()),
                     "unresolved invocation prevents admission")
        yield _OwnedHistory(root, cfg, model, events, state, authority, topology, deadline, clock)


def _select_historical(owned, selection):
    from . import _historical
    ledger._need(not (owned.root / "STOP").exists(), "STOP exists; the owner must remove it to run")
    context = _historical.load(owned.root, owned.cfg, owned.events, selection)
    owned.recheck(context, owned.cfg, owned.events)
    return context


def _admit_checked(owned, context=None):
    """Effects following the shared stage; context is local to its owned interval."""
    from . import commit
    root, cfg, model, events = owned.root, owned.cfg, owned.model, owned.events
    deadline, clock, state = owned.deadline, owned.clock, owned.state
    authority, topology = owned.authority, owned.topology
    if context is not None:
        owned.recheck(context, cfg, events)
    item = _admitted_item(model, state)
    selected = (receipt.native_runtime(root, model,
        render.task_contract(root, model, item, state.slices[item.id], events, executable=cfg.git), cfg)
        if item else runtime.resolve_runtime(root, model, git=cfg.git))
    selected = replace(selected, executable=gitws.selected_executable(
        selected.executable, label="selected Python"))
    try:
        _checker(root, cfg, selected, deadline=deadline, clock=clock)
    except (OSError, subprocess.SubprocessError):
        raise ValueError("project checker observation unavailable; inspect retained state") from None
    ledger._need(authority == _authority_bytes(root)
                 and topology == _ownership_identity(root, cfg.git),
                 "admission authority or ownership changed")
    if context is not None:
        owned.recheck(context, cfg, events)
    commits = commit.status(root, events, model, executable=cfg.git) if state.intents else ()
    if any(row["state"] == "pending" for row in commits) or commit._marker(root, executable=cfg.git).exists():
        if context is not None:
            owned.recheck(context, cfg, events)
        # A STOP that appeared during the checker still precedes any recovery effect.
        ledger._need(not (root / "STOP").exists(), "STOP exists; the owner must remove it to run")
        commit.recover(root, model, executable=cfg.git)
        # Recovery changes approved Git state, never authority or ledger history.
        ledger._need(authority == _authority_bytes(root), "recovery changed admission authority")
        model = roadmap_module.load(root / "roadmap.yaml")
        cfg = Config.load(root)
        ledger._need(cfg.ledger == commit.LEDGER, "runner admission requires the canonical ledger")
        cfg = replace(cfg, git=gitws.selected_executable(cfg.git))
        events = ledger.read(root / cfg.ledger)
        ledger.validate_history(root, events, model, executable=cfg.git)
    if context is not None:
        owned.recheck(context, cfg, events)
    return cfg, model, events


@contextmanager
def _owned_admission(root, cfg, model, *, deadline, clock=time.monotonic, historical_assessment=None):
    """Private pair-only entry; no caller-constructed eligibility context."""
    with _owned_history(root, cfg, model, deadline=deadline, clock=clock,
                        selected=historical_assessment is not None) as owned:
        context = (_select_historical(owned, historical_assessment)
                   if historical_assessment is not None else None)
        yield _admit_checked(owned, context)


UNTIL = frozenset({"slice", "milestone", "roadmap"})


def until_error(value):
    """Effective run boundary, including programmatic Config and explicit overrides."""
    if not isinstance(value, str) or value not in UNTIL:
        return f"until: must be one of {', '.join(sorted(UNTIL))}"
    return None


def selection_error(selection):
    """The public historical selection is the raw pair or nothing; never a context."""
    if selection is not None and not (type(selection) is tuple and len(selection) == 2
                                      and all(type(v) is str and v for v in selection)):
        return "historical assessment: selection must be one (path, raw_sha256) pair"
    return None


def canonical_errors(root, cfg, roadmap, *, config=True):
    """Public execution uses on-disk authority, not dataclass-only changes."""
    if roadmap != roadmap_module.load(root / "roadmap.yaml"):
        return ["admission roadmap differs from canonical authority"]
    if config:
        try:
            canonical = Config.load(root)
            canonical = replace(canonical, git=gitws.selected_executable(canonical.git))
        except (ValueError, OSError):
            canonical = None
        if canonical != replace(cfg, git=gitws.selected_executable(cfg.git)):
            return ["admission configuration differs from canonical project configuration"]
    return []


def _fresh_admissible(root, cfg, roadmap):
    """Probe only when the owned iteration could dispatch fresh work under the grant."""
    from . import commit
    if roadmap.autonomy is None:
        return False
    events = ledger.read(root / cfg.ledger)
    state = ledger.validate_history(root, events, roadmap, executable=cfg.git)
    try:
        _profile_gate(roadmap, state, commit.status(root, events, roadmap, executable=cfg.git)
                      if state.intents else ())
    except ValueError:
        return False
    return True


def _probe_plan(cfg, roadmap, state):
    """One probe per distinct configured seat identity on the current routes."""
    names, _ = _routes(roadmap, state)
    plan, checked = [], set()
    for name in sorted(names):
        seat = cfg.seats[name]
        key = (seat.adapter, seat.provider, seat.model)
        if key not in checked:
            checked.add(key)
            plan.append(name)
    return plan


def _probes(root, cfg, roadmap, names):
    errors = []
    for name in names:
        try:
            error = _probe(root, cfg, name, roadmap.memory is not None, _roadmap=roadmap)
            if error:
                errors.append(error)
        except _ProbeIntegrityError as exc:
            errors.append(str(exc))
            break
        except Exception as exc:  # noqa: BLE001 - A probe failure is a stable refusal.
            errors.append(f"model probe failed: {name}: {exc}")
    return errors


@contextmanager
def admission(root, cfg=None, roadmap=None, *, until=None, historical_assessment=None, probe=True):
    """Keep one ownership interval across static validation, probes and run effects.

    Recovery and terminal completion stay with the owned iteration. Preflight
    probes only when fresh dispatch is admissible under the current grant; a
    public run instead probes once immediately before its first model-seat dispatch.
    """
    root = Path(root).resolve()
    errors, selected = [], None
    before = _authority_bytes(root)
    supplied = cfg is not None
    try:
        cfg = cfg or Config.load(root)
    except (ValueError, OSError) as exc:
        errors.extend(str(exc).splitlines())
    try:
        roadmap = roadmap or roadmap_module.load(root / "roadmap.yaml")
    except (ValueError, OSError) as exc:
        errors.extend(getattr(exc, "errors", (str(exc),)))
    if roadmap is not None and roadmap.schema != ledger.ROADMAP_V2:
        errors.append("unsupported roadmap schema; use native /2 or pinned v4")
    if cfg is not None and not errors:
        errors.extend(e for e in (until_error(cfg.until if until is None else until),
                                  selection_error(historical_assessment)) if e)
    if not errors:
        try:
            cfg = replace(cfg, git=gitws.selected_executable(cfg.git))
        except ValueError as exc:
            errors.append(str(exc))
    if errors:
        yield Admission(cfg, roadmap, _errors(root, errors), None)
        return
    with ExitStack() as owned:
        names = []
        try:
            owned.enter_context(writer_lock(root, roadmap, executable=cfg.git))
            if _authority_bytes(root) != before:
                errors.append("admission authority or ownership changed")
            else:
                errors.extend(canonical_errors(root, cfg, roadmap, config=supplied))
            if not errors:
                selected, names, static = _static(root, cfg, roadmap, fresh=False)
                errors.extend(static)
            if not errors:
                state = ledger.validate_history(root, ledger.read(root / cfg.ledger), roadmap,
                                                executable=cfg.git)
                if any(a["finish"] is None for a in state.attempts.values()):
                    errors.append("unresolved invocation prevents admission")
            eligible = (not errors and probe and bool(names)
                        and _fresh_admissible(root, cfg, roadmap))
            if not errors and (historical_assessment is not None or eligible):
                deadline = time.monotonic() + cfg.max_wall_minutes * 60
                with _owned_history(root, cfg, roadmap, deadline=deadline,
                                    selected=historical_assessment is not None) as history:
                    context = (_select_historical(history, historical_assessment)
                               if historical_assessment is not None else None)
                    if eligible:
                        # The real project checker, under this same ownership, precedes any
                        # probe reservation; pure read/terminal admission runs no checker.
                        _admit_checked(history, context)
                        memory = roadmap.memory is not None
                        errors.extend(_probes(root, cfg, roadmap, [
                            name for name in names if not _probed(root, cfg, roadmap, name, memory)]))
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            errors.append(str(exc))
        yield Admission(cfg, roadmap, _errors(root, errors), None if errors else selected)


def prepare(root, cfg=None, roadmap=None, *, historical_assessment=None):
    with admission(root, cfg, roadmap, historical_assessment=historical_assessment) as admitted:
        return admitted.cfg, admitted.roadmap, admitted.errors


def preflight(root, cfg=None, roadmap=None, *, historical_assessment=None):
    return prepare(root, cfg, roadmap, historical_assessment=historical_assessment)[2]
