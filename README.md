# Transcribe YouTube → Obsidian

Import any YouTube video as a clean, structured Obsidian note — complete with metadata, Claude-generated summary, and a cleaned transcript organized into chapters.

```
YouTube URL  →  [native Python backend on macOS]  →  Claude API  →  Obsidian Note
```

The backend runs as a native macOS launchd agent (no Docker, no OrbStack) and auto-starts on login. Python is managed by [`uv`](https://docs.astral.sh/uv/) so there's no global pip/pyenv mess.

---

## What You Get

For each video, a new `.md` note is created in your vault:

```markdown
---
title: "How to Build AI Workflows"
channel: "Some Channel"
channel_url: "https://youtube.com/@..."
url: "https://youtube.com/watch?v=xxx"
published: "2024-03-15"
duration: "24:31"
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

## Extended Summary  ← (optional, when enabled)

### Topic One: The Core Argument
Several paragraphs of synthesized editorial prose…

### Topic Two: Practical Applications
More prose…

---

## Transcript

## Introduction
Cleaned transcript text without filler words, broken into chapters…
```

---

## Features

- **Model selection** — Haiku (fastest), Sonnet (balanced), or Opus (highest quality), picked per-import.
- **Extended summary** — a topic-by-topic editorial rewrite that reads like a standalone piece.
- **Cookie support** — Safari (default) / Chrome / Firefox / Edge / Brave via automatic browser extraction, with a `cookies.txt` upload fallback. Handles age-restricted and region-locked videos.
- **Duplicate detection** — warns you if a note for the same video already exists in your vault.
- **Auto-start** — backend runs as a launchd agent from the moment you log in.

---

## Requirements

- macOS (Apple Silicon or Intel)
- [Homebrew](https://brew.sh)
- [Node.js](https://nodejs.org) (only needed for building the Obsidian plugin)
- An [Anthropic API key](https://console.anthropic.com)

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

In Obsidian: **Settings → Community Plugins → Reload plugins**, then enable **YouTube to Obsidian**.

---

## Using it

### Trigger the import
- Click the YouTube icon in the left ribbon, **or**
- `Cmd+P` → **Import YouTube Video as Note**

### In the dialog
1. Paste a YouTube URL
2. Pick Transcript or Extended Summary mode
3. Pick a model
4. **Import** (or press Enter)

The new note opens automatically. Videos over ~90 minutes start getting unreliable (YouTube rate-limits, transcripts truncate); Sonnet or Opus handles long videos better than Haiku.

### Plugin settings

| Setting | Default | Description |
|---|---|---|
| Backend API URL | `http://localhost:8000` | Where the Python server listens |
| Browser for Cookies | **Safari** | Auto-extract YouTube cookies; falls back to cookies.txt if unreadable |
| YouTube Cookie File | — | Netscape `cookies.txt` upload for when the browser path isn't available |
| Output Folder | `YouTube` | Vault folder for new notes (auto-created) |

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
│   ├── main.py              # FastAPI routes + SSE pipeline
│   ├── models.py            # Pydantic request/response models
│   ├── youtube.py           # yt-dlp + transcript fetching + cookie logic
│   ├── note.py              # Obsidian markdown assembly
│   ├── claude.py            # Anthropic streaming client
│   ├── cookies.py           # cookies.txt persistence
│   ├── prompts/             # composable prompt modules
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
│   │   └── check.sh
│   ├── Dockerfile           # legacy; not used by the native flow
│   └── requirements.txt     # legacy; replaced by pyproject.toml
├── obsidian-plugin/
│   ├── src/
│   │   ├── main.ts
│   │   ├── settings.ts
│   │   ├── import-modal.ts
│   │   ├── sse-handler.ts
│   │   └── youtube-utils.ts
│   ├── manifest.json
│   ├── package.json
│   ├── esbuild.config.mjs
│   ├── tsconfig.json
│   └── .env.example
├── docker-compose.yml       # legacy
└── README.md
```
