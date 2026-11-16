import asyncio
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import batch_queue as queue
with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-no-network"}):
    import main
from models import BatchFolders, BatchOptions, QueueItemRequest


class QueueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db_patch = patch.object(queue, 'DB_PATH', self.root / 'queue.db')
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        queue.init_db()
        self.folders = BatchFolders(youtube=str(self.root / 'YouTube'), podcast=str(self.root / 'Podcasts'), resources=str(self.root / 'Resources'))

    async def enqueue(self, url='https://www.youtube.com/watch?v=abcdefghijk'):
        return await main.queue_single(QueueItemRequest(url=url, vault_root=str(self.root), folders=self.folders, scan_roots=[str(self.root)]))

    def test_db_connections_are_closed(self):
        queue.lane_status(); queue.list_batches(); queue.next_item(time.time())
        before = len(os.listdir('/dev/fd'))
        for _ in range(500):
            queue.lane_status()
            queue.list_batches()
            queue.next_item(time.time())
        self.assertLessEqual(len(os.listdir('/dev/fd')) - before, 5)

    def test_db_rolls_back_on_error_and_closes(self):
        conns = []
        real = queue._connect
        def spy():
            conns.append(real())
            return conns[-1]
        with patch.object(queue, '_connect', spy):
            with self.assertRaises(RuntimeError):
                with queue._db() as conn:
                    conn.execute("INSERT INTO lanes(name) VALUES ('ghost')")
                    raise RuntimeError('boom')
        with queue._db() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM lanes WHERE name='ghost'").fetchone())
        with self.assertRaises(Exception):
            conns[0].execute("SELECT 1")

    async def test_single_imports_are_immediately_queued_in_fifo_order(self):
        first = await self.enqueue()
        second = await self.enqueue('https://podcasts.apple.com/us/podcast/test/id123?i=456')
        self.assertEqual(first['state'], 'queued')
        self.assertEqual(first['items'][0]['state'], 'pending')
        self.assertEqual(queue.next_item(time.time())['batch_id'], first['id'])
        # History limits must never hide pending work.
        self.assertEqual([b['id'] for b in queue.list_batches(0)], [first['id'], second['id']])

    async def test_openrouter_model_snapshot_survives_restart_and_reaches_worker(self):
        model = 'openrouter:deepseek/deepseek-v4.1-flash'
        req = QueueItemRequest(
            url='https://www.youtube.com/watch?v=abcdefghijk',
            options=BatchOptions(model=model, extended_model=model, extended_summary=True, include_transcript=False),
            vault_root=str(self.root), folders=self.folders, scan_roots=[str(self.root)],
        )
        await main.queue_single(req)
        queue.init_db()
        item = queue.next_item(time.time())
        seen = []
        def pipeline(request, steps):
            seen.append(request)
            yield json.dumps({'stage': 'done', 'filename': 'router.md', 'content': 'summary', 'resources': []})
        with patch.object(main, '_run_youtube_pipeline', pipeline):
            result = main._process_batch_item(item, queue.get_batch(item['batch_id'], with_vault=True))
        self.assertEqual(result['state'], 'done')
        self.assertEqual(seen[0].model, model)
        self.assertEqual(seen[0].extended_model, model)
        self.assertTrue(seen[0].extended_summary)
        self.assertFalse(seen[0].include_transcript)
        self.assertEqual((self.root / 'YouTube/router.md').read_text(), 'summary')

    async def test_antigravity_options_survive_restart_retry_and_worker_dispatch(self):
        model = 'antigravity:gemini-3.8-flash-medium'
        req = QueueItemRequest(
            url='https://podcasts.apple.com/us/podcast/test/id123?i=456',
            options=BatchOptions(model=model, extended_model=model, extended_summary=True,
                                 focus_topic='safety', focus_include_extended=True,
                                 extract_resources=True, include_transcript=False),
            vault_root=str(self.root), folders=self.folders, scan_roots=[str(self.root)],
        )
        batch = await main.queue_single(req)
        queue.init_db()
        item = queue.next_item(time.time())
        queue._mark_running(item['id'])
        queue._record_result(item, {'state': 'failed', 'error': 'quota', 'network': False})
        self.assertEqual(queue.retry_failed(batch['id']), 1)
        item = queue.next_item(time.time())
        seen = []
        def pipeline(request, steps):
            seen.append(request)
            yield json.dumps({'stage': 'done', 'filename': 'agy.md', 'content': 'complete', 'resources': []})
        with patch.object(main, '_run_podcast_pipeline', pipeline):
            result = main._process_batch_item(item, queue.get_batch(item['batch_id'], with_vault=True))
        self.assertEqual(result['state'], 'done')
        self.assertEqual(seen[0].model, model)
        self.assertEqual(seen[0].extended_model, model)
        self.assertEqual(seen[0].focus_topic, 'safety')
        self.assertTrue(seen[0].focus_include_extended)
        self.assertTrue(seen[0].extract_resources)
        self.assertFalse(seen[0].include_transcript)

    async def test_quota_failure_does_not_write_or_pause_youtube_lane(self):
        model = 'antigravity:gemini-3.8-flash-medium'
        req = QueueItemRequest(url='https://www.youtube.com/watch?v=abcdefghijk',
            options=BatchOptions(model=model, extended_model=model), vault_root=str(self.root),
            folders=self.folders, scan_roots=[str(self.root)])
        batch = await main.queue_single(req)
        item = batch['items'][0]
        with patch.object(main, '_run_youtube_pipeline', return_value=iter([
                json.dumps({'stage': 'error', 'message': 'Antigravity subscription quota reached.'})])), \
             patch.object(main, '_stream_llm') as stream:
            result = main._process_batch_item(item, queue.get_batch(item['batch_id'], with_vault=True))
        stream.assert_not_called()
        self.assertEqual(result['state'], 'failed')
        self.assertFalse(result.get('blocked', False))
        self.assertFalse((self.root / 'YouTube').exists())
        queue._record_result(item, result)
        lane = queue.lane_status()['youtube']
        self.assertEqual(lane['blocked_until'], 0)
        self.assertEqual(queue.get_batch(batch['id'])['items'][0]['state'], 'failed')

    async def queue_cli_podcast(self):
        model = 'claude-cli:sonnet'
        req = QueueItemRequest(url='https://podcasts.apple.com/us/podcast/test/id123?i=456',
            options=BatchOptions(model=model, extended_model=model), vault_root=str(self.root),
            folders=self.folders, scan_roots=[str(self.root)])
        batch = await main.queue_single(req)
        return batch, batch['items'][0]

    def podcast_pipeline_with_cli_error(self, error: Exception):
        def pipeline(request, total_steps):
            yield json.dumps({'stage': 'metadata_done', 'message': 'ok', 'title': 'Episode'})
            yield from main._run_claude_and_note(request, {'title': 'Episode', 'show': 'Show', 'url': request.url},
                                                 'spoken words', False, 'podcast', '', total_steps)
        return patch.object(main, '_run_podcast_pipeline', side_effect=pipeline), \
            patch.object(main.claude_cli, 'stream_claude_cli', side_effect=error)

    async def test_usage_limit_keeps_item_pending_pauses_lane_until_reset_without_paid_fallback(self):
        batch, item = await self.queue_cli_podcast()
        reset = int(time.time()) + 7200
        pipeline, cli = self.podcast_pipeline_with_cli_error(
            main.claude_cli.ClaudeCliLimitError('Claude subscription usage limit reached; retry after the limit resets.', reset))
        with pipeline, cli as cli_stream, patch.object(main, 'stream_claude') as anthropic_stream, \
             patch.object(main.openrouter, 'stream_openrouter') as openrouter_stream, \
             patch.object(main.antigravity, 'stream_antigravity') as antigravity_stream:
            result = main._process_batch_item(item, queue.get_batch(item['batch_id'], with_vault=True))
        self.assertEqual(cli_stream.call_count, 1)
        anthropic_stream.assert_not_called()
        openrouter_stream.assert_not_called()
        antigravity_stream.assert_not_called()
        self.assertTrue(result['blocked'])
        self.assertEqual((result['block_kind'], result['resets_at']), ('usage_limit', reset))
        self.assertFalse((self.root / 'Podcasts').exists())
        queue._record_result(item, result)
        lane = queue.lane_status()['podcast']
        self.assertTrue(lane['blocked'])
        self.assertEqual(lane['blocked_until'], reset)
        self.assertEqual(lane['backoff_level'], 0)
        self.assertIn('subscription usage limit', lane['block_reason'])
        self.assertEqual(queue.get_batch(batch['id'])['items'][0]['state'], 'pending')
        self.assertIsNone(queue.next_item(time.time()))
        self.assertEqual(queue.lane_status()['youtube']['blocked_until'], 0)

    async def test_usage_limit_without_reset_uses_backoff_and_skips_youtube_cookie_retry(self):
        model = 'claude-cli:haiku'
        req = QueueItemRequest(url='https://www.youtube.com/watch?v=abcdefghijk',
            options=BatchOptions(model=model, extended_model=model), vault_root=str(self.root),
            folders=self.folders, scan_roots=[str(self.root)])
        batch = await main.queue_single(req)
        item = batch['items'][0]
        error = json.dumps({'stage': 'error', 'message': 'Claude subscription usage limit reached.', 'blocked': True,
                            'block_kind': 'usage_limit', 'resets_at': None})
        with patch.object(main, '_run_youtube_pipeline', side_effect=lambda *a: iter([error])) as pipeline:
            result = main._process_batch_item(item, queue.get_batch(item['batch_id'], with_vault=True))
        self.assertEqual(pipeline.call_count, 1)
        before = time.time()
        queue._record_result(item, result)
        lane = queue.lane_status()['youtube']
        self.assertAlmostEqual(lane['blocked_until'] - before, queue.BLOCK_BACKOFF_SECONDS[0], delta=5)
        self.assertEqual(lane['backoff_level'], 1)
        self.assertEqual(queue.get_batch(batch['id'])['items'][0]['state'], 'pending')

    async def test_non_limit_cli_error_fails_only_that_item(self):
        batch, item = await self.queue_cli_podcast()
        second = await self.enqueue('https://podcasts.apple.com/us/podcast/test/id123?i=789')
        pipeline, cli = self.podcast_pipeline_with_cli_error(RuntimeError('Claude CLI failed (exit 1); check `claude auth status` and retry.'))
        with pipeline, cli, patch.object(main, 'stream_claude') as anthropic_stream:
            result = main._process_batch_item(item, queue.get_batch(item['batch_id'], with_vault=True))
        anthropic_stream.assert_not_called()
        self.assertEqual(result['state'], 'failed')
        self.assertFalse(result['blocked'])
        queue._record_result(item, result)
        lane = queue.lane_status()['podcast']
        self.assertFalse(lane['blocked'])
        self.assertEqual(queue.get_batch(batch['id'])['items'][0]['state'], 'failed')
        self.assertEqual(queue.get_batch(second['id'])['items'][0]['state'], 'pending')
        with patch.object(queue, '_eligible_lanes', return_value=['podcast']):
            self.assertEqual(queue.next_item(time.time())['batch_id'], second['id'])

    async def test_worker_runs_single_video_and_podcast_serially_and_writes_notes(self):
        first = await self.enqueue()
        second = await self.enqueue('https://podcasts.apple.com/us/podcast/test/id123?i=456')
        started = []
        def pipeline(req, steps):
            started.append(req.url)
            yield json.dumps({'stage': 'metadata_done', 'title': 'Imported title', 'duration_seconds': 120})
            yield json.dumps({'stage': 'done', 'filename': f'{len(started)}.md', 'content': 'saved note', 'resources': []})
        with patch.object(main, '_run_youtube_pipeline', pipeline), patch.object(main, '_run_podcast_pipeline', pipeline), \
             patch.object(queue, 'YOUTUBE_PAUSE_SECONDS', (0, 0)), patch.object(queue, 'PODCAST_PAUSE_SECONDS', (0, 0)):
            worker = asyncio.create_task(queue.run_worker(main._process_batch_item))
            try:
                async with asyncio.timeout(5):
                    while queue.get_batch(second['id'])['state'] != 'done':
                        await asyncio.sleep(0.01)
            finally:
                worker.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await worker
        self.assertEqual(started, [first['source_url'], second['source_url']])
        self.assertEqual((self.root / 'YouTube/1.md').read_text(), 'saved note')
        self.assertEqual((self.root / 'Podcasts/2.md').read_text(), 'saved note')
        self.assertEqual(queue.get_batch(first['id'])['title'], 'Imported title')

    async def test_clear_preserves_running_work_notes_and_hourly_cap(self):
        completed = await self.enqueue()
        item = completed['items'][0]
        self.assertTrue(queue._mark_running(item['id']))
        saved = self.root / 'saved.md'
        saved.write_text('keep me')
        queue._record_result(item, {'state': 'done', 'note_path': str(saved), 'network': False})
        running = await self.enqueue('https://www.youtube.com/watch?v=lmnopqrstuv')
        self.assertTrue(queue._mark_running(running['items'][0]['id']))
        queue.cancel(running['id'])
        self.assertEqual(queue.remove_finished(running['id']), 0)
        self.assertEqual(queue.remove_finished(), 1)
        self.assertEqual(saved.read_text(), 'keep me')
        self.assertEqual(queue.youtube_done_last_hour(), 1)
        self.assertEqual([b['id'] for b in queue.list_batches()], [running['id']])

    async def test_failed_history_can_be_retried_and_cancelled_work_is_not_claimed(self):
        batch = await self.enqueue()
        item = batch['items'][0]
        queue._mark_running(item['id'])
        queue._record_result(item, {'state': 'failed', 'error': 'missing model', 'network': False})
        self.assertEqual(queue.retry_failed(batch['id']), 1)
        self.assertIsNone(queue.get_batch(batch['id'])['items'][0]['error'])
        queue.cancel(batch['id'])
        self.assertFalse(queue._mark_running(item['id']))
        self.assertEqual(queue.remove_finished(batch['id']), 1)

    async def test_restart_recovers_pending_items_but_not_cancelled_work(self):
        batch = await self.enqueue()
        queue._mark_running(batch['items'][0]['id'])
        queue.init_db()
        self.assertEqual(queue.get_batch(batch['id'])['items'][0]['state'], 'pending')
        queue._mark_running(batch['items'][0]['id'])
        queue.cancel(batch['id'])
        queue.init_db()
        self.assertEqual(queue.get_batch(batch['id'])['items'][0]['state'], 'cancelled')

    async def test_single_import_keeps_finished_livestreams_but_collections_skip_them(self):
        await self.enqueue()
        item = queue.next_item(time.time())
        collection = queue.create_batch('youtube', 'https://www.youtube.com/@chan', 'chan', {}, {
            'vault_root': str(self.root), 'folders': self.folders.model_dump(), 'scan_roots': [str(self.root)],
        }, {}, [{'url': 'https://www.youtube.com/watch?v=bcdefghijkl', 'external_id': 'bcdefghijkl', 'title': 't', 'duration_seconds': 60}])
        seen = []
        def pipeline(request, steps):
            seen.append(request.skip_live)
            yield json.dumps({'stage': 'error', 'message': 'stop'})
        with patch.object(main, '_run_youtube_pipeline', pipeline):
            main._process_batch_item(item, queue.get_batch(item['batch_id'], with_vault=True))
            other = queue.get_batch(collection, with_vault=True)
            main._process_batch_item(other['items'][0], other)
        self.assertEqual(seen, [False, True])

    def test_youtube_pipeline_imports_finished_livestream_but_not_a_running_one(self):
        def run(live_status, skip_live):
            metadata = {'title': 'Stream', 'live_status': live_status, 'duration': '1:00', 'duration_seconds': 60}
            note = iter([json.dumps({'stage': 'done', 'message': 'ok'})])
            with patch.object(main, 'get_video_metadata', return_value=metadata), \
                 patch.object(main, 'get_transcript', return_value=([{'text': 'hi', 'start': 0}], False)), \
                 patch.object(main, 'transcript_to_plain_text', return_value='hi'), \
                 patch.object(main, '_run_claude_and_note', return_value=note):
                request = main.TranscriptRequest(url='https://www.youtube.com/watch?v=abcdefghijk', skip_live=skip_live)
                return json.loads(list(main._run_youtube_pipeline(request, 4))[-1])
        self.assertEqual(run('was_live', False)['stage'], 'done')
        self.assertTrue(run('was_live', True)['skipped'])
        live = run('is_live', False)
        self.assertTrue(live['skipped'])
        self.assertIn('has not finished', live['message'])
