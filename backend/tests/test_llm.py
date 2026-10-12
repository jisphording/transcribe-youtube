import json
import os
import unittest
from unittest.mock import MagicMock, patch

import claude
import main
import openrouter
from models import BatchOptions, TranscriptRequest


MODEL = "openrouter:deepseek/deepseek-v4.1-flash"


def completion(content='{"summary":"A summary","topics":[],"transcript":"Cleaned"}', reason="stop"):
    return [
        ": keepalive",
        "data: " + json.dumps({"choices": [{"delta": {"reasoning": "ignored"}}]}),
        "data: " + json.dumps({"choices": [{"delta": {"content": content}}]}),
        "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": reason}]}),
        "data: " + json.dumps({"usage": {"prompt_tokens": 101, "completion_tokens": 23, "cost": 0.0007}}),
        "data: [DONE]",
    ]


class OpenRouterTests(unittest.TestCase):
    def stream(self, lines, status=200):
        response = MagicMock(status_code=status)
        response.iter_lines.return_value = iter(lines)
        context = MagicMock()
        context.__enter__.return_value = response
        return patch.object(openrouter.httpx, "stream", return_value=context)

    def test_stream_contract_and_budget_request(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}), self.stream(completion()) as request:
            updates = list(openrouter.stream_openrouter(MODEL, "system", "input"))
        body = request.call_args.kwargs["json"]
        self.assertEqual(body["model"], "deepseek/deepseek-v4.1-flash")
        self.assertEqual(body["reasoning"], {"enabled": False})
        self.assertEqual(body["provider"], {"sort": "price", "require_parameters": True})
        self.assertEqual(updates[0]["type"], "progress")
        done = updates[-1]
        self.assertEqual(done["type"], "done")
        self.assertEqual(done["input_tokens"], 101)
        self.assertEqual(done["output_tokens"], 23)
        self.assertEqual(done["cost_usd"], 0.0007)
        self.assertNotIn("ignored", done["response"])

    def test_incomplete_streams_fail_before_json_repair_can_save_a_partial_note(self):
        for lines in (completion(reason="length"), completion()[:3] + ["data: [DONE]"],
                      ['data: {"error":{"code":502}}'], completion(content="")):
            with self.subTest(lines=lines), patch.dict(os.environ, {"OPENROUTER_API_KEY": "test"}), self.stream(lines):
                with self.assertRaises(RuntimeError):
                    list(openrouter.stream_openrouter(MODEL, "system", "input"))

    def test_http_errors_are_actionable_and_do_not_echo_credentials(self):
        for status, hint in ((401, "Check OPENROUTER_API_KEY"), (402, "credits"), (429, "Rate limit")):
            with self.subTest(status=status), patch.dict(os.environ, {"OPENROUTER_API_KEY": "secret-value"}), self.stream([], status):
                with self.assertRaisesRegex(RuntimeError, hint) as error:
                    list(openrouter.stream_openrouter(MODEL, "system", "input"))
                self.assertNotIn("secret-value", str(error.exception))

    def test_missing_provider_keys_fail_only_when_that_provider_is_used(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "OPENROUTER_API_KEY"):
                list(openrouter.stream_openrouter(MODEL, "system", "input"))
            with self.assertRaisesRegex(RuntimeError, "ANTHROPIC_API_KEY"):
                list(claude.stream_claude(claude.HAIKU, "system", "input"))
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test"}, clear=True), self.stream(completion()):
            self.assertEqual(list(openrouter.stream_openrouter(MODEL, "system", "input"))[-1]["type"], "done")

    def test_unknown_openrouter_model_never_falls_back_to_claude(self):
        with self.assertRaisesRegex(ValueError, "Unsupported OpenRouter"):
            main._resolve_llm("openrouter:unknown/model", claude.HAIKU)

    def test_routing_for_every_source_and_output_mode_uses_openrouter_cost(self):
        for source in ("youtube", "podcast", "web"):
            for options in ({}, {"extended_summary": True, "include_transcript": False},
                            {"focus_topic": "cost", "focus_include_extended": True, "include_transcript": False}):
                with self.subTest(source=source, options=options):
                    req = TranscriptRequest(url="https://example.com", model=MODEL, extended_model=MODEL, **options)
                    metadata = {"title": "Title", "channel": "Channel", "show": "Show", "url": req.url,
                                "site": "Site", "authors": [], "published": "", "description": ""}
                    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test"}, clear=True), self.stream(completion()), \
                         patch.object(main, "stream_claude") as anthropic_stream, \
                         patch.object(main, "build_obsidian_note", return_value=("note.md", "note")):
                        events = [json.loads(e) for e in main._run_claude_and_note(req, metadata, "text", False, source, "", 4)]
                    anthropic_stream.assert_not_called()
                    self.assertEqual(events[-1]["stage"], "done")
                    billed = next(e for e in events if e["stage"] == "claude_done")
                    self.assertEqual(billed["cost_usd"], 0.0007)

    def test_cost_preview_uses_curated_prices_and_claude_long_prompt_tier(self):
        self.assertAlmostEqual(main._estimate_cost(MODEL, 1_000_000, 1_000_000), 1.50)
        self.assertAlmostEqual(main._estimate_cost(claude.HAIKU, 100_000, 1000), 0.0105)
        self.assertAlmostEqual(main._estimate_cost(claude.HAIKU, 100_001, 1000), 0.0525005)
        cloud = main._estimate_item_cost(3600, BatchOptions(model=MODEL))
        claude_cost = main._estimate_item_cost(3600, BatchOptions(model=claude.SONNET))
        self.assertLess(cloud, claude_cost)
        self.assertEqual(main._batch_model(BatchOptions(model=MODEL)), (MODEL, False))
        self.assertEqual(main._batch_model(BatchOptions(extended_summary=True, extended_model=MODEL)), (MODEL, True))

    def test_previous_claude_ids_upgrade(self):
        for old, current in (("claude-haiku-4-5", claude.HAIKU), ("claude-haiku-4-5-20251001", claude.HAIKU),
                             ("claude-sonnet-5", claude.SONNET), ("claude-sonnet-4-6", claude.SONNET),
                             ("claude-opus-4-7", claude.OPUS)):
            self.assertEqual(main._resolve_llm(old, claude.HAIKU), current)

    def test_latest_haiku_sends_effort_and_current_output_limit(self):
        client = MagicMock()
        client.messages.stream.return_value.__enter__.return_value = [
            type('Event', (), {'type': 'content_block_delta', 'delta': type('Delta', (), {'type': 'text_delta', 'text': '{}'})()})(),
        ]
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test'}), patch.object(claude.anthropic, 'Anthropic', return_value=client):
            done = list(claude.stream_claude('claude-haiku-4-5', 'system', 'input', 'low'))[-1]
        self.assertEqual(client.messages.stream.call_args.kwargs['model'], 'claude-haiku-5-5')
        self.assertEqual(client.messages.stream.call_args.kwargs['max_tokens'], 128000)
        self.assertEqual(client.messages.stream.call_args.kwargs['output_config'], {'effort': 'low'})
        self.assertEqual(done['response'], '{}')

    def test_chunked_openrouter_cost_adds_each_reported_charge(self):
        req = TranscriptRequest(url='https://example.com', model=MODEL)
        metadata = {'title': 'Title', 'channel': 'Channel'}
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test'}), \
             patch.object(main, '_chunk_plan', return_value=(True, 10)), \
             patch.object(main, '_split_transcript', return_value=['first', 'second']), \
             patch.object(main, 'build_obsidian_note', return_value=('note.md', 'note')):
            # A fresh stream is needed for each chunk and the final summary.
            with patch.object(main, '_stream_llm', side_effect=lambda *args: iter([{
                'type': 'done', 'response': '{"heading":"Part","transcript":"Clean","summary":"Summary"}',
                'input_tokens': 101, 'output_tokens': 23, 'elapsed': 0.1, 'stop_reason': 'stop', 'cost_usd': 0.001,
            }])):
                events = [json.loads(e) for e in main._run_claude_and_note(req, metadata, 'text', False, 'youtube', '', 4)]
        self.assertEqual(events[-1]['stage'], 'done')
        self.assertEqual(next(e for e in events if e['stage'] == 'claude_done')['cost_usd'], 0.003)


if __name__ == "__main__":
    unittest.main()
