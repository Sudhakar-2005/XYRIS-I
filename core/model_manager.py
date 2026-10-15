"""
ModelManager + LiveModelManager — one home for every model string the app
uses, and the record of how each rung actually behaves.

WHY THIS EXISTS (audit §3.5)
    Before this module the live model name was written twice — `main.py`
    (the conversation) and `core/gemini.py` (the one-shot fallback) — and the
    REST ladders lived in `gemini.py` with no interface around them: nothing
    recorded how often a rung answered, how long it took, or why it failed,
    so "which model should we try first" could only ever be answered from a
    one-off afternoon of manual timing. This module is that answer's home:
    the strings are defined exactly once, the ladder order is explicit, and
    every attempt through `core/gemini.py` lands in the stats.

THE LADDER IS MEASURED, NOT INVENTED
    The order below is the order from the measurements recorded in
    core/gemini.py's module docstring (Live first because of quota pools;
    pinned REST names before the rolling `-latest` aliases because those were
    the ones having a bad day). Nothing re-orders itself at runtime: a ladder
    that reshuffles mid-conversation cannot be reasoned about, and a sample
    of one afternoon is not evidence. `snapshot()` exists so the NEXT
    measurement pass — real traffic, real failures — can be based on data
    instead of memory.

COOLDOWN POLICY LIVES HERE TOO
    A rung that answered 429 is out of quota; on the free tier it stays that
    way for minutes. `record()` recognises that failure and starts the
    cooldown, so no caller has to remember the rule. A cooling rung is
    demoted to the back of its ladder by `order()`, never deleted — if
    everything is cooling the ladder still tries everything, because trying
    is better than returning nothing.

THE LIVE RUNG
    `LiveModelManager` owns the model string for the Live API and the cap on
    concurrent one-shot Live sessions (the conversation must never wait for
    a side call). The transport — throwaway sessions, the transcription
    drain — stays in core/gemini.py; this is policy, not plumbing.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# The Live model, defined once. main.py imports this for the conversation's
# session; core/gemini.py uses it for one-shot side calls. Change it HERE.
LIVE_MODEL = "models/gemini-3.1-flash-live-preview"

# Tier names. Callers pick a tier (FAST/SMART/SEARCH); gemini.call maps the
# tier to an ordered ladder of model strings through ModelManager.
FAST = "fast"      # short classification, extraction, one-line decisions
SMART = "smart"    # reasoning, generation, long documents, images
SEARCH = "search"  # grounded search — REST only (a Live turn carries no
                   # grounding metadata, see core/gemini.py)
LIVE = "live"      # rung that means "ask the Live model, throwaway session"

# Ladders, tried left to right. Measured order — see module docstring.
LADDERS: dict[str, tuple[str, ...]] = {
    FAST: (LIVE, "gemini-2.5-flash-lite", "gemini-2.5-flash"),
    SMART: (LIVE, "gemini-2.5-flash", "gemini-2.5-flash-lite"),
    SEARCH: ("gemini-2.5-flash", "gemini-flash-latest", "gemini-2.5-flash-lite"),
}

# Minutes a rung stays demoted after answering 429/RESOURCE_EXHAUSTED.
COOLDOWN_SECONDS = 300

# How many one-shot Live sessions may exist at once, and how long a call
# waits for one before falling to REST.
#
# THE USER'S CONVERSATION OUTRANKS EVERY SIDE CALL.  Nothing a side call does
# can reach the microphone (main.py owns the only receive loop), but it CAN
# hold a concurrent-session slot, and that is the one way it could hurt the
# conversation — so the count is capped, and a call that cannot get a slot
# quickly does not queue: it falls to the REST rung, which is what the ladder
# is for. Three, from the measurement (gemini.py docstring): four side calls
# plus the conversation ran together without complaint, so three leaves the
# conversation a slot in hand.
LIVE_SLOTS = 3
LIVE_SLOT_WAIT = 3.0

# Where a live session leaves its RungStats for offline consumers
# (tools/eval_scorecard.py reads this for the M7 provider mix). Throttled:
# record() never writes more than once per interval, atomically, so the hot
# path pays a monotonic check and the file only moves when there is news.
DUMP_MIN_INTERVAL_S = 30.0
DEFAULT_DUMP_PATH = (Path(__file__).resolve().parent.parent
                     / "memory" / "model_stats.json")


class RungStats:
    """Counters for one rung (one model string). Plain values only —
    `snapshot()` hands these out as dicts so callers cannot mutate history."""

    __slots__ = ("attempts", "ok", "fail", "latency_total",
                 "last_latency", "last_error", "cooldown_until")

    def __init__(self):
        self.attempts = 0
        self.ok = 0
        self.fail = 0
        self.latency_total = 0.0
        self.last_latency = 0.0
        self.last_error = ""
        self.cooldown_until = 0.0

    @property
    def avg_latency(self) -> float:
        return self.latency_total / self.ok if self.ok else 0.0


class ModelManager:
    """Ladder lookup, rung stats, and cooldown policy for the REST/Live
    one-shot path. One instance (`MODEL_MANAGER`) is shared process-wide;
    every method is thread-safe because gemini.call runs on plugin, UI and
    event-loop threads alike."""

    def __init__(self, ladders: dict[str, tuple[str, ...]] | None = None,
                 dump_path: Path | str | None = None):
        self._ladders = {t: tuple(r) for t, r in
                         (ladders if ladders is not None else LADDERS).items()}
        self._stats: dict[str, RungStats] = {}
        self._lock = threading.Lock()
        self._dump_path = Path(dump_path) if dump_path is not None \
            else DEFAULT_DUMP_PATH
        self._last_dump = 0.0

    # ── ladder ──────────────────────────────────────────────────────────

    def ladder(self, tier: str) -> tuple[str, ...]:
        """Rungs for `tier` in measured order. An unknown tier is taken to
        be an explicit model name (screen_agent lets the user pick one in
        its settings) — it leads, with the SMART ladder behind it, so a
        user's choice that is having an outage still degrades to something
        that answers instead of to nothing."""
        ladder = self._ladders.get(tier)
        if ladder is None:
            ladder = (tier,) + tuple(m for m in self._ladders[SMART]
                                     if m != tier)
        return ladder

    def order(self, tier: str) -> tuple[str, ...]:
        """`ladder(tier)` with cooling rungs demoted to the back — and if
        EVERY rung is cooling, the plain ladder: trying everything beats
        returning nothing."""
        ladder = self.ladder(tier)
        with self._lock:
            ready = tuple(m for m in ladder if not self._is_cooling(m))
        if not ready or len(ready) == len(ladder):
            return ladder
        cooled = tuple(m for m in ladder if m not in ready)
        return ready + cooled

    # ── cooldown ────────────────────────────────────────────────────────

    def _is_cooling(self, model: str, now: float | None = None) -> bool:
        """Caller must hold the lock."""
        st = self._stats.get(model)
        if st is None or not st.cooldown_until:
            return False
        if (now if now is not None else time.monotonic()) < st.cooldown_until:
            return True
        st.cooldown_until = 0.0
        return False

    def is_cooling(self, model: str) -> bool:
        with self._lock:
            return self._is_cooling(model)

    def cool(self, model: str, seconds: float | None = None) -> None:
        """Send a rung to cooldown for `seconds` (default the policy
        constant). Exposed for recovery paths that know a model is
        unhealthy without seeing the 429 itself."""
        until = time.monotonic() + (COOLDOWN_SECONDS if seconds is None
                                    else seconds)
        with self._lock:
            st = self._stats.setdefault(model, RungStats())
            st.cooldown_until = max(st.cooldown_until, until)

    # ── stats ───────────────────────────────────────────────────────────

    @staticmethod
    def _is_quota_error(msg: str) -> bool:
        # The API surfaces this three ways: a bare "429", the error name
        # RESOURCE_EXHAUSTED (genai errors), or the mixed-case exception
        # name ResourceExhausted (api_core). All three mean the same thing.
        u = msg.upper()
        return ("429" in msg or "RESOURCE_EXHAUSTED" in u
                or "RESOURCEEXHAUSTED" in u)

    @staticmethod
    def _is_provider_error(msg: str) -> bool:
        """The endpoint itself is unhealthy (Phase 7): connection refused,
        DNS failure, TLS failure, timeout, or a 5xx — not the model's
        fault, but the rung must still step down or the ladder keeps
        hammering a dead provider."""
        u = msg.upper()
        return any(m in u for m in (
            "CONNECTION", "CONNECT", "TIMED OUT", "TIMEOUT",
            "DNS", "SSL", "GAIERROR", "UNREACHABLE",
            "GETADDRINFO", "RESOLV", "GAIERROR",
            "502", "503", "504", "SERVICE UNAVAILABLE",
            "GATEWAY", "BAD GATEWAY",
        ))

    def record(self, model: str, ok: bool, latency: float,
               error: str = "") -> str:
        """Record one attempt. Returns the cooldown reason applied:
        ``"quota"`` (429/RESOURCE_EXHAUSTED), ``"provider"`` (endpoint
        unhealthy), or ``""`` — an empty reason is falsy, so legacy
        ``if record(...)`` call sites keep working. The caller prints its
        own message, this only applies the policy.
        Latency averages cover SUCCESSFUL attempts only: a rung that spent
        10s timing out before failing did not answer, and `avg_latency` is
        the question "how long until I have an answer"."""
        reason = ""
        if not ok:
            if self._is_quota_error(error):
                reason = "quota"
            elif self._is_provider_error(error):
                reason = "provider"
        with self._lock:
            st = self._stats.setdefault(model, RungStats())
            st.attempts += 1
            st.last_latency = latency
            if ok:
                st.ok += 1
                st.latency_total += latency
            else:
                st.fail += 1
                st.last_error = error[:200]
                if reason:
                    st.cooldown_until = max(
                        st.cooldown_until,
                        time.monotonic() + COOLDOWN_SECONDS)
        self._maybe_dump()
        return reason

    # ── live dump (Phase 8 / M7) ────────────────────────────────────────

    def dump_stats(self) -> bool:
        """Write ``snapshot()`` to the dump path with provenance metadata.

        Atomic (temp + os.replace) so a consumer never reads a half file.
        Fail-soft: a read-only disk must never break a model call, so any
        error returns False instead of raising. The ``_meta`` key carries
        when this snapshot was taken — the scorecard shows it so a stale
        dump can never masquerade as live data.
        """
        try:
            payload = {
                "_meta": {
                    "updated_at": datetime.now(timezone.utc)
                                  .isoformat(timespec="seconds"),
                    "source": "ModelManager.record",
                    "pid": os.getpid(),
                },
            }
            payload.update(self.snapshot())
            tmp = self._dump_path.parent / (self._dump_path.name + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                           encoding="utf-8")
            os.replace(tmp, self._dump_path)
            return True
        except Exception:
            logger.debug("model_stats dump failed", exc_info=True)
            return False

    def _maybe_dump(self) -> None:
        """Throttled dump hook — one monotonic check on the hot path."""
        now = time.monotonic()
        with self._lock:
            if now - self._last_dump < DUMP_MIN_INTERVAL_S:
                return
            self._last_dump = now
        self.dump_stats()

    def snapshot(self) -> dict[str, dict[str, float | int | str]]:
        """Copy of every rung's counters, safe to print or log."""
        out: dict[str, dict[str, float | int | str]] = {}
        with self._lock:
            for model, st in sorted(self._stats.items()):
                out[model] = {
                    "attempts": st.attempts,
                    "ok": st.ok,
                    "fail": st.fail,
                    "avg_latency": round(st.avg_latency, 3),
                    "last_latency": round(st.last_latency, 3),
                    "cooling": self._is_cooling(model),
                    "last_error": st.last_error,
                }
        return out

    def format_stats(self) -> str:
        """The stats as an aligned table — for a log line or a debug view."""
        snap = self.snapshot()
        if not snap:
            return "[model-stats] no attempts recorded yet"
        lines = ["[model-stats] rung                 ok/att   avg(s)  last(s)  state"]
        for model, s in snap.items():
            state = "COOLING" if s["cooling"] else ("ok" if s["fail"] == 0 else "mixed")
            lines.append(f"[model-stats] {model[:20]:<20} {s['ok']:>3}/{s['attempts']:<3} "
                         f"{s['avg_latency']:>7.3f} {s['last_latency']:>8.3f}  {state}")
        return "\n".join(lines)

    def reset_for_tests(self) -> None:
        with self._lock:
            self._stats.clear()


class LiveModelManager:
    """Policy for the Live rung: which model string, and how many throwaway
    sessions may exist at once. The transport stays in core/gemini.py."""

    def __init__(self, model: str = LIVE_MODEL,
                 slots: int = LIVE_SLOTS,
                 slot_wait: float = LIVE_SLOT_WAIT):
        self._model = model
        self._slots = threading.BoundedSemaphore(slots)
        self._slot_wait = slot_wait

    @property
    def model(self) -> str:
        return self._model

    @contextmanager
    def slot(self, wait: float | None = None):
        """Hold one session slot for the duration of a side call.

        Raises RuntimeError immediately (same message as before) when every
        slot is busy for longer than the wait — the caller falls to the REST
        rung, which is what the ladder is for. Always releases, even when
        the body raises, so a failed call can never leak its slot.
        """
        got = self._slots.acquire(timeout=self._slot_wait if wait is None
                                  else wait)
        if not got:
            raise RuntimeError("no free Live slot — leaving them for the conversation")
        try:
            yield
        finally:
            self._slots.release()


# Process-wide instances. gemini.call records every attempt through the
# first; _live_call borrows sessions through the second.
MODEL_MANAGER = ModelManager()
LIVE_MANAGER = LiveModelManager()
