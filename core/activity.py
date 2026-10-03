"""
Activity feed — Phase 32, the HUD's half of §3.15.

M11 wired the event bus and bridged every event to the dashboard (the
/api/events ring + live /ws push). The audit's remaining line was "expose
stream to dashboard + activity center (Phase 32)": this is the activity
center's model — a bounded, lock-protected ring of one-line summaries.

Threading contract: the asyncio loop thread appends (bus subscriber in
main.py), the GUI thread drains on its own QTimer inside
ui.ActivityOverlay. Only a lock-guarded deque ever crosses threads — no Qt
object is touched from a bus callback, the same store/poll seam
JarvisUI.set_audio_level already uses.

Nothing here may raise: the bridge wraps it anyway, because observability
must not be able to break the thing it observes.
"""
from __future__ import annotations

import threading
import time
from collections import deque

DEFAULT_MAXLEN = 200

# data["kind"] values the app actually emits (main.py), humanized.
_KIND_LABELS = {
    "emergency_stop": "Emergency stop — mic muted from the dashboard",
    "degraded_start": "Degraded mode engaged — offline voice fallback (§3.6)",
    "degraded_end":   "Degraded mode ended — live session reconnected",
    # Automation V0.1 (Phase 12) — run-level lifecycle from
    # core/automation/runtime.py (node-level detail stays in run records).
    "run_started":           "Automation run started",
    "run_completed":         "Automation run completed",
    "run_failed":            "Automation run failed",
    "run_cancelled":         "Automation run cancelled",
    "run_paused":            "Automation run paused",
    "run_resumed":           "Automation run resumed",
    "run_waiting_approval":  "Automation run waiting for approval",
    "run_dry_run":           "Automation dry run finished",
    "approval_waiting":      "Automation needs confirmation on screen",
}

_ERROR_TYPES = frozenset({"tool_error", "system_error", "user_interrupt"})


def summarize(event_type: str, source: str, data: dict | None) -> str:
    """One human line per event. Defensive: any shape at all, no exceptions."""
    data = data if isinstance(data, dict) else {}
    kind = str(data.get("kind") or "")
    if kind == "emergency_stop" or event_type == "user_interrupt":
        return _KIND_LABELS["emergency_stop"]
    if kind in _KIND_LABELS:
        label = _KIND_LABELS[kind]
        if kind.startswith(("run_", "approval_")):
            name = str(data.get("name") or "")
            if name:
                label = f"{label} — {name}"
        return label
    if kind:
        return kind.replace("_", " ").capitalize()
    if event_type in ("tool_result", "tool_error"):
        ok = bool(data.get("ok"))
        body = " ".join(str(data.get("result") or "").split())[:90]
        mark = "ok" if ok else "FAILED"
        head = f"{source} {mark}" if source else mark
        return f"{head} — {body}" if body else head
    if event_type == "system_startup":
        return "Session started"
    if event_type == "health_check":
        return "Health check"
    head = event_type.replace("_", " ") if event_type else "event"
    if source and source != "jarvis":
        head = f"{head} [{source}]"
    return head.capitalize()


class ActivityFeed:
    """Bounded ring of activity rows with a monotonic sequence number.

    ``seq`` never resets — not even on clear() — so a UI cursor stays valid
    across a clear/new-append cycle.
    """

    def __init__(self, maxlen: int = DEFAULT_MAXLEN) -> None:
        self._lock = threading.Lock()
        self._rows: deque[dict] = deque(maxlen=maxlen)
        self._seq = 0

    def append(self, event) -> dict | None:
        """Store one row from an Event (or its to_dict()/any dict shape).
        Returns the stored row, or None when the input was unusable."""
        try:
            if isinstance(event, dict):
                payload = event
            else:
                payload = event.to_dict()
            etype = str(payload.get("event_type") or "custom")
            source = str(payload.get("source") or "")
            data = payload.get("data")
            data = data if isinstance(data, dict) else {}
            try:
                ts = float(payload.get("timestamp"))
            except (TypeError, ValueError):
                ts = time.time()
            prio = payload.get("priority")
            try:
                prio = int(prio)
            except (TypeError, ValueError):
                prio = 20
            with self._lock:
                self._seq += 1
                row = {
                    "seq": self._seq,
                    "time": time.strftime("%H:%M:%S", time.localtime(ts)),
                    "type": etype,
                    "source": source,
                    "text": summarize(etype, source, data),
                    "critical": prio <= 0 or etype in _ERROR_TYPES,
                }
                self._rows.append(row)
            return row
        except Exception:
            return None

    def drain_since(self, seq: int) -> list[dict]:
        """Rows newer than a UI cursor (seq > cursor), oldest first."""
        try:
            with self._lock:
                return [r for r in self._rows if r["seq"] > seq]
        except Exception:
            return []

    def entries(self) -> list[dict]:
        with self._lock:
            return list(self._rows)

    def clear(self) -> None:
        with self._lock:
            self._rows.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._rows)
