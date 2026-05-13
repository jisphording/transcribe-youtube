"""Apple Podcasts → RSS feed → episode metadata → optional RSS transcript.

This module mirrors `youtube.py` for podcasts. It is independent: only `main.py`
orchestrates it.
"""

import re
import json
from urllib.parse import urlparse

import httpx
import feedparser


PODCAST_NS = "https://podcastindex.org/namespace/1.0"
ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"

USER_AGENT = "Obsidian-MediaTranscriber/1.0"


def is_apple_podcast_url(url: str) -> bool:
    return "podcasts.apple.com" in url


def parse_apple_url(url: str) -> tuple[str, str | None, str | None]:
    """Returns (show_id, episode_track_id, title_slug). Raises ValueError if not parsable."""
    show_match = re.search(r"/id(\d+)", url)
    if not show_match:
        raise ValueError(f"Could not extract Apple Podcasts show ID from URL: {url}")
    show_id = show_match.group(1)

    ep_match = re.search(r"[?&]i=(\d+)", url)
    episode_track_id = ep_match.group(1) if ep_match else None

    # The Apple share URL has a slug between /podcast/ and /id...
    slug_match = re.search(r"/podcast/([^/]+)/id\d+", url)
    title_slug = slug_match.group(1) if slug_match else None

    return show_id, episode_track_id, title_slug


def get_rss_feed_url(show_id: str) -> str:
    res = httpx.get(
        f"https://itunes.apple.com/lookup?id={show_id}&entity=podcast",
        timeout=15.0,
    )
    res.raise_for_status()
    data = res.json()
    if not data.get("resultCount") or not data["results"][0].get("feedUrl"):
        raise ValueError(
            f"No RSS feed found for Apple Podcasts show ID {show_id}. "
            "The show may be Spotify-exclusive or paywalled."
        )
    return data["results"][0]["feedUrl"]


def resolve_canonical_slug(apple_url: str) -> str | None:
    """Apple's CDN often rewrites the share URL to include the canonical episode
    title slug. We follow redirects with HEAD to read the final path. Returns the
    slug from the final URL if it differs from the input; otherwise the original
    slug from the input URL.
    """
    try:
        with httpx.Client(follow_redirects=True, timeout=10.0) as client:
            res = client.head(apple_url, headers={"User-Agent": "Mozilla/5.0"})
            final_url = str(res.url)
    except Exception:
        return None

    slug_match = re.search(r"/podcast/([^/]+)/id\d+", final_url)
    return slug_match.group(1) if slug_match else None


def _slugify(text: str) -> str:
    s = text.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-")


def _slug_token_overlap(a: str, b: str) -> float:
    ta, tb = set(a.split("-")), set(b.split("-"))
    if not ta or not tb:
        return 0.0
    common = ta & tb
    return len(common) / max(len(ta), len(tb))


def fetch_rss(rss_url: str) -> feedparser.FeedParserDict:
    res = httpx.get(rss_url, headers={"User-Agent": USER_AGENT}, timeout=30.0)
    res.raise_for_status()
    feed = feedparser.parse(res.content)
    if feed.bozo and not feed.entries:
        raise ValueError(f"Could not parse RSS feed at {rss_url}")
    return feed


def _format_duration(seconds: int) -> str:
    if not seconds:
        return "Unknown"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _parse_duration(value) -> int:
    if not value:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value)
    parts = s.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        return int(float(s))
    except ValueError:
        return 0


def _format_pub_date(parsed) -> str:
    if not parsed:
        return ""
    try:
        return f"{parsed.tm_year:04d}-{parsed.tm_mon:02d}-{parsed.tm_mday:02d}"
    except AttributeError:
        return ""


def find_episode(
    feed: feedparser.FeedParserDict,
    apple_url: str,
    title_slug: str | None,
    canonical_slug: str | None,
) -> tuple[dict, dict]:
    """Returns (episode_metadata, match_info).

    match_info has keys: matched (bool), strategy ("slug-exact"|"slug-token"|"latest-fallback"),
    notice (str | None — non-fatal warning to surface to the user).
    """
    entries = feed.entries
    if not entries:
        raise ValueError("RSS feed contains no episodes.")

    candidate_slugs = [s for s in (canonical_slug, title_slug) if s]

    matched = None
    strategy = "latest-fallback"
    for slug in candidate_slugs:
        for entry in entries:
            es = _slugify(entry.get("title", ""))
            if es == slug or slug == es:
                matched = entry
                strategy = "slug-exact"
                break
            if slug in es or es in slug:
                matched = entry
                strategy = "slug-exact"
                break
        if matched:
            break

    if matched is None:
        # Token-overlap fallback (handles partial matches, e.g. truncated slugs)
        best_score = 0.0
        best_entry = None
        for slug in candidate_slugs:
            for entry in entries:
                score = _slug_token_overlap(slug, _slugify(entry.get("title", "")))
                if score > best_score:
                    best_score = score
                    best_entry = entry
        if best_score >= 0.5 and best_entry is not None:
            matched = best_entry
            strategy = "slug-token"

    notice = None
    if matched is None:
        # Hard fail: do NOT silently use the latest episode.
        sample = "\n".join(
            f"  • {e.get('title', '')}" for e in entries[:5]
        )
        raise ValueError(
            "Could not match the Apple Podcasts URL to a specific RSS episode.\n"
            f"Apple slug: {canonical_slug or title_slug or '(none)'}\n"
            f"Latest 5 RSS titles:\n{sample}\n"
            "Try pasting the URL again, or check that the show isn't paywalled."
        )

    show_title = feed.feed.get("title", "Unknown Show")

    audio_url = None
    audio_type = None
    for enc in matched.get("enclosures", []) or []:
        if enc.get("href"):
            audio_url = enc["href"]
            audio_type = enc.get("type")
            break
    if not audio_url:
        raise ValueError(
            f'No audio URL found for episode "{matched.get("title", "?")}". '
            "The show may use a paywall or DRM-protected audio host."
        )

    duration = _parse_duration(matched.get("itunes_duration"))
    image_url = ""
    if matched.get("image", {}).get("href"):
        image_url = matched["image"]["href"]
    elif feed.feed.get("image", {}).get("href"):
        image_url = feed.feed["image"]["href"]

    metadata = {
        "source": "podcast",
        "title": matched.get("title", "Untitled Episode"),
        "show": show_title,
        "show_url": feed.feed.get("link", ""),
        "url": apple_url,
        "audio_url": audio_url,
        "audio_type": audio_type or "audio/mpeg",
        "published": _format_pub_date(matched.get("published_parsed")),
        "duration_seconds": duration,
        "duration": _format_duration(duration),
        "description": (matched.get("summary") or matched.get("description") or "")[:500],
        "thumbnail_url": image_url,
        "rss_entry": matched,  # passed along to RSS transcript fetcher
    }
    return metadata, {"matched": True, "strategy": strategy, "notice": notice}


# ─── RSS transcript (Podcasting 2.0) ─────────────────────────────────────────

def get_rss_transcript(rss_entry: dict) -> str | None:
    """Returns transcript text if a podcast:transcript tag is present and parseable.

    Prefers text/plain → text/vtt → application/srt → application/json.
    """
    transcripts = _extract_transcript_tags(rss_entry)
    if not transcripts:
        return None

    preference = ["text/plain", "text/vtt", "application/srt", "application/json"]
    transcripts.sort(key=lambda t: preference.index(t["type"]) if t["type"] in preference else 99)

    for t in transcripts:
        try:
            res = httpx.get(t["url"], headers={"User-Agent": USER_AGENT}, timeout=30.0)
            if res.status_code != 200:
                continue
            text = _normalise_transcript(res.text, t["type"])
            if text and len(text.strip()) >= 100:
                return text
        except Exception:
            continue
    return None


def _extract_transcript_tags(entry: dict) -> list[dict]:
    """feedparser surfaces podcast:transcript as `podcast_transcript` (single)
    or in `entry.get('podcast_transcripts')` depending on version. We also
    fall back to scanning the raw entry dict for keys containing 'transcript'.
    """
    out: list[dict] = []
    candidates = []

    raw = entry.get("podcast_transcript")
    if raw:
        candidates.append(raw)

    # feedparser stores repeated namespaced elements in entry as lists too
    for key, val in entry.items():
        if "transcript" in key.lower() and key not in ("podcast_transcript",):
            if isinstance(val, list):
                candidates.extend(val)
            else:
                candidates.append(val)

    for c in candidates:
        url = None
        mime = None
        if isinstance(c, dict):
            url = c.get("url") or c.get("href")
            mime = c.get("type") or c.get("mime") or "text/plain"
        elif hasattr(c, "url"):
            url = c.url
            mime = getattr(c, "type", "text/plain")
        if url:
            out.append({"url": url, "type": mime or "text/plain"})

    # Deduplicate by URL
    seen = set()
    unique = []
    for t in out:
        if t["url"] not in seen:
            seen.add(t["url"])
            unique.append(t)
    return unique


def _normalise_transcript(raw: str, mime_type: str) -> str:
    if mime_type == "text/plain":
        return raw.strip()
    if mime_type == "text/vtt":
        return _parse_vtt(raw)
    if mime_type == "application/srt":
        return _parse_srt(raw)
    if mime_type == "application/json":
        return _parse_json_transcript(raw)
    return raw.strip()


def _parse_vtt(vtt: str) -> str:
    out: list[str] = []
    pending_ts = ""
    for line in vtt.splitlines():
        t = line.strip()
        if not t or t == "WEBVTT" or t.startswith("NOTE"):
            continue
        if "-->" in t:
            ts = t.split("-->")[0].strip()
            ts = re.sub(r"\.\d+$", "", ts)
            if ts.count(":") == 1:
                ts = "00:" + ts
            pending_ts = ts
            continue
        if re.fullmatch(r"\d+", t):
            continue
        if pending_ts:
            out.append(f"[{pending_ts}] {t}")
            pending_ts = ""
        else:
            out.append(t)
    return "\n".join(out)


def _parse_srt(srt: str) -> str:
    blocks = re.split(r"\n\s*\n", srt.strip())
    out: list[str] = []
    for block in blocks:
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if len(lines) < 3:
            continue
        ts = lines[1].split("-->")[0].strip().replace(",", ".")[:8]
        out.append(f"[{ts}] {' '.join(lines[2:])}")
    return "\n".join(out)


def _parse_json_transcript(raw: str) -> str:
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        return raw.strip()
    if isinstance(d, dict) and isinstance(d.get("segments"), list):
        return "\n".join(
            f"[{_seconds_to_hms(s.get('start', 0))}] {s.get('text', '').strip()}"
            for s in d["segments"]
            if s.get("text")
        )
    if isinstance(d, dict) and isinstance(d.get("text"), str):
        return d["text"].strip()
    return raw.strip()


def _seconds_to_hms(sec: float) -> str:
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


# ─── Public entry point ──────────────────────────────────────────────────────

def resolve_episode(apple_url: str) -> tuple[dict, dict]:
    """Returns (episode_metadata, match_info). The metadata dict contains the
    fields used by the rest of the pipeline (note builder, whisper client).
    """
    show_id, _track_id, title_slug = parse_apple_url(apple_url)
    rss_url = get_rss_feed_url(show_id)
    canonical_slug = resolve_canonical_slug(apple_url)
    feed = fetch_rss(rss_url)
    metadata, match_info = find_episode(feed, apple_url, title_slug, canonical_slug)
    metadata["rss_url"] = rss_url
    return metadata, match_info
