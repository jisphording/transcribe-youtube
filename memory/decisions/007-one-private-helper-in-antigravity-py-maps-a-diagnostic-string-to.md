---
candidate-id: 04-debug-record-follow-ups:decision:02
kind: decisions
id: MEM-007
slug: one-private-helper-in-antigravity-py-maps-a-diagnostic-string-to
title: Antigravity CLI failures map to safe messages through one ordered classifier, sandbox first
status: accepted
supersedes:
origin: 04-debug-record-follow-ups/decisions/2
created: 2026-11-17
---

## Context / Forces

The Antigravity CLI fails in two places: a non-SUCCESS terminal result (`_terminal_result()`) and a non-zero exit (`_cli_error_detail()`, e.g. exit 71). Each had its own copy of broad term lists, so they drifted. Broad terms also misfired: a sandbox denial whose stderr named a model path showed up as "model unavailable", and "author" matched "auth". Raw CLI stderr can contain prompts, transcript text or tokens, so it must never reach the user (`04-debug-record-follow-ups/decisions/2`).

## Decision

`_classify()` in `backend/antigravity.py` is the single classifier. Both failure paths call it. `_PATTERNS` is ordered from specific to broad: sandbox → quota → auth → network → model. It uses word-boundary terms and returns only constant messages from `_MESSAGES`. Unmatched diagnostics fall back to a generic "unclassified (exit N)" message, never to the raw text. Keep sandbox first: reordering brings back the misclassification.

## Alternatives

| Option | Why rejected |
|---|---|
| Keep separate term lists per call site | They drift. Fixes to one path don't reach the other |
| Broad substring terms (`model`, `auth`) | Misclassify sandbox denials and unrelated words |
| Surface (redacted) stderr to the user | Redaction can't be guaranteed. Prompts, transcripts and credentials can leak |

## Consequences

Messages are consistent and safe, and a single test class covers them. The cost is a narrower match: ECONNREFUSED, DNS, timeouts and "Requested entity was not found" currently land in "unclassified", and bare "permission denied" reports the sandbox message even for keychain or file errors (both tracked in the improvements backlog under plan/). Extend the patterns only from observed real CLI wording.
