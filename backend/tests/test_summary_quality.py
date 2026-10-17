import json
import unittest
from unittest.mock import patch

import main
from models import TranscriptRequest
from note import build_obsidian_note
from prompts import get_system_prompt


def done(result):
    return {'type': 'done', 'response': json.dumps(result), 'input_tokens': 10,
            'output_tokens': 10, 'elapsed': 1, 'stop_reason': 'stop', 'cost_usd': 0.001}


class SummaryQualityTests(unittest.TestCase):
    def test_unsupported_antigravity_model_never_falls_back_to_claude(self):
        with patch.object(main, 'resolve_model') as claude_resolver:
            with self.assertRaises(ValueError):
                main._resolve_llm('antigravity:unsupported', main.DEFAULT_MODEL)
        claude_resolver.assert_not_called()

    def test_antigravity_selection_builds_note_with_prefixed_model_metadata(self):
        model = 'antigravity:gemini-3.8-flash-medium'
        req = TranscriptRequest(url='url', model=model, extended_model=model, include_transcript=False)
        result = {'summary': 'A concise summary.', 'transcript': '', 'topics': ['test']}
        with patch.object(main, '_stream_llm', return_value=iter([done(result)])) as stream:
            events = [json.loads(event) for event in main._run_claude_and_note(
                req, {'title': 'Title', 'channel': 'Channel', 'channel_url': 'channel-url', 'url': 'url'}, 'Source text.', False, 'youtube', '', 4)]
        self.assertEqual(events[-1]['stage'], 'done')
        self.assertIn('import_selected_model: "' + model + '"', events[-1]['content'])
        self.assertIn('import_model: "' + model + '"', events[-1]['content'])
        self.assertEqual(stream.call_args.args[0], model)

    def test_extended_cloud_is_chunked_for_proportional_detail(self):
        self.assertTrue(main._chunk_plan(main.SONNET, 'youtube', True, 150000, 0, 0, False)[0])

    def test_local_extended_prompt_does_not_request_one_sentence(self):
        prompt = main._local_chunk_system(True, True, True)
        self.assertIn('40-60%', prompt)
        self.assertNotIn('1-2 sentences', prompt)
        self.assertIn('40-60%', get_system_prompt(['extended']))

    def test_resource_evidence_and_table_follow_all_main_text(self):
        metadata = {'title': 'Title', 'channel': 'Channel', 'channel_url': '', 'url': 'url'}
        _, note = build_obsidian_note(metadata, 'Executive', 'Transcript body', extended_summary='Detailed body',
                                     resources=[{'name': 'Obsidian', 'type': 'software', 'mention': 'I use Obsidian.'}])
        self.assertGreater(note.index('## Mentioned Resources'), note.index('Transcript body'))
        self.assertIn('| Resource | Type | Source mention |', note)
        self.assertIn('I use Obsidian.', note)
        self.assertIn('> ```base', note)
        self.assertIn('file.hasLink(this.file)', note)
        self.assertIn('formula.type', note)
        self.assertIn('## Executive Summary', note)

    def test_extended_length_retries_and_accounts_for_both_calls(self):
        with patch.object(main, '_stream_llm', side_effect=[iter([done({'extended_summary': 'too short'})]),
                iter([done({'extended_summary': 'detail ' * 50})])]) as stream:
            updates = list(main._stream_with_extended_target(main.SONNET, 'system', 'input', 'medium',
                                                            'source ' * 100, 'extended_summary'))
        self.assertEqual(stream.call_count, 2)
        self.assertIn('previous extended summary had 2 words', stream.call_args.args[2])
        self.assertEqual(updates[-1]['input_tokens'], 20)
        self.assertEqual(updates[-1]['cost_usd'], 0.002)

    def test_persistently_short_extended_is_not_saved(self):
        req = TranscriptRequest(url='url', extended_summary=True, include_transcript=False)
        with patch.object(main, '_stream_llm', side_effect=lambda *args: iter([done({'extended_summary': 'tiny'})])), \
             patch.object(main, 'build_obsidian_note') as build:
            events = [json.loads(e) for e in main._run_claude_and_note(
                req, {'title': 'Title', 'channel': 'Channel'}, 'source ' * 100, False, 'youtube', '', 4)]
        self.assertEqual(events[-1]['stage'], 'error')
        self.assertIn('after retry', events[-1]['message'])
        build.assert_not_called()

    def test_long_cloud_extended_preserves_last_segment_and_its_resources(self):
        chunks = ['first ' * 100, 'I use Obsidian. ' + 'last ' * 100]
        sections = [
            {'heading': 'First', 'summary': 'First point', 'extended_summary': 'first detail ' * 25},
            {'heading': 'Last', 'summary': 'Last point', 'extended_summary': 'last detail ' * 26,
             'resources': [{'name': 'Obsidian', 'type': 'software', 'mention': 'I use Obsidian.'}]},
            {'summary': 'Executive', 'topics': []},
        ]
        req = TranscriptRequest(url='url', extended_summary=True, include_transcript=False, extract_resources=True)
        with patch.object(main, '_chunk_plan', return_value=(True, 100)), \
             patch.object(main, '_split_transcript', return_value=chunks), \
             patch.object(main, '_stream_llm', side_effect=[iter([done(s)]) for s in sections]) as stream, \
             patch.object(main, 'build_obsidian_note', return_value=('note.md', 'note')) as build:
            events = [json.loads(e) for e in main._run_claude_and_note(
                req, {'title': 'Title', 'channel': 'Channel'}, '\n'.join(chunks), False, 'youtube', '', 4)]
        self.assertEqual(events[-1]['stage'], 'done')
        self.assertIn('last detail', build.call_args.kwargs['extended_summary'])
        self.assertEqual(len(build.call_args.kwargs['extended_summary'].split()), 102)
        self.assertEqual(events[-1]['resources'][0]['name'], 'Obsidian')
        self.assertIn('Last point', stream.call_args.args[2])
        self.assertEqual(next(e for e in events if e['stage'] == 'claude_done')['cost_usd'], 0.003)

    def test_resources_reject_partial_names_and_invented_evidence(self):
        resources = [
            {'name': 'Notion', 'type': 'software', 'mention': 'Notional ideas'},
            {'name': 'Obsidian', 'type': 'software', 'mention': 'Obsidian is great'},
            {'name': 'OBSIDIAN', 'type': 'software', 'mention': 'I use OBSIDIAN.'},
            {'name': 'Obsidian', 'type': 'software', 'mention': 'I use Obsidian.'},
        ]
        self.assertEqual(len(main._verified_resources(resources, 'Notional ideas. I use\nObsidian.')), 1)

    def test_local_extended_sections_are_joined_without_condensing(self):
        req = TranscriptRequest(url='url', extended_summary=True, include_transcript=False,
                                extended_model='local:test')
        results = [
            {'heading': 'First', 'notes': ['first point'], 'extended': 'first detail ' * 25},
            {'heading': 'Last', 'notes': ['last point'], 'extended': 'last detail ' * 25},
            {'summary': 'Executive', 'topics': []},
        ]
        with patch.object(main, '_chunk_plan', return_value=(True, 100)), \
             patch.object(main, '_split_transcript', return_value=['first ' * 100, 'last ' * 100]), \
             patch.object(main.local_llm, 'input_budget_chars', return_value=100000), \
             patch.object(main.local_llm, 'stream_local', side_effect=[iter([done(r)]) for r in results]) as stream, \
             patch.object(main, 'build_obsidian_note', return_value=('note.md', 'note')) as build:
            events = [json.loads(e) for e in main._run_claude_and_note(
                req, {'title': 'Title', 'channel': 'Channel'}, 'source ' * 200, False, 'youtube', '', 4)]
        self.assertEqual(events[-1]['stage'], 'done')
        extended = build.call_args.kwargs['extended_summary']
        self.assertIn('## First', extended)
        self.assertIn('## Last', extended)
        self.assertEqual(extended.count('detail'), 50)
        self.assertEqual(stream.call_count, 3)

    def test_article_resources_are_last_and_table_escapes_pipes(self):
        metadata = {'source': 'web', 'title': 'Title', 'url': 'url', 'accessed': '2026-10-13'}
        _, note = build_obsidian_note(metadata, 'Summary', '', extended_summary='Extended',
            resources=[{'name': 'Obsidian', 'type': 'software', 'mention': 'Obsidian | notes\nnext'}])
        self.assertGreater(note.index('## Mentioned Resources'), note.index('## Extended Summary'))
        self.assertIn('Obsidian \\| notes next', note)

    def test_resources_are_verified_against_original_not_metadata_or_generated_text(self):
        req = TranscriptRequest(url='url', extract_resources=True)
        metadata = {'title': 'Title', 'channel': 'Channel', 'description': 'Notion'}
        result = {'summary': 'Notion and Obsidian', 'transcript': 'I use Obsidian.', 'topics': [], 'resources': [
            {'name': 'Notion', 'type': 'software', 'mention': 'I use Notion.'},
            {'name': 'Obsidian', 'type': 'software', 'mention': 'I use Obsidian.'},
            {'name': 'Obsidian', 'type': 'software', 'mention': 'I use Obsidian.'},
        ]}
        with patch.object(main, '_stream_llm', return_value=iter([{
            'type': 'done', 'response': json.dumps(result), 'input_tokens': 10, 'output_tokens': 10,
            'elapsed': 1, 'stop_reason': 'stop',
        }])), patch.object(main, 'build_obsidian_note', return_value=('note.md', 'note')) as build:
            events = [json.loads(e) for e in main._run_claude_and_note(req, metadata, 'I use Obsidian.', False, 'youtube', '', 4)]
        self.assertEqual([r['name'] for r in events[-1]['resources']], ['Obsidian'])
        self.assertEqual(build.call_args.kwargs['resources'], events[-1]['resources'])


if __name__ == '__main__':
    unittest.main()
