# Glossary

Optional reference; start with [operating](operating.md) for the manual loop.
Human-facing names below do not rename CLI, schema or report tokens.

| Term | Meaning and preserved spelling |
| --- | --- |
| Slice | Bounded reviewable change; not necessarily a vertical feature or one session. |
| Milestone / round | A planned group of slices / one implementation and review cycle. Corrective rounds retain relevant prior evidence. |
| Derived state | State obtained by replaying ledger events; code still uses `fold`. |
| Agent role / configured agent | Responsibility / its particular model and tools. Keep `seat`, `[seats.*]`, routing names and `FRUTLUPS_SEAT`. |
| Milestone review | Review across accepted slices; commands/data retain `holistic`, `--holistic`, `holistic_review`, filenames and report grammar. |
| Frozen task contract | Acceptance envelope bound at issuance, including objective, acceptance, boundaries and verification; its schema and identity remain exact. |
| Verification record | Receipt of command execution, not proof that tests are sufficient; receipt keys/schema stay exact. |
| File / change / commit manifest | Different inventories with their own fields. Change-manifest `sha` is normalized evidence SHA-256; see [hash rules](ledger.md#hash-rules). |
| Evidence snapshot / completion commit | A workspace witness records contents/types/index/HEAD. A validated completion commit proves the exact Git operation; these are different witnesses. |
| Next eligible work | Work selected by existing order and run boundaries; an empty frontier is not completion. |
| Schedule / authorize for execution | Planning work differs from a bound autonomous run grant. Keep `run_admitted`. |
| Recorded actor and reason | Attribution, not authentication of a malicious writer. |
| Toolchain smoke test / spike | A bounded tool check / investigation of uncertain feasibility. A spike needs credible evidence and safety controls; disposable code is an explicit decision. |
| Bounded live integration trial | A defined canary; neither every smoke test nor automatically a production rollout. |
| Native protocol 2 / legacy protocol 1 | Lifecycle versions. Independent current schemas such as `frutlups.autonomy/1` remain current; `/1` alone does not mean obsolete. |
| Architect / loop operator | Issues work, records evidence and accepts within owner authority. Actor token stays `architect`; see [roles](method.md#roles). |
| frutlups / llloom | Optional autonomous runner / optional project memory tool. Neither is required for the ordinary manual loop. |
| Finding / disposition | A review observation with stable identity / its explicit closure status. A coder cannot self-close; only human may waive. |
| Blocker / resolution / reopen | Stopped work needing another actor / recorded new authority or evidence / authorized correction of accepted work. Preserve history. |
| Commit intent | Bound approval for exact Git work, recoverable without repeating approval. |
| Acceptance criteria / definition of done | Task-specific required results / standing completion bar; neither is merely an activity list. |
| Invariant / regression test | Property that must remain true / check that detects loss of required behavior. |
| Non-goal | Explicit scope exclusion, not permission to omit required safeguards. |

Refactoring preserves externally observable behavior; tests may change when
coupled to altered internals. YAGNI excludes unsupported future capability, not
needed verification or simplification justified by present code. Idempotence
describes repeated effects under stated conditions, not universally safe retries.
Hardening names a failure mode; optimization names a measured target; polish
must not conceal behavior changes. A pilot can expose ambiguity and effort,
but cannot prove general productivity or justify deleting unused recovery.
Optional features can be removed from an instantiated project only through a
scoped dependency/retention decision; product delivery need not include its
development harness. See [method](method.md) and [compatibility](upgrading.md#compatibility).
