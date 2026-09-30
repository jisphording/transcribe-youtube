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
import local_llm
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


_LOCAL_CHUNK_SYSTEM = """You are a transcript editor.
You will receive one segment of a raw transcript. Return a JSON object with these keys:
1. "heading": Short title (3-6 words) for the segment's topic.
2. "transcript": The segment with filler words removed ("um", "uh", "like", "you know", "I mean", etc.). Keep the original wording, keep paragraph breaks, add nothing.
3. "notes": 2-5 terse bullet sentences (one string each, in an array) with the segment's key points.
{resources_key}{extended_key}{focus_key}
Return ONLY the JSON object."""

_LOCAL_NOTES_SYSTEM = """You are a note taker.
You will receive one segment of a raw transcript. Return a JSON object with these keys:
1. "heading": Short title (3-6 words) for the segment's topic.
2. "notes": 2-5 terse bullet sentences (one string each, in an array) with the segment's key points.
{resources_key}{extended_key}{focus_key}
Return ONLY the JSON object."""

_LOCAL_RESOURCES_KEY = '{n}. "resources": Array of objects with "name" and "type" for every product, software, website, service, tool or platform named in the segment. [] if none.'
_LOCAL_EXTENDED_KEY = '{n}. "extended": 1-2 sentences explaining the segment\'s theme and key context (for topic-by-topic summary).'
_LOCAL_FOCUS_KEY = '{n}. "focus_notes": 1-3 terse bullet sentences if the segment relates to the focus topic, otherwise empty array.'
_LOCAL_OVERHEAD_CHARS = 600
_LOCAL_SPLIT_DEPTH = 2


def _local_chunk_system(notes_only: bool, extract_resources: bool, include_extended: bool = False, include_focus: bool = False) -> str:
    template = _LOCAL_NOTES_SYSTEM if notes_only else _LOCAL_CHUNK_SYSTEM
    n = 3 if notes_only else 4
    resources_str = _LOCAL_RESOURCES_KEY.format(n=n) if extract_resources else ""
    n += 1 if extract_resources else 0
    extended_str = _LOCAL_EXTENDED_KEY.format(n=n) if include_extended else ""
    n += 1 if include_extended else 0
    focus_str = _LOCAL_FOCUS_KEY.format(n=n) if include_focus else ""
    return template.format(
        resources_key="\n" + resources_str if resources_str else "",
        extended_key="\n" + extended_str if extended_str else "",
        focus_key="\n" + focus_str if focus_str else "",
    )


_LOCAL_REDUCE_SYSTEM = """You are a note condenser.
You will receive consecutive segment notes of a {kind}. Merge them into fewer, shorter notes that keep every distinct key point, name and number.
Return a JSON object with one key: "notes": an array of terse bullet sentences (one string each), about half as many as the input.
Return ONLY the JSON object."""

_LOCAL_REDUCE_EXTENDED_SYSTEM = """You are a summary editor.
You will receive consecutive topic-by-topic sections from a {kind}. Merge the thematic context into a shorter editorial narrative (1-2 paragraphs) that keeps every theme and distinct idea.
Return a JSON object with one key: "extended": the merged narrative.
Return ONLY the JSON object."""

_LOCAL_REDUCE_FOCUS_SYSTEM = """You are a note condenser (focus mode).
You will receive consecutive focus-relevant notes from a {kind}. Merge them into fewer, shorter notes that keep every distinct detail about the focus topic.
Return a JSON object with one key: "notes": an array of terse bullet sentences (one string each), about half as many as the input.
Return ONLY the JSON object."""

_LOCAL_REDUCE_RATIO = 0.5
_LOCAL_REDUCE_MAX_ROUNDS = 6


def _group_units(units: list[str], group_chars: int) -> list[list[str]]:
    groups: list[list[str]] = []
    size = 0
    for unit in units:
        if groups and size + len(unit) + 2 <= group_chars:
            groups[-1].append(unit)
            size += len(unit) + 2
        else:
            groups.append([unit])
            size = len(unit)
    return groups


def _local_reduce_notes(
    model: str, source: str, title: str, units: list[str], final_chars: int,
    stats: dict, stage_label: str, total_steps: int,
):
    """Condense segment notes until they fit final_chars. Yields SSE events, returns the joined notes."""
    kind = "podcast episode" if source == "podcast" else "YouTube video"
    system = _LOCAL_REDUCE_SYSTEM.format(kind=kind)
    group_chars = local_llm.input_budget_chars(model, len(system) + _LOCAL_OVERHEAD_CHARS, _LOCAL_REDUCE_RATIO)
    for round_no in range(1, _LOCAL_REDUCE_MAX_ROUNDS + 1):
        joined = "\n\n".join(units)
        if len(joined) <= final_chars:
            return joined
        pieces = [p for u in units for p in _split_transcript(u, group_chars)]
        groups = _group_units(pieces, group_chars)
        yield _sse_event(
            stage_label,
            f"Step 3/{total_steps} — Condensing notes — round {round_no}, {len(groups)} groups…",
            step=3, total_steps=total_steps,
        )
        reduced: list[str] = []
        for i, group in enumerate(groups):
            user = (
                f"{_title_label(source)}: {title} (notes group {i + 1}/{len(groups)})\n\n"
                "--- NOTES ---\n" + "\n\n".join(group) + "\n--- END NOTES ---"
            )
            raw = None
            try:
                for update in local_llm.stream_local(model, system, user):
                    if update["type"] == "done":
                        raw = update["response"]
                        stats["in"] += update["input_tokens"]
                        stats["out"] += update["output_tokens"]
                notes = parse_claude_response(raw or "").get("notes", [])
            except Exception as e:
                raise RuntimeError(f"note condensing (round {round_no}, group {i + 1}): {e}")
            if isinstance(notes, str):
                notes = [notes]
            lines = [str(n).strip() for n in notes if str(n).strip()]
            if not lines:
                raise RuntimeError(f"note condensing (round {round_no}, group {i + 1}): empty result")
            reduced.append(f"## Part {i + 1}\n" + "\n".join(f"- {n}" for n in lines))
        if sum(map(len, reduced)) >= len(joined):
            raise RuntimeError(f"note condensing (round {round_no}): output did not shrink")
        units = reduced
    joined = "\n\n".join(units)
    if len(joined) > final_chars:
        raise RuntimeError(f"notes still too long after {_LOCAL_REDUCE_MAX_ROUNDS} condensing rounds")
    return joined


def _local_reduce_extended(
    model: str, source: str, title: str, units: list[str], final_chars: int,
    stats: dict, stage_label: str, total_steps: int,
):
    """Condense extended sections until they fit final_chars. Yields SSE events, returns the merged text."""
    kind = "podcast episode" if source == "podcast" else "YouTube video"
    system = _LOCAL_REDUCE_EXTENDED_SYSTEM.format(kind=kind)
    group_chars = local_llm.input_budget_chars(model, len(system) + _LOCAL_OVERHEAD_CHARS, _LOCAL_REDUCE_RATIO)
    for round_no in range(1, _LOCAL_REDUCE_MAX_ROUNDS + 1):
        joined = "\n\n".join(units)
        if len(joined) <= final_chars:
            return joined
        pieces = [p for u in units for p in _split_transcript(u, group_chars)]
        groups = _group_units(pieces, group_chars)
        yield _sse_event(
            stage_label,
            f"Step 3/{total_steps} — Condensing extended sections — round {round_no}, {len(groups)} groups…",
            step=3, total_steps=total_steps,
        )
        reduced: list[str] = []
        for i, group in enumerate(groups):
            user = (
                f"{_title_label(source)}: {title} (extended group {i + 1}/{len(groups)})\n\n"
                "--- SECTIONS ---\n" + "\n\n".join(group) + "\n--- END SECTIONS ---"
            )
            raw = None
            try:
                for update in local_llm.stream_local(model, system, user):
                    if update["type"] == "done":
                        raw = update["response"]
                        stats["in"] += update["input_tokens"]
                        stats["out"] += update["output_tokens"]
                extended = parse_claude_response(raw or "").get("extended", "")
            except Exception as e:
                raise RuntimeError(f"extended condensing (round {round_no}, group {i + 1}): {e}")
            if not str(extended).strip():
                raise RuntimeError(f"extended condensing (round {round_no}, group {i + 1}): empty result")
            reduced.append(str(extended).strip())
        if sum(map(len, reduced)) >= len(joined):
            raise RuntimeError(f"extended condensing (round {round_no}): output did not shrink")
        units = reduced
    joined = "\n\n".join(units)
    if len(joined) > final_chars:
        raise RuntimeError(f"extended still too long after {_LOCAL_REDUCE_MAX_ROUNDS} condensing rounds")
    return joined


def _local_reduce_focus_notes(
    model: str, source: str, title: str, units: list[str], final_chars: int,
    stats: dict, stage_label: str, total_steps: int,
):
    """Condense focus notes until they fit final_chars. Yields SSE events, returns the merged notes."""
    kind = "podcast episode" if source == "podcast" else "YouTube video"
    system = _LOCAL_REDUCE_FOCUS_SYSTEM.format(kind=kind)
    group_chars = local_llm.input_budget_chars(model, len(system) + _LOCAL_OVERHEAD_CHARS, _LOCAL_REDUCE_RATIO)
    for round_no in range(1, _LOCAL_REDUCE_MAX_ROUNDS + 1):
        joined = "\n\n".join(units)
        if len(joined) <= final_chars:
            return joined
        pieces = [p for u in units for p in _split_transcript(u, group_chars)]
        groups = _group_units(pieces, group_chars)
        yield _sse_event(
            stage_label,
            f"Step 3/{total_steps} — Condensing focus notes — round {round_no}, {len(groups)} groups…",
            step=3, total_steps=total_steps,
        )
        reduced: list[str] = []
        for i, group in enumerate(groups):
            user = (
                f"{_title_label(source)}: {title} (focus notes group {i + 1}/{len(groups)})\n\n"
                "--- NOTES ---\n" + "\n\n".join(group) + "\n--- END NOTES ---"
            )
            raw = None
            try:
                for update in local_llm.stream_local(model, system, user):
                    if update["type"] == "done":
                        raw = update["response"]
                        stats["in"] += update["input_tokens"]
                        stats["out"] += update["output_tokens"]
                notes = parse_claude_response(raw or "").get("notes", [])
            except Exception as e:
                raise RuntimeError(f"focus condensing (round {round_no}, group {i + 1}): {e}")
            if isinstance(notes, str):
                notes = [notes]
            lines = [str(n).strip() for n in notes if str(n).strip()]
            if not lines:
                raise RuntimeError(f"focus condensing (round {round_no}, group {i + 1}): empty result")
            reduced.append(f"## Part {i + 1}\n" + "\n".join(f"- {n}" for n in lines))
        if sum(map(len, reduced)) >= len(joined):
            raise RuntimeError(f"focus condensing (round {round_no}): output did not shrink")
        units = reduced
    joined = "\n\n".join(units)
    if len(joined) > final_chars:
        raise RuntimeError(f"focus notes still too long after {_LOCAL_REDUCE_MAX_ROUNDS} condensing rounds")
    return joined


def _chunk_plan(
    model: str, source: str, use_extended_model: bool, transcript_chars: int,
    system_chars: int, chunk_system_chars: int, notes_only: bool,
) -> tuple[bool, int]:
    """Decide whether to chunk and how many transcript chars go into one chunk."""
    if source == "web":
        return False, 0
    if use_extended_model and not local_llm.is_local(model):
        return False, 0
    if not local_llm.is_local(model):
        return model == HAIKU and transcript_chars > _CHUNK_THRESHOLD_CHARS, _CHUNK_CHARS
    ctx = local_llm.context_length(model) or local_llm.DEFAULT_CONTEXT
    per_token = local_llm.CHARS_PER_TOKEN_SAFE
    # single-shot always returns the full transcript, so expected output ≈ input
    in_tokens = (system_chars + transcript_chars + _LOCAL_OVERHEAD_CHARS) / per_token
    out_tokens = transcript_chars / per_token + 500
    if in_tokens + out_tokens <= ctx - local_llm.CONTEXT_MARGIN_TOKENS and out_tokens <= local_llm.MAX_OUTPUT_TOKENS:
        return False, 0
    return True, local_llm.input_budget_chars(
        model, chunk_system_chars + _LOCAL_OVERHEAD_CHARS, 0.15 if notes_only else 1.0,
    )


def _local_chunk(
    model: str, system: str, source: str, title: str, label: str, chunk: str,
    depth: int, stats: dict, stage_label: str, total_steps: int, focus_topic: str = "",
):
    """Process one chunk on a local model; splits it in two when the answer hits the output limit.

    Yields SSE events and returns a list of section dicts (heading, transcript, notes, resources, extended, focus_notes).
    """
    user = (
        f"{_title_label(source)}: {title} (segment {label})\n\n"
        f"--- TRANSCRIPT SEGMENT ---\n{chunk}\n--- END SEGMENT ---"
    )
    if focus_topic:
        user += f"\nFocus topic: {focus_topic}"
    raw = None
    try:
        for update in local_llm.stream_local(model, system, user):
            if update["type"] == "done":
                raw = update["response"]
                stats["in"] += update["input_tokens"]
                stats["out"] += update["output_tokens"]
    except local_llm.LocalOutputLimitError:
        if depth >= _LOCAL_SPLIT_DEPTH:
            raise RuntimeError(f"chunk {label} is still too dense after splitting it twice")
        halves = _split_transcript(chunk, len(chunk) // 2 + 1)
        yield _sse_event(
            stage_label,
            f"Step 3/{total_steps} — Chunk {label} too dense — split in {'two' if len(halves) == 2 else len(halves)}…",
            step=3, total_steps=total_steps,
        )
        sections: list[dict] = []
        for j, half in enumerate(halves):
            sections += yield from _local_chunk(
                model, system, source, title, f"{label}{chr(97 + j)}", half,
                depth + 1, stats, stage_label, total_steps, focus_topic,
            )
        return sections
    if raw is None:
        raise RuntimeError(f"no response for chunk {label}")
    try:
        result = parse_claude_response(raw)
    except Exception as e:
        raise RuntimeError(f"invalid JSON on chunk {label}: {e}")
    notes = result.get("notes", [])
    if isinstance(notes, str):
        notes = [notes]
    focus_notes = result.get("focus_notes", [])
    if isinstance(focus_notes, str):
        focus_notes = [focus_notes]
    return [{
        "heading": result.get("heading") or f"Part {label}",
        "transcript": result.get("transcript", ""),
        "notes": [str(n).strip() for n in notes if str(n).strip()],
        "extended": result.get("extended", ""),
        "focus_notes": [str(n).strip() for n in focus_notes if str(n).strip()],
        "resources": result.get("resources", []) or [],
    }]


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


def _resolve_llm(model: str | None, default: str) -> str:
    return model if local_llm.is_local(model) else resolve_model(model, default)


def _llm_label(model: str) -> str:
    if local_llm.is_local(model):
        return local_llm.model_id(model)
    return f"Claude {model.split('-')[1].capitalize()}"


def _stream_llm(model: str, system_prompt: str, user_message: str, effort: str | None):
    if local_llm.is_local(model):
        return local_llm.stream_local(model, system_prompt, user_message)
    return stream_claude(model, system_prompt, user_message, effort)


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    if local_llm.is_local(model):
        return 0.0
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
        model = _resolve_llm(request.extended_model, DEFAULT_EXTENDED_MODEL)
    else:
        model = _resolve_llm(request.model, DEFAULT_MODEL)
    effort = "medium" if use_extended_model else "low"
    if request.focus_topic:
        stage_label = "claude_focus"
    elif request.extended_summary:
        stage_label = "claude_extended"
    else:
        stage_label = "claude"

    is_local = local_llm.is_local(model)
    notes_only = is_local and not request.include_transcript
    include_extended_in_chunks = is_local and (request.extended_summary or request.focus_include_extended)
    include_focus_in_chunks = is_local and bool(request.focus_topic)
    local_system = _local_chunk_system(
        notes_only, request.extract_resources, include_extended_in_chunks, include_focus_in_chunks
    )
    use_chunks, chunk_chars = _chunk_plan(
        model, source, use_extended_model, transcript_chars,
        len(get_system_prompt(features, source=source)) if is_local else 0,
        len(local_system), notes_only,
    )

    if use_chunks:
        chunks = _split_transcript(raw_text, chunk_chars)
        n_chunks = len(chunks)
        if is_local:
            ctx = local_llm.context_length(model) or local_llm.DEFAULT_CONTEXT
            size_label = f"{_llm_label(model)} ({ctx // 1024}k context)"
            mode_label = " (notes only)" if notes_only else ""
        else:
            size_label, mode_label = _llm_label(model), ""
        yield _sse_event(
            stage_label,
            f"Step 3/{total_steps} — Transcript split into {n_chunks} chunks for {size_label}{mode_label}…",
            step=3, total_steps=total_steps,
        )

        cleaned_sections: list[str] = []
        total_in_tokens = 0
        total_out_tokens = 0
        chunk_start_time = time.time()

        local_sections: list[dict] = []
        local_stats = {"in": 0, "out": 0}

        for i, chunk in enumerate(chunks):
            yield _sse_event(
                stage_label,
                f"Step 3/{total_steps} — {'Processing' if is_local else 'Cleaning'} chunk {i + 1}/{n_chunks}…",
                step=3, total_steps=total_steps,
            )
            if is_local:
                try:
                    local_sections += yield from _local_chunk(
                        model, local_system, source, metadata["title"], str(i + 1), chunk,
                        0, local_stats, stage_label, total_steps, request.focus_topic or "",
                    )
                except Exception as e:
                    yield _sse_event("error", f"{_llm_label(model)} error on {e}")
                    return
                continue
            chunk_user = (
                f"{_title_label(source)}: {metadata['title']} (segment {i + 1}/{n_chunks})\n\n"
                f"--- TRANSCRIPT SEGMENT ---\n{chunk}\n--- END SEGMENT ---"
            )
            raw_chunk = None
            try:
                for update in _stream_llm(model, _CHUNK_CLEAN_SYSTEM, chunk_user, effort):
                    if update["type"] == "done":
                        if update["stop_reason"] == "refusal":
                            yield _sse_event("error", f"{REFUSAL_MESSAGE} (chunk {i + 1})")
                            return
                        raw_chunk = update["response"]
                        total_in_tokens += update["input_tokens"]
                        total_out_tokens += update["output_tokens"]
            except Exception as e:
                yield _sse_event("error", f"{_llm_label(model)} error on chunk {i + 1}: {e}")
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
                yield _sse_event("error", f"Invalid JSON from {_llm_label(model)} on chunk {i + 1}: {e}")
                return

        yield _sse_event(
            stage_label,
            f"Step 3/{total_steps} — Generating summary…",
            step=3, total_steps=total_steps,
        )
        source_name_label = "Channel" if source == "youtube" else "Show"
        source_name = metadata.get("channel") if source == "youtube" else metadata.get("show", "")
        if is_local:
            total_in_tokens += local_stats["in"]
            total_out_tokens += local_stats["out"]
            combined_cleaned = "\n\n".join(
                f"## {s['heading']}\n\n{s['transcript']}" for s in local_sections
            ) if not notes_only else ""
            chunk_summary_system = _build_chunk_summary_system(False, source)
            units = [
                f"## {s['heading']}\n" + "\n".join(f"- {n}" for n in s["notes"]) for s in local_sections
            ]
            budget = local_llm.input_budget_chars(model, len(chunk_summary_system) + _LOCAL_OVERHEAD_CHARS, 0.15)
            try:
                notes_text = yield from _local_reduce_notes(
                    model, source, metadata["title"], units, budget, local_stats, stage_label, total_steps,
                )
            except Exception as e:
                yield _sse_event("error", f"{_llm_label(model)} error on {e}")
                return
            total_in_tokens, total_out_tokens = local_stats["in"], local_stats["out"]
            summary_body = f"Notes by section:\n{notes_text}"
        else:
            combined_cleaned = "\n\n".join(cleaned_sections)
            chunk_summary_system = _build_chunk_summary_system(request.extract_resources, source)
            summary_body = f"Transcript:\n{combined_cleaned[:40_000]}"
        summary_user = (
            f"Title: {metadata['title']}\n{source_name_label}: {source_name}\n\n{summary_body}"
        )
        summary_raw = None
        try:
            for update in _stream_llm(model, chunk_summary_system, summary_user, effort):
                if update["type"] == "done":
                    if update["stop_reason"] == "refusal":
                        yield _sse_event("error", REFUSAL_MESSAGE)
                        return
                    summary_raw = update["response"]
                    total_in_tokens += update["input_tokens"]
                    total_out_tokens += update["output_tokens"]
        except Exception as e:
            yield _sse_event("error", f"{_llm_label(model)} error on summary: {e}")
            return

        elapsed_total = time.time() - chunk_start_time
        cost = _estimate_cost(model, total_in_tokens, total_out_tokens)
        yield _sse_event(
            "claude_done",
            f"Step 3/{total_steps} — {_llm_label(model)} done! {_fmt_tokens(total_in_tokens)} in → {_fmt_tokens(total_out_tokens)} out ({_fmt_elapsed(elapsed_total)}, {total_out_tokens / max(elapsed_total, 0.1):.0f} tok/s)",
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
        if is_local:
            seen: set[str] = set()
            resources = []
            for sec in local_sections:
                for r in sec["resources"] if request.extract_resources else []:
                    name = r.get("name", "").strip() if isinstance(r, dict) else ""
                    if name and name.lower() not in seen:
                        seen.add(name.lower())
                        resources.append(r)
            extended_summary = ""
            focused_summary = ""
            if include_extended_in_chunks:
                extended_units = [
                    f"## {s['heading']}\n\n{s['extended']}" for s in local_sections if s.get("extended", "").strip()
                ]
                if extended_units:
                    budget = local_llm.input_budget_chars(model, len(_LOCAL_REDUCE_EXTENDED_SYSTEM) + _LOCAL_OVERHEAD_CHARS, 0.15)
                    try:
                        before_in, before_out = local_stats["in"], local_stats["out"]
                        extended_summary = yield from _local_reduce_extended(
                            model, source, metadata["title"], extended_units, budget, local_stats, stage_label, total_steps,
                        )
                        total_in_tokens += local_stats["in"] - before_in
                        total_out_tokens += local_stats["out"] - before_out
                    except Exception as e:
                        yield _sse_event("error", f"{_llm_label(model)} error on extended: {e}")
                        return
            if include_focus_in_chunks:
                focus_units = [
                    f"## {s['heading']}\n" + "\n".join(f"- {n}" for n in s.get("focus_notes", []))
                    for s in local_sections if s.get("focus_notes", [])
                ]
                if focus_units:
                    budget = local_llm.input_budget_chars(model, len(_LOCAL_REDUCE_FOCUS_SYSTEM) + _LOCAL_OVERHEAD_CHARS, 0.15)
                    try:
                        before_in, before_out = local_stats["in"], local_stats["out"]
                        focused_summary = yield from _local_reduce_focus_notes(
                            model, source, metadata["title"], focus_units, budget, local_stats, stage_label, total_steps,
                        )
                        total_in_tokens += local_stats["in"] - before_in
                        total_out_tokens += local_stats["out"] - before_out
                    except Exception as e:
                        yield _sse_event("error", f"{_llm_label(model)} error on focus: {e}")
                        return
        else:
            resources = summary_result.get("resources", []) if request.extract_resources else []
            extended_summary = ""
            focused_summary = ""
        transcript_md = combined_cleaned
        article_info = None

    else:
        system_prompt = get_system_prompt(features, source=source)

        if source == "web":
            task_desc = "summary + extended summary" if request.extended_summary else "summary"
        else:
            task_desc = "summary + extended summary + transcript" if request.extended_summary else "summary + transcript"
        yield _sse_event(
            stage_label,
            f"Step 3/{total_steps} — Sending to {_llm_label(model)}… Processing {task_desc}",
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
            for update in _stream_llm(model, system_prompt, user_message, effort):
                if update["type"] == "progress":
                    elapsed = _fmt_elapsed(update["elapsed"])
                    in_tok = _fmt_tokens(update["input_tokens"])
                    out_tok = _fmt_tokens(update["output_tokens"])
                    if update["phase"] == "starting":
                        msg = f"Step 3/{total_steps} — {_llm_label(model)} received {in_tok} input tokens, generating…"
                    else:
                        msg = f"Step 3/{total_steps} — {_llm_label(model)} generating… {out_tok} output tokens ({elapsed})"
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
                        f"Step 3/{total_steps} — {_llm_label(model)} done! {in_tok} in → {out_tok} out ({elapsed}, {update['output_tokens'] / max(update['elapsed'], 0.1):.0f} tok/s)",
                        step=3, total_steps=total_steps,
                        input_tokens=update["input_tokens"],
                        output_tokens=update["output_tokens"],
                        elapsed=round(update["elapsed"], 1),
                        cost_usd=round(cost, 4),
                    )
        except Exception as e:
            yield _sse_event("error", f"{_llm_label(model)} error: {e}")
            return

        if raw_response is None:
            yield _sse_event("error", "Claude stream ended without a response.")
            return

        try:
            result = parse_claude_response(raw_response)
        except Exception as e:
            yield _sse_event("error", f"{_llm_label(model)} returned invalid JSON: {e}")
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
        return _resolve_llm(opts.extended_model, DEFAULT_EXTENDED_MODEL), True
    return _resolve_llm(opts.model, DEFAULT_MODEL), False


def _estimate_item_cost(duration_s: int, opts: BatchOptions) -> float:
    model, use_extended = _batch_model(opts)
    minutes = max(duration_s, 60) / 60
    transcript = minutes * EST_TOKENS_PER_MINUTE
    inp = transcript + EST_SYSTEM_TOKENS
    out = EST_SUMMARY_OUTPUT_TOKENS * (2 if use_extended else 1)
    if opts.include_transcript:
        out += transcript
    chars = minutes * EST_CHARS_PER_MINUTE
    if local_llm.is_local(model):
        return 0.0
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


@app.get("/local-models")
async def local_models():
    return {"url": local_llm.LOCAL_LLM_URL, **await asyncio.to_thread(local_llm.list_models)}


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
