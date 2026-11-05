import unittest
from unittest.mock import patch

from local_chat import server


class BrowserSessionTests(unittest.TestCase):
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
