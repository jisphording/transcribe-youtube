import json
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from models import TranscriptRequest, CookieUpload
from youtube import extract_video_id, get_video_metadata, get_transcript, transcript_to_plain_text
from note import build_obsidian_note
from claude import stream_claude, parse_claude_response, VALID_MODELS, DEFAULT_MODEL
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


def _sse_event(stage: str, message: str, **extra) -> str:
    data = {"stage": stage, "message": message, **extra}
    return json.dumps(data)


# Pricing per million tokens (USD)
MODEL_PRICING = {
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-opus-4-6":   {"input": 5.00, "output": 25.00},
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

            system_prompt = get_system_prompt(features)
            model = request.model if request.model in VALID_MODELS else DEFAULT_MODEL
            stage_label = "claude_extended" if request.extended_summary else "claude"

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
            extended_summary = result.get("extended_summary", "") if request.extended_summary else ""
            transcript_md = result.get("transcript", "")

            # Step 4: Build note
            yield _sse_event("building",
                             "Step 4/{total} — Building Obsidian note…".format(total=total_steps),
                             step=4, total_steps=total_steps)
            filename, note_content = build_obsidian_note(metadata, summary, transcript_md, extended_summary=extended_summary)

            yield _sse_event("done", "Done!", filename=filename, content=note_content, metadata=metadata)

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
