import asyncio
import json
import math
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from models import TranscriptRequest, CookieUpload, BatchRequest, BatchOptions
from youtube import (
    is_youtube_url, extract_video_id, get_video_metadata, get_transcript, transcript_to_plain_text,
    YouTubeBlockedError, LIVE_STATUSES, is_youtube_collection_url, list_collection,
)
from podcast import is_apple_podcast_url, resolve_episode, resolve_episode_by_guid, get_rss_transcript, is_apple_show_url, list_show_episodes
from web import is_web_url, fetch_article, pasted_article, trim_to_body, set_word_count, WebFetchError, MAX_TEXT_CHARS
import whisper as whisper_mod
from whisper import transcribe_url as whisper_transcribe, WhisperUnavailableError, DEFAULT_SERVER_URL
from note import build_obsidian_note
from claude import stream_claude, parse_claude_response, resolve_model, DEFAULT_MODEL, DEFAULT_EXTENDED_MODEL, HAIKU, SONNET, OPUS
from cookies import has_cookies, save_cookies, delete_cookies
import vault
import batch_queue
from prompts import get_system_prompt


WHISPER_IDLE_TIMEOUT_SECONDS = 30 * 60
WHISPER_IDLE_CHECK_SECONDS = 60


async def _whisper_idle_watcher():
    """Stop the whisper-server after WHISPER_IDLE_TIMEOUT_SECONDS without activity."""
    while True:
        try:
            await asyncio.sleep(WHISPER_IDLE_CHECK_SECONDS)
            if whisper_mod.is_in_flight():
                continue
            if not whisper_mod.is_available(DEFAULT_SERVER_URL):
                continue
            last = whisper_mod.get_last_activity()
            if last == 0.0:
                # Server is up but the backend never observed activity
                # (e.g. started by a different process). Treat "now" as activity.
                whisper_mod.mark_activity()
                continue
            if (time.time() - last) > WHISPER_IDLE_TIMEOUT_SECONDS:
                whisper_mod.stop_server()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Watcher must never die
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    batch_queue.init_db()
    tasks = [
        asyncio.create_task(_whisper_idle_watcher()),
        asyncio.create_task(batch_queue.run_worker(_process_batch_item)),
    ]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


app = FastAPI(title="Media to Obsidian API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["app://obsidian.md", "http://localhost", "*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# Chunked transcript processing (used when transcript exceeds Haiku's output limit)
_CHUNK_CHARS = 20_000
_CHUNK_THRESHOLD_CHARS = 25_000

_CHUNK_CLEAN_SYSTEM = """You are a transcript editor.
You will receive a segment of a raw transcript. Clean it and return a JSON object with exactly 2 keys:
1. "heading": A short, descriptive title (3-6 words) for this segment's topic.
2. "transcript": The cleaned transcript text for this segment.

Rules for "transcript":
- Remove ALL filler words: "um", "uh", "like", "you know", "sort of", "kind of", "basically", "literally", "actually", "right", "okay so", "so yeah", "I mean", etc.
- Keep content close to original wording — do not paraphrase or rewrite sentences.
- Preserve paragraph breaks for readability. Group related sentences together.
- Do NOT add any content that wasn't in the original.

Return ONLY the JSON object, no other text."""

_CHUNK_SUMMARY_SYSTEM_BASE = """You are a transcript summarizer.
You will receive the cleaned transcript of a {kind} along with its title and {source_attr}.
Return a JSON object with exactly {key_count} keys:
1. "summary": A concise 3-5 sentence summary of the {noun}'s main content and key takeaways.
2. "topics": A JSON array of 5-10 short tags (1-3 words each) describing the main topics discussed. Lowercase, use hyphens for multi-word tags (e.g. "machine-learning"). Focus on core subject matter — concepts, domains, technologies, people.
{resources_key}
Return ONLY the JSON object, no other text."""


def _build_chunk_summary_system(extract_resources: bool, source: str) -> str:
    if source == "podcast":
        kind, noun, source_attr = "podcast episode", "episode", "show"
    else:
        kind, noun, source_attr = "YouTube video", "video", "channel"
    if extract_resources:
        resources_key = '3. "resources": A JSON array of objects (each with "name" and "type") for every product, software, website, service, tool, or platform mentioned by name. Deduplicate. Return [] if none.'
        return _CHUNK_SUMMARY_SYSTEM_BASE.format(
            kind=kind, noun=noun, source_attr=source_attr, key_count=3, resources_key=resources_key,
        )
    return _CHUNK_SUMMARY_SYSTEM_BASE.format(
        kind=kind, noun=noun, source_attr=source_attr, key_count=2, resources_key="",
    )


def _split_transcript(text: str, chunk_size: int) -> list[str]:
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end >= len(text):
            chunks.append(text[start:].strip())
            break
        para = text.rfind("\n\n", start + chunk_size // 2, end)
        if para != -1:
            end = para
        else:
            for sep in (". ", "? ", "! "):
                sent = text.rfind(sep, start + chunk_size // 2, end)
                if sent != -1:
                    end = sent + 1
                    break
        chunks.append(text[start:end].strip())
        start = end
    return [c for c in chunks if c.strip()]


def _sse_event(stage: str, message: str, **extra) -> str:
    data = {"stage": stage, "message": message, **extra}
    return json.dumps(data)


# Pricing per million tokens (USD)
MODEL_PRICING = {
    HAIKU:  {"input": 1.00, "output": 5.00},
    SONNET: {"input": 2.00, "output": 10.00},
    OPUS:   {"input": 4.00, "output": 20.00},
}

REFUSAL_MESSAGE = "Claude declined to process this content (safety refusal)."


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = MODEL_PRICING[resolve_model(model, DEFAULT_MODEL)]
    return (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000


def _fmt_tokens(n: int) -> str:
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _fmt_elapsed(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def _detect_source(url: str) -> str:
    if is_apple_podcast_url(url):
        return "podcast"
    if is_youtube_url(url) or not is_web_url(url):
        return "youtube"
    return "web"


@app.post("/process")
async def process_media(request: TranscriptRequest):
    source = _detect_source(request.url)
    total_steps = 4

    def event_generator():
        try:
            if source == "youtube":
                yield from _run_youtube_pipeline(request, total_steps)
            elif source == "podcast":
                yield from _run_podcast_pipeline(request, total_steps)
            else:
                yield from _run_web_pipeline(request, total_steps)
        except Exception as e:
            yield _sse_event("error", f"Unexpected error: {e}")

    return EventSourceResponse(event_generator())


# ─── YouTube pipeline ────────────────────────────────────────────────────────

def _run_youtube_pipeline(request: TranscriptRequest, total_steps: int):
    try:
        video_id = extract_video_id(request.url)
    except ValueError as e:
        yield _sse_event("error", str(e))
        return

    cookie_browser = request.cookie_browser
    cookie_file = request.cookie_file

    # Step 1: Metadata
    yield _sse_event("metadata", f"Step 1/{total_steps} — Fetching video metadata…",
                     step=1, total_steps=total_steps)
    try:
        metadata = get_video_metadata(request.url, cookie_browser, cookie_file, request.sleep_requests)
    except YouTubeBlockedError as e:
        yield _sse_event("error", f"YouTube blocked the request: {e}", blocked=True)
        return
    except Exception as e:
        yield _sse_event("error", f"Could not fetch video metadata: {e}")
        return

    if request.skip_live and metadata["live_status"] in LIVE_STATUSES:
        yield _sse_event("error", f"Skipped: livestream ({metadata['live_status']})", skipped=True)
        return

    yield _sse_event(
        "metadata_done",
        f"Step 1/{total_steps} — Got metadata: {metadata['title']} ({metadata.get('duration', '?')})",
        step=1, total_steps=total_steps,
    )

    # Step 2: Transcript
    yield _sse_event("transcript", f"Step 2/{total_steps} — Fetching transcript…",
                     step=2, total_steps=total_steps)
    try:
        entries, is_multi_speaker = get_transcript(video_id, cookie_browser, cookie_file, request.sleep_requests)
    except YouTubeBlockedError as e:
        yield _sse_event("error", f"YouTube blocked the request: {e}", blocked=True)
        return
    except HTTPException as e:
        yield _sse_event("error", e.detail)
        return
    except Exception as e:
        yield _sse_event("error", f"Could not fetch transcript: {e}")
        return

    raw_text = transcript_to_plain_text(entries)
    transcript_chars = len(raw_text)
    yield _sse_event(
        "transcript_done",
        f"Step 2/{total_steps} — Got transcript ({len(entries)} segments, ~{transcript_chars:,} chars)",
        step=2, total_steps=total_steps,
        segments=len(entries), transcript_chars=transcript_chars,
    )

    # Steps 3 + 4 (shared)
    yield from _run_claude_and_note(
        request=request,
        metadata=metadata,
        raw_text=raw_text,
        is_multi_speaker=is_multi_speaker,
        source="youtube",
        transcript_source_label="",
        total_steps=total_steps,
    )


# ─── Podcast pipeline ────────────────────────────────────────────────────────

def _run_podcast_pipeline(request: TranscriptRequest, total_steps: int):
    # Step 1: Resolve Apple Podcasts URL → RSS → episode metadata
    yield _sse_event("metadata", f"Step 1/{total_steps} — Resolving Apple Podcasts URL…",
                     step=1, total_steps=total_steps)
    try:
        if request.episode_guid:
            metadata, match_info = resolve_episode_by_guid(request.url, request.episode_guid)
        else:
            metadata, match_info = resolve_episode(request.url)
    except ValueError as e:
        yield _sse_event("error", str(e))
        return
    except Exception as e:
        yield _sse_event("error", f"Could not resolve podcast URL: {e}")
        return

    yield _sse_event(
        "metadata_done",
        f"Step 1/{total_steps} — Found episode: {metadata['title']} ({metadata.get('duration', '?')})",
        step=1, total_steps=total_steps,
    )
    if match_info.get("strategy") == "slug-token":
        yield _sse_event(
            "metadata", "Note: matched RSS episode via fuzzy title comparison.",
            step=1, total_steps=total_steps,
        )

    # Step 2: Try RSS transcript first; fall back to whisper.
    yield _sse_event("transcript_rss", f"Step 2/{total_steps} — Checking for free RSS transcript…",
                     step=2, total_steps=total_steps)
    rss_text = None
    try:
        rss_text = get_rss_transcript(metadata["rss_entry"])
    except Exception:
        rss_text = None

    if rss_text:
        raw_text = rss_text
        transcript_source_label = "RSS feed (free)"
        transcript_chars = len(raw_text)
        yield _sse_event(
            "transcript_done",
            f"Step 2/{total_steps} — Got RSS transcript (~{transcript_chars:,} chars). No audio download needed.",
            step=2, total_steps=total_steps,
            segments=raw_text.count("\n"), transcript_chars=transcript_chars,
        )
    else:
        # Fall back to whisper-server. Lazy-start it if not yet running.
        if not whisper_mod.is_available(DEFAULT_SERVER_URL):
            yield _sse_event(
                "transcript_whisper_starting",
                f"Step 2/{total_steps} — Starting local whisper-server (loading model, ~3s)…",
                step=2, total_steps=total_steps,
            )

        yield _sse_event(
            "transcript_whisper_download",
            f"Step 2/{total_steps} — No RSS transcript found. Downloading audio for whisper…",
            step=2, total_steps=total_steps,
        )
        latest_progress = {"msg": ""}

        def progress(msg: str):
            latest_progress["msg"] = msg

        try:
            language = request.whisper_language or "auto"
            raw_text, detected_language = whisper_transcribe(
                metadata["audio_url"],
                server_url=DEFAULT_SERVER_URL,
                language=language,
                include_timestamps=True,
                on_progress=progress,
            )
        except WhisperUnavailableError as e:
            yield _sse_event("error", str(e))
            return
        except Exception as e:
            yield _sse_event("error", f"Whisper transcription failed: {e}")
            return

        transcript_source_label = "whisper.cpp local"
        transcript_chars = len(raw_text)
        yield _sse_event(
            "transcript_done",
            f"Step 2/{total_steps} — Whisper transcription complete (~{transcript_chars:,} chars, language: {detected_language})",
            step=2, total_steps=total_steps,
            segments=raw_text.count("\n"), transcript_chars=transcript_chars,
        )

    # Strip non-serializable internals from metadata before downstream use.
    metadata.pop("rss_entry", None)
    metadata.pop("rss_url", None)

    # Steps 3 + 4 (shared)
    yield from _run_claude_and_note(
        request=request,
        metadata=metadata,
        raw_text=raw_text,
        is_multi_speaker=True,  # podcasts almost always have multiple voices
        source="podcast",
        transcript_source_label=transcript_source_label,
        total_steps=total_steps,
    )


# ─── Web article pipeline ───────────────────────────────────────────────────

def _run_web_pipeline(request: TranscriptRequest, total_steps: int):
    if request.manual_text:
        # Step 1+2: the user pasted the page text because the site blocked us
        try:
            metadata, text = pasted_article(request.url, request.manual_text)
        except WebFetchError as e:
            yield _sse_event("error", str(e))
            return
        yield _sse_event(
            "metadata_done",
            f"Step 1/{total_steps} — Using pasted page text ({metadata['site']})",
            step=1, total_steps=total_steps,
        )
        extracted_label = "Cleaned pasted text"
    else:
        # Step 1: Fetch the HTML document only (no images, scripts or linked pages)
        yield _sse_event("web_fetch", f"Step 1/{total_steps} — Fetching page (text only)…",
                         step=1, total_steps=total_steps)
        try:
            metadata, text = fetch_article(request.url)
        except WebFetchError as e:
            if e.manual_ok:
                yield _sse_event("web_blocked", f"Page not accessible: {e}")
            else:
                yield _sse_event("error", f"Page not accessible: {e}")
            return
        except Exception as e:
            yield _sse_event("error", f"Could not fetch page: {e}")
            return

        byline = f" · {', '.join(metadata['authors'])}" if metadata["authors"] else ""
        yield _sse_event(
            "metadata_done",
            f"Step 1/{total_steps} — Page accessible: {metadata['site']}{byline}",
            step=1, total_steps=total_steps,
        )
        extracted_label = f"Extracted \"{metadata['title']}\""

    note = f" (truncated to first {MAX_TEXT_CHARS:,} chars)" if metadata["truncated"] else ""
    yield _sse_event(
        "web_extract_done",
        f"Step 2/{total_steps} — {extracted_label} — {metadata['word_count']:,} words{note}",
        step=2, total_steps=total_steps,
        words=metadata["word_count"], transcript_chars=len(text),
    )

    # Steps 3 + 4 (shared)
    yield from _run_claude_and_note(
        request=request,
        metadata=metadata,
        raw_text=text,
        is_multi_speaker=False,
        source="web",
        transcript_source_label="",
        total_steps=total_steps,
    )


# ─── Shared Claude + note assembly ───────────────────────────────────────────

def _run_claude_and_note(
    request: TranscriptRequest,
    metadata: dict,
    raw_text: str,
    is_multi_speaker: bool,
    source: str,
    transcript_source_label: str,
    total_steps: int,
):
    transcript_chars = len(raw_text)

    features = ["base"]
    if request.extended_summary or request.focus_include_extended:
        features.append("extended")
    if request.focus_topic:
        features.append("focus")
    if request.extract_resources:
        features.append("resources")
    pasted = source == "web" and metadata.get("manual", False)
    if pasted:
        features.append("pasted")

    use_extended_model = request.extended_summary or bool(request.focus_topic) or request.focus_include_extended
    if use_extended_model:
        model = resolve_model(request.extended_model, DEFAULT_EXTENDED_MODEL)
    else:
        model = resolve_model(request.model, DEFAULT_MODEL)
    effort = "medium" if use_extended_model else "low"
    if request.focus_topic:
        stage_label = "claude_focus"
    elif request.extended_summary:
        stage_label = "claude_extended"
    else:
        stage_label = "claude"

    use_chunks = (
        source != "web"
        and not use_extended_model
        and model == HAIKU
        and transcript_chars > _CHUNK_THRESHOLD_CHARS
    )

    if use_chunks:
        chunks = _split_transcript(raw_text, _CHUNK_CHARS)
        n_chunks = len(chunks)
        yield _sse_event(
            stage_label,
            f"Step 3/{total_steps} — Transcript split into {n_chunks} chunks for Haiku…",
            step=3, total_steps=total_steps,
        )

        cleaned_sections: list[str] = []
        total_in_tokens = 0
        total_out_tokens = 0
        chunk_start_time = time.time()

        for i, chunk in enumerate(chunks):
            yield _sse_event(
                stage_label,
                f"Step 3/{total_steps} — Cleaning chunk {i + 1}/{n_chunks}…",
                step=3, total_steps=total_steps,
            )
            chunk_user = (
                f"{_title_label(source)}: {metadata['title']} (segment {i + 1}/{n_chunks})\n\n"
                f"--- TRANSCRIPT SEGMENT ---\n{chunk}\n--- END SEGMENT ---"
            )
            raw_chunk = None
            try:
                for update in stream_claude(model, _CHUNK_CLEAN_SYSTEM, chunk_user, effort):
                    if update["type"] == "done":
                        if update["stop_reason"] == "refusal":
                            yield _sse_event("error", f"{REFUSAL_MESSAGE} (chunk {i + 1})")
                            return
                        raw_chunk = update["response"]
                        total_in_tokens += update["input_tokens"]
                        total_out_tokens += update["output_tokens"]
            except Exception as e:
                yield _sse_event("error", f"Claude API error on chunk {i + 1}: {e}")
                return

            if raw_chunk is None:
                yield _sse_event("error", f"No response for chunk {i + 1}.")
                return
            try:
                chunk_result = parse_claude_response(raw_chunk)
                heading = chunk_result.get("heading", f"Part {i + 1}")
                chunk_text = chunk_result.get("transcript", "")
                cleaned_sections.append(f"## {heading}\n\n{chunk_text}")
            except Exception as e:
                yield _sse_event("error", f"Invalid JSON from Claude on chunk {i + 1}: {e}")
                return

        yield _sse_event(
            stage_label,
            f"Step 3/{total_steps} — Generating summary…",
            step=3, total_steps=total_steps,
        )
        combined_cleaned = "\n\n".join(cleaned_sections)
        source_name_label = "Channel" if source == "youtube" else "Show"
        source_name = metadata.get("channel") if source == "youtube" else metadata.get("show", "")
        summary_user = (
            f"Title: {metadata['title']}\n{source_name_label}: {source_name}\n\n"
            f"Transcript:\n{combined_cleaned[:40_000]}"
        )
        summary_raw = None
        chunk_summary_system = _build_chunk_summary_system(request.extract_resources, source)
        try:
            for update in stream_claude(model, chunk_summary_system, summary_user, effort):
                if update["type"] == "done":
                    if update["stop_reason"] == "refusal":
                        yield _sse_event("error", REFUSAL_MESSAGE)
                        return
                    summary_raw = update["response"]
                    total_in_tokens += update["input_tokens"]
                    total_out_tokens += update["output_tokens"]
        except Exception as e:
            yield _sse_event("error", f"Claude API error on summary: {e}")
            return

        elapsed_total = time.time() - chunk_start_time
        cost = _estimate_cost(model, total_in_tokens, total_out_tokens)
        yield _sse_event(
            "claude_done",
            f"Step 3/{total_steps} — Claude done! {_fmt_tokens(total_in_tokens)} in → {_fmt_tokens(total_out_tokens)} out ({_fmt_elapsed(elapsed_total)})",
            step=3, total_steps=total_steps,
            input_tokens=total_in_tokens, output_tokens=total_out_tokens,
            elapsed=round(elapsed_total, 1), cost_usd=round(cost, 4),
        )

        try:
            summary_result = parse_claude_response(summary_raw) if summary_raw else {}
        except Exception:
            summary_result = {}

        summary = summary_result.get("summary", "")
        topics = summary_result.get("topics", [])
        resources = summary_result.get("resources", []) if request.extract_resources else []
        transcript_md = combined_cleaned
        extended_summary = ""
        focused_summary = ""
        article_info = None

    else:
        system_prompt = get_system_prompt(features, source=source)

        if source == "web":
            task_desc = "summary + extended summary" if request.extended_summary else "summary"
        else:
            task_desc = "summary + extended summary + transcript" if request.extended_summary else "summary + transcript"
        yield _sse_event(
            stage_label,
            f"Step 3/{total_steps} — Sending to Claude ({model.split('-')[1].capitalize()})… Processing {task_desc}",
            step=3, total_steps=total_steps,
        )

        focus_line = f"\nFocus instruction: {request.focus_topic}" if request.focus_topic else ""
        if source == "youtube":
            user_message = f"""Video Title: {metadata['title']}
Channel: {metadata['channel']}
Video duration: {metadata.get('duration', '')}
Description excerpt: {metadata.get('description', '')}
Multi-speaker detected: {is_multi_speaker}{focus_line}

--- RAW TRANSCRIPT ---
{raw_text}
--- END TRANSCRIPT ---

Please process this transcript according to the instructions."""
        elif source == "web" and pasted:
            user_message = f"""Page URL: {metadata['url']}
Site: {metadata['site']}{focus_line}

--- PASTED PAGE TEXT ---
{raw_text}
--- END PASTED PAGE TEXT ---

Please process this article according to the instructions."""
        elif source == "web":
            user_message = f"""Article Title: {metadata['title']}
Author: {', '.join(metadata['authors']) or 'unknown'}
Site: {metadata['site']}
Published: {metadata['published'] or 'unknown'}
Description: {metadata['description']}{focus_line}

--- ARTICLE TEXT ---
{raw_text}
--- END ARTICLE TEXT ---

Please process this article according to the instructions."""
        else:
            user_message = f"""Episode Title: {metadata['title']}
Show: {metadata.get('show', '')}
Episode duration: {metadata.get('duration', '')}
Description excerpt: {metadata.get('description', '')}
Multi-speaker detected: {is_multi_speaker}{focus_line}

--- RAW TRANSCRIPT ---
{raw_text}
--- END TRANSCRIPT ---

Please process this transcript according to the instructions."""

        raw_response = None
        try:
            for update in stream_claude(model, system_prompt, user_message, effort):
                if update["type"] == "progress":
                    elapsed = _fmt_elapsed(update["elapsed"])
                    in_tok = _fmt_tokens(update["input_tokens"])
                    out_tok = _fmt_tokens(update["output_tokens"])
                    if update["phase"] == "starting":
                        msg = f"Step 3/{total_steps} — Claude received {in_tok} input tokens, generating…"
                    else:
                        msg = f"Step 3/{total_steps} — Claude generating… {out_tok} output tokens ({elapsed})"
                    yield _sse_event(
                        stage_label, msg,
                        step=3, total_steps=total_steps,
                        input_tokens=update["input_tokens"],
                        output_tokens=update["output_tokens"],
                        elapsed=round(update["elapsed"], 1),
                    )
                elif update["type"] == "done":
                    if update["stop_reason"] == "refusal":
                        yield _sse_event("error", REFUSAL_MESSAGE)
                        return
                    raw_response = update["response"]
                    elapsed = _fmt_elapsed(update["elapsed"])
                    in_tok = _fmt_tokens(update["input_tokens"])
                    out_tok = _fmt_tokens(update["output_tokens"])
                    cost = _estimate_cost(model, update["input_tokens"], update["output_tokens"])
                    yield _sse_event(
                        "claude_done",
                        f"Step 3/{total_steps} — Claude done! {in_tok} in → {out_tok} out ({elapsed})",
                        step=3, total_steps=total_steps,
                        input_tokens=update["input_tokens"],
                        output_tokens=update["output_tokens"],
                        elapsed=round(update["elapsed"], 1),
                        cost_usd=round(cost, 4),
                    )
        except Exception as e:
            yield _sse_event("error", f"Claude API error: {e}")
            return

        if raw_response is None:
            yield _sse_event("error", "Claude stream ended without a response.")
            return

        try:
            result = parse_claude_response(raw_response)
        except Exception as e:
            yield _sse_event("error", f"Claude returned invalid JSON: {e}")
            return

        summary = result.get("summary", "")
        topics = result.get("topics", [])
        resources = result.get("resources", []) if request.extract_resources else []
        extended_summary = result.get("extended_summary", "") if (request.extended_summary or request.focus_include_extended) else ""
        focused_summary = result.get("focused_summary", "") if request.focus_topic else ""
        transcript_md = result.get("transcript", "")
        article_info = {
            "tldr": result.get("tldr", ""),
            "key_points": result.get("key_points", []),
            "content_type": result.get("content_type", ""),
            "useful_for": result.get("useful_for", []),
        } if source == "web" else None
        if pasted:
            metadata["title"] = (result.get("title") or "").strip() or metadata["title"]
            metadata["authors"] = [a.strip() for a in result.get("authors") or [] if isinstance(a, str) and a.strip()]
            metadata["published"] = (result.get("published") or "").strip()
            body = trim_to_body(raw_text, result.get("body_first_words") or "", result.get("body_last_words") or "")
            set_word_count(metadata, body)

    # Step 4: Build note
    yield _sse_event("building", f"Step 4/{total_steps} — Building Obsidian note…",
                     step=4, total_steps=total_steps)
    filename, note_content = build_obsidian_note(
        metadata, summary, transcript_md,
        extended_summary=extended_summary,
        focused_summary=focused_summary,
        focus_topic=request.focus_topic or "",
        include_transcript=request.include_transcript,
        topics=topics,
        resources=resources,
        transcript_source_label=transcript_source_label,
        article_info=article_info,
    )

    yield _sse_event(
        "done", "Done!",
        filename=filename, content=note_content, metadata=metadata,
        resources=resources, source=source,
    )


def _title_label(source: str) -> str:
    return {"podcast": "Episode", "web": "Article"}.get(source, "Video")


# ─── Batch queue (channels, playlists, podcast shows) ───────────────────────

BATCH_ALL_LIMIT = 20            # "all" is only offered for collections of up to 20 items
BATCH_MAX_COUNT = 200
BATCH_YOUTUBE_SLEEP_REQUESTS = 0.75

# Cost estimate heuristics (calibrate against the recorded cost_usd of real batches)
EST_TOKENS_PER_MINUTE = 200     # ≈ 150 spoken words/min × 1.3 tokens/word
EST_CHARS_PER_MINUTE = 830
EST_SYSTEM_TOKENS = 2_000
EST_SUMMARY_OUTPUT_TOKENS = 3_000
EST_CHUNK_OVERHEAD_TOKENS = 600  # per chunk: system prompt + heading/JSON wrapper
EST_THINKING_HEADROOM = 1.2      # Sonnet 5 / Opus 5.5 bill thinking as output
EST_SPREAD = 0.3


def _batch_model(opts: BatchOptions) -> tuple[str, bool]:
    use_extended = opts.extended_summary or bool(opts.focus_topic) or opts.focus_include_extended
    if use_extended:
        return resolve_model(opts.extended_model, DEFAULT_EXTENDED_MODEL), True
    return resolve_model(opts.model, DEFAULT_MODEL), False


def _estimate_item_cost(duration_s: int, opts: BatchOptions) -> float:
    model, use_extended = _batch_model(opts)
    minutes = max(duration_s, 60) / 60
    transcript = minutes * EST_TOKENS_PER_MINUTE
    inp = transcript + EST_SYSTEM_TOKENS
    out = EST_SUMMARY_OUTPUT_TOKENS * (2 if use_extended else 1)
    if opts.include_transcript:
        out += transcript
    chars = minutes * EST_CHARS_PER_MINUTE
    if model == HAIKU and not use_extended and chars > _CHUNK_THRESHOLD_CHARS:
        n_chunks = math.ceil(chars / _CHUNK_CHARS)
        inp += n_chunks * EST_CHUNK_OVERHEAD_TOKENS + min(transcript, 10_000)  # + summary call
        out += n_chunks * 50
    if model != HAIKU:
        out *= EST_THINKING_HEADROOM
    return _estimate_cost(model, int(inp), int(out))


@app.post("/batch")
async def batch_create(req: BatchRequest):
    """List + filter + dedup + estimate. The batch waits for /confirm (expires after 30 min)."""
    if is_youtube_collection_url(req.url):
        source, lister = "youtube", list_collection
    elif is_apple_show_url(req.url):
        source, lister = "podcast", list_show_episodes
    else:
        raise HTTPException(400, "Not a YouTube channel / playlist or an Apple Podcasts show URL.")
    try:
        vault.validate_paths(req.vault_root, [req.folders.youtube, req.folders.podcast, req.folders.resources, *req.scan_roots])
    except vault.VaultPathError as e:
        raise HTTPException(400, str(e))

    limit = min(req.count or BATCH_ALL_LIMIT, BATCH_MAX_COUNT)
    # List the whole window, not just `limit` items: items already in the vault are skipped
    # and the next new ones take their place.
    try:
        listing = await asyncio.to_thread(lister, req.url, BATCH_MAX_COUNT, max(req.min_minutes, 0) * 60)
    except YouTubeBlockedError as e:
        raise HTTPException(429, f"YouTube blocked the request: {e}")
    except Exception as e:
        raise HTTPException(502, f"Could not list {'videos' if source == 'youtube' else 'episodes'}: {e}")

    known = set(req.known_ids) | await asyncio.to_thread(vault.scan_ids, req.scan_roots)
    model, _ = _batch_model(req.options)
    new_items, skipped_duplicates = [], 0
    for it in listing["items"]:
        external_id = it["video_id"] if source == "youtube" else it["apple_episode_id"]
        guid = it.get("episode_guid")
        item = {
            "url": it["url"],
            "external_id": external_id,
            "guid": guid,
            "title": it["title"],
            "duration_seconds": it["duration_seconds"],
            "duration_estimated": it.get("duration_estimated", False),
            "duplicate": False,
            "cost_estimate": round(_estimate_item_cost(it["duration_seconds"], req.options), 4),
        }
        if external_id in known or (guid in known if guid else False):
            skipped_duplicates += 1
        else:
            new_items.append(item)
    has_more = len(new_items) > limit
    new_items = new_items[:limit]

    total = sum(i["cost_estimate"] for i in new_items)
    estimate = {
        "model": model,
        "total": round(total, 4),
        "low": round(total * (1 - EST_SPREAD), 4),
        "high": round(total * (1 + EST_SPREAD), 4),
    }
    batch_id = None
    if new_items:
        batch_id = batch_queue.create_batch(
            source, req.url, listing["title"], req.options.model_dump(),
            {"vault_root": req.vault_root, "folders": req.folders.model_dump(), "scan_roots": req.scan_roots},
            estimate, new_items,
        )
    return {
        "batch_id": batch_id,
        "source": source,
        "title": listing["title"],
        "items": new_items,
        "estimate": estimate,
        "has_more": has_more,
        "skipped_duplicates": skipped_duplicates,
        "searched": len(listing["items"]),
        "window_exhausted": listing["has_more"] and len(new_items) < limit,
        "capped": listing.get("capped", False) or (req.count or 0) > BATCH_MAX_COUNT,
        "count_requested": req.count,
        "expires_in_seconds": batch_queue.CONFIRM_TIMEOUT_SECONDS,
    }


@app.post("/batch/{batch_id}/confirm")
async def batch_confirm(batch_id: str):
    if not batch_queue.confirm(batch_id):
        raise HTTPException(409, "Batch not found, already started, or expired — preview it again.")
    return batch_queue.get_batch(batch_id)


@app.post("/batch/{batch_id}/cancel")
async def batch_cancel(batch_id: str):
    if not batch_queue.cancel(batch_id):
        raise HTTPException(409, "Batch not found or already finished.")
    return batch_queue.get_batch(batch_id)


@app.post("/batch/{batch_id}/retry")
async def batch_retry(batch_id: str):
    return {"retried": batch_queue.retry_failed(batch_id)}


@app.post("/batch/lanes/{lane}/resume")
async def batch_lane_resume(lane: str):
    """Lift a block early (e.g. after changing networks)."""
    if lane not in batch_queue.LANES:
        raise HTTPException(404, "Unknown lane.")
    batch_queue.clear_block(lane)
    return batch_queue.lane_status()


@app.get("/batch")
async def batch_list(limit: int = 10):
    return {"batches": batch_queue.list_batches(limit), "lanes": batch_queue.lane_status(), "now": time.time()}


@app.get("/batch/{batch_id}")
async def batch_get(batch_id: str):
    batch = batch_queue.get_batch(batch_id)
    if batch is None:
        raise HTTPException(404, "Batch not found.")
    return batch


def _run_batch_pipeline(request: TranscriptRequest, source: str, item_id: int) -> dict:
    pipeline = _run_youtube_pipeline if source == "youtube" else _run_podcast_pipeline
    cost = 0.0
    last_progress = 0.0
    for raw in pipeline(request, 4):
        event = json.loads(raw)
        stage = event["stage"]
        if stage == "claude_done":
            cost += event.get("cost_usd") or 0
        elif stage == "error":
            return {
                "state": "skipped" if event.get("skipped") else "failed",
                "error": event["message"],
                "blocked": event.get("blocked", False),
                "cost_usd": round(cost, 4) or None,
            }
        elif stage == "done":
            return {"state": "done", "done": event, "cost_usd": round(cost, 4)}
        if event.get("message") and time.time() - last_progress >= 1:
            batch_queue.set_progress(item_id, event["message"])
            last_progress = time.time()
    return {"state": "failed", "error": "Pipeline ended without a result."}


def _process_batch_item(item: dict, batch: dict) -> dict:
    """Runs in the queue worker's thread. Writes the finished note straight into the vault."""
    cfg = batch["vault"]
    ids = {item["external_id"], item["guid"]} - {None, ""}
    # Re-check right before the work: the same item may have been imported manually meanwhile
    if ids & vault.scan_ids(cfg["scan_roots"]):
        return {"state": "duplicate", "error": "Already in the vault.", "network": False}

    source = item["source"]
    request = TranscriptRequest(
        url=item["url"],
        **batch["options"],
        skip_live=True,
        episode_guid=item["guid"],
        sleep_requests=BATCH_YOUTUBE_SLEEP_REQUESTS if source == "youtube" else 0,
    )
    result = _run_batch_pipeline(request, source, item["id"])
    if result.get("blocked") and source == "youtube":
        # Guest session first; account cookies only as a fallback after a bot check
        batch_queue.set_progress(item["id"], "Blocked as guest — retrying once with browser cookies…")
        result = _run_batch_pipeline(request.model_copy(update={"cookie_browser": "safari"}), source, item["id"])
    if result["state"] != "done":
        return result

    done = result.pop("done")
    folders = cfg["folders"]
    result["note_path"] = vault.write_note(folders[source], done["filename"], done["content"])
    names = [r.get("name", "") for r in done.get("resources") or []]
    if names:
        vault.create_resource_stubs(folders["resources"], names)
    return result


# ─── Cookies & health ────────────────────────────────────────────────────────

@app.post("/cookies")
async def upload_cookies(payload: CookieUpload):
    save_cookies(payload.content)
    return {"status": "ok", "message": "Cookie file saved."}


@app.delete("/cookies")
async def remove_cookies():
    delete_cookies()
    return {"status": "ok", "message": "Cookie file removed."}


@app.get("/cookies")
async def cookies_status():
    return {"has_cookies": has_cookies()}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/whisper/status")
async def whisper_status():
    """Tell the frontend whether the local whisper-server is running."""
    return {
        "available": whisper_mod.is_available(DEFAULT_SERVER_URL),
        "server_url": DEFAULT_SERVER_URL,
        "last_activity": whisper_mod.get_last_activity(),
        "in_flight": whisper_mod.is_in_flight(),
        "idle_timeout_seconds": WHISPER_IDLE_TIMEOUT_SECONDS,
    }


@app.post("/whisper/start")
async def whisper_start():
    """Start the whisper-server LaunchAgent. Idempotent."""
    started = await asyncio.to_thread(whisper_mod.start_server)
    return {"available": started, "server_url": DEFAULT_SERVER_URL}


@app.post("/whisper/stop")
async def whisper_stop():
    """Stop the whisper-server LaunchAgent. Idempotent."""
    if whisper_mod.is_in_flight():
        return {"available": True, "stopped": False, "reason": "transcription in progress"}
    await asyncio.to_thread(whisper_mod.stop_server)
    return {"available": False, "stopped": True}
