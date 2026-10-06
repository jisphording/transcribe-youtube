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


_VALID_ESCAPES = set('"\\/bfnrtu')


def _repair_json(text: str) -> str:
    """Best-effort fix for typical LLM JSON slips.

    Handles unescaped quotes inside strings, invalid escapes (e.g. `\\_`),
    raw control characters, trailing commas and truncated output.
    """
    out: list[str] = []
    stack: list[str] = []
    in_str = False
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if in_str:
            if c == "\\":
                nxt = text[i + 1] if i + 1 < n else ""
                if nxt in _VALID_ESCAPES and nxt:
                    out.append(c + nxt)
                    i += 2
                else:
                    out.append("\\\\")
                    i += 1
                continue
            if c == '"':
                j = i + 1
                while j < n and text[j] in " \t\r\n":
                    j += 1
                nxt = text[j] if j < n else ""
                # A real closing quote is followed by a structural character (or EOF)
                closes = nxt in ",:}]" or nxt == ""
                if closes and nxt == ":" and stack and stack[-1] == "[":
                    closes = False
                if closes and nxt == "," :
                    k = j + 1
                    while k < n and text[k] in " \t\r\n":
                        k += 1
                    after = text[k] if k < n else ""
                    closes = after in '"{[-0123456789tfn]}' or after == ""
                if closes:
                    in_str = False
                    out.append(c)
                else:
                    out.append('\\"')
                i += 1
                continue
            if c == "\n":
                out.append("\\n")
            elif c == "\r":
                out.append("\\r")
            elif c == "\t":
                out.append("\\t")
            elif ord(c) < 0x20:
                out.append(f"\\u{ord(c):04x}")
            else:
                out.append(c)
            i += 1
            continue
        if c == '"':
            in_str = True
        elif c in "{[":
            stack.append(c)
        elif c in "}]":
            # drop trailing comma before a closer
            k = len(out) - 1
            while k >= 0 and out[k].isspace():
                k -= 1
            if k >= 0 and out[k] == ",":
                out.pop(k)
            if stack:
                stack.pop()
        out.append(c)
        i += 1

    # Truncated output: close the open string and any open containers
    if in_str:
        out.append('"')
    while out and out[-1].isspace():
        out.pop()
    if out and out[-1] == ",":
        out.pop()
    if out and out[-1] == ":":
        out.append("null")
    for opener in reversed(stack):
        out.append("}" if opener == "{" else "]")
    return "".join(out)


def parse_claude_response(raw_response: str) -> dict:
    """Parse JSON from an LLM response, tolerating fences, prose and common syntax slips."""
    cleaned = raw_response.strip()

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        return json.loads(cleaned, strict=False)
    except json.JSONDecodeError as first_error:
        start = cleaned.find("{")
        if start == -1:
            raise
        candidate = cleaned[start:]
        end = candidate.rfind("}")
        attempts = [candidate[: end + 1]] if end != -1 else []
        attempts.append(candidate)
        for attempt in attempts:
            try:
                result = json.loads(attempt, strict=False)
            except json.JSONDecodeError:
                try:
                    result = json.loads(_repair_json(attempt), strict=False)
                except json.JSONDecodeError:
                    continue
            if isinstance(result, dict):
                return result
        raise first_error
