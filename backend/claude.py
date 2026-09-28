import re
import json
import os
import time
from typing import Generator
import anthropic


client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

HAIKU = "claude-haiku-4-5"
SONNET = "claude-sonnet-5"
OPUS = "claude-opus-5-5"

VALID_MODELS = {HAIKU, SONNET, OPUS}
DEFAULT_MODEL = HAIKU
DEFAULT_EXTENDED_MODEL = SONNET

# Older plugin builds still send the previous-generation ids.
LEGACY_MODELS = {
    "claude-haiku-4-5-20251001": HAIKU,
    "claude-sonnet-4-6": SONNET,
    "claude-opus-4-7": OPUS,
}

MODEL_MAX_OUTPUT_TOKENS = {
    HAIKU: 8192,
    SONNET: 128000,
    OPUS: 128000,
}


def resolve_model(model: str | None, default: str) -> str:
    model = LEGACY_MODELS.get(model or "", model)
    return model if model in VALID_MODELS else default


def stream_claude(
    model: str,
    system_prompt: str,
    user_message: str,
    effort: str | None = None,
) -> Generator[dict, None, None]:
    """Stream a Claude completion, yielding real-time progress dicts.

    Yields dicts with type="progress" containing token/timing info,
    and finally a dict with type="done" containing the full response.
    Haiku rejects `effort`; the other models think adaptively by default.
    """
    validated_model = resolve_model(model, DEFAULT_MODEL)

    raw_response = ""
    output_tokens = 0
    input_tokens = 0
    stop_reason = None
    start_time = time.time()

    max_tokens = MODEL_MAX_OUTPUT_TOKENS.get(validated_model, 8192)
    extra = {"output_config": {"effort": effort}} if effort and validated_model != HAIKU else {}

    with client.messages.stream(
        model=validated_model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
        **extra,
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
                # Thinking deltas count toward progress but are not part of the answer
                if event.delta.type == "text_delta":
                    raw_response += event.delta.text
                elif event.delta.type != "thinking_delta":
                    continue
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
                if getattr(event.delta, "stop_reason", None):
                    stop_reason = event.delta.stop_reason
                if hasattr(event.usage, "output_tokens"):
                    output_tokens = event.usage.output_tokens

    yield {
        "type": "done",
        "response": raw_response,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "elapsed": time.time() - start_time,
        "stop_reason": stop_reason,
    }


def parse_claude_response(raw_response: str) -> dict:
    """Strip code fences and parse JSON from Claude's response."""
    cleaned = raw_response.strip()

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)

    return json.loads(cleaned)
