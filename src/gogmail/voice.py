"""Voice I/O for the assistant, entirely via shell-outs (no native audio dep).

- Recording: push-to-talk capture to a 16 kHz mono WAV using the first
  available mic recorder (PipeWire / ALSA / ffmpeg / sox).
- Speech: optional local text-to-speech using the first available engine.

Both the recorder and TTS commands are auto-detected but overridable, and the
whole module is import-safe with none of the tools present (detection just
returns None) so the app and tests run anywhere.
"""
import logging
import os
import shutil
import signal
import subprocess
import tempfile

# Recorder commands in preference order. Each records 16 kHz mono WAV to {out}
# until the process is signalled to stop. PipeWire first (modern desktop), then
# ALSA, then ffmpeg (PulseAudio), then sox.
_RECORDERS = [
    ("pw-record", ["pw-record", "--rate", "16000", "--channels", "1", "{out}"]),
    ("arecord", ["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1", "{out}"]),
    ("ffmpeg", ["ffmpeg", "-loglevel", "quiet", "-f", "pulse", "-i", "default",
                "-ar", "16000", "-ac", "1", "-y", "{out}"]),
    ("rec", ["rec", "-q", "-r", "16000", "-c", "1", "{out}"]),  # sox
]

# Text-to-speech engines in preference order; the text is appended as the final
# argument. All speak asynchronously when spawned with Popen. (These are the
# local fallback — the natural-sounding default is Gemini TTS, played as WAV.)
_TTS = [
    ("espeak-ng", ["espeak-ng"]),
    ("espeak", ["espeak"]),
    ("spd-say", ["spd-say"]),
    ("say", ["say"]),  # macOS
]

# WAV players for Gemini TTS output, in preference order.
_PLAYERS = [
    ("paplay", ["paplay"]),
    ("aplay", ["aplay", "-q"]),
    ("ffplay", ["ffplay", "-autoexit", "-nodisp", "-loglevel", "quiet"]),
    ("play", ["play", "-q"]),  # sox
]


def detect_player():
    """Return the WAV-player command template (list) or None if none installed."""
    for name, cmd in _PLAYERS:
        if shutil.which(name):
            return cmd
    return None


def play_wav(data: bytes, command=None) -> bool:
    """Play WAV bytes via a local player. Blocking — call from a worker thread."""
    if not data:
        return False
    template = command or detect_player()
    if not template:
        return False
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="gogmail-tts-")
    try:
        os.write(fd, data)
        os.close(fd)
        subprocess.run(template + [path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception as e:
        logging.error(f"voice: wav playback failed: {e}")
        return False
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def detect_recorder():
    """Return the recorder command template (list) or None if none installed."""
    for name, cmd in _RECORDERS:
        if shutil.which(name):
            return cmd
    return None


def detect_tts():
    """Return the TTS command template (list) or None if none installed."""
    for name, cmd in _TTS:
        if shutil.which(name):
            return cmd
    return None


class Recorder:
    """Push-to-talk recorder: start() begins capture, stop() ends it and returns
    the WAV path (or None on failure / empty capture)."""

    def __init__(self, command=None):
        self._command = command  # optional override (list with {out})
        self._proc = None
        self._path = None

    def start(self) -> bool:
        template = self._command or detect_recorder()
        if not template:
            return False
        fd, self._path = tempfile.mkstemp(suffix=".wav", prefix="gogmail-voice-")
        os.close(fd)
        cmd = [a.replace("{out}", self._path) for a in template]
        try:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception as e:
            logging.error(f"voice: recorder failed to start: {e}")
            self._cleanup()
            return False

    def stop(self):
        """Stop capture and return the WAV path, or None. Blocking (~seconds) —
        call from a worker thread, not the UI thread."""
        if not self._proc:
            return None
        try:
            # SIGINT lets arecord/ffmpeg/sox finalise the WAV header cleanly.
            self._proc.send_signal(signal.SIGINT)
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.terminate()
                self._proc.wait(timeout=2)
        except Exception as e:
            logging.error(f"voice: recorder failed to stop: {e}")
        path, self._proc, self._path = self._path, None, None
        # A bare/zero recording is just the 44-byte WAV header (or missing).
        if path and os.path.exists(path) and os.path.getsize(path) > 44:
            return path
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
        return None

    @property
    def recording(self) -> bool:
        return self._proc is not None

    def _cleanup(self):
        if self._path and os.path.exists(self._path):
            try:
                os.remove(self._path)
            except OSError:
                pass
        self._path = None


def speak(text: str, command=None) -> bool:
    """Speak text via a local TTS engine (non-blocking). Returns False if no
    engine is available or the text is empty."""
    if not text or not text.strip():
        return False
    template = command or detect_tts()
    if not template:
        return False
    try:
        subprocess.Popen(template + [text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception as e:
        logging.error(f"voice: tts failed: {e}")
        return False
