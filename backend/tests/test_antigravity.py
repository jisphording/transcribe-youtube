import json
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import antigravity


class AntigravityTests(unittest.TestCase):
    def test_model_is_curated_and_unknown_prefix_rejected(self):
        self.assertTrue(antigravity.is_antigravity("antigravity:gemini-3.8-flash-medium"))
        with self.assertRaises(ValueError):
            antigravity.validate_model("antigravity:made-up")

    def test_envelope_parsing_uses_terminal_result_and_usage(self):
        response, usage = antigravity._terminal_result([
            {"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": "partial"}},
            {"event": "result", "result": {"status": "SUCCESS", "response": '{"summary":"ok"}',
             "usage": {"input_tokens": 4, "output_tokens": 2, "thinking_tokens": 1, "cache_read_tokens": 3}}},
        ])
        self.assertEqual(json.loads(response), {"summary": "ok"})
        self.assertEqual(usage["cache_read_tokens"], 3)

    def test_missing_or_failed_terminal_result_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "without a terminal"):
            antigravity._terminal_result([])
        with self.assertRaisesRegex(RuntimeError, "did not complete"):
            antigravity._terminal_result([{"event": "result", "result": {"status": "ERROR"}}])

    def test_quota_errors_are_actionable_and_do_not_expose_cli_payload(self):
        with self.assertRaisesRegex(RuntimeError, "quota or rate limit"):
            antigravity._terminal_result([{"event": "result", "result": {
                "status": "ERROR", "error": "RESOURCE_EXHAUSTED private diagnostic"}}])

    @patch.object(antigravity.os, "uname")
    @patch.object(antigravity.subprocess, "Popen")
    def test_argv_stdin_environment_and_isolation(self, popen, uname):
        uname.return_value.sysname = "Darwin"
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "agy"
            exe.write_text("fixture")
            captured = {}
            class Child:
                pid = 123
                returncode = 0
                stdout = io.BytesIO(b'{"event":"result","result":{"status":"SUCCESS","response":"{\\"summary\\":\\"ok\\"}","usage":{}}}\n')
                stderr = io.BytesIO()
                class Input(io.BytesIO):
                    def close(self):
                        captured["data"] = self.getvalue()
                        super().close()
                stdin = Input()
                def wait(self, timeout):
                    captured["timeout"] = timeout
                    return 0
                def poll(self): return 0
            popen.return_value = Child()
            with patch.dict(os.environ, {"ANTIGRAVITY_CLI_PATH": str(exe), "ANTIGRAVITY_TIMEOUT_SECONDS": "30", "ANTHROPIC_API_KEY": "secret", "GEMINI_API_KEY": "secret"}, clear=False):
                response, _ = antigravity._run("system", "transcript")
            argv, kwargs = popen.call_args
            self.assertEqual(response, '{"summary":"ok"}')
            self.assertIn("/usr/bin/sandbox-exec", argv[0][0])
            self.assertIn("--agent", argv[0])
            self.assertNotIn("ANTHROPIC_API_KEY", kwargs["env"])
            self.assertNotIn("GEMINI_API_KEY", kwargs["env"])
            payload = json.loads(captured["data"].decode())
            self.assertIn("system", payload["message"]["content"])
            self.assertIn("transcript", payload["message"]["content"])
            self.assertTrue(kwargs["start_new_session"])
            self.assertEqual(kwargs["cwd"].name, "workspace")

    def test_provider_yields_subscription_cost_zero(self):
        with patch.object(antigravity, "_run", return_value=('{"summary":"ok"}', {"input_tokens": 10, "output_tokens": 3, "thinking_tokens": 2, "cache_read_tokens": 1})):
            updates = list(antigravity.stream_antigravity(antigravity.MODEL_ID, "system", "source"))
        self.assertEqual(updates[-1]["cost_usd"], 0.0)
        self.assertEqual(updates[-1]["thinking_tokens"], 2)
        self.assertEqual(updates[-1]["cache_read_tokens"], 1)

    @patch.object(antigravity.os, "killpg")
    @patch.object(antigravity.os, "uname")
    @patch.object(antigravity.subprocess, "Popen")
    def test_timeout_terminates_and_reaps_process_group(self, popen, uname, killpg):
        uname.return_value.sysname = "Darwin"
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "agy"
            exe.write_text("fixture")
            class Child:
                pid = 44
                returncode = None
                stdout = io.BytesIO()
                stderr = io.BytesIO()
                stdin = io.BytesIO()
                waits = 0
                def wait(self, timeout):
                    self.waits += 1
                    if self.waits == 1:
                        raise antigravity.subprocess.TimeoutExpired("agy", timeout)
                    self.returncode = 0
                    return 0
                def poll(self): return self.returncode
            child = Child()
            popen.return_value = child
            with patch.dict(os.environ, {"ANTIGRAVITY_CLI_PATH": str(exe)}, clear=False), \
                    patch.object(antigravity, "TIMEOUT", 1):
                with self.assertRaisesRegex(RuntimeError, "timed out"):
                    antigravity._run("system", "source")
            self.assertTrue(any(call.args == (44, antigravity.signal.SIGTERM) for call in killpg.call_args_list))
            self.assertTrue(any(call.args == (44, antigravity.signal.SIGKILL) for call in killpg.call_args_list))
            self.assertEqual(child.waits, 2)

    def test_process_group_cleanup_escalates_after_deadline(self):
        calls = []
        def killpg(pid, sig):
            calls.append((pid, sig))
        with patch.object(antigravity.os, 'killpg', side_effect=killpg), \
             patch.object(antigravity.time, 'monotonic', side_effect=[0, 1]), \
             patch.object(antigravity.time, 'sleep'):
            antigravity._terminate_process_group(51, grace=0.5)
        self.assertEqual(calls, [(51, antigravity.signal.SIGTERM), (51, antigravity.signal.SIGKILL)])

    @patch.object(antigravity.os, "uname")
    @patch.object(antigravity.subprocess, "Popen")
    def test_malformed_child_output_is_rejected(self, popen, uname):
        uname.return_value.sysname = "Darwin"
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "agy"
            exe.write_text("fixture")
            class Child:
                pid = 123
                returncode = 0
                stdout = io.BytesIO(b"not-json\\n")
                stderr = io.BytesIO()
                stdin = io.BytesIO()
                def wait(self, timeout): return 0
                def poll(self): return 0
            popen.return_value = Child()
            with patch.dict(os.environ, {"ANTIGRAVITY_CLI_PATH": str(exe)}, clear=False), \
                    patch.object(antigravity, "_terminate_process_group"):
                with self.assertRaisesRegex(RuntimeError, "malformed stream JSON"):
                    antigravity._run("system", "source")

    @patch.object(antigravity.os, "uname")
    @patch.object(antigravity.subprocess, "Popen")
    def test_stderr_pressure_is_bounded_and_reported(self, popen, uname):
        uname.return_value.sysname = "Darwin"
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "agy"
            exe.write_text("fixture")
            class Child:
                pid = 123
                returncode = 0
                stdout = io.BytesIO()
                stderr = io.BytesIO(b"x" * 1024)
                stdin = io.BytesIO()
                def wait(self, timeout): return 0
                def poll(self): return 0
            popen.return_value = Child()
            with patch.dict(os.environ, {"ANTIGRAVITY_CLI_PATH": str(exe)}, clear=False), \
                    patch.object(antigravity, "MAX_OUTPUT", 128):
                with self.assertRaisesRegex(RuntimeError, "output exceeded"):
                    antigravity._run("system", "source")


if __name__ == "__main__":
    unittest.main()
