# Real-seat canary qualification

## Scope and recorded environment

The owner's record covers frutlups-canary on 2026-09-05, Windows 10 and
PowerShell 5.1; recorded times are UTC. It describes a v4 template export with
framework tests removed and three word-counter slices under `07_app/`.
[commands_and_versions.txt:1-2, 11-12](qualification/commands_and_versions.txt#L1-L12)

| Component | Recorded version / seat | Evidence |
| --- | --- | --- |
| frutlups | 0.3.0a0, editable install from `08_pkg/frutlups` in a dedicated venv | [commands_and_versions.txt:5](qualification/commands_and_versions.txt#L5) |
| Pi | 0.85.0; coder `openai-codex gpt-5.6-sol`, medium effort | [commands_and_versions.txt:6](qualification/commands_and_versions.txt#L6) |
| Claude Code | 2.1.226; reviewer `opus`, medium effort | [commands_and_versions.txt:7](qualification/commands_and_versions.txt#L7) |
| Python / pytest | 3.14.3 / 9.0.2 | [commands_and_versions.txt:8](qualification/commands_and_versions.txt#L8) |
| Git | Git for Windows; numeric version not observed | [commands_and_versions.txt:8](qualification/commands_and_versions.txt#L8) |

The recorded configuration uses `until = slice`, one corrective round, six jobs,
60 wall minutes, and `commit_on_accept = true`. Budgets are coder 900 s
(20 s for the forced timeout), reviewer 600 s, and verification 300 s. Ordinary
reviews route to Claude; `holistic_review` is false.
[commands_and_versions.txt:12-16](qualification/commands_and_versions.txt#L12-L16)

## Exact commands and manual recovery

The following is the owner's command record, quoted verbatim. Commands were
issued from the canary root using the venv's `Scripts\frutlups.exe`; bracketed
entries describe hand actions, rather than executable commands.
[commands_and_versions.txt:20-40](qualification/commands_and_versions.txt#L20-L40)

```text
  frutlups preflight                              -> "preflight: ok" (first attempt, 2026-09-05 ~16:40)
  frutlups status                                 -> three slices unstarted
  frutlups run --until slice                      -> run 1a, 17:27, stop seat_output (see run_01_ordinary.txt)
  frutlups run --until slice                      -> run 1b, 17:42, same stop; transcript overwritten later, ledger rows 7-9
  [frutlups-dev: M001-S04 reopened and accepted, fenced-report fix, D021]
  [canary: prompts/templates/review_prompt.md wording change committed by hand]
  frutlups run --until slice                      -> "preflight: unrecorded change: prompts/templates/review_prompt.md"
                                                     (before that commit; transcript overwritten by the next run)
  frutlups run --until slice                      -> run 1c, 18:47, M001-S01 accepted, commit f0c6227
  [canary: frutlups.toml coder_seconds = 20 committed]
  frutlups run --until slice                      -> run 2a, 18:50, stop seat_transport after one 20 s kill
  tasklist                                        -> tasklist_after_kills.txt
  [canary: git checkout -- 07_app/canary/wordcount.py by hand; D004 committed, budget mistakenly left at 20]
  frutlups run --until slice                      -> run 2b, 18:57, same stop, same leftover file
  [canary: git checkout -- 07_app/canary/wordcount.py by hand; coder_seconds = 900 committed]
  frutlups run --until slice                      -> run 2c, M001-S02 accepted, commit c4da6ca
  frutlups run --until slice                      -> run 3, 19:37, stop path_violation
  frutlups status --usage                         -> status_usage.txt
  [canary: file restored and untracked test removed by hand; ledger and prompt 003 committed; pushed]
```

The record places the template-edit entry above the preflight refusal but
explicitly says that refusal occurred **before** the hand commit. The refused
path was `prompts/templates/review_prompt.md`; run 1c followed the parser fix
and template wording change.
[commands_and_versions.txt:26-30](qualification/commands_and_versions.txt#L26-L30),
[run_01_ordinary.txt:11-15](qualification/run_01_ordinary.txt#L11-L15)

The hand recovery moves were:

- Commit the review-template wording change after its dirty path blocked preflight.
  [commands_and_versions.txt:43](qualification/commands_and_versions.txt#L43)
- Commit `coder_seconds` from 900 to 20 before run 2a; after that timeout, run
  `git checkout -- 07_app/canary/wordcount.py` by hand and commit canary D004,
  mistakenly leaving the budget at 20 for run 2b.
  [commands_and_versions.txt:31-35, 45](qualification/commands_and_versions.txt#L31-L45)
- After run 2b, repeat that exact checkout command and commit `coder_seconds`
  from 20 to 900 before run 2c.
  [commands_and_versions.txt:36-37, 44-45](qualification/commands_and_versions.txt#L36-L45)
- After run 3, restore `wordcount.py`, remove the untracked test, and hand-commit
  the ledger and prompt; the command record also reports a push. Exact removal,
  commit, and push command lines are not observed.
  [commands_and_versions.txt:40, 46](qualification/commands_and_versions.txt#L40-L46)

## Ordinary slice: M001-S01

The first slice reached `accepted` in round 1 after two `seat_output` stops.
Run 1a printed the coder and verification results, then stopped after the format
retry. Run 1b's transcript was not retained; its review-prompt artifacts and stop
remain in the copied ledger. Run 1c resumed at review, passed with zero open
findings, and printed acceptance commit `f0c6227` and the slice boundary stop.
[run_01_ordinary.txt:1-15](qualification/run_01_ordinary.txt#L1-L15),
[ledger.jsonl:5-13](qualification/ledger.jsonl#L5-L13)

These are the six requested event rows, quoted without field changes: `prompt`,
`coded`, `verified`, the initial review-prompt `artifact`, `reviewed`, and
`accepted`. The intervening format attempts and final review-prompt artifact
remain visible in the source ledger.
[ledger.jsonl:1-4, 11-12](qualification/ledger.jsonl#L1-L12)

```jsonl
{"schema":"frutlups.ledger/1","t":"2026-09-05T17:27:10Z","ev":"prompt","by":"frutlups","slice":"M001-S01","round":1,"path":"prompts/for_coding_agent/001_M001-S01_r1.md","sha":"4ef85270154be42d61f9fdbddb3aa8a98876188e0c1b49e4cb8ea8a72ef4da0f","baseline":[]}
{"schema":"frutlups.ledger/1","t":"2026-09-05T17:27:49Z","ev":"coded","by":"frutlups","slice":"M001-S01","round":1,"changed":[{"path":"07_app/canary/__init__.py","sha":"fc6151980ff06a1feb55b8b91622add4eaad197d064d3e4b4907860ddac455ab","kind":"added"},{"path":"07_app/canary/wordcount.py","sha":"c6d0cabeeebb5dc47c365ddbf3b527d68df121ff5815ef56b5959281b8202309","kind":"added"},{"path":"07_app/conftest.py","sha":"39264d0fc1046696bf325b1dc0fed20b521a3c2a9752d80ee14ea38e3288f4ba","kind":"added"},{"path":"07_app/tests/test_wordcount.py","sha":"1ee248c9a74c912c8461f31a169852ebc21bc4dbb07b98472c5ed5ac67be6cf7","kind":"added"}],"notes_path":"05_governance/reviews/m001/001_M001-S01_r1_coder.md","seat":"coder","secs":39.71,"tokens_in":6644,"tokens_out":193}
{"schema":"frutlups.ledger/1","t":"2026-09-05T17:27:50Z","ev":"verified","by":"frutlups","slice":"M001-S01","round":1,"receipt":"05_governance/reviews/m001/002_M001-S01_r1_verification.json","sha":"24c8ed87246c4cebcf0a48e54a8a754417043b9d3631137d6562687fb6f4c43f","ok":true}
{"schema":"frutlups.ledger/1","t":"2026-09-05T17:27:50Z","ev":"artifact","by":"frutlups","scope":"M001-S01","round":1,"role":"review_prompt","path":"prompts/for_review_agent/001_M001-S01_r1_review.md","sha":"faf7b6f79b0b7bd28a983a3ee1f7929bd983ea4ae3d7ac9075911bd8f3f3c4a1"}
{"schema":"frutlups.ledger/1","t":"2026-09-05T18:47:46Z","ev":"reviewed","by":"frutlups","slice":"M001-S01","round":1,"report":"05_governance/reviews/m001/003_M001-S01_r1_review.md","sha":"226e86fd83332d0065c51fea1997e7a76112ccc8fc207185ed461dc26faf3972","verdict":"pass","open":[],"seat":"reviewer","secs":11.084,"tokens_in":11302,"tokens_out":640,"cost_usd":0.104768}
{"schema":"frutlups.ledger/1","t":"2026-09-05T18:47:46Z","ev":"accepted","by":"frutlups","slice":"M001-S01","round":1}
```

The coded row records 39.71 s, 6,644 input tokens and 193 output tokens; the
reviewed row records 11.084 s, 11,302 input tokens, 640 output tokens and
$0.104768. Verification took 0.52 s in the transcript. Duration and usage fields
for the other quoted event rows are not observed; their timestamps are not job
wall times.
[ledger.jsonl:1-4, 11-12](qualification/ledger.jsonl#L1-L12),
[run_01_ordinary.txt:3-4](qualification/run_01_ordinary.txt#L3-L4)

## Forced timeout: M001-S02

Run 2a used the committed 20 s coder budget. The killed job recorded
`status=timed_out failure_class=timeout exit=1 secs=20.126`; its stderr was empty,
and stdout contained 164 events / 105,690 bytes, with four reads and one edit
started. It left five added lines implementing `top_words` in
`07_app/canary/wordcount.py`.
[commands_and_versions.txt:16, 31-32](qualification/commands_and_versions.txt#L16-L32),
[timeout_stderr_tail.txt:1-8](qualification/timeout_stderr_tail.txt#L1-L8)

The printed stop was:
[run_02_timeout.txt:3](qualification/run_02_timeout.txt#L3)

```text
seat_transport: coder: Pi timeout failure; files left by failed attempt: 07_app/canary/wordcount.py; owner must inspect and record or restore the edits
```

The changed tree caused the run to stop after one kill, with no automatic
same-round retry. Run 2b was a separate owner invocation after hand restoration,
still at 20 s by mistake, and produced the same stop and leftover file. The
ledger contains one round-1 prompt, both timeout stops, and then the successful
coded event; the subsequent attempts did not create another coding prompt.
[commands_and_versions.txt:32-37](qualification/commands_and_versions.txt#L32-L37),
[run_02_timeout.txt:1-9](qualification/run_02_timeout.txt#L1-L9),
[ledger.jsonl:14-17](qualification/ledger.jsonl#L14-L17)

Run 2a's copied Git status line is encoding-garbled. Encoding that line as
UTF-16LE bytes and decoding those bytes as UTF-8 recovers the following text;
the reverse conversion reproduces the source line exactly. This is a decoded
quotation, not an edit to the evidence.
[run_02_timeout.txt:4](qualification/run_02_timeout.txt#L4)

```text
 M 05_governance/ledger.jsonl
 M 07_app/canary/wordcount.py
?? prompts/for_coding_agent/002_M001-S02_r1.md
```

After hand restoration and the 900 s budget commit, run 2c completed coding,
verification and review, accepted M001-S02 with commit `c4da6ca`, and stopped
at the slice boundary.
[run_02_timeout.txt:8-14](qualification/run_02_timeout.txt#L8-L14)

### Process survival observation (D018)

The copied listing is labelled as taken immediately after run 2a. It contains
six `node.exe` entries and no named Pi process. The recorded node rows are:
[tasklist_after_kills.txt:1-19](qualification/tasklist_after_kills.txt#L1-L19)

```text
node.exe                     35788 Console                    1     34,472 K
node.exe                      5440 Console                    1     34,408 K
node.exe                     13184 Console                    1     34,404 K
node.exe                     19816 Console                    1     48,048 K
node.exe                      4436 Console                    1     47,740 K
node.exe                     28564 Console                    1     55,640 K
```

The listing's annotation and the owner's supplemental record attribute every
listed `node.exe` to the Codex desktop runtime started before the run. The owner
reports that a separate attributed `Win32_Process` listing showed no process
launched from `pi.cmd` or the Node.js executable, and no orphaned grandchild.
Thus node processes remained present, but **no surviving Pi job process or
orphaned grandchild was reported after run 2a**. The underlying command-line
listing was kept locally and is not in this evidence set; independent process
attribution is not observed here. A listing immediately after run 2b is also
not observed.
[tasklist_after_kills.txt:19](qualification/tasklist_after_kills.txt#L19),
[commands_and_versions.txt:33-36, 50-53](qualification/commands_and_versions.txt#L33-L53)

## Forced path violation: M001-S03

The boundary was narrowed to `07_app/canary/` while the slice required
`07_app/tests/test_stop_words.py`. The printed violation file list contains
exactly that test path:
[commands_and_versions.txt:17-18](qualification/commands_and_versions.txt#L17-L18),
[run_03_violation.txt:1-3](qualification/run_03_violation.txt#L1-L3)

```text
path_violation: 07_app/tests/test_stop_words.py; owner must inspect
```

The transcript's following Git status line is encoding-garbled. The same
reversible UTF-16LE-to-UTF-8 recovery used above yields this decoded quotation:
[run_03_violation.txt:4](qualification/run_03_violation.txt#L4)

```text
 M 05_governance/ledger.jsonl
 M 07_app/canary/wordcount.py
?? 07_app/tests/test_stop_words.py
?? prompts/for_coding_agent/003_M001-S03_r1.md
```

The modified product file and untracked test remained after the stop; the owner
subsequently restored the file and removed the test by hand. This is the observed
D006 no-automatic-revert behavior. Exact post-stop file contents are not observed
in the copied evidence. The ledger records `path_violation`, and status still
shows M001-S03 in `coding`, next M001-S03.
[run_03_violation.txt:3-4](qualification/run_03_violation.txt#L3-L4),
[commands_and_versions.txt:40, 46](qualification/commands_and_versions.txt#L40-L46),
[ledger.jsonl:23-24](qualification/ledger.jsonl#L23-L24),
[status_usage.txt:1-4](qualification/status_usage.txt#L1-L4)

## Job wall times

| Run / job or verification | Recorded wall time | Evidence / limitation |
| --- | --- | --- |
| 1a Pi coder | 39.71 s | [run_01_ordinary.txt:3](qualification/run_01_ordinary.txt#L3) |
| 1a verification | 0.52 s | [run_01_ordinary.txt:4](qualification/run_01_ordinary.txt#L4) |
| 1a Claude review and format retry | not observed for either job | [run_01_ordinary.txt:5-7](qualification/run_01_ordinary.txt#L5-L7) |
| 1b Claude review and format retry | not observed for either job; transcript not retained | [run_01_ordinary.txt:9](qualification/run_01_ordinary.txt#L9), [ledger.jsonl:7-9](qualification/ledger.jsonl#L7-L9) |
| 1c Claude review | 11.084 s | [run_01_ordinary.txt:13](qualification/run_01_ordinary.txt#L13) |
| 2a Pi coder, killed | 20.126 s; configured budget 20 s; job duration not printed in run transcript | [timeout_stderr_tail.txt:3](qualification/timeout_stderr_tail.txt#L3), [run_02_timeout.txt:1-3](qualification/run_02_timeout.txt#L1-L3); prompt-to-stop interval 20 s at whole-second ledger precision: [ledger.jsonl:14-15](qualification/ledger.jsonl#L14-L15) |
| 2b Pi coder, killed | not observed; configured budget still 20 s | [run_02_timeout.txt:5-6](qualification/run_02_timeout.txt#L5-L6) |
| 2c Pi coder | 47.074 s | [run_02_timeout.txt:9](qualification/run_02_timeout.txt#L9) |
| 2c verification | 0.707 s | [run_02_timeout.txt:10](qualification/run_02_timeout.txt#L10) |
| 2c Claude review | 21.278 s | [run_02_timeout.txt:12](qualification/run_02_timeout.txt#L12) |
| 3 Pi coder, path violation | 56.208 s in the owner's supplemental record; job duration not printed in run transcript | [commands_and_versions.txt:66-67](qualification/commands_and_versions.txt#L66-L67), [run_03_violation.txt:1-4](qualification/run_03_violation.txt#L1-L4); prompt-to-stop interval 56 s at whole-second ledger precision: [ledger.jsonl:23-24](qualification/ledger.jsonl#L23-L24) |
| Preflight/authentication probes | not observed | [commands_and_versions.txt:22, 28-29](qualification/commands_and_versions.txt#L22-L29) |

## Recorded usage and its limits

The final `frutlups status --usage` output reports the following sums verbatim:
[commands_and_versions.txt:39](qualification/commands_and_versions.txt#L39),
[status_usage.txt:5-8](qualification/status_usage.txt#L5-L8)

```text
M001-S01 usage secs=50.794 tokens_in=17946 tokens_out=833 cost_usd=0.14377800000000002
M001-S02 usage secs=68.352 tokens_in=24867 tokens_out=1585 cost_usd=0.1589565
M001-S03 usage secs=? tokens_in=? tokens_out=? cost_usd=?
M001 usage secs=119.146 tokens_in=42813 tokens_out=2418 cost_usd=0.3027345
```

The recorded seat seconds are 39.71 + 11.084 = 50.794 for M001-S01 and
47.074 + 21.278 = 68.352 for M001-S02, totaling 119.146. These match the status
rows and exclude the separately printed verification durations.
[ledger.jsonl:2, 11, 17, 20](qualification/ledger.jsonl#L2-L20),
[status_usage.txt:5-8](qualification/status_usage.txt#L5-L8),
[run_01_ordinary.txt:4](qualification/run_01_ordinary.txt#L4),
[run_02_timeout.txt:10](qualification/run_02_timeout.txt#L10)

The coder cost contributions are absent from the copied `coded` ledger rows;
the owner's supplemental record says `status --usage` reads them from each
coder job's local `result.json`. The recorded coder costs reconcile the reviewer
costs to the status sums: M001-S01 is $0.104768 + $0.03901 = $0.143778
(printed as `0.14377800000000002`), and M001-S02 is
$0.1488335 + $0.010123 = $0.1589565, totaling $0.3027345. The M001-S02 coder
cost is the last-message cost only (D022), not a corrected whole-job total.
[commands_and_versions.txt:62-69](qualification/commands_and_versions.txt#L62-L69),
[ledger.jsonl:2, 11, 17, 20](qualification/ledger.jsonl#L2-L20),
[status_usage.txt:5-8](qualification/status_usage.txt#L5-L8)

These are incomplete recorded sums. The owner's note says Pi ledger usage
contains only the last assistant message (D022). For the six-message M001-S02
coder job, the supported true totals are **19,535 input + 16,640 cache-read =
36,175 input/cache-read tokens**, and **1,148 output tokens**, compared with
243 in / 186 out recorded in the ledger. A cache-write total and a corrected
cost total are not observed in this qualification evidence set; the true
M001-S01 and M001-S03 Pi totals are also not observed here. The recorded status
rows above are preserved without substituting reconstructed totals.
[commands_and_versions.txt:56-57](qualification/commands_and_versions.txt#L56-L57),
[ledger.jsonl:17](qualification/ledger.jsonl#L17)

The owner reports roughly 11.3k and 35.4k input tokens for the failed M001-S01
reviews in runs 1a and 1b, with no `reviewed` events and no inclusion in status
usage. Exact per-attempt input, output, cost and wall-time totals are not observed.
The timeout stops likewise have no usage fields, and M001-S03's status quantities
are all `?`; a total covering every attempted job is not observed.
[commands_and_versions.txt:58-59](qualification/commands_and_versions.txt#L58-L59),
[ledger.jsonl:5-9, 15-16, 24](qualification/ledger.jsonl#L5-L24),
[status_usage.txt:7](qualification/status_usage.txt#L7)

## Exercised failures and remaining observations

| Class / stop | Real-seat observation | Evidence |
| --- | --- | --- |
| Output / `seat_output` | Review-format rejection after the format retry in runs 1a and 1b; run 1c resumed after the D021 parser fix | [run_01_ordinary.txt:1-15](qualification/run_01_ordinary.txt#L1-L15), [ledger.jsonl:6-9](qualification/ledger.jsonl#L6-L9) |
| Timeout / `seat_transport` | Pi timeout classification at 20.126 s in run 2a, followed by a dirty-attempt transport stop; another timeout stop in run 2b | [timeout_stderr_tail.txt:3](qualification/timeout_stderr_tail.txt#L3), [run_02_timeout.txt:1-6](qualification/run_02_timeout.txt#L1-L6) |
| Independent provider/network transport failure | not observed; the observed `seat_transport` stops name Pi timeout failures | [ledger.jsonl:15-16](qualification/ledger.jsonl#L15-L16) |
| Path fence / `path_violation` | Required test outside the allowed prefix stopped run 3 with the file named; this is a loop stop | [run_03_violation.txt:1-4](qualification/run_03_violation.txt#L1-L4) |
| Claude auth and capacity | not observed; the owner says they were neither forced nor encountered, so the D016 text-defined rules remain unqualified by this canary | [commands_and_versions.txt:60](qualification/commands_and_versions.txt#L60) |
| Pi auth or capacity failure | not observed in this canary evidence | [ledger.jsonl:1-24](qualification/ledger.jsonl#L1-L24) |

No milestone-close hand commit arose because the canary ran with
`holistic_review: false` (D019). Milestone-close commit behavior under holistic
review is not observed in these runs.
[commands_and_versions.txt:15, 47](qualification/commands_and_versions.txt#L15-L47)
