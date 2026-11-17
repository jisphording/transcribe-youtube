---
candidate-id: 03-claude-subscription-provider:decision:02
kind: decisions
id: pending
slug: batch-queue-sqlite-connections-are-opened-per-call-and-always-closed
title: Batch queue SQLite connections are opened per call and always closed through one context manager
status: accepted
supersedes:
origin: 03-claude-subscription-provider/decisions/2
created: 2026-11-17
---

## Context / Forces

In November 2026 the backend went down for days: `backend/batch_queue.py` opened a new sqlite3 connection per call inside `with conn:`, which in sqlite3 only commits or rolls back and never closes. The Queue view polls `GET /batch` constantly, so the uvicorn process leaked ~243 `batch.db` / `batch.db-wal` handles against the LaunchAgent's soft limit of 256 open files. After that every DB open failed and even `/health` stopped answering. A restart frees the handles, but polling refills them within days, so the fix had to be structural. (`03-claude-subscription-provider/decisions/2`)

## Decision

Every DB access in `backend/batch_queue.py` goes through the `_db()` context manager, which commits or rolls back and always closes the connection. A bare `with _connect()` is never used.

## Alternatives

| Option | Why rejected |
| --- | --- |
| One module-level shared connection | Would need locking across the asyncio worker and the request threads. |
| Connection pooling or moving off SQLite | Out of scope; per-call open/close is cheap at this load. |
| Raise the LaunchAgent file limit | Only delays the outage; the leak stays. |

## Consequences

Leaks are impossible by construction and each of the call sites stays a one-line change. Each call pays an open/close, which does not matter at queue scale. Revisit only if the queue needs real concurrency or a different store.
