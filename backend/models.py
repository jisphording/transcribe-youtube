from pydantic import BaseModel


class TranscriptRequest(BaseModel):
    url: str
    cookie_browser: str | None = None
    cookie_file: str | None = None
    extended_summary: bool = False
    model: str = "claude-sonnet-4-6"


class CookieUpload(BaseModel):
    content: str
