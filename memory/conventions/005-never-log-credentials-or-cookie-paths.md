---
candidate-id: 03-claude-subscription-provider:convention:03
kind: conventions
id: MEM-005
slug: never-log-credentials-or-cookie-paths
title: Never print or log API keys, OAuth tokens or cookie paths
status: accepted
supersedes:
origin: 03-claude-subscription-provider/conventions/3
created: 2026-11-17
---

Never print or log the API keys, OAuth tokens or cookie paths, and redact them in docs, evidence and test output. This matters more since the backend drives logged-in CLIs whose environment and stderr can carry credentials.

Origin: `03-claude-subscription-provider/conventions/3`.
