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
    pass


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
                    raise WebFetchError("Page not found (HTTP 404).")
                if resp.status_code == 429:
                    raise WebFetchError("The site is rate-limiting requests (HTTP 429). Try again later.")
                if resp.status_code >= 400:
                    raise WebFetchError(f"The site returned HTTP {resp.status_code}.")

                ctype = resp.headers.get("content-type", "").lower()
                if ctype and "html" not in ctype and "xml" not in ctype:
                    raise WebFetchError(f"Not a web page (content-type: {ctype.split(';')[0]}). Only HTML articles are supported.")

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

    text = trafilatura.extract(
        html,
        url=final_url,
        output_format="txt",
        include_comments=False,
        include_images=False,
        include_links=False,
        include_tables=True,
        include_formatting=False,
        deduplicate=True,
        favor_precision=True,
    )
    if not text:
        raise WebFetchError(
            "Could not find any article text on this page. It may be rendered with JavaScript, paywalled, or not an article."
        )
    text = _compact(text)
    word_count = len(text.split())
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
