"""
Pollinations.ai provider — default, free, no API key required.

GET https://image.pollinations.ai/prompt/{url-encoded prompt}
    ?width=&height=&nologo=true&seed=&model=

Returns raw image bytes.  Used as the zero-config default so Visual
Generation works out of the box without any secret management.
"""
from __future__ import annotations

import time
import urllib.parse
from typing import Any

import requests

from ..provider import (
    AspectRatio,
    Quality,
    VisualErrorCode,
    VisualGenerationProvider,
    VisualGenerationResult,
    VisualStatus,
)

_BASE = "https://image.pollinations.ai/prompt/"

# Logical aspect ratio → pixel dimensions (kept modest for latency/cost).
_AR_PIXELS: dict[AspectRatio, tuple[int, int]] = {
    AspectRatio.SQUARE:    (1024, 1024),
    AspectRatio.LANDSCAPE: (1280, 720),
    AspectRatio.PORTRAIT:  (720, 1280),
    AspectRatio.WIDE:      (1216, 832),
    AspectRatio.TALL:      (832, 1216),
}

_MIME_BY_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff",       "image/jpeg"),
    (b"GIF87a",             "image/gif"),
    (b"GIF89a",             "image/gif"),
    (b"RIFF",               "image/webp"),  # RIFF....WEBP
    (b"BM",                 "image/bmp"),
)


def _detect_mime(data: bytes) -> str:
    for magic, mime in _MIME_BY_MAGIC:
        if data.startswith(magic):
            return mime
    return ""


class PollinationsProvider(VisualGenerationProvider):
    """Free, keyless image generation via image.pollinations.ai."""

    name = "pollinations"
    supports_edit = False
    supports_variation = False
    supported_aspect_ratios = frozenset(_AR_PIXELS)
    supported_qualities = frozenset({Quality.STANDARD, Quality.HD})

    def is_configured(self) -> bool:
        return True  # no credentials needed

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

        bad = self.validate_prompt(prompt)
        if bad:
            return VisualGenerationResult.failure(
                bad, "Prompt is empty or too long.",
                provider=self.name, prompt_summary=summary,
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        if aspect_ratio not in _AR_PIXELS:
            return VisualGenerationResult.failure(
                VisualErrorCode.INVALID_REQUEST,
                f"Unsupported aspect ratio: {aspect_ratio}",
                provider=self.name, prompt_summary=summary,
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        width, height = _AR_PIXELS[aspect_ratio]
        if quality == Quality.HD:
            width = min(width * 2, 2048)
            height = min(height * 2, 2048)

        full_prompt = f"{style}, {prompt}" if style and style.strip() else prompt
        url = (
            _BASE + urllib.parse.quote(full_prompt, safe="")
            + f"?width={width}&height={height}&nologo=true"
        )
        if output_format and output_format.lower() != "png":
            url += f"&format={output_format.lower()}"

        try:
            resp = requests.get(url, timeout=timeout_s)
        except requests.exceptions.Timeout:
            return VisualGenerationResult.failure(
                VisualErrorCode.TIMEOUT,
                f"Provider timed out after {timeout_s:.0f}s.",
                provider=self.name, prompt_summary=summary,
                duration_ms=(time.monotonic() - t0) * 1000,
            )
        except requests.exceptions.RequestException as e:
            return VisualGenerationResult.failure(
                VisualErrorCode.NETWORK_ERROR,
                f"Network error contacting provider: {e}",
                provider=self.name, prompt_summary=summary,
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        if resp.status_code == 429:
            return VisualGenerationResult.failure(
                VisualErrorCode.QUOTA_EXCEEDED,
                "Provider rate limit reached (429).",
                provider=self.name, prompt_summary=summary,
                duration_ms=(time.monotonic() - t0) * 1000,
            )
        if resp.status_code in (401, 403):
            return VisualGenerationResult.failure(
                VisualErrorCode.PROVIDER_NOT_CONFIGURED,
                f"Provider rejected the request ({resp.status_code}).",
                provider=self.name, prompt_summary=summary,
                duration_ms=(time.monotonic() - t0) * 1000,
            )
        if resp.status_code in (400, 422):
            return VisualGenerationResult.failure(
                VisualErrorCode.CONTENT_RESTRICTION
                       if resp.status_code == 422
                       else VisualErrorCode.INVALID_REQUEST,
                f"Provider refused the prompt ({resp.status_code}).",
                provider=self.name, prompt_summary=summary,
                duration_ms=(time.monotonic() - t0) * 1000,
            )
        if resp.status_code >= 500:
            return VisualGenerationResult.failure(
                VisualErrorCode.PROVIDER_UNAVAILABLE,
                f"Provider server error ({resp.status_code}).",
                provider=self.name, prompt_summary=summary,
                duration_ms=(time.monotonic() - t0) * 1000,
            )
        if not resp.ok:
            return VisualGenerationResult.failure(
                VisualErrorCode.GENERATION_FAILED,
                f"Provider returned HTTP {resp.status_code}.",
                provider=self.name, prompt_summary=summary,
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        data = resp.content or b""
        if not data:
            return VisualGenerationResult.failure(
                VisualErrorCode.OUTPUT_UNAVAILABLE,
                "Provider returned an empty response.",
                provider=self.name, prompt_summary=summary,
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        mime = _detect_mime(data)
        if not mime:
            return VisualGenerationResult.failure(
                VisualErrorCode.OUTPUT_INVALID,
                "Response is not a recognisable image.",
                provider=self.name, prompt_summary=summary,
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        # Best-effort dimension read without requiring Pillow at import time.
        w = h = 0
        try:
            import io
            from PIL import Image
            with Image.open(io.BytesIO(data)) as im:
                w, h = im.size
        except Exception:
            w, h = width, height  # requested dims; verifier will confirm later

        return VisualGenerationResult(
            success=True,
            status=VisualStatus.GENERATED,
            image_reference=data.hex()[:32] + f"…({len(data)}B)",
            mime_type=mime,
            width=w,
            height=h,
            provider=self.name,
            prompt_summary=summary,
            verification="RECEIVED",
            duration_ms=(time.monotonic() - t0) * 1000,
            raw_meta={"bytes_len": len(data), "requested_w": width,
                      "requested_h": height, "image_bytes": data},
        )
