"""M5 — startup instrumentation, lazy loading, benchmark script (§3.19, §3.6).

Three layers:

1. ``core.perf`` unit tests — marks, deltas, report, reset.
2. Source guards — instrumentation points exist in main.py/ui.py; the heavy
   imports (core.spatial → mediapipe, gesture cursor overlay) must appear
   ONLY inside lazy builders, never at module top level.
3. Behavioural — in-process fake-engine test of ``_ensure_gesture_engine``
   (idempotent, binds UI, never auto-starts), and a subprocess boot with
   gesture disabled asserting mediapipe/core.spatial are NOT imported during
   a full JarvisUI + JarvisLive construction.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import perf  # noqa: E402
import main  # noqa: E402

MAIN_PY = (ROOT / "main.py").read_text(encoding="utf-8")
UI_PY = (ROOT / "ui.py").read_text(encoding="utf-8")
BENCH_PY = (ROOT / "tools" / "bench_startup.py").read_text(encoding="utf-8")


class TestCorePerf(unittest.TestCase):
    def setUp(self):
        perf.reset()

    def tearDown(self):
        perf.reset()

    def test_marks_record_monotonic_time(self):
        perf.mark("a")
        time.sleep(0.01)
        perf.mark("b")
        rows = perf.phase_table()
        self.assertEqual([r[0] for r in rows], ["a", "b"])
        self.assertGreaterEqual(rows[1][2], 0.01)   # delta since previous mark

    def test_totals_accumulate_from_origin(self):
        perf.mark("a")
        time.sleep(0.01)
        perf.mark("b")
        rows = perf.phase_table()
        self.assertGreaterEqual(rows[1][1], rows[0][1])   # total is cumulative
        self.assertGreaterEqual(rows[1][1], 0.01)

    def test_mark_never_raises_and_accepts_extra(self):
        perf.mark("with_extra", extra="hello")
        name, _, extra = perf.marks()[0]
        self.assertEqual((name, extra), ("with_extra", "hello"))

    def test_reset_clears_marks_and_origin(self):
        perf.mark("a")
        perf.reset()
        self.assertEqual(perf.marks(), [])
        self.assertEqual(perf.phase_table(), [])

    def test_report_contains_every_mark(self):
        perf.mark("phase_one")
        perf.mark("phase_two", extra="x")
        report = perf.report()
        self.assertIn("phase_one", report)
        self.assertIn("phase_two", report)
        self.assertIn("x", report)
        self.assertIn("ms", report)

    def test_empty_report_is_honest(self):
        self.assertEqual(perf.report(), "[perf] no marks recorded")

    def test_print_report_is_opt_in(self):
        with mock.patch.dict(os.environ, {"AETHER_PERF": ""}):
            with mock.patch("builtins.print") as p:
                perf.print_report_if_enabled()
            p.assert_not_called()
        with mock.patch.dict(os.environ, {"AETHER_PERF": "1"}):
            with mock.patch("builtins.print") as p:
                perf.print_report_if_enabled()
            p.assert_called_once()


class TestInstrumentationSource(unittest.TestCase):
    """The benchmark's phase table only works if the marks stay in the code."""

    def test_main_records_all_boot_phases(self):
        for mark_name in ("import:sounddevice+numpy", "import:google-genai",
                          "import:ui", "import:actions+memory+core",
                          "imports_done", "ui_constructed", "tray_started",
                          "discover_actions", "discover_plugins",
                          "spatial_init", "jarvis_constructed"):
            self.assertIn(f'"{mark_name}"', MAIN_PY, mark_name)

    def test_main_prints_report_only_when_enabled(self):
        self.assertIn("_perf_report()", MAIN_PY)

    def test_ui_records_window_phases(self):
        for mark_name in ("ui:mainwindow_start", "ui:mainwindow_done",
                          "ui:ready"):
            self.assertIn(f'"{mark_name}"', UI_PY, mark_name)

    def test_spatial_import_only_inside_lazy_builder(self):
        # No module-level core.spatial import anywhere — everything is
        # indented inside a function/method.
        for line in MAIN_PY.splitlines():
            if line.startswith(("from core.spatial", "import core.spatial")):
                self.fail(f"eager core.spatial import: {line!r}")
        for line in UI_PY.splitlines():
            if line.startswith(("from core.spatial", "import core.spatial")):
                self.fail(f"eager core.spatial import: {line!r}")
        self.assertIn("def _ensure_gesture_engine", MAIN_PY)
        self.assertIn("def _ensure_gesture_cursor", UI_PY)

    def test_lazy_builder_is_wired_to_the_ui(self):
        self.assertIn("self.ui.on_gesture_engine_needed = self._ensure_gesture_engine",
                      MAIN_PY)
        self.assertEqual(MAIN_PY.count("def _ensure_gesture_engine"), 1)
        # Both UI toggles resolve the engine through the callback.
        self.assertIn("self.on_gesture_engine_needed()", UI_PY)
        self.assertGreaterEqual(
            UI_PY.count("self.on_gesture_engine_needed"), 3)  # init + 2 toggles


class TestLazyGestureEngine(unittest.TestCase):
    """_ensure_gesture_engine: idempotent, binds UI, never auto-starts."""

    def _make_live(self):
        live = main.JarvisLive.__new__(main.JarvisLive)
        live._bound = []
        live.ui = types.SimpleNamespace(
            set_gesture_engine=lambda e: live._bound.append(e))
        live._gesture_engine = None
        return live

    @staticmethod
    def _fake_spatial_modules(record):
        pkg = types.ModuleType("core.spatial")
        pkg.__path__ = []                      # package-shaped, never executed

        class FakeSettings:
            def __init__(self, **kw):
                self.__dict__.update(kw)

        class FakeEngine:
            def __init__(self, settings):
                record["constructed"] = record.get("constructed", 0) + 1
                self.settings = settings
                self.on_gesture_event = None

            def start(self):
                record["started"] = record.get("started", 0) + 1
                return True

        engine_mod = types.ModuleType("core.spatial.engine")
        engine_mod.SpatialInteractionEngine = FakeEngine
        state_mod = types.ModuleType("core.spatial.interaction_state")
        state_mod.GestureSettings = FakeSettings
        return {
            "core.spatial": pkg,
            "core.spatial.engine": engine_mod,
            "core.spatial.interaction_state": state_mod,
        }, FakeEngine

    def test_builds_once_and_binds_ui(self):
        live = self._make_live()
        record: dict = {}
        fakes, FakeEngine = self._fake_spatial_modules(record)
        with mock.patch.dict(sys.modules, fakes):
            e1 = live._ensure_gesture_engine()
            e2 = live._ensure_gesture_engine()
        self.assertIsInstance(e1, FakeEngine)
        self.assertIs(e1, e2, "must be idempotent")
        self.assertEqual(record.get("constructed"), 1, "built exactly once")
        self.assertEqual(live._bound, [e1], "engine handed to the UI once")
        self.assertIsNotNone(e1.on_gesture_event, "event hook wired")

    def test_ensure_never_starts_the_engine(self):
        live = self._make_live()
        record: dict = {}
        fakes, _ = self._fake_spatial_modules(record)
        with mock.patch.dict(sys.modules, fakes):
            live._ensure_gesture_engine()
        self.assertEqual(record.get("started", 0), 0,
                         "boot auto-start and toggles own start(), not ensure")

    def test_returns_existing_engine_without_import(self):
        live = self._make_live()
        sentinel = object()
        live._gesture_engine = sentinel
        # No fake modules patched — if this imported core.spatial it would
        # pay for (broken) mediapipe or fail loudly.
        self.assertIs(live._ensure_gesture_engine(), sentinel)
        self.assertEqual(live._bound, [])


BOOT_CHECK = textwrap.dedent(r"""
    import os, sys
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    import memory.config_manager as cm
    cm.get_gesture_settings = lambda: {
        "gesture_enabled": False, "gesture_camera_index": 0,
        "gesture_confidence_threshold": 0.7, "gesture_smoothing": 0.3,
        "gesture_mirror_mode": True}
    import main
    from core import perf
    ui = main.JarvisUI("face.png")
    jarvis = main.JarvisLive(ui)
    names = [m[0] for m in perf.marks()]
    print("mediapipe:", "mediapipe" in sys.modules)
    print("spatial:", "core.spatial" in sys.modules)
    print("engine:", jarvis._gesture_engine)
    print("wired:", jarvis.ui.on_gesture_engine_needed is not None)
    print("marks_ok:", all(n in names for n in
          ("imports_done", "discover_actions", "discover_plugins",
           "spatial_init")))
    sys.stdout.flush()
    os._exit(0)
""")


class TestBootDoesNotLoadOptionalSubsystems(unittest.TestCase):
    """Full boot with gesture OFF must not import mediapipe/core.spatial."""

    def test_boot_skips_mediapipe_when_gesture_disabled(self):
        env = os.environ.copy()
        env["QT_QPA_PLATFORM"] = "offscreen"
        proc = subprocess.run(
            [sys.executable, "-c", BOOT_CHECK],
            capture_output=True, text=True, timeout=180,
            cwd=str(ROOT), env=env,
        )
        out = proc.stdout or ""
        self.assertEqual(proc.returncode, 0,
                         f"boot failed:\n{out}\n{proc.stderr}")
        self.assertIn("mediapipe: False", out)
        self.assertIn("spatial: False", out)
        self.assertIn("engine: None", out)
        self.assertIn("wired: True", out)
        self.assertIn("marks_ok: True", out)


class TestBenchmarkScript(unittest.TestCase):
    def test_import_machinery_runs_in_a_fresh_interpreter(self):
        sys.path.insert(0, str(ROOT / "tools"))
        try:
            import bench_startup as bench
            rows = bench.bench_imports(["core.perf"])
        finally:
            sys.path.remove(str(ROOT / "tools"))
            sys.modules.pop("bench_startup", None)
        self.assertEqual(len(rows), 1)
        name, seconds, note = rows[0]
        self.assertEqual(name, "core.perf")
        self.assertIsInstance(seconds, float)
        self.assertGreaterEqual(seconds, 0.0)
        self.assertEqual(note, "")

    def test_targets_cover_the_milestone_suspects(self):
        sys.path.insert(0, str(ROOT / "tools"))
        try:
            import bench_startup as bench
        finally:
            sys.path.remove(str(ROOT / "tools"))
            sys.modules.pop("bench_startup", None)
        for target in ("ui", "main", "core.wake_word", "core.spatial.engine",
                       "actions.screen_processor"):
            self.assertIn(target, bench.IMPORT_TARGETS)
        self.assertLessEqual(set(bench.QUICK_TARGETS), set(bench.IMPORT_TARGETS))
        self.assertIn("main", bench.QUICK_TARGETS)
        for probe in ("wake_detector_first_touch", "camera_frame_grab",
                      "spatial_engine_construct"):
            self.assertIn(probe, bench.PROBES)

    def test_documented_usage(self):
        self.assertIn("--quick", BENCH_PY)
        self.assertIn("boot phase timeline", BENCH_PY)


if __name__ == "__main__":
    unittest.main()
