"""HUD paint-cost bench — V2 spec §12: measure, never claim.

Renders the neural reactor core offscreen for a number of frames per state
and reports real wall-clock paint costs (avg / p50 / p95 / max ms) against
the 60 Hz budget of 16.67 ms. Pure stdlib + the app's own PyQt — no
invented numbers, no assertions, safe to run on any box.

Usage:
    python tools/hud_paint_bench.py [--frames 200] [--size 640x420]
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time

# repo root on sys.path when launched as tools/hud_paint_bench.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _pct(values: list[float], p: float) -> float:
    """Nearest-rank percentile (ceil), same convention as the Ollama drill."""
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(1, math.ceil(p / 100.0 * len(ordered))) - 1
    return ordered[min(k, len(ordered) - 1)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--frames", type=int, default=200,
                    help="timed frames per scenario (default 200)")
    ap.add_argument("--size", default="640x420",
                    help="widget size WxH (default 640x420)")
    ap.add_argument("--states", default="IDLE,EXECUTING,VERIFYING",
                    help="comma list of states to measure")
    args = ap.parse_args()

    try:
        w_s, h_s = args.size.lower().split("x")
        W, H = int(w_s), int(h_s)
    except Exception:
        print(f"bad --size: {args.size!r} (want WxH)")
        return 2

    from PyQt6.QtGui import QImage, QPainter
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    import ui

    hud = ui.HudCanvas("", "BENCH")
    hud.hud_style = "core"
    hud.resize(W, H)
    hud._paint_perf = False          # we time the render() call itself

    results = []
    for state in [s.strip() for s in args.states.split(",") if s.strip()]:
        for reduced in (False, True):
            hud.state = state
            hud._reduced_motion = reduced
            if reduced:
                hud._particles = []
            else:
                from core import reactor_visual as rviz
                hud._particles = rviz.spawn_particles(
                    rviz.motion_for(state).particle_budget)
            img = QImage(W, H, QImage.Format.Format_RGB32)
            img.fill(0)

            # warm-up (grid cache, geometry) — not timed
            for _ in range(20):
                p = QPainter(img)
                hud.render(p)
                p.end()

            samples: list[float] = []
            for _ in range(args.frames):
                p = QPainter(img)
                t0 = time.perf_counter()
                hud.render(p)
                p.end()
                samples.append((time.perf_counter() - t0) * 1000.0)

            avg = sum(samples) / len(samples)
            results.append((state, reduced, avg, _pct(samples, 50),
                            _pct(samples, 95), max(samples)))
            print(f"state={state:<10} reduced={str(reduced):<5} "
                  f"frames={args.frames:<4} avg={avg:6.2f}ms "
                  f"p50={_pct(samples, 50):6.2f} p95={_pct(samples, 95):6.2f} "
                  f"max={max(samples):6.2f}  (60Hz budget 16.67ms)")

    hud.close()
    avg_ok = all(r[2] < 16.67 for r in results)
    p95_ok = all(r[4] < 16.67 for r in results)
    print(f"scenarios with avg < 16.67 ms (60 Hz budget): {avg_ok}")
    print(f"scenarios with p95 < 16.67 ms (60 Hz budget): {p95_ok} "
          f"--- quote BOTH in the docs, never just the averages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
