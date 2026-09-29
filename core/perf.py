"""Startup performance instrumentation.

AETHER-I's audit (§3.19) says: *never optimize by guess — instrument first*.
This module is the instrumentation: a dependency-free stopwatch whose marks are
sprinkled through ``main.py`` at every startup phase the audit names as a
suspect (imports, UI construction, action/plugin discovery, spatial engine).

Design constraints:

* **Zero dependencies, zero cost when unused.** ``mark()`` is a
  ``perf_counter()`` append — microseconds. Importing this module must never
  pull anything heavy, because it is imported at the very top of ``main.py``
  *before* the heavy imports it exists to measure.
* **Always collect, print on demand.** Marks are always recorded (so a
  benchmark subprocess can print them); the human-readable report is printed
  only when ``AETHER_PERF=1`` is set or when something calls ``report()``.
* **Resettable**, so tests can drive it without cross-test bleed.

Read the numbers with ``tools/bench_startup.py``; the phase table it prints is
built from these marks.
"""
from __future__ import annotations

import os
import time
from typing import Dict, List

_marks: List[tuple] = []          # (name, perf_counter value, extra)
_t0: float = time.perf_counter()  # module import = first mark


def mark(name: str, extra: object = None) -> None:
    """Record a named startup phase at 'now'. O(1); never raises."""
    try:
        _marks.append((name, time.perf_counter(), extra))
    except Exception:
        pass


def reset() -> None:
    """Forget all marks (tests). Restarts the origin at now."""
    global _t0
    _marks.clear()
    _t0 = time.perf_counter()


def started_at() -> float:
    """perf_counter value of the origin mark (module import / last reset)."""
    return _t0


def marks() -> List[tuple]:
    """Copy of recorded marks: [(name, perf_counter, extra), ...]."""
    return list(_marks)


def phase_table() -> List[tuple]:
    """Marks turned into rows: (name, total_s, delta_s, extra).

    * total_s — time since the origin mark (module import of main.py)
    * delta_s — time since the previous mark (what this phase itself cost)
    """
    rows = []
    prev = _t0
    for name, at, extra in _marks:
        rows.append((name, at - _t0, at - prev, extra))
        prev = at
    return rows


def report() -> str:
    """Human-readable startup timeline."""
    rows = phase_table()
    if not rows:
        return "[perf] no marks recorded"
    width = max(len(r[0]) for r in rows)
    lines = ["[perf] startup timeline (since main.py import):"]
    for name, total, delta, extra in rows:
        suffix = f"  {extra}" if extra else ""
        lines.append(
            f"[perf]   {name:<{width}}  +{delta * 1000:8.1f} ms  "
            f"(total {total * 1000:8.1f} ms){suffix}"
        )
    return "\n".join(lines)


def print_report_if_enabled() -> None:
    """Print the report when AETHER_PERF=1 (opt-in; silent otherwise)."""
    if os.environ.get("AETHER_PERF", ""):
        print(report())


# Convenience flag for call sites that want to skip extra bookkeeping.
ENABLED: bool = bool(os.environ.get("AETHER_PERF", ""))
