"""Mocked tests for Presenton API routing, diagnostics, and fallback behavior."""

import unittest
from unittest.mock import patch

import config
import docs
import presenton


class FakeResponse:
    def __init__(self, status=200, body=None, content=b"pptx"):
        self.status_code = status
        self._body = body if body is not None else {"path": "https://api.presenton.ai/static/deck.pptx", "title": "Deck"}
        self.content = content
        self.text = str(self._body)

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class PresentonTests(unittest.TestCase):
    def setUp(self):
        self.config = (
            patch.object(config, "PRESENTON_API_MODE", "cloud"),
            patch.object(config, "PRESENTON_API_KEY", "sk-presenton-test-secret"),
            patch.object(config, "PRESENTON_BASE_URL", "https://api.presenton.ai"),
            patch.object(config, "PRESENTON_TIMEOUT", 20),
        )
        for item in self.config:
            item.start()
            self.addCleanup(item.stop)

    def test_cloud_success_uses_cloud_endpoint_and_downloads_pptx(self):
        with patch.object(presenton.requests, "post", return_value=FakeResponse()) as post, \
                patch.object(presenton.requests, "get", return_value=FakeResponse(content=b"pptx-bytes")) as get:
            name, data, summary = presenton.generate_presentation("Mavzu")
        self.assertEqual(post.call_args.args[0], "https://api.presenton.ai/api/v3/presentation/generate")
        self.assertEqual(post.call_args.kwargs["json"]["standard_template"], "general")
        self.assertEqual(get.call_args.args[0], "https://api.presenton.ai/static/deck.pptx")
        self.assertIn("Bearer sk-presenton-test-secret", get.call_args.kwargs["headers"]["Authorization"])
        self.assertEqual((name, data), ("Deck.pptx", b"pptx-bytes"))
        self.assertIn("8 slayd", summary)

    def test_wrong_api_key_has_safe_401_diagnostics(self):
        response = FakeResponse(401, {"detail": "Invalid sk-presenton-test-secret Bearer abc"})
        with patch.object(presenton.requests, "post", return_value=response):
            with self.assertRaises(presenton.PresentonError) as caught:
                presenton.generate_presentation("Mavzu")
        self.assertEqual(caught.exception.kind, "AuthenticationError")
        self.assertEqual(caught.exception.status, 401)
        self.assertNotIn("secret", caught.exception.detail)
        self.assertNotIn("Bearer abc", caught.exception.detail)

    def test_wrong_endpoint_is_a_404(self):
        with patch.object(presenton.requests, "post", return_value=FakeResponse(404, {"detail": "route missing"})):
            with self.assertRaises(presenton.PresentonError) as caught:
                presenton.generate_presentation("Mavzu")
        self.assertEqual((caught.exception.kind, caught.exception.status), ("EndpointNotFound", 404))

    def test_timeout_is_reported_separately(self):
        with patch.object(presenton.requests, "post", side_effect=presenton.requests.Timeout()):
            with self.assertRaises(presenton.PresentonError) as caught:
                presenton.generate_presentation("Mavzu")
        self.assertEqual(caught.exception.kind, "Timeout")

    def test_malformed_base_url_is_reported_without_request(self):
        config.PRESENTON_BASE_URL = "not a URL"
        with patch.object(presenton.requests, "post") as post:
            with self.assertRaises(presenton.PresentonError) as caught:
                presenton.generate_presentation("Mavzu")
        self.assertEqual(caught.exception.kind, "InvalidURL")
        post.assert_not_called()

    def test_self_hosted_uses_v1_payload_without_cloud_auth(self):
        config.PRESENTON_API_MODE = "self_hosted"
        config.PRESENTON_BASE_URL = "http://presenton.internal:5000"
        response = FakeResponse(body={"path": "/static/deck.pptx", "title": "Deck"})
        with patch.object(presenton.requests, "post", return_value=response) as post, \
                patch.object(presenton.requests, "get", return_value=FakeResponse()) as get:
            presenton.generate_presentation("Mavzu")
        self.assertEqual(post.call_args.args[0], "http://presenton.internal:5000/api/v1/ppt/presentation/generate")
        self.assertEqual(post.call_args.kwargs["json"]["template"], "general")
        self.assertNotIn("standard_template", post.call_args.kwargs["json"])
        self.assertNotIn("Authorization", post.call_args.kwargs["headers"])
        self.assertNotIn("Authorization", get.call_args.kwargs["headers"])

    def test_presenton_failure_logs_cause_and_keeps_fallback(self):
        fallback = docs.GenerateResult("fallback.pptx", b"pptx", "fallback")
        error = presenton.PresentonError("unauthorized", kind="AuthenticationError", status=403, detail="key rejected")
        with patch.object(config, "PRESENTON_ENABLED", True), \
                patch.object(presenton, "generate_presentation", side_effect=error), \
                patch.object(docs, "_generate_deck", return_value=fallback), \
                self.assertLogs("docs", level="WARNING") as captured:
            result = docs.generate([], "10 slaydli taqdimot")
        self.assertIs(result, fallback)
        line = "\n".join(captured.output)
        self.assertIn("AuthenticationError", line)
        self.assertIn("403", line)
        self.assertIn("key rejected", line)


if __name__ == "__main__":
    unittest.main()
