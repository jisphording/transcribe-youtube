import re
import os
import sys
import glob
import http.cookiejar
from pathlib import Path
import httpx
from fastapi import HTTPException
from youtube_transcript_api import YouTubeTranscriptApi, RequestBlocked, IpBlocked
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError
from cookies import COOKIE_FILE_PATH


VALID_BROWSERS = {"chrome", "firefox", "safari", "edge", "brave"}
LIVE_STATUSES = {"is_live", "is_upcoming", "was_live", "post_live"}
_BLOCK_MARKERS = ("sign in to confirm", "not a bot", "try again later")


class YouTubeBlockedError(Exception):
    """YouTube refused the request (rate limit / bot check). Retrying soon makes it worse."""


def _is_block_message(msg: str) -> bool:
    msg = msg.lower()
    return any(m in msg for m in _BLOCK_MARKERS)


def is_youtube_url(url: str) -> bool:
    return bool(re.search(r"(youtube\.com/|youtu\.be/)", url, re.I))


def _resolve_cookie_path(cookie_file: str | None) -> str | None:
    if cookie_file and os.path.isfile(cookie_file):
        return cookie_file
    if COOKIE_FILE_PATH.is_file():
        return str(COOKIE_FILE_PATH)
    return None


def _browser_cookies_readable(browser: str) -> bool:
    """Return True iff the current process can actually read the browser's cookie store.
    On macOS, Safari's cookies are TCC-protected — without Full Disk Access the read
    raises PermissionError. Returning False lets the caller fall back to cookies.txt."""
    if sys.platform != "darwin":
        return False
    home = Path.home()
    candidates: list[Path] = {
        "safari":  [home / "Library/Cookies/Cookies.binarycookies"],
        "chrome":  [home / "Library/Application Support/Google/Chrome/Default/Cookies"],
        "firefox": [Path(p) for p in glob.glob(str(home / "Library/Application Support/Firefox/Profiles/*/cookies.sqlite"))],
        "edge":    [home / "Library/Application Support/Microsoft Edge/Default/Cookies"],
        "brave":   [home / "Library/Application Support/BraveSoftware/Brave-Browser/Default/Cookies"],
    }.get(browser, [])
    for p in candidates:
        try:
            with open(p, "rb") as f:
                f.read(1)
            return True
        except (PermissionError, OSError):
            continue
    return False


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
    """Apply cookie config to yt-dlp opts. Browser extraction is preferred, but if the
    browser's cookie store isn't readable (e.g. Full Disk Access not granted for Safari,
    or running inside Linux Docker), we fall through to the uploaded cookies.txt."""
    if cookie_browser and cookie_browser in VALID_BROWSERS and _browser_cookies_readable(cookie_browser):
        opts["cookiesfrombrowser"] = (cookie_browser,)
        return
    if cookie_file and os.path.isfile(cookie_file):
        opts["cookiefile"] = cookie_file
        return
    if COOKIE_FILE_PATH.is_file():
        opts["cookiefile"] = str(COOKIE_FILE_PATH)


def get_video_metadata(
    url: str,
    cookie_browser: str | None = None,
    cookie_file: str | None = None,
    sleep_requests: float = 0,
) -> dict:
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": False,
        "ignore_no_formats_error": True,
        "sleep_interval_requests": sleep_requests,
    }
    _apply_cookies(ydl_opts, cookie_browser, cookie_file)
    with YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except DownloadError as e:
            if _is_block_message(str(e)):
                raise YouTubeBlockedError(str(e)) from e
            raise
        duration_seconds = info.get("duration") or 0
        minutes = duration_seconds // 60
        seconds = duration_seconds % 60
        duration_str = f"{minutes}:{seconds:02d}"

        upload_date = info.get("upload_date", "")
        if upload_date and len(upload_date) == 8:
            upload_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"

        return {
            "source": "youtube",
            "video_id": info.get("id", ""),
            "title": info.get("title", "Unknown Title"),
            "channel": info.get("channel", info.get("uploader", "Unknown Channel")),
            "channel_url": info.get("channel_url", info.get("uploader_url", "")),
            "upload_date": upload_date,
            "duration": duration_str,
            "duration_seconds": duration_seconds,
            "live_status": info.get("live_status") or "",
            "thumbnail_url": info.get("thumbnail", ""),
            "description": (info.get("description", "") or "")[:500],
            "view_count": info.get("view_count", 0),
            "url": url,
        }


def get_transcript(
    video_id: str,
    cookie_browser: str | None = None,
    cookie_file: str | None = None,
    sleep_requests: float = 0,
) -> tuple[list, bool]:
    """Returns (transcript_entries, is_multi_speaker). Falls back to yt-dlp subtitles.
    Raises YouTubeBlockedError when YouTube blocks the IP — no point in falling back then."""
    try:
        transcript_list = YouTubeTranscriptApi().list(video_id)
        try:
            transcript = transcript_list.find_manually_created_transcript(["en", "en-US", "en-GB"])
        except Exception:
            transcript = transcript_list.find_generated_transcript(["en", "en-US", "en-GB"])

        entries = transcript.fetch().to_raw_data()
        raw_text = " ".join(e["text"] for e in entries[:50])
        is_multi_speaker = bool(re.search(r"\[[\w\s]+\]", raw_text))
        return entries, is_multi_speaker

    except (RequestBlocked, IpBlocked) as e:
        raise YouTubeBlockedError(f"YouTube blocked the transcript request: {type(e).__name__}") from e
    except Exception:
        pass

    entries = _get_transcript_via_ytdlp(video_id, cookie_browser, cookie_file, sleep_requests)
    if entries:
        raw_text = " ".join(e["text"] for e in entries[:50])
        is_multi_speaker = bool(re.search(r"\[[\w\s]+\]", raw_text))
        return entries, is_multi_speaker

    raise HTTPException(status_code=422, detail="No transcript available from YouTube or yt-dlp.")


def _get_transcript_via_ytdlp(
    video_id: str,
    cookie_browser: str | None = None,
    cookie_file: str | None = None,
    sleep_requests: float = 0,
) -> list:
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
        "sleep_interval_requests": sleep_requests,
    }
    _apply_cookies(ydl_opts, cookie_browser, cookie_file)
    cookie_path = _resolve_cookie_path(cookie_file)
    jar: http.cookiejar.MozillaCookieJar | None = None
    if cookie_path:
        jar = http.cookiejar.MozillaCookieJar(cookie_path)
        try:
            jar.load(ignore_discard=True, ignore_expires=True)
        except Exception:
            jar = None

    with YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except DownloadError as e:
            if _is_block_message(str(e)):
                raise YouTubeBlockedError(str(e)) from e
            raise
        subs = info.get("requested_subtitles") or {}
        for lang in ["en", "en-US", "en-GB"]:
            sub_info = subs.get(lang)
            if not sub_info:
                continue
            sub_url = sub_info.get("url")
            if not sub_url:
                continue
            resp = httpx.get(sub_url, cookies=jar, timeout=30)
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


# ─── Channel / playlist listing (batch queue) ────────────────────────────────

_CHANNEL_RE = re.compile(r"youtube\.com/(@[^/?#]+|channel/[^/?#]+|c/[^/?#]+|user/[^/?#]+)", re.I)


def is_youtube_collection_url(url: str) -> bool:
    """A channel or a playlist — but not a single video (a watch URL with &list= stays single)."""
    if re.search(r"[?&]v=|youtu\.be/|/shorts/|/live/|/embed/", url):
        return False
    return bool(_CHANNEL_RE.search(url) or re.search(r"youtube\.com/playlist\?(.*&)?list=", url, re.I))


def _normalise_collection_url(url: str) -> str:
    # Channels: always the /videos tab (no Shorts, no /streams — livestreams are excluded)
    m = _CHANNEL_RE.search(url)
    if m and "playlist?" not in url:
        return f"https://www.youtube.com/{m.group(1)}/videos"
    return url


def list_collection(url: str, limit: int, min_seconds: int) -> dict:
    """Newest-first videos of a channel or playlist, filtered by minimum duration.
    One flat request; entries already carry `duration`. Returns
    {title, items: [{video_id, url, title, duration_seconds}], has_more}."""
    fetch_n = min(max(limit * 3, limit + 1), 300)
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "playlist_items": f"1:{fetch_n}",
    }
    with YoutubeDL(opts) as ydl:
        try:
            info = ydl.extract_info(_normalise_collection_url(url), download=False)
        except DownloadError as e:
            if _is_block_message(str(e)):
                raise YouTubeBlockedError(str(e)) from e
            raise
    entries = [e for e in (info.get("entries") or []) if e]
    items = []
    for e in entries:
        video_url = e.get("url") or ""
        duration = e.get("duration")
        if "/shorts/" in video_url or duration is None or duration < min_seconds or not e.get("id"):
            continue
        items.append({
            "video_id": e["id"],
            "url": f"https://www.youtube.com/watch?v={e['id']}",
            "title": e.get("title") or e["id"],
            "duration_seconds": int(duration),
        })
    return {
        "title": (info.get("title") if "playlist?" in url else info.get("channel")) or info.get("title") or "",
        "items": items[:limit],
        "has_more": len(items) > limit or len(entries) >= fetch_n,
    }
