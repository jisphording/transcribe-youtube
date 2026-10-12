import subprocess
import unittest
from unittest.mock import patch

import whisper


class StartupTests(unittest.TestCase):
    def test_kickstart_failure_reports_launchctl_error_without_polling(self):
        failure = subprocess.CompletedProcess([], 3, '', 'Could not find service com.whisper.server')
        with patch.object(whisper, 'is_available', return_value=False), \
             patch.object(whisper.subprocess, 'run', return_value=failure), \
             patch.object(whisper.time, 'sleep') as sleep, \
             patch.object(whisper.Path, 'is_file', return_value=False):
            with self.assertRaisesRegex(whisper.WhisperUnavailableError, 'Could not find service'):
                whisper.start_server(timeout=0)
            sleep.assert_not_called()

    def test_custom_server_does_not_launch_local_default(self):
        with patch.object(whisper, 'is_available', return_value=False), \
             patch.object(whisper, 'start_server') as start, \
             patch.object(whisper, '_download_audio', side_effect=AssertionError('must not download')):
            with self.assertRaises(whisper.WhisperUnavailableError):
                whisper.transcribe_url('https://example.com/audio.mp3', server_url='http://remote:2022')
            start.assert_not_called()

    def test_unregistered_installed_agent_is_bootstrapped(self):
        fail = subprocess.CompletedProcess([], 3, '', 'service missing')
        ok = subprocess.CompletedProcess([], 0, '', '')
        with patch.object(whisper, 'is_available', side_effect=[False, True]), \
             patch.object(whisper.Path, 'is_file', return_value=True), \
             patch.object(whisper.subprocess, 'run', side_effect=[fail, ok, ok]) as run:
            self.assertTrue(whisper.start_server())
            self.assertEqual(run.call_args_list[1].args[0][1], 'bootstrap')
            self.assertEqual(run.call_args_list[2].args[0][1], 'kickstart')

    def test_cold_start_polls_until_ready_without_restarting(self):
        ok = subprocess.CompletedProcess([], 0, '', '')
        with patch.object(whisper, 'is_available', side_effect=[False, False, True]), \
             patch.object(whisper.subprocess, 'run', return_value=ok) as run, \
             patch.object(whisper.time, 'sleep'):
            self.assertTrue(whisper.start_server())
            self.assertEqual(run.call_count, 1)

    def test_timeout_includes_server_log(self):
        ok = subprocess.CompletedProcess([], 0, '', '')
        with patch.object(whisper, 'is_available', return_value=False), \
             patch.object(whisper.subprocess, 'run', return_value=ok), \
             patch.object(whisper.Path, 'read_text', return_value='failed to load model'):
            with self.assertRaisesRegex(whisper.WhisperUnavailableError, 'failed to load model'):
                whisper.start_server(timeout=0)


if __name__ == '__main__':
    unittest.main()
