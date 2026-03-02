import re
import json
import os
from typing import Callable
import anthropic


client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

VALID_MODELS = {"claude-sonnet-4-6", "claude-opus-4-6"}
DEFAULT_MODEL = "claude-sonnet-4-6"


def stream_claude(
    model: str,
    system_prompt: str,
    user_message: str,
    on_progress: Callable[[int], None] | None = None,
) -> str:
    """Stream a Claude completion and return the full response text.

    Args:
        model: Model ID (must be in VALID_MODELS).
        system_prompt: System prompt text.
        user_message: User message text.
        on_progress: Optional callback called with chars_generated count every ~100 tokens.
    """
    validated_model = model if model in VALID_MODELS else DEFAULT_MODEL

    raw_response = ""
    token_count = 0
    with client.messages.stream(
        model=validated_model,
        max_tokens=128000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    ) as stream:
        for text in stream.text_stream:
            raw_response += text
            token_count += 1
            if on_progress and token_count % 100 == 0:
                on_progress(token_count * 4)

    return raw_response


def parse_claude_response(raw_response: str) -> dict:
    """Strip code fences and parse JSON from Claude's response."""
    cleaned = raw_response.strip()

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)

    return json.loads(cleaned)
