import json
import os
import time
from typing import Generator

import httpx


PREFIX = "openrouter:"
# Verified against OpenRouter's model catalog on 2026-10-12. USD per million tokens.
# DeepSeek uses time-dependent pricing; use the higher rate for previews.
MODELS = {
    "deepseek/deepseek-v4.1-flash": {"name": "DeepSeek V4.1 Flash", "input": 0.30, "output": 1.20},
    "qwen/qwen3.5-flash": {"name": "Qwen3.5 Flash", "input": 0.065, "output": 0.26},
    "google/gemini-3.1-flash-lite-preview": {"name": "Gemini 3.1 Flash Lite (preview)", "input": 0.25, "output": 1.50},
}
MAX_OUTPUT_TOKENS = 65_536


def is_openrouter(model: str | None) -> bool:
    return bool(model) and model.startswith(PREFIX)


def model_info(model: str) -> dict:
    name = model.removeprefix(PREFIX)
    if name not in MODELS:
        raise ValueError(f"Unsupported OpenRouter model: {name}. Choose a curated model in plugin settings.")
    return MODELS[name]


def stream_openrouter(model: str, system_prompt: str, user_message: str) -> Generator[dict, None, None]:
    """Use the shared progress/done contract without storing credentials in queued jobs."""
    model_info(model)
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Set OPENROUTER_API_KEY in backend/.env and restart the backend to use OpenRouter.")
    body = {
        "model": model.removeprefix(PREFIX),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": MAX_OUTPUT_TOKENS,
        "stream": True,
        "stream_options": {"include_usage": True},
        "response_format": {"type": "json_object"},
        "reasoning": {"enabled": False},
        "provider": {"sort": "price", "require_parameters": True},
    }
    start = time.monotonic()
    raw = ""
    deltas = 0
    usage = {}
    stop_reason = None
    yield {"type": "progress", "input_tokens": 0, "output_tokens": 0, "elapsed": 0.0, "phase": "starting"}
    timeout = httpx.Timeout(connect=10, read=600, write=60, pool=10)
    with httpx.stream(
        "POST", "https://openrouter.ai/api/v1/chat/completions", json=body,
        headers={"Authorization": f"Bearer {api_key}", "X-Title": "Media to Obsidian"}, timeout=timeout,
    ) as response:
        if response.status_code >= 400:
            hints = {401: "Check OPENROUTER_API_KEY.", 402: "Add credits to your OpenRouter account.",
                     429: "Rate limit reached; retry later."}
            raise RuntimeError(f"OpenRouter returned HTTP {response.status_code}. {hints.get(response.status_code, 'Check model availability and retry.')}")
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            chunk = json.loads(payload)
            if chunk.get("error"):
                code = chunk["error"].get("code", "unknown")
                raise RuntimeError(f"OpenRouter streaming error ({code}); retry or choose another model.")
            if chunk.get("usage"):
                usage.update(chunk["usage"])
            for choice in chunk.get("choices") or []:
                if choice.get("finish_reason"):
                    stop_reason = choice["finish_reason"]
                content = (choice.get("delta") or {}).get("content")
                if content:
                    raw += content
                    deltas += 1
                    if deltas % 20 == 0:
                        yield {"type": "progress", "input_tokens": usage.get("prompt_tokens", 0),
                               "output_tokens": deltas, "elapsed": time.monotonic() - start, "phase": "generating"}
    if stop_reason == "length":
        raise RuntimeError("OpenRouter hit the output limit; the response is incomplete. Use a summary-only output mode.")
    if stop_reason not in ("stop", "content_filter") or not raw.strip():
        raise RuntimeError("OpenRouter stream ended without a complete response; retry or choose another model.")
    yield {
        "type": "done", "response": raw,
        "input_tokens": usage.get("prompt_tokens", 0), "output_tokens": usage.get("completion_tokens", deltas),
        "elapsed": time.monotonic() - start,
        "stop_reason": "refusal" if stop_reason == "content_filter" else stop_reason,
        "cost_usd": usage.get("cost"),
    }
