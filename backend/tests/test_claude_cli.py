import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import claude_cli
import main
from models import TranscriptRequest

FAKE_CLI = """#!{python}
import json, os, sys, time
mode = os.environ.get("FAKE_MODE", "ok")
record = {{"argv": sys.argv[1:], "env": dict(os.environ), "stdin": sys.stdin.read(), "cwd": os.getcwd()}}
with open(os.environ["FAKE_RECORD"], "w") as f:
    json.dump(record, f)
with open(os.environ["FAKE_RECORD"] + ".calls", "a") as f:
    f.write(json.dumps({{"argv": sys.argv[1:], "stdin_chars": len(record["stdin"]), "segment": "TRANSCRIPT SEGMENT" in record["stdin"]}}) + "\\n")
if mode == "hang":
    with open(os.environ["FAKE_RECORD"] + ".pid", "w") as f:
        f.write(str(os.getpid()))
    time.sleep(60)
print(json.dumps({{"type": "system", "subtype": "init", "model": "claude-haiku-5-5", "apiKeySource": "none"}}))
if mode == "error":
    print(json.dumps({{"type": "result", "subtype": "success", "is_error": True, "result": "Not logged in · Please run /login",
                      "api_error_status": None, "stop_reason": None}}))
    sys.exit(1)
if mode == "refusal":
    print(json.dumps({{"type": "result", "subtype": "success", "is_error": True, "result": "can't help",
                      "stop_reason": "refusal", "usage": {{"input_tokens": 5, "output_tokens": 1}}}}))
    sys.exit(1)
if mode == "limit":
    print(json.dumps({{"type": "rate_limit_event", "rate_limit_info": {{"status": "rejected", "resetsAt": 1900000000}}}}))
    print(json.dumps({{"type": "result", "subtype": "success", "is_error": True, "result": "Usage limit reached"}}))
    sys.exit(1)
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "stop_reason": "end_turn",
                  "result": json.dumps({{"summary": "A summary", "topics": [], "transcript": "Cleaned"}}),
                  "total_cost_usd": 0.5,
                  "usage": {{"input_tokens": 10, "output_tokens": 7, "cache_read_input_tokens": 3, "cache_creation_input_tokens": 2}}}}))
"""

TRANSCRIPT = "UNIQUE-TRANSCRIPT-MARKER spoken words of the video"


class ClaudeCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.exe = Path(self.tmp.name) / "claude"
        self.exe.write_text(FAKE_CLI.format(python=sys.executable))
        self.exe.chmod(0o755)
        self.record = Path(self.tmp.name) / "record.json"
        self.env = {"CLAUDE_CLI_PATH": str(self.exe), "FAKE_RECORD": str(self.record),
                    "ANTHROPIC_API_KEY": "sk-test-secret", "ANTHROPIC_AUTH_TOKEN": "token-secret",
                    "ANTHROPIC_BASE_URL": "https://example.invalid", "OPENROUTER_API_KEY": "or-secret",
                    "CLAUDE_CODE_USE_BEDROCK": "1"}

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, mode: str = "ok", **kwargs):
        with patch.dict(os.environ, {**self.env, "FAKE_MODE": mode}):
            return list(claude_cli.stream_claude_cli("claude-cli:haiku", "SYSTEM PROMPT", TRANSCRIPT, kwargs.get("effort", "low")))

    def recorded(self) -> dict:
        return json.loads(self.record.read_text())

    def test_argv_contract_no_bare_and_transcript_only_on_stdin(self):
        updates = self.run_cli()
        rec = self.recorded()
        argv = rec["argv"]
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertNotIn("--bare", argv)
        for flag in ("-p", "--no-session-persistence", "--strict-mcp-config", "--disable-slash-commands", "--verbose"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--model") + 1], "haiku")
        self.assertEqual(argv[argv.index("--effort") + 1], "low")
        self.assertEqual(argv[argv.index("--system-prompt") + 1], "SYSTEM PROMPT")
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "")
        self.assertFalse(any("UNIQUE-TRANSCRIPT-MARKER" in arg for arg in argv))
        self.assertEqual(rec["stdin"], TRANSCRIPT)
        self.assertIn("media-claude-cli-", rec["cwd"])
        self.assertFalse(Path(rec["cwd"]).exists())
        done = updates[-1]
        self.assertEqual(updates[0]["type"], "progress")
        self.assertEqual(done["type"], "done")
        self.assertEqual(json.loads(done["response"])["summary"], "A summary")
        self.assertEqual(done["cost_usd"], 0.0)
        self.assertEqual((done["input_tokens"], done["output_tokens"], done["cache_read_tokens"]), (15, 7, 3))

    def test_child_env_has_no_anthropic_auth_variables(self):
        self.run_cli()
        env = self.recorded()["env"]
        for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "OPENROUTER_API_KEY", "CLAUDE_CODE_USE_BEDROCK"):
            self.assertNotIn(key, env)
        self.assertNotIn("sk-test-secret", json.dumps(env))
        self.assertIn("PATH", env)

    def test_is_error_maps_to_actionable_error(self):
        with self.assertRaisesRegex(RuntimeError, "claude auth login"):
            self.run_cli("error")
        with self.assertRaisesRegex(RuntimeError, "usage limit reached \\(resets"):
            self.run_cli("limit")

    def test_only_blocking_rate_limit_status_counts_as_usage_limit(self):
        warn = [{"type": "rate_limit_event", "rate_limit_info": {"status": "allowed_warning", "resetsAt": 1900000000}}]
        missing = {"result": "There's an issue with the selected model", "api_error_status": 404}
        self.assertIn("model is unavailable", claude_cli._error_message(warn, missing, 1))
        self.assertIn("unavailable", claude_cli._error_message([], {"result": "context limit exceeded", "api_error_status": 404}, 1))
        odd = [{"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": "soon"}}]
        self.assertEqual(claude_cli._error_message(odd, None, 1), "Claude subscription usage limit reached; retry after the limit resets.")

    def test_refusal_becomes_refusal_stop_reason(self):
        self.assertEqual(self.run_cli("refusal")[-1]["stop_reason"], "refusal")

    def test_is_error_becomes_error_event_in_pipeline(self):
        req = TranscriptRequest(url="https://youtu.be/x", model="claude-cli:haiku")
        with patch.dict(os.environ, {**self.env, "FAKE_MODE": "error"}), \
             patch.object(main, "stream_claude") as anthropic_stream:
            events = [json.loads(e) for e in main._run_claude_and_note(req, {"title": "T", "channel": "C", "url": req.url}, "text", False, "youtube", "", 4)]
        anthropic_stream.assert_not_called()
        self.assertEqual(events[-1]["stage"], "error")
        self.assertIn("claude auth login", events[-1]["message"])

    def test_timeout_kills_process_group(self):
        with patch.object(claude_cli, "TIMEOUT", 1):
            started = time.monotonic()
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                self.run_cli("hang")
        self.assertLess(time.monotonic() - started, 15)
        pid = int(Path(str(self.record) + ".pid").read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_missing_executable_and_invalid_effort_are_clear_errors(self):
        with patch.dict(os.environ, {"CLAUDE_CLI_PATH": ""}):
            with self.assertRaisesRegex(RuntimeError, "CLAUDE_CLI_PATH"):
                claude_cli._run("claude-cli:haiku", "low", "s", "u")
        with self.assertRaisesRegex(ValueError, "effort"):
            claude_cli._run("claude-cli:haiku", "bogus", "s", "u")

    def test_unknown_model_never_calls_paid_claude(self):
        with patch.object(main, "stream_claude") as anthropic_stream:
            with self.assertRaisesRegex(ValueError, "Unsupported Claude subscription model"):
                main._resolve_llm("claude-cli:made-up", main.DEFAULT_MODEL)
            req = TranscriptRequest(url="https://youtu.be/x", model="claude-cli:made-up")
            with self.assertRaises(ValueError):
                list(main._run_claude_and_note(req, {"title": "T", "channel": "C", "url": req.url}, "text", False, "youtube", "", 4))
        anthropic_stream.assert_not_called()

    def test_pipeline_routes_to_cli_with_subscription_cost_kind(self):
        req = TranscriptRequest(url="https://youtu.be/x", model="claude-cli:haiku")
        with patch.dict(os.environ, self.env), patch.object(main, "stream_claude") as anthropic_stream, \
             patch.object(main, "build_obsidian_note", return_value=("note.md", "note")) as build:
            events = [json.loads(e) for e in main._run_claude_and_note(req, {"title": "T", "channel": "C", "url": req.url}, "text", False, "youtube", "", 4)]
        anthropic_stream.assert_not_called()
        self.assertEqual(events[-1]["stage"], "done")
        billed = next(e for e in events if e["stage"] == "claude_done")
        self.assertEqual((billed["cost_usd"], billed["cost_kind"]), (0.0, "subscription_quota"))
        settings = build.call_args.kwargs["import_settings"]
        self.assertEqual((settings["selected_model"], settings["model"]), ("claude-cli:haiku", "claude-cli:haiku"))
        self.assertEqual(self.recorded()["argv"][self.recorded()["argv"].index("--effort") + 1], "low")

    def calls(self) -> list[dict]:
        path = Path(str(self.record) + ".calls")
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_limit_error_carries_reset_time(self):
        with self.assertRaises(claude_cli.ClaudeCliLimitError) as ctx:
            self.run_cli("limit")
        self.assertEqual(ctx.exception.resets_at, 1900000000)

    def test_chunked_extended_import_calls_cli_once_per_segment(self):
        segment = ("word " * 2_000).strip()
        transcript = "\n\n".join([segment] * 4)
        n_segments = len(main._split_transcript(transcript, main._EXTENDED_CHUNK_CHARS))
        self.assertGreater(n_segments, 1)
        req = TranscriptRequest(url="https://youtu.be/x", model="claude-cli:haiku", extended_model="claude-cli:haiku",
                                extended_summary=True)
        with patch.dict(os.environ, self.env), patch.object(main, "stream_claude") as anthropic_stream, \
             patch.object(main.openrouter, "stream_openrouter") as openrouter_stream, \
             patch.object(main, "build_obsidian_note", return_value=("note.md", "note")):
            events = [json.loads(e) for e in main._run_claude_and_note(req, {"title": "T", "channel": "C", "url": req.url}, transcript, False, "youtube", "", 4)]
        anthropic_stream.assert_not_called()
        openrouter_stream.assert_not_called()
        self.assertEqual(events[-1]["stage"], "done")
        calls = self.calls()
        segment_calls = [c for c in calls if c["segment"]]
        self.assertEqual(len(segment_calls), n_segments)
        self.assertEqual(len(calls), n_segments + 1)  # plus the executive summary over segment summaries
        self.assertTrue(all(c["argv"][c["argv"].index("--effort") + 1] == "medium" for c in calls))
        self.assertTrue(all(c["stdin_chars"] <= main._EXTENDED_CHUNK_CHARS + 2_000 for c in calls))

    def test_limit_error_is_a_blocked_error_event_without_paid_fallback(self):
        req = TranscriptRequest(url="https://youtu.be/x", model="claude-cli:haiku")
        with patch.dict(os.environ, {**self.env, "FAKE_MODE": "limit"}), \
             patch.object(main, "stream_claude") as anthropic_stream, \
             patch.object(main.openrouter, "stream_openrouter") as openrouter_stream:
            events = [json.loads(e) for e in main._run_claude_and_note(req, {"title": "T", "channel": "C", "url": req.url}, "text", False, "youtube", "", 4)]
        anthropic_stream.assert_not_called()
        openrouter_stream.assert_not_called()
        self.assertEqual(len(self.calls()), 1)
        last = events[-1]
        self.assertEqual((last["stage"], last["blocked"], last["block_kind"], last["resets_at"]),
                         ("error", True, "usage_limit", 1900000000))
        self.assertIn("usage limit reached", last["message"])

    def test_chunk_plan_and_cost(self):
        self.assertEqual(main._chunk_plan("claude-cli:sonnet", "youtube", False, 250_000, 0, 0, False), (False, claude_cli.MAX_TRANSCRIPT_CHARS))
        self.assertTrue(main._chunk_plan("claude-cli:sonnet", "youtube", False, 350_000, 0, 0, False)[0])
        self.assertEqual(main._estimate_cost("claude-cli:opus", 1_000_000, 1_000_000), 0.0)


if __name__ == "__main__":
    unittest.main()
