---
candidate-id: 03-claude-subscription-provider:decision:08
kind: decisions
id: MEM-003
slug: plugin-default-provider-stays-anthropic
title: The plugin's default LLM provider stays anthropic; the subscription provider is opt-in
status: accepted
supersedes:
origin: 03-claude-subscription-provider/decisions/8
created: 2026-11-17
---

## Context / Forces

Only the user's own vault needed to move to the subscription. Changing `DEFAULT_SETTINGS` would change behaviour for every other vault and for fresh installs, which may not have a logged-in CLI. (`03-claude-subscription-provider/decisions/8`)

## Decision

`DEFAULT_SETTINGS.llmProvider` in `obsidian-plugin/src/settings.ts` stays `anthropic`. Each vault picks "Claude subscription" (`claude-cli`) in settings.

## Alternatives

| Option | Why rejected |
| --- | --- |
| Default to `claude-cli` | Breaks installs without `CLAUDE_CLI_PATH` or a CLI login. |

## Consequences

No surprise behaviour change. Revisit only if the subscription becomes the expected setup for all users.
