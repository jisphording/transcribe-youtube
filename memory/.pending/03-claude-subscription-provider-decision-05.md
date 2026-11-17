---
candidate-id: 03-claude-subscription-provider:decision:05
kind: decisions
id: pending
slug: subscription-calls-never-touch-the-paid-anthropic-api
title: Subscription calls never touch the paid Anthropic API: strip API credentials and never fall back
status: accepted
supersedes:
origin: 03-claude-subscription-provider/decisions/5
origins: [03-claude-subscription-provider/decisions/5, 03-claude-subscription-provider/decisions/6]
created: 2026-11-17
---

## Context / Forces

The backend loads `ANTHROPIC_API_KEY` from `backend/.env`. If the CLI child inherits it, the key outranks the claude.ai OAuth login and the call bills the API account (empty at the time). The CLI's `--bare` mode accepts only API-key auth and skips the keychain, so it cannot use the subscription at all. A silent fallback to another provider on failure would spend money the user does not have. CLAUDE.md sets the same no-fallback rule for OpenRouter and Antigravity. (`03-claude-subscription-provider/decisions/5`, `03-claude-subscription-provider/decisions/6`)

## Decision

`backend/claude_cli.py` strips every `ANTHROPIC_*` and `*API_KEY*` variable (plus Bedrock/Vertex switches) from the child environment and never passes `--bare`. A failing `claude-cli:` call (not logged in, usage limit, timeout) surfaces as an error. It never falls back to the Anthropic API or another provider.

## Alternatives

| Option | Why rejected |
| --- | --- |
| Inherit the environment | The API key wins over OAuth and bills the API account. |
| `--bare` for a smaller, faster CLI start | Accepts only `ANTHROPIC_API_KEY`; cannot use the subscription. |
| Fall back to the API or OpenRouter on failure | Spends money silently; hides auth and quota problems. |

## Consequences

A subscription import costs $0 or fails visibly. New credential variables that the CLI might honour must be added to the strip list.
