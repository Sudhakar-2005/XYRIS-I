"""
Google Imagen provider — optional extension point.

Only reports is_configured() == True when an Imagen-capable credential is
present in config/api_keys.json.  Until wired to a real Imagen endpoint,
generate_image() returns an honest PROVIDER_NOT_CONFIGURED /
PROVIDER_UNAVAILABLE failure — it NEVER fabricates an image reference.

This file exists so the provider slot is real and swappable; a future
commit can implement the actual Imagen API call without touching the
agent architecture.
"""
from __future__ import annotations

import time
from typing import Any

from ..provider import (
    AspectRatio,
    Quality,
    VisualErrorCode,
    VisualGenerationProvider,
    VisualGenerationResult,
)


class GeminiImagenProvider(VisualGenerationProvider):
    """Placeholder for Google Imagen — intentionally not yet functional."""

    name = "gemini_imagen"
    supports_edit = False
    supports_variation = False
    supported_aspect_ratios = frozenset()   # not yet bound to a real API
    supported_qualities = frozenset()

    def _has_key(self) -> bool:
        try:
            from memory.config_manager import load_api_keys
            keys = load_api_keys()
            return bool(
                keys.get("imagen_api_key")
                or keys.get("gemini_api_key")  # may later route via Gemini
            )
        except Exception:
            return False

    def is_configured(self) -> bool:
        # A key existing does not mean the Imagen API is wired; we keep this
        # False until the real endpoint call is implemented, so the agent
        # falls back to a working provider instead of a guaranteed failure.
        return False

    def generate_image(
        self,
        prompt: str,
        *,
        aspect_ratio: AspectRatio = AspectRatio.SQUARE,
        quality: Quality = Quality.STANDARD,
        style: str = "",
        output_format: str = "png",
        timeout_s: float = 30.0,
        **kwargs: Any,
    ) -> VisualGenerationResult:
        t0 = time.monotonic()
        summary = (prompt or "").strip()[:80]
        return VisualGenerationResult.failure(
            VisualErrorCode.PROVIDER_NOT_CONFIGURED
            if not self._has_key()
            else VisualErrorCode.PROVIDER_UNAVAILABLE,
            "Imagen provider is not yet connected to a live endpoint.",
            provider=self.name,
            prompt_summary=summary,
            duration_ms=(time.monotonic() - t0) * 1000,
        )
