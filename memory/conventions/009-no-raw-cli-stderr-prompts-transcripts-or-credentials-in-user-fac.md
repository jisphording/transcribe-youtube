---
candidate-id: 04-debug-record-follow-ups:convention:02
kind: conventions
id: MEM-009
slug: no-raw-cli-stderr-prompts-transcripts-or-credentials-in-user-fac
title: User-facing errors, logs and test output never contain raw CLI stderr, prompts, transcripts or credentials
status: accepted
supersedes:
origin: 04-debug-record-follow-ups/conventions/2
created: 2026-11-17
---

Subscription-CLI providers (`backend/antigravity.py`, `backend/claude_cli.py`) and the LLM paths report failures only through constant or classified messages. Raw stderr, prompt text, transcript fragments and credentials never go into SSE `error` events, logs or assertion messages. Tests that feed fake tokens or transcript snippets into a failure path assert that none of them show up in the resulting message. This convention exists because CLI diagnostics routinely echo the prompt and auth state (`04-debug-record-follow-ups/conventions/2`).
