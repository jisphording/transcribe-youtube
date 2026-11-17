---
candidate-id: 04-debug-record-follow-ups:decision:01
kind: decisions
id: MEM-006
slug: keep-the-advisory-40-60-target-do-not-reintroduce-the-length-che
title: Extended summaries use advisory 40-60% length targets, never a length check or retry
status: accepted
supersedes:
origin: 04-debug-record-follow-ups/decisions/1
created: 2026-11-17
---

## Context / Forces

An early debug record for extended summaries proposed validating the output length against the source, retrying once and failing the import when the summary was "disproportionate". Commit 4af81b0 deliberately replaced that with advisory targets: the prompt asks for 40-60% of the source words, and `_stream_complete()` in `backend/main.py` accepts any completed response. Retries doubled cost and latency, and real models miss approximate word targets often enough that a hard check failed good imports. The plan removed the remaining dead parameters of the old retry path (`04-debug-record-follow-ups/decisions/1`).

## Decision

Extended-summary length is guidance in the prompt only. Do not reintroduce a length-ratio check, a length-based retry or a length-based failure. The only rejection is a response that reports hitting an output limit (`stop_reason` `max_tokens` / `length`), which is truncated, not merely short or long.

## Alternatives

| Option | Why rejected |
|---|---|
| Validate length, retry once, fail if still disproportionate (original debug record) | Doubles cost and time on a miss, fails usable imports, and word targets are inherently approximate across providers |
| Silently trim or pad to the target | Distorts content; padding invents material |

## Consequences

Imports finish predictably and cost one call per segment. Summaries can come out shorter or longer than 40-60%. Revisit only with evidence that a provider routinely returns unusably short extended summaries, and then prefer a prompt fix over a retry loop.
