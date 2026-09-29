"""M6 — reconnect/resume hardening (§3.8) + v1 memory recall guard (§3.11).

The audit asked for exactly this: "formalize resume: on reconnect, restore
context then fresh session; transcript dedup — Test: reconnect unit tests".
Covered here without any network session:

* ``_ReconnectSignal`` / ``_is_reconnect_signal`` / ``_keep_context_of`` —
  including TaskGroup-wrapped groups (the shapes run() actually sees).
* ``request_reconnect`` / ``_watch_reconnect`` — the voluntary reconnect path.
* ``_prepare_voluntary_reconnect`` — keep_context drops the handle, the
  default never silently wipes the conversation.
* ``_note_resumption_update`` — only resumable handles are kept, newest wins.
* ``_is_resumption_rejection`` — dead-handle classification matrix (a dead
  handle must not block reconnect; a fresh session must not mask real errors).
* ``_is_repeat_chunk`` — transcript dedup.
* ``_build_config`` — the connect config really replays ``self._resume_handle``,
  and the system instruction is assembled by v1's ``format_memory_for_prompt``
  (the §3.11 keep-v1 decision).
"""
from __future__ import annotations

import asyncio
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main  # noqa: E402

MAIN_PY = (ROOT / "main.py").read_text(encoding="utf-8")


def _bare_live(**attrs):
    """JarvisLive without __init__ — just enough state for resume helpers."""
    live = main.JarvisLive.__new__(main.JarvisLive)
    live._resume_handle = None
    live._conn_backoff = 3
    live._reconnect_keep = True
    live._reconnect_reason = ""
    live._reconnect_event = None
    live._loop = None
    live.ui = types.SimpleNamespace(write_log=lambda msg: attrs.setdefault(
        "_logs", []).append(msg))
    for k, v in attrs.items():
        if not k.startswith("_logs"):
            setattr(live, k, v)
    return live


class TestReconnectSignal(unittest.TestCase):
    def test_signal_carries_keep_flag(self):
        self.assertTrue(main._ReconnectSignal().keep_context)
        self.assertFalse(main._ReconnectSignal(keep_context=False).keep_context)

    def test_is_reconnect_signal_plain(self):
        self.assertTrue(main._is_reconnect_signal(main._ReconnectSignal()))
        self.assertFalse(main._is_reconnect_signal(ValueError("boom")))

    def test_is_reconnect_signal_inside_taskgroup(self):
        grp = ExceptionGroup("g", [ValueError("other"),
                                   main._ReconnectSignal(keep_context=False)])
        self.assertTrue(main._is_reconnect_signal(grp))

    def test_is_reconnect_signal_nested_groups(self):
        inner = ExceptionGroup("inner", [main._ReconnectSignal()])
        outer = ExceptionGroup("outer", [inner])
        self.assertTrue(main._is_reconnect_signal(outer))

    def test_keep_context_reads_flag_through_group(self):
        grp = ExceptionGroup("g", [main._ReconnectSignal(keep_context=False)])
        self.assertFalse(main._keep_context_of(grp))

    def test_keep_context_defaults_true_for_unknown_shapes(self):
        # An unexpected shape must never silently wipe the conversation.
        self.assertTrue(main._keep_context_of(ValueError("boom")))
        self.assertTrue(main._keep_context_of(ExceptionGroup("g", [ValueError()])))

    def test_keep_context_finds_signal_among_many(self):
        grp = ExceptionGroup("g", [ValueError(),
                                   main._ReconnectSignal(keep_context=False)])
        self.assertFalse(main._keep_context_of(grp))


class TestTranscriptDedup(unittest.TestCase):
    def test_short_chunk_repeats_only_exact_previous(self):
        self.assertTrue(main._is_repeat_chunk("ok", ["ok"]))
        self.assertFalse(main._is_repeat_chunk("ok", ["different"]))

    def test_short_chunk_with_empty_buf(self):
        self.assertFalse(main._is_repeat_chunk("ok", []))

    def test_long_chunk_matches_anywhere_in_turn(self):
        buf = ["Earlier: the answer is 42, computed"]
        self.assertTrue(main._is_repeat_chunk("answer is 42", buf))

    def test_long_chunk_new_content_passes(self):
        buf = ["Earlier: the answer is 42, computed"]
        self.assertFalse(main._is_repeat_chunk("and now for something else", buf))

    def test_long_chunk_against_empty_buf(self):
        self.assertFalse(main._is_repeat_chunk("a long enough chunk", []))


class TestRequestReconnect(unittest.TestCase):
    def test_noop_before_loop_exists(self):
        live = _bare_live()
        live.request_reconnect(keep_context=False, reason="early")  # must not raise
        self.assertFalse(live._reconnect_keep)
        self.assertEqual(live._reconnect_reason, "early")

    def test_sets_event_thread_safely(self):
        live = _bare_live()
        live._reconnect_event = ev = asyncio.Event()
        calls = []

        class FakeLoop:
            def call_soon_threadsafe(self, fn):
                calls.append(fn)

        live._loop = FakeLoop()
        live.request_reconnect(keep_context=False, reason="audio device")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], ev.set)
        self.assertFalse(live._reconnect_keep)
        self.assertEqual(live._reconnect_reason, "audio device")


class TestWatchReconnect(unittest.TestCase):
    def _run_watch(self, keep, reason):
        logs: list = []
        live = _bare_live(logs=logs)
        live._reconnect_event = ev = asyncio.Event()
        live._reconnect_keep = keep
        live._reconnect_reason = reason
        live.ui = types.SimpleNamespace(write_log=logs.append)
        ev.set()                                   # request already pending

        async def scenario():
            with self.assertRaises(main._ReconnectSignal) as cm:
                await asyncio.wait_for(live._watch_reconnect(), 2.0)
            return cm.exception

        return asyncio.run(scenario()), logs

    def test_raises_signal_with_keep_context(self):
        sig, logs = self._run_watch(keep=True, reason="audio device")
        self.assertTrue(sig.keep_context)
        self.assertTrue(any("audio device" in m and "reconnecting..." in m
                            for m in logs), logs)

    def test_raises_signal_for_clean_slate(self):
        sig, logs = self._run_watch(keep=False, reason="new voice")
        self.assertFalse(sig.keep_context)
        self.assertTrue(any("fresh conversation" in m for m in logs), logs)

    def test_event_is_cleared_for_the_next_session(self):
        live = _bare_live()
        live._reconnect_event = ev = asyncio.Event()
        live._reconnect_keep = True
        live.ui = types.SimpleNamespace(write_log=lambda m: None)
        ev.set()

        async def scenario():
            with self.assertRaises(main._ReconnectSignal):
                await asyncio.wait_for(live._watch_reconnect(), 2.0)
            return ev.is_set()

        # Cleared, not left set — a stale request must not fire on the NEXT
        # session the run loop builds.
        self.assertFalse(asyncio.run(scenario()))


class TestPrepareVoluntaryReconnect(unittest.TestCase):
    def test_keep_context_preserves_handle_and_resets_backoff(self):
        live = _bare_live()
        live._resume_handle = "h-keep"
        live._prepare_voluntary_reconnect(main._ReconnectSignal(keep_context=True))
        self.assertEqual(live._resume_handle, "h-keep")
        self.assertEqual(live._conn_backoff, 0)

    def test_clean_slate_drops_handle(self):
        live = _bare_live()
        live._resume_handle = "h-drop"
        live._prepare_voluntary_reconnect(
            main._ReconnectSignal(keep_context=False))
        self.assertIsNone(live._resume_handle)
        self.assertEqual(live._conn_backoff, 0)

    def test_wrapped_clean_slate_also_drops(self):
        live = _bare_live()
        live._resume_handle = "h-drop"
        grp = ExceptionGroup("g",
                             [main._ReconnectSignal(keep_context=False)])
        live._prepare_voluntary_reconnect(grp)
        self.assertIsNone(live._resume_handle)

    def test_unknown_exception_never_drops_handle(self):
        live = _bare_live()
        live._resume_handle = "h-safe"
        live._prepare_voluntary_reconnect(ValueError("unexpected"))
        self.assertEqual(live._resume_handle, "h-safe")


class TestResumptionUpdates(unittest.TestCase):
    @staticmethod
    def _sru(resumable=True, handle="h1"):
        return types.SimpleNamespace(resumable=resumable, new_handle=handle)

    def test_none_update_is_ignored(self):
        live = _bare_live()
        live._note_resumption_update(None)
        self.assertIsNone(live._resume_handle)

    def test_first_resumable_handle_is_stored_and_announced(self):
        live = _bare_live()
        with mock.patch("builtins.print") as p:
            live._note_resumption_update(self._sru(handle="h1"))
        self.assertEqual(live._resume_handle, "h1")
        self.assertTrue(any("armed" in str(c.args) for c in p.call_args_list))

    def test_reissued_handle_replaces_previous(self):
        live = _bare_live()
        live._note_resumption_update(self._sru(handle="h1"))
        with mock.patch("builtins.print"):
            live._note_resumption_update(self._sru(handle="h2"))
        self.assertEqual(live._resume_handle, "h2")

    def test_non_resumable_update_keeps_old_handle(self):
        live = _bare_live()
        live._note_resumption_update(self._sru(handle="h1"))
        live._note_resumption_update(self._sru(resumable=False, handle="h2"))
        self.assertEqual(live._resume_handle, "h1")

    def test_update_without_handle_keeps_old(self):
        live = _bare_live()
        live._note_resumption_update(self._sru(handle="h1"))
        live._note_resumption_update(self._sru(handle=None))
        self.assertEqual(live._resume_handle, "h1")


class TestResumptionRejection(unittest.TestCase):
    """Dead handle → fresh session; real errors → normal backoff, never masked."""

    def test_dead_handle_rejected_when_resumed(self):
        for exc in (ValueError("NOT_FOUND: session gone"),
                    ValueError("invalid resumption handle"),
                    ValueError("INVALID_ARGUMENT: stale"),
                    ValueError("Handle expired")):
            self.assertTrue(main._is_resumption_rejection(exc, True), exc)

    def test_unrelated_error_while_resumed_is_not_a_rejection(self):
        self.assertFalse(
            main._is_resumption_rejection(ValueError("connection reset"), True))

    def test_never_classifies_when_not_resumed(self):
        # A fresh session that fails with NOT_FOUND is a real error — dropping
        # "the handle" we never sent would mask it.
        for exc in (ValueError("NOT_FOUND: session gone"),
                    ValueError("invalid resumption handle")):
            self.assertFalse(main._is_resumption_rejection(exc, False), exc)


class TestBuildConfig(unittest.TestCase):
    def _config_live(self):
        live = _bare_live()
        live._asst_name = "JARVIS"
        live._action_registry = types.SimpleNamespace(
            get_tool_declarations=lambda: [])
        live._plugin_registry = types.SimpleNamespace(
            get_tool_declarations=lambda: [])
        live._persona_registry = types.SimpleNamespace(
            get_system_prompt_addition=lambda: "")
        live._enhanced_live = False     # skip optional preview knobs
        live._tuned_live = False
        return live

    def _build(self, live, memory_text=""):
        with mock.patch.object(main, "load_memory", return_value={}), \
             mock.patch.object(main, "format_memory_for_prompt",
                               return_value=memory_text), \
             mock.patch.object(main, "get_voice", return_value="Kore"):
            return live._build_config()

    def test_first_connect_asks_fresh_with_null_handle(self):
        cfg = self._build(self._config_live())
        self.assertIsNone(cfg.session_resumption.handle)

    def test_reconnect_replays_the_stored_handle(self):
        live = self._config_live()
        live._resume_handle = "h-abc"
        cfg = self._build(live)
        self.assertEqual(cfg.session_resumption.handle, "h-abc")

    def test_system_instruction_is_assembled_by_v1_memory(self):
        # §3.11 decision (M6): v1's format_memory_for_prompt stays the wired
        # recall formatter — this fails if the prompt ever stops using it.
        cfg = self._build(self._config_live(),
                          memory_text="[MEMORY] v1-recall-marker")
        self.assertIn("v1-recall-marker", cfg.system_instruction)


class TestRunLoopWiring(unittest.TestCase):
    """run() and the receive loop must go through the extracted helpers."""

    def test_run_loop_uses_both_extracted_helpers(self):
        self.assertIn("self._prepare_voluntary_reconnect(e)", MAIN_PY)
        self.assertIn("_is_resumption_rejection(e, _resumed_with)", MAIN_PY)

    def test_receive_loop_routes_through_note_method(self):
        self.assertIn("self._note_resumption_update(", MAIN_PY)
        # The inline version must be gone — one choke point only.
        self.assertNotIn("_sru = getattr(response", MAIN_PY)


if __name__ == "__main__":
    unittest.main()
