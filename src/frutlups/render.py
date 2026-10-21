"""Pure rendering for coding, review, and holistic prompts."""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
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
HASH_RULE = ("`sha` in change manifests and bound artifact references means normalized evidence SHA-256: "
    "replace CRLF with LF only if no NUL occurs anywhere; preserve lone CR. With any NUL, hash all bytes unchanged. "
    "Exact-byte archives require raw SHA-256 and raw byte size. Embedded receipt witness/index/product digests keep their own meanings.")
HASH_EXPLANATION = "\n## Evidence hash rule\n\n" + HASH_RULE + "\n"
CODING_LIMIT = 16384
REVIEW_LIMIT = 49152
SUPPORT_LIMIT = 128 * 1024
# Exact extra dispatch bytes; adapters and reservation binding share this owner.
PI_FRAMING = "Execute the attached prompt."
MAIN_FRAMING = (
    "Manage your helpers within this task's scope and role. Before your final "
    "handoff, await every background task whose output or edits the task needs. "
    "Inspect its outcome, finish or report any unresolved work, and stop temporary "
    "support processes you started. A failed exploration may be recovered within "
    "the task authority; do not repeat a failed designated final gate without the "
    "required reassessment. Do not present still-running required work as done."
)


def dispatch_framing(adapter):
    return MAIN_FRAMING + "\n\n" + PI_FRAMING if adapter == "pi" else "\n\n" + MAIN_FRAMING


def reference_text(ref):
    return f"`{ref['path']}` (normalized evidence SHA-256 `{ref['sha']}`)"


def prompt_size(text, adapter="claude"):
    return len(text.encode("utf-8", "strict")) + len(dispatch_framing(adapter).encode())


def require_prompt(text, limit, adapter="claude"):
    if prompt_size(text, adapter) > limit:
        raise RenderError(f"complete prompt exceeds {limit // 1024} KiB byte bound; mandatory instructions cannot be truncated")
    return text


def _native_prompt(custom, mandatory, limit, framing=""):
    mandatory += HASH_EXPLANATION
    if prompt_size(custom + mandatory + framing, "pi") <= limit:
        return custom + mandatory
    fallback = "Custom/inline presentation omitted by the byte bound. Read the complete bound evidence below.\n"
    refs = _complete_references(custom)
    candidate = fallback + ("\n".join(refs) + "\n" if refs else "") + mandatory
    require_prompt(candidate + framing, limit, "pi")
    return candidate


def _complete_references(text):
    # Preserve native historical labels as well as new labels without rehashing.
    return list(dict.fromkeys(re.findall(
        r"`[^`\n]+` \((?:normalized evidence SHA-256|sha256) `[a-f0-9]{64}`[^)\n]*\)", text)))


FINDING_UPDATES = (
    "Optional ## Finding updates follows Findings and precedes Closure Decision. "
    "Use exactly | source | sha | id | disposition | related | with a five-column separator. "
    "Each row binds the original report path, normalized evidence SHA-256 and finding ID; "
    "disposition is open|closed_by_review|carried|waived_by_human; related is comma-separated "
    "finding IDs or -. Autonomous seats cannot use waived_by_human. Only P3 may be carried. "
    "Explicitly close relevant earlier P0-P2 before pass; related IDs close nothing implicitly. "
    "Unknown sources, duplicate or contradictory updates and ambiguous related IDs refuse.\n")

REVIEW_GUIDANCE = (
    "\nReturn the report text only; the runner saves it. Do not write files or run verification. "
    "For each finding state the obligation, location, conditions, evidence and consequence. "
    "Keep objective status separate from verdict. No overrides or human waivers; only P3 may be carried.\n"
    "Include ## Findings with | id | severity | disposition | summary |; "
    "## Closure Decision with Objective status: achieved|not_achieved|indeterminate and Objective evidence:; "
    "and exactly one Verdict: pass|needs_work|blocked - next: <one move>.\n" + FINDING_UPDATES)

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
    return _render_template(path, values, optional, coding_v2=True)


def _render_template(path, values, optional, *, coding_v2, review_v2=False):
    with path.open("rb") as stream:
        raw = stream.read(SUPPORT_LIMIT + 1)
    if len(raw) > SUPPORT_LIMIT:
        raise RenderError("template exceeds 128 KiB input bound")
    text = raw.decode("utf-8").replace("\r\n", "\n")
    found = set(re.findall(r"{{([a-z_]+)}}", text))
    known = KNOWN | {"notes", "outcome_contract"} if coding_v2 else KNOWN
    if review_v2:
        known = known | {"notes", "finding_updates"}
    unknown = found - known
    if unknown:
        raise RenderError(f"unknown placeholders: {sorted(unknown)}")
    bare = re.sub(r"{{[a-z_]+}}", "", text)
    if "{{" in bare or "}}" in bare:
        raise RenderError("unresolved placeholder")
    for heading, key in optional:
        if not values.get(key):
            pattern = rf"\n## {re.escape(heading)}\n.*?(?=\n## |\Z)"
            text = re.sub(pattern, "", text, flags=re.DOTALL)
    # Bound expansion before allocating repeated custom placeholders.
    parts, cursor, size = [], 0, 0
    for match in re.finditer(r"{{([a-z_]+)}}", text):
        literal, replacement = text[cursor:match.start()], str(values.get(match[1], ""))
        size += len(literal.encode("utf-8")) + len(replacement.encode("utf-8"))
        if size > SUPPORT_LIMIT:
            refs = _complete_references(text)
            for entry in values.values():
                refs.extend(_complete_references(str(entry)))
            return "[Inline expansion omitted; read complete bound authority.]\n" + "\n".join(dict.fromkeys(refs))
        parts.extend((literal, replacement))
        cursor = match.end()
    parts.append(text[cursor:])
    text = "".join(parts)
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
    def read(relative):
        path = repo_path(root, relative)
        with path.open("rb") as stream:
            raw = stream.read(SUPPORT_LIMIT + 1)
        if len(raw) > SUPPORT_LIMIT:
            raise RenderError("finding support exceeds 128 KiB read bound")
        return raw.decode("utf-8")
    rows = ()
    if state.last_report and state.open_findings:
        rows = finding_rows(read(state.last_report), state.open_findings)
    if state.reopen_reason:
        rows += (f"Reopen reason: {state.reopen_reason}",)
        if state.reopen_report:
            ids = tuple(re.findall(rf"{state.id}-[\w-]+", state.reopen_reason))
            report = read(state.reopen_report)
            rows += finding_rows(report, ids)
    rows += (f"Unblock reason: {state.unblock_reason}",) if state.unblock_reason else ()
    return "\n".join(rows)




def resumes_contract(events, sid):
    """Validated event order alone selects an unconsumed exact-slice resolution."""
    from . import ledger
    for event in reversed(events):
        if event.ev == ledger.Ev.prompt and event.slice == sid:
            return False
        if event.ev == ledger.Ev.resolved and event.data["scope"] == sid:
            return True
    return False


def task_contract(root, roadmap, item, state, events, *, executable="git"):
    """Select before issuance; afterwards consume the exact issued envelope."""
    from . import ledger
    ledger.validate_history(root, events, roadmap, executable=executable)
    if state.step in ("unstarted", "fix"):
        return coding_envelope(root, roadmap, item, state, events, executable=executable)
    ref = ledger._thaw(state.envelope)
    return ledger.envelope(root, ref, item.id, state.round)


def coding_envelope(root, roadmap, item, state, events=None, *, executable="git"):
    from . import ledger
    if roadmap.schema != ledger.ROADMAP_V2:
        raise RenderError("unsupported roadmap schema; use native /2 or pinned v4")
    events = ledger.read(root / "05_governance/ledger.jsonl") if events is None else tuple(events)
    ledger.validate_history(root, events, roadmap, executable=executable)
    resuming = resumes_contract(events, item.id)
    context_state = (replace(state, unblock_reason=None)
                     if not resuming and state.resolution == state.unblock_reason else state)
    context = _findings_context(root, context_state)
    value = dict(schema="frutlups.envelope/2", slice=item.id, round=state.round,
                 title=item.title, objective=item.objective.strip(),
                 acceptance=list(item.acceptance), non_goals=list(item.non_goals),
                 read_first=list(item.read_first),
                 allowed_prefixes=list(effective_prefixes(roadmap, item)),
                 forbidden=list(roadmap.forbidden),
                 focused=[list(a) for a in (item.focused or roadmap.focused_default)],
                 full=list(item.verification or roadmap.verification_full),
                 runtime=dict(roadmap.runtime), timeout_seconds=roadmap.verification_timeout_seconds,
                 observation=roadmap.verification_observation, notes=(item.notes or "").strip(),
                 findings=context, memory=_memory(roadmap, item))
    if resuming:
        issued = next(e for e in reversed(events) if e.ev == ledger.Ev.prompt and e.slice == item.id)
        value = ledger.envelope(root, ledger._thaw(issued.data["envelope"]), item.id, issued.round)
        value.update(round=state.round, findings=context)
    for relative in dict.fromkeys(p for p in (state.last_report, state.reopen_report) if p):
        value["findings"] += "\nOriginal finding source: " + reference_text(dict(
            path=relative, sha=ledger._file_sha(repo_path(root, relative))))
    if state.blocker:
        value["findings"] += ("\nOriginal blocker: " if resuming else "\nHistorical blocker: ") + reference_text(ledger._thaw(state.blocker))
        if state.manifest:
            value["findings"] += "\nRetained changes: " + reference_text(ledger._thaw(state.manifest))
        if state.notes_path:
            value["findings"] += "\nPrevious notes: " + reference_text(dict(
                path=state.notes_path, sha=ledger._file_sha(repo_path(root, state.notes_path))))
    if state.resolution:
        value["findings"] += ("\nRecorded resolution: " if resuming else "\nHistorical resolution: ") + str(state.resolution)
    for event in events:
        if event.ev == ledger.Ev.resolved and event.data["scope"] == item.id:
            label = "Resolution authority: " if resuming else "Historical resolution authority: "
            value["findings"] += "\n" + label + ", ".join(reference_text(ref) for ref in event.data["authority"])
    return value


def coding_v2(root, envelope, state, envelope_ref=None):
    values = {"slice_id": envelope["slice"], **envelope}
    for key in ("acceptance", "non_goals", "read_first"):
        values[key] = _bullets(envelope[key])
    for key in ("allowed_prefixes", "forbidden"):
        values[key] = ", ".join(envelope[key])
    for key in ("focused", "full"):
        values[key] = _argv(envelope[key])
    values["open_findings"] = envelope["findings"]
    values["outcome_contract"] = "Return the typed outcome bound to the invocation supplied at dispatch."
    text = _render_template(_template(root, "coding_prompt.md"), values, (), coding_v2=True)
    mandatory = "\n## Complete frozen coding envelope\n\n"
    if envelope_ref:
        mandatory += ("Read this complete canonical envelope before work: " + reference_text(envelope_ref)
                      + ". All scope, commands, runtime, notes, findings, memory and resolution remain binding.\n")
    else:
        mandatory += json.dumps(envelope, ensure_ascii=False, sort_keys=True) + "\n"
    if state.last_receipt:
        from . import ledger
        mandatory += "Prior verification receipt: " + reference_text(dict(
            path=state.last_receipt, sha=ledger._file_sha(repo_path(root, state.last_receipt)))) + "\n"
    mandatory += "Return the typed outcome bound to the invocation supplied at dispatch.\n"
    return _native_prompt(text, mandatory, CODING_LIMIT,
                          outcome_contract(envelope["slice"], envelope["round"], "0" * 32))


def outcome_contract(sid, round_no, invocation):
    value = dict(schema="frutlups.outcome/2", slice=sid, round=round_no,
                 invocation=invocation, outcome="implemented", requirement="<requirement>",
                 reason="<observed result>", actor="", action="", remaining=[], evidence=[], authority=[])
    return ("\n## Autonomous outcome\n\nReplace the manual Finish response with exactly one JSON object, "
            "at most 16384 UTF-8 bytes, with these exact fields and invocation binding:\n```json\n"
            + json.dumps(value, ensure_ascii=False) + "\n```\n"
            "outcome is implemented, blocked_authority, or blocked_environment. Blockers require "
            "nonempty actor/action. remaining is a string list; evidence is a list of {path,sha} "
            "references; authority lists safe relative decision/budget paths. Preserve meaningful "
            "edits; never invent authority, evidence, completion, or a budget.\n")


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




def review_v2(root, frozen, support, report_path, support_ref=None):
    """Keep custom rendering, with complete frozen authority and bound support."""
    values = {"slice_id": frozen["slice"], **frozen}
    for key in ("acceptance", "non_goals", "read_first"):
        values[key] = _bullets(frozen[key])
    for key in ("allowed_prefixes", "forbidden"):
        values[key] = ", ".join(frozen[key])
    for key in ("focused", "full"):
        values[key] = _argv(frozen[key])
    def refs(key):
        ref = support.get(key)
        return reference_text(ref) if isinstance(ref, dict) and set(ref) == {"path", "sha"} else json.dumps(ref, ensure_ascii=False, sort_keys=True)
    values.update(diff_manifest=refs("manifest"), diff_evidence=("Read the complete path index: " + refs("index") if support_ref
                                                 else "Inspect the complete manifest paths locally."),
                  coder_notes=refs("notes"), receipt=refs("receipt"), prior_findings=frozen["findings"],
                  finding_updates=FINDING_UPDATES,
                  report_path=report_path, finding_id_rule=f"Start every finding ID with `{frozen['slice']}-`.")
    text = _render_template(_template(root, "review_prompt.md"), values, REVIEW_OPTIONAL,
                            coding_v2=False, review_v2=True)
    mandatory = (f"\n# Review: {frozen['slice']} round {frozen['round']}\n"
        + values["finding_id_rule"] + "\n## Complete frozen review authority\n")
    if support_ref:
        mandatory += "Read all bound evidence: " + reference_text(support_ref) + "\n"
        mandatory += "Frozen envelope: " + reference_text(support["envelope"]) + "\n"
        mandatory += "Canonical manifest: " + reference_text(support["manifest"]) + "\n"
        mandatory += "Canonical receipt: " + reference_text(support["receipt"]) + "\n"
        mandatory += "Complete path index: " + reference_text(support["index"]) + "\n"
    else:
        mandatory += json.dumps(frozen, ensure_ascii=False, sort_keys=True) + "\n"
        mandatory += json.dumps(support, ensure_ascii=False, sort_keys=True) + "\n"
    return _native_prompt(text, mandatory + REVIEW_GUIDANCE, REVIEW_LIMIT)


def holistic_v2(root, milestone, support, support_ref=None):
    values = dict(slice_id=milestone.id, title=milestone.title, round="holistic",
        objective=f"Judge holistic closure of {milestone.id} across all accepted frozen contracts.",
        acceptance=_bullets([f"{row['contract']['slice']}: {item}" for row in support["accepted"]
                             for item in row["contract"]["acceptance"]]),
        diff_manifest=(reference_text(support["index"]) if support_ref else json.dumps(support["latest"], sort_keys=True)),
        diff_evidence="Read the complete bound manifests and inspect their paths locally.",
        coder_notes="", receipt=_bullets([reference_text(row["receipt"]) for row in support["accepted"]]),
        prior_findings=json.dumps(support["cycle"], sort_keys=True), report_path="Runner retains original output.",
        finding_id_rule="Every P0-P2 finding ID must start with its exact affected slice ID followed by '-': "
            + ", ".join(s.id for s in milestone.slices),
        finding_updates=FINDING_UPDATES)
    text = _render_template(_template(root, "review_prompt.md"), values, REVIEW_OPTIONAL,
                            coding_v2=False, review_v2=True)
    mandatory = (f"\n# Review: {milestone.id} round holistic\n"
        + values["finding_id_rule"] + "\n## Complete frozen holistic authority and bound evidence\n")
    if support_ref:
        mandatory += "Read all accepted contracts, canonical references and original row provenance: " + reference_text(support_ref) + "\n"
        mandatory += "Complete path index: " + reference_text(support["index"]) + "\n"
    else:
        mandatory += json.dumps(support, ensure_ascii=False, sort_keys=True) + "\n"
    return _native_prompt(text, mandatory + REVIEW_GUIDANCE, REVIEW_LIMIT)
