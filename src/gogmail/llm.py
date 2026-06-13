"""Pluggable LLM provider seam.

The app talks to the AI backend through an ``LLMProvider`` abstraction rather
than reaching for ``GeminiAPI`` directly. Today the only backend is Gemini
(``GeminiProvider``, which delegates 1:1 to ``GeminiAPI`` so behavior is
unchanged), but the seam lets another backend be dropped in without touching
the call sites in ``app.py`` / ``widgets.py`` / ``screens.py``.

Pick the provider with the ``GOGMAIL_LLM_PROVIDER`` env var (default
``"gemini"``); ``get_provider()`` is the factory.

Note: the assistant is driven by a prompt-driven JSON tool-calling loop (see
``run_ai`` in ``app.py``), deliberately *not* a provider's native
function-calling. Native FC is a future per-provider capability that a provider
could opt into; for now stability wins and every provider just returns text.
"""
import os
from abc import ABC, abstractmethod

from gogmail.gemini_api import GeminiAPI


class LLMProvider(ABC):
    """Async surface the app needs from any LLM backend.

    Signatures mirror ``GeminiAPI`` exactly so providers are drop-in. Text
    methods return a plain ``str`` (an ``"Error:"``/``"Exception:"`` string on
    failure); ``synthesize_speech`` returns WAV ``bytes`` or ``None``.
    """

    @abstractmethod
    async def generate_chat(self, contents: list, system_instruction: str = None) -> str:
        """Multi-turn chat (the tool-calling loop's single step)."""

    @abstractmethod
    async def generate_content(self, prompt: str, system_instruction: str = None) -> str:
        """Single-turn prompt completion."""

    @abstractmethod
    async def summarize_email(self, subject: str, sender: str, body: str) -> str:
        """Summarize an email thread."""

    @abstractmethod
    async def draft_reply(self, original_subject: str, original_sender: str,
                          original_body: str, user_instructions: str) -> str:
        """Draft an email reply body."""

    @abstractmethod
    async def transcribe_audio(self, audio_bytes: bytes, mime_type: str = "audio/wav") -> str:
        """Transcribe recorded speech (push-to-talk)."""

    @abstractmethod
    async def synthesize_speech(self, text: str, voice: str = "Kore") -> bytes:
        """Natural TTS. Returns WAV bytes, or None on any failure."""


class GeminiProvider(LLMProvider):
    """Default provider: delegates straight to ``GeminiAPI`` (no behavior change)."""

    async def generate_chat(self, contents: list, system_instruction: str = None) -> str:
        return await GeminiAPI.generate_chat(contents, system_instruction)

    async def generate_content(self, prompt: str, system_instruction: str = None) -> str:
        return await GeminiAPI.generate_content(prompt, system_instruction)

    async def summarize_email(self, subject: str, sender: str, body: str) -> str:
        return await GeminiAPI.summarize_email(subject, sender, body)

    async def draft_reply(self, original_subject: str, original_sender: str,
                          original_body: str, user_instructions: str) -> str:
        return await GeminiAPI.draft_reply(original_subject, original_sender,
                                           original_body, user_instructions)

    async def transcribe_audio(self, audio_bytes: bytes, mime_type: str = "audio/wav") -> str:
        return await GeminiAPI.transcribe_audio(audio_bytes, mime_type)

    async def synthesize_speech(self, text: str, voice: str = "Kore") -> bytes:
        return await GeminiAPI.synthesize_speech(text, voice)


# Registry of available providers, keyed by GOGMAIL_LLM_PROVIDER value.
_PROVIDERS = {
    "gemini": GeminiProvider,
}


def get_provider(name: str = None) -> LLMProvider:
    """Return the selected LLM provider.

    Resolution order: explicit ``name`` arg → ``GOGMAIL_LLM_PROVIDER`` env →
    ``"gemini"``. Unknown names fall back to the default provider.
    """
    key = (name or os.environ.get("GOGMAIL_LLM_PROVIDER") or "gemini").strip().lower()
    return _PROVIDERS.get(key, GeminiProvider)()
