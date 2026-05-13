import re


def slugify(title: str) -> str:
    slug = title.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    slug = slug.strip("-")
    return slug


def _demote_headings(text: str) -> str:
    """Bump ## headings → ### so section content nests under the ## section header."""
    return re.sub(r"^##(?!#)", "###", text, flags=re.MULTILINE)


def _yt_view(metadata: dict) -> dict:
    """Adapt a YouTube metadata dict to the source-agnostic shape used below."""
    return {
        "source": "youtube",
        "title": metadata["title"],
        "source_label": "Channel",
        "source_name": metadata["channel"],
        "source_url": metadata["channel_url"],
        "url": metadata["url"],
        "url_label": "Watch on YouTube",
        "published": metadata.get("upload_date", ""),
        "duration": metadata.get("duration", ""),
        "thumbnail_url": metadata.get("thumbnail_url", ""),
        "tag": "youtube",
    }


def _podcast_view(metadata: dict) -> dict:
    return {
        "source": "podcast",
        "title": metadata["title"],
        "source_label": "Show",
        "source_name": metadata["show"],
        "source_url": metadata.get("show_url", ""),
        "url": metadata["url"],
        "url_label": "Listen on Apple Podcasts",
        "published": metadata.get("published", ""),
        "duration": metadata.get("duration", ""),
        "thumbnail_url": metadata.get("thumbnail_url", ""),
        "tag": "podcast",
    }


def _adapt_metadata(metadata: dict) -> dict:
    if metadata.get("source") == "podcast":
        return _podcast_view(metadata)
    return _yt_view(metadata)


def build_obsidian_note(
    metadata: dict,
    summary: str,
    transcript_md: str,
    extended_summary: str = "",
    focused_summary: str = "",
    focus_topic: str = "",
    include_transcript: bool = True,
    topics: list[str] | None = None,
    resources: list[dict] | None = None,
    transcript_source_label: str = "",
) -> tuple[str, str]:
    """Returns (filename, markdown_content). Works for YouTube and podcast metadata."""
    view = _adapt_metadata(metadata)

    filename = slugify(view["title"]) + ".md"

    thumbnail_line = ""
    if view["thumbnail_url"]:
        thumbnail_line = f'![thumbnail]({view["thumbnail_url"]})\n\n'

    topics_yaml = ""
    if topics:
        topics_yaml = "topics:\n" + "\n".join(f"  - {t}" for t in topics) + "\n"

    source_field_label = view["source_label"].lower()  # "channel" or "show"

    # Source-specific frontmatter fields
    fm_lines = [
        "---",
        f'title: "{view["title"]}"',
        f'{source_field_label}: "{view["source_name"]}"',
    ]
    if view["source_url"]:
        fm_lines.append(f'{source_field_label}_url: "{view["source_url"]}"')
    fm_lines.extend([
        f'url: "{view["url"]}"',
        f'published: "{view["published"]}"',
        f'duration: "{view["duration"]}"',
    ])
    if topics_yaml:
        fm_lines.append(topics_yaml.rstrip())
    fm_lines.append("tags:")
    fm_lines.append(f"  - {view['tag']}")
    fm_lines.append("  - transcript")
    fm_lines.append("---")
    frontmatter = "\n".join(fm_lines) + "\n\n"

    resources_section = ""
    if resources:
        lines = []
        for r in resources:
            name = r.get("name", "")
            rtype = r.get("type", "")
            if name:
                lines.append(f"- [[{name}]]" + (f" *({rtype})*" if rtype else ""))
        if lines:
            resources_section = "## Mentioned Resources\n\n" + "\n".join(lines) + "\n\n---\n\n"

    extended_summary_section = ""
    if extended_summary.strip():
        extended_summary_section = (
            "## Extended Summary\n\n"
            + _demote_headings(extended_summary.strip())
            + "\n\n---\n\n"
        )

    focused_summary_section = ""
    if focused_summary.strip():
        heading = f"## Focus: {focus_topic}" if focus_topic else "## Focus"
        focused_summary_section = (
            heading + "\n\n"
            + _demote_headings(focused_summary.strip())
            + "\n\n---\n\n"
        )

    transcript_heading = "## Transcript"
    if transcript_source_label:
        transcript_heading += f" *(via {transcript_source_label})*"
    transcript_section = ""
    if include_transcript and transcript_md.strip():
        transcript_section = (
            transcript_heading + "\n\n"
            + _demote_headings(transcript_md.strip())
            + "\n"
        )

    header_quote = (
        f"> **{view['source_label']}:** [{view['source_name']}]({view['source_url']})  \n"
        if view['source_url']
        else f"> **{view['source_label']}:** {view['source_name']}  \n"
    )

    note = (
        frontmatter
        + thumbnail_line
        + f"# {view['title']}\n\n"
        + header_quote
        + f"> **Published:** {view['published']}  \n"
        + f"> **Duration:** {view['duration']}  \n"
        + f"> **Link:** [{view['url_label']}]({view['url']})\n\n"
        + "---\n\n"
        + "## Summary\n\n"
        + summary.strip()
        + "\n\n---\n\n"
        + focused_summary_section
        + resources_section
        + extended_summary_section
        + transcript_section
    )

    return filename, note
