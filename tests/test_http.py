#!/usr/bin/env python3
"""Tests for the HTTP layer's failure handling: error descriptions and retry policy. No network."""

import email.message
import io
import os
import sys
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fetch_ms_cf as ms  # noqa: E402

BLOCK_PAGE = (b"<html><head><title>Access Denied</title><script>var x=1;</script></head>"
              b"<body><h1>Access Denied</h1><p>Reference #18.abc123</p></body></html>")


def http_error(code, reason="", body=b"", server=None):
    headers = email.message.Message()
    if server:
        headers["Server"] = server
    return urllib.error.HTTPError("https://example.test/", code, reason, headers, io.BytesIO(body))


class FakeOpener:
    """Raises (or returns) a scripted sequence of outcomes, counting calls."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.addheaders = []

    def open(self, req, timeout=None):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return io.BytesIO(outcome)


class TestDescribeHttpError(unittest.TestCase):
    def test_status_server_and_block_page_text(self):
        got = ms.describe_http_error(http_error(403, "Forbidden", BLOCK_PAGE, "AkamaiGHost"))
        self.assertEqual(got, "HTTP 403 Forbidden (server: AkamaiGHost): "
                              "Access Denied Access Denied Reference #18.abc123")

    def test_script_bodies_are_not_reported_as_text(self):
        self.assertNotIn("var x", ms.describe_http_error(http_error(403, "Forbidden", BLOCK_PAGE)))

    def test_missing_server_and_empty_body(self):
        self.assertEqual(ms.describe_http_error(http_error(404, "Not Found")),
                         "HTTP 404 Not Found (server: ?)")


class TestRetryPolicy(unittest.TestCase):
    def setUp(self):
        self._sleep = ms.time.sleep
        ms.time.sleep = lambda s: None

    def tearDown(self):
        ms.time.sleep = self._sleep

    def test_403_fails_fast_with_description(self):
        opener = FakeOpener(http_error(403, "Forbidden", BLOCK_PAGE, "AkamaiGHost"))
        with self.assertRaises(RuntimeError) as ctx:
            ms.post_search(opener, "ContributionSearch", {})
        self.assertEqual(opener.calls, 1)
        self.assertIn("HTTP 403", str(ctx.exception))
        self.assertIn("AkamaiGHost", str(ctx.exception))

    def test_asmx_fault_500_fails_fast(self):
        opener = FakeOpener(http_error(500, "Internal Server Error", b'{"Message":"Invalid JSON"}'))
        with self.assertRaises(RuntimeError) as ctx:
            ms.post_search(opener, "ContributionSearch", {})
        self.assertEqual(opener.calls, 1)
        self.assertIn("Invalid JSON", str(ctx.exception))

    def test_gateway_error_is_retried(self):
        opener = FakeOpener(http_error(503, "Service Unavailable"), b'{"d": "[]"}')
        self.assertEqual(ms.post_search(opener, "ContributionSearch", {}), b'{"d": "[]"}')
        self.assertEqual(opener.calls, 2)

    def test_connection_errors_retry_then_give_up(self):
        opener = FakeOpener(*[urllib.error.URLError("reset")] * 3)
        with self.assertRaises(urllib.error.URLError):
            ms.post_search(opener, "ContributionSearch", {}, retries=3)
        self.assertEqual(opener.calls, 3)


class TestOpenSession(unittest.TestCase):
    def setUp(self):
        self._build = urllib.request.build_opener

    def tearDown(self):
        urllib.request.build_opener = self._build

    def _serve(self, *outcomes):
        opener = FakeOpener(*outcomes)
        urllib.request.build_opener = lambda *handlers: opener
        return opener

    def test_refusal_names_the_blocker(self):
        self._serve(http_error(403, "Forbidden", BLOCK_PAGE, "AkamaiGHost"))
        with self.assertRaises(RuntimeError) as ctx:
            ms.open_session()
        self.assertIn("portal refused the session request", str(ctx.exception))
        self.assertIn("AkamaiGHost", str(ctx.exception))

    def test_sends_browser_standard_headers(self):
        opener = self._serve(b"<html></html>")
        ms.open_session()
        names = {name for name, _ in opener.addheaders}
        self.assertEqual(names, {"User-Agent", "Accept", "Accept-Language"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
