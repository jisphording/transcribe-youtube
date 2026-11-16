# Claude CLI (subscription) headless contract

Measured 2026-11-16 with Claude Code CLI 2.1.318 (`~/.local/bin/claude`, a symlink into `~/.local/share/claude/versions/`), logged in via claude.ai (`claude auth status`: `loggedIn: true`, `authMethod: claude.ai`, `subscriptionType: team`). Account identifiers are omitted on purpose.

**Verdict: GO** — with the caveats under "Findings that shape the provider".

**Status: implemented** in `backend/claude_cli.py` (provider `claude-cli:`); the usage-limit case is still unobserved and is handled as described under "Error mapping".

## Invocation

Resolve the executable with `os.path.realpath()` of an absolute path from env (the LaunchAgent PATH omits `~/.local/bin`; same pattern as `antigravity.py`).

```
<claude> -p --output-format stream-json --verbose \
  --tools "" --system-prompt <system> --model <alias|id> --effort <level> \
  --no-session-persistence --strict-mcp-config --setting-sources "" \
  --disable-slash-commands
```

- Prompt goes on **stdin** (tested up to 400k characters); the system prompt is argv.
- `--verbose` is required with `-p --output-format stream-json`.
- **cwd:** a fresh empty temp dir per call, removed afterwards.
- **env:** inherit, but delete `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`. Auth then comes from the keychain OAuth login. Do not use `--bare` (it restricts auth to `ANTHROPIC_API_KEY`).
- **timeout:** measured calls take 2–10 s for small/medium input, 220–435 s for ~40–50k output tokens. Use a timeout of at least 900 s and kill the whole process group on expiry.
- Exit code: 0 on success, 1 on any `is_error` result.

## Authentication from the LaunchAgent

A temporary agent (`gui/<uid>/com.claude-probe`, `RunAtLoad`, PATH `/usr/bin:/bin:/usr/sbin:/sbin`) was given a fake `ANTHROPIC_API_KEY` in its environment; the probe stripped it from the child env. Result: exit 0, reply `OK`, cost `0.0001282`. The terminal run gave the same result. The agent and probe script were removed afterwards (`launchctl list | grep -i claude-probe` is empty).

Redacted init event (identical from terminal and LaunchAgent for these fields):

```json
{"type":"system","subtype":"init","cwd":"<tmp dir>","session_id":"<uuid>",
 "tools":[],"mcp_servers":[],"model":"claude-haiku-5-5","permissionMode":"default",
 "slash_commands":[],"apiKeySource":"none","claude_code_version":"2.1.318",
 "agents":["claude","Explore","general-purpose","Plan","statusline-setup","web-fetch"],
 "skills":[],"plugins":[{"name":"cc-plugin-*","path":"builtin"} x5], ...}
```

`apiKeySource` is `none`: the call was authenticated through claude.ai OAuth, not an API key. `tools`, `mcp_servers`, `skills` and `slash_commands` are empty; the only `plugins` are five built-in `cc-plugin-*` entries (sec, agents-md, telemetry, privacy, plugin-authoring) that cannot be disabled by flags, and the built-in agent names are listed but unusable without tools. No `tool_use` events occurred in any run. No session file was written under `~/.claude/projects` for the probe cwds (`--no-session-persistence`).

## Event shapes (stream-json, one JSON object per line)

1. `system`/`init` — as above; `model` is the resolved id.
2. `assistant` — `message.content[]` with `{"type":"text","text":...}`; `message.usage` has `input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens`; `message.model`. Without `--include-partial-messages` text arrives in whole messages, not deltas (long outputs arrive as one event at the end).
3. `rate_limit_event` — `rate_limit_info`: `status` (`allowed`), `resetsAt` (epoch s), `rateLimitType` (`five_hour`), `overageStatus`, `isUsingOverage`, `unifiedWindows.{five_hour,seven_day}.{utilization,resetsAt,status}`. Observed: 5 h window at 9 %, 7-day at 37 %.
4. `result` — `subtype` (`success` even for errors!), `is_error`, `result` (final text), `stop_reason` (`end_turn` / `refusal`), `api_error_status` (HTTP code or null), `terminal_reason`, `total_cost_usd` (list-price equivalent, **not billed** on a subscription), `duration_api_ms`, `usage{input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens}`, `modelUsage.<model>{contextWindow, maxOutputTokens, costUSD}`, `num_turns`.

Parsing rule: take the `result` event; success only when `is_error` is false. Report cost to the user as subscription quota (0.0), not `total_cost_usd`.

## Models and effort

| Input | Result |
|---|---|
| `haiku` → `claude-haiku-5-5` | accepted |
| `sonnet`, `opus`, `fable` | accepted |
| full id `claude-haiku-5-5` | accepted |
| `low`, `medium`, `high`, `xhigh`, `max` | accepted (sonnet) |
| invalid effort `bogus` | **not an error**: warning on stderr, default effort used — validate in the backend |
| invalid model `bogus-model` | exit 1, `is_error` true, `api_error_status` 404, result text "There's an issue with the selected model (bogus-model)…", stderr `[claude-code:unrecognized_model]` |

Reported limits (`modelUsage`): context window 1,000,000 tokens and max output 128,000 for haiku.

## Limits table (measured)

Realistic prose (this repo's CLAUDE.md, repeated), prompt on stdin:

| Model | Input chars | Latency | Result |
|---|---|---|---|
| haiku | 20,000 | 6.1 s | ok |
| haiku | 100,000 | 6.0 s | ok |
| haiku | 400,000 | 9.5 s | ok |
| sonnet (random-word filler) | 20,000 / 100,000 / 400,000 | 3.9 / 3.9 / 6.1 s | ok (~136k input tokens at 400k) |
| haiku (random-word filler) | 20,000–400,000 | 4–6 s | **refused** (`stop_reason: refusal`, `is_error` true, text "Haiku 5.5 can't help with this… Details: `[bio]`") |

The refusal on meaningless filler text is a classifier false positive; it surfaces as an error result with `stop_reason == "refusal"` and must map to a clear error, like `claude.py` does today.

Long output (no truncation seen): haiku wrote 41,724 tokens (~130k chars) in 222 s; sonnet 49,490 tokens (~142k chars) in 434 s; both `end_turn`. So the ~16k-token worry does not apply.

Derived `MAX_TRANSCRIPT_CHARS` for single-shot calls: **400,000 characters verified; use 300,000** as the conservative default (≈75–100k tokens, well inside the 1M window, leaving headroom for token-dense non-English text), and chunk above it. Keep the existing 12k-segment chunking out of this path unless extended mode is requested.

## Error mapping

| Situation | Signal | Map to |
|---|---|---|
| Logged out (simulated with an empty `CLAUDE_CONFIG_DIR`) | exit 1, `is_error` true, `api_error_status` null, result `Not logged in · Please run /login` | "Run `claude auth login` as this user" |
| Unknown model | exit 1, `api_error_status` 404, "issue with the selected model" | invalid model |
| Refusal | `stop_reason: refusal` | content refused |
| Usage / rate limit | **unobserved** — no limit was hit (window 9 % / 37 %). Expected: `is_error` true with a message naming the limit and a reset time, plus a `rate_limit_event` with `status` other than `allowed` and `resetsAt` (epoch s). Verify when it first happens; treat any `rate_limit_event.status != "allowed"` or error text containing "limit" as a quota block carrying `resetsAt` | block the lane until `resetsAt` |
| Timeout | process killed by the backend | timeout error |
| Spawn failure / missing binary | `OSError` | configuration error |

## Findings that shape the provider

- Cost is subscription quota: show `$0`/"subscription", never `total_cost_usd`.
- `rate_limit_event` provides the utilization and reset times for free; surface it.
- Effort must be validated by the backend (invalid values are silently ignored).
- Success/failure must be read from `is_error`, not `subtype`.
- Transcript text is sent to Anthropic under the user's claude.ai plan.
- Not covered: simultaneous calls (concurrency), partial-message streaming (`--include-partial-messages`) and a real usage-limit hit.
