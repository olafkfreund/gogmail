import os
import re
import io
import wave
import base64
import requests
import asyncio
import logging

class GeminiAPI:
    @staticmethod
    def _call_sync(contents: list, system_instruction: str = None) -> str:
        """Single entry point for Gemini generateContent calls."""
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return "Error: GEMINI_API_KEY environment variable is not set."

        model = os.environ.get("GEMINI_MODEL_DEFAULT", "gemini-3.5-flash")
        # Key goes in a header, never the URL: requests exception strings include
        # the URL, which would leak the key into gogmail.log.
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

        payload = {"contents": contents}

        if system_instruction:
            payload["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }

        try:
            response = requests.post(url, json=payload, timeout=30,
                                     headers={"x-goog-api-key": api_key})
            if response.status_code != 200:
                logging.error(f"Gemini API returned error {response.status_code}: {response.text}")
                return f"Error from Gemini API: {response.status_code} - {response.text}"

            data = response.json()
            candidates = data.get("candidates", [])
            if not candidates:
                return "Error: No candidates returned from Gemini."

            content = candidates[0].get("content", {})
            parts = content.get("parts", [])
            if not parts:
                return "Error: Empty response content from Gemini."

            return parts[0].get("text", "")
        except Exception as e:
            logging.error(f"Exception during Gemini API call: {str(e)}")
            return f"Exception: {str(e)}"

    @staticmethod
    def _generate_content_sync(prompt: str, system_instruction: str = None) -> str:
        return GeminiAPI._call_sync([{"parts": [{"text": prompt}]}], system_instruction)

    @classmethod
    async def generate_content(cls, prompt: str, system_instruction: str = None) -> str:
        """Call Gemini API asynchronously using a thread pool."""
        return await asyncio.to_thread(cls._generate_content_sync, prompt, system_instruction)
        
    @classmethod
    async def summarize_email(cls, subject: str, sender: str, body: str) -> str:
        prompt = f"Please summarize this email concisely.\n\nFrom: {sender}\nSubject: {subject}\n\nBody:\n{body}"
        system = "You are a helpful office assistant. Summarize the email thread in a few bullet points, highlighting key requests, actions, and deadlines."
        return await cls.generate_content(prompt, system)

    @classmethod
    async def draft_reply(cls, original_subject: str, original_sender: str, original_body: str, user_instructions: str) -> str:
        prompt = (
            f"Please write a professional reply to the email below.\n\n"
            f"Original Sender: {original_sender}\n"
            f"Original Subject: {original_subject}\n"
            f"Original Body:\n{original_body}\n\n"
            f"My drafting instructions:\n{user_instructions}"
        )
        system = (
            "You are a helpful assistant drafting an email reply. Write a professional, polite, and clear email reply. "
            "Do not include the email headers (Subject, To, etc.) in your output, just write the body. Do not include markdown code block syntax (like ```) around the email."
        )
        return await cls.generate_content(prompt, system)

    @staticmethod
    def _generate_chat_sync(contents: list, system_instruction: str = None) -> str:
        return GeminiAPI._call_sync(contents, system_instruction)

    @classmethod
    async def generate_chat(cls, contents: list, system_instruction: str = None) -> str:
        """Call Gemini API for multi-turn chat asynchronously using a thread pool."""
        return await asyncio.to_thread(cls._generate_chat_sync, contents, system_instruction)

    @staticmethod
    def _pcm_to_wav(pcm: bytes, rate: int = 24000) -> bytes:
        """Wrap raw 16-bit mono PCM (what Gemini TTS returns) in a WAV container."""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm)
        return buf.getvalue()

    @staticmethod
    def _synthesize_sync(text: str, voice: str) -> bytes:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key or not text.strip():
            return None
        # gemini-3.1 is the newest TTS preview; override with GEMINI_TTS_MODEL.
        model = os.environ.get("GEMINI_TTS_MODEL", "gemini-3.1-flash-tts-preview")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        payload = {
            "contents": [{"parts": [{"text": text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
            },
        }
        try:
            resp = requests.post(url, json=payload, timeout=40, headers={"x-goog-api-key": api_key})
            if resp.status_code != 200:
                logging.error(f"Gemini TTS error {resp.status_code}: {resp.text[:200]}")
                return None
            parts = (resp.json().get("candidates") or [{}])[0].get("content", {}).get("parts", [])
            for p in parts:
                inline = p.get("inlineData") or p.get("inline_data")
                if inline and inline.get("data"):
                    pcm = base64.b64decode(inline["data"])
                    mt = inline.get("mimeType") or inline.get("mime_type") or ""
                    m = re.search(r"rate=(\d+)", mt)
                    return GeminiAPI._pcm_to_wav(pcm, int(m.group(1)) if m else 24000)
            return None
        except Exception as e:
            logging.error(f"Gemini TTS exception: {e}")
            return None

    @classmethod
    async def synthesize_speech(cls, text: str, voice: str = "Kore") -> bytes:
        """Natural TTS via Gemini. Returns WAV bytes, or None on any failure
        (caller falls back to a local engine)."""
        return await asyncio.to_thread(cls._synthesize_sync, text, voice)

    @classmethod
    async def transcribe_audio(cls, audio_bytes: bytes, mime_type: str = "audio/wav") -> str:
        """Transcribe recorded speech to text via inline audio (push-to-talk).

        Reuses the same generateContent path/key as everything else; the clip is
        sent inline (the API caps inline payloads at 20 MB, far above a short
        utterance). Returns the transcript, or an "Error:"/"Exception:" string.
        """
        import base64
        data = base64.b64encode(audio_bytes).decode("ascii")
        contents = [{"parts": [
            {"inline_data": {"mime_type": mime_type, "data": data}},
            {"text": "Transcribe this audio verbatim. Output only the transcript text, "
                     "with no preamble, quotes, or commentary."},
        ]}]
        return await asyncio.to_thread(cls._call_sync, contents, None)
