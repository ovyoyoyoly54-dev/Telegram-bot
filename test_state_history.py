"""Tests for per-user state isolation and bounded in-memory history."""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import state


class StateHistoryTests(unittest.TestCase):
    def setUp(self):
        for uid in (101, 202):
            state.clear_history(uid)
            state.clear_file(uid)

    def test_history_is_isolated_by_user_and_returned_as_copy(self):
        state.remember(101, "private A", "answer A")
        state.remember(202, "private B", "answer B")
        a = state.get_history(101)
        a[0]["content"] = "tampered"
        self.assertEqual(state.get_history(202)[0]["content"], "private B")
        self.assertEqual(state.get_history(101)[0]["content"], "private A")

    def test_history_keeps_only_complete_bounded_pairs(self):
        with patch.object(state.config, "HISTORY_LIMIT", 4), patch.object(state.config, "HISTORY_MSG_CHARS", 100):
            for i in range(5):
                state.remember(101, f"q{i}", f"a{i}")
        history = state.get_history(101)
        self.assertEqual(len(history), 4)
        self.assertEqual([m["content"] for m in history], ["q3", "a3", "q4", "a4"])

    def test_expired_history_is_removed(self):
        state.clear_history(101)
        with patch.object(state.config, "HISTORY_TTL_HOURS", 1), patch.object(state.time, "monotonic", return_value=100):
            state.remember(101, "question", "answer")
        with patch.object(state.config, "HISTORY_TTL_HOURS", 1), patch.object(state.time, "monotonic", return_value=3701):
            self.assertEqual(state.get_history(101), [])
            self.assertEqual(state.cleanup_expired(), 0)

    def test_history_redacts_credentials(self):
        state.clear_history(101)
        with patch.object(state.config, "OPENROUTER_API_KEY_1", "router-secret"):
            state.remember(
                101,
                "OPENROUTER_API_KEY_1=router-secret password=hunter2",
                "Bearer router-secret",
            )

        stored = " ".join(item["content"] for item in state.get_history(101))
        self.assertNotIn("router-secret", stored)
        self.assertNotIn("hunter2", stored)
        self.assertIn("[REDACTED]", stored)

    def test_file_ttl_expiry_and_cleanup(self):
        with patch.object(state, "DOC_TTL_SECONDS", 60), patch.object(state.time, "monotonic", return_value=100):
            state.set_file(101, "a.txt", "secret A")
        with patch.object(state, "DOC_TTL_SECONDS", 60), patch.object(state.time, "monotonic", return_value=161):
            self.assertIsNone(state.get_file(101))
            self.assertEqual(state.cleanup_expired(), 0)
        with patch.object(state, "DOC_TTL_SECONDS", 60), patch.object(state.time, "monotonic", return_value=200):
            state.set_file(101, "a.txt", "secret A")
        with patch.object(state, "DOC_TTL_SECONDS", 60), patch.object(state.time, "monotonic", return_value=261):
            self.assertEqual(state.cleanup_expired(), 1)
            self.assertIsNone(state.get_file(101))
            self.assertIsNone(state.get_file(202))


if __name__ == "__main__":
    unittest.main()
