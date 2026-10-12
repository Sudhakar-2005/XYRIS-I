"""Brain Phase 7 — model abstraction contract tests.

ModelCall/ModelResult must have one shape across backends, the ladder must
step down on provider errors (not just quota), and no configured key may
ever appear in a result or error string.
"""
import time
import unittest
from threading import Event
from unittest.mock import patch

from core.model_api import ModelCall, ModelResult, run_model
from core.model_manager import ModelManager


class TestModelCallContract(unittest.TestCase):

    def test_success_result_shape_is_complete(self):
        with patch("core.llm_client.get_llm_provider", return_value="openai"), \
             patch("core.llm_client.get_llm_settings",
                   return_value=("http://localhost:11434", "qwen")), \
             patch("core.llm_client.call_llm",
                   return_value={"content": "hello", "tool_calls": []}):
            res = run_model(ModelCall(prompt="hi"))
        self.assertIsInstance(res, ModelResult)
        for f in ("ok", "text", "provider", "model", "latency",
                  "error", "tool_calls"):
            self.assertIn(f, res.as_dict())
        self.assertTrue(res.ok)
        self.assertEqual(res.text, "hello")
        self.assertEqual(res.provider, "openai")
        self.assertEqual(res.model, "qwen")
        self.assertGreaterEqual(res.latency, 0.0)

    def test_failure_keeps_the_same_shape_and_never_raises(self):
        with patch("core.llm_client.get_llm_provider", return_value="ollama"), \
             patch("core.llm_client.get_llm_settings",
                   return_value=("http://x", "m")), \
             patch("core.llm_client.call_llm",
                   side_effect=RuntimeError("Connection refused")):
            res = run_model(ModelCall(prompt="hi"))
        self.assertFalse(res.ok)
        self.assertEqual(res.text, "")
        self.assertEqual(res.provider, "ollama")
        self.assertIn("Connection refused", res.error)
        self.assertEqual(res.as_dict()["ok"], False)

    def test_pre_cancelled_call_never_dispatches(self):
        ev = Event()
        ev.set()
        with patch("core.llm_client.call_llm") as call:
            res = run_model(ModelCall(prompt="hi", cancel=ev))
        call.assert_not_called()
        self.assertFalse(res.ok)
        self.assertIn("cancelled", res.error)

    def test_configured_key_is_scrubbed_from_errors(self):
        secret = "sk-test-ABCDEF1234567890"
        with patch("core.llm_client.get_llm_provider", return_value="openai"), \
             patch("core.llm_client.get_llm_settings",
                   return_value=("http://x", "m")), \
             patch("core.model_api._configured_secrets",
                   return_value=[secret]), \
             patch("core.llm_client.call_llm",
                   side_effect=RuntimeError(f"auth failed for {secret}")):
            res = run_model(ModelCall(prompt="hi"))
        self.assertNotIn(secret, res.error)
        self.assertIn("***", res.error)


class TestLadderStepsDownOnProviderErrors(unittest.TestCase):

    def setUp(self):
        self.mgr = ModelManager(ladders={"smart": ("a", "b")})

    def test_quota_error_still_returns_quota(self):
        self.assertEqual(
            self.mgr.record("a", False, 0.1, error="429 RESOURCE_EXHAUSTED"),
            "quota")
        self.assertTrue(self.mgr.is_cooling("a"))

    def test_connection_error_cools_the_rung(self):
        self.assertEqual(
            self.mgr.record("a", False, 5.0,
                            error="Connection refused by host"),
            "provider")
        self.assertTrue(self.mgr.is_cooling("a"))
        self.assertEqual(self.mgr.order("smart"), ("b", "a"),
                         "an unreachable provider must be demoted, not retried")

    def test_timeout_and_5xx_are_provider_errors(self):
        for msg in ("Read timed out after 10s",
                    "503 Service Unavailable",
                    "NameResolutionError: [Errno 11001] getaddrinfo failed"):
            self.assertEqual(self.mgr.record("a", False, 1.0, error=msg),
                             "provider", msg)
            ModelManager(ladders={"smart": ("a",)})  # sanity: no state leak
            self.mgr = ModelManager(ladders={"smart": ("a", "b")})

    def test_ordinary_failure_still_does_not_cool(self):
        self.assertEqual(self.mgr.record("a", False, 0.2, error="boom"), "")
        self.assertFalse(self.mgr.is_cooling("a"))

    def test_success_never_cools(self):
        self.assertEqual(self.mgr.record("a", True, 0.4), "")


if __name__ == "__main__":
    unittest.main()
