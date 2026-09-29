"""M14 — degraded offline voice wired (§3.6).

The DECIDED-at-M5 disposition ("keep core/stt.py + core/tts.py and wire them
as the degraded/offline fallback when the Live API is down") becomes code
here:

* ``EndpointDetector`` cuts mic audio into utterances with no model and no
  device — pure numpy, fully testable.
* ``OfflineVoice`` constructs engines lazily and *remembers* failures rather
  than retrying them into a log storm: the engines are optional installs.
* The runtime engages the mode after three straight connection failures,
  ends it the moment a session comes back, and answers dashboard text over
  the REST ladder instead of dropping it on the floor.
* The degraded loop runs end-to-end against a fake sounddevice with every
  engine faked: speech frames in → transcript → reply → speak, mic closed
  when the session returns.
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main  # noqa: E402
from core.degraded import EndpointDetector, OfflineVoice  # noqa: E402


class _RecorderUI:
    def __init__(self):
        self.logs = []

    def write_log(self, s):
        self.logs.append(str(s))


class _FakeSTT:
    def __init__(self, text="hello there"):
        self.text = text
        self.calls = 0

    def transcribe(self, pcm):
        self.calls += 1
        return self.text


class _FakeTTS:
    def __init__(self):
        self.said = []

    def speak(self, text):
        self.said.append(text)


class TestEndpointDetector(unittest.TestCase):
    """VAD-lite utterance cutting — no model, no microphone."""

    def _det(self, **kw):
        return EndpointDetector(**kw)

    def test_leading_silence_is_never_buffered(self):
        det = self._det()
        for _ in range(40):
            self.assertIsNone(det.feed(np.zeros(480, dtype=np.float32)))
        self.assertEqual(det._buf, [])

    def test_speech_then_silence_cuts_and_trims(self):
        det = self._det()                       # 900 ms silence @ 30 ms = 30
        for _ in range(5):                      # room tone before the speech
            self.assertIsNone(det.feed(np.zeros(480, dtype=np.float32)))
        for _ in range(10):
            self.assertIsNone(det.feed(np.full(480, 0.3, dtype=np.float32)))
        got = None
        for i in range(40):                     # 30 quiet frames trigger the cut
            got = det.feed(np.zeros(480, dtype=np.float32))
            if got is not None:
                break
        self.assertIsNotNone(got)
        # leading room tone dropped; 10 loud + keep_trailing (10) quiet kept
        self.assertEqual(len(got), 20)
        self.assertEqual(det._buf, [])          # clean for the next utterance
        self.assertIsNone(det.feed(np.zeros(480, dtype=np.float32)))

    def test_max_duration_force_cuts_mid_speech(self):
        det = self._det(max_utterance_s=0.3)    # 10 frames @ 30 ms
        got = None
        for _ in range(10):
            got = det.feed(np.full(480, 0.5, dtype=np.float32))
        self.assertIsNotNone(got)
        self.assertEqual(len(got), 10)

    def test_threshold_separates_room_from_speech(self):
        det = self._det(threshold=0.015)
        self.assertIsNone(det.feed(np.full(480, 0.005, dtype=np.float32)))
        self.assertIsNone(det.feed(np.full(480, 0.02, dtype=np.float32)))


class TestOfflineVoice(unittest.TestCase):
    """Optional engines: lazy, cached, honest about being absent."""

    def test_missing_stt_logged_once_and_remembered(self):
        logs, made = [], []

        def broken_factory():
            made.append(1)
            raise ImportError("faster-whisper")

        v = OfflineVoice(stt_factory=broken_factory, log=logs.append)
        self.assertEqual(v.transcribe(np.zeros(480, dtype=np.float32)), "")
        self.assertEqual(v.transcribe(np.zeros(480, dtype=np.float32)), "")
        self.assertEqual(len(made), 1)          # never retried into a storm
        self.assertEqual(len(logs), 1)
        self.assertIn("No offline STT", logs[0])
        self.assertFalse(v.stt_ready)

    def test_transcribe_passes_engine_text_through(self):
        eng = _FakeSTT("time please")
        v = OfflineVoice(stt_factory=lambda: eng, log=lambda _s: None)
        self.assertEqual(
            v.transcribe(np.zeros(16, dtype=np.float32)), "time please")
        self.assertEqual(eng.calls, 1)
        self.assertTrue(v.stt_ready)

    def test_missing_tts_fails_soft_once(self):
        logs, made = [], []

        def broken_factory():
            made.append(1)
            raise ImportError("edge-tts")

        v = OfflineVoice(tts_factory=broken_factory, log=logs.append)
        self.assertFalse(v.speak("hello"))
        self.assertFalse(v.speak("again"))
        self.assertEqual(len(made), 1)
        self.assertEqual(len(logs), 1)
        self.assertIn("No offline TTS", logs[0])
        self.assertFalse(v.tts_ready)

    def test_speak_passes_text_to_engine(self):
        eng = _FakeTTS()
        v = OfflineVoice(tts_factory=lambda: eng, log=lambda _s: None)
        self.assertTrue(v.speak("good morning"))
        self.assertEqual(eng.said, ["good morning"])

    def test_empty_text_never_touches_the_factory(self):
        made = []
        v = OfflineVoice(tts_factory=lambda: made.append(1), log=lambda _s: None)
        self.assertFalse(v.speak(""))
        self.assertEqual(made, [])


class TestOfflineReply(unittest.TestCase):
    """The REST ladder answer while Live is down — with an honest floor."""

    def _live(self):
        live = main.JarvisLive.__new__(main.JarvisLive)
        live.ui = _RecorderUI()
        return live

    def test_model_answer_comes_through(self):
        with patch("core.gemini.call",
                   return_value=SimpleNamespace(text="  It is noon.  ")):
            self.assertEqual(self._live()._offline_reply("what time"),
                             "It is noon.")

    def test_model_unreachable_falls_to_honest_line(self):
        with patch("core.gemini.call", return_value=None):
            out = self._live()._offline_reply("what time")
        self.assertIn("can't reach", out)

    def test_model_raising_falls_to_honest_line(self):
        with patch("core.gemini.call", side_effect=RuntimeError("ladder down")):
            out = self._live()._offline_reply("what time")
        self.assertIn("can't reach", out)


class TestDegradedEngagement(unittest.TestCase):
    """Three straight failures engage the mode; a session ends it."""

    def _fresh(self):
        live = main.JarvisLive.__new__(main.JarvisLive)
        live.ui = _RecorderUI()
        live.session = None
        live._degraded = False
        live._degraded_failures = 0
        live._offline = None
        from core.event_bus import EventBus
        live._event_bus = EventBus()            # not running → emits drop
        return live

    def test_third_failure_engages_exactly_once(self):
        async def scenario():
            live = self._fresh()
            started = []

            async def fake_loop():
                started.append(1)

            live._degraded_loop = fake_loop
            r = [live.note_connection_failure() for _ in range(4)]
            await asyncio.sleep(0)              # let the created task run
            return r, started, live._degraded, live.ui.logs

        r, started, flag, logs = asyncio.run(scenario())
        self.assertEqual(r, [False, False, True, False])
        self.assertTrue(flag)
        self.assertEqual(len(started), 1)       # one loop, never two
        self.assertTrue(any("Degraded mode" in ln for ln in logs))

    def test_existing_session_never_engages(self):
        live = self._fresh()
        live.session = object()
        live._degraded_failures = 2
        self.assertFalse(live.note_connection_failure())
        self.assertFalse(live._degraded)

    def test_engage_without_running_loop_still_sets_the_flag(self):
        live = self._fresh()
        live._degraded_failures = 2
        self.assertTrue(live.note_connection_failure())   # RuntimeError swallowed
        self.assertTrue(live._degraded)

    def test_session_back_ends_the_mode_and_says_so(self):
        async def scenario():
            live = self._fresh()
            live._degraded = True
            live._degraded_failures = 3
            live._exit_degraded()
            return live._degraded, live._degraded_failures, live.ui.logs

        flag, failures, logs = asyncio.run(scenario())
        self.assertFalse(flag)
        self.assertEqual(failures, 0)
        self.assertTrue(any("degraded mode ended" in ln for ln in logs))

    def test_exit_when_never_engaged_is_quiet(self):
        live = self._fresh()
        live._degraded_failures = 1
        live._exit_degraded()
        self.assertEqual(live.ui.logs, [])
        self.assertEqual(live._degraded_failures, 0)


class _FakeStream:
    def __init__(self, cb):
        self.cb = cb
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


class _FakeSounddevice:
    """module stand-in: captures the stream instead of opening a device."""
    def __init__(self):
        self.stream = None
        self.kwargs = None

    def InputStream(self, samplerate=None, channels=None, dtype=None,
                    callback=None):
        self.kwargs = dict(samplerate=samplerate, channels=channels, dtype=dtype)
        self.stream = _FakeStream(callback)
        return self.stream


class TestDegradedLoop(unittest.TestCase):
    """End-to-end: frames in → transcript → reply → speak; mic closed when
    the session returns. Fakes for the device and every engine — no audio
    hardware, no model downloads."""

    def test_loop_runs_the_full_pipeline_and_closes_on_session(self):
        async def scenario():
            live = main.JarvisLive.__new__(main.JarvisLive)
            live.ui = _RecorderUI()
            live.session = None
            live._degraded = True
            live._degraded_failures = 3
            live._offline = None
            from core.event_bus import EventBus
            live._event_bus = EventBus()

            calls = {}

            def fake_transcribe(pcm):
                calls["pcm"] = pcm
                return "what time is it"

            def fake_reply(text):
                calls["rt"] = text
                return "It is noon."

            def fake_speak(text):
                calls["sp"] = text
                return True

            live._offline_transcribe = fake_transcribe
            live._offline_reply = fake_reply
            live._offline_speak = fake_speak

            fake_sd = _FakeSounddevice()
            real_sd = main.sd
            main.sd = fake_sd
            try:
                task = asyncio.create_task(live._degraded_loop())
                for _ in range(50):
                    if fake_sd.stream is not None and fake_sd.stream.started:
                        break
                    await asyncio.sleep(0.01)
                self.assertIsNotNone(fake_sd.stream)
                self.assertTrue(fake_sd.stream.started)
                self.assertEqual(fake_sd.kwargs["samplerate"],
                                 main.SEND_SAMPLE_RATE)
                self.assertEqual(fake_sd.kwargs["dtype"], "float32")

                # 10 speech frames + 40 room-tone frames = one utterance
                for _ in range(10):
                    fake_sd.stream.cb(np.full(480, 0.3, dtype=np.float32),
                                      480, None, None)
                for _ in range(40):
                    fake_sd.stream.cb(np.zeros(480, dtype=np.float32),
                                      480, None, None)

                for _ in range(100):
                    if "sp" in calls:
                        break
                    await asyncio.sleep(0.02)

                live.session = object()          # the session came back
                await asyncio.wait_for(task, 2)
                return calls, fake_sd.stream, live.ui.logs
            finally:
                main.sd = real_sd

        calls, stream, logs = asyncio.run(scenario())
        self.assertIn("pcm", calls)
        self.assertEqual(calls["pcm"].shape[0] % 480, 0)   # frames, concatenated
        self.assertGreaterEqual(calls["pcm"].shape[0] // 480, 10)
        self.assertEqual(calls["rt"], "what time is it")
        self.assertEqual(calls["sp"], "It is noon.")
        self.assertTrue(stream.stopped)
        self.assertTrue(stream.closed)
        self.assertTrue(any(ln.startswith("[Offline]: what time is it")
                            for ln in logs))
        self.assertTrue(any(ln == "[Offline] It is noon." for ln in logs))
        self.assertTrue(any("Degraded listening active" in ln for ln in logs))

    def test_mic_open_failure_drops_the_flag(self):
        async def scenario():
            live = main.JarvisLive.__new__(main.JarvisLive)
            live.ui = _RecorderUI()
            live.session = None
            live._degraded = True
            live._degraded_failures = 3

            class _NoMic:
                def InputStream(self, **_kw):
                    raise RuntimeError("no input devices")

            real_sd = main.sd
            main.sd = _NoMic()
            try:
                await asyncio.wait_for(live._degraded_loop(), 2)
                return live._degraded, live.ui.logs
            finally:
                main.sd = real_sd

        flag, logs = asyncio.run(scenario())
        self.assertFalse(flag)          # flag ⇔ a loop is actually running
        self.assertTrue(any("needs a microphone" in ln for ln in logs))


if __name__ == "__main__":
    unittest.main()
