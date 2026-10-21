# Method

Progress means a more explicit, reviewable or correct artifact. Activity
alone is not evidence.

## Authorities

- `roadmap.yaml`: milestones, slices, acceptance, verification, risk, and write
  boundaries.
- `05_governance/ledger.jsonl`: append-only loop events and derived state.
- `00_brief/decisions.md`: accepted project decisions.
- `questions/`: durable blockers and their answers.

`docs/roadmap.md`, status/index output, prompts, coder notes, and review prose
are views or evidence. They do not replace their authority source.

## Roles

| Role | Positive responsibility and limit |
| --- | --- |
| Owner | Sets destination, priorities, external effects, waivers and delegated authority. |
| Architect / loop operator | Issues work, records evidence, routes review, accepts and makes authorized commits. |
| Coder | Changes the authorized product and reports implementation and verification facts. |
| Reviewer | Assesses contract evidence and findings; product stays read-only. Manual report writes need the named tool grant; autonomous reviewers return text for storage. |
| Optional frutlups runner | Performs authorized mechanical handoffs; automation adds no authority. |

One person may be owner and operator: issue as architect; record human-only
waivers as `human` with evidence. Neither role rewrites history; scheduling grants
no autonomous dispatch. [Commands](operating.md); [terms](glossary.md).

## One slice

1. The architect renders a coding prompt. Its successful write and hash are
   followed by a `prompt` event.
2. The coder implements within the effective prefixes, runs focused checks, and
   returns factual notes. Git—not those notes—produces the changed-file list.
3. The architect records `coded`. An out-of-bound change refuses the event and
   leaves the tree for a human; nothing is reverted automatically.
4. `verify.py` runs the declared full argv and writes a verification record (receipt). It appends
   `verified` whether the check passes or fails.
5. On success, a review prompt carries the receipt, cumulative changed-path
   manifest, current-round bounded diff, and optional notes to a read-only
   reviewer. A neutral `artifact` event records its immutable identity without
   changing lifecycle state.
6. `ledger.py record` validates the report and appends `reviewed`.
7. A pass becomes `accept_pending`. The architect accepts it, optionally making
   the exact acceptance commit. Needs-work or failed verification produces a
   new corrective round; blocked stops for the named owner. A human or architect
   records `/2` `resolved` with a reason and bound authority to start the next
   corrective round, preserving the blocker.

Manual and compatible autonomous operation share these files and transitions.
This `/2` candidate is manual-ready; paired conformance and mode switching still
require qualification. Old runners refuse its roadmap. Follow [upgrading](upgrading.md)
before transferring ownership, including pending attempts or Git operations.

## Frozen task contract (acceptance envelope)

Before issuance, the architect compares scoped owner sources/accepted decisions
with acceptance and verification: each duty needs an acceptance home and evidence
route or an authorized deferral, citing source locations; no new report schema.

Review the objective, acceptance, non-goals, read/write and verification boundaries,
safety rules and source/contract comparison. Route omitted owner duties to the
architect for authority correction; reviewers do not enlarge the envelope. Block
dependent acceptance when external authority is needed; respecify lawfully.
New wishes need a roadmap proposal. Refactored tests or removed assertions are
not inherently P2: require evidence of retained mandatory behavioral coverage.

Findings have stable ids, severity, disposition, and summary:

- P0: imminent safety, credential, destructive-authority, or data-loss risk.
- P1: incorrect behavior, broken required contract, or material architecture
  error; must be fixed before pass.
- P2: material bounded defect in evidence, compatibility, or maintainability;
  must be fixed before pass unless a human waives the exact finding.
- P3: clarity or deferred hardening that cannot falsely authorize acceptance;
  it may be carried to `backlog.md` with a pass.

Reviewers open/close findings; coders remediate/challenge, never self-close.
Only the human owner records `waived_by_human`; open P0-P2 preclude pass.
Each P0-P2 summary names obligation, location, conditions, evidence/argument and
material consequence. Distinguish observed defects from missing demonstrations.
Present maintainability costs can be material; speculative flexibility and
personal preference are nonblocking.

## Objective and verdict

Closure records `achieved`, `not_achieved` or `indeterminate` with one evidence
sentence; verdict assesses the envelope: `pass`, `needs_work` or `blocked`.
Pass needs evidence for every mandatory requirement and zero open P0-P2.
Failed requirements/missing mandatory evidence need needs_work/blocked by actor.
A required CLI missing cannot pass. An authorized negative
trial may pass execution/reporting with not_achieved, a predeclared inconclusive
trial with indeterminate. Explain this in Objective evidence; never call unknown
achieved. Syntax cannot prove evidence sufficiency.

The report contains one final section and one line:

```text
## Verdict
Verdict: pass|needs_work|blocked - next: <one move>
```

Human records may additionally use `override`; autonomous reviewers may not.

## Rounds and reopening

Rounds start at 1. Failed verification, needs-work or authorized resolution
advances the next prompt, carrying command tails, open findings and resolution
reason. Issuance baselines exact ledger-known same-slice state; --allow-dirty is
only for inspected unknown architect state. Transport retries need declared
allowance; unresolved invocation blocks work. Retries do not consume rounds.
Review open findings, the delta and evidence it invalidates.

Only the immediate continuation after the latest exact-slice resolution preserves
the blocked frozen contract. Issuance consumes it; preview does not. Changing
blocked scope needs a new slice; edited acceptance cannot replace that contract.
Ordinary needs-work reads the admitted roadmap. Before every ordinary corrective
issuance, compare the previous frozen task AND scoped owner baseline with the new
task: scope, non-goals, verification and critical-check ownership. Explain material
deletions/weakening and cite actor authority. A clean commit proves identity, not
human approval; retain lawful delegated architect decisions.

An accepted slice may be reopened only by a human or architect record with a
reason. Append `reopened` at the next round; never edit the earlier pass or
acceptance.

## Milestones

Roadmap status remains `planned`, `active`, or `done`; zero active milestones are
valid with nonempty milestones/slices and the existing shape rules. The architect
marks done status; that label creates neither acceptance nor a `milestone_done`
event. Status reports no active milestone as a planning fact, never project success.
Reopened work and pending-operation diagnostics remain visible. Manual selection
stays explicit; no work is automatically activated.

Preserving a superseded planned/blocked milestone is supported for manual history
without a grant for that milestone, including autonomy projects whose superseded
milestone never had a run. Q001 requires any ever-admitted milestone to stay active
or be provably completed done with settled attempts; closed/superseded/overrun runs
cannot become planned. Manual operation does not erase that restriction. Keep the
active marker and record a supersession decision without granting new dispatch or
allowance. Completed admitted done plus never-admitted planned work can have no
active milestone. General retirement of unfinished admitted work is excluded.
Next-slice selection prioritizes reopened work, then scans active milestones
in order, skipping those whose slices are all accepted. It does not activate
planned milestones or require holistic completion before considering the next
active milestone. The rendered roadmap displays each declared status.

A milestone's slices are accepted only through the ledger. If
`holistic_review: false`, all accepted slices make it done without a
`milestone_done` event. If true, a milestone review (`holistic`) must pass before that event
is recorded. A milestone-review P0-P2 finding id starts with the affected slice id. All
open findings for one slice produce one reopen reason containing every id; then
continue that slice's round sequence. Holistic prompt/report artifact events
preserve provenance across that reopen. Empty work or an absence of next eligible work is
not completion.

## Commits

Coders never commit unless the owner explicitly changes that rule. In `/2`,
`ledger.py accept <slice>` records ledger-only approval. With authorized
`--commit`, that same append binds the exact payload, parent and operation intent.
Other writes freeze until Git completion or attributed cancellation. A commit
proves completion through its validated `Template-Operation:` witness; no second
approval or completion append dirties the ledger. Interrupted work uses
`ledger.py recover`, then `recover --execute` for only its missing Git work.
Neither command pushes.

Ledger-only acceptance requires inspected manual housekeeping commits before a
new slice starts. Owner hotfixes committed after acceptance may match HEAD for
holistic review/close; they do not change what the earlier review approved.
Active review and acceptance remain pinned to the exact verified candidate.

Before committing, require passing verification and review, inspect status,
exclude secrets/local state, and inspect the exact staged paths. Pull requests,
front-repository publication, tags, and pushes require separate human authority.

## Planning uncertainty

Separate the project horizon, scheduled milestones and current run boundary.
Start with a toolchain smoke test using the intended tools; explicitly decide
whether its code is disposable. A spike answers an uncertain feasibility question
and still requires evidence. Prepare hermetic verification before the baseline,
schedule a real user-path check at integration, and budget in operational units.

`not_yet_specified` holds plausibly in-scope concerns not sharp enough to be a
slice or precise question. `ruled_out` holds accepted project exclusions. They
never enter next eligible work. Sharp externally blocked work belongs in
`questions/open/`; do not hide it as uncertainty. Changing the project
destination or resurrecting a ruled-out item needs human approval.
