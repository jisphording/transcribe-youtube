import re
import json
import os
import time
from typing import Generator
import anthropic


client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

VALID_MODELS = {"claude-haiku-4-5-20251001", "claude-sonnet-4-6", "claude-opus-4-6"}
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_EXTENDED_MODEL = "claude-sonnet-4-6"

MODEL_MAX_OUTPUT_TOKENS = {
    "claude-haiku-4-5-20251001": 8192,
    "claude-sonnet-4-6": 128000,
    "claude-opus-4-6": 128000,
}


def stream_claude(
    model: str,
    system_prompt: str,
    user_message: str,
) -> Generator[dict, None, None]:
    """Stream a Claude completion, yielding real-time progress dicts.

    Yields dicts with type="progress" containing token/timing info,
    and finally a dict with type="done" containing the full response.
    """
    validated_model = model if model in VALID_MODELS else DEFAULT_MODEL

    raw_response = ""
    output_tokens = 0
    input_tokens = 0
    start_time = time.time()

    max_tokens = MODEL_MAX_OUTPUT_TOKENS.get(validated_model, 8192)

    with client.messages.stream(
        model=validated_model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    ) as stream:
        for event in stream:
            if event.type == "message_start":
                input_tokens = event.message.usage.input_tokens
                yield {
                    "type": "progress",
                    "input_tokens": input_tokens,
                    "output_tokens": 0,
                    "elapsed": time.time() - start_time,
                    "phase": "starting",
                }

            elif event.type == "content_block_delta":
                text = event.delta.text
                raw_response += text
                output_tokens += 1  # each delta ≈ 1 token
                if output_tokens % 20 == 0:
                    yield {
                        "type": "progress",
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "elapsed": time.time() - start_time,
                        "phase": "generating",
                    }

            elif event.type == "message_delta":
                if hasattr(event.usage, "output_tokens"):
                    output_tokens = event.usage.output_tokens

    yield {
        "type": "done",
        "response": raw_response,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "elapsed": time.time() - start_time,
    }


def parse_claude_response(raw_response: str) -> dict:
    """Strip code fences and parse JSON from Claude's response."""
    cleaned = raw_response.strip()

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)

    return json.loads(cleaned)
