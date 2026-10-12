"""Phase 7 — provider-agnostic model call contract (BRAIN V0.1).

`ModelCall` in, `ModelResult` out, regardless of which backend answered.
The ladder (`core.model_manager.ModelManager`) consumes the metadata: a
provider outage (connection refused, 5xx, timeout) cools the rung the same
way a quota error does, so "provider down" steps the ladder down instead of
retrying the same dead endpoint forever.

Never logs or returns API keys: error text is scrubbed against the keys the
config actually holds before it leaves this module.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import Event
from typing import Optional


@dataclass(frozen=True)
class ModelCall:
    """What the caller wants — identical for every backend."""
    prompt: str
    tier: str = "smart"
    system: str | None = None
    timeout: float = 120.0
    cancel: Optional[Event] = None   # set() = do not start / abort

    @property
    def cancelled(self) -> bool:
        return bool(self.cancel is not None and self.cancel.is_set())


@dataclass
class ModelResult:
    """What came back — same fields, same types, every provider."""
    ok: bool
    text: str
    provider: str
    model: str
    latency: float
    error: str = ""
    tool_calls: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok, "text": self.text, "provider": self.provider,
            "model": self.model, "latency": round(self.latency, 3),
            "error": self.error, "tool_calls": self.tool_calls,
        }


def _configured_secrets() -> list[str]:
    try:
        from core.llm_client import _load_config
        cfg = _load_config()
        return [str(v) for k, v in cfg.items()
                if "key" in str(k).lower() and isinstance(v, str)
                and len(v) >= 8]
    except Exception:
        return []


def _scrub(text: str) -> str:
    for secret in _configured_secrets():
        if secret and secret in text:
            text = text.replace(secret, "***")
    return text


def run_model(call: ModelCall) -> ModelResult:
    """Execute one `ModelCall` over the configured llm_client backend.

    Every outcome — success, provider error, cancellation — returns a fully
    populated `ModelResult`; this function raises nothing.
    """
    from core import llm_client

    provider = llm_client.get_llm_provider()
    try:
        model = llm_client.get_llm_settings()[1]
    except Exception:
        model = ""

    if call.cancelled:
        return ModelResult(ok=False, text="", provider=provider, model=model,
                           latency=0.0, error="cancelled before dispatch")

    messages = []
    if call.system:
        messages.append({"role": "system", "content": call.system})
    messages.append({"role": "user", "content": call.prompt})

    t0 = time.monotonic()
    try:
        raw = llm_client.call_llm(messages, timeout=int(call.timeout) or 120)
        latency = time.monotonic() - t0
        if call.cancelled:
            return ModelResult(ok=False, text="", provider=provider,
                               model=model, latency=latency,
                               error="cancelled after dispatch")
        return ModelResult(
            ok=True,
            text=str(raw.get("content", "")),
            provider=provider,
            model=model,
            latency=latency,
            tool_calls=list(raw.get("tool_calls") or []),
        )
    except Exception as e:
        return ModelResult(
            ok=False, text="", provider=provider, model=model,
            latency=time.monotonic() - t0,
            error=_scrub(f"{type(e).__name__}: {e}"),
        )
