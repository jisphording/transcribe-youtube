# YouTube → Obsidian

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

## Transcript

## Introduction

Cleaned transcript text without filler words, broken into chapters...

## Chapter Two: The Core Concept

More text...
```

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
cd yt-to-obsidian
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
3. Create a folder: `yt-to-obsidian`
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
2. Hit **Import** (or press Enter)
3. Wait ~15–30 seconds (depends on video length)
4. The new note opens automatically in your vault ✅

---

## Plugin Settings

Go to **Settings → YouTube to Obsidian**:

| Setting | Default | Description |
|---|---|---|
| Backend API URL | `http://localhost:8000` | Where your Python server runs |
| Output Folder | `YouTube` | Vault folder for new notes (created automatically) |

---

## Updating the Container

If you edit `backend/main.py`:

```bash
docker compose up -d --build
```

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
yt-to-obsidian/
├── backend/
│   ├── main.py              # FastAPI app
│   └── requirements.txt
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
