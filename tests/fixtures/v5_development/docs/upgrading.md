# Upgrading and operating the manual candidate

Manual `/2` operation requires Python 3.11+, PyYAML and Git. Package numbering
does not assert runner compatibility; the exact pair needs qualification.

| Project and tools | Supported claim |
| --- | --- |
| Legacy roadmap, ledger prefix, mixed history or receipt | Unsupported by these tools; continue with pinned v4. |
| `/2` project with these manual scripts | Manual candidate; use the locally recorded qualification and limitations. |
| `/2` project with frutlups 0.3.2 | Unsupported. The new roadmap schema is the static refusal gate; do not bypass it or downgrade the schema to make an old runner proceed. |
| `/2` project with a future matching runner | Pending paired conformance, offline manual/autonomous switching and final live canary; no compatible released version is asserted here. |

## New projects

Start from a reviewed export. Replace example brief/roadmap facts, prepare the
project interpreter and configure the project-owned full verifier. From the
extracted directory, run `python scripts/roadmap.py check`, then `git init`,
`git add --all` and `git commit -m "Initialize project"` (configure your Git
name/email if needed). Now run `python scripts/roadmap.py render` and perform
the ordinary prompt → coder → verification →
review → acceptance sequence. The empty shipped verifier fails intentionally.
Use `docs/project_checks.md` only for the runtime, user-path, byte policy or
research boundaries the project actually has.

`git archive HEAD` contains committed files only; select the reviewed candidate
explicitly. Publication and runner installation remain owner actions.

## Existing projects

1. Before replacing tools, inspect the entire roadmap, retained ledger and every
   required referenced artifact with the pinned existing tools. Only a native
   `/2` roadmap and completely native valid history are eligible. A current
   roadmap label, latest event or clean Git status alone proves no eligibility.
   A legacy roadmap with an empty ledger requires separately authorized native
   setup; it is not silently reinterpreted.
2. Settle issued rounds, blockers, unfinished invocations, pending commit intents
   and Git ownership with their matching tools. Keep a recoverable owner-approved
   checkpoint. Do not replace tools underneath live work or unresolved recovery.
3. In a separately scoped selective upgrade, preserve the real roadmap, brief,
   decisions, reports, ledger, product settings, dependencies, project verifier
   and custom prompt content. Never replace a verifier with the empty scaffold.
   Preserve raw-byte `-text` scopes and export exclusions; do not rehash frozen
   evidence or weaken storage policy. Merge runtime support deliberately.
4. With the selected interpreter, check roadmap and complete ledger/artifacts,
   preview actual custom prompts, and qualify a disposable project and the
   maintained project's full verifier before continued work. Keep incompatible
   runners disabled until the exact pair passes its separate qualification.

There is no conversion, relabeling, fallback reader, archived-ledger reset or
attempt reattribution. Legacy and mixed projects stay on pinned v4. Preserve
historical bytes, findings and run balances; an upgrade cannot manufacture fresh
admission to reset retries or accounting. Native nonterminal run continuation
needs explicit unchanged bindings and no in-flight work; prefer settled runs.
Independently versioned current schemas such as `frutlups.autonomy/1`,
`frutlups.commit-manifest/1` and `template.local/1` remain valid and unchanged.
All writers require the shared Git lock. Finding closure is source/path/hash/ID
bound and waivers require human authority, including historical native reports.

## Exceptional commands

Ordinary `coded <slice>` means implemented work, including zero changes;
`--notes <path>` optionally binds the manual handoff. Preserve blocked edits.
For a short manual blocker, avoid hand-authoring JSON:

```powershell
python scripts/ledger.py blocked M001-S01 --requirement "acceptance 1" --reason "Allowance absent" --actor human --action "Record allowance"
```

Use `--result blocked_environment` for an environment blocker. Repeatable
`--remaining`, `--evidence` and `--authority` identify actions, evidence files
and relative decision/budget paths. `--outcome <file.json>` supplies a saved
outcome instead; exact grammar is in [contracts](contracts.md). A blocker stops
before verification. Human/architect resumption uses:

```text
python scripts/ledger.py resolve M001-S01 --reason "authority now recorded" --authority 00_brief/decisions.md
```

The same verb resolves a blocked milestone. It preserves the original frozen
acceptance and grants no undeclared budget. Materially changed scope belongs in
a new slice.

`accept <slice>` is ledger-only. Inspect and manually commit accepted products
and loop evidence before the next slice. If the original approval requested
`--commit`, use `ledger.py recover` to diagnose, then `recover --execute` to
complete only missing exact Git work. Never repeat approval. An exact completed
witness permits executable recovery to archive its stale marker automatically.
A stale filename/PID alone is insufficient.

For unresolved Git ownership, first establish that the owned process ended,
then use `recover --git-resolved <operation> --reason ... --by human|architect`.
An unfinishable intent uses `cancel <operation> --reason ... --by human|architect`;
add `--git-resolved` to combine explicit process attribution with cancellation.
Only human may add `--retain-acceptance` to keep ledger-only approval; otherwise
the scope reopens. Cancellation preserves evidence, files and index and grants
no cleanup authority. Unknown model/probe reservations use the separate
[`ledger.py attempt` examples](operating.md#recovery).

Record holistic reports with `record <report> --milestone M001`. A `/2` pass
persists `close_pending`; `close M001` completes closure, optionally with an
authorized `--commit`. Blocked requires resolution; needs-work reopens affected
slices. Manual selection of another active slice remains architect authority.

`ledger.py reconcile` rebuilds only the backlog's marked region. Preserve
outside prose. `/2` closures are explicit and source-bound; only human may waive
findings. See [operating](operating.md) for the ordinary loop and crash recovery.

## Qualification handoff

Record tested identities and limits; local tests do not qualify adapters,
T40/F40 mode switching or F41. Upgrading grants no paid work or publication.
The optional [autonomy profile](autonomy.md) has its own strict gate and
pre-admission history restrictions; ordinary manual projects need no opt-in.
