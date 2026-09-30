# CLAUDE.md — Media to Obsidian (YouTube + Podcasts + Web Articles)

## Project Overview

A full-stack app that imports YouTube videos, Apple Podcasts episodes **and** web articles into Obsidian as structured notes — one URL at a time, or whole channels / playlists / podcast shows through a persistent backend batch queue. The plugin auto-detects the source from the pasted URL, fetches transcripts (RSS / YouTube CC / whisper.cpp) or the article's main text, processes them with Claude AI (summary + cleaned transcript + topics + optional resources/extended/focused summaries; articles get summary + key points + knowledge-base frontmatter instead of a transcript), and creates formatted markdown notes.

- **Frontend:** TypeScript Obsidian plugin (`obsidian-plugin/`)
- **Backend:** Python FastAPI with SSE streaming (`backend/`)
- **Deployment:** macOS LaunchAgents (uvicorn + whisper-server, managed via `backend/scripts/`)
- **AI:** Anthropic Claude API (Haiku 4.5 / Sonnet 5 / Opus 5.5; effort `low` for base, `medium` for extended/focus)
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
| `youtube.py` | Video ID extraction, metadata (yt-dlp), transcript fetching (youtube-transcript-api 1.x + yt-dlp fallback), `YouTubeBlockedError` on bot checks / IP blocks, channel + playlist listing (`list_collection`) | `cookies` |
| `podcast.py` | Apple URL parsing, iTunes lookup, RSS fetch (`feedparser`), episode matching (slug, or guid in batch mode), RSS transcript extractor (Podcasting 2.0), show listing (`list_show_episodes`) | — |
| `whisper.py` | Local whisper-server HTTP client. Downloads audio → POSTs to `whisper-server` → returns transcript | — |
| `web.py` | Web article fetch (HTML only — no images/scripts/linked pages), accessibility checks, main-text + metadata extraction via `trafilatura`, URL cleaning | — |
| `claude.py` | Claude API streaming, response parsing, model constants | — |
| `local_llm.py` | Optional local LLM via an OpenAI-compatible server (`LOCAL_LLM_URL`, default MLX Core on `:11234`): `list_models()` (served models + installed ones under `LOCAL_LLM_MODELS_DIR`, each with a `status`), `stream_local()` with the same yield contract as `stream_claude()`. Model ids are prefixed `local:` | — |
| `note.py` | Source-agnostic Obsidian markdown note assembly (YouTube + podcast variants, separate web article layout) | — |
| `cookies.py` | Cookie file persistence (save/delete/check) | — |
| `vault.py` | Batch queue writes into the vault: path validation, atomic note write + collision suffix, resource stubs, frontmatter id scan | — |
| `batch_queue.py` | SQLite job table (`backend/data/batch.db`) + the single asyncio worker; lanes, pacing, hourly cap, block backoff. The per-item work is injected by `main.py` | — |
| `prompts/__init__.py` | Source-aware prompt registry and composition engine | prompt feature modules |
| `prompts/base.py` | Base prompt: transcript cleaning + short summary + topics | — |
| `prompts/pasted.py` | Added with `article` when the user pasted the page text by hand: recovers `title`, `authors`, `published` and the verbatim `body_first_words` / `body_last_words` so the backend can trim page clutter | — |
| `prompts/article.py` | Base prompt for `source="web"` (replaces `base`): tldr, summary, key points, topics, content type, useful-for. No transcript key — articles are never reproduced | — |
| `prompts/extended.py` | Extended summary prompt: topic-by-topic editorial rewrite | — |
| `prompts/focus.py` | Focus topic prompt: deep-dive on a user-supplied topic | — |
| `prompts/resources.py` | Resources prompt: extract products/tools/services as wiki-link stubs | — |

**Rules:**
- `youtube.py`, `podcast.py`, `whisper.py`, `web.py`, `claude.py`, `local_llm.py`, `note.py`, `cookies.py`, `vault.py` and `batch_queue.py` must NOT import from each other. They are independent modules orchestrated only by `main.py`.
- `models.py` contains only Pydantic models — no logic.
- Prompt modules expose only `ROLE_PREAMBLE`, `JSON_KEYS`, and `RULES` — no functions.
- The prompt composer (`prompts.get_system_prompt`) takes a `source` argument (`"youtube"`, `"podcast"` or `"web"`) so the same feature flags work for all sources — the wording adapts. For `"web"` it swaps `base` for `article`.

### Frontend (`obsidian-plugin/src/`)

Modular TypeScript plugin with one file per concern:

| File | Responsibility | Depends on |
|---|---|---|
| `main.ts` | `YTObsidianPlugin` class — plugin lifecycle, settings persistence, source-aware note creation | `settings`, `import-modal`, `sse-handler` (type) |
| `settings.ts` | `YTObsidianSettings` interface, `DEFAULT_SETTINGS`, `YTObsidianSettingTab` (settings UI: parent-folder toggle + name, per-source folder names, whisper language, cookie management) | `main` (type only) |
| `import-modal.ts` | `YouTubeImportModal` — single modal that auto-detects source, adapts UI, builds the request, displays progress | `main` (type only), `url-utils`, `sse-handler` |
| `sse-handler.ts` | `processSSEStream()` — SSE parsing + dispatch via callbacks. Knows about all stages (incl. `transcript_rss`, `transcript_whisper_download`, `transcript_whisper_running`) | — |
| `url-utils.ts` | `detectSource()` (YouTube vs Apple Podcasts vs web vs null), `detectCollection()` (channel / playlist / show — mirror of the backend detectors), `extractVideoId()`, `extractAppleEpisodeId/ShowId()`, `cleanWebUrl()` (mirror of `web.clean_url`), `collectKnownIds()` + `findExistingNote()` (frontmatter ids via `metadataCache`, content scan as fallback) | — |
| `batch-api.ts` | Types + fetch helpers for the `/batch` endpoints | — |
| `batch-panel.ts` | `BatchPanel` — batch section of the Import modal (count, min length, preview + cost estimate, confirm) | `main` (type only), `url-utils`, `batch-api` |
| `queue-view.ts` | `QueueView` — sidebar that polls `GET /batch` (progress, lane status, cancel / retry / resume) | `main` (type only), `batch-api` |

**Rules:**
- `sse-handler.ts`, `url-utils.ts` and `batch-api.ts` are pure modules with no plugin dependencies — they must not import from `main`, `settings`, or `import-modal`.
- `settings.ts`, `import-modal.ts`, `batch-panel.ts` and `queue-view.ts` import `main.ts` only as a type (`import type`) to avoid circular runtime dependencies.
- SSE event stage handling lives in `sse-handler.ts`. When adding new SSE stages, update the switch statement there (not in `import-modal.ts`).
- Folder layout is configurable: `youtubeFolder`, `podcastFolder`, `articleFolder` and the shared `resourcesFolder` (defaults `YouTube`, `Podcasts`, `Articles`, `Mentioned_Resources`). With `useParentFolder` on (default) they live below `mediaTranscriptsFolder` (default `MEDIA_Transcripts`); off, they are siblings in the vault root. Empty names fall back to the defaults. Resolve paths only via `plugin.folderForSource()` / `plugin.resourceFolder()` (and `plugin.batchTargets()` for the absolute paths sent with a batch).
- The plugin id stays `youtube-to-obsidian` (no breakage in existing vaults). The display name is "Media to Obsidian".

### Communication

Frontend ↔ Backend communicate via:
- `POST /process` — SSE stream for the import pipeline. Same endpoint for YouTube and podcasts; backend detects source from the URL.
- `GET/POST/DELETE /cookies` — Cookie file management
- `GET /whisper/status` — Reports whether whisper-server is running, plus `last_activity`, `in_flight`, `idle_timeout_seconds`
- `POST /whisper/start` — Kickstart the whisper-server LaunchAgent (blocks until reachable, ~1–3 s). Idempotent.
- `POST /whisper/stop` — `launchctl kill SIGTERM` the LaunchAgent. Refuses if a transcription is in-flight.
- `POST /batch` — list + filter + dedup + estimate a channel / playlist / show; creates a `pending_confirmation` batch (expires after 30 min)
- `POST /batch/{id}/confirm` · `/cancel` · `/retry` (failed items) · `POST /batch/lanes/{lane}/resume` (lift a block early)
- `GET /batch` (recent batches + item states + lane status) · `GET /batch/{id}`
- `GET /local-models` — `{server, models}`; each model has `status` `loaded` / `unloaded` / `switch` (installed in another folder — pick it in the MLX Core menu) / `offline`. The modal shows the "Local" dropdown (with ↻ refresh) only when the list is non-empty; `switch` / `offline` entries are disabled
- `GET /health` — Health check

## URL detection

`backend/main.py`'s `_detect_source()` checks `podcast.is_apple_podcast_url(url)` first → `_run_podcast_pipeline`; then `youtube.is_youtube_url(url)` (or a non-http string, to keep the YouTube error message) → `_run_youtube_pipeline`; any other `http(s)` URL → `_run_web_pipeline`. The frontend mirrors this in `url-utils.detectSource()`.

Web article duplicate detection: the backend writes `source: "<clean_url>"` to the frontmatter and the frontend searches for the same string built by `cleanWebUrl()`. Keep the two cleaners in sync.

## Processing Pipeline

The `/process` endpoint runs a 4-step SSE streaming pipeline. Steps 1–2 differ per source; Steps 3–4 are shared.

### YouTube pipeline
1. **Metadata** — `youtube.get_video_metadata()` via yt-dlp
2. **Transcript** — `youtube.get_transcript()` via youtube-transcript-api, falls back to yt-dlp
3. **Claude** — `claude.stream_claude()` with `prompts.get_system_prompt(features, source="youtube")`
4. **Note** — `note.build_obsidian_note()` assembles the final markdown

### Batch queue (channels, playlists, podcast shows)
1. **Preview** — `POST /batch`: `youtube.list_collection()` (flat yt-dlp listing of the `/videos` tab or a playlist — durations included, Shorts and `duration=None` dropped) or `podcast.list_show_episodes()` (iTunes `entity=podcastEpisode` → `trackId` + `episodeGuid`, joined to the RSS feed by guid; max 200; missing durations are estimated from the show's median). Items whose ids are in `known_ids` (plugin, `metadataCache`) or `vault.scan_ids()` are skipped: the listing always covers the whole window (up to 200), duplicates are dropped and the first `count` new items are returned, plus `skipped_duplicates` / `searched` / `window_exhausted` so the plugin can say "everything is already in your vault". Per-item cost comes from `_estimate_item_cost()` (duration heuristic, constants at the top of the batch section in `main.py`).
2. **Confirm** — the batch becomes `queued`; the worker in `lifespan` picks it up.
3. **Worker** — one item at a time via `_process_batch_item()`: re-scan the vault for the id → run the normal `_run_youtube_pipeline` / `_run_podcast_pipeline` generator (with `skip_live`, `episode_guid`, `sleep_requests`) → `vault.write_note()` + `vault.create_resource_stubs()`. The backend is the note writer here (the plugin sends absolute paths; `vault.validate_paths` keeps writes inside the vault).
4. **Pacing** — YouTube lane: random 15–45 s pause, ≤ 60 items/hour, guest session first (one retry with Safari cookies after a bot check). An `error` event with `blocked=True` pauses the lane 30 min → 2 h → 6 h and returns the item to pending; podcasts keep going. Livestreams yield `skipped=True`.
5. **Recovery** — `init_db()` resets `running` items to `pending` on start.

### Web article pipeline
1. **Fetch** — `web.fetch_article()` GETs the HTML document only (Safari UA, redirects followed, 20 s timeout, 5 MB cap). Clear errors for 401/403/404/429, non-HTML content types and unreachable hosts.
2. **Extract** — `trafilatura` keeps the main text only (no comments/images/links/formatting). Fewer than `MIN_WORDS` words → error (paywall / login wall / JS-rendered). Text is capped at `MAX_TEXT_CHARS`. Metadata: title, authors, site, date, description, language, word count, reading time, accessed date.
3. **Claude** — `source="web"`; never chunked (output is small). Default model is Haiku.
**Manual paste fallback:** when `fetch_article()` raises a `WebFetchError` with `manual_ok=True` (everything except 404 / non-HTML), the backend emits `web_blocked` instead of `error`. The modal then shows a paste box and re-POSTs with `manual_text`. `web.pasted_article()` runs a rule-based cleanup (`clean_pasted_text`: chrome lines, counts, duplicates), the `pasted` prompt feature returns title/authors/date + body boundaries, and `web.trim_to_body()` recomputes the word count from the article body only.

4. **Note** — `note._build_web_note()`; frontmatter includes `source` (the cleaned URL), `author`, `site`, `published`, `accessed`, `content_type`, `description` (tldr), `useful_for`, `topics`.

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
- `trafilatura` — web article main-text + metadata extraction
- `httpx` — HTTP client (RSS fetch, audio download, whisper-server POST)
- `pydantic` — data validation

### Frontend (TypeScript)
- `obsidian` — Obsidian plugin API
- `esbuild` — bundler
- `typescript` 4.7.4

## Environment Variables

Both `.env` files are gitignored. Copy the `.env.example` templates on first setup.

- `backend/.env`: `ANTHROPIC_API_KEY` — required for Claude API. `LOCAL_LLM_URL` — optional, OpenAI-compatible local server (default `http://127.0.0.1:11234`). `LOCAL_LLM_MODELS_DIR` — installed models (`<org>/<model>`, default `~/.mlx-serve/models`). The LaunchAgent must be restarted (`stop.sh` + `start.sh`) after changes.
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
| `web_fetch` | Step 1 web — fetching the HTML page | `step`, `total_steps` |
| `web_extract_done` | Step 2 web — main text extracted | `words`, `transcript_chars` |
| `web_blocked` | Web fetch refused/failed — terminal; frontend asks the user to paste the page text and retries with `manual_text` | — |
| `claude` / `claude_extended` / `claude_focus` | Step 3 progress | `input_tokens`, `output_tokens`, `elapsed` |
| `claude_done` | Step 3 complete | `input_tokens`, `output_tokens`, `elapsed`, `cost_usd` |
| `building` | Step 4 progress | `step`, `total_steps` |
| `done` | Final result | `filename`, `content`, `metadata`, `resources`, `source` (`"youtube"`/`"podcast"`/`"web"`) |
| `error` | Failure at any point | `blocked` (YouTube bot check / IP block), `skipped` (livestream with `skip_live`) — read by the batch queue |

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
- The ribbon icon is a bundled SVG registered with `addIcon()` — don't switch it back to a Lucide id; renamed Lucide ids render as an invisible (but clickable) ribbon button.
- Apple Podcasts episode resolution: do NOT compare the `?i=` value directly with the RSS `guid` or `itunes:episode` — those are different namespaces. Single imports use the redirect-based slug match in `podcast.resolve_canonical_slug()`; batch mode bridges the two via the iTunes episode lookup (`trackId` → `episodeGuid`) and `resolve_episode_by_guid()`.
- Frontmatter id keys (`youtube_id`, `apple_episode_id`, `episode_guid`) drive duplicate detection in the plugin and in `vault.scan_ids()`. Legacy notes without them are matched by parsing ids out of `url:` — keep both parsers in sync.
- Local models: `main._stream_llm()` / `_resolve_llm()` dispatch `local:<id>` to `local_llm`, everything else to Claude. Local models cost $0, use the same chunking as Haiku for long transcripts (their context is small), and `stream_local()` refuses input that exceeds the model's `context_length`. mlx-serve silently answers with the *loaded* model for any unknown name, so `stream_local()` refuses models the server doesn't list — keep that guard.
- Claude calls: `claude.resolve_model()` maps old plugin model ids to the current ones. Thinking is on by default for Sonnet 5 / Opus 5.5; `stream_claude()` appends only `text_delta`s and passes `output_config.effort` (never on Haiku). `stop_reason == "refusal"` becomes an SSE `error`.
