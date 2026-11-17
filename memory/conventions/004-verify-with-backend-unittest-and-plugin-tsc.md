---
candidate-id: 03-claude-subscription-provider:convention:01
kind: conventions
id: MEM-004
slug: verify-with-backend-unittest-and-plugin-tsc
title: Verify changes with the backend unittest suite and a plugin type-check; build only to deploy
status: accepted
supersedes:
origin: 03-claude-subscription-provider/conventions/1
origins: [03-claude-subscription-provider/conventions/1, 03-claude-subscription-provider/conventions/2]
created: 2026-11-17
---

Every change keeps the backend suite green: `(cd backend && .venv/bin/python -m unittest discover -s tests)`. Plugin changes are type-checked with `(cd obsidian-plugin && npx tsc -noEmit -skipLibCheck)`. `npm run build` also deploys into the vault, so run it only when the change is meant to land there.

Origins: `03-claude-subscription-provider/conventions/1`, `03-claude-subscription-provider/conventions/2`.
