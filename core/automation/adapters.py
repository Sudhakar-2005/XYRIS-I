"""Phase 7 — adapters between main.py's live systems and the automation
runtime (spec §13).

Each builder takes plain callables so main.py stays the only place that
knows about the dispatcher gate, the confirm banner, gemini and memory.
Everything here is unit-testable with fakes; no module imports main or ui.

Honesty rules encoded in this file:
  * forbidden / ungranted permission / no-user-turn shutdown / busy or
    missing gate are refused BEFORE dispatch — the rules come from
    core/gates.evaluate_gates, the SAME function JarvisLive._execute_tool
    uses, so the two paths cannot drift
  * requires_confirmation tools wait for the real human gate, synchronously
    inside the run — a declined/expired banner ends the step as refused,
    never as success
  * a failure-shaped dispatcher string is reported as ok=False, and a
    structured verifier verdict with passed=False is reported as ok=False
    even when the text looks fine
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from types import SimpleNamespace
from typing import Any, Callable

from core.gates import DENY, evaluate_gates, message_for

# Text markers that mean the dispatcher did NOT do the thing — the single
# failure matcher in the repo (the dead duplicate in
# NodeResult.from_tool_output was deleted in brain Phase 1).
FAILURE_MARKERS = (
    " failed:",
    " is not available.",
    " is currently disabled.",
    "timed out after",
    "Unknown tool:",
    "Tool returned nothing",
)


def looks_like_failure(text: str) -> bool:
    t = str(text or "")
    if not t.strip():
        return True
    return any(m in t for m in FAILURE_MARKERS)


# ------------------------------------------------------------------- ledger

class LiveLedger:
    """PermissionLedger proxy that re-reads the live object each call, so a
    permissions file edited while the app runs is honored immediately."""

    def __init__(self, getter: Callable[[], Any]) -> None:
        self._getter = getter

    def is_granted(self, permission: str) -> bool:
        ledger = self._getter()
        if ledger is None:
            return True
        return bool(ledger.is_granted(permission))


# ------------------------------------------------------------------ confirm

def build_confirm(request: Callable[[str, str, str, Callable[[], str]], str],
                  pending: Callable[[], str], *,
                  wait_s: float = 95.0,
                  poll_s: float = 0.3,
                  key_prefix: str = "automation"
                  ) -> Callable[..., Any]:
    """confirm(title, detail) -> awaitable bool.

    Wraps core/confirm.request with a future so a workflow step can wait for
    the human. core/confirm never calls `run` on decline/expiry, so the
    wrapper watches pending_title(): when the banner disappears without the
    future resolving (within a short grace for the approve worker), the
    answer is False.
    """
    async def confirm(title: str, detail: str = "") -> bool:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()

        def _approved() -> None:
            def _set() -> None:
                if not fut.done():
                    fut.set_result(True)
            try:
                loop.call_soon_threadsafe(_set)
            except RuntimeError:
                pass

        request(key=f"{key_prefix}:{uuid.uuid4().hex[:8]}",
                title=str(title)[:200], detail=str(detail)[:600],
                run=_approved)
        if not pending():
            # gate unavailable / could not show — refused, never assumed
            return False
        deadline = time.monotonic() + wait_s
        while not fut.done() and time.monotonic() < deadline:
            if not pending():
                # either declined/expired, or approved and the worker thread
                # is about to call us — grant a short grace
                for _ in range(20):
                    if fut.done():
                        break
                    await asyncio.sleep(0.05)
                if fut.done():
                    break
                return False
            await asyncio.sleep(poll_s)
        return bool(fut.done() and fut.result())

    return confirm


# ---------------------------------------------------------------- tool call

def build_tool_call(*,
                    dispatch: Callable,
                    policy_of: Callable[[str], Any],
                    confirm: Callable[[str, str], Any] | None = None,
                    ledger: Any = None,
                    pending: Callable[[], str] | None = None,
                    heard_user_text: Callable[[], bool] | None = None
                    ) -> Callable[..., Any]:
    """tool_call(tool, args, timeout_s) -> awaitable dict.

    Applies the SAME gate as JarvisLive._execute_tool — both call
    core/gates.evaluate_gates (forbidden → permissions → user-turn →
    gate availability → busy → consent) — and then dispatches through the
    existing dispatcher, which enforces its own policy timeout.
    """

    async def tool_call(tool: str, args: dict, timeout_s: int) -> dict:
        fc = SimpleNamespace(id=f"auto-{uuid.uuid4().hex[:8]}",
                             name=str(tool), args=dict(args or {}))
        policy = policy_of(tool) or SimpleNamespace(
            risk="safe", permissions=(), requires_confirmation=False,
            timeout=timeout_s)

        heard = bool(heard_user_text()) if callable(heard_user_text) else False
        gate = evaluate_gates(
            tool, policy, ledger=ledger, heard_user_text=heard,
            busy=bool(pending()) if callable(pending) else False,
            confirm_available=confirm is not None)
        if gate.decision == DENY:
            return {"ok": False, "refused": True, "status": "refused",
                    "text": message_for(gate.rule, tool, gate.denied)}

        if getattr(policy, "requires_confirmation", False):
            # decision is CONFIRM: the gate already ruled the banner free,
            # the shutdown user-turn rule passed, and confirm exists.
            title = tool.replace("_", " ").capitalize()
            detail = (f"Automation wants to run '{tool}' with "
                      f"{json.dumps(args, ensure_ascii=False, default=str)[:400]}")
            try:
                ok = await confirm(title, detail)
            except Exception as exc:
                return {"ok": False, "refused": True, "status": "refused",
                        "text": f"confirmation failed: {exc}. Nothing was done."}
            if not ok:
                return {"ok": False, "refused": True, "status": "refused",
                        "text": (f"'{tool}' was not confirmed by the user "
                                 f"(declined or timed out). Nothing was done.")}

        try:
            resp = await dispatch(fc, policy)
        except Exception as exc:                                 # noqa: BLE001
            return {"ok": False, "status": "error", "text": str(exc),
                    "error": str(exc)}
        r = getattr(resp, "response", None)
        text = str((r or {}).get("result") or "") if isinstance(r, dict) else str(r or "")
        # Structured verifier verdict first: passed=False ends the node as
        # an error even when the prose never matches a failure marker.
        ver = r.get("verification") if isinstance(r, dict) else None
        ver_status = ""
        if isinstance(ver, dict):
            ver_status = str(ver.get("status") or "")
            if ver.get("passed") is False:
                return {"ok": False, "status": "error", "text": text,
                        "verification": ver_status or "failed"}
        elif isinstance(ver, str):
            ver_status = ver or ""
        if looks_like_failure(text):
            out = {"ok": False, "status": "error", "text": text}
            if ver_status:
                out["verification"] = ver_status
            return out
        out = {"ok": True, "status": "ok", "text": text}
        if ver_status:
            out["verification"] = ver_status
        return out

    return tool_call


# ------------------------------------------------------------------- ai call

def build_ai_call(text_fn: Callable | None = None,
                  tier: str = "SMART") -> Callable[..., Any]:
    """ai_call(operation, objective, input_text, max_chars, timeout_s)."""

    def _resolve():
        fn = text_fn
        if fn is None:
            from core import gemini
            fn = gemini.text
            return fn, getattr(gemini, "SMART", tier)
        return fn, tier

    async def ai_call(*, operation: str, objective: str, input_text: str,
                      max_chars: int = 1200, timeout_s: int = 60) -> str:
        fn, use_tier = _resolve()
        prompt = (f"Operation: {operation}\n"
                  f"Objective: {objective}\n\n"
                  f"Input:\n{input_text}\n\n"
                  f"Answer directly and concisely, at most "
                  f"{max_chars} characters.")
        out = await asyncio.to_thread(
            fn, prompt, use_tier,
            timeout_ms=max(10_000, int(timeout_s) * 1000), default="")
        return str(out or "")[:int(max_chars)]

    return ai_call


# ------------------------------------------------------------ memory / notify

def build_memory_save(fn: Callable[[str, str, str], str],
                      category: str = "automation") -> Callable[..., Any]:
    async def memory_save(key: str, value: str) -> str:
        return await asyncio.to_thread(fn, str(key)[:80], str(value)[:380],
                                       category)
    return memory_save


def build_memory_recall(fn: Callable[..., str]) -> Callable[..., Any]:
    async def memory_recall(query: str) -> str:
        return await asyncio.to_thread(fn, str(query), 8)
    return memory_recall


def build_notify(fn: Callable[[str], Any]) -> Callable[..., Any]:
    async def notify(message: str) -> str:
        await asyncio.to_thread(fn, str(message))
        return "notified"
    return notify
