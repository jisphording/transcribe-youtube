"""Claude subscription provider: headless Claude Code CLI per docs/claude-cli-contract.md."""
import json
import os
import signal
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Generator

PREFIX = "claude-cli:"
MODELS = {
    "claude-cli:haiku": "Claude Haiku (subscription)",
    "claude-cli:sonnet": "Claude Sonnet (subscription)",
    "claude-cli:opus": "Claude Opus (subscription)",
    "claude-cli:fable": "Claude Fable (subscription)",
}
EFFORTS = ("low", "medium", "high", "xhigh", "max")
# The contract measured 220-435 s for ~40-50k output tokens; 900 s leaves headroom.
TIMEOUT = int(os.environ.get("CLAUDE_CLI_TIMEOUT_SECONDS", "900"))
MAX_OUTPUT = 16_000_000
# 400k chars were verified single-shot; keep a conservative margin and chunk above it.
MAX_TRANSCRIPT_CHARS = 300_000


def is_claude_cli(model: str | None) -> bool:
    return bool(model) and model.startswith(PREFIX)


def validate_model(model: str) -> str:
    if model not in MODELS:
        raise ValueError(f"Unsupported Claude subscription model: {model}. Choose one of {', '.join(MODELS)}.")
    return model[len(PREFIX):]


def model_label(model: str) -> str:
    validate_model(model)
    return MODELS[model]


def _executable() -> str:
    path = os.environ.get("CLAUDE_CLI_PATH", "").strip()
    if not path:
        raise RuntimeError("Set CLAUDE_CLI_PATH in backend/.env to the absolute path of the claude executable.")
    if not Path(path).is_absolute() or not Path(path).is_file():
        raise RuntimeError("CLAUDE_CLI_PATH must be the absolute path of an installed claude executable.")
    return str(Path(path).resolve())


def _child_env() -> dict[str, str]:
    # Inherited ANTHROPIC_* / API keys or a Bedrock/Vertex switch would outrank the keychain
    # OAuth login and bill an API account instead of the subscription.
    env = os.environ.copy()
    for key in list(env):
        upper = key.upper()
        if upper.startswith("ANTHROPIC_") or "API_KEY" in upper or upper.startswith(("CLAUDE_CODE_USE_", "AWS_BEARER_TOKEN_BEDROCK")):
            env.pop(key)
    return env


def _terminate_process_group(pid: int, grace: float = 3.0) -> None:
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        try:
            os.killpg(pid, 0)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(0.05)
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _limit_reset(events: list[dict]) -> int | None:
    """resetsAt of a blocking rate_limit_event (0 when unknown); None when not blocked."""
    for event in reversed(events):
        info = event.get("rate_limit_info") if event.get("type") == "rate_limit_event" else None
        status = str((info or {}).get("status") or "allowed")
        if info and not status.startswith("allowed"):
            reset = info.get("resetsAt")
            return int(reset) if isinstance(reset, (int, float)) and 0 < reset < 10**11 else 0
    return None


def _error_message(events: list[dict], final: dict | None, returncode: int) -> str:
    text = str((final or {}).get("result", "")).lower()
    reset = _limit_reset(events)
    if reset is not None or any(term in text for term in ("usage limit", "rate limit", "limit reached")):
        when = f" (resets {datetime.fromtimestamp(reset).strftime('%Y-%m-%d %H:%M')})" if reset else ""
        return f"Claude subscription usage limit reached{when}; retry after the limit resets."
    if "not logged in" in text or "/login" in text:
        return "The Claude CLI is not logged in; run `claude auth login` as this user and retry."
    if (final or {}).get("api_error_status") == 404 or "selected model" in text:
        return "The selected Claude subscription model is unavailable for this account."
    if final is None:
        return f"Claude CLI failed (exit {returncode}) without a result event."
    return f"Claude CLI failed (exit {returncode}); check `claude auth status` and retry."


def _parse(stdout: bytes, returncode: int) -> tuple[str, dict]:
    events = []
    for line in stdout.decode(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    results = [event for event in events if event.get("type") == "result"]
    final = results[-1] if results else None
    if final and final.get("stop_reason") == "refusal":
        return str(final.get("result") or ""), {**(final.get("usage") or {}), "stop_reason": "refusal"}
    if final is None or final.get("is_error") or returncode:
        raise RuntimeError(_error_message(events, final, returncode))
    text = final.get("result")
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("Claude CLI returned an empty result.")
    return text, {**(final.get("usage") or {}), "stop_reason": final.get("stop_reason") or "end_turn"}


def _run(model: str, effort: str, system_prompt: str, user_message: str) -> tuple[str, dict]:
    alias = validate_model(model)
    if effort not in EFFORTS:
        raise ValueError(f"Unsupported effort for the Claude CLI: {effort}")
    executable = _executable()
    argv = [executable, "-p", "--output-format", "stream-json", "--verbose",
            "--tools", "", "--system-prompt", system_prompt, "--model", alias, "--effort", effort,
            "--no-session-persistence", "--strict-mcp-config", "--setting-sources", "",
            "--disable-slash-commands"]
    with tempfile.TemporaryDirectory(prefix="media-claude-cli-") as cwd:
        try:
            process = subprocess.Popen(argv, cwd=cwd, env=_child_env(), stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        except OSError as exc:
            raise RuntimeError("Could not start the Claude CLI; check CLAUDE_CLI_PATH.") from exc
        output: dict[str, bytes | bool] = {"stdout": b"", "stderr": b"", "overflow": False}

        def drain(name: str, stream) -> None:
            chunks, size = [], 0
            while chunk := stream.read(65536):
                keep = chunk[:max(0, MAX_OUTPUT - size)]
                chunks.append(keep)
                size += len(keep)
                if len(keep) < len(chunk):
                    output["overflow"] = True
            output[name] = b"".join(chunks)

        def send_input() -> None:
            try:
                process.stdin.write(user_message.encode())
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass

        threads = [threading.Thread(target=drain, args=(name, getattr(process, name)), daemon=True)
                   for name in ("stdout", "stderr")]
        threads.append(threading.Thread(target=send_input, daemon=True))
        try:
            for thread in threads:
                thread.start()
            process.wait(timeout=TIMEOUT)
            _terminate_process_group(process.pid, grace=0.2)
            for thread in threads:
                thread.join(timeout=5)
            if output["overflow"]:
                raise RuntimeError("Claude CLI output exceeded the configured limit.")
            return _parse(output["stdout"], process.returncode)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Claude CLI timed out after {TIMEOUT} s.") from exc
        finally:
            _terminate_process_group(process.pid, grace=0.2)
            if process.poll() is None:
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
                    process.wait()


def stream_claude_cli(model: str, system_prompt: str, user_message: str, effort: str | None) -> Generator[dict, None, None]:
    validate_model(model)
    started = time.monotonic()
    yield {"type": "progress", "input_tokens": 0, "output_tokens": 0, "elapsed": 0.0, "phase": "starting"}
    response, usage = _run(model, effort or "low", system_prompt, user_message)
    cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
    cache_write = int(usage.get("cache_creation_input_tokens", 0) or 0)
    yield {"type": "done", "response": response,
           "input_tokens": int(usage.get("input_tokens", 0) or 0) + cache_read + cache_write,
           "output_tokens": int(usage.get("output_tokens", 0) or 0),
           "thinking_tokens": 0, "cache_read_tokens": cache_read,
           "elapsed": time.monotonic() - started, "stop_reason": usage["stop_reason"], "cost_usd": 0.0}
