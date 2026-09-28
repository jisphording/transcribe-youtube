import json
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


def _yaml_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _yaml_list(key: str, values: list[str]) -> list[str]:
    values = [v for v in values if v]
    if not values:
        return []
    return [f"{key}:"] + [f"  - {_yaml_str(v)}" for v in values]


def _build_web_note(
    metadata: dict,
    summary: str,
    extended_summary: str,
    focused_summary: str,
    focus_topic: str,
    topics: list[str],
    resources: list[dict],
    article_info: dict,
) -> tuple[str, str]:
    title = metadata["title"]
    url = metadata["url"]
    authors = metadata.get("authors", [])
    tldr = article_info.get("tldr", "").strip()
    key_points = [p for p in article_info.get("key_points", []) if p]
    useful_for = [u for u in article_info.get("useful_for", []) if u]

    fm = ["---", f"title: {_yaml_str(title)}", f"source: {_yaml_str(url)}"]
    fm += _yaml_list("author", authors)
    fm.append(f"site: {_yaml_str(metadata.get('site', ''))}")
    if metadata.get("published"):
        fm.append(f"published: {_yaml_str(metadata['published'])}")
    fm.append(f"accessed: {_yaml_str(metadata['accessed'])}")
    if metadata.get("manual"):
        fm.append('capture: "manual paste"')
    if article_info.get("content_type"):
        fm.append(f"content_type: {_yaml_str(article_info['content_type'])}")
    if metadata.get("language"):
        fm.append(f"language: {_yaml_str(metadata['language'])}")
    fm.append(f"word_count: {metadata.get('word_count', 0)}")
    fm.append(f"reading_time: {_yaml_str(metadata.get('reading_time', ''))}")
    if tldr:
        fm.append(f"description: {_yaml_str(tldr)}")
    fm += _yaml_list("useful_for", useful_for)
    fm += _yaml_list("topics", topics)
    fm += ["tags:", "  - article", "  - summary", "---"]

    header = [f"# {title}", ""]
    byline = []
    if authors:
        byline.append(f"**Author:** {', '.join(authors)}")
    byline.append(f"**Site:** {metadata.get('site', '')}")
    header.append("> " + " · ".join(byline) + "  ")
    dates = []
    if metadata.get("published"):
        dates.append(f"**Published:** {metadata['published']}")
    dates.append(f"**Reading time:** {metadata.get('reading_time', '')}")
    header.append("> " + " · ".join(dates) + "  ")
    header.append(f"> **Source:** [{url}]({url})")
    header.append("")
    if metadata.get("truncated"):
        header += ["> [!warning] Very long page — only the first part was summarized.", ""]
    if tldr:
        header += ["> [!abstract] TL;DR", f"> {tldr}", ""]

    sections = ["## Summary\n\n" + summary.strip()]
    if key_points:
        sections.append("## Key Points\n\n" + "\n".join(f"- {p}" for p in key_points))
    if useful_for:
        sections.append("## Useful For\n\n" + "\n".join(f"- {u}" for u in useful_for))
    if focused_summary.strip():
        heading = f"## Focus: {focus_topic}" if focus_topic else "## Focus"
        sections.append(heading + "\n\n" + _demote_headings(focused_summary.strip()))
    resource_lines = [
        f"- [[{r['name']}]]" + (f" *({r['type']})*" if r.get("type") else "")
        for r in resources if r.get("name")
    ]
    if resource_lines:
        sections.append("## Mentioned Resources\n\n" + "\n".join(resource_lines))
    if extended_summary.strip():
        sections.append("## Extended Summary\n\n" + _demote_headings(extended_summary.strip()))

    note = "\n".join(fm) + "\n\n" + "\n".join(header) + "\n---\n\n" + "\n\n---\n\n".join(sections) + "\n"
    filename = (slugify(title)[:100].rstrip("-") or "article") + ".md"
    return filename, note


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
    article_info: dict | None = None,
) -> tuple[str, str]:
    """Returns (filename, markdown_content). Works for YouTube, podcast and web metadata."""
    if metadata.get("source") == "web":
        return _build_web_note(
            metadata, summary, extended_summary, focused_summary, focus_topic,
            topics or [], resources or [], article_info or {},
        )

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
