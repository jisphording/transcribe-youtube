"""Antigravity subscription provider using the approved macOS containment boundary."""
import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Generator

PREFIX = "antigravity:"
MODEL = "gemini-3.8-flash-medium"
MODEL_ID = PREFIX + MODEL
TIMEOUT = int(os.environ.get("ANTIGRAVITY_TIMEOUT_SECONDS", "600"))
MAX_OUTPUT = 8_000_000
# Subscription CLI limits are not documented as Gemini API context limits. Keep
# each request deliberately small; long transcripts are handled by main.py's
# existing source-aware chunk/reduce pipeline.
MAX_TRANSCRIPT_CHARS = 12_000


def is_antigravity(model: str | None) -> bool:
    return bool(model) and model.startswith(PREFIX)


def validate_model(model: str) -> str:
    if model != MODEL_ID:
        raise ValueError(f"Unsupported Antigravity model: {model}. Choose Gemini 3.8 Flash (medium).")
    return MODEL


def _terminal_result(events: list[dict]) -> tuple[str, dict]:
    finals = [event.get("result", event) for event in events if event.get("event") == "result" or event.get("type") == "result"]
    if not finals:
        raise RuntimeError("Antigravity stream ended without a terminal result.")
    final = finals[-1]
    if final.get("status") != "SUCCESS":
        detail = " ".join(str(final.get(key, "")) for key in ("status", "error", "message")).lower()
        if any(word in detail for word in ("quota", "rate limit", "resource exhausted", "resource_exhausted", "too many requests")):
            message = "Antigravity subscription quota or rate limit reached; retry explicitly after access is available."
        elif any(word in detail for word in ("auth", "login", "unauthorized", "credential")):
            message = "Antigravity authentication failed or expired; sign in to the CLI and retry."
        elif any(word in detail for word in ("model", "not found", "unavailable")):
            message = "The configured Antigravity model is unavailable; verify the signed-in account's model access."
        else:
            message = "Antigravity did not complete successfully."
        raise RuntimeError(message)
    response = final.get("structured_output") or final.get("response")
    if not isinstance(response, str) or not response.strip():
        raise RuntimeError("Antigravity returned no result.")
    return response, final.get("usage") or final.get("stats") or {}


def _terminate_process_group(pid: int, grace: float = 3.0) -> None:
    """Stop surviving CLI descendants and escalate so inherited pipes cannot hang readers."""
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _run(system_prompt: str, user_message: str) -> tuple[str, dict]:
    executable = os.environ.get("ANTIGRAVITY_CLI_PATH", "").strip() or shutil.which("agy") or str(Path.home() / ".local/bin/agy")
    if not Path(executable).is_absolute() or not Path(executable).is_file():
        raise RuntimeError("Set ANTIGRAVITY_CLI_PATH to the absolute path of the installed agy executable.")
    if os.uname().sysname != "Darwin" or not Path("/usr/bin/sandbox-exec").is_file():
        raise RuntimeError("macOS sandbox-exec is required; refusing an uncontained Antigravity process.")
    repo = Path(__file__).resolve().parents[1]
    vaults = [Path(path).expanduser().resolve() for path in os.environ.get("ANTIGRAVITY_VAULT_PATHS", "").split(os.pathsep) if path]
    with tempfile.TemporaryDirectory(prefix="media-antigravity-") as temp:
        base = Path(temp).resolve()
        workspace = base / "workspace"
        agent = workspace / ".agents/agents/transcript-only.md"
        agent.parent.mkdir(parents=True)
        agent.write_text("""---
name: transcript-only
description: Process supplied transcript text only.
mainAgent: true
subagent: false
tools: []
mcpServers: []
skills: []
plugins: []
commandExecutionPolicy: off
---
Return only the requested JSON. No tools are available.
""")
        log = base / "agy.log"
        deny_paths = [repo, *vaults, Path("/Volumes")]
        rules = ["(version 1)", "(allow default)",
                 f'(deny process-exec (require-not (require-any (literal "{executable}") (literal "/usr/bin/security"))))',
                 "(deny signal)"]
        rules.extend(f'(deny file-read* file-write* (subpath "{path}"))' for path in deny_paths)
        rules.extend([
            f'(deny file-write* (require-not (require-any (literal "{log}") (subpath "{Path.home()}/.gemini/antigravity-cli"))))',
            f'(deny file-write* (subpath "{workspace}"))',
            f'(deny file-read* (require-all (subpath "{workspace}/.agents") (require-not (require-any (literal "{workspace}/.agents") (subpath "{agent.parent}")))))',
            f'(deny file-read-data (require-all (subpath "{Path.home()}") (require-not (require-any (literal "{executable}") (subpath "{Path.home()}/.gemini/antigravity-cli") (subpath "{Path.home()}/Library/Keychains") (subpath "{Path.home()}/Library/Preferences") (literal "{Path.home()}/.gemini/config") (subpath "{Path.home()}/.gemini/config/projects")))))',
            f'(deny file-read* (subpath "{workspace}/.agent"))',
            f'(deny file-read* (subpath "{Path.home()}/.gemini/antigravity-cli/settings.json"))',
            f'(deny file-read* (subpath "{Path.home()}/.gemini/antigravity-cli/hooks.json"))',
            f'(deny file-read* (subpath "{Path.home()}/.gemini/antigravity-cli/rules"))',
            f'(deny file-read* (subpath "{Path.home()}/.gemini/antigravity-cli/plugins"))',
            f'(deny file-read* (subpath "{Path.home()}/.gemini/antigravity-cli/skills"))',
            f'(deny file-read* (subpath "{Path.home()}/.gemini/antigravity-cli/mcp_config.json"))',
            f'(deny file-read* (subpath "{Path.home()}/.gemini/antigravity-cli/history.jsonl"))',
            f'(deny file-read* (subpath "{Path.home()}/.gemini/AGENTS.md"))',
            f'(deny file-read* (subpath "{Path.home()}/.gemini/GEMINI.md"))',
            f'(deny file-read* (subpath "{workspace}/.agents/hooks.json"))',
            f'(deny file-read* (subpath "{workspace}/.agents/mcp_config.json"))',
            f'(deny file-read* (subpath "{workspace}/.agents/rules"))',
            f'(deny file-read* (subpath "{workspace}/GEMINI.md"))',
            f'(deny file-read* (subpath "{workspace}/AGENTS.md"))',
        ])
        profile = base / "agy.sb"
        profile.write_text("\n".join(rules))
        argv = ["/usr/bin/sandbox-exec", "-f", str(profile), executable, "--agent", "transcript-only",
                "--model", MODEL, "--disable-slash-commands", "--input-format", "stream-json",
                "--output-format", "stream-json", "--print-timeout", f"{max(1, TIMEOUT - 15)}s", "--log-file", str(log)]
        env = os.environ.copy()
        for key in list(env):
            upper = key.upper()
            if "API_KEY" in upper or upper.startswith(("AGY_", "ANTIGRAVITY_", "GEMINI_", "GOOGLE_GENAI_USE_")):
                env.pop(key, None)
        env.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
        env.pop("GOOGLE_GEMINI_BASE_URL", None)
        prompt = f"{system_prompt}\n\n--- SOURCE TEXT ---\n{user_message}\n--- END SOURCE TEXT ---"
        payload = json.dumps({"event": "user", "message": {"content": prompt}}) + "\n"
        process = subprocess.Popen(argv, cwd=workspace, env=env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        output: dict[str, bytes | bool] = {"stdout": b"", "stderr": b"", "overflow": False}

        def drain(name: str, stream) -> None:
            chunks = []
            size = 0
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                if size < MAX_OUTPUT:
                    keep = chunk[:MAX_OUTPUT - size]
                    chunks.append(keep)
                    size += len(keep)
                else:
                    keep = b""
                if size >= MAX_OUTPUT and len(chunk) > len(keep):
                    output["overflow"] = True
            output[name] = b"".join(chunks)

        def send_input() -> None:
            try:
                process.stdin.write(payload.encode())
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass

        readers = [threading.Thread(target=drain, args=(name, getattr(process, name)), daemon=True)
                   for name in ("stdout", "stderr")]
        writer = threading.Thread(target=send_input, daemon=True)
        try:
            for reader in readers:
                reader.start()
            writer.start()
            process.wait(timeout=TIMEOUT)
            writer.join(timeout=2)
            _terminate_process_group(process.pid, grace=0.2)
            for reader in readers:
                reader.join(timeout=5)
            out = output["stdout"]
            err = output["stderr"]
            if output["overflow"]:
                raise RuntimeError("Antigravity output exceeded the configured limit.")
            if process.returncode:
                raise RuntimeError(f"Antigravity CLI failed (exit {process.returncode}).")
            try:
                events = [json.loads(line) for line in out.decode().splitlines() if line.strip()]
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("Antigravity returned malformed stream JSON.") from exc
            return _terminal_result(events)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Antigravity timed out.") from exc
        finally:
            _terminate_process_group(process.pid, grace=0.2)
            if process.poll() is None:
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()


def stream_antigravity(model: str, system_prompt: str, user_message: str) -> Generator[dict, None, None]:
    validate_model(model)
    started = time.monotonic()
    yield {"type": "progress", "input_tokens": 0, "output_tokens": 0, "elapsed": 0.0, "phase": "starting"}
    response, usage = _run(system_prompt, user_message)
    yield {"type": "done", "response": response,
           "input_tokens": int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0),
           "output_tokens": int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0),
           "thinking_tokens": int(usage.get("thinking_tokens", 0) or 0),
           "cache_read_tokens": int(usage.get("cache_read_tokens", usage.get("cached_tokens", 0)) or 0),
           "elapsed": time.monotonic() - started, "stop_reason": "stop", "cost_usd": 0.0}
