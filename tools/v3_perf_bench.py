"""V3 Phase 9 — measured performance + contrast for the dashboard surfaces.

Honesty rules (DASHBOARD_V3_DESIGN): real timings on this machine via
perf_counter, reported as avg/p95 over N iterations; contrast computed
from the actual token hex values with the WCAG 2.1 relative-luminance
formula. Nothing here is a fabricated benchmark — rerun and the numbers
change with the hardware.

Usage:
    QT_QPA_PLATFORM=offscreen python tools/v3_perf_bench.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtWidgets import QApplication   # noqa: E402


def _pct(times: list[float], p: float) -> float:
    s = sorted(times)
    return s[min(len(s) - 1, max(0, int(len(s) * p) - 1))]


def bench(label: str, fn, iters: int = 200) -> tuple[float, float]:
    fn()                                   # warm-up / JIT-ish
    times: list[float] = []
    for _ in range(iters):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000.0)
    avg = sum(times) / len(times)
    p95 = _pct(times, 0.95)
    print("%-40s avg %7.3f ms   p95 %7.3f ms   n=%d"
          % (label, avg, p95, iters))
    return avg, p95


# ── contrast ────────────────────────────────────────────────────────────────

def _lum(hexcol: str) -> float:
    h = hexcol.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = f(r), f(g), f(b)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def contrast_report() -> bool:
    from ui import C
    bg, panel, panel2, dark = C.BG, C.PANEL, C.PANEL2, C.DARK
    pairs = [
        ("TEXT",     C.TEXT,     "BG",     bg),
        ("TEXT_MED", C.TEXT_MED, "BG",     bg),
        ("TEXT_MED", C.TEXT_MED, "PANEL2", panel2),
        ("TEXT_DIM", C.TEXT_DIM, "BG",     bg),
        ("TEXT_DIM", C.TEXT_DIM, "PANEL2", panel2),
        ("PRI",      C.PRI,      "DARK",   dark),
        ("GREEN",    C.GREEN,    "PANEL2", panel2),
        ("RED",      C.RED,      "PANEL2", panel2),
        ("ACC2",     C.ACC2,     "PANEL2", panel2),
        ("ACC",      C.ACC,      "PANEL2", panel2),
        ("MUTED_C",  C.MUTED_C,  "BG",     bg),
        ("WHITE",    C.WHITE,    "PANEL",  panel),
    ]
    ok = True
    print("\n-- contrast (WCAG 2.1 AA small text >= 4.5:1) --")
    for n1, c1, n2, c2 in pairs:
        r = contrast(c1, c2)
        grade = ("AA-small" if r >= 4.5
                 else "AA-large/UI" if r >= 3.0 else "FAIL")
        if r < 4.5:
            ok = False
        print("  %-9s on %-7s %5.2f:1  %s" % (n1, n2, r, grade))
    return ok


# ── widget benches ──────────────────────────────────────────────────────────

def main() -> int:
    from core.activity import ActivityFeed
    from ui import MainWindow, _FeedPanel, _TasksPanel

    app = QApplication.instance() or QApplication([])
    win = MainWindow("face.png")
    win.hide()

    print("-- V3 surface timings (offscreen, this machine) --")

    def nav_switch():
        i = win._pages.currentIndex()
        win._on_nav(win._nav_btns[("system", "command", "memory")[
            (i + 1) % 3]])
    bench("nav page switch (_on_nav)", nav_switch, 300)

    bench("metric tick (_update_metrics)", win._update_metrics, 200)
    bench("memory stats (_refresh_mem_stats)", win._refresh_mem_stats, 100)
    bench("provider card (_update_provider_card)",
          win._update_provider_card, 100)
    bench("state sink (_apply_state EXECUTING)",
          lambda: win._apply_state("EXECUTING"), 300)

    # feed pull across a realistic backlog (cursor reset each pass)
    feed = ActivityFeed(maxlen=200)
    for i in range(100):
        feed.append({"event_type": "custom", "source": "s",
                     "data": {"kind": "note", "n": i},
                     "timestamp": float(i), "priority": 20})
    panel = _FeedPanel(win=None)
    panel._feed = feed

    def feed_pull():
        panel._cursor = 0
        while panel._rows.count() - 1:
            it = panel._rows.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        panel._pull()
    bench("feed pull, 100-row backlog (first open)", feed_pull, 100)

    # steady state: cursor caught up, one new row per tick
    panel._cursor = 0
    panel._pull()                      # drain the backlog once
    _seq = [1000]

    def feed_steady():
        _seq[0] += 1
        feed.append({"event_type": "custom", "source": "s",
                     "data": {"kind": "note", "n": _seq[0]},
                     "timestamp": float(_seq[0]), "priority": 20})
        panel._pull()
    bench("feed pull, steady state (1 new row)", feed_steady, 200)

    # tasks refresh across a store of 30 runs
    class _Store:
        def all_runs(self, limit=100):
            return [{"run_id": "wf%d__%04d" % (i % 5, i),
                     "workflow_id": "wf%d" % (i % 5),
                     "status": ("RUNNING" if i == 0 else
                                "COMPLETED" if i % 3 else "FAILED"),
                     "trigger": "manual", "error": ""}
                    for i in range(30)]
    win._automation_store = _Store()
    tasks = _TasksPanel(win=win)
    bench("tasks refresh, 30 runs", tasks._refresh, 100)
    tasks.deleteLater()

    panel.deleteLater()
    try:
        win._metric_tmr.stop()
    except Exception:
        pass
    win.deleteLater()
    app.processEvents()

    ok = contrast_report()
    print("\ncontrast AA (small text): %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
