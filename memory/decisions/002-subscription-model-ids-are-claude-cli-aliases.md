---
candidate-id: 03-claude-subscription-provider:decision:04
kind: decisions
id: MEM-002
slug: subscription-model-ids-are-claude-cli-aliases
title: Subscription model ids are claude-cli:<alias> and map to the CLI's model aliases
status: accepted
supersedes:
origin: 03-claude-subscription-provider/decisions/4
created: 2026-11-17
---

## Context / Forces

The subscription exposes whatever current models Anthropic ships. Pinning full model ids would go stale with every release. The CLI accepts the aliases `haiku`, `sonnet`, `opus` (and later `fable`) and resolves them itself. (`03-claude-subscription-provider/decisions/4`)

## Decision

Model ids are `claude-cli:<alias>` (`haiku`, `sonnet`, `opus`, `fable`), listed in `MODELS` in `backend/claude_cli.py`. Unknown ids are rejected. Effort follows `backend/claude.py`: `low` for base imports, `medium` for extended/focus.

## Alternatives

| Option | Why rejected |
| --- | --- |
| Full dated model ids | Go stale on each model release; the subscription decides what is available anyway. |

## Consequences

New models arrive without code changes as long as the alias stays the same. A new alias (as with `fable`) needs one entry in `MODELS` and the plugin list. The resolved model is still recorded in the note's `import_resolved_model`.
