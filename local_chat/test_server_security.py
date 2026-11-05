import json
import unittest
from unittest.mock import patch

from local_chat import server


class BrowserSessionTests(unittest.TestCase):
    def test_programming_tool_allowlist_is_narrow(self):
        names = {item["function"]["name"] for item in server.PROGRAMMING_TOOL_DEFINITIONS}
        self.assertEqual(names, {"create_project", "create_venv", "write_python"})
        self.assertNotIn("run_python", names)
        self.assertNotIn("install_packages", names)

    def test_chat_approval_waiter_is_bound_to_session(self):
        approval_id = "approval-session-test"
        with server.CHAT_APPROVAL_WAITERS_LOCK:
            server.CHAT_APPROVAL_WAITERS.pop(approval_id, None)
        waiter = server._register_chat_approval(approval_id, "a" * 40)
        self.assertIsNotNone(waiter)
        self.assertFalse(server._resolve_chat_approval(approval_id, "b" * 40, {"ok": True}))
        self.assertTrue(server._resolve_chat_approval(approval_id, "a" * 40, {"ok": True}))
        self.assertTrue(waiter["event"].is_set())
        self.assertEqual(waiter["result"], {"ok": True})
        with server.CHAT_APPROVAL_WAITERS_LOCK:
            server.CHAT_APPROVAL_WAITERS.pop(approval_id, None)

    def test_tool_loop_sends_approved_result_back_to_ollama(self):
        import io

        first = {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "create_project", "arguments": {"name": "demo"}}}
        ]}}
        second = {"message": {"role": "assistant", "content": "پروژه ساخته شد."}, "done": True}

        class Response:
            def __init__(self, value):
                self.value = json.dumps(value).encode("utf-8")
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return None
            def read(self):
                return self.value

        class HandlerStub:
            browser_session_id = "s" * 40
            def __init__(self):
                self.wfile = io.BytesIO()
                self.headers_sent = []
                self.close_connection = False
            def send_response(self, status):
                self.status = status
            def send_header(self, name, value):
                self.headers_sent.append((name, value))
            def end_headers(self):
                return None

        handler = HandlerStub()
        prepared = {"approval_id": "tool-approval-1", "expires_in": 300,
                    "plan": {"action": "create_project", "project": "demo", "path": "workspace/demo"}}
        with patch("local_chat.server.prepare_action", return_value=prepared), \
             patch("local_chat.server._register_chat_approval", return_value=object()), \
             patch("local_chat.server._wait_for_chat_approval", return_value={"ok": True, "path": "workspace/demo"}), \
             patch("local_chat.server.urlopen", side_effect=[Response(first), Response(second)]) as model_call:
            server.ChatHandler._chat_with_approved_tools(
                handler, {"model": "test-model", "messages": [{"role": "system", "content": "programming"}],
                          "options": {"num_predict": 128}}
            )
        requests = [json.loads(call.args[0].data) for call in model_call.call_args_list]
        self.assertEqual(requests[0]["tools"], server.PROGRAMMING_TOOL_DEFINITIONS)
        self.assertFalse(requests[0]["stream"])
        self.assertEqual(requests[1]["messages"][-1]["role"], "tool")
        self.assertIn('"ok": true', requests[1]["messages"][-1]["content"])
        frames = [json.loads(line) for line in handler.wfile.getvalue().splitlines()]
        self.assertEqual(frames[0]["approval_required"]["approval_id"], "tool-approval-1")
        self.assertTrue(frames[-1]["done"])


    def setUp(self):
        self.sessions_patch = patch.dict(server.BROWSER_SESSIONS, {}, clear=True)
        self.sessions_patch.start()

    def tearDown(self):
        self.sessions_patch.stop()

    def test_session_uses_separate_random_id_and_csrf_token(self):
        session_id, csrf = server._new_browser_session()
        self.assertNotEqual(session_id, csrf)
        self.assertGreaterEqual(len(session_id), 32)
        self.assertGreaterEqual(len(csrf), 32)
        self.assertEqual(server._browser_session(session_id)["csrf"], csrf)

    def test_expired_session_is_rejected(self):
        with patch.object(server.time, "monotonic", side_effect=[100, 100 + server.BROWSER_SESSION_TTL + 1]):
            session_id, _ = server._new_browser_session()
            self.assertIsNone(server._browser_session(session_id))

    def _handler_stub(self, headers):
        class Stub:
            def __init__(self):
                self.headers = headers
                self.sent = []

            def _send_json(self, status, payload):
                self.sent.append((status, payload))

        return Stub()

    def test_same_origin_request_with_csrf_is_accepted(self):
        session_id, csrf = server._new_browser_session()
        stub = self._handler_stub({
            "Host": "127.0.0.1:8000",
            "Origin": "http://127.0.0.1:8000",
            "Cookie": server.BROWSER_SESSION_COOKIE + "=" + session_id,
            "X-CSRF-Token": csrf,
        })
        self.assertTrue(server.ChatHandler._validate_browser_post(stub))
        self.assertEqual(stub.browser_session_id, session_id)
        self.assertEqual(stub.sent, [])

    def test_foreign_origin_is_rejected(self):
        session_id, csrf = server._new_browser_session()
        stub = self._handler_stub({
            "Host": "127.0.0.1:8000",
            "Origin": "http://evil.example",
            "Cookie": server.BROWSER_SESSION_COOKIE + "=" + session_id,
            "X-CSRF-Token": csrf,
        })
        self.assertFalse(server.ChatHandler._validate_browser_post(stub))
        self.assertEqual(stub.sent[0][0], 403)

    def test_missing_or_wrong_csrf_is_rejected(self):
        session_id, _ = server._new_browser_session()
        stub = self._handler_stub({
            "Host": "127.0.0.1:8000",
            "Origin": "http://127.0.0.1:8000",
            "Cookie": server.BROWSER_SESSION_COOKIE + "=" + session_id,
            "X-CSRF-Token": "wrong-token",
        })
        self.assertFalse(server.ChatHandler._validate_browser_post(stub))
        self.assertEqual(stub.sent[0][0], 403)


if __name__ == "__main__":
    unittest.main()
