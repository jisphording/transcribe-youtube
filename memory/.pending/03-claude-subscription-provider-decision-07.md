---
candidate-id: 03-claude-subscription-provider:decision:07
kind: decisions
id: pending
slug: subscription-usage-limits-pause-the-queue-lane
title: A subscription usage limit pauses the queue lane instead of failing the remaining items
status: accepted
supersedes:
origin: 03-claude-subscription-provider/decisions/7
created: 2026-11-17
---

## Context / Forces

The subscription has 5-hour and weekly usage limits. Without special handling, a long channel batch hitting the limit would fail every remaining item one after another. The queue already had a pause-and-backoff mechanism for YouTube bot checks. (`03-claude-subscription-provider/decisions/7`)

## Decision

A usage limit raises `ClaudeCliLimitError`, which `backend/main.py` turns into an `error` event with `blocked=True`, `block_kind="usage_limit"` and `resets_at` when known. The item goes back to pending and its lane pauses until `resets_at` (backoff level unchanged), or along the normal backoff ladder when no reset time is reported. The YouTube Safari-cookie retry is skipped for this kind of block. This applies to every source.

## Alternatives

| Option | Why rejected |
| --- | --- |
| Fail the item and continue | Burns through the whole batch as failures. |
| Cancel the batch | Loses the queue; the user has to rebuild it after the reset. |

## Consequences

Batches resume on their own after the limit resets. The mechanism is generic: another quota-based provider can reuse `block_kind`. A real usage-limit event had not been observed at plan close, so the mapping relies on `docs/claude-cli-contract.md`.
