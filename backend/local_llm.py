import json
import os
import re
import time
from typing import Generator

import httpx


# Any OpenAI-compatible server works (MLX Core's mlx-serve, mlx_lm.server, LM Studio, …)
LOCAL_LLM_URL = os.environ.get("LOCAL_LLM_URL", "http://127.0.0.1:11234").rstrip("/")
LOCAL_LLM_MODELS_DIR = os.path.expanduser(os.environ.get("LOCAL_LLM_MODELS_DIR", "~/.mlx-serve/models"))
PREFIX = "local:"
MAX_OUTPUT_TOKENS = 12_000
DEFAULT_CONTEXT = 32_768
CHARS_PER_TOKEN_SAFE = 3.0
CONTEXT_MARGIN_TOKENS = 512
MIN_INPUT_CHARS = 2_000
_ctx_cap = os.environ.get("LOCAL_LLM_MAX_CONTEXT", "").strip()
LOCAL_LLM_MAX_CONTEXT = int(_ctx_cap) if _ctx_cap.isdigit() and int(_ctx_cap) > 0 else None

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class LocalOutputLimitError(RuntimeError):
    """The model stopped because it ran out of output tokens — the answer is incomplete."""


def is_local(model: str | None) -> bool:
    return bool(model) and model.startswith(PREFIX)


def model_id(model: str) -> str:
    return model[len(PREFIX):]


def _served() -> list[dict] | None:
    """Chat models the running server can serve; None when it isn't running."""
    try:
        resp = httpx.get(f"{LOCAL_LLM_URL}/v1/models", timeout=2)
        resp.raise_for_status()
        data = resp.json().get("data", [])
    except (httpx.HTTPError, ValueError):
        return None
    return [m for m in data if "chat" in (m.get("capabilities") or ["chat"])]


def _disk_models() -> list[str]:
    """Model folders below LOCAL_LLM_MODELS_DIR (<org>/<model>), speculative drafters excluded."""
    if not os.path.isdir(LOCAL_LLM_MODELS_DIR):
        return []
    names = []
    for org in sorted(os.listdir(LOCAL_LLM_MODELS_DIR)):
        org_dir = os.path.join(LOCAL_LLM_MODELS_DIR, org)
        if not os.path.isdir(org_dir):
            continue
        for name in sorted(os.listdir(org_dir)):
            try:
                with open(os.path.join(org_dir, name, "config.json")) as f:
                    model_type = json.load(f).get("model_type", "")
            except (OSError, ValueError):
                continue
            if not model_type.endswith("_assistant"):
                names.append(name)
    return names


def list_models() -> dict:
    """Served models plus installed ones the server can't reach until switched in the MLX Core menu.

    status: "loaded" | "unloaded" (served, loads on first use — may not fit in RAM)
            | "switch" (installed in another folder) | "offline" (server not running)
    """
    served = _served()
    models = []
    for m in served or []:
        meta = m.get("meta") or {}
        models.append({
            "id": f"{PREFIX}{m['id']}",
            "name": m["id"],
            "status": "loaded" if m.get("state", "ready") == "ready" else "unloaded",
            "context_length": min(
                meta.get("context_length") or DEFAULT_CONTEXT,
                LOCAL_LLM_MAX_CONTEXT or float("inf"),
            ),
        })
    known = {m["name"] for m in models}
    for name in _disk_models():
        if name not in known:
            models.append({
                "id": f"{PREFIX}{name}",
                "name": name,
                "status": "offline" if served is None else "switch",
                "context_length": None,
            })
    return {"server": served is not None, "models": models}


def context_length(model: str) -> int | None:
    """Context window of a served local:<id> model, None when the server doesn't list it."""
    name = model_id(model)
    for m in list_models()["models"]:
        if m["name"] == name and m["status"] in ("loaded", "unloaded"):
            return m["context_length"]
    return None


def input_budget_chars(model: str, system_chars: int, output_ratio: float) -> int:
    """Input chars that fit next to the system prompt and an answer of output_ratio × input."""
    ctx = context_length(model) or DEFAULT_CONTEXT
    system_tokens = system_chars / CHARS_PER_TOKEN_SAFE
    budget_tokens = (ctx - system_tokens - CONTEXT_MARGIN_TOKENS) / (1 + output_ratio)
    if output_ratio > 0:
        budget_tokens = min(budget_tokens, MAX_OUTPUT_TOKENS / output_ratio)
    return max(MIN_INPUT_CHARS, int(budget_tokens * CHARS_PER_TOKEN_SAFE))


def _clean(text: str) -> str:
    """Drop inline reasoning and anything around the JSON object."""
    text = _THINK_RE.sub("", text).strip()
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start != -1 and end > start else text


def stream_local(
    model: str,
    system_prompt: str,
    user_message: str,
) -> Generator[dict, None, None]:
    """Same yield contract as claude.stream_claude()."""
    name = model_id(model)
    # ≈ 3.5 chars per token; leave the rest of the context window for the answer
    prompt_tokens = int((len(system_prompt) + len(user_message)) / 3.5)
    served = {m["name"]: m for m in list_models()["models"] if m["status"] in ("loaded", "unloaded")}
    if name not in served:
        # mlx-serve silently answers with the loaded model for unknown names — never let that happen
        raise RuntimeError(
            f"{name} isn't served by the local MLX server. Select it in the MLX Core menu, "
            "then refresh the Local dropdown."
        )
    ctx = served[name]["context_length"]
    if prompt_tokens > ctx - 1024:
        raise RuntimeError(
            f"Input (~{prompt_tokens} tokens) exceeds {name}'s context window ({ctx} tokens). "
            "Pick a model with a larger context window or use a Claude model."
        )
    max_tokens = int(min(MAX_OUTPUT_TOKENS, ctx - prompt_tokens - 256))

    raw_response = ""
    output_tokens = 0
    input_tokens = 0
    stop_reason = None
    start_time = time.time()

    yield {"type": "progress", "input_tokens": prompt_tokens, "output_tokens": 0,
           "elapsed": 0.0, "phase": "starting"}

    body = {
        "model": name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
        "response_format": {"type": "json_object"},
        "chat_template_kwargs": {"enable_thinking": False},
    }
    # Loading an unloaded model can take a while before the first token
    timeout = httpx.Timeout(connect=5, read=600, write=60, pool=5)
    with httpx.stream("POST", f"{LOCAL_LLM_URL}/v1/chat/completions", json=body, timeout=timeout) as resp:
        if resp.status_code >= 400:
            resp.read()
            if "model_load_failed" in resp.text:
                raise RuntimeError(
                    f"The MLX server couldn't load {name} (usually not enough free RAM next to the "
                    "loaded model). Select it in the MLX Core menu instead."
                )
            raise RuntimeError(f"Local model server returned {resp.status_code}: {resp.text[:300]}")
        for line in resp.iter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except ValueError:
                continue
            if chunk.get("usage"):
                input_tokens = chunk["usage"].get("prompt_tokens", input_tokens)
                output_tokens = chunk["usage"].get("completion_tokens", output_tokens)
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                if choice.get("finish_reason"):
                    stop_reason = choice["finish_reason"]
                text = delta.get("content")
                if text:
                    raw_response += text
                elif not delta.get("reasoning_content"):
                    continue
                output_tokens += 1
                if output_tokens % 20 == 0:
                    yield {
                        "type": "progress",
                        "input_tokens": input_tokens or prompt_tokens,
                        "output_tokens": output_tokens,
                        "elapsed": time.time() - start_time,
                        "phase": "generating",
                    }

    if stop_reason == "length":
        raise LocalOutputLimitError(f"{name} hit the output limit ({max_tokens} tokens) — the answer is incomplete.")

    yield {
        "type": "done",
        "response": _clean(raw_response),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "elapsed": time.time() - start_time,
        "stop_reason": stop_reason,
    }
