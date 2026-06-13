"""Tests for the pluggable LLM provider seam (no network calls)."""
import asyncio
import unittest
from unittest import mock

from gogmail.gemini_api import GeminiAPI
from gogmail.llm import LLMProvider, GeminiProvider, get_provider

REQUIRED_METHODS = (
    "generate_chat",
    "generate_content",
    "summarize_email",
    "draft_reply",
    "transcribe_audio",
    "synthesize_speech",
)


class TestGetProvider(unittest.TestCase):
    def test_default_is_gemini(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            provider = get_provider()
        self.assertIsInstance(provider, GeminiProvider)
        self.assertIsInstance(provider, LLMProvider)

    def test_env_selects_gemini(self):
        with mock.patch.dict("os.environ", {"GOGMAIL_LLM_PROVIDER": "gemini"}, clear=True):
            self.assertIsInstance(get_provider(), GeminiProvider)

    def test_unknown_name_falls_back_to_default(self):
        with mock.patch.dict("os.environ", {"GOGMAIL_LLM_PROVIDER": "nope"}, clear=True):
            self.assertIsInstance(get_provider(), GeminiProvider)

    def test_explicit_arg_overrides_env(self):
        with mock.patch.dict("os.environ", {"GOGMAIL_LLM_PROVIDER": "nope"}, clear=True):
            self.assertIsInstance(get_provider("gemini"), GeminiProvider)

    def test_exposes_required_methods(self):
        provider = get_provider()
        for name in REQUIRED_METHODS:
            self.assertTrue(callable(getattr(provider, name)), name)


class TestGeminiProviderDelegation(unittest.TestCase):
    def test_generate_chat_delegates(self):
        seen = {}

        async def fake(contents, system_instruction=None):
            seen["contents"] = contents
            seen["system"] = system_instruction
            return "chat-ok"

        with mock.patch.object(GeminiAPI, "generate_chat", fake):
            result = asyncio.run(
                GeminiProvider().generate_chat([{"parts": [{"text": "hi"}]}], "sys")
            )
        self.assertEqual(result, "chat-ok")
        self.assertEqual(seen["contents"], [{"parts": [{"text": "hi"}]}])
        self.assertEqual(seen["system"], "sys")

    def test_synthesize_speech_delegates(self):
        seen = {}

        async def fake(text, voice="Kore"):
            seen["text"] = text
            seen["voice"] = voice
            return b"WAVDATA"

        with mock.patch.object(GeminiAPI, "synthesize_speech", fake):
            result = asyncio.run(GeminiProvider().synthesize_speech("hello", "Puck"))
        self.assertEqual(result, b"WAVDATA")
        self.assertEqual(seen["text"], "hello")
        self.assertEqual(seen["voice"], "Puck")


if __name__ == "__main__":
    unittest.main()
