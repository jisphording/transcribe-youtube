import json
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from models import TranscriptRequest, CookieUpload
from youtube import extract_video_id, get_video_metadata, get_transcript, transcript_to_plain_text
from note import build_obsidian_note
from claude import stream_claude, parse_claude_response, VALID_MODELS, DEFAULT_MODEL
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


@app.post("/process")
async def process_video(request: TranscriptRequest):
    try:
        video_id = extract_video_id(request.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    cookie_browser = request.cookie_browser
    cookie_file = request.cookie_file

    def event_generator():
        try:
            # Stage 1: Metadata
            yield _sse_event("metadata", "Fetching video metadata...")
            try:
                metadata = get_video_metadata(request.url, cookie_browser, cookie_file)
            except Exception as e:
                yield _sse_event("error", f"Could not fetch video metadata: {e}")
                return

            yield _sse_event("metadata_done", f"Got metadata for: {metadata['title']}")

            # Stage 2: Transcript
            yield _sse_event("transcript", "Fetching transcript...")
            try:
                entries, is_multi_speaker = get_transcript(video_id, cookie_browser, cookie_file)
            except HTTPException as e:
                yield _sse_event("error", e.detail)
                return
            except Exception as e:
                yield _sse_event("error", f"Could not fetch transcript: {e}")
                return

            raw_text = transcript_to_plain_text(entries)
            yield _sse_event("transcript_done", f"Got transcript ({len(entries)} segments)")

            # Stage 3: Claude processing
            features = ["base"]
            if request.extended_summary:
                features.append("extended")

            system_prompt = get_system_prompt(features)
            model = request.model if request.model in VALID_MODELS else DEFAULT_MODEL
            stage_label = "claude_extended" if request.extended_summary else "claude"

            if request.extended_summary:
                yield _sse_event("claude", "Claude is processing transcript and writing extended summary...")
            else:
                yield _sse_event("claude", "Claude is processing the transcript...")

            user_message = f"""Video Title: {metadata['title']}
Channel: {metadata['channel']}
Video duration: {metadata['duration']}
Description excerpt: {metadata['description']}
Multi-speaker detected: {is_multi_speaker}

--- RAW TRANSCRIPT ---
{raw_text}
--- END TRANSCRIPT ---

Please process this transcript according to the instructions."""

            progress_events = []

            def on_progress(chars_generated: int):
                progress_events.append(
                    _sse_event(stage_label, f"Claude is writing... ({chars_generated} chars generated)")
                )

            try:
                raw_response = stream_claude(model, system_prompt, user_message, on_progress)
            except Exception as e:
                yield _sse_event("error", f"Claude API error: {e}")
                return

            # Yield accumulated progress events
            for evt in progress_events:
                yield evt

            try:
                result = parse_claude_response(raw_response)
            except Exception as e:
                yield _sse_event("error", f"Claude returned invalid JSON: {e}")
                return

            summary = result.get("summary", "")
            extended_summary = result.get("extended_summary", "") if request.extended_summary else ""
            transcript_md = result.get("transcript", "")

            # Stage 4: Build note
            yield _sse_event("building", "Building Obsidian note...")
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
