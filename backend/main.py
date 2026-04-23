import json
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from models import TranscriptRequest, CookieUpload
from youtube import extract_video_id, get_video_metadata, get_transcript, transcript_to_plain_text
from note import build_obsidian_note
from claude import stream_claude, parse_claude_response, VALID_MODELS, DEFAULT_MODEL, DEFAULT_EXTENDED_MODEL, MODEL_MAX_OUTPUT_TOKENS
import time
from cookies import has_cookies, save_cookies, delete_cookies
from prompts import get_system_prompt

app = FastAPI(title="YT to Obsidian API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["app://obsidian.md", "http://localhost", "*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# Chunked transcript processing (used when transcript exceeds Haiku's output limit)
# Haiku max output ≈ 8192 tokens ≈ 32k chars; we trigger chunking well before that.
_CHUNK_CHARS = 20_000        # target chars per chunk (input)
_CHUNK_THRESHOLD_CHARS = 25_000  # trigger chunking above this raw transcript size

_CHUNK_CLEAN_SYSTEM = """You are a transcript editor.
You will receive a segment of a raw YouTube video transcript. Clean it and return a JSON object with exactly 2 keys:
1. "heading": A short, descriptive title (3-6 words) for this segment's topic.
2. "transcript": The cleaned transcript text for this segment.

Rules for "transcript":
- Remove ALL filler words: "um", "uh", "like", "you know", "sort of", "kind of", "basically", "literally", "actually", "right", "okay so", "so yeah", "I mean", etc.
- Keep content close to original wording — do not paraphrase or rewrite sentences.
- Preserve paragraph breaks for readability. Group related sentences together.
- Do NOT add any content that wasn't in the original.

Return ONLY the JSON object, no other text."""

_CHUNK_SUMMARY_SYSTEM_BASE = """You are a transcript summarizer.
You will receive the cleaned transcript of a YouTube video along with its title and channel.
Return a JSON object with exactly {key_count} keys:
1. "summary": A concise 3-5 sentence summary of the video's main content and key takeaways.
2. "topics": A JSON array of 5-10 short tags (1-3 words each) describing the main topics discussed. Lowercase, use hyphens for multi-word tags (e.g. "machine-learning"). Focus on core subject matter — concepts, domains, technologies, people.
{resources_key}
Return ONLY the JSON object, no other text."""


def _build_chunk_summary_system(extract_resources: bool) -> str:
    if extract_resources:
        resources_key = '3. "resources": A JSON array of objects (each with "name" and "type") for every product, software, website, service, tool, or platform mentioned by name. Deduplicate. Return [] if none.'
        return _CHUNK_SUMMARY_SYSTEM_BASE.format(key_count=3, resources_key=resources_key)
    return _CHUNK_SUMMARY_SYSTEM_BASE.format(key_count=2, resources_key="")


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
        # Prefer paragraph break
        para = text.rfind("\n\n", start + chunk_size // 2, end)
        if para != -1:
            end = para
        else:
            # Fall back to sentence boundary
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
    "claude-opus-4-6":           {"input": 5.00, "output": 25.00},
}


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = MODEL_PRICING.get(model, MODEL_PRICING[DEFAULT_MODEL])
    return (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000


@app.post("/process")
async def process_video(request: TranscriptRequest):
    try:
        video_id = extract_video_id(request.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    cookie_browser = request.cookie_browser
    cookie_file = request.cookie_file

    def _fmt_tokens(n: int) -> str:
        if n >= 1000:
            return f"{n / 1000:.1f}k"
        return str(n)

    def _fmt_elapsed(seconds: float) -> str:
        m, s = divmod(int(seconds), 60)
        if m > 0:
            return f"{m}m {s}s"
        return f"{s}s"

    total_steps = 4

    def event_generator():
        try:
            # Step 1: Metadata
            yield _sse_event("metadata", "Step 1/{total} — Fetching video metadata…".format(total=total_steps),
                             step=1, total_steps=total_steps)
            try:
                metadata = get_video_metadata(request.url, cookie_browser, cookie_file)
            except Exception as e:
                yield _sse_event("error", f"Could not fetch video metadata: {e}")
                return

            yield _sse_event("metadata_done",
                             "Step 1/{total} — Got metadata: {title} ({dur})".format(
                                 total=total_steps, title=metadata['title'], dur=metadata.get('duration', '?')),
                             step=1, total_steps=total_steps)

            # Step 2: Transcript
            yield _sse_event("transcript", "Step 2/{total} — Fetching transcript…".format(total=total_steps),
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
            yield _sse_event("transcript_done",
                             "Step 2/{total} — Got transcript ({segs} segments, ~{chars} chars)".format(
                                 total=total_steps, segs=len(entries), chars=f"{transcript_chars:,}"),
                             step=2, total_steps=total_steps,
                             segments=len(entries), transcript_chars=transcript_chars)

            # Step 3: Claude processing
            features = ["base"]
            if request.extended_summary:
                features.append("extended")
            if request.extract_resources:
                features.append("resources")

            if request.extended_summary:
                model = request.extended_model if request.extended_model in VALID_MODELS else DEFAULT_EXTENDED_MODEL
            else:
                model = request.model if request.model in VALID_MODELS else DEFAULT_MODEL
            stage_label = "claude_extended" if request.extended_summary else "claude"

            use_chunks = (
                not request.extended_summary
                and model == "claude-haiku-4-5-20251001"
                and transcript_chars > _CHUNK_THRESHOLD_CHARS
            )

            if use_chunks:
                # ── Chunked path: clean each segment separately, then summarise ──
                chunks = _split_transcript(raw_text, _CHUNK_CHARS)
                n_chunks = len(chunks)
                yield _sse_event(stage_label,
                                 "Step 3/{total} — Transcript split into {n} chunks for Haiku…".format(
                                     total=total_steps, n=n_chunks),
                                 step=3, total_steps=total_steps)

                cleaned_sections: list[str] = []
                total_in_tokens = 0
                total_out_tokens = 0
                chunk_start_time = time.time()

                for i, chunk in enumerate(chunks):
                    yield _sse_event(stage_label,
                                     "Step 3/{total} — Cleaning chunk {i}/{n}…".format(
                                         total=total_steps, i=i + 1, n=n_chunks),
                                     step=3, total_steps=total_steps)
                    chunk_user = (
                        f"Video: {metadata['title']} (segment {i + 1}/{n_chunks})\n\n"
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

                # Summary pass
                yield _sse_event(stage_label,
                                 "Step 3/{total} — Generating summary…".format(total=total_steps),
                                 step=3, total_steps=total_steps)
                combined_cleaned = "\n\n".join(cleaned_sections)
                summary_user = (
                    f"Title: {metadata['title']}\nChannel: {metadata['channel']}\n\n"
                    f"Transcript:\n{combined_cleaned[:40_000]}"
                )
                summary_raw = None
                chunk_summary_system = _build_chunk_summary_system(request.extract_resources)
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
                yield _sse_event("claude_done",
                                 "Step 3/{total} — Claude done! {in_tok} in → {out_tok} out ({elapsed})".format(
                                     total=total_steps,
                                     in_tok=_fmt_tokens(total_in_tokens),
                                     out_tok=_fmt_tokens(total_out_tokens),
                                     elapsed=_fmt_elapsed(elapsed_total)),
                                 step=3, total_steps=total_steps,
                                 input_tokens=total_in_tokens, output_tokens=total_out_tokens,
                                 elapsed=round(elapsed_total, 1), cost_usd=round(cost, 4))

                try:
                    summary_result = parse_claude_response(summary_raw) if summary_raw else {}
                except Exception:
                    summary_result = {}

                summary = summary_result.get("summary", "")
                topics = summary_result.get("topics", [])
                resources = summary_result.get("resources", []) if request.extract_resources else []
                transcript_md = combined_cleaned
                extended_summary = ""

            else:
                # ── Single-call path ──
                system_prompt = get_system_prompt(features)

                task_desc = "summary + extended summary + transcript" if request.extended_summary else "summary + transcript"
                yield _sse_event(stage_label,
                                 "Step 3/{total} — Sending to Claude ({model})… Processing {task}".format(
                                     total=total_steps, model=model.split("-")[-1].capitalize(), task=task_desc),
                                 step=3, total_steps=total_steps)

                user_message = f"""Video Title: {metadata['title']}
Channel: {metadata['channel']}
Video duration: {metadata['duration']}
Description excerpt: {metadata['description']}
Multi-speaker detected: {is_multi_speaker}

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
                                msg = "Step 3/{total} — Claude received {in_tok} input tokens, generating…".format(
                                    total=total_steps, in_tok=in_tok)
                            else:
                                msg = "Step 3/{total} — Claude generating… {out_tok} output tokens ({elapsed})".format(
                                    total=total_steps, out_tok=out_tok, elapsed=elapsed)
                            yield _sse_event(stage_label, msg,
                                             step=3, total_steps=total_steps,
                                             input_tokens=update["input_tokens"],
                                             output_tokens=update["output_tokens"],
                                             elapsed=round(update["elapsed"], 1))
                        elif update["type"] == "done":
                            raw_response = update["response"]
                            elapsed = _fmt_elapsed(update["elapsed"])
                            in_tok = _fmt_tokens(update["input_tokens"])
                            out_tok = _fmt_tokens(update["output_tokens"])
                            cost = _estimate_cost(model, update["input_tokens"], update["output_tokens"])
                            yield _sse_event("claude_done",
                                             "Step 3/{total} — Claude done! {in_tok} in → {out_tok} out ({elapsed})".format(
                                                 total=total_steps, in_tok=in_tok, out_tok=out_tok, elapsed=elapsed),
                                             step=3, total_steps=total_steps,
                                             input_tokens=update["input_tokens"],
                                             output_tokens=update["output_tokens"],
                                             elapsed=round(update["elapsed"], 1),
                                             cost_usd=round(cost, 4))
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
                extended_summary = result.get("extended_summary", "") if request.extended_summary else ""
                transcript_md = result.get("transcript", "")

            # Step 4: Build note
            yield _sse_event("building",
                             "Step 4/{total} — Building Obsidian note…".format(total=total_steps),
                             step=4, total_steps=total_steps)
            filename, note_content = build_obsidian_note(
                metadata, summary, transcript_md,
                extended_summary=extended_summary,
                include_transcript=request.include_transcript,
                topics=topics,
                resources=resources,
            )

            yield _sse_event("done", "Done!", filename=filename, content=note_content, metadata=metadata, resources=resources)

        except Exception as e:
            yield _sse_event("error", f"Unexpected error: {e}")

    return EventSourceResponse(event_generator())


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
