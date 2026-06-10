"""Tests for the Gemini API wrapper (no network calls)."""
import unittest
from unittest import mock

from gogmail.gemini_api import GeminiAPI


class TestGeminiNoKey(unittest.TestCase):
    def test_missing_api_key_returns_error(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            out = GeminiAPI._call_sync([{"parts": [{"text": "hi"}]}])
            self.assertIn("GEMINI_API_KEY", out)


class TestGeminiDelegation(unittest.TestCase):
    def test_content_sync_wraps_prompt_and_delegates(self):
        seen = {}

        def fake_call(contents, system_instruction=None):
            seen["contents"] = contents
            seen["system"] = system_instruction
            return "ok"

        with mock.patch.object(GeminiAPI, "_call_sync", staticmethod(fake_call)):
            result = GeminiAPI._generate_content_sync("hello", "be brief")
        self.assertEqual(result, "ok")
        self.assertEqual(seen["contents"], [{"parts": [{"text": "hello"}]}])
        self.assertEqual(seen["system"], "be brief")


if __name__ == "__main__":
    unittest.main()
