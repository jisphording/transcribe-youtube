---
candidate-id: 03-claude-subscription-provider:decision:03
kind: decisions
id: MEM-001
slug: run-the-claude-subscription-through-the-claude-code-cli-as-a-subprocess
title: Run the Claude subscription through the Claude Code CLI as a contained subprocess
status: accepted
supersedes:
origin: 03-claude-subscription-provider/decisions/3
created: 2026-11-17
---

## Context / Forces

The Anthropic API account had no credit, but the user has a claude.ai subscription and a logged-in Claude Code CLI on the same Mac. The repo already runs another subscription CLI (Antigravity) as a contained subprocess with a timeout, bounded output and a process-group kill, and that pattern is trusted. (`03-claude-subscription-provider/decisions/3`)

## Decision

The subscription provider lives in its own independent module, `backend/claude_cli.py`, which runs `claude -p --output-format stream-json` as a subprocess, modelled on `backend/antigravity.py`. Calls are single-shot (text in, JSON out) with tools disabled. The tested contract is pinned in `docs/claude-cli-contract.md`.

## Alternatives

| Option | Why rejected |
| --- | --- |
| Claude Agent SDK | Wraps the same CLI and adds a Python dependency for no gain. |
| Reading the OAuth token and calling the API directly | Not a supported path for consumer subscriptions; fragile. |
| Agentic/tool use in the CLI call | Not needed for summarisation; widens the attack surface. |

## Consequences

No new dependency and the same containment as Antigravity. The cost: CLI flags or the stream-json format can change with Claude Code auto-updates. Tests use a fake CLI, so breakage shows up only at runtime. Subscription imports share the 5-hour and weekly limits with the user's interactive Claude Code work.
