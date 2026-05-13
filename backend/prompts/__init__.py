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
}


def get_system_prompt(features: list[str], source: str = "youtube") -> str:
    """Build a composite system prompt from selected feature keys.

    Always includes 'base'. Additional features add their role preambles,
    JSON keys, and rules sections to the final prompt. The `source` parameter
    swaps the wording so the model knows whether the transcript is from a
    video or a podcast episode.
    """
    if "base" not in features:
        features = ["base"] + features

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

    preamble = f"""{role_line}
Your job is to process a raw {terms['kind']} transcript and return a clean, well-structured result.

You will receive:
{terms['metadata_line']}
- Raw transcript text"""

    key_count = len(all_json_keys)
    keys_section = f"\nYou must return a valid JSON object with exactly {key_count} key{'s' if key_count != 1 else ''}:"
    for i, key_def in enumerate(all_json_keys, 1):
        keys_section += f'\n{i}. "{key_def["key"]}": {key_def["description"]}'

    # Substitute the source noun in rules so "video" → "episode" for podcasts
    rules_text = "\n\n".join(all_rules)
    if source == "podcast":
        rules_text = rules_text.replace("video", terms["noun"])
        rules_text = rules_text.replace("Video", terms["noun"].capitalize())

    return f"""{preamble}

{keys_section}

{rules_text}

Return ONLY the JSON object, no other text."""
