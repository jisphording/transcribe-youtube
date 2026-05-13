"""Local whisper.cpp HTTP server client.

Talks to `whisper-server` (whisper.cpp's HTTP daemon) via POST /inference.
Accepts a remote audio URL: downloads to a temp file, posts the file,
returns the transcript text.

Also owns the whisper-server LaunchAgent lifecycle: start/stop on demand
plus a last-activity tracker so an idle watcher in `main.py` can shut it
down after a configurable period of inactivity.

Independent module: only `main.py` orchestrates it.
"""

import os
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import httpx


DEFAULT_SERVER_URL = "http://127.0.0.1:2022"
DEFAULT_LANGUAGE = "auto"

USER_AGENT = "Obsidian-MediaTranscriber/1.0"

LAUNCH_AGENT_LABEL = "com.whisper.server"
START_TIMEOUT_SECONDS = 20.0


class WhisperUnavailableError(Exception):
    """Raised when the local whisper-server is not reachable."""


# ─── Lifecycle: start, stop, last-activity tracker ───────────────────────────

_last_activity: float = 0.0
_in_flight: int = 0


def is_available(server_url: str = DEFAULT_SERVER_URL, timeout: float = 2.0) -> bool:
    try:
        res = httpx.get(server_url.rstrip("/") + "/", timeout=timeout)
        # whisper-server returns 200 or 404 on root — both confirm it's alive
        return res.status_code < 500
    except Exception:
        return False


def _service_target() -> str:
    return f"gui/{os.getuid()}/{LAUNCH_AGENT_LABEL}"


def start_server(timeout: float = START_TIMEOUT_SECONDS, on_progress=None) -> bool:
    """Kickstart the LaunchAgent and wait until /‎ responds. Idempotent."""
    if is_available():
        mark_activity()
        return True

    if on_progress:
        on_progress("Starting whisper-server…")

    subprocess.run(
        ["launchctl", "kickstart", _service_target()],
        check=False, capture_output=True,
    )

    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_available():
            mark_activity()
            return True
        time.sleep(0.4)
    return False


def stop_server() -> None:
    """SIGTERM the LaunchAgent. Idempotent — no error if already stopped."""
    subprocess.run(
        ["launchctl", "kill", "SIGTERM", _service_target()],
        check=False, capture_output=True,
    )


def mark_activity() -> None:
    global _last_activity
    _last_activity = time.time()


def get_last_activity() -> float:
    return _last_activity


@contextmanager
def in_flight():
    """Mark that a transcription is in progress so the idle watcher won't kill it."""
    global _in_flight
    _in_flight += 1
    try:
        yield
    finally:
        _in_flight -= 1
        mark_activity()


def is_in_flight() -> bool:
    return _in_flight > 0


def transcribe_url(
    audio_url: str,
    server_url: str = DEFAULT_SERVER_URL,
    language: str = DEFAULT_LANGUAGE,
    include_timestamps: bool = True,
    on_progress=None,
) -> tuple[str, str]:
    """Download audio_url, send to whisper-server, return (full_text, detected_language).

    Lazy-starts the whisper-server LaunchAgent if it isn't already running.
    Raises WhisperUnavailableError if the server can't be brought up.
    """
    with in_flight():
        if not is_available(server_url):
            if not start_server(on_progress=on_progress):
                raise WhisperUnavailableError(
                    f"Local whisper-server failed to start within {START_TIMEOUT_SECONDS:.0f}s.\n\n"
                    "Check /tmp/whisper-server.err, or run:\n"
                    "  ./backend/scripts/whisper-setup.sh"
                )

        if on_progress:
            on_progress("Downloading audio…")

        tmp_path = _download_audio(audio_url, on_progress)
        try:
            if on_progress:
                on_progress("Sending to local whisper-server…")
            return _post_to_whisper(tmp_path, server_url, language, include_timestamps)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _download_audio(audio_url: str, on_progress=None) -> str:
    """Stream-download audio to a tempfile. Returns the temp path."""
    suffix = "." + audio_url.split("?")[0].rsplit(".", 1)[-1] if "." in audio_url.split("?")[0] else ".mp3"
    if len(suffix) > 6 or "/" in suffix:
        suffix = ".mp3"
    fd, tmp_path = tempfile.mkstemp(prefix="podcast_", suffix=suffix)
    os.close(fd)

    total_bytes = 0
    with httpx.stream(
        "GET",
        audio_url,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        timeout=120.0,
    ) as res:
        res.raise_for_status()
        content_length = int(res.headers.get("content-length", 0))
        with open(tmp_path, "wb") as f:
            for chunk in res.iter_bytes(chunk_size=1024 * 256):
                f.write(chunk)
                total_bytes += len(chunk)
                if on_progress and content_length:
                    pct = total_bytes * 100 // content_length
                    if pct % 10 == 0:
                        on_progress(
                            f"Downloading audio… {total_bytes // (1024 * 1024)}/"
                            f"{content_length // (1024 * 1024)} MB"
                        )

    return tmp_path


def _post_to_whisper(
    audio_path: str,
    server_url: str,
    language: str,
    include_timestamps: bool,
) -> tuple[str, str]:
    filename = Path(audio_path).name
    response_format = "verbose_json" if include_timestamps else "json"

    data = {"response_format": response_format}
    if language and language != "auto":
        data["language"] = language

    with open(audio_path, "rb") as f:
        files = {"file": (filename, f, "application/octet-stream")}
        # No HTTP timeout: long episodes can take many minutes on CPU.
        res = httpx.post(
            server_url.rstrip("/") + "/inference",
            data=data,
            files=files,
            timeout=None,
        )

    if res.status_code != 200:
        raise RuntimeError(f"whisper-server error {res.status_code}: {res.text[:300]}")

    payload = res.json()
    detected_language = payload.get("language", language if language != "auto" else "en")
    text = _format_transcript(payload, include_timestamps)
    return text, detected_language


def _format_transcript(payload: dict, include_timestamps: bool) -> str:
    if include_timestamps and isinstance(payload.get("segments"), list) and payload["segments"]:
        out = []
        for seg in payload["segments"]:
            ts = _hms(seg.get("start", 0))
            txt = (seg.get("text") or "").strip()
            if txt:
                out.append(f"[{ts}] {txt}")
        return "\n\n".join(out)
    return (payload.get("text") or "").strip()


def _hms(sec: float) -> str:
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"
