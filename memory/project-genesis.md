# Project Genesis

<!-- Authored, not generated. `memory_index.py --sync` only creates this skeleton. -->

## Origin

Media to Obsidian (started February 2026) turns a pasted URL into a structured Obsidian
note: YouTube videos, Apple Podcasts episodes and web articles, one at a time or whole
channels, playlists and shows through a persistent backend queue. A local FastAPI backend
fetches transcripts (YouTube captions, podcast RSS, or local whisper.cpp) or article text,
runs them through a choice of AI providers, and writes the note into the vault. It runs
entirely on the user's Mac as LaunchAgents, so cost and privacy stay in the user's hands:
paid API, cheap cloud models, a local model, or an existing subscription.

## Lineage

| Plan | Contributed |
|---|---|
| 03-claude-subscription-provider | Recovered a multi-day outage caused by leaked SQLite handles in the batch queue (every DB access now closes through `_db()`). Added a fourth provider that runs imports on the user's claude.ai subscription through the logged-in Claude Code CLI (`claude-cli:` ids). It is contained like Antigravity, never falls back to the paid API, and pauses queue lanes on usage limits until the reported reset. |
| 04-debug-record-follow-ups | Closed what four debug records (Antigravity exit 71, Antigravity sandbox startup, extended summary + resources, Whisper startup + import queue) left open. Antigravity CLI failures now go through one ordered, tested classifier that returns safe messages. The dead parameters of the superseded extended-length retry are gone. Real-import checks are recorded as manual checks MC-06 to MC-08 in `checks/manual-checks.md` for an operator run. |
