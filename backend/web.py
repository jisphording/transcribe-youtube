"""Web article fetching + main-text extraction.

Fetches only the HTML document itself — no images, stylesheets, scripts or
linked pages are requested. trafilatura strips navigation, ads, comments,
images and links, leaving just the article body as plain text.
"""

import re
from datetime import date
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import httpx
import trafilatura
from trafilatura.metadata import extract_metadata


MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_TEXT_CHARS = 60_000  # ~15k tokens — enough for long-form, caps cost on huge pages
MIN_WORDS = 120          # below this the page is likely paywalled, JS-rendered or not an article
WORDS_PER_MINUTE = 230

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.5 Safari/605.1.15"
    ),
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
    "Accept-Language": "en,de;q=0.8",
}

_TRACKING_PARAMS = re.compile(r"^(utm_\w+|fbclid|gclid|mc_cid|mc_eid|ref|ref_src|igshid|si)$", re.I)


class WebFetchError(Exception):
    """`manual_ok` is False when pasting the page text by hand can't help (404, not HTML)."""

    def __init__(self, message: str, manual_ok: bool = True):
        super().__init__(message)
        self.manual_ok = manual_ok


def is_web_url(url: str) -> bool:
    return urlsplit(url.strip()).scheme in ("http", "https")


def clean_url(url: str) -> str:
    """Drop fragment and tracking params so the stored source URL is stable."""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _TRACKING_PARAMS.match(k)]
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", urlencode(query), ""))


def _fetch_html(url: str) -> tuple[str, str]:
    """Return (html, final_url). Raises WebFetchError with a user-facing reason."""
    try:
        with httpx.Client(headers=_HEADERS, follow_redirects=True, timeout=20.0) as client:
            with client.stream("GET", url) as resp:
                if resp.status_code in (401, 403):
                    raise WebFetchError(f"Access denied (HTTP {resp.status_code}) — the site blocks automated access or requires a login.")
                if resp.status_code == 404:
                    raise WebFetchError("Page not found (HTTP 404).", manual_ok=False)
                if resp.status_code == 429:
                    raise WebFetchError("The site is rate-limiting requests (HTTP 429). Try again later.")
                if resp.status_code >= 400:
                    raise WebFetchError(f"The site returned HTTP {resp.status_code}.")

                ctype = resp.headers.get("content-type", "").lower()
                if ctype and "html" not in ctype and "xml" not in ctype:
                    raise WebFetchError(f"Not a web page (content-type: {ctype.split(';')[0]}). Only HTML articles are supported.", manual_ok=False)

                chunks: list[bytes] = []
                size = 0
                for chunk in resp.iter_bytes():
                    size += len(chunk)
                    if size > MAX_HTML_BYTES:
                        break
                    chunks.append(chunk)
                raw = b"".join(chunks)
                encoding = resp.encoding or "utf-8"
                final_url = str(resp.url)
    except httpx.TimeoutException:
        raise WebFetchError("The site did not respond within 20 seconds.")
    except httpx.HTTPError as e:
        raise WebFetchError(f"Could not reach the site: {e}")

    try:
        html = raw.decode(encoding, errors="replace")
    except LookupError:
        html = raw.decode("utf-8", errors="replace")
    return html, final_url


def _compact(text: str) -> str:
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fetch_article(url: str) -> tuple[dict, str]:
    """Fetch a URL and extract the main article text.

    Returns (metadata, text). Raises WebFetchError if the page is not
    accessible or yields no usable article text.
    """
    html, final_url = _fetch_html(url)

    # Precision mode gives the cleanest text on classic articles but drops most of
    # sectioned layouts (case studies, landing-style posts) — fall back if too short.
    text, word_count = "", 0
    for mode in ({"favor_precision": True}, {}, {"favor_recall": True}):
        candidate = trafilatura.extract(
            html,
            url=final_url,
            output_format="txt",
            include_comments=False,
            include_images=False,
            include_links=False,
            include_tables=True,
            include_formatting=False,
            deduplicate=True,
            **mode,
        )
        candidate = _compact(candidate or "")
        candidate_words = len(candidate.split())
        if candidate_words > word_count:
            text, word_count = candidate, candidate_words
        if word_count >= MIN_WORDS:
            break

    if not text:
        raise WebFetchError(
            "Could not find any article text on this page. It may be rendered with JavaScript, paywalled, or not an article."
        )
    if word_count < MIN_WORDS:
        raise WebFetchError(
            f"Only {word_count} words of main text found — the page is probably paywalled, behind a login/cookie wall, or rendered with JavaScript."
        )

    truncated = len(text) > MAX_TEXT_CHARS
    if truncated:
        cut = text.rfind("\n\n", 0, MAX_TEXT_CHARS)
        text = text[: cut if cut > MAX_TEXT_CHARS // 2 else MAX_TEXT_CHARS]

    meta = extract_metadata(html, default_url=final_url)
    authors = [a.strip() for a in (meta.author or "").split(";") if a.strip()]

    metadata = {
        "source": "web",
        "title": (meta.title or "").strip() or urlsplit(final_url).path.strip("/").split("/")[-1] or meta.hostname or "Untitled",
        "authors": authors,
        "site": (meta.sitename or meta.hostname or urlsplit(final_url).netloc).strip(),
        "url": clean_url(url),
        "published": meta.date or "",
        "accessed": date.today().isoformat(),
        "description": (meta.description or "").strip(),
        "language": meta.language or "",
        "word_count": word_count,
        "reading_time": f"{max(1, round(word_count / WORDS_PER_MINUTE))} min",
        "truncated": truncated,
    }
    return metadata, text


# ─── Manually pasted page text (fallback when the site blocks automated access) ───

# Whole lines that are page chrome on common blog/news platforms (compared lowercased).
_CHROME_LINES = {
    "get app", "open in app", "write", "sign up", "sign in", "log in", "login", "register",
    "follow", "following", "subscribe", "share", "listen", "save", "bookmark", "respond", "reply",
    "cancel", "menu", "search", "skip to content", "skip to main content", "unknown user",
    "member-only story", "top highlight", "more", "see all", "read more", "load more",
    "help", "status", "about", "careers", "press", "blog", "store", "privacy", "rules", "terms",
    "text to speech", "cookie settings", "accept all cookies", "advertisement",
    "press enter or click to view image in full size", "remember me for faster sign in",
    "write a response", "what are your thoughts?", "no responses yet", "see more recommendations",
}
_COUNT_LINE = re.compile(r"^[\d.,]+[kKmM]?$|^[·•|\-–—]+$")
_READ_TIME_LINE = re.compile(r"^\d+\s*min(ute)?s?\s+read$", re.I)


def clean_pasted_text(text: str) -> str:
    """Deterministic first pass over clipboard text: drop obvious UI chrome and duplicates.

    Anything subtler (tag lists, author bios, recommendations) is left for Claude,
    which reports where the article body starts and ends — see `trim_to_body`.
    """
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = re.sub(r"[ \t ​]+", " ", raw).strip()
        low = line.lower()
        if line and (low in _CHROME_LINES or _COUNT_LINE.match(line) or _READ_TIME_LINE.match(line)):
            continue
        if line and lines and line == lines[-1]:
            continue
        lines.append(line)
    return _compact("\n".join(lines))


def _snippet_pattern(snippet: str) -> re.Pattern | None:
    words = re.findall(r"\S+", snippet)
    return re.compile(r"\s+".join(re.escape(w) for w in words)) if words else None


def trim_to_body(text: str, first_words: str, last_words: str) -> str:
    """Cut `text` to the span between two verbatim snippets. Falls back to the full text."""
    start_pat, end_pat = _snippet_pattern(first_words), _snippet_pattern(last_words)
    start = start_pat.search(text) if start_pat else None
    begin = start.start() if start else 0
    ends = list(end_pat.finditer(text, begin)) if end_pat else []
    end = ends[-1].end() if ends else len(text)
    body = text[begin:end].strip()
    return body if len(body.split()) >= MIN_WORDS // 2 else text


def pasted_article(url: str, text: str) -> tuple[dict, str]:
    """Build (metadata, text) from text the user copied from the page.

    Title, author and date are unknown here — Claude fills them in from the text.
    """
    text = clean_pasted_text(text)
    word_count = len(text.split())
    if word_count < MIN_WORDS:
        raise WebFetchError(f"The pasted text has only {word_count} words — copy the whole article and try again.")

    truncated = len(text) > MAX_TEXT_CHARS
    if truncated:
        text = text[:MAX_TEXT_CHARS]

    parts = urlsplit(url.strip())
    slug = parts.path.strip("/").split("/")[-1] if parts.path.strip("/") else ""
    metadata = {
        "source": "web",
        "manual": True,
        "title": slug or parts.netloc or "Untitled",
        "authors": [],
        "site": parts.netloc.lower().removeprefix("www."),
        "url": clean_url(url),
        "published": "",
        "accessed": date.today().isoformat(),
        "description": "",
        "language": "",
        "truncated": truncated,
    }
    set_word_count(metadata, text)
    return metadata, text


def set_word_count(metadata: dict, text: str) -> None:
    metadata["word_count"] = len(text.split())
    metadata["reading_time"] = f"{max(1, round(metadata['word_count'] / WORDS_PER_MINUTE))} min"
