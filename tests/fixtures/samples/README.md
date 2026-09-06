# Recorded seat output samples

The five original samples were recorded by the owner on 2026-09-04 with Claude Code 2.1.226 and
Pi 0.84.4 on Windows 10, each on a trivial read-only prompt, captured through
cmd.exe redirection so the bytes are what a `Popen` pipe receives. Files are
UTF-8 without a byte-order mark. Random per-run ids (`session_id`, `uuid`) in
the Claude success samples were replaced by fixed placeholders; every other
value is untouched. No file contains a path, account id, email, key, or token.

| File | Seat | Case | Exit | Notes |
| --- | --- | --- | --- | --- |
| `claude_opus_ok.json` | claude, model `opus` | success | 0 | one JSON result object; `result` is the final text, `total_cost_usd` and `usage` carry usage |
| `claude_fable_ok.json` | claude, model `fable` | success | 0 | same shape; `modelUsage` lists a helper model beside the main one |
| `claude_transport_fail.json` | claude, model `opus` | transport | 1 | recorded with `ANTHROPIC_BASE_URL` pointing at a closed port; `is_error` true, `terminal_reason` `api_error`, `result` starts with `API Error:` |
| `pi_ok.jsonl` | pi, openai-codex `gpt-5.6-sol` | success | 0 | one JSON event per line: `session`, `agent_start`, `turn_start`, `message_start`/`message_update`/`message_end`, `turn_end`, `agent_end`, `agent_settled`; each assistant `message_end` carries that message's own `usage` |
| `pi_auth_fail.jsonl` | pi, anthropic `claude-opus-5` | auth | 0 | provider not logged in; the assistant `message_start` and `message_end` carry `stopReason` `error` and an `errorMessage` beginning `401` with `authentication_error`; Pi still exits 0 |
| `pi_canary_usage.jsonl` | [Pi 0.85.0](../../../docs/qualification/commands_and_versions.txt#L6), openai-codex `gpt-5.6-sol` | multi-message usage | not recorded in excerpt | redacted recording of the 2026-09-05 M004 canary M001-S02 coder job; six assistant messages with exact usage objects; content text, cwd, and the `agent_end` message list elided (D023, M002-S02-H8) |

Pi usage is per message and summed over every assistant `message_end` (D022):
`tokens_in` sums `input + cacheRead + cacheWrite`, `tokens_out` sums `output`,
and `cost_usd` sums `usage.cost.total`. Repeated messages in other event types
do not add usage. The canary excerpt preserves the recorded usage numbers;
it is not a constructed usage example. See the
[qualification record](../../../docs/qualification.md) for the canary evidence.

Original recording commands, run from a scratch folder outside any repository:

```bat
call pi -p --mode json --no-session --provider openai-codex --model gpt-5.6-sol --thinking medium --tools read --no-extensions --no-skills --no-prompt-templates "Reply with the single word OK and nothing else." > pi_ok.jsonl
call pi -p --mode json --no-session --provider anthropic --model claude-opus-5 "Reply OK." > pi_auth_fail.jsonl
call claude -p "Reply with the single word OK and nothing else." --output-format json --model opus > claude_opus_ok.json
set ANTHROPIC_BASE_URL=http://127.0.0.1:9
call claude -p "Reply OK." --output-format json --model opus < NUL > claude_transport_fail.json
```

Not recorded: a Claude auth failure (setting an invalid `ANTHROPIC_API_KEY`
makes `claude -p` wait for an interactive confirmation), and any capacity
failure for either seat. See `questions/answered/001_seat_output_samples.md`
for how those classes are defined until M004 exercises real cases.
