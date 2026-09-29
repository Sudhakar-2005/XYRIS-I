"""
core/degraded.py — offline voice path for the Live API being down (§3.6).

DECIDED at M5, wired at M14: keep core/stt.py + core/tts.py and use them as
the degraded/offline fallback. When the session is unreachable, JARVIS still
listens (Whisper), still talks (offline TTS) and still answers (the REST
model ladder) — every step fail-soft, because neither faster-whisper nor an
TTS engine is a hard dependency: if the engine is not installed, the path
degrades again, to text only, and says so once instead of on every frame.

Two pieces live here so they can be tested without a microphone:

* EndpointDetector — VAD-lite utterance cutter: buffers speech, cuts after
  sustained silence, force-cuts at a max length, trims leading/trailing
  quiet. No model involved, pure numpy.
* OfflineVoice     — lazy, cached engines with honest failure accounting
  (a broken engine is remembered, not retried into a log storm).
"""
from __future__ import annotations

import numpy as np


class EndpointDetector:
    """Cuts a mic stream into utterances.

    ``feed(frame)`` takes one float32 frame and returns the utterance's
    frame list when (and only when) an utterance just ended — ``None``
    otherwise. Silence before the first loud frame is never buffered, so
    the transcript has no padded front; the silence that *ends* an
    utterance is trimmed back to ``keep_trailing_ms``.
    """

    def __init__(self, sample_rate: int = 16000, frame_ms: int = 30,
                 silence_ms: int = 900, max_utterance_s: float = 15.0,
                 threshold: float = 0.015, keep_trailing_ms: int = 300):
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.silence_frames = max(1, int(silence_ms / frame_ms))
        self.max_frames = max(1, int(max_utterance_s * 1000 / frame_ms))
        self.keep_trailing = max(1, int(keep_trailing_ms / frame_ms))
        self.threshold = threshold
        self._buf: list[np.ndarray] = []
        self._quiet = 0
        self._heard_speech = False

    @staticmethod
    def _rms(frame) -> float:
        arr = np.asarray(frame, dtype=np.float32).reshape(-1)
        if arr.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(arr * arr)))

    def feed(self, frame) -> list | None:
        loud = self._rms(frame) >= self.threshold
        if not self._heard_speech:
            if not loud:
                return None
            self._heard_speech = True
        self._buf.append(np.asarray(frame, dtype=np.float32).reshape(-1))
        if loud:
            self._quiet = 0
        else:
            self._quiet += 1
        ended = (self._quiet >= self.silence_frames
                 or len(self._buf) >= self.max_frames)
        if not ended:
            return None
        return self._take()

    def _take(self) -> list:
        buf = self._buf
        self._buf, self._quiet, self._heard_speech = [], 0, False
        quiet_end = 0
        for piece in reversed(buf):
            if self._rms(piece) < self.threshold:
                quiet_end += 1
            else:
                break
        drop = max(0, quiet_end - self.keep_trailing)
        if drop:
            buf = buf[:len(buf) - drop]
        return buf


def _default_stt():
    from core.stt import WhisperSTT
    return WhisperSTT()


def _default_tts():
    from core.tts import create_tts_player
    return create_tts_player({})


class OfflineVoice:
    """Lazy local engines with honest failure accounting (§3.6).

    Factories are injectable so tests never construct a real Whisper or
    TTS engine; production uses the module-level defaults above.
    """

    def __init__(self, stt_factory=None, tts_factory=None, log=print):
        self._stt_factory = stt_factory or _default_stt
        self._tts_factory = tts_factory or _default_tts
        self._log = log
        self._stt = None
        self._tts = None
        self._stt_broken = False
        self._tts_broken = False

    @property
    def stt_ready(self) -> bool:
        return self._stt is not None and not self._stt_broken

    @property
    def tts_ready(self) -> bool:
        return self._tts is not None and not self._tts_broken

    def transcribe(self, pcm) -> str:
        """Whisper transcript for a float32 16 kHz array; '' when it can't."""
        if self._stt_broken:
            return ""
        if self._stt is None:
            try:
                self._stt = self._stt_factory()
            except Exception as e:
                self._stt_broken = True
                self._log(f"[Degraded] No offline STT available "
                          f"(pip install faster-whisper): {e}")
                return ""
        try:
            return str(self._stt.transcribe(pcm) or "").strip()
        except Exception as e:
            self._log(f"[Degraded] Transcription failed: {e}")
            return ""

    def speak(self, text) -> bool:
        """Play text on the offline TTS; False when no engine can."""
        if not text or self._tts_broken:
            return False
        if self._tts is None:
            try:
                self._tts = self._tts_factory()
            except Exception as e:
                self._tts_broken = True
                self._log(f"[Degraded] No offline TTS available "
                          f"(pip install edge-tts): {e}")
                return False
        try:
            self._tts.speak(text)   # TTSPlayer.speak catches engine errors
            return True
        except Exception as e:
            self._log(f"[Degraded] TTS failed: {e}")
            return False
