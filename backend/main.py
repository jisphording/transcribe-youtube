import asyncio
import json
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from models import TranscriptRequest, CookieUpload
from youtube import extract_video_id, get_video_metadata, get_transcript, transcript_to_plain_text
from podcast import is_apple_podcast_url, resolve_episode, get_rss_transcript
import whisper as whisper_mod
from whisper import transcribe_url as whisper_transcribe, WhisperUnavailableError, DEFAULT_SERVER_URL
from note import build_obsidian_note
from claude import stream_claude, parse_claude_response, VALID_MODELS, DEFAULT_MODEL, DEFAULT_EXTENDED_MODEL, MODEL_MAX_OUTPUT_TOKENS
from cookies import has_cookies, save_cookies, delete_cookies
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
    task = asyncio.create_task(_whisper_idle_watcher())
    try:
        yield
    finally:
        task.cancel()
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
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    "claude-sonnet-4-6":         {"input": 3.00, "output": 15.00},
    "claude-opus-4-7":           {"input": 15.00, "output": 75.00},
}


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = MODEL_PRICING.get(model, MODEL_PRICING[DEFAULT_MODEL])
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


@app.post("/process")
async def process_media(request: TranscriptRequest):
    source = "podcast" if is_apple_podcast_url(request.url) else "youtube"
    total_steps = 4

    def event_generator():
        try:
            if source == "youtube":
                yield from _run_youtube_pipeline(request, total_steps)
            else:
                yield from _run_podcast_pipeline(request, total_steps)
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
        metadata = get_video_metadata(request.url, cookie_browser, cookie_file)
    except Exception as e:
        yield _sse_event("error", f"Could not fetch video metadata: {e}")
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
        entries, is_multi_speaker = get_transcript(video_id, cookie_browser, cookie_file)
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

    use_extended_model = request.extended_summary or bool(request.focus_topic) or request.focus_include_extended
    if use_extended_model:
        model = request.extended_model if request.extended_model in VALID_MODELS else DEFAULT_EXTENDED_MODEL
    else:
        model = request.model if request.model in VALID_MODELS else DEFAULT_MODEL
    if request.focus_topic:
        stage_label = "claude_focus"
    elif request.extended_summary:
        stage_label = "claude_extended"
    else:
        stage_label = "claude"

    use_chunks = (
        not use_extended_model
        and model == "claude-haiku-4-5-20251001"
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
                for update in stream_claude(model, _CHUNK_CLEAN_SYSTEM, chunk_user):
                    if update["type"] == "done":
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
            for update in stream_claude(model, chunk_summary_system, summary_user):
                if update["type"] == "done":
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

    else:
        system_prompt = get_system_prompt(features, source=source)

        task_desc = "summary + extended summary + transcript" if request.extended_summary else "summary + transcript"
        yield _sse_event(
            stage_label,
            f"Step 3/{total_steps} — Sending to Claude ({model.split('-')[-1].capitalize()})… Processing {task_desc}",
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
            for update in stream_claude(model, system_prompt, user_message):
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
    )

    yield _sse_event(
        "done", "Done!",
        filename=filename, content=note_content, metadata=metadata,
        resources=resources, source=source,
    )


def _title_label(source: str) -> str:
    return "Episode" if source == "podcast" else "Video"


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
