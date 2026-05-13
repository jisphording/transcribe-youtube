# Media to Obsidian (YouTube + Apple Podcasts)

Import any YouTube video **or Apple Podcasts episode** as a clean, structured Obsidian note — complete with metadata, Claude-generated summary, optional resource extraction / focus topic / extended summary, and a cleaned transcript organized into chapters.

```
YouTube URL    ─┐
                ├─→  [native Python backend on macOS]  →  Claude API  →  Obsidian Note
Apple Podcast ─┘            │
                            └─→  podcast:transcript  →  free
                                 (RSS Podcasting 2.0)
                            └─→  whisper.cpp local server  →  M-series GPU
                                 (when no RSS transcript)
```

The plugin auto-detects whether the URL is a YouTube video or an Apple Podcasts episode. For podcasts, it first checks the RSS feed for a free transcript (Podcasting 2.0 `<podcast:transcript>` tag); if none is available, it downloads the audio and sends it to a **local whisper.cpp HTTP server** on your Mac — no audio leaves your machine.

The backend runs as a native macOS launchd agent (no Docker, no OrbStack) and auto-starts on login. Python is managed by [`uv`](https://docs.astral.sh/uv/) so there's no global pip/pyenv mess.

---

## What You Get

For each video or episode, a new `.md` note is created in your vault.

**YouTube note:**

```markdown
---
title: "How to Build AI Workflows"
channel: "Some Channel"
channel_url: "https://youtube.com/@..."
url: "https://youtube.com/watch?v=xxx"
published: "2024-03-15"
duration: "24:31"
topics:
  - ai-workflows
  - automation
tags:
  - youtube
  - transcript
---

![thumbnail](https://i.ytimg.com/...)

# How to Build AI Workflows

> **Channel:** [Some Channel](...)
> **Published:** 2024-03-15
> **Duration:** 24:31
> **Link:** [Watch on YouTube](...)

---

## Summary
A 3–5 sentence summary of the key takeaways…

---

## Mentioned Resources    ← (optional, when "Extract resources" is on)
- [[Obsidian]] *(software)*
- [[Anthropic]] *(service)*

---

## Extended Summary       ← (optional, when enabled)
### Topic One
Several paragraphs of synthesized editorial prose…

---

## Transcript
### Introduction
Cleaned transcript text without filler words, broken into chapters…
```

**Podcast note** (same structure, source-aware fields):

```markdown
---
title: "Episode Title"
show: "Podcast Title"
show_url: "https://example.com/feed/"
url: "https://podcasts.apple.com/.../id1234567890?i=..."
published: "2026-01-15"
duration: "42:30"
topics:
  - topic-one
  - topic-two
tags:
  - podcast
  - transcript
---

# Episode Title

> **Show:** [Podcast Title](https://example.com/)
> **Published:** 2026-01-15
> **Duration:** 42:30
> **Link:** [Listen on Apple Podcasts](https://podcasts.apple.com/...)

---

## Summary
…

---

## Transcript *(via whisper.cpp local)*
…
```

---

## Features

- **One modal, two sources** — paste a YouTube URL or an Apple Podcasts URL; the plugin auto-detects which it is and adapts the UI.
- **Model selection** — Haiku (fastest), Sonnet (balanced), or Opus (highest quality), picked per-import.
- **Extended summary** — a topic-by-topic editorial rewrite that reads like a standalone piece.
- **Focus topic** — deep-dive summary on a specific user-supplied topic.
- **Resource extraction** — pulls every product / tool / website / service mentioned and creates `[[wiki-link]]` stubs in your vault.
- **RSS-first podcast transcripts** — uses the free `<podcast:transcript>` tag when shows publish one (many shows already do).
- **Local whisper.cpp fallback** — when no RSS transcript is available, transcribes audio offline on your Mac via Metal-accelerated whisper.cpp. Audio never leaves the machine.
- **Cookie support** — Safari (default) / Chrome / Firefox / Edge / Brave via automatic browser extraction, with a `cookies.txt` upload fallback. Handles age-restricted and region-locked YouTube videos.
- **Duplicate detection** — warns you if a note for the same video/episode already exists in your vault.
- **Auto-start** — backend (and optionally whisper-server) run as launchd agents from the moment you log in.

---

## Requirements

- macOS (Apple Silicon recommended for whisper performance; Intel works for YouTube only)
- [Homebrew](https://brew.sh)
- [Node.js](https://nodejs.org) (only needed for building the Obsidian plugin)
- An [Anthropic API key](https://console.anthropic.com)
- **For podcasts:** whisper.cpp (installed by `backend/scripts/whisper-setup.sh`) — only required for shows without an RSS transcript

You do **not** need a pre-installed Python — `uv` installs the right version (3.12) into its own cache.

---

## One-shot install (macOS)

### 1. Install the CLI prerequisites

```bash
brew install uv deno
```

**Raycast users** — save this as a "Run Shell Command" snippet for one-click setup:

```bash
brew install uv deno && open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
```

- `uv` — Python version + package manager (replaces `pip`/`pyenv`/`venv`).
- `deno` — JavaScript runtime that `yt-dlp` uses to compute YouTube's "PO tokens". Without it, yt-dlp gets bot-checked on most videos.

### 2. Clone and configure

```bash
cd ~/Developer                                # or wherever you keep projects
git clone <this-repo> transcribe-youtube
cd transcribe-youtube

cp backend/.env.example backend/.env
# open backend/.env and paste your ANTHROPIC_API_KEY
```

### 3. Install the backend as a launchd agent

```bash
cd backend
./scripts/install.sh
```

This script is idempotent and does:

1. Verifies `uv`, `deno`, and `brew` are available.
2. Runs `uv sync` — creates `backend/.venv/` with Python 3.12 and all pinned deps. Generates `uv.lock`.
3. Renders `com.johannes.yt-obsidian.plist.template` with your absolute paths and installs it to `~/Library/LaunchAgents/`.
4. `launchctl load`s the agent. Uvicorn starts on port 8000 immediately and on every subsequent login.
5. Prints the resolved Python path you'll need for Safari (see next step) and offers to open System Settings.

Verify:

```bash
curl http://localhost:8000/health
# → {"status":"ok"}
```

### 4. Grant Safari cookies access (recommended, one-time)

Safari stores its cookies at `~/Library/Cookies/Cookies.binarycookies`, which macOS protects under TCC. For `yt-dlp` to read them, the Python binary launchd runs needs **Full Disk Access**.

1. `./scripts/install.sh` printed the binary path — it looks like
   `~/.local/share/uv/python/cpython-3.12.x-macos-*/bin/python3.12` (the actual target of the `.venv` symlink).
2. **System Settings → Privacy & Security → Full Disk Access**
3. Click **+**, press **⌘⇧G**, paste the path, **Open**, then toggle it **on**.
4. Restart the backend so the new permission applies:
   ```bash
   ./scripts/stop.sh && ./scripts/start.sh
   ```
5. Verify:
   ```bash
   ./scripts/check.sh --browser safari
   ```

Don't want to grant FDA? Skip this step. The plugin will fall back to the `cookies.txt` you upload via the plugin settings. You can also switch to Firefox in the plugin settings — Firefox's cookie DB isn't TCC-protected.

### 5. Build and deploy the Obsidian plugin

```bash
cd ../obsidian-plugin
cp .env.example .env
# edit .env: OBSIDIAN_PLUGINS_PATH=<your vault>/.obsidian/plugins/youtube-to-obsidian
# (add _2, _3, … for more vaults)

npm install
npm run build
```

In Obsidian: **Settings → Community Plugins → Reload plugins**, then enable **Media to Obsidian**.

### 6. (Optional, for podcasts) Install whisper.cpp

For podcasts that publish a free `<podcast:transcript>` in their RSS feed (Huberman, Lex Fridman, This American Life, etc.), nothing more is needed. For everything else, you'll want a local whisper.cpp server so the plugin can transcribe audio offline.

```bash
cd backend
./scripts/whisper-setup.sh
```

The script is idempotent. It:

1. Detects an existing source build at `~/whisper.cpp` first; otherwise installs `whisper-cpp` + `ffmpeg` via Homebrew.
2. Downloads the recommended model `ggml-large-v3-turbo.bin` (~800 MB) into `~/models/`.
3. Writes `~/Library/LaunchAgents/com.whisper.server.plist` (lazy-start: `RunAtLoad=false`, `KeepAlive=false`) and registers it.
4. Smoke-tests by kickstarting the agent once and stopping it. After install, the LaunchAgent is registered but inert — it will be started on demand.

#### Lifecycle (lazy by default, idle auto-stop)

By default, `whisper-server` is **off** until needed. The flow is:

- The Obsidian plugin queries `GET /whisper/status` to show live state in the settings tab.
- When you import a podcast that needs whisper (no RSS transcript), the backend lazy-starts the server (~1–3 s model load) and transcribes.
- After **30 minutes of inactivity**, the backend's idle watcher stops the server. Memory goes back to zero.
- A "Keep whisper-server warm" toggle in plugin settings flips this to eager mode: the server starts when Obsidian opens and stops when Obsidian closes (the 30-min idle stop still applies as a safety net).
- The settings tab also has explicit Start / Stop buttons for manual control.

Verify:
```bash
curl http://127.0.0.1:2022/                     # whisper-server (only when running)
curl http://localhost:8000/whisper/status       # backend's view (always works)
curl -X POST http://localhost:8000/whisper/start  # manual start
curl -X POST http://localhost:8000/whisper/stop   # manual stop
```

Low-level manual control bypassing the backend:

```bash
launchctl kickstart gui/$(id -u)/com.whisper.server      # start
launchctl kill SIGTERM gui/$(id -u)/com.whisper.server   # stop
tail -f /tmp/whisper-server.log /tmp/whisper-server.err  # logs
```

**Performance note:** an M-series Mac with Metal acceleration transcribes a 60-minute podcast in roughly 3–5 minutes. The Obsidian modal shows real-time download + transcription progress.

---

## Using it

### Trigger the import
- Click the audio-file icon in the left ribbon, **or**
- `Cmd+P` → **Import Media (YouTube or Podcast) as Note**

### In the dialog
1. Paste a YouTube or Apple Podcasts URL — a small badge shows the detected source
2. Pick Transcript / Extended Summary / Focus Topic mode
3. (Podcast only) Whisper language: `auto` works in most cases; set `en`, `de`, etc. if auto-detect picks the wrong one on short clips
4. Pick a Claude model
5. **Import** (or press Enter)

The new note opens automatically. Videos over ~90 minutes start getting unreliable (YouTube rate-limits, transcripts truncate); Sonnet or Opus handles long videos better than Haiku. Podcasts of any length work, but whisper transcription scales linearly with duration (~3–5 min per hour on M-series).

### Plugin settings

| Setting | Default | Description |
|---|---|---|
| Backend API URL | `http://localhost:8000` | Where the Python server listens |
| YouTube notes folder | `YouTube` | Vault folder for new YouTube notes (auto-created) |
| Podcast notes folder | `Podcasts` | Vault folder for new podcast notes (auto-created) |
| Whisper language | `auto` | Default language hint for whisper.cpp; can be overridden per import |
| Cookie file | — | Netscape `cookies.txt` upload for when the browser path isn't available (YouTube only) |

The settings tab also shows a live indicator for whether the local whisper-server is reachable.

---

## Daily operations

From `backend/`:

| Command | What it does |
|---|---|
| `./scripts/start.sh` | `launchctl load` the agent; waits for `/health` |
| `./scripts/stop.sh` | `launchctl unload` the agent |
| `./scripts/logs.sh` | tail `~/Library/Logs/yt-obsidian/{stdout,stderr}.log` |
| `./scripts/check.sh` | run the full diagnostic — agent state, Python binary, deno, cookie readability, yt-dlp end-to-end |
| `./scripts/check.sh --browser safari` | as above, forcing yt-dlp to use Safari cookies |
| `./scripts/update.sh` | `git pull` + `uv sync` from the lockfile + restart the backend |
| `./scripts/whisper-setup.sh` | one-time install of whisper.cpp + model + LaunchAgent (idempotent) |

Changed `backend/.env` (e.g. new API key)? Restart the backend:
```bash
./scripts/stop.sh && ./scripts/start.sh
```

Changed plugin TypeScript? Rebuild:
```bash
cd obsidian-plugin && npm run build
```
Then in Obsidian: **Settings → Community Plugins → Reload plugins**.

---

## Uninstall

```bash
cd backend
./scripts/stop.sh
rm "$HOME/Library/LaunchAgents/com.johannes.yt-obsidian.plist"
rm -rf .venv
rm -rf "$HOME/Library/Logs/yt-obsidian"

# Optional: remove whisper-server LaunchAgent and model
launchctl unload "$HOME/Library/LaunchAgents/com.whisper.server.plist" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/com.whisper.server.plist"
rm -rf "$HOME/models"   # ⚠ also removes any other whisper models you may have downloaded
```

---

## Why `uv`?

Python environments age badly — `pip install` into the system Python is how `/usr/bin/python3` ends up with conflicting versions of `httpx` from six projects. `uv` fixes this:

- **Per-project isolation.** `backend/.venv` is self-contained. Delete it, run `uv sync`, you're back where you were.
- **Python version pinned.** `.python-version` says `3.12`. `uv` downloads that exact version into its cache if you don't have it. No Homebrew Python / Xcode Python / pyenv confusion.
- **Lockfile is the source of truth.** `uv.lock` records the exact resolved tree. When you move to a new machine, `uv sync` reproduces it bit-for-bit — which is exactly what prevented this project from working after moving vaults (a floating `yt-dlp>=2025.2.19` upgraded silently and started requiring a JS runtime).
- **One tool, not three.** No separate pyenv + pipx + poetry stack.

If `uv` ever goes away, falling back is ten minutes of work: `python3.12 -m venv .venv && source .venv/bin/activate && pip install -r <(uv export)`.

---

## Troubleshooting

**`./scripts/check.sh` says Safari is `BLOCKED by TCC`.**
Full Disk Access isn't granted to the Python binary launchd is using. Re-read step 4 above. The path printed by `scripts/check.sh` is the one that needs the permission — granting FDA to Terminal or iTerm does **not** help here because launchd runs the agent directly.

**I granted FDA, still blocked.**
Probably granted to a symlink or the wrong binary. `scripts/check.sh` prints the resolved path (`resolved to: /Users/…/cpython-3.12.x-macos-*/bin/python3.12`). Remove everything under FDA labeled `python*` and re-add that exact path. Restart the backend.

**Existing `data.json` still says `cookieBrowser: ""`.**
The Safari default only applies to fresh plugin installs. Open **Settings → YouTube to Obsidian** and change the dropdown to Safari, or edit `data.json` directly.

**`yt-dlp` complains about "No supported JavaScript runtime".**
`deno` isn't in the agent's PATH. The plist sets `PATH=/opt/homebrew/bin:/usr/local/bin:…` which picks up brew on Apple Silicon and Intel. If brew is somewhere else, edit `com.johannes.yt-obsidian.plist.template`'s `EnvironmentVariables` block and reinstall.

**Video fails with 429 / "Sign in to confirm you're not a bot".**
1. Run `./scripts/check.sh` to confirm cookies are being read.
2. If cookies are fine but you still fail, your cookie file is probably only signed-out 3P cookies. Make sure Safari is currently signed into youtube.com; the browser extraction will then include the full 1P session.
3. Try a different video — some private / age-gated / members-only videos require proper sign-in even with cookies.

**Backend unreachable after update.**
```bash
./scripts/logs.sh -n 50
```
Common cause: `uv sync` broke because a pin is unavailable. Roll back `pyproject.toml`/`uv.lock` with `git checkout` and rerun `./scripts/update.sh`.

**Podcast import fails: "Local whisper-server failed to start within 20s".**
The backend tried to lazy-start `whisper-server` via `launchctl kickstart` but it never came up. First check whether the LaunchAgent exists at all (`ls ~/Library/LaunchAgents/com.whisper.server.plist`). If missing, run `backend/scripts/whisper-setup.sh`. If present, check `/tmp/whisper-server.err` — most failures are model-path problems or a port collision. As a quick test: `launchctl kickstart gui/$(id -u)/com.whisper.server && sleep 3 && curl http://127.0.0.1:2022/`.

**Whisper transcription fails: `whisper-server error 404: File Not Found (/v1/audio/transcriptions)`.**
You're on stale backend code. whisper.cpp's `whisper-server` exposes `/inference`, not the OpenAI-compatible `/v1/audio/transcriptions` path. `backend/whisper.py` now posts to `/inference` — `git pull` and `./scripts/stop.sh && ./scripts/start.sh` to pick it up.

**Whisper transcription fails: `whisper-server error 500: {"error":"FFmpeg conversion failed."}`.**
whisper-server with `--convert` writes a temp WAV to its current working directory and shells out to ffmpeg with a relative path. If the LaunchAgent has no `WorkingDirectory` (or one that isn't writable), ffmpeg can't find the file. Re-run `./scripts/whisper-setup.sh` — the current script sets `WorkingDirectory=/tmp` in the plist. To check an existing install: `/usr/libexec/PlistBuddy -c "Print :WorkingDirectory" ~/Library/LaunchAgents/com.whisper.server.plist`. Confirm with `tail /tmp/whisper-server.log` — you'll see `Error opening input file ./whisper-server-…wav` when the path is broken.

**Podcast import: "Could not match the Apple Podcasts URL to a specific RSS episode".**
The plugin matches by following the Apple share-URL redirect to extract the canonical episode title slug, then fuzzy-matches that against RSS item titles. If the RSS feed has unusually different titles (e.g. show numbers only) the match can fail. Open an issue with the URL — pasting a URL with the canonical slug in the path usually fixes it.

**Podcast import: "No RSS feed found".**
The show is likely Spotify-exclusive or paywalled — Apple's iTunes lookup API returns no `feedUrl` in that case.

**I still have the old Docker setup.**
`Dockerfile` and `docker-compose.yml` remain in the repo as a fallback. They are no longer the recommended path — the native agent is simpler and avoids the deno-in-container headache. To delete: `rm backend/Dockerfile docker-compose.yml`.

---

## Adding custom prompts

The backend uses a composable prompt system in `backend/prompts/`. Each feature module exports:

| Export | Type | Purpose |
|---|---|---|
| `ROLE_PREAMBLE` | `str` | Added to the "You are …" role line |
| `JSON_KEYS` | `list[dict]` | Keys to request in Claude's JSON response |
| `RULES` | `str` | Instructions for this feature's output |

To add a feature:

1. Create `backend/prompts/your_feature.py` with the three exports.
2. Register it in the `PROMPTS` dict in `backend/prompts/__init__.py`.
3. Add a `bool` flag to `TranscriptRequest` in `backend/models.py`.
4. Wire the flag in `main.py`'s `event_generator` (append to `features` list).
5. Extend `build_obsidian_note()` in `note.py` to render the new JSON key.

Example:

```python
# backend/prompts/action_items.py
ROLE_PREAMBLE = "task extraction specialist"

JSON_KEYS = [
    {"key": "action_items",
     "description": "A bulleted list of actionable takeaways from the video."},
]

RULES = """Rules for "action_items":
- Extract concrete, actionable items mentioned in the video.
- Each item is a single clear sentence.
- Order by importance, not by appearance.
- Limit to 10 items maximum."""
```

---

## Project layout

```
transcribe-youtube/
├── backend/
│   ├── main.py              # FastAPI routes + SSE pipeline (URL source branching)
│   ├── models.py            # Pydantic request/response models
│   ├── youtube.py           # yt-dlp + YouTube transcript fetching + cookie logic
│   ├── podcast.py           # Apple URL → iTunes lookup → RSS → episode + RSS transcript
│   ├── whisper.py           # Local whisper-server HTTP client (audio download + POST)
│   ├── note.py              # Obsidian markdown assembly (YouTube + podcast variants)
│   ├── claude.py            # Anthropic streaming client
│   ├── cookies.py           # cookies.txt persistence
│   ├── prompts/             # composable prompt modules (source-aware)
│   ├── pyproject.toml       # deps (uv reads this)
│   ├── uv.lock              # committed lockfile
│   ├── .python-version      # pins Python 3.12
│   ├── .env.example
│   ├── com.johannes.yt-obsidian.plist.template
│   ├── scripts/
│   │   ├── install.sh
│   │   ├── start.sh
│   │   ├── stop.sh
│   │   ├── logs.sh
│   │   ├── update.sh
│   │   ├── check.sh
│   │   └── whisper-setup.sh # one-time whisper.cpp + LaunchAgent install
│   └── requirements.txt     # mirror of pyproject.toml deps
├── obsidian-plugin/
│   ├── src/
│   │   ├── main.ts          # plugin lifecycle + source-aware note creation
│   │   ├── settings.ts      # YouTube + Podcasts folders + whisper language
│   │   ├── import-modal.ts  # one modal, auto-detects YouTube vs Apple Podcasts
│   │   ├── sse-handler.ts   # SSE event → callbacks
│   │   └── url-utils.ts     # detectSource(), extractVideoId(), Apple ID extractors
│   ├── manifest.json
│   ├── package.json
│   ├── esbuild.config.mjs
│   ├── tsconfig.json
│   └── .env.example
└── README.md
```
