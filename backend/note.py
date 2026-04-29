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
) -> tuple[str, str]:
    """Returns (filename, markdown_content)."""
    filename = slugify(metadata["title"]) + ".md"

    thumbnail_line = ""
    if metadata["thumbnail_url"]:
        thumbnail_line = f'![thumbnail]({metadata["thumbnail_url"]})\n\n'

    topics_yaml = ""
    if topics:
        topics_yaml = "topics:\n" + "\n".join(f"  - {t}" for t in topics) + "\n"

    frontmatter = (
        "---\n"
        f'title: "{metadata["title"]}"\n'
        f'channel: "{metadata["channel"]}"\n'
        f'channel_url: "{metadata["channel_url"]}"\n'
        f'url: "{metadata["url"]}"\n'
        f'published: "{metadata["upload_date"]}"\n'
        f'duration: "{metadata["duration"]}"\n'
        + topics_yaml
        + "tags:\n"
        "  - youtube\n"
        "  - transcript\n"
        "---\n\n"
    )

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

    transcript_section = ""
    if include_transcript and transcript_md.strip():
        transcript_section = (
            "## Transcript\n\n"
            + _demote_headings(transcript_md.strip())
            + "\n"
        )

    note = (
        frontmatter
        + thumbnail_line
        + f"# {metadata['title']}\n\n"
        + f"> **Channel:** [{metadata['channel']}]({metadata['channel_url']})  \n"
        + f"> **Published:** {metadata['upload_date']}  \n"
        + f"> **Duration:** {metadata['duration']}  \n"
        + f"> **Link:** [Watch on YouTube]({metadata['url']})\n\n"
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
