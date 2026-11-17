---
candidate-id: 04-debug-record-follow-ups:convention:01
kind: conventions
id: MEM-008
slug: backend-tests-stay-green-pythonpath-backend-backend-venv-bin-pyt
title: Backend changes keep the full unittest suite green
status: accepted
supersedes:
origin: 04-debug-record-follow-ups/conventions/1
created: 2026-11-17
---

Every backend change keeps the whole suite passing, run from the repo root:

    PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests

The suite is plain `unittest` (no pytest) and needs `PYTHONPATH=backend` because modules import each other by bare name. It had 73 tests when plan 04 started and 84 after it. A shrinking count needs an explanation (`04-debug-record-follow-ups/conventions/1`).
