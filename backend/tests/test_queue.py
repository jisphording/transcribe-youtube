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
