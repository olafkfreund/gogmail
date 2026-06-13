"""Tests for the voice module (recorder/TTS detection mocked; no audio hardware)
and Gemini audio transcription (requests mocked)."""
import asyncio
import os
import unittest
from unittest import mock

from gogmail import voice
from gogmail.gemini_api import GeminiAPI


def _async(value):
    async def f(*a, **k):
        return value
    return f


class TestDetection(unittest.TestCase):
    def test_recorder_detection_prefers_first_available(self):
        def which(name):
            return f"/usr/bin/{name}" if name in ("ffmpeg", "rec") else None
        with mock.patch("gogmail.voice.shutil.which", side_effect=which):
            cmd = voice.detect_recorder()
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd[0], "ffmpeg")  # ffmpeg outranks rec in the list

    def test_recorder_detection_none_when_absent(self):
        with mock.patch("gogmail.voice.shutil.which", return_value=None):
            self.assertIsNone(voice.detect_recorder())

    def test_tts_detection(self):
        with mock.patch("gogmail.voice.shutil.which",
                        side_effect=lambda n: "/usr/bin/x" if n == "spd-say" else None):
            cmd = voice.detect_tts()
        self.assertEqual(cmd[0], "spd-say")


class TestRecorder(unittest.TestCase):
    def test_start_returns_false_without_recorder(self):
        rec = voice.Recorder()
        with mock.patch("gogmail.voice.detect_recorder", return_value=None):
            self.assertFalse(rec.start())

    def test_start_stop_returns_path_for_nonempty_wav(self):
        # Fake recorder process; write a >44-byte file to the {out} path.
        class FakeProc:
            def __init__(self, path):
                self._path = path
            def send_signal(self, sig):
                with open(self._path, "wb") as f:
                    f.write(b"RIFF" + b"\x00" * 100)
            def wait(self, timeout=None):
                return 0
        rec = voice.Recorder(command=["fakerec", "{out}"])
        captured = {}
        def fake_popen(cmd, **kw):
            captured["out"] = cmd[-1]
            return FakeProc(cmd[-1])
        with mock.patch("gogmail.voice.subprocess.Popen", side_effect=fake_popen):
            self.assertTrue(rec.start())
            path = rec.stop()
        self.assertIsNotNone(path)
        self.assertTrue(os.path.exists(path))
        os.remove(path)

    def test_stop_discards_empty_capture(self):
        class FakeProc:
            def send_signal(self, sig): pass
            def wait(self, timeout=None): return 0
        rec = voice.Recorder(command=["fakerec", "{out}"])
        with mock.patch("gogmail.voice.subprocess.Popen", return_value=FakeProc()):
            rec.start()
            # The mkstemp'd file stays empty (header-only) -> treated as no audio.
            path = rec.stop()
        self.assertIsNone(path)


class TestSpeak(unittest.TestCase):
    def test_speak_noop_without_engine(self):
        with mock.patch("gogmail.voice.detect_tts", return_value=None):
            self.assertFalse(voice.speak("hello"))

    def test_speak_invokes_engine_with_text(self):
        calls = []
        with mock.patch("gogmail.voice.detect_tts", return_value=["espeak-ng"]), \
                mock.patch("gogmail.voice.subprocess.Popen",
                           side_effect=lambda cmd, **k: calls.append(cmd)):
            self.assertTrue(voice.speak("hello world"))
        self.assertEqual(calls[0], ["espeak-ng", "hello world"])

    def test_speak_ignores_empty(self):
        with mock.patch("gogmail.voice.detect_tts", return_value=["espeak-ng"]):
            self.assertFalse(voice.speak("   "))


class TestTranscribe(unittest.TestCase):
    def test_transcribe_sends_inline_audio_and_returns_text(self):
        seen = {}

        def fake_call(contents, system_instruction=None):
            seen["contents"] = contents
            return "show me my latest emails"

        with mock.patch.object(GeminiAPI, "_call_sync", side_effect=fake_call):
            text = asyncio.run(GeminiAPI.transcribe_audio(b"\x00\x01\x02", "audio/wav"))
        self.assertEqual(text, "show me my latest emails")
        part = seen["contents"][0]["parts"][0]
        self.assertEqual(part["inline_data"]["mime_type"], "audio/wav")
        self.assertTrue(part["inline_data"]["data"])  # base64 payload present


class TestGeminiTTS(unittest.TestCase):
    def test_pcm_to_wav_is_valid_wav(self):
        import wave, io
        pcm = b"\x00\x01" * 1000
        wav = GeminiAPI._pcm_to_wav(pcm, 24000)
        w = wave.open(io.BytesIO(wav), "rb")
        self.assertEqual(w.getnchannels(), 1)
        self.assertEqual(w.getframerate(), 24000)
        self.assertEqual(w.getsampwidth(), 2)

    def test_synthesize_returns_wav_from_inline_audio(self):
        import base64

        class _R:
            status_code = 200
            def json(self):
                return {"candidates": [{"content": {"parts": [
                    {"inlineData": {"mimeType": "audio/L16;rate=24000",
                                    "data": base64.b64encode(b"\x00\x01" * 500).decode()}}]}}]}

        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "k"}), \
                mock.patch("gogmail.gemini_api.requests.post", return_value=_R()):
            wav = asyncio.run(GeminiAPI.synthesize_speech("hi", "Kore"))
        self.assertTrue(wav and wav[:4] == b"RIFF")

    def test_synthesize_none_without_key(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            os.environ.pop("GEMINI_API_KEY", None)
            self.assertIsNone(asyncio.run(GeminiAPI.synthesize_speech("hi")))


class TestPlayWav(unittest.TestCase):
    def test_play_wav_noop_without_player(self):
        with mock.patch("gogmail.voice.detect_player", return_value=None):
            self.assertFalse(voice.play_wav(b"RIFFxxxx"))

    def test_play_wav_invokes_player(self):
        calls = []
        with mock.patch("gogmail.voice.detect_player", return_value=["aplay", "-q"]), \
                mock.patch("gogmail.voice.subprocess.run",
                           side_effect=lambda cmd, **k: calls.append(cmd[0])):
            self.assertTrue(voice.play_wav(b"RIFFxxxx"))
        self.assertEqual(calls[0], "aplay")

    def test_play_wav_empty_is_false(self):
        self.assertFalse(voice.play_wav(b""))


class TestSpeakRouting(unittest.IsolatedAsyncioTestCase):
    async def test_auto_uses_gemini_then_plays(self):
        from gogmail.app import _speak_reply
        syn = mock.AsyncMock(return_value=b"RIFFwav")
        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "k"}), \
                mock.patch.object(GeminiAPI, "synthesize_speech", syn), \
                mock.patch("gogmail.app.voice.play_wav", return_value=True) as play, \
                mock.patch("gogmail.app.voice.speak") as local:
            await _speak_reply({"tts_engine": "auto"}, "hello")
        syn.assert_called_once()
        play.assert_called_once()
        local.assert_not_called()

    async def test_system_engine_uses_local(self):
        from gogmail.app import _speak_reply
        syn = mock.AsyncMock(return_value=b"x")
        with mock.patch.object(GeminiAPI, "synthesize_speech", syn), \
                mock.patch("gogmail.app.voice.speak") as local:
            await _speak_reply({"tts_engine": "system"}, "hello")
        syn.assert_not_called()
        local.assert_called_once()

    async def test_auto_falls_back_to_local_when_gemini_fails(self):
        from gogmail.app import _speak_reply
        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "k"}), \
                mock.patch.object(GeminiAPI, "synthesize_speech", mock.AsyncMock(return_value=None)), \
                mock.patch("gogmail.app.voice.play_wav", return_value=False), \
                mock.patch("gogmail.app.voice.speak") as local:
            await _speak_reply({"tts_engine": "auto"}, "hello")
        local.assert_called_once()


if __name__ == "__main__":
    unittest.main()
