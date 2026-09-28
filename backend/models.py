from pydantic import BaseModel


class TranscriptRequest(BaseModel):
    url: str
    cookie_browser: str | None = None
    cookie_file: str | None = None
    extended_summary: bool = False
    focus_topic: str | None = None
    focus_include_extended: bool = False
    include_transcript: bool = True
    extract_resources: bool = False
    model: str = "claude-haiku-4-5-20251001"
    extended_model: str = "claude-sonnet-4-6"
    # Podcast-only options
    whisper_language: str | None = None  # None / "auto" / ISO 639-1 like "en", "de"
    # Web-only: page text pasted by the user when the site blocks automated access
    manual_text: str | None = None


class CookieUpload(BaseModel):
    content: str
