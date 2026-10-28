"""Regression coverage for provider limits, errors and finite failover."""

import logging
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")

import config
import llm
import requests


class _Response:
    def __init__(self, status, data=None):
        self.status_code = status
        self._data = data or {}

    def json(self):
        return self._data


def _ok(text="ok"):
    return _Response(200, {"choices": [{"message": {"content": text}}]})


class HttpErrorTests(unittest.TestCase):
    def test_http_statuses_have_actionable_messages(self):
        cases = {
            402: "kredit",
            401: "kalit",
            403: "ruxsat",
            429: "limit",
            500: "vaqtincha",
        }
        for status, expected in cases.items():
            with self.subTest(status=status), self.assertRaises(llm.LLMError) as caught:
                llm._request_json(_Response(status), "OpenRouter")
            self.assertIn(expected, str(caught.exception).lower())

    def test_success_response_is_parsed(self):
        self.assertEqual(llm._request_json(_Response(200, {"ok": True}), "mock"), {"ok": True})

    def test_http_errors_are_distinct_and_keep_sanitized_body(self):
        cases = {
            401: "unauthorized", 403: "forbidden", 404: "model_not_found",
            429: "rate_limit", 503: "server_error",
        }
        for status, kind in cases.items():
            response = _Response(status, {"error": {"status": "SAMPLE", "message": "model unavailable", "debug": "AIza-secret-value"}})
            with self.subTest(status=status), self.assertRaises(llm.ProviderAPIError) as caught:
                llm._request_json(response, "Gemini", model="gemini-example")
            self.assertEqual(caught.exception.kind, kind)
            self.assertIn("model unavailable", caught.exception.detail)
            self.assertNotIn("AIza-secret-value", caught.exception.detail)

    def test_gemini_v1beta_url_and_access_listing(self):
        with patch.object(config, "GEMINI_API_VERSION", "v1beta"), \
             patch.object(config, "GEMINI_API_KEY", "gemini-test-key"), \
             patch.object(llm, "_session") as session:
            session.get.return_value = _Response(200, {"models": [
                {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/embedding-test", "supportedGenerationMethods": ["embedContent"]},
            ]})
            models = llm.check_gemini_access()
        self.assertEqual(models, ["gemini-2.5-flash"])
        self.assertEqual(session.get.call_args.args[0], "https://generativelanguage.googleapis.com/v1beta/models")
        self.assertEqual(session.get.call_args.kwargs["headers"]["x-goog-api-key"], "gemini-test-key")
        self.assertEqual(llm._gemini_model_url("gemini-2.5-flash"),
                         "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent")

    def test_gemini_404_uses_only_configured_and_listed_fallback(self):
        missing = _Response(404, {"error": {"message": "model not found"}})
        success = _Response(200, {"candidates": [{"content": {"parts": [{"text": "fallback answer"}]}}]})
        with patch.multiple(config, GEMINI_API_KEY="gemini-test-key", GEMINI_MODEL="gemini-original",
                            GEMINI_FALLBACK_MODELS=["gemini-available"]), \
             patch.object(llm, "_post", side_effect=[missing, success]) as post, \
             patch.object(llm, "check_gemini_access", return_value=["gemini-available"]):
            answer = llm._chat_gemini([{"role": "user", "content": "hello"}], "system")
        self.assertEqual(answer, "fallback answer")
        self.assertEqual(post.call_count, 2)
        self.assertIn("gemini-available:generateContent", post.call_args.args[0])

    def test_gemini_404_without_configured_fallback_is_not_retried(self):
        with patch.multiple(config, GEMINI_API_KEY="gemini-test-key", GEMINI_MODEL="gemini-original",
                            GEMINI_FALLBACK_MODELS=[]), \
             patch.object(llm, "_post", return_value=_Response(404, {"error": {"message": "unknown model"}})) as post:
            with self.assertRaises(llm.ProviderAPIError) as caught:
                llm._chat_gemini([{"role": "user", "content": "hello"}], "system")
        self.assertEqual(caught.exception.kind, "model_not_found")
        post.assert_called_once()

    def test_rate_limit_is_not_retried_by_http_transport(self):
        with patch.object(llm._session, "post", return_value=_Response(429)) as post:
            self.assertEqual(llm._post("https://provider.invalid").status_code, 429)
        post.assert_called_once()

    def test_timeout_and_5xx_retries_are_bounded(self):
        with patch.object(llm._session, "post", side_effect=requests.Timeout):
            with self.assertRaises(llm.LLMError):
                llm._post("https://provider.invalid", timeout=1)
            self.assertEqual(llm._session.post.call_count, 2)

        with patch.object(llm._session, "post", side_effect=[_Response(503), _Response(503)]):
            response = llm._post("https://provider.invalid", timeout=1)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(llm._session.post.call_count, 2)


class ProviderFallbackTests(unittest.TestCase):
    def setUp(self):
        self.config = patch.multiple(
            config,
            GROQ_API_KEY="groq-test-secret",
            OPENROUTER_API_KEYS=["router-one-secret", "router-two-secret"],
            OPENROUTER_API_KEY="router-one-secret",
            OPENROUTER_MODEL="openrouter/free",
            OPENROUTER_VISION_MODEL="",
            GEMINI_API_KEY="",
            CHAT_PROVIDER="openrouter",
            FILE_PROVIDER="openrouter",
            CHAT_MAX_TOKENS=600,
            PRESENTATION_MAX_TOKENS=1200,
            GROQ_CONTEXT_TOKENS=12000,
            OPENROUTER_CONTEXT_TOKENS=12000,
            GEMINI_CONTEXT_TOKENS=12000,
        )
        self.config.start()

    def tearDown(self):
        self.config.stop()

    def test_provider_defaults_allow_openrouter_or_gemini_without_groq(self):
        self.assertEqual(config._provider_defaults("", ["router-key"], ""), ("openrouter", "openrouter"))
        self.assertEqual(config._provider_defaults("", ["router-key"], "", "groq", ""), ("openrouter", "openrouter"))
        self.assertEqual(config._provider_defaults("", [], "gemini-key"), ("gemini", "gemini"))
        self.assertEqual(config._provider_defaults("groq-key", [], ""), ("groq", "groq"))
        self.assertEqual(config._provider_defaults("", ["router-key"], "gemini-key"), ("openrouter", "openrouter"))

    def test_missing_all_providers_fails_with_clear_configuration_error(self):
        with self.assertRaises(SystemExit) as caught:
            config._provider_defaults("", [], "")
        self.assertIn("GEMINI_API_KEY", str(caught.exception))

    def test_openrouter_tries_next_key_once_after_402(self):
        with patch.object(llm, "_post", side_effect=[_Response(402), _ok()]) as post:
            result = llm._chat_openrouter([], "system", max_tokens=100)
        self.assertEqual(result, "ok")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args_list[0].kwargs["headers"]["Authorization"], "Bearer router-one-secret")
        self.assertEqual(post.call_args_list[1].kwargs["headers"]["Authorization"], "Bearer router-two-secret")

    def test_openrouter_key_names_are_deduplicated_and_trimmed(self):
        with patch.multiple(
            config,
            OPENROUTER_API_KEYS=[" key-a ", "key-b", "key-a"],
            OPENROUTER_API_KEY_1="key-a",
            OPENROUTER_API_KEY_2="key-c",
            OPENROUTER_API_KEY_LEGACY="key-b",
            OPENROUTER_API_KEY="key-a",
        ):
            self.assertEqual(llm._get_openrouter_keys(), ["key-a", "key-b", "key-c"])

    def test_vision_routing_requires_an_explicit_openrouter_vision_model(self):
        with patch.multiple(config, OPENROUTER_API_KEYS=["router"],
                            OPENROUTER_VISION_MODEL="", GROQ_API_KEY="",
                            GEMINI_API_KEY=""):
            self.assertNotIn("openrouter", llm._available_providers(has_image=True))
        with patch.multiple(config, OPENROUTER_API_KEYS=["router"],
                            OPENROUTER_VISION_MODEL="vision-model", GROQ_API_KEY="",
                            GEMINI_API_KEY=""):
            self.assertIn("openrouter", llm._available_providers(has_image=True))

    def test_all_openrouter_keys_fail_then_configured_groq_succeeds(self):
        with patch.object(llm, "_post", side_effect=[_Response(402), _Response(402)]), \
             patch.dict(llm._PROVIDERS, {"groq": lambda *a, **kw: "groq fallback"}):
            self.assertEqual(llm.chat([], "hello", "system"), "groq fallback")

    def test_all_providers_fail_and_keep_final_error_without_unbounded_retry(self):
        call_count = {"openrouter": 0, "groq": 0}

        def fail(name):
            def _f(*args, **kwargs):
                call_count[name] += 1
                raise llm.LLMError(name + " unavailable")
            return _f

        with patch.dict(llm._PROVIDERS, {"openrouter": fail("openrouter"), "groq": fail("groq")}):
            with self.assertRaises(llm.LLMError) as caught:
                llm.chat([], "hello", "system")
        self.assertEqual(call_count, {"openrouter": 1, "groq": 1})
        self.assertIn("groq unavailable", str(caught.exception))

    def test_chat_multi_is_only_a_single_chat_fallback_call(self):
        with patch.object(llm, "chat", return_value="ok") as chat:
            self.assertEqual(llm.chat_multi([], "hello", "system"), "ok")
        chat.assert_called_once()

    def test_chat_with_search_uses_supplied_results_without_searching_again(self):
        supplied = "1. Example\nExample description\nManba: https://example.com"
        with patch.object(llm, "web_search") as search, \
             patch.object(llm, "chat", return_value="answer with source") as chat:
            answer = llm.chat_with_search([], "question", "system", search_results=supplied)
        self.assertEqual(answer, "answer with source")
        search.assert_not_called()
        self.assertIn(supplied, chat.call_args.args[2])

    def test_chat_with_search_empty_supplied_results_does_not_retry_search(self):
        with patch.object(llm, "web_search") as search, patch.object(llm, "chat") as chat:
            answer = llm.chat_with_search([], "question", "system", search_results="")
        self.assertIn("natijasi olinmadi", answer)
        search.assert_not_called()
        chat.assert_not_called()

    def test_brave_api_error_returns_no_results(self):
        with patch.object(llm, "_get", side_effect=llm.requests.RequestException("private detail")):
            self.assertEqual(llm.web_search("query"), "")

    def test_chat_rejects_empty_provider_then_uses_next_configured_provider(self):
        with patch.dict(llm._PROVIDERS, {"openrouter": lambda *a, **kw: "", "groq": lambda *a, **kw: "recovered"}), \
             patch.object(config, "CHAT_PROVIDER", "openrouter"):
            self.assertEqual(llm.chat([], "hello", "system"), "recovered")

    def test_max_tokens_are_capped_by_task_configuration(self):
        captured = []

        def capture(*args, **kwargs):
            captured.append(kwargs["max_tokens"])
            return "ok"

        with patch.dict(llm._PROVIDERS, {"openrouter": capture}):
            llm.chat([], "hello", "system", max_tokens=9000)
            llm.chat([], "12 slaydli prezentatsiya", "system", task="file", max_tokens=9000)
        self.assertEqual(captured, [600, 1200])

    def test_oversized_history_is_trimmed_and_context_caps_output(self):
        captured = {}

        def capture(messages, system, **kwargs):
            captured["messages"] = messages
            captured["max_tokens"] = kwargs["max_tokens"]
            return "ok"

        with patch.object(config, "OPENROUTER_CONTEXT_TOKENS", 1024), \
             patch.dict(llm._PROVIDERS, {"openrouter": capture}):
            llm.chat(
                [{"role": "user", "content": "old " * 1000},
                 {"role": "assistant", "content": "history " * 1000}],
                "current " * 360,
                "system",
                max_tokens=600,
            )
        self.assertEqual(len(captured["messages"]), 1)
        self.assertLess(captured["max_tokens"], 600)

    def test_gemini_response_is_normalized_and_receives_limited_tokens(self):
        response = _Response(200, {
            "candidates": [{"content": {"parts": [{"text": "Gemini answer"}]}}]
        })
        with patch.object(config, "GEMINI_API_KEY", "gemini-test-key"), \
             patch.object(llm, "_post", return_value=response) as post:
            answer = llm._chat_gemini(
                [{"role": "user", "content": "hello"}], "system",
                max_tokens=150,
            )
        self.assertEqual(answer, "Gemini answer")
        self.assertEqual(post.call_args.kwargs["json"]["generationConfig"]["maxOutputTokens"], 150)

    def test_secrets_are_never_written_to_logs(self):
        records = []
        class Handler(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler = Handler()
        logger = logging.getLogger("llm")
        logger.addHandler(handler)
        try:
            with patch.object(llm, "_post", side_effect=[_Response(401), _Response(401)]):
                with patch.dict(llm._PROVIDERS, {"groq": lambda *a, **kw: "ok"}):
                    llm.chat([], "hello", "system")
        finally:
            logger.removeHandler(handler)
        joined = "\n".join(records)
        self.assertNotIn("router-one-secret", joined)
        self.assertNotIn("router-two-secret", joined)


if __name__ == "__main__":
    unittest.main()
