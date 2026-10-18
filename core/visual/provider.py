"""
Visual Generation Provider Abstraction — Phase 1.

XYRIS must NEVER be tightly coupled to a single image provider.
This module defines the provider interface, the normalized result model,
and the provider registry.  Provider-specific implementation details are
hidden behind VisualGenerationProvider so the agent architecture never
needs to change when the provider changes.

Architecture:
    XYRIS AGENT
          ↓
    VISUAL GENERATION TOOL  (actions/generate_image.py)
          ↓
    VisualGenerationProvider  (this file — the interface)
          ↓
    ACTIVE PROVIDER  (pollinations / gemini_imagen / …)
          ↓
    VisualGenerationResult  (normalized, provider-agnostic)
"""
from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ── Error taxonomy (Phase 14) ───────────────────────────────────────────────

class VisualErrorCode(str, Enum):
    """Truthful failure codes.  Never invent success."""

    PROVIDER_NOT_CONFIGURED = "PROVIDER_NOT_CONFIGURED"
    PROVIDER_UNAVAILABLE    = "PROVIDER_UNAVAILABLE"
    NETWORK_ERROR           = "NETWORK_ERROR"
    TIMEOUT                 = "TIMEOUT"
    INVALID_PROMPT          = "INVALID_PROMPT"
    INVALID_REQUEST         = "INVALID_REQUEST"
    CONTENT_RESTRICTION     = "CONTENT_RESTRICTION"
    QUOTA_EXCEEDED          = "QUOTA_EXCEEDED"
    GENERATION_FAILED       = "GENERATION_FAILED"
    OUTPUT_INVALID          = "OUTPUT_INVALID"
    OUTPUT_UNAVAILABLE      = "OUTPUT_UNAVAILABLE"
    DISPLAY_FAILED          = "DISPLAY_FAILED"
    SAVE_FAILED             = "SAVE_FAILED"
    VERIFICATION_FAILED     = "VERIFICATION_FAILED"


class VisualStatus(str, Enum):
    """Lifecycle status of a visual generation request."""

    GENERATED         = "GENERATED"
    GENERATION_FAILED = "GENERATION_FAILED"
    VERIFIED          = "VERIFIED"
    FAILED            = "FAILED"


# ── Supported parameter value sets (exposed only when provider supports) ────

class AspectRatio(str, Enum):
    SQUARE   = "1:1"
    LANDSCAPE = "16:9"
    PORTRAIT  = "9:16"
    WIDE      = "3:2"
    TALL      = "2:3"


class Quality(str, Enum):
    STANDARD = "standard"
    HD       = "hd"


# ── Normalized result model (Phase 6) ───────────────────────────────────────

@dataclass
class VisualGenerationResult:
    """
    Provider-agnostic result.  The agent only ever sees this — never
    raw provider responses.

    success=True  → image_reference and/or file_path are usable.
    success=False → error_code and error_message explain why.
    Never claim success without evidence.
    """

    success: bool
    status: VisualStatus
    image_reference: str = ""          # bytes-in-memory handle or remote ref
    file_path: str = ""                # populated after a save
    mime_type: str = ""
    width: int = 0
    height: int = 0
    provider: str = ""
    generation_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    prompt_summary: str = ""           # truncated, safe for logs
    verification: str = "UNVERIFIED"   # RECEIVED | VERIFIED | FAILED | UNVERIFIED
    error_code: Optional[VisualErrorCode] = None
    error_message: str = ""
    duration_ms: float = 0.0
    raw_meta: dict = field(default_factory=dict)  # non-essential provider hints

    @property
    def is_verified(self) -> bool:
        return self.verification == "VERIFIED"

    @classmethod
    def failure(
        cls,
        code: VisualErrorCode,
        message: str,
        provider: str = "",
        prompt_summary: str = "",
        duration_ms: float = 0.0,
    ) -> "VisualGenerationResult":
        """Construct a truthful failure result — never fabricate references."""
        return cls(
            success=False,
            status=VisualStatus.GENERATION_FAILED
                   if code not in (VisualErrorCode.VERIFICATION_FAILED,
                                   VisualErrorCode.SAVE_FAILED,
                                   VisualErrorCode.DISPLAY_FAILED)
                   else VisualStatus.FAILED,
            provider=provider,
            prompt_summary=prompt_summary,
            verification="FAILED",
            error_code=code,
            error_message=message,
            duration_ms=duration_ms,
        )


# ── Provider interface (Phase 1) ────────────────────────────────────────────

class VisualGenerationProvider(ABC):
    """
    Abstract base for every image provider.

    Subclasses implement generate_image() and optionally edit_image() /
    generate_variation().  Do NOT invent capabilities a provider does not
    actually support — leave unimplemented methods raising NotImplementedError
    and set supports_edit / supports_variation accordingly.
    """

    #: Stable identifier used in config and logs (e.g. "pollinations").
    name: str = "base"

    #: Capability flags — only set True when genuinely implemented.
    supports_edit: bool = False
    supports_variation: bool = False

    #: Declared parameter support (empty set = provider accepts any value).
    supported_aspect_ratios: frozenset[AspectRatio] = frozenset()
    supported_qualities: frozenset[Quality] = frozenset()

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True when credentials/endpoint are present and usable."""

    @abstractmethod
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
        """
        Generate an image from a text prompt.

        MUST return a VisualGenerationResult — never raise for provider
        errors (map them to error codes).  Only raise for programmer
        errors (bad arguments).  On any failure set success=False with
        an accurate VisualErrorCode.
        """

    def edit_image(self, *args: Any, **kwargs: Any) -> VisualGenerationResult:
        """Image editing — only implemented by providers that support it."""
        raise NotImplementedError(f"{self.name} does not support image editing")

    def generate_variation(self, *args: Any, **kwargs: Any) -> VisualGenerationResult:
        """Variation generation — only implemented by providers that support it."""
        raise NotImplementedError(f"{self.name} does not support variations")

    def validate_prompt(self, prompt: str) -> Optional[VisualErrorCode]:
        """Shared sanity check.  Returns None when OK, else an error code."""
        if not prompt or not prompt.strip():
            return VisualErrorCode.INVALID_PROMPT
        if len(prompt) > 4000:
            return VisualErrorCode.INVALID_PROMPT
        return None


# ── Provider registry ───────────────────────────────────────────────────────

_REGISTRY: dict[str, VisualGenerationProvider] = {}


def register_provider(provider: VisualGenerationProvider) -> None:
    """Register a provider instance under its .name."""
    _REGISTRY[provider.name] = provider


def get_provider(name: str) -> Optional[VisualGenerationProvider]:
    """Look up a registered provider by name (None when absent)."""
    return _REGISTRY.get(name)


def available_providers() -> list[str]:
    """Names of all registered providers."""
    return list(_REGISTRY.keys())


def _load_default_providers() -> None:
    """Import and register built-in providers.  Failures are non-fatal —
    a broken optional provider must never take down the app."""
    try:
        from .providers.pollinations import PollinationsProvider
        register_provider(PollinationsProvider())
    except Exception:
        pass
    try:
        from .providers.gemini_imagen import GeminiImagenProvider
        register_provider(GeminiImagenProvider())
    except Exception:
        pass


_load_default_providers()
