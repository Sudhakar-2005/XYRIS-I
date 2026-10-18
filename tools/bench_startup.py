"""Startup benchmark for XYRIS I V3 (audit §3.19).

"Never optimize by guess" — this script produces the numbers:

1. **Cold import cost** — every suspect module imported in its own fresh
   interpreter, wall-clock measured (main.py's suspects: ui.py 265 KB,
   sounddevice, google.genai, core.wake_word, actions/screen_processor,
   core.spatial, plugin/action discovery, main itself).
2. **Boot phase timeline** — constructs JarvisUI + JarvisLive offscreen and
   prints the ``core.perf`` marks recorded by main.py (imports → UI →
   discovery → spatial). Requires ``AETHER_PERF=1``-style reporting, which
   this script triggers directly via ``core.perf.report()``.
3. **Lazy subsystem probes** — first-touch cost of the things V3 wants to
   lazy-load: wake-word detector construction, camera frame grab, spatial
   engine construction. Failures (no camera, no model) are reported as
   "n/a" — a probe never crashes the benchmark.

Usage:
    python tools/bench_startup.py            # full run (imports + boot + probes)
    python tools/bench_startup.py --quick    # import timings only (smoke/tests)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Suspects from §3.19 + the V3 lazy-load targets from the M5 milestone row.
IMPORT_TARGETS = [
    "ui",
    "sounddevice",
    "numpy",
    "google.genai",
    "core.wake_word",
    "core.plugin_loader",
    "core.action_loader",
    "actions.screen_processor",
    "actions.system_monitor",
    "core.spatial.engine",
    "memory.memory_manager",
    "main",
]
QUICK_TARGETS = ["ui", "core.wake_word", "actions.screen_processor", "main"]


def _env() -> dict:
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"     # PyQt6 tray/UI imports headless
    env["PYTHONIOENCODING"] = "utf-8"
    env["AETHER_PERF"] = "1"
    return env


def _run(code: str, timeout: float = 120.0) -> tuple:
    """Run code in a fresh interpreter; return (ok, stdout, elapsed_s)."""
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(ROOT), env=_env(),
        )
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout}s", time.perf_counter() - t0
    elapsed = time.perf_counter() - t0
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    return proc.returncode == 0, out or err, elapsed


def bench_imports(targets: list) -> list:
    rows = []
    for mod in targets:
        code = (
            "import time;"
            "t=time.perf_counter();"
            f"import {mod};"
            "print(f'{time.perf_counter()-t:.3f}')"
        )
        ok, out, _ = _run(code)
        try:
            seconds = float(out.splitlines()[-1]) if ok else None
        except (ValueError, IndexError):
            seconds = None
        rows.append((mod, seconds, out if seconds is None else ""))
    return rows


def bench_boot() -> str:
    """Construct the real app offscreen; print main.py's phase marks."""
    code = (
        "import os;"
        "os.environ['QT_QPA_PLATFORM']='offscreen';"
        "from core import perf;"
        "perf.reset();"
        "perf.mark('bench_start');"
        "import main;"
        "ui = main.JarvisUI('face.png');"
        "perf.mark('ui_constructed');"
        "jarvis = main.JarvisLive(ui);"
        "perf.mark('jarvis_constructed');"
        "print(perf.report())"
    )
    ok, out, _ = _run(code, timeout=180.0)
    return out if ok else f"BOOT FAILED: {out}"


PROBES = {
    "wake_detector_first_touch": (
        "from core.wake_word import WakeWordDetector;"
        "import time;"
        "t=time.perf_counter();"
        "d=WakeWordDetector(on_detect=lambda:None, logger=lambda m:None);"
        "print(f'{time.perf_counter()-t:.3f}')"
    ),
    "camera_frame_grab": (
        "from actions.screen_processor import _capture_camera;"
        "import time;"
        "t=time.perf_counter();"
        "_capture_camera();"
        "print(f'{time.perf_counter()-t:.3f}')"
    ),
    "spatial_engine_construct": (
        "from core.spatial.engine import SpatialInteractionEngine;"
        "from core.spatial.interaction_state import GestureSettings;"
        "import time;"
        "t=time.perf_counter();"
        "SpatialInteractionEngine(GestureSettings());"
        "print(f'{time.perf_counter()-t:.3f}')"
    ),
}


def bench_probes() -> list:
    rows = []
    for name, code in PROBES.items():
        ok, out, _ = _run(code, timeout=90.0)
        try:
            seconds = float(out.splitlines()[-1]) if ok else None
        except (ValueError, IndexError):
            seconds = None
        rows.append((name, seconds, "" if seconds is not None else out))
    return rows


def _fmt(seconds) -> str:
    return f"{seconds * 1000:9.1f} ms" if seconds is not None else "      n/a"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--quick", action="store_true",
                        help="import timings for the fast subset only")
    args = parser.parse_args(argv)

    targets = QUICK_TARGETS if args.quick else IMPORT_TARGETS
    print(f"== cold import cost ({'quick' if args.quick else 'full'}) ==")
    for mod, seconds, note in bench_imports(targets):
        line = f"  {mod:<28} {_fmt(seconds)}"
        if note:
            line += f"   ({note.splitlines()[-1][:70]})"
        print(line)

    if not args.quick:
        print("\n== boot phase timeline (JarvisUI + JarvisLive, offscreen) ==")
        for ln in bench_boot().splitlines():
            print(f"  {ln}")

        print("\n== lazy subsystem first-touch probes ==")
        for name, seconds, note in bench_probes():
            line = f"  {name:<28} {_fmt(seconds)}"
            if note:
                line += f"   ({note.splitlines()[-1][:70]})"
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
