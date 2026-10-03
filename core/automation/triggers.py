"""Trigger engine (Phase 8 / spec §5).

V0.1 triggers: manual · schedule (daily HH:MM or every_minutes) ·
file_watch (created/changed in a directory). Designed so new trigger types
(email, calendar, webhook, system events) slot in as new entries in
_due() without touching the runtime — unsupported types never appear as
fake UI options: they simply are not in TriggerType.

Pure + synchronous: tick(now) computes what is due; engine.py starts runs.
Trigger state persists per workflow in the store (statistics.trigger_state).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from .schema import TriggerType, Workflow

DEFAULT_INTERVAL_S = 5.0
MAX_PATTERN_LEN = 128


def _local_hhmm(ts: float | None = None) -> str:
    lt = time.localtime(ts if ts is not None else time.time())
    return f"{lt.tm_hour:02d}:{lt.tm_min:02d}"


def _local_day(ts: float | None = None) -> str:
    lt = time.localtime(ts if ts is not None else time.time())
    return f"{lt.tm_year:04d}-{lt.tm_mon:02d}-{lt.tm_mday:02d}"


class TriggerEngine:
    def __init__(self, store: Any,
                 now: Callable[[], float] | None = None,
                 file_glob: Callable | None = None) -> None:
        self._store = store
        self._now = now or time.time
        self._glob = file_glob or self._default_glob

    # ---------------------------------------------------------------- api

    def due(self, workflows: list[Workflow]) -> list[tuple[Workflow, str]]:
        out: list[tuple[Workflow, str]] = []
        for wf in workflows:
            try:
                reason = self._due_reason(wf)
            except Exception:
                reason = None
            if reason:
                out.append((wf, reason))
        return out

    def mark_fired(self, wf: Workflow, reason: str) -> None:
        state = dict((wf.statistics or {}).get("trigger_state") or {})
        if wf.trigger.type == TriggerType.SCHEDULE.value:
            cfg = wf.trigger.config
            if "time" in cfg:
                state["fired_for"] = f"{_local_day()} {cfg['time']}"
            else:
                state["last_fired"] = self._now()
        elif wf.trigger.type == TriggerType.FILE_WATCH.value:
            state["snapshot"] = self._snapshot(wf)
        else:
            state["last_fired"] = self._now()
        wf.statistics = dict(wf.statistics or {})
        wf.statistics["trigger_state"] = state
        if self._store is not None:
            try:
                self._persist_state(wf, wf.statistics)
            except Exception:
                pass

    def baseline(self, wf: Workflow) -> None:
        """Record current trigger state without firing (first activation)."""
        if wf.trigger.type == TriggerType.FILE_WATCH.value:
            self.mark_fired(wf, "baseline")
        elif wf.trigger.type == TriggerType.SCHEDULE.value and \
                "every_minutes" in wf.trigger.config:
            state = dict((wf.statistics or {}).get("trigger_state") or {})
            state["last_fired"] = self._now()
            wf.statistics = dict(wf.statistics or {})
            wf.statistics["trigger_state"] = state
            if self._store is not None:
                try:
                    self._persist_state(wf, wf.statistics)
                except Exception:
                    pass

    # ------------------------------------------------------------ internals

    def _state(self, wf: Workflow) -> dict:
        return dict((wf.statistics or {}).get("trigger_state") or {})

    def _persist_state(self, wf: Workflow, statistics: dict) -> None:
        current = self._store.load(wf.id)
        if current is None:
            return
        current.statistics = dict(current.statistics or {})
        current.statistics["trigger_state"] = (
            statistics or {}).get("trigger_state", {})
        self._store.save(current, bump_version=False, force=True)

    def _due_reason(self, wf: Workflow) -> str | None:
        if wf.status != "ACTIVE":
            return None
        ttype = wf.trigger.type

        if ttype == TriggerType.MANUAL.value:
            return None

        if ttype == TriggerType.SCHEDULE.value:
            cfg = wf.trigger.config
            state = self._state(wf)
            now = self._now()
            if "time" in cfg:
                want = str(cfg["time"])
                due = _local_hhmm(now) >= want
                fired_key = f"{_local_day()} {want}"
                if due and state.get("fired_for") != fired_key:
                    return f"daily {want}"
                return None
            try:
                every = int(cfg.get("every_minutes", 0))
            except (TypeError, ValueError):
                return None
            if every < 1:
                return None
            last = float(state.get("last_fired", 0) or 0)
            if now - last >= every * 60:
                return f"every {every}m"
            return None

        if ttype == TriggerType.FILE_WATCH.value:
            cfg = wf.trigger.config
            state = self._state(wf)
            snap = self._snapshot(wf)
            prev = state.get("snapshot")
            if prev is None:
                self.mark_fired(wf, "baseline")
                return None
            if not isinstance(prev, dict):
                return None
            event = str(cfg.get("event", "created"))
            for key, sig in snap.items():
                old = prev.get(key)
                if old is None and event in ("created", "any"):
                    return f"file created: {Path(key).name}"
                if old is not None and tuple(old) != tuple(sig) \
                        and event in ("changed", "any"):
                    return f"file changed: {Path(key).name}"
            for key in prev:
                if key not in snap and event in ("any",):
                    return f"file removed: {Path(key).name}"
            return None

        return None

    def _snapshot(self, wf: Workflow) -> dict:
        cfg = wf.trigger.config
        path = str(cfg.get("path", ""))
        pattern = str(cfg.get("pattern", "*")) or "*"
        if len(pattern) > MAX_PATTERN_LEN:
            pattern = "*"
        return self._glob(path, pattern)

    @staticmethod
    def _default_glob(path: str, pattern: str) -> dict:
        out: dict[str, tuple] = {}
        try:
            root = Path(path).expanduser()
            if not root.exists():
                return out
            if root.is_file():
                candidates = [root]
            else:
                candidates = list(root.glob(pattern))[:500]
            for p in candidates:
                try:
                    if p.is_file():
                        st = p.stat()
                        out[str(p)] = (int(st.st_mtime), int(st.st_size))
                except OSError:
                    continue
        except (OSError, ValueError):
            return out
        return out
