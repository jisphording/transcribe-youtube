"""Composable prompt system.

Each prompt module in this package exposes:
- ROLE_PREAMBLE: str — added to the "You are ..." role line
- JSON_KEYS: list[dict] — each with "key" and "description"
- RULES: str — the rules text block for this feature

To add a new prompt feature:
1. Create a new .py file in this directory with the three exports above
2. Register it in PROMPTS below
3. Add the corresponding flag to TranscriptRequest in models.py
4. Wire it up in main.py's event_generator
"""

import importlib

PROMPTS = {
    "base": {
        "name": "Transcript + Summary",
        "description": "Clean transcript with short 3-5 sentence summary",
        "module": "prompts.base",
    },
    "article": {
        "name": "Article Summary",
        "description": "Summary + key points + knowledge-base metadata for web articles (replaces base)",
        "module": "prompts.article",
    },
    "extended": {
        "name": "Extended Summary",
        "description": "Topic-by-topic editorial rewrite",
        "module": "prompts.extended",
    },
    "resources": {
        "name": "Mentioned Resources",
        "description": "Extract all products, software, websites, and services mentioned",
        "module": "prompts.resources",
    },
    "focus": {
        "name": "Focus Topic",
        "description": "Deep-dive summary focused on a user-specified topic",
        "module": "prompts.focus",
    },
}


SOURCE_TERMS = {
    "youtube": {
        "kind": "YouTube video",
        "noun": "video",
        "metadata_line": "- Video metadata (title, channel, description)",
    },
    "podcast": {
        "kind": "podcast episode",
        "noun": "episode",
        "metadata_line": "- Episode metadata (title, show, description)",
    },
    "web": {
        "kind": "web article",
        "noun": "article",
        "metadata_line": "- Article metadata (title, author, site, date, description)",
    },
}


def get_system_prompt(features: list[str], source: str = "youtube") -> str:
    """Build a composite system prompt from selected feature keys.

    Always includes 'base' ('article' for web sources). Additional features
    add their role preambles, JSON keys, and rules sections to the final
    prompt. The `source` parameter swaps the wording so the model knows
    whether the input is a video, a podcast episode or a web article.
    """
    base = "article" if source == "web" else "base"
    features = [base] + [f for f in features if f not in ("base", "article")]

    terms = SOURCE_TERMS.get(source, SOURCE_TERMS["youtube"])

    role_parts = []
    all_json_keys = []
    all_rules = []

    for feature_key in features:
        entry = PROMPTS.get(feature_key)
        if not entry:
            continue
        mod = importlib.import_module(entry["module"])
        if hasattr(mod, "ROLE_PREAMBLE"):
            role_parts.append(mod.ROLE_PREAMBLE)
        all_json_keys.extend(mod.JSON_KEYS)
        all_rules.append(mod.RULES)

    role_line = "You are " + ", ".join(role_parts) + "."

    if source == "web":
        preamble = f"""{role_line}
Your job is to turn the extracted main text of a {terms['kind']} into a concise, well-structured knowledge-base note.

You will receive:
{terms['metadata_line']}
- The article's main text (images, links and page chrome already removed)"""
    else:
        preamble = f"""{role_line}
Your job is to process a raw {terms['kind']} transcript and return a clean, well-structured result.

You will receive:
{terms['metadata_line']}
- Raw transcript text"""

    key_count = len(all_json_keys)
    keys_section = f"\nYou must return a valid JSON object with exactly {key_count} key{'s' if key_count != 1 else ''}:"
    for i, key_def in enumerate(all_json_keys, 1):
        keys_section += f'\n{i}. "{key_def["key"]}": {key_def["description"]}'

    # Substitute the source noun so "video" → "episode"/"article"
    rules_text = _adapt_wording("\n\n".join(all_rules), source, terms["noun"])
    keys_section = _adapt_wording(keys_section, source, terms["noun"])

    return f"""{preamble}

{keys_section}

{rules_text}

Return ONLY the JSON object, no other text."""


def _adapt_wording(text: str, source: str, noun: str) -> str:
    if source == "web":
        text = text.replace("transcript", "article text").replace("watched", "read")
    if source in ("podcast", "web"):
        text = text.replace("video", noun).replace("Video", noun.capitalize())
    return text
