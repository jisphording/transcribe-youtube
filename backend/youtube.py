import re
import os
import httpx
from fastapi import HTTPException
from youtube_transcript_api import YouTubeTranscriptApi
from yt_dlp import YoutubeDL
from cookies import COOKIE_FILE_PATH


VALID_BROWSERS = {"chrome", "firefox", "safari", "edge", "brave"}


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


def _apply_cookies(opts: dict, cookie_browser: str | None, cookie_file: str | None) -> None:
    """Apply cookie config to yt-dlp opts. Prefers browser extraction, then explicit file, then uploaded file."""
    if cookie_browser and cookie_browser in VALID_BROWSERS:
        opts["cookiesfrombrowser"] = (cookie_browser,)
    elif cookie_file and os.path.isfile(cookie_file):
        opts["cookiefile"] = cookie_file
    elif COOKIE_FILE_PATH.is_file():
        opts["cookiefile"] = str(COOKIE_FILE_PATH)


def get_video_metadata(url: str, cookie_browser: str | None = None, cookie_file: str | None = None) -> dict:
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": False,
        "ignore_no_formats_error": True,
    }
    _apply_cookies(ydl_opts, cookie_browser, cookie_file)
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


def get_transcript(video_id: str, cookie_browser: str | None = None, cookie_file: str | None = None) -> tuple[list, bool]:
    """Returns (transcript_entries, is_multi_speaker). Falls back to yt-dlp subtitles."""
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        try:
            transcript = transcript_list.find_manually_created_transcript(["en", "en-US", "en-GB"])
        except Exception:
            transcript = transcript_list.find_generated_transcript(["en", "en-US", "en-GB"])

        entries = transcript.fetch()
        raw_text = " ".join(e["text"] for e in entries[:50])
        is_multi_speaker = bool(re.search(r"\[[\w\s]+\]", raw_text))
        return entries, is_multi_speaker

    except Exception:
        pass

    entries = _get_transcript_via_ytdlp(video_id, cookie_browser, cookie_file)
    if entries:
        raw_text = " ".join(e["text"] for e in entries[:50])
        is_multi_speaker = bool(re.search(r"\[[\w\s]+\]", raw_text))
        return entries, is_multi_speaker

    raise HTTPException(status_code=422, detail="No transcript available from YouTube or yt-dlp.")


def _get_transcript_via_ytdlp(video_id: str, cookie_browser: str | None = None, cookie_file: str | None = None) -> list:
    """Extract subtitles using yt-dlp as a fallback."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "ignore_no_formats_error": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en", "en-US", "en-GB"],
        "subtitlesformat": "json3",
    }
    _apply_cookies(ydl_opts, cookie_browser, cookie_file)

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        subs = info.get("requested_subtitles") or {}
        for lang in ["en", "en-US", "en-GB"]:
            sub_info = subs.get(lang)
            if not sub_info:
                continue
            sub_url = sub_info.get("url")
            if not sub_url:
                continue
            resp = httpx.get(sub_url, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            entries = []
            for event in data.get("events", []):
                segs = event.get("segs", [])
                text = "".join(s.get("utf8", "") for s in segs).strip()
                if text and text != "\n":
                    entries.append({
                        "text": text,
                        "start": event.get("tStartMs", 0) / 1000,
                        "duration": event.get("dDurationMs", 0) / 1000,
                    })
            if entries:
                return entries
    return []


def transcript_to_plain_text(entries: list) -> str:
    """Merge transcript entries into a single block of text."""
    texts = [e["text"].strip() for e in entries]
    texts = [re.sub(r"\[[\w\s]+\]", "", t).strip() for t in texts]
    texts = [t for t in texts if t]
    return " ".join(texts)
