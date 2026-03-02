# Transcribe YouTube → Obsidian

Import any YouTube video as a clean, structured Obsidian note — complete with metadata, Claude-generated summary, and a cleaned transcript organized into chapters.

```
YouTube URL  →  [OrbStack Container]  →  Claude API  →  Obsidian Note
```

---

## What You Get

For each video, a new `.md` note is created in your vault like this:

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

A 3-5 sentence summary of the key takeaways...

---

## Extended Summary  ← (optional, when enabled)

### Topic One: The Core Argument

Several paragraphs of synthesized editorial prose covering this topic...

### Topic Two: Practical Applications

More prose...

---

## Transcript

## Introduction

Cleaned transcript text without filler words, broken into chapters...

## Chapter Two: The Core Concept

More text...
```

---

## Features

### Model Selection
Choose between **Sonnet** (faster, cheaper) and **Opus** (higher quality) directly in the import dialog. Defaults to Sonnet.

### Extended Summary
Check **"Create extended summary"** in the import dialog to add a comprehensive, topic-by-topic editorial rewrite between the summary and transcript. This reads like a standalone document written for a professional audience — useful for long conversations where you want a condensed but thorough version without watching the whole video.

### Cookie Support
Handle age-restricted or region-locked videos by providing YouTube cookies via browser extraction or a `cookies.txt` file upload.

---

## Prerequisites

- [OrbStack](https://orbstack.dev) installed on your Mac
- [Node.js](https://nodejs.org) (for building the Obsidian plugin)
- An [Anthropic API key](https://console.anthropic.com)

---

## Setup

### 1. Clone / copy this project

```bash
# Place the project somewhere sensible
cd ~/Developer
# (copy or clone the project here)
cd youtube-to-obsidian
```

### 2. Configure your API key

```bash
cp .env.example .env
# Edit .env and add your Anthropic API key
```

### 3. Start the backend with OrbStack

```bash
docker compose up -d
```

OrbStack will build the container and start the FastAPI server at `http://localhost:8000`.

To verify it's running:
```bash
curl http://localhost:8000/health
# → {"status":"ok"}
```

### 4. Auto-start on login (optional but recommended)

In OrbStack's menu bar app → Container settings → enable "Start on login" for `yt-obsidian-api`.

Or use a launchd plist — OrbStack handles this automatically if you enable it in the UI.

### 5. Build the Obsidian plugin

```bash
cd obsidian-plugin
npm install
npm run build
```

This produces a `main.js` file.

### 6. Install the plugin into Obsidian

1. Open your vault in Finder
2. Navigate to `.obsidian/plugins/`
3. Create a folder: `youtube-to-obsidian`
4. Copy these three files into it:
   - `obsidian-plugin/main.js`
   - `obsidian-plugin/manifest.json`
5. In Obsidian: **Settings → Community Plugins → Reload plugins**
6. Enable **YouTube to Obsidian**

---

## Usage

### Option A — Ribbon icon
Click the YouTube icon in the left sidebar ribbon.

### Option B — Command palette
`Cmd+P` → **Import YouTube Video as Note**

### Then:
1. Paste your YouTube URL
2. Select a model (Sonnet or Opus)
3. Optionally check **"Create extended summary"**
4. Hit **Import** (or press Enter)
5. Wait ~15–30 seconds (depends on video length and model choice)
6. The new note opens automatically in your vault

With very long videos above 90 minutes the reliability starts to drop.

---

## Plugin Settings

Go to **Settings → YouTube to Obsidian**:

| Setting | Default | Description |
|---|---|---|
| Backend API URL | `http://localhost:8000` | Where your Python server runs |
| Browser for Cookies | None | Extract YouTube cookies from a browser (Chrome, Firefox, Safari, Edge, Brave) |
| YouTube Cookie File | — | Upload a Netscape `cookies.txt` as fallback |
| Output Folder | `YouTube` | Vault folder for new notes (created automatically) |

---

## Updating the Container

After editing any backend files:

```bash
docker compose up -d --build
```

---

## Adding Custom Prompts

The backend uses a composable prompt system in `backend/prompts/`. Each prompt feature is a standalone Python module with three exports:

| Export | Type | Purpose |
|---|---|---|
| `ROLE_PREAMBLE` | `str` | Added to the "You are ..." role line |
| `JSON_KEYS` | `list[dict]` | Keys to request in Claude's JSON response |
| `RULES` | `str` | Instructions for this feature's output |

To add a new prompt feature:

1. Create a new file in `backend/prompts/` (e.g., `action_items.py`)
2. Define `ROLE_PREAMBLE`, `JSON_KEYS`, and `RULES`
3. Register it in the `PROMPTS` dict in `backend/prompts/__init__.py`
4. Add a corresponding `bool` flag to `TranscriptRequest` in `backend/models.py`
5. Wire the flag in `main.py`'s `event_generator` (add to `features` list)
6. Handle the new JSON key in `note.py`'s `build_obsidian_note()`

Example prompt module:

```python
# backend/prompts/action_items.py

ROLE_PREAMBLE = "task extraction specialist"

JSON_KEYS = [
    {
        "key": "action_items",
        "description": "A bulleted list of actionable takeaways from the video.",
    },
]

RULES = """Rules for "action_items":
- Extract concrete, actionable items mentioned in the video.
- Each item should be a single clear sentence.
- Order by importance, not by appearance in the video.
- Limit to 10 items maximum."""
```

The `get_system_prompt(features)` function in `prompts/__init__.py` automatically composes the selected features into a single coherent system prompt with merged role descriptions, JSON keys, and rules.

---

## Troubleshooting

**"No transcript available"**
The video may have disabled captions, or only has auto-generated captions in a non-English language. Try a different video to confirm the setup works.

**Container not reachable**
```bash
docker compose ps           # check it's running
docker compose logs -f      # watch logs
```

**Plugin not showing up**
Make sure you copied both `main.js` AND `manifest.json` into the plugin folder, then reloaded plugins in Obsidian settings.

**Claude returns garbled output**
Check your `ANTHROPIC_API_KEY` in `.env` is correct and has sufficient credits.

---

## Project Structure

```
youtube-to-obsidian/
├── backend/
│   ├── main.py              # FastAPI routes (thin layer)
│   ├── models.py            # Pydantic request/response models
│   ├── youtube.py           # Video metadata + transcript fetching
│   ├── note.py              # Obsidian note builder
│   ├── claude.py            # Anthropic client + streaming
│   ├── cookies.py           # Cookie file management
│   ├── prompts/
│   │   ├── __init__.py      # Prompt registry + composer
│   │   ├── base.py          # Transcript + short summary
│   │   └── extended.py      # Extended editorial summary
│   ├── requirements.txt
│   └── Dockerfile
├── obsidian-plugin/
│   ├── src/
│   │   └── main.ts          # Plugin source
│   ├── manifest.json
│   ├── package.json
│   ├── tsconfig.json
│   └── esbuild.config.mjs
├── docker-compose.yml
├── .env.example
└── README.md
```
