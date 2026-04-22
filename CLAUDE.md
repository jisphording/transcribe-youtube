# CLAUDE.md — YouTube to Obsidian

## Project Overview

A full-stack app that imports YouTube videos as structured Obsidian notes. It fetches transcripts, processes them with Claude AI (summary + cleaned transcript), and creates formatted markdown notes.

- **Frontend:** TypeScript Obsidian plugin (`obsidian-plugin/`)
- **Backend:** Python FastAPI with SSE streaming (`backend/`)
- **Deployment:** macOS LaunchAgent (uvicorn, managed via `backend/scripts/`)
- **AI:** Anthropic Claude API (Sonnet 4.6 / Opus 4.6)

## Code Style

- **Indentation: 4 spaces** in all files (Python and TypeScript). Never use 2 spaces or tabs.
- Keep code concise — avoid unnecessary abstractions, comments, or docstrings unless logic is non-obvious.
- Python: use type hints for function signatures. Use `str | None` union syntax (not `Optional`).
- TypeScript: follow Obsidian plugin conventions. Use `strictNullChecks`.

## Architecture & Separation of Concerns

### Backend Modules (`backend/`)

Each file has a single responsibility. Do not merge concerns across modules.

| File | Responsibility | Depends on |
|---|---|---|
| `main.py` | FastAPI app, routes, SSE event generation, request orchestration | all other modules |
| `models.py` | Pydantic request/response models | — |
| `youtube.py` | Video ID extraction, metadata (yt-dlp), transcript fetching (youtube-transcript-api + yt-dlp fallback) | `cookies` |
| `claude.py` | Claude API streaming, response parsing, model constants | — |
| `note.py` | Obsidian markdown note assembly (frontmatter, sections) | — |
| `cookies.py` | Cookie file persistence (save/delete/check) | — |
| `prompts/__init__.py` | Prompt registry and composition engine | prompt feature modules |
| `prompts/base.py` | Base prompt: transcript cleaning + short summary | — |
| `prompts/extended.py` | Extended summary prompt: topic-by-topic editorial rewrite | — |

**Rules:**
- `youtube.py`, `claude.py`, `note.py`, and `cookies.py` must NOT import from each other. They are independent modules orchestrated only by `main.py`.
- `models.py` contains only Pydantic models — no logic.
- Prompt modules expose only `ROLE_PREAMBLE`, `JSON_KEYS`, and `RULES` — no functions.

### Frontend (`obsidian-plugin/src/`)

Modular TypeScript plugin with one file per concern:

| File | Responsibility | Depends on |
|---|---|---|
| `main.ts` | `YTObsidianPlugin` class — plugin lifecycle, settings persistence, note creation (entry point) | `settings`, `import-modal` |
| `settings.ts` | `YTObsidianSettings` interface, `DEFAULT_SETTINGS`, `YTObsidianSettingTab` class (settings UI + cookie management) | `main` (type only) |
| `import-modal.ts` | `YouTubeImportModal` class — import UI, request building, progress display | `main` (type only), `youtube-utils`, `sse-handler` |
| `sse-handler.ts` | `processSSEStream()` — SSE stream parsing, event dispatch via callbacks | — |
| `youtube-utils.ts` | `extractVideoId()`, `findExistingNote()` — stateless utility functions | — |

**Rules:**
- `sse-handler.ts` and `youtube-utils.ts` are pure modules with no plugin dependencies — they must not import from `main`, `settings`, or `import-modal`.
- `settings.ts` and `import-modal.ts` import `main.ts` only as a type (`import type`) to avoid circular runtime dependencies.
- SSE event stage handling lives in `sse-handler.ts`. When adding new SSE stages, update the switch statement there (not in `import-modal.ts`).

### Communication

Frontend ↔ Backend communicate via:
- `POST /process` — SSE stream for the import pipeline (metadata → transcript → Claude → note)
- `GET/POST/DELETE /cookies` — Cookie file management
- `GET /health` — Health check

## Processing Pipeline

The `/process` endpoint runs a 4-step SSE streaming pipeline:

1. **Metadata** — `youtube.get_video_metadata()` via yt-dlp
2. **Transcript** — `youtube.get_transcript()` via youtube-transcript-api, falls back to yt-dlp
3. **Claude** — `claude.stream_claude()` with composed prompt from `prompts.get_system_prompt()`
4. **Note** — `note.build_obsidian_note()` assembles final markdown

Each step emits SSE events (`stage`, `message`, extras) so the frontend can show real-time progress.

## Composable Prompt System

Prompts live in `backend/prompts/` and are composed at runtime.

### Adding a new prompt feature:

1. Create `backend/prompts/yourfeature.py` with three exports:
   ```python
   ROLE_PREAMBLE = "a role description"
   JSON_KEYS = [{"key": "your_key", "description": "What this key contains."}]
   RULES = """Rules for "your_key":\n- Rule one.\n- Rule two."""
   ```
2. Register it in `PROMPTS` dict in `backend/prompts/__init__.py`
3. Add the corresponding boolean flag to `TranscriptRequest` in `backend/models.py`
4. Wire the feature flag in `main.py`'s `event_generator` (add to `features` list, extract result)
5. Pass the new content to `build_obsidian_note()` (extend its signature if needed)

## Build & Run Commands

### Backend (LaunchAgent)

The backend runs as a native Python process via a macOS LaunchAgent. Managed with scripts in `backend/scripts/`:

```bash
./backend/scripts/start.sh        # Load and start the LaunchAgent
./backend/scripts/stop.sh         # Unload (stop) the LaunchAgent
./backend/scripts/logs.sh         # Tail stdout + stderr logs
./backend/scripts/install.sh      # First-time install (creates the plist)
./backend/scripts/update.sh       # Pull changes and restart
curl http://localhost:8000/health # Health check
```

After changing `backend/.env`, restart with `stop.sh` then `start.sh`.

### Obsidian Plugin

```bash
cd obsidian-plugin
npm install                       # Install dependencies
npm run dev                       # Development (watch mode)
npm run build                     # Type-check + bundle + deploy to Obsidian
```

The `deploy` script reads `OBSIDIAN_PLUGINS_PATH` from `obsidian-plugin/.env` and copies `main.js` + `manifest.json` there.

## Key Dependencies

### Backend (Python 3.12)
- `fastapi` + `uvicorn` — web framework + ASGI server
- `sse-starlette` — Server-Sent Events
- `anthropic` — Claude API client
- `youtube-transcript-api` — YouTube transcript fetching
- `yt-dlp` — video metadata + fallback subtitles
- `httpx` — HTTP client for subtitle download
- `pydantic` — data validation

### Frontend (TypeScript)
- `obsidian` — Obsidian plugin API
- `esbuild` — bundler
- `typescript` 4.7.4

## Environment Variables

Both `.env` files are gitignored. Copy the `.env.example` templates on first setup.

- `backend/.env`: `ANTHROPIC_API_KEY` — required for Claude API. The LaunchAgent must be restarted (`stop.sh` + `start.sh`) after changes.
- `obsidian-plugin/.env`: `OBSIDIAN_PLUGINS_PATH` — primary vault deploy target. Additional vaults can be added as `OBSIDIAN_PLUGINS_PATH_2`, `_3`, etc. — `deploy.sh` picks up all matching variables automatically.

## SSE Event Protocol

Events are JSON objects with at minimum `stage` and `message` fields.

| Stage | Direction | Extra fields |
|---|---|---|
| `metadata` / `metadata_done` | Step 1 progress | `step`, `total_steps` |
| `transcript` / `transcript_done` | Step 2 progress | `segments`, `transcript_chars` |
| `claude` / `claude_extended` | Step 3 progress | `input_tokens`, `output_tokens`, `elapsed` |
| `claude_done` | Step 3 complete | `input_tokens`, `output_tokens`, `elapsed`, `cost_usd` |
| `building` | Step 4 progress | `step`, `total_steps` |
| `done` | Final result | `filename`, `content`, `metadata` |
| `error` | Failure at any point | — |

## Cookie Handling

Three-tier priority for YouTube authentication:
1. Browser extraction (`cookiesfrombrowser` in yt-dlp) — highest priority
2. Explicit cookie file path
3. Uploaded cookie file (`backend/cookies.txt`)

## Notes for Claude

- When modifying the backend, keep modules independent. If a change touches `youtube.py`, it should NOT require changes to `claude.py` or `note.py` unless the interface contract changes.
- When modifying SSE events, update the backend emitter (`main.py`) and the frontend consumer in `sse-handler.ts` together.
- The prompt system is designed for extension. Prefer adding new prompt modules over modifying `base.py`.
- Token counting in `claude.py` is approximate during streaming (1 delta ≈ 1 token) but corrected by `message_delta` at the end.
