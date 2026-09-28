# Research — Batch import queue for YouTube channels/playlists and podcast shows

**Date:** 2026-09-28
**Goal:** Import the last X videos (or all, if ≤ 20) of a YouTube channel or playlist, filtered by a minimum duration, and the last X episodes of an Apple Podcasts show. A backend queue processes one item at a time with pauses, survives connection loss, and skips items already in the vault (matched by frontmatter).
**Informs:** standalone. Feeds a later `/plan` for the batch-queue feature.

---

## Question

1. How do we **list** a channel's or playlist's videos (newest first, with durations) cheaply and without extra per-video requests?
2. What pacing keeps a **residential Mac** below YouTube's rate and bot limits when fetching metadata and transcripts for many videos in a row?
3. How do we **list** the last X episodes of a show from an Apple Podcasts show URL, with IDs that match the existing single-episode notes?
4. Which **queue mechanism** fits the current FastAPI + LaunchAgent setup and loses at most the in-flight item on a crash?
5. How should the plugin do **frontmatter-based duplicate detection** below the plugin's root folder?

A good answer decides the listing APIs, the default pacing, the queue storage, the ID keys in frontmatter, and who writes the notes (backend or plugin).

## Decisions (user, 2026-09-28)

| # | Decision | Consequences |
|---|---|---|
| D1 | **The backend writes finished notes directly into the vault.** The media folders are separate from the rest of the vault. | Resolves `RES-15`. The plugin must send the absolute vault path and resolved folder paths with each batch. The backend takes over note-collision handling and resource-stub creation (`RES-20`). |
| D2 | **Exclude livestreams.** | List only the `/videos` tab; never request `/streams`. Skip items whose per-video metadata reports a live, upcoming or past-live status (`RES-21`). |
| D3 | **Minimum duration defaults to 15 minutes.** The user can change it per batch. | The filter uses `RES-01` durations (YouTube) or iTunes/RSS durations (podcasts). |
| D4 | **Pacing cap: 60 YouTube videos per hour** for now. | A random 15–45 s pause between items plus a hard hourly ceiling of 60; backoff on block signals (`RES-10`). Podcasts are not capped (`RES-14`). |
| D5 | **Show a cost estimate first; start only after the user confirms.** | A batch is created in a `pending_confirmation` state with a per-item and total estimate (`RES-19`), then starts on an explicit confirm call. The estimate must use correct prices (`RES-18`). |
| D6 | **Update every Claude model to the latest in its tier:** Haiku 4.5 (alias `claude-haiku-4-5`), Sonnet 5 (`claude-sonnet-5`), Opus 5.5 (`claude-opus-5-5`). | Lands before the batch queue, so the D5 estimate and `MODEL_PRICING` use the new IDs and prices (`RES-22`–`RES-24`). |

## Search strategy

- Read the codebase to find constraints: `backend/youtube.py`, `backend/podcast.py`, `backend/note.py`, `obsidian-plugin/src/url-utils.ts` and the duplicate check in `import-modal.ts`.
- Ran the installed yt-dlp **2026.03.17** against a real channel (`@lexfridman`), including its `/videos`, `/shorts` and `/streams` tabs, and a real playlist. Tested `extract_flat="in_playlist"`, `playlist_items`, and the `youtubetab:approximate_date` extractor argument.
- Read the `-t sleep` preset values from yt-dlp's own source (`yt_dlp/options.py`).
- Called the iTunes Lookup API with `entity=podcastEpisode` against a real show, and the YouTube channel RSS feed.
- Fetched the yt-dlp Extractors wiki (rate limits), the youtube-transcript-api README, the Apple Search API docs and the Obsidian `MetadataCache` reference.
- Checked PyPI for the current library versions.

## Sources

| ID | Type | Reliability | Title / Description | URL | Accessed |
|---|---|---|---|---|---|
| `SRC-01` | docs | official-docs | yt-dlp wiki: Extractors → YouTube (rate limits, cookies, PO tokens) | https://github.com/yt-dlp/yt-dlp/wiki/Extractors | 2026-09-28 |
| `SRC-02` | repo | official-docs | yt-dlp source, `options.py` `_PRESET_ALIASES['sleep']` (installed 2026.03.17) | https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/options.py | 2026-09-28 |
| `SRC-03` | docs | official-docs | yt-dlp README (sleep, playlist-items, match-filters, download-archive) | https://github.com/yt-dlp/yt-dlp/blob/master/README.md | 2026-09-28 |
| `SRC-04` | independent-research | independent-research | Local experiment: yt-dlp flat extraction on `@lexfridman` (channel root, /videos, /shorts, /streams) and playlist `PLrAXtmErZgOdP_8GztsuKi9nrraNbKKp4` | local run | 2026-09-28 |
| `SRC-05` | docs | official-docs | youtube-transcript-api README (instance API, IP blocks, cookie auth broken, proxies) | https://github.com/jdepoix/youtube-transcript-api | 2026-09-28 |
| `SRC-06` | release notes | official-docs | PyPI: youtube-transcript-api 1.2.4 (2026-01-29), 1.0.0 (2025-03-11); yt-dlp 2026.8.19 | https://pypi.org/project/youtube-transcript-api/ | 2026-09-28 |
| `SRC-07` | article | secondary-article | IP-blocked guide: RequestBlocked vs IpBlocked, anecdotal 24–48 h block duration | https://github.com/hxckya/youtube-transcript-ip-blocked-guide | 2026-09-28 |
| `SRC-08` | API ref | official-docs | Apple Search/Lookup API (≈20 calls/min, `limit` 1–200, `podcastEpisode` entity) | https://performance-partners.apple.com/search-api | 2026-09-28 |
| `SRC-09` | independent-research | independent-research | Local experiment: `itunes.apple.com/lookup?id=1434243584&entity=podcastEpisode` (fields, 200 cap) | local run | 2026-09-28 |
| `SRC-10` | independent-research | independent-research | Local experiment: YouTube channel RSS `feeds/videos.xml?channel_id=…` | local run | 2026-09-28 |
| `SRC-11` | API ref | official-docs | Obsidian API: `MetadataCache` (`getFileCache`, `changed`, `resolved`) | https://docs.obsidian.md/Reference/TypeScript+API/MetadataCache | 2026-09-28 |
| `SRC-12` | repo | independent-research | This repo: `backend/youtube.py`, `backend/podcast.py`, `backend/note.py`, `backend/main.py` (`MODEL_PRICING`, chunking), `obsidian-plugin/src/main.ts` (`createNote`, `createResourceStubs`), `url-utils.ts`, `import-modal.ts`, `requirements.txt` | local | 2026-09-28 |
| `SRC-13` | docs | official-docs | Anthropic model pricing table (claude-api skill, cached 2026-06-24) | https://docs.anthropic.com/en/docs/about-claude/pricing | 2026-09-28 |
| `SRC-14` | docs | official-docs | claude-api skill: current model table, thinking/effort matrix, migration notes for Sonnet 5 and Opus 5.5 (thinking defaults, removed params, `refusal` stop reason) | https://docs.anthropic.com/en/docs/about-claude/models | 2026-09-28 |

## Findings

| ID | Severity | Confidence | Finding | Sources | Supersedes |
|---|---|---|---|---|---|
| `RES-01` | HIGH | high | `YoutubeDL({"extract_flat": "in_playlist", "playlist_items": "1:N"})` lists a channel's **`/videos` tab** or a **playlist** in ~1 s in a single request. Entries arrive newest-first with `id`, `title`, `url` and **`duration` (seconds)**. No per-video request is needed to apply a minimum-duration filter. | `SRC-04` | — |
| `RES-02` | HIGH | high | A bare channel URL (`youtube.com/@handle`) returns **tabs** (`Videos`, `Shorts`), not videos. The backend must normalise channel URLs to `…/videos`. Shorts live in a separate tab, so the `/videos` tab already excludes them. A channel without a Streams tab errors on `/streams`; livestream archives (common for podcast-style channels) only appear there. | `SRC-04` | — |
| `RES-03` | MEDIUM | high | Flat entries carry **no upload date or timestamp**. `extractor_args={"youtubetab": {"approximate_date": [""]}}` did **not** add one on the current layout. "Last X" must rely on tab order (newest first). The real `upload_date` comes from the per-video `get_video_metadata()` call that already runs during processing. | `SRC-04`, `SRC-03` | — |
| `RES-04` | MEDIUM | high | Shorts entries have `duration=None` and a `/shorts/` URL. A playlist can contain Shorts, so the duration filter must treat `None` as "exclude when a minimum is set" and also drop `/shorts/` URLs (the README's `original_url!*=/shorts/` pattern). | `SRC-04`, `SRC-03` | — |
| `RES-05` | HIGH | high | For the whole channel `/videos` tab, `playlist_count` is `None` (only playlists report it: 447 here). "More than 20?" cannot be answered for a channel without paging the full list. **Probe with `playlist_items="1:21"`**: 21 results means "more than 20". | `SRC-04` | — |
| `RES-06` | HIGH | high | YouTube's documented thresholds: **guest session ≈ 300 videos/hour (~1,000 webpage/player requests/hour)**; logged-in account ≈ 2,000 videos/hour. Going over them returns "This content isn't available, try again later". The wiki recommends 5–10 s between downloads. | `SRC-01` | — |
| `RES-07` | HIGH | high | yt-dlp's `-t sleep` preset is `--sleep-subtitles 5 --sleep-requests 0.75 --sleep-interval 10 --max-sleep-interval 20`. The Python equivalents are `sleep_interval_subtitles`, `sleep_interval_requests`, `sleep_interval`, `max_sleep_interval`. This gives a sensible, officially endorsed baseline for inter-item jitter (10–20 s random). | `SRC-02`, `SRC-03` | — |
| `RES-08` | HIGH | high | Using **account cookies** risks a temporary or permanent **account ban**, according to the yt-dlp maintainers. A long unattended batch should default to the guest session and use cookies only as a fallback on bot checks. The current code applies cookies to every request when configured. | `SRC-01`, `SRC-12` | — |
| `RES-09` | HIGH | high | The pinned `youtube-transcript-api==0.6.2` is ~1.5 years old; current is **1.2.4**. 1.x uses an **instance** API (`YouTubeTranscriptApi().fetch()` / `.list()`) instead of the static `list_transcripts`, and adds explicit `RequestBlocked` / `IpBlocked` exceptions. **Cookie auth is currently broken upstream**, so the `cookies=` argument in `get_transcript()` gives no protection. | `SRC-05`, `SRC-06`, `SRC-12` | — |
| `RES-10` | HIGH | medium | Residential IPs get blocked when they make "too many requests". Upstream publishes no threshold. Anecdotal block duration is **24–48 h**. On `RequestBlocked`/`IpBlocked`, or yt-dlp's "try again later" / "Sign in to confirm you're not a bot", the queue must **pause the whole YouTube lane with long backoff**, not retry the item immediately. | `SRC-05`, `SRC-07`, `SRC-01` | — |
| `RES-11` | MEDIUM | high | The YouTube channel RSS (`/feeds/videos.xml?channel_id=`) returns only the **latest 15** entries, with `published` dates but **no duration**, and mixes in Shorts. It is useful only as a cheap "anything new?" poll, not as the listing source. | `SRC-10` | — |
| `RES-12` | HIGH | high | `itunes.apple.com/lookup?id=<showId>&entity=podcastEpisode&limit=N` lists a show's episodes **newest first**. Each has `trackId` (= the `?i=` value), **`episodeGuid` (= the RSS `<guid>`)**, `trackViewUrl` (the canonical Apple episode URL with `?i=`), `releaseDate` and `feedUrl`. This maps Apple episodes to RSS entries **deterministically**, so no slug matching is needed in batch mode. | `SRC-09` | — |
| `RES-13` | MEDIUM | high | The iTunes lookup caps at **200 episodes** (`limit=300` → 200 episodes + 1 show record) and is rate-limited to **≈20 calls/min**. `trackTimeMillis` is often `null`. The duration filter for podcasts must fall back to RSS `itunes:duration`, which `podcast._parse_duration()` already parses. | `SRC-08`, `SRC-09`, `SRC-12` | — |
| `RES-14` | MEDIUM | high | Podcasts carry no YouTube-style ban risk: the RSS feed is fetched **once per batch**, and the audio comes from the publisher's CDN. The bottleneck is whisper runtime (minutes per episode), which sequential processing already covers. A short pause (~2–5 s) is enough. | `SRC-12`, `SRC-08` | — |
| `RES-15` | HIGH | high | The backend **never writes notes**. The `done` SSE event carries `content`, and the plugin writes the file. A server-side queue that keeps running while Obsidian is closed or disconnected must **persist finished notes** (e.g. in SQLite) until the plugin collects them, or write straight to a configured vault path. This is the main architectural decision. | `SRC-12` | — |
| `RES-16` | MEDIUM | high | Current duplicate detection (`findExistingNote`) reads **every markdown file's content** with `vault.read` and substring-matches the ID. For batch mode, the plugin can instead use `app.metadataCache.getFileCache(file)?.frontmatter`. It is synchronous and in-memory, and the `changed`/`resolved` events keep it current. | `SRC-11`, `SRC-12` | — |
| `RES-17` | MEDIUM | high | The frontmatter has no stable ID key today. YouTube/podcast notes store `url:` exactly as the user pasted it (`youtu.be/…`, `&t=`, Apple `?i=…&uo=4`); web notes store `source:`. Reliable frontmatter dedup needs **explicit keys** (e.g. `youtube_id`, `apple_episode_id`, `episode_guid`), with a fallback that parses IDs out of `url:` for existing notes. | `SRC-12` | — |
| `RES-18` | HIGH | high | `MODEL_PRICING` in `backend/main.py` is **stale for 2 of 3 models**. Haiku 4.5 is **$1 / $5** per M tokens (code: $0.80 / $4); Opus 4.7 is **$5 / $25** (code: $15 / $75); Sonnet 4.6 at $3 / $15 is correct. The pre-run estimate (D5) and the reported `cost_usd` both depend on this table: Haiku is under-reported by 20%, Opus over-reported by 3×. | `SRC-13`, `SRC-12` | — |
| `RES-19` | MEDIUM | medium | The estimate has to be made **before any transcript is fetched**, so duration is the only input. Heuristic: speech ≈ 150 words/min × ≈ 1.3 tokens/word ≈ **200 input tokens per minute**, plus the fixed system prompt. The base prompt returns a cleaned transcript, so **output ≈ input** plus summary and topics. Transcripts over 25k chars (≈ 30 min) take the chunked path in `main.py` (`_CHUNK_THRESHOLD_CHARS`), which adds per-chunk system prompts. Extended/focus/resources features add extra calls. Show the estimate as a range (e.g. ±30%), then record the actual `cost_usd` per item to calibrate the constants later. | `SRC-12`, `SRC-13` | — |
| `RES-20` | HIGH | high | Once the backend writes notes (D1), it must port two plugin behaviours. **`createNote`**: create the folder, and on a path collision append `-<timestamp>`. **`createResourceStubs`**: skip a stub when any file in the resources folder has the same basename (case-insensitive); otherwise create an empty `<name>.md`. The plugin knows the vault's absolute path (desktop `FileSystemAdapter.getBasePath()`) and the resolved folders (`folderForSource()` / `resourceFolder()`), so it must send them with the batch; the backend cannot resolve Obsidian settings itself. Write each note via temp file + `os.replace` so a crash never leaves a half-written note for Obsidian's file watcher to index. | `SRC-12` | — |
| `RES-21` | MEDIUM | medium | Livestream exclusion (D2): the `/videos` tab does not contain stream archives (they live in `/streams`, `RES-02`). Playlists can contain them, and flat entries report `live_status=None` even for normal videos (`SRC-04`), so livestreams cannot be detected at listing time. Upcoming or live entries usually have no duration, so the D3 filter drops them. Past livestreams in playlists are caught at processing time via `live_status` in the full per-video metadata (`was_live` / `is_live` / `is_upcoming`) and marked `skipped`, not `failed`. | `SRC-04`, `SRC-03` | — |
| `RES-22` | HIGH | high | The app pins last-generation models in `claude.py` (`VALID_MODELS`, `DEFAULT_MODEL`, `DEFAULT_EXTENDED_MODEL`, `MODEL_MAX_OUTPUT_TOKENS`), `models.py` (request defaults), `main.py` (`MODEL_PRICING`, plus the chunking check that compares against the Haiku ID) and `import-modal.ts` (select values, extended-model switch). The latest per tier are **Haiku 4.5** `claude-haiku-4-5` ($1 / $5, 64K max output; the code caps it at 8192), **Sonnet 5** `claude-sonnet-5` ($2 / $10, 128K) and **Opus 5.5** `claude-opus-5-5` ($4 / $20, 128K). Both upgrades are *cheaper* than the models they replace (Sonnet 4.6 $3 / $15, Opus 4.7 $5 / $25). | `SRC-14`, `SRC-12` | `RES-18` (prices for Sonnet/Opus) |
| `RES-23` | HIGH | high | **Thinking is on by default** on Sonnet 5 (adaptive when `thinking` is omitted) and **cannot be disabled** on Opus 5.5 (`{type: "disabled"}` returns a 400; effort defaults to `medium`). The stream then carries `thinking_delta` events, and `stream_claude()` reads `event.delta.text` on every `content_block_delta`, so it will raise `AttributeError`. It must handle only `delta.type == "text_delta"`. Thinking tokens are billed as output, so the `RES-19` estimate needs headroom. Set `output_config.effort` explicitly per call; Haiku 4.5 rejects `effort`, so omit it there. `budget_tokens`, `temperature`/`top_p` and assistant prefill return a 400 on the new models (none are used today). | `SRC-14`, `SRC-12` | — |
| `RES-24` | MEDIUM | high | The pinned `anthropic==0.34.2` predates `output_config`, adaptive thinking and the `refusal` stop reason. Upgrade the SDK in the same change. Check `stop_reason == "refusal"` before `parse_claude_response()` and surface it as an SSE `error`. Opus 5.5's safety classifiers are broader, so a transcript can occasionally be refused. | `SRC-14`, `SRC-12` | — |

## Contradictions identified

| Finding A | Finding B | Sources A | Sources B | Note |
|---|---|---|---|---|
| `RES-12` | CLAUDE.md rule "do NOT match `?i=` against RSS `guid`" | `SRC-09` | `SRC-12` | Both hold. The rule forbids comparing the `?i=` *value* to `guid` directly. The iTunes episode lookup supplies the bridge (`trackId` → `episodeGuid`), so batch mode matches `episodeGuid == rss guid`. The rule should be reworded, not removed. |
| `RES-06` | `RES-10` | `SRC-01` | `SRC-05`, `SRC-07` | The yt-dlp wiki gives hourly thresholds; the transcript library says no threshold is known. The timedtext endpoint that youtube-transcript-api uses may be policed separately from the player endpoint, so the 300/h figure is an upper bound, not a target. |

## Alternatives evaluated

**Listing YouTube**

| Option | Pros | Cons | Verdict | Sources |
|---|---|---|---|---|
| yt-dlp flat extraction of `/videos` tab or playlist | 1 request, durations included, already a dependency | No dates, channel count unknown | **chosen** | `SRC-04` |
| YouTube channel RSS | Dates, very cheap | Only 15 items, no duration, includes Shorts | rejected (maybe useful later for "new since") | `SRC-10` |
| YouTube Data API v3 | Official, dates + durations | API key, quota, extra call for durations | rejected (new credential for little gain) | — |

**Listing podcasts**

| Option | Pros | Cons | Verdict | Sources |
|---|---|---|---|---|
| iTunes `entity=podcastEpisode` + existing RSS fetch | Exact Apple-ID ↔ GUID map, canonical Apple URLs, reuses `podcast.py` | Max 200, rate-limited, duration often missing | **chosen** | `SRC-09`, `SRC-08` |
| RSS only | Full history, durations | No Apple `?i=` IDs, so notes wouldn't match single-import dedup | rejected as primary; used for audio, duration, transcript tags | `SRC-12` |

**Queue mechanism** (design reasoning from the codebase; no external sources needed)

| Option | Pros | Cons | Verdict | Sources |
|---|---|---|---|---|
| In-process asyncio worker started in FastAPI `lifespan` + stdlib `sqlite3` job table | No new service; fits the existing lifespan watcher pattern; survives restarts; exactly one worker = sequential by construction | Must reset `running` → `pending` on startup | **chosen** | `SRC-12` |
| Huey (SqliteHuey) | Ready-made retries and scheduling | Separate consumer process, a second LaunchAgent | rejected | — |
| Celery / RQ / arq | Mature | Needs Redis: overkill for one user on one Mac | rejected | — |
| Plugin-side queue (Obsidian drives it) | No backend state | Stops when Obsidian closes; this is exactly what the user wants to avoid | rejected | — |

## Implications

- `RES-01`, `RES-02`, `RES-04`, `RES-05` — New `youtube.list_channel_videos(url, limit, min_seconds)`: normalise to `/videos` (playlists as-is), flat-extract with `playlist_items`, drop `/shorts/` and `duration is None or < min`, probe 21 items for the ≤ 20 "all" decision. Because of `RES-03`, over-fetch (e.g. `limit × 3`, capped) so X items still remain after filtering.
- `RES-06`, `RES-07`, `RES-08`, `RES-10`, D4 — Default pacing: one item at a time, **random 15–45 s** between YouTube items (well above yt-dlp's 10–20 s preset, since each item costs 2–3 requests), `sleep_interval_requests=0.75` inside yt-dlp calls, and a **hard cap of 60 videos/hour** (configurable). On a block signal, pause the YouTube lane (e.g. 30 min → 2 h → 6 h backoff) and surface the reason. Guest session first; cookies only after a bot check.
- `RES-22`, `RES-23`, `RES-24`, D6 — Before the batch work, upgrade the models in one change. Update the constants in `claude.py`, `models.py`, `main.py` (`MODEL_PRICING` and the Haiku chunking check) and `import-modal.ts` (option values and labels) together. Make `stream_claude()` handle only text deltas and set `effort` per call, never on Haiku. Upgrade `anthropic` and handle `refusal`. Update `CLAUDE.md` (the AI line) and `README.md`. Existing notes are unaffected, because the model ID is not written to the frontmatter.
- `RES-18`, `RES-19`, D5 — The pricing fix is covered by D6 (`RES-22`). The batch flow is `POST /batch` (list + filter + dedup, then **estimate**; state `pending_confirmation`) → the plugin shows items, durations and the estimated cost range → `POST /batch/{id}/confirm` enqueues. An unconfirmed batch expires rather than lingering.
- `RES-20`, D1 — New backend module (e.g. `vault.py`: atomic note write, collision suffix, resource stubs, frontmatter ID scan) with no imports from the other modules. `main.py` passes it the paths the plugin supplied. Because the backend is the writer, it can **re-check duplicates on disk right before writing** by scanning only the YAML head of files below the media root. That closes the gap between enqueueing and writing (e.g. the same video imported manually in the meantime).
- `RES-21`, D2, D3 — Listing drops `/shorts/`, `duration is None` and `duration < 900 s`. Processing skips any item whose full metadata reports a live status.
- `RES-09` — Upgrade `youtube-transcript-api` to 1.2.x in the same change so block errors can be told apart from "no transcript" errors. Today both are swallowed by `except Exception: pass`.
- `RES-12`, `RES-13`, `RES-14` — New `podcast.list_show_episodes(show_url, limit, min_seconds)`: iTunes episode lookup → fetch RSS once → join on `episodeGuid`. Each queue item keeps the canonical `trackViewUrl` as its URL, so batch notes look exactly like single imports. Clamp X to 200.
- `RES-15` — Resolved by D1: the backend writes into the vault. The plugin polls `GET /batch` only to **show progress**, not to receive content.
- `RES-16`, `RES-17` — Add `youtube_id` / `apple_episode_id` / `episode_guid` to the frontmatter in `note.py`. At batch creation, the plugin builds a Set of known IDs from `metadataCache` below `mediaTranscriptsFolder` (or all per-source folders when `useParentFolder` is off), parsing legacy `url:` values as a fallback, and sends it along. The backend then re-checks on disk before each write (`RES-20`).
- Module boundaries hold: listing functions live in `youtube.py` / `podcast.py`, the queue (worker + SQLite) goes in a new `batch_queue.py` (avoid shadowing stdlib `queue`), vault writes go in `vault.py`, and `main.py` orchestrates. The plugin gets a batch modal (preview + cost confirmation) and a progress view that polls `GET /batch`.

## Open questions

Resolved on 2026-09-28: note writer (D1), livestreams (D2), minimum duration (D3), pacing (D4), cost guardrail (D5).

- Podcasts with more than 200 episodes, where the user wants "all": fall back to RSS-only for older episodes (no Apple `?i=` ID), or cap at 200? Proposed default: cap at 200.
- Which features (extended summary, resources, focus) and which model apply to a batch? Proposed default: the plugin's current settings, snapshotted into the batch at creation so later settings changes don't alter a running batch.
- Effort level per feature on Sonnet 5 / Opus 5.5 (`RES-23`): proposed default is `low` for base transcript cleaning and `medium` for extended/focus summaries.
- Raise the Haiku `max_tokens` from 8192 toward its 64K limit, and raise `_CHUNK_THRESHOLD_CHARS` with it? Fewer chunks means fewer repeated system prompts, but also longer single calls.
- The `RES-19` constants (tokens/min, output ratio) are heuristic. Calibrate them after the first real batch using the recorded actual costs.
