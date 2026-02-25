import re
import os
import json
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled
from yt_dlp import YoutubeDL
import anthropic

app = FastAPI(title="YT to Obsidian API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["app://obsidian.md", "http://localhost", "*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


class TranscriptRequest(BaseModel):
    url: str


def extract_video_id(url: str) -> str:
    patterns = [
        r"(?:v=|\/)([0-9A-Za-z_-]{11})",
        r"youtu\.be\/([0-9A-Za-z_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    raise ValueError(f"Could not extract video ID from URL: {url}")


def get_video_metadata(url: str) -> dict:
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": False,
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        duration_seconds = info.get("duration", 0)
        minutes = duration_seconds // 60
        seconds = duration_seconds % 60
        duration_str = f"{minutes}:{seconds:02d}"

        upload_date = info.get("upload_date", "")
        if upload_date and len(upload_date) == 8:
            upload_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"

        return {
            "title": info.get("title", "Unknown Title"),
            "channel": info.get("channel", info.get("uploader", "Unknown Channel")),
            "channel_url": info.get("channel_url", info.get("uploader_url", "")),
            "upload_date": upload_date,
            "duration": duration_str,
            "thumbnail_url": info.get("thumbnail", ""),
            "description": (info.get("description", "") or "")[:500],
            "view_count": info.get("view_count", 0),
            "url": url,
        }


def get_transcript(video_id: str) -> tuple[list, bool]:
    """Returns (transcript_entries, is_multi_speaker). Tries auto-generated as fallback."""
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        # prefer manual, fall back to auto-generated
        try:
            transcript = transcript_list.find_manually_created_transcript(["en", "en-US", "en-GB"])
        except Exception:
            transcript = transcript_list.find_generated_transcript(["en", "en-US", "en-GB"])

        entries = transcript.fetch()
        # Heuristic: if many entries contain speaker labels like "[Music]" or capitalized names
        raw_text = " ".join(e["text"] for e in entries[:50])
        is_multi_speaker = bool(re.search(r"\[[\w\s]+\]", raw_text))
        return entries, is_multi_speaker

    except (NoTranscriptFound, TranscriptsDisabled) as e:
        raise HTTPException(status_code=422, detail=f"No transcript available: {e}")


def transcript_to_plain_text(entries: list) -> str:
    """Merge transcript entries into a single block of text."""
    texts = [e["text"].strip() for e in entries]
    # Remove [Music], [Applause] etc.
    texts = [re.sub(r"\[[\w\s]+\]", "", t).strip() for t in texts]
    texts = [t for t in texts if t]
    return " ".join(texts)


def slugify(title: str) -> str:
    """Convert title to all-lowercase-hyphen filename."""
    slug = title.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    slug = slug.strip("-")
    return slug


def build_obsidian_note(metadata: dict, summary: str, transcript_md: str) -> tuple[str, str]:
    """Returns (filename, markdown_content)."""
    filename = slugify(metadata["title"]) + ".md"

    thumbnail_line = ""
    if metadata["thumbnail_url"]:
        thumbnail_line = f'![thumbnail]({metadata["thumbnail_url"]})\n\n'

    frontmatter = f"""---
title: "{metadata['title']}"
channel: "{metadata['channel']}"
channel_url: "{metadata['channel_url']}"
url: "{metadata['url']}"
published: "{metadata['upload_date']}"
duration: "{metadata['duration']}"
tags:
  - youtube
  - transcript
---

"""

    note = (
        frontmatter
        + thumbnail_line
        + f"# {metadata['title']}\n\n"
        + f"> **Channel:** [{metadata['channel']}]({metadata['channel_url']})  \n"
        + f"> **Published:** {metadata['upload_date']}  \n"
        + f"> **Duration:** {metadata['duration']}  \n"
        + f"> **Link:** [Watch on YouTube]({metadata['url']})\n\n"
        + "---\n\n"
        + "## Summary\n\n"
        + summary.strip()
        + "\n\n---\n\n"
        + "## Transcript\n\n"
        + transcript_md.strip()
        + "\n"
    )

    return filename, note


SYSTEM_PROMPT = """You are an expert transcript editor and summarizer. 
Your job is to process a raw YouTube transcript and return a clean, well-structured result.

You will receive:
- Video metadata (title, channel, description)
- Raw transcript text

You must return a valid JSON object with exactly two keys:
1. "summary": A concise 3-5 sentence summary of the video's main content and key takeaways.
2. "transcript": The cleaned transcript in Markdown format.

Rules for the transcript:
- Remove ALL filler words: "um", "uh", "like", "you know", "sort of", "kind of", "basically", "literally", "actually", "right", "okay so", "so yeah", "I mean", etc.
- Keep the content as close to the original wording as possible — do not paraphrase or rewrite sentences.
- If there are clearly multiple speakers (e.g. interview format), format as:
  **Speaker A:** Their text here.
  **Speaker B:** Their response here.
  Use generic labels (Host, Guest, Speaker 1, etc.) unless names are clearly mentioned.
- Break the transcript into logical chapters using ## headings (Markdown h2). 
  For short videos (< 10 min): 2-4 chapters.
  For medium videos (10-30 min): 4-8 chapters.
  For long videos (> 30 min): 8-15 chapters.
- Chapter headings should be descriptive and reflect the content of that section.
- Do NOT include timestamps.
- Do NOT add any content that wasn't in the original transcript.
- Preserve paragraph breaks for readability — group related sentences together.

Return ONLY the JSON object, no other text."""


@app.post("/process")
async def process_video(request: TranscriptRequest):
    try:
        video_id = extract_video_id(request.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Fetch metadata and transcript in parallel conceptually (sequential here for simplicity)
    try:
        metadata = get_video_metadata(request.url)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Could not fetch video metadata: {e}")

    entries, is_multi_speaker = get_transcript(video_id)
    raw_text = transcript_to_plain_text(entries)

    # Estimate video length from metadata for prompt context
    duration_note = f"Video duration: {metadata['duration']}"

    user_message = f"""Video Title: {metadata['title']}
Channel: {metadata['channel']}
{duration_note}
Description excerpt: {metadata['description']}
Multi-speaker detected: {is_multi_speaker}

--- RAW TRANSCRIPT ---
{raw_text}
--- END TRANSCRIPT ---

Please process this transcript according to the instructions."""

    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=8096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    raw_response = response.content[0].text.strip()

    # Strip markdown code fences if present
    if raw_response.startswith("```"):
        raw_response = re.sub(r"^```(?:json)?\n?", "", raw_response)
        raw_response = re.sub(r"\n?```$", "", raw_response)

    try:
        result = json.loads(raw_response)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"Claude returned invalid JSON: {e}\n\nRaw: {raw_response[:500]}")

    summary = result.get("summary", "")
    transcript_md = result.get("transcript", "")

    filename, note_content = build_obsidian_note(metadata, summary, transcript_md)

    return {
        "filename": filename,
        "content": note_content,
        "metadata": metadata,
    }


@app.get("/health")
async def health():
    return {"status": "ok"}
