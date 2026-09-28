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
    model: str = "claude-haiku-4-5"
    extended_model: str = "claude-sonnet-5"
    # Podcast-only options
    whisper_language: str | None = None  # None / "auto" / ISO 639-1 like "en", "de"
    # Web-only: page text pasted by the user when the site blocks automated access
    manual_text: str | None = None
    # Batch queue only
    skip_live: bool = False             # skip livestreams / premieres (YouTube)
    sleep_requests: float = 0           # yt-dlp pause between HTTP requests
    episode_guid: str | None = None     # podcast: match the RSS entry by guid instead of slug


class CookieUpload(BaseModel):
    content: str


class BatchOptions(BaseModel):
    """Snapshot of the single-import options, applied to every item of a batch."""
    extended_summary: bool = False
    focus_topic: str | None = None
    focus_include_extended: bool = False
    include_transcript: bool = True
    extract_resources: bool = False
    model: str = "claude-haiku-4-5"
    extended_model: str = "claude-sonnet-5"
    whisper_language: str | None = None


class BatchFolders(BaseModel):
    youtube: str
    podcast: str
    resources: str


class BatchRequest(BaseModel):
    url: str
    count: int | None = None            # None = all, as long as there are ≤ 20
    min_minutes: int = 15
    options: BatchOptions = BatchOptions()
    vault_root: str                     # absolute path of the vault
    folders: BatchFolders               # absolute paths, resolved by the plugin
    scan_roots: list[str]               # absolute paths searched for existing notes
    known_ids: list[str] = []           # ids the plugin already found via metadataCache
