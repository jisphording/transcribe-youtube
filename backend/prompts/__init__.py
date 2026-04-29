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


def get_system_prompt(features: list[str]) -> str:
    """Build a composite system prompt from selected feature keys.

    Always includes 'base'. Additional features add their role preambles,
    JSON keys, and rules sections to the final prompt.
    """
    if "base" not in features:
        features = ["base"] + features

    # Collect parts from each feature module
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

    # Build role line
    role_line = "You are " + ", ".join(role_parts) + "."

    # Build preamble
    preamble = f"""{role_line}
Your job is to process a raw YouTube transcript and return a clean, well-structured result.

You will receive:
- Video metadata (title, channel, description)
- Raw transcript text"""

    # Build JSON keys instruction
    key_count = len(all_json_keys)
    keys_section = f"\nYou must return a valid JSON object with exactly {key_count} key{'s' if key_count != 1 else ''}:"
    for i, key_def in enumerate(all_json_keys, 1):
        keys_section += f'\n{i}. "{key_def["key"]}": {key_def["description"]}'

    # Combine rules
    rules_section = "\n\n".join(all_rules)

    return f"""{preamble}

{keys_section}

{rules_section}

Return ONLY the JSON object, no other text."""
