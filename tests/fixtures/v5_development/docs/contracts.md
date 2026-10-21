# Manual protocol 2 candidate

Manual use needs Python 3.11+, PyYAML and Git. frutlups 0.3.2 is unsupported;
versions imply no qualification; paired conformance/live canary remain pending.
`frutlups.roadmap/2` gates writers before issuance. Admit fresh native projects or
wholly native valid histories at settled boundaries only. Legacy/mixed roadmaps,
events or receipts refuse: use pinned v4. Selective upgrades inspect the entire
ledger/bound artifacts without converting or rewriting history.

## References, versions and exclusion

References `{path,sha}` require strict repository-relative POSIX regular files and
64 lowercase SHA-256 characters. Hashes normalize CRLF to LF only without NUL;
lone CR/binary bytes stay unchanged. CLI paths may normalize backslashes and one
leading `./`. Unknown keys refuse. JSONL `frutlups.ledger/2` requires `schema,t,ev,by`,
at most 2 MiB/event; attempt CLI bounds input before parsing. `t`: real UTC
`YYYY-MM-DDTHH:MM:SSZ`. Actors: human/architect/frutlups, with stricter rules below.
Slice/milestone IDs: `Mnnn-Snn`/`Mnnn`; rounds: positive integers, never booleans.

| Event | Required fields beyond schema/t/ev/by | Optional fields |
| --- | --- | --- |
| `prompt` | `slice,round,path,sha,envelope` | `baseline` |
| `coded` | `slice,round,changed,manifest,result` | `notes_path,notes_sha,outcome,seat,secs,tokens_in,tokens_out,cost_usd` |
| `verified` | `slice,round,receipt,sha,ok` | none |
| `reviewed` | `slice,round,report,sha,verdict,open` | `seat,secs,tokens_in,tokens_out,cost_usd` |
| `accepted` | `slice,round` | `commit,commit_intent` |
| `reopened` | `slice,round,reason` | none |
| `artifact` | `scope,round,role,path,sha` | none |
| `milestone_done` | `milestone` | `holistic_report,commit_intent` |
| `note` | `text` | `slice` |
| `stop` | `reason,detail` | none |

`baseline` and current `changed` contain exact `path,sha,kind` rows; kind is
`added|modified|deleted|renamed`. Text/seat fields are nonempty strings; `secs`
and `cost_usd` are nonnegative numbers, token counts nonnegative integers.
`ok` is boolean; `open` is a list of nonempty finding IDs. Verdicts are
`pass|needs_work|blocked|override`; override requires human. Optional `commit`
is 7–64 lowercase hexadecimal characters of attribution, not a commit witness.
`reopened` requires human/architect and `stop` requires frutlups.

Artifact role `review_prompt` uses slice scope and a numeric round;
`holistic_prompt` and `holistic_report` use milestone scope and round `holistic`.
These roles require architect/frutlups, except human may record `holistic_report`.
Role `evidence` supports either scope with its corresponding round and any common
actor. Holistic artifacts remain neutral; `milestone_done` requires the recorded
passing holistic decision and all slices accepted. `holistic_report` is a safe
relative path. The removed `unblocked` event refuses with an authority-bound
resolution diagnostic.

Writers (including `roadmap.py render`) require Git and lock byte 0 of
`<git-common-dir>/frutlups-writer.lock` through all artifact/ledger/Git effects:
Windows `msvcrt.locking(LK_NBLCK, 1)` or POSIX `flock(LOCK_EX|LOCK_NB)`. Lock-file
existence/content grants no authority. Process reentry is allowed; compatible
runners hold the lock for the whole run. Unlocked status may see a stable prefix.
Never kill another owner.

## Immutable envelopes and evidence

A `prompt` binds `envelope: {path,sha}`, schema `frutlups.envelope/2`. Review and
blocked resumption freeze scope, commands, findings and resolution; roadmap edits
cannot change issued acceptance. Exact keys:
`schema,slice,round,title,objective,acceptance,non_goals,read_first,allowed_prefixes,
forbidden,focused,full,runtime,timeout_seconds,observation,notes,findings,memory`.
`findings` includes prior findings/blocker/resolution context; materially changed
scope needs a new slice.

A `/2 coded` retains `changed` for the current round and adds `result`
(`implemented|blocked_authority|blocked_environment`) and `manifest: {path,sha}`.
The manifest is `frutlups.manifest/2`, with `slice`, `round`, and cumulative
`changed` rows containing `path,sha,kind,round`. Optional `notes_path` requires
`notes_sha`. Optional `outcome` is a reference. `artifact` role `evidence` is lifecycle-neutral. Bound references are immutable accepted-commit payload.

Reopens record current identities of owned paths changed by intervening slices,
including commits, without rewriting/reattributing history. Closure uses latest
globally recorded identities; old commit witnesses remain valid. Accepted paths
may match HEAD after owner hotfixes without extending old approval; holistic review
examines current evidence. Uncommitted/active-candidate drift refuses.

`frutlups.receipt/2` binds the cumulative `manifest`. Exact receipt keys:
`schema,slice,round,t,base_commit,tree_dirty_before,commands,tree_clean_after,ok,
manifest,witness,runtime,observation`. Base commit is the full Git ID; tree flags
and `ok` are booleans. `witness` keys: `before,after,stable,head,index,product`.
Before/after hash canonical complete snapshots; head is the observed commit;
index hashes staged entries/modes/flags; product hashes raw file/type/mode identities
excluding exact ledger/backlog, `roadmap.yaml`, `docs/roadmap.md` and bound evidence.
Later recorded artifacts cannot hide product drift. Exactly one command has
`label,argv,exit,secs,stdout_tail,stderr_tail,timed_out`: label `full`; argv matches
the frozen portable command; exit integer or null on launch failure; duration
finite/nonnegative; sanitized tails at most 4096 UTF-8 bytes each; timed_out boolean.
Runtime keys: `python,implementation,source,sha`; source invoking_python or
project.local; digest binds portable identity and frozen runtime declaration.
Observation matches the envelope. `ok` needs exit zero, no timeout and equal
snapshots, including already-dirty files. Git-owned tracked/untracked nonignored
files are witnessed; ignored scratch is not.

Coding/review prompts cap UTF-8 at 16/48 KiB including framing. Reference complete
manifests and bounded readable diff pages; inline source/config/tests before bulk
evidence. Label HEAD diffs filtered to current-round paths. Oversized mandatory
scope refuses without truncation. Preview uses the same renderer without
reservations, writes or events.

## Outcomes and resolution

Autonomous coding returns an outcome object, schema `frutlups.outcome/2`, bounded
to 16 KiB. Exact keys are `schema,slice,round,invocation,outcome,requirement,reason,
actor,action,remaining,evidence,authority`. `remaining` is a string list, `evidence`
is a reference list, `authority` contains relative decision/budget paths. The
three outcomes match `coded.result`. `requirement` and `reason` are nonempty;
blocked outcomes also require actor/action. Implemented work may use empty
actor/action. No outcome is inferred from enthusiastic prose.

Manual `coded` means implemented, with optional notes; zero changed files are
valid. `blocked` supplies blocker fields or a saved outcome. Both validate the
same edit fence; blocked work skips automatic verification. Human/architect-only
`resolved` has `scope,reason,authority` (bound references), advances the corrective
round and preserves blocker/resolution. Only recorded authority is granted, no
new model/domain budget. Autonomous format repair may read original output once,
never repeat writable coding or scientific commands.

## Commit intent and witness

`accepted` or `milestone_done` may add `commit_intent`, in the same approval
append: `{id,parent,manifest,message}`. No intent means ledger-only approval.
The operation id is 32 lowercase hexadecimal characters; parent is a full Git object id;
manifest is a bound `frutlups.commit-manifest/1` artifact. It pins `id,scope,parent,
index_tree,entries,ledger,git_boundary`; entries contain `path,state,sha,storage,mode`, state
`file|deleted`, storage `normalized|raw`. Rename origin is a deletion row. The
manifest and ledger are implicit eligible paths, avoiding a self-hash. Ledger
metadata pins its previous normalized prefix. Current working ledger bytes are
preserved; Git must store exactly that prefix plus the ordered approval event
under the existing normalized-text rule.

Completion is a Git commit with `Template-Operation: <id>`, exact parent, actual
approved delta, matching blobs/modes and ledger prefix through approval. Unchanged
eligible files need not appear in the delta. The trailer only locates a candidate.
Validate historical witnesses from committed blobs, not today's worktree/index.
Framework Git commands disable local replacement objects so pinned object IDs
always name their original stored content; replacement refs are left untouched.
Subsequent legitimate descendants do not invalidate earlier completion. Lookup is
bounded and refuses missing history, duplicate witnesses and unexplained movement.
Only fully validated witnesses count as duplicates; quoted trailers merely
identify candidates. Each blob is limited to 64 MiB. Aggregate payload has no
64 MiB ceiling: sizes are checked first and bytes fetched/hashed in batches
bounded to 64 MiB plus 1 KiB of framing. Metadata capture is bounded to 8 MiB.
Oversized individual blobs refuse.

Pending intent freezes other ledger writes. Recovery first recognizes an existing
witness, otherwise stages and commits only the pinned payload. No new approval,
review, coder call or completion append occurs. Unrelated staged entries and
content/index drift refuse without cleanup. An unresolved local Git dispatch
marker requires explicit process attribution unless its exact completed witness
is already proven; executable recovery can then archive the stale marker.
A stale filename/PID alone proves no process has ended.
`--git-resolved --reason ...` is mandatory human/architect attribution,
not permission to kill a process.

`commit_cancelled` has `operation,reason,retain_acceptance`, human/architect only,
after no witness and any in-flight Git action is resolved. Only human may retain
ledger-only acceptance. Otherwise the scope is reopened for new verification and
review. Cancellation preserves approval, evidence and index; it authorizes no
reset, cleanup, changed-payload approval or fabricated completion.

Optional `/2 verification.git_boundary` is a nonmutating argv hook receiving
`TEMPLATE_CANDIDATE_TREE`. It validates project-owned raw manifests against that
tree; snapshots before/after must match. The commit manifest freezes `git_boundary`
as `null` or `{argv,timeout_seconds,runtime}`, with a portable `runtime` declaration
(`{}` or `{python: "3.x[.y]"}`). Recovery refuses a changed hook, timeout or runtime
policy, including adding/removing the hook; use cancellation and a new review.
Completed historical witnesses retain their original policy. Choose scoped `-text` before hashing
raw data. Generic projects need no hook. Historical raw evidence remains frozen;
current derived output uses a project-owned scratch regeneration check instead.

Binary hashing checks the whole file for NUL. Git automatic text classification
guarantees no raw bytes: declare scoped `-text` before raw hashing, including mixed
CRLF/NUL, and verify materialized Git bytes. Never alter frozen manifests to hide
mismatches. With `core.filemode=false`, set/stage a new executable's intended Git
mode before verification/approval; recovery cannot change pinned modes. POSIX
executable-mode qualification remains platform-dependent.

## Reviews, findings and checkpoints

Finding/closure/verdict grammar is unchanged. Optional `## Finding updates` before
Closure Decision has `source | sha | id | disposition | related`: original report
path/hash, finding ID, existing disposition, comma-separated linked IDs (or `-`).
Updates never close linked findings implicitly. Unknown sources/IDs, contradictory
updates and collisions refuse. Unrelated passes close nothing. Every record path
checks human-only waiver authority. Only P3 may be carried. Unresolved findings
keep original scope despite missing slice prefixes or omission.

Backlog markers `<!-- findings:begin -->` / `<!-- findings:end -->` bound the
deterministic report projection. Preserve outside prose and old free-text notes.
`reconcile` repairs projection without rerunning reviews or changing history.

`holistic_reviewed` stores `milestone,report,sha,verdict,open`. Needs-work atomically
reopens grouped slices; blocked needs explicit resolution; pass stays `close_pending`
until `milestone_done`. Replaying a recorded report only completes its pending
mechanical close; no re-review/reopen. Manual selection stays flexible; autonomous
runs must pin/enforce their boundary.

Zero-active roadmaps retain nonempty shapes/status/ID rules and reopen priority;
no auto-activation or implied completion. Non-holistic `close`, under writer and
whole-history admission, rejects `--commit`, requires all slices accepted and
validates evidence/current integrity for any label. It confirms derived completion
and whether a done edit remains; no project/ledger/index/HEAD writes except lock
bookkeeping. [Q001](autonomy.md): admitted history requires active or fully completed
done with settled attempts, never planned.

`review_checkpoint` stores `invocation,scope,round,contract,report,sha,seat`.
`contract` hashes the complete envelope/product/baseline/prompt/route/seat/model/
effort/tools/parser binding. Reuse requires exact equality; changed bindings require
new review. Checkpoints preserve each routed report; a merge cannot erase them.

`attempt_started` stores `invocation,scope,round,role,contract,allowance_seconds,
retry` and optional `parent`; roles are coder/reviewer/holistic/probe, retries are
initial/transport/format. `attempt_finished` stores `invocation,status,usage` and
optional `reason,evidence`; status is completed/failed/resolved. Usage has only
known `secs,tokens_in,tokens_out,cost_usd` plus `completeness` (complete/partial/
unknown). Missing metrics are unknown, never zero. Invocation IDs are unique;
retry links must reference their prior invocation. An unfinished reservation
blocks new work even with a clean tree or lost local logs. Only human/architect
may resolve an unknown attempt, with reason and evidence. A format retry is
read-only; no writable coder is relaunched for formatting. Compatible runner
format repair keeps reviewer/holistic roles, or uses `probe` for a coder outcome;
it cannot turn that coder outcome into a review checkpoint. Compatible runner
admission/reservation limits remain runner-owned; ordinary manual use needs none
of these events.

The optional [autonomy profile](autonomy.md) adds gated native run grants,
aggregate reservation checks and a verification attempt role. Its declaration
is absent by default; existing `/2` attempts retain their base meaning without it.

## Qualification boundary

Synthetic tests specify local semantics, including old-runner rejection. Matching
runner, per-seat adapter capture, cross-mode interruption, persistent autonomous
budgets and exact-pair live canary remain F20/F40/F41 gates. Manual candidates are
not autonomous releases. Upgrades preserve verifiers, decisions, custom templates
and accepted ledgers.
