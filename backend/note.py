import re


def slugify(title: str) -> str:
    """Convert title to all-lowercase-hyphen filename."""
    slug = title.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    slug = slug.strip("-")
    return slug


def build_obsidian_note(metadata: dict, summary: str, transcript_md: str, extended_summary: str = "") -> tuple[str, str]:
    """Returns (filename, markdown_content)."""
    filename = slugify(metadata["title"]) + ".md"

    thumbnail_line = ""
    if metadata["thumbnail_url"]:
        thumbnail_line = f'![thumbnail]({metadata["thumbnail_url"]})\n\n'

    frontmatter = f"""---
title: "{metadata['title']}"
channel: "{metadata['channel']}"
channel_url: "{metadata['channel_url']}"
url: "{metadata['url']}"
published: "{metadata['upload_date']}"
duration: "{metadata['duration']}"
tags:
  - youtube
  - transcript
---

"""

    extended_summary_section = ""
    if extended_summary.strip():
        extended_summary_section = (
            "## Extended Summary\n\n"
            + extended_summary.strip()
            + "\n\n---\n\n"
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
        + extended_summary_section
        + "## Transcript\n\n"
        + transcript_md.strip()
        + "\n"
    )

    return filename, note
