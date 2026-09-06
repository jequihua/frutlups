# Review: M004-S01 round 2

## Findings
| id | severity | disposition | summary |
| --- | --- | --- | --- |
| M004-S01-01 | P2 | closed_by_review | `qualification.md:250-259` now attributes the coder shares (0.03901 and 0.010123 USD) to each job's local `result.json` and cites `commands_and_versions.txt:62-69`, which records exactly those values and the sums 0.104768 + 0.03901 = 0.143778 and 0.1488335 + 0.010123 = 0.1589565 matching `status_usage.txt:5-6`; the arithmetic checks and the M001-S02 share is marked last-message only (D022). |
| M004-S01-11 | P2 | closed_by_review | Same gap as -01: the document states the `coded` rows carry no `cost_usd` and names the cited evidence source for the coder contributions, so the cost composition is no longer silent or unsourced. |
| M004-S01-21 | P2 | closed_by_review | Same gap as -01: the delta is stated with its source inside `docs/qualification/`; the document does not reach outside the evidence set for the M001-S02 last-message cost. |
| M004-S01-03 | P3 | closed_by_review | `samples/README.md:17` now names Pi 0.85.0 for `pi_canary_usage.jsonl`, linked to `commands_and_versions.txt:6`; the relative link resolves from the samples folder. |
| M004-S01-22 | P3 | closed_by_review | Wall-time rows for run 2a and run 3 now give the ledger-timestamp intervals (20 s from `ledger.jsonl:14-15`, 56 s from `ledger.jsonl:23-24`) and run 3 cites the 56.208 s owner record at `commands_and_versions.txt:66-67`. |
| M004-S01-23 | P3 | carried | Process-survival section still quotes only the six `node.exe` rows; the two `python.exe` and two `claude.exe` rows at `tasklist_after_kills.txt:5, 7, 13, 18` remain unmentioned. Acceptance asks only about node and pi, so this stays backlog quality. |
| M004-S01-25 | P3 | carried | `commands_and_versions.txt:66-67` records an M001-S03 coder cost of 0.012919 USD that appears in no status row, but `qualification.md:271-280` cites lines 66-67 only for the 56.208 s wall time and the usage section leaves M001-S03 at the status `?` values without noting the recorded coder figure; the "not observed" statement about a total over every attempted job stays true because the 1a/1b review and timed-out attempts have no exact totals. |

## Closure Decision
Objective status: achieved
Objective evidence: Every claim rechecked in qualification.md matches its cited line under docs/qualification/ (verbatim six ledger rows and command block, decoded status lines round-trip, token and cost sums reconcile), the README meets its acceptance clause, and the receipt shows the full hermetic run at exit 0 with 287 passed over the two changed files with matching sha256.

## Verdict
Verdict: pass - next: architect records the review and accepts M004-S01
