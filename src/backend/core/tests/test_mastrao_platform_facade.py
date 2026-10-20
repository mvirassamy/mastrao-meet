"""Provider-free diagnostics at the actual Platform bridge refusal boundary."""

import logging
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests
from urllib3.exceptions import ReadTimeoutError

from core import mastrao_platform_facade as facade


class PlatformFacadeDiagnosticsTests(unittest.TestCase):
    """Keep diagnosis separate from HTTP decisions and private request data."""

    def setUp(self):
        self.request = SimpleNamespace(
            session={"oidc_access_token": "private-synthetic-access-token"}
        )
        self.response = MagicMock()
        self.response.status_code = 200
        self.response.headers = {}
        self.response.iter_content.return_value = [b'{"ok":true}']
        self.session = MagicMock()
        self.session.__enter__.return_value = self.session
        self.session.request.return_value = self.response
        self.enterContext(
            patch.object(facade.requests, "Session", return_value=self.session)
        )
        self.enterContext(
            patch.object(
                facade,
                "settings",
                SimpleNamespace(MASTRAO_PLATFORM_API_BASE_URL="https://platform.test"),
            )
        )

    def call_bridge(self, **options):
        """Include private markers that must never reach a log record."""
        return facade.request_platform(
            self.request,
            "GET",
            "/api/meet/meetings/history/private-meeting-reference/",
            accepted_statuses={200},
            options={"cursor": "private_cursor", **options},
        )

    def assert_refusal_event(self, cause, stage, upstream_status, public_status=503):
        """Assert the entire closed event, not a substring of a verbose log."""
        with (
            patch("time.monotonic", side_effect=[1.0, 1.125]),
            self.assertLogs(facade.__name__, level="WARNING") as captured,
            self.assertRaises(facade.PlatformFacadeError) as refused,
        ):
            self.call_bridge()
        self.assertEqual(refused.exception.status, public_status)
        self.assertEqual(len(captured.records), 1)
        record = captured.records[0]
        self.assertEqual(
            record.msg,
            "meet_platform_facade_refused cause_code=%s stage=%s "
            "elapsed_ms=%d upstream_status=%s",
        )
        self.assertEqual(record.args, (cause, stage, 125, upstream_status))
        self.assertIsNone(record.exc_info)
        self.assertIsNone(record.stack_info)
        self.session.request.assert_called_once()
        return refused.exception

    def test_timeouts_and_transport_errors_before_response_headers(self):
        for error, cause in [
            (requests.ConnectTimeout("private-url-token"), "connect_timeout"),
            (requests.ReadTimeout("private-url-token"), "read_timeout"),
            (requests.Timeout("private-url-token"), "timeout"),
            (requests.ConnectionError("private-url-token"), "transport_error"),
        ]:
            with self.subTest(cause=cause):
                self.session.request.reset_mock()
                self.session.request.side_effect = error
                refused = self.assert_refusal_event(
                    cause, "await_response_headers", None
                )
                self.assertIs(refused.__cause__, error)
                self.response.close.assert_not_called()

    def test_wrapped_read_timeout_during_response_body_is_distinct(self):
        timeout = ReadTimeoutError(None, "private-url", "private-content")
        error = requests.ConnectionError(timeout)
        self.response.iter_content.side_effect = error
        self.assert_refusal_event("read_timeout", "read_response_body", 200)
        self.response.close.assert_called_once_with()

    def test_http_refusals_keep_the_original_status_and_session_policy(self):
        for upstream, public in [
            (401, 401),
            (404, 404),
            (409, 409),
            (422, 422),
            (503, 503),
            (429, 503),
        ]:
            with self.subTest(upstream=upstream):
                self.session.request.reset_mock()
                self.response.close.reset_mock()
                self.request.session["oidc_access_token"] = (
                    "private-synthetic-access-token"
                )
                self.response.status_code = upstream
                self.assert_refusal_event(
                    "upstream_http_refused", "check_http_status", upstream, public
                )
                self.assertEqual(
                    "oidc_access_token" in self.request.session, upstream != 401
                )
                self.response.iter_content.assert_not_called()
                self.response.close.assert_called_once_with()

    def test_invalid_json_and_non_object_json_have_distinct_causes(self):
        for body, cause, stage in [
            (b"private-invalid-json", "invalid_json", "decode_json"),
            (b"\xff", "invalid_json", "decode_json"),
            (b'["private-content"]', "invalid_object", "check_response_shape"),
        ]:
            with self.subTest(cause=cause, body=body):
                self.session.request.reset_mock()
                self.response.close.reset_mock()
                self.response.iter_content.return_value = [body]
                self.assert_refusal_event(cause, stage, 200)
                self.response.close.assert_called_once_with()

    def test_declared_and_streamed_bounds_are_distinguishable(self):
        for length, cause in [
            ("-1", "invalid_content_length"),
            ("9" * 21, "invalid_content_length"),
            (str(facade.MAX_PLATFORM_RESPONSE_BYTES + 1), "response_too_large"),
        ]:
            with self.subTest(length=length):
                self.session.request.reset_mock()
                self.response.close.reset_mock()
                self.response.headers = {"content-length": length}
                self.assert_refusal_event(cause, "check_response_size", 200)
                self.response.iter_content.assert_not_called()
                self.response.close.assert_called_once_with()
        self.session.request.reset_mock()
        self.response.close.reset_mock()
        self.response.headers = {}
        self.response.iter_content.return_value = [
            b"x" * (facade.MAX_PLATFORM_RESPONSE_BYTES + 1)
        ]
        self.assert_refusal_event("response_too_large", "read_response_body", 200)
        self.response.close.assert_called_once_with()

    def test_success_is_quiet_and_preserves_the_transport_options(self):
        with self.assertNoLogs(facade.__name__, level="WARNING"):
            self.assertEqual(self.call_bridge(), ({"ok": True}, 200))
        self.session.request.assert_called_once_with(
            "GET",
            "https://platform.test/api/meet/meetings/history/private-meeting-reference/?cursor=private_cursor",
            headers={"authorization": "Bearer private-synthetic-access-token"},
            timeout=5,
            allow_redirects=False,
            stream=True,
        )
        self.assertFalse(self.session.trust_env)
        self.response.close.assert_called_once_with()

    def test_custom_call_budgets_and_http_allowlists_are_preserved(self):
        self.response.status_code = 429
        with (
            self.assertLogs(facade.__name__, level="WARNING"),
            self.assertRaises(facade.PlatformFacadeError) as refused,
        ):
            self.call_bridge(timeout=(5, 20), error_statuses={429})
        self.assertEqual(refused.exception.status, 429)
        self.assertEqual(self.session.request.call_args.kwargs["timeout"], (5, 20))
        self.assertIn("oidc_access_token", self.request.session)
        self.response.close.assert_called_once_with()

    def test_logger_failure_never_replaces_http_refusal_or_skips_close(self):
        self.response.status_code = 401
        logger = logging.getLogger(facade.__name__)
        with (
            patch.object(
                logger, "warning", side_effect=RuntimeError("private-message")
            ),
            self.assertRaises(facade.PlatformFacadeError) as refused,
        ):
            self.call_bridge()
        self.assertEqual(refused.exception.status, 401)
        self.assertNotIn("oidc_access_token", self.request.session)
        self.response.close.assert_called_once_with()

    def test_logger_failure_never_replaces_body_or_transport_refusal(self):
        logger = logging.getLogger(facade.__name__)
        with patch.object(
            logger, "warning", side_effect=RuntimeError("private-message")
        ):
            self.response.iter_content.return_value = [b"private-invalid-json"]
            with self.assertRaises(facade.PlatformFacadeError) as body_refusal:
                self.call_bridge()
            self.assertEqual(body_refusal.exception.status, 503)
            self.response.close.assert_called_once_with()
            error = requests.ReadTimeout("private-token")
            self.session.request.side_effect = error
            with self.assertRaises(facade.PlatformFacadeError) as transport_refusal:
                self.call_bridge()
            self.assertIs(transport_refusal.exception.__cause__, error)

    def test_missing_token_is_still_refused_before_transport(self):
        self.request.session.clear()
        with self.assertRaises(facade.PlatformFacadeError) as refused:
            self.call_bridge()
        self.assertEqual(refused.exception.status, 401)
        self.session.request.assert_not_called()


class DelayedPlatformPeer(BaseHTTPRequestHandler):
    """A finite loopback peer exercising real Requests timeout wrapping."""

    def log_message(self, *_args):
        """Keep synthetic request paths out of test logs."""

    def do_GET(self):
        """Delay either headers or body beyond the test's existing call budget."""
        if self.path == "/headers":
            time.sleep(0.2)
        self.send_response(200)
        self.send_header("Content-Length", "11")
        self.end_headers()
        self.wfile.flush()
        if self.path == "/body":
            time.sleep(0.2)
        try:
            self.wfile.write(b'{"ok":true}')
        except (BrokenPipeError, ConnectionResetError):
            return


class PlatformFacadeRealTransportTests(unittest.TestCase):
    """Exercise sockets and response.close with the pinned Requests transport."""

    def test_header_and_streamed_body_read_timeouts(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), DelayedPlatformPeer)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        close = requests.Response.close
        try:
            for path, stage, status, closed in [
                ("/headers", "await_response_headers", None, 0),
                ("/body", "read_response_body", 200, 1),
            ]:
                with self.subTest(stage=stage):
                    request = SimpleNamespace(
                        session={"oidc_access_token": "private-synthetic-access-token"}
                    )
                    with (
                        patch.object(
                            facade,
                            "settings",
                            SimpleNamespace(MASTRAO_PLATFORM_API_BASE_URL=origin),
                        ),
                        patch.object(
                            requests.Response, "close", autospec=True, side_effect=close
                        ) as response_close,
                        self.assertLogs(facade.__name__, level="WARNING") as captured,
                        self.assertRaises(facade.PlatformFacadeError) as refused,
                    ):
                        facade.request_platform(
                            request,
                            "GET",
                            path,
                            accepted_statuses={200},
                            options={"timeout": 0.05},
                        )
                    self.assertEqual(refused.exception.status, 503)
                    self.assertEqual(len(captured.records), 1)
                    cause, actual_stage, elapsed, upstream = captured.records[0].args
                    self.assertEqual(
                        (cause, actual_stage, upstream), ("read_timeout", stage, status)
                    )
                    self.assertGreaterEqual(elapsed, 0)
                    self.assertIsNone(captured.records[0].exc_info)
                    self.assertEqual(response_close.call_count, closed)
                    self.assertIn("oidc_access_token", request.session)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
