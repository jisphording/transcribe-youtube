# CLAUDE.md — Media to Obsidian (YouTube + Podcasts)

## Project Overview

A full-stack app that imports YouTube videos **and** Apple Podcasts episodes into Obsidian as structured notes. The plugin auto-detects the source from the pasted URL, fetches transcripts (RSS / YouTube CC / whisper.cpp), processes them with Claude AI (summary + cleaned transcript + topics + optional resources/extended/focused summaries), and creates formatted markdown notes.

- **Frontend:** TypeScript Obsidian plugin (`obsidian-plugin/`)
- **Backend:** Python FastAPI with SSE streaming (`backend/`)
- **Deployment:** macOS LaunchAgents (uvicorn + whisper-server, managed via `backend/scripts/`)
- **AI:** Anthropic Claude API (Sonnet 4.6 / Opus 4.7 / Haiku 4.5)
- **Local STT:** whisper.cpp via local HTTP server (Apple Silicon, Metal-accelerated)

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
| `main.py` | FastAPI app, routes, URL-source branching, SSE event generation | all other modules |
| `models.py` | Pydantic request/response models | — |
| `youtube.py` | Video ID extraction, metadata (yt-dlp), transcript fetching (youtube-transcript-api + yt-dlp fallback) | `cookies` |
| `podcast.py` | Apple URL parsing, iTunes lookup, RSS fetch (`feedparser`), episode matching, RSS transcript extractor (Podcasting 2.0) | — |
| `whisper.py` | Local whisper-server HTTP client. Downloads audio → POSTs to `whisper-server` → returns transcript | — |
| `claude.py` | Claude API streaming, response parsing, model constants | — |
| `note.py` | Source-agnostic Obsidian markdown note assembly (YouTube + podcast variants) | — |
| `cookies.py` | Cookie file persistence (save/delete/check) | — |
| `prompts/__init__.py` | Source-aware prompt registry and composition engine | prompt feature modules |
| `prompts/base.py` | Base prompt: transcript cleaning + short summary + topics | — |
| `prompts/extended.py` | Extended summary prompt: topic-by-topic editorial rewrite | — |
| `prompts/focus.py` | Focus topic prompt: deep-dive on a user-supplied topic | — |
| `prompts/resources.py` | Resources prompt: extract products/tools/services as wiki-link stubs | — |

**Rules:**
- `youtube.py`, `podcast.py`, `whisper.py`, `claude.py`, `note.py`, and `cookies.py` must NOT import from each other. They are independent modules orchestrated only by `main.py`.
- `models.py` contains only Pydantic models — no logic.
- Prompt modules expose only `ROLE_PREAMBLE`, `JSON_KEYS`, and `RULES` — no functions.
- The prompt composer (`prompts.get_system_prompt`) takes a `source` argument (`"youtube"` or `"podcast"`) so the same feature flags work for both media types — the wording adapts.

### Frontend (`obsidian-plugin/src/`)

Modular TypeScript plugin with one file per concern:

| File | Responsibility | Depends on |
|---|---|---|
| `main.ts` | `YTObsidianPlugin` class — plugin lifecycle, settings persistence, source-aware note creation | `settings`, `import-modal`, `sse-handler` (type) |
| `settings.ts` | `YTObsidianSettings` interface, `DEFAULT_SETTINGS`, `YTObsidianSettingTab` (settings UI: separate folders for YouTube/Podcasts, whisper language, cookie management) | `main` (type only) |
| `import-modal.ts` | `YouTubeImportModal` — single modal that auto-detects source, adapts UI, builds the request, displays progress | `main` (type only), `url-utils`, `sse-handler` |
| `sse-handler.ts` | `processSSEStream()` — SSE parsing + dispatch via callbacks. Knows about all stages (incl. `transcript_rss`, `transcript_whisper_download`, `transcript_whisper_running`) | — |
| `url-utils.ts` | `detectSource()` (YouTube vs Apple Podcasts vs null), `extractVideoId()`, `extractAppleEpisodeId/ShowId()`, `findExistingNote()` | — |

**Rules:**
- `sse-handler.ts` and `url-utils.ts` are pure modules with no plugin dependencies — they must not import from `main`, `settings`, or `import-modal`.
- `settings.ts` and `import-modal.ts` import `main.ts` only as a type (`import type`) to avoid circular runtime dependencies.
- SSE event stage handling lives in `sse-handler.ts`. When adding new SSE stages, update the switch statement there (not in `import-modal.ts`).
- The plugin id stays `youtube-to-obsidian` (no breakage in existing vaults). The display name is "Media to Obsidian".

### Communication

Frontend ↔ Backend communicate via:
- `POST /process` — SSE stream for the import pipeline. Same endpoint for YouTube and podcasts; backend detects source from the URL.
- `GET/POST/DELETE /cookies` — Cookie file management
- `GET /whisper/status` — Reports whether whisper-server is running, plus `last_activity`, `in_flight`, `idle_timeout_seconds`
- `POST /whisper/start` — Kickstart the whisper-server LaunchAgent (blocks until reachable, ~1–3 s). Idempotent.
- `POST /whisper/stop` — `launchctl kill SIGTERM` the LaunchAgent. Refuses if a transcription is in-flight.
- `GET /health` — Health check

## URL detection

`backend/main.py` calls `podcast.is_apple_podcast_url(url)` first. If it matches `podcasts.apple.com`, it branches into `_run_podcast_pipeline`; otherwise `_run_youtube_pipeline`. The frontend mirrors this in `url-utils.detectSource()`.

## Processing Pipeline

The `/process` endpoint runs a 4-step SSE streaming pipeline. Steps 1–2 differ per source; Steps 3–4 are shared.

### YouTube pipeline
1. **Metadata** — `youtube.get_video_metadata()` via yt-dlp
2. **Transcript** — `youtube.get_transcript()` via youtube-transcript-api, falls back to yt-dlp
3. **Claude** — `claude.stream_claude()` with `prompts.get_system_prompt(features, source="youtube")`
4. **Note** — `note.build_obsidian_note()` assembles the final markdown

### Podcast pipeline
1. **Resolve** — `podcast.resolve_episode()`:
   - Parses Apple Podcasts URL → `(show_id, episode_track_id, title_slug)`
   - iTunes lookup `?id=show_id&entity=podcast` → RSS feed URL
   - HEAD-follows the original Apple URL to extract the canonical episode title slug from the redirect
   - Parses the RSS feed (`feedparser`)
   - Matches the episode by slug-exact, then slug-token-overlap (≥0.5). **No silent fallback to "latest"** — raises if no match.
2. **Transcript**:
   - First tries `podcast:transcript` (Podcasting 2.0 tag) → `text/plain` / `text/vtt` / `application/srt` / `application/json` parsers
   - On miss, downloads audio enclosure to a tempfile and POSTs it to `http://127.0.0.1:2022/inference` (whisper.cpp `whisper-server`). On whisper-unreachable, returns a clear error pointing at `backend/scripts/whisper-setup.sh`.
3. **Claude** — same as YouTube but `source="podcast"` (the prompt swaps "video"→"episode", "channel"→"show")
4. **Note** — same `build_obsidian_note()`; `note._adapt_metadata()` switches the frontmatter/header layout based on `metadata["source"]`.

Each step emits SSE events (`stage`, `message`, extras) so the frontend can show real-time progress.

## Composable Prompt System

Prompts live in `backend/prompts/` and are composed at runtime. **Source-aware.**

### Adding a new prompt feature:

1. Create `backend/prompts/yourfeature.py` with three exports:
   ```python
   ROLE_PREAMBLE = "a role description"
   JSON_KEYS = [{"key": "your_key", "description": "What this key contains."}]
   RULES = """Rules for "your_key":\n- Rule one.\n- Rule two."""
   ```
2. Register it in `PROMPTS` dict in `backend/prompts/__init__.py`
3. Add the corresponding boolean flag to `TranscriptRequest` in `backend/models.py`
4. Wire the feature flag in `main.py`'s `_run_claude_and_note` (add to `features` list, extract result)
5. Pass the new content to `build_obsidian_note()` (extend its signature if needed)

The prompt composer substitutes "video"→"episode" automatically when `source="podcast"`; rules can use generic wording without further changes.

## Build & Run Commands

### Backend (LaunchAgent)

The backend runs as a native Python process via a macOS LaunchAgent. Managed with scripts in `backend/scripts/`:

```bash
./backend/scripts/start.sh           # Load and start the LaunchAgent
./backend/scripts/stop.sh            # Unload (stop) the LaunchAgent
./backend/scripts/logs.sh            # Tail stdout + stderr logs
./backend/scripts/install.sh         # First-time install (creates the plist)
./backend/scripts/update.sh          # Pull changes and restart
./backend/scripts/whisper-setup.sh   # Install whisper.cpp + model + LaunchAgent (one-time)
curl http://localhost:8000/health    # Health check
curl http://localhost:8000/whisper/status   # Is whisper-server reachable?
```

After changing `backend/.env`, restart with `stop.sh` then `start.sh`.

### whisper.cpp (LaunchAgent for whisper-server)

`backend/scripts/whisper-setup.sh` is idempotent. It:
1. Detects an existing `~/whisper.cpp` source build first; otherwise installs `whisper-cpp` + `ffmpeg` via Homebrew.
2. Downloads `ggml-large-v3-turbo.bin` (~800 MB) into `~/models/` if not present.
3. Writes `~/Library/LaunchAgents/com.whisper.server.plist` with the right binary path, model path, `GGML_METAL_PATH_RESOURCES`, and `WorkingDirectory=/tmp` (so whisper-server's relative-path temp WAV is writable for the ffmpeg child process).
4. The plist uses `RunAtLoad=false` and `KeepAlive=false` — the LaunchAgent is *registered* on login but does not auto-start. The backend manages lifecycle via `launchctl kickstart` / `launchctl kill SIGTERM` against `gui/$UID/com.whisper.server`.
5. The setup script smoke-tests by kickstarting the agent once, polling `/`, and stopping it again so the post-install state is "registered, stopped".

#### Lifecycle

- The Obsidian plugin posts `POST /whisper/start` on plugin load when `keepWhisperWarm` is on, and `POST /whisper/stop` on `onunload`.
- When `keepWhisperWarm` is off (default), whisper-server stays stopped until `_run_podcast_pipeline` falls through the RSS-transcript check; `whisper.transcribe_url` then lazy-starts the agent (~1–3 s) before transcribing.
- A background task in `main.py`'s FastAPI `lifespan` polls every 60 s and stops the server after `WHISPER_IDLE_TIMEOUT_SECONDS` (1800 s) of inactivity. The `_in_flight` counter in `whisper.py` blocks the watcher from killing the server during a long transcription.
- `last_activity` is bumped by `whisper.mark_activity()`, called from `start_server` and the `in_flight` context manager exit. The watcher treats `last_activity == 0` (server up but no observed activity, e.g. started by some other process) as activity-now.

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
- `feedparser` — podcast RSS parsing (Podcasting 2.0 namespaces)
- `httpx` — HTTP client (RSS fetch, audio download, whisper-server POST)
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
| `transcript` / `transcript_done` | Step 2 progress (YouTube + podcast convergence) | `segments`, `transcript_chars` |
| `transcript_rss` | Step 2 podcast — RSS check in progress | `step`, `total_steps` |
| `transcript_whisper_starting` | Step 2 podcast — whisper-server is being lazy-started | `step`, `total_steps` |
| `transcript_whisper_download` | Step 2 podcast — audio download | `step`, `total_steps` |
| `transcript_whisper_running` | Step 2 podcast — whisper transcription in progress | `step`, `total_steps` |
| `claude` / `claude_extended` / `claude_focus` | Step 3 progress | `input_tokens`, `output_tokens`, `elapsed` |
| `claude_done` | Step 3 complete | `input_tokens`, `output_tokens`, `elapsed`, `cost_usd` |
| `building` | Step 4 progress | `step`, `total_steps` |
| `done` | Final result | `filename`, `content`, `metadata`, `resources`, `source` (`"youtube"`/`"podcast"`) |
| `error` | Failure at any point | — |

## Cookie Handling (YouTube only)

Three-tier priority for YouTube authentication:
1. Browser extraction (`cookiesfrombrowser` in yt-dlp) — highest priority
2. Explicit cookie file path
3. Uploaded cookie file (`backend/cookies.txt`)

Podcasts don't need cookies — RSS feeds are public.

## Notes for Claude

- When modifying the backend, keep modules independent. If a change touches `youtube.py`, it should NOT require changes to `claude.py`, `podcast.py`, `whisper.py`, or `note.py` unless the interface contract changes.
- When modifying SSE events, update the backend emitter (`main.py`) and the frontend consumer in `sse-handler.ts` together.
- The prompt system is designed for extension. Prefer adding new prompt modules over modifying `base.py`.
- Token counting in `claude.py` is approximate during streaming (1 delta ≈ 1 token) but corrected by `message_delta` at the end.
- The `metadata` dict for podcasts contains a `rss_entry` field (a feedparser object). It is NOT JSON-serializable — `_run_podcast_pipeline` strips it before sending the `done` event. Don't add it back unless you also strip it before serialization.
- Apple Podcasts episode resolution: do NOT match by `?i=` value against RSS `guid` or `itunes:episode` — those are different namespaces. Use the redirect-based slug match in `podcast.resolve_canonical_slug()`.
