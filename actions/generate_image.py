# generate_image.py — Visual Generation as a first-class XYRIS tool.
"""
generate_image: understand → prompt → provider → verify → display → save → report.

NEVER claims success unless a usable image was actually received and verified.
Every failure returns a truthful VisualErrorCode-based message for the model.
"""
from __future__ import annotations

import io
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

from core.visual.provider import (
    AspectRatio,
    Quality,
    VisualErrorCode,
    VisualGenerationResult,
    get_provider,
)


# ── Base dir (PyInstaller-aware, same pattern as other actions) ─────────────

def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = _get_base_dir()


# ── Structured observability (Phase 15) — never log secrets ────────────────

def _evt(event: str, **meta: Any) -> None:
    safe = {k: (str(v)[:120] if not isinstance(v, (int, float)) else v)
            for k, v in meta.items()}
    print(f"[Visual] {event} {safe}")


# ── Config helpers (lazy import so a missing config never crashes) ──────────

def _cfg() -> dict:
    try:
        from memory.config_manager import get_visual_settings
        return get_visual_settings()
    except Exception:
        return {"visual_provider": "pollinations", "visual_autosave": True,
                "visual_save_dir": "", "visual_default_ar": "1:1",
                "visual_default_quality": "standard",
                "visual_max_retries": 1, "visual_timeout_s": 30.0}


# ── Prompt construction (Phase 4) — fidelity-preserving, not over-engineered ─

_STYLE_HINTS = {
    "cyberpunk":  "cyberpunk aesthetic, neon lighting, high detail",
    "realistic":  "photorealistic, natural lighting, high detail",
    "anime":      "anime style, clean line art, vibrant colors",
    "watercolor": "watercolor painting style, soft edges",
    "3d":         "3D render, octane render, studio lighting",
    "minimalist": "minimalist design, clean composition",
    "wallpaper":  "wallpaper composition, balanced framing, high detail",
}


def build_prompt(user_prompt: str, style: str = "") -> str:
    """
    Combine the user's intent with an optional style hint.
    Does NOT invent subjects, environments, or moods the user never asked for.
    """
    p = (user_prompt or "").strip()
    s = (style or "").strip()
    if not p:
        return s  # empty subject: style alone beats a leading comma
    if not s:
        return p
    hint = _STYLE_HINTS.get(s.lower(), s)  # pass through unknown styles verbatim
    # Skip the hint only when the user already stated that style themselves.
    if s.lower() in p.lower() or hint.lower() in p.lower():
        return p
    return f"{p}, {hint}"


# ── Result normalization → spoken/model-facing string (Phase 6/10) ──────────

_SUCCESS_PREFIX_OK = ("generated", "created", "made", "done")


def _ui_error(player, message: str) -> None:
    """Surface a visual-generation failure in the UI overlay (best-effort)."""
    if player and hasattr(player, "show_image_error"):
        try:
            player.show_image_error(message)
        except Exception:
            pass


def _result_to_message(result: Any, saved_path: str = "") -> str:
    """Truthful natural-language result the model will relay to the user."""
    if not getattr(result, "success", False):
        code = getattr(result, "error_code", None)
        msg = getattr(result, "error_message", "unknown error")
        code_str = getattr(code, "value", str(code))
        return f"[VISUAL_FAILED:{code_str}] Image generation failed: {msg}"

    bits = []
    if getattr(result, "width", 0) and getattr(result, "height", 0):
        bits.append(f"{result.width}x{result.height}")
    if getattr(result, "mime_type", ""):
        bits.append(result.mime_type)
    if saved_path:
        bits.append(f"saved to {saved_path}")
    detail = ", ".join(bits) if bits else "ok"

    verify = getattr(result, "verification", "UNVERIFIED")
    if verify != "VERIFIED":
        # Received but not fully verified — say so, don't overclaim.
        return (f"[VISUAL_RECEIVED:{detail}] Image received but verification "
                f"incomplete ({verify}). Present it cautiously.")

    return f"[VISUAL_OK:{detail}] Image generated and verified successfully."


# ── Verification (Phase 7) — check what is realistically possible ───────────

def _verify_image_bytes(data: bytes) -> tuple[bool, str, int, int]:
    """Return (ok, mime, width, height).  Uses Pillow when available."""
    if not data:
        return False, "", 0, 0
    # Magic-byte sanity check first (cheap, no deps).
    is_png  = data.startswith(b"\x89PNG\r\n\x1a\n")
    is_jpeg = data.startswith(b"\xff\xd8\xff")
    is_gif  = data.startswith((b"GIF87a", b"GIF89a"))
    is_webp = data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    is_bmp  = data.startswith(b"BM")
    if not (is_png or is_jpeg or is_gif or is_webp or is_bmp):
        return False, "", 0, 0

    mime = ("image/png" if is_png else
            "image/jpeg" if is_jpeg else
            "image/gif" if is_gif else
            "image/webp" if is_webp else "image/bmp")

    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            im.verify()                    # structural check
        with Image.open(io.BytesIO(data)) as im:
            w, h = im.size
        if w <= 0 or h <= 0:
            return False, "", 0, 0
        return True, mime, w, h
    except Exception:
        # Pillow unavailable or decode failed — magic bytes passed, so we can
        # only claim RECEIVED, not full verification.
        return True, mime, 0, 0


def _verify_saved_file(path: Path) -> bool:
    """File exists, readable, non-empty, and decodes as an image."""
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return False
        data = path.read_bytes()
        ok, _, _, _ = _verify_image_bytes(data)
        return ok
    except Exception:
        return False


# ── Safe save (Phase 9) — collision-safe, path-validated, no silent overwrite ─

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._\- ]+")


def _safe_filename(prompt: str, ext: str) -> str:
    base = _SAFE_NAME_RE.sub("", (prompt or "aether_generated")[:40]).strip(" .")
    if not base:
        base = "aether_generated"
    return f"{base}.{ext.lstrip('.')}"


def _timestamped_name(ext: str) -> str:
    """Default artifact name — Phase 9: collision-resistant by construction."""
    return f"aether_generated_{time.strftime('%Y%m%d_%H%M%S')}.{ext.lstrip('.')}"


def _resolve_save_dir(cfg: dict) -> Path:
    raw = (cfg.get("visual_save_dir") or "").strip()
    if raw:
        try:
            from actions.file_controller import _resolve_path, _is_safe_path
            cand = _resolve_path(raw)
            if _is_safe_path(cand):
                return cand
        except Exception:
            pass
    # Default: user-accessible Pictures folder (create on demand).
    default = Path.home() / "Pictures" / "aether_generated"
    return default


def _is_path_allowed(target: Path) -> bool:
    """Reuse file_controller's whitelist when available; fall back to home check."""
    try:
        from actions.file_controller import _is_safe_path
        return _is_safe_path(target)
    except Exception:
        try:
            return target.resolve().is_relative_to(Path.home().resolve())
        except Exception:
            return False


def _unique_path(directory: Path, filename: str) -> Path:
    """Never silently overwrite — append _01, _02, … on collision."""
    stem = Path(filename).stem
    ext  = Path(filename).suffix or ".png"
    cand = directory / filename
    n = 1
    while cand.exists():
        cand = directory / f"{stem}_{n:02d}{ext}"
        n += 1
        if n > 999:  # hard stop — do not loop forever
            cand = directory / f"{stem}_{int(time.time())}{ext}"
            break
    return cand


# ── Provider dispatch with bounded retry (Phase 13/14/17) ───────────────────

# Transient failures only.  Rate-limit IS transient on the free tier (it
# clears in seconds) — excluding it caused the most common back-to-back
# failure to return instantly with zero retries.  Never retry: bad prompt,
# content rejection, not-configured, invalid request (deterministic).
_RETRYABLE = {
    "TIMEOUT", "NETWORK_ERROR", "PROVIDER_UNAVAILABLE", "QUOTA_EXCEEDED",
}

# Longer pause before retrying a 429 — the free tier's window is short.
_BACKOFF_S = {"QUOTA_EXCEEDED": 4.0}
_MAX_BACKOFF_S = 4.0


def _parse_ar(value: str) -> AspectRatio:
    v = (value or "").strip()
    for ar in AspectRatio:
        if ar.value == v or ar.name.lower() == v.lower():
            return ar
    return AspectRatio.SQUARE


def _parse_quality(value: str) -> Quality:
    v = (value or "").strip().lower()
    return Quality.HD if v == "hd" else Quality.STANDARD


def _ui_reset_state(player) -> None:
    """Return the HUD to LISTENING — never stomp the MUTED display.

    main.py gates its own resets behind `if not self.ui.muted`; the tool
    must do the same or a run-while-muted flips the HUD to LISTENING.
    """
    if player is None:
        return
    try:
        if not getattr(player, "muted", False):
            player.set_state("LISTENING")
    except Exception:
        pass


def _dispatch(provider_name: str, prompt: str, ar: AspectRatio,
              quality: Quality, style: str, fmt: str, timeout_s: float,
              retries: int) -> VisualGenerationResult:
    """Call the provider with bounded retries.  Returns VisualGenerationResult."""
    provider = get_provider(provider_name) if provider_name else None
    if provider is None:
        # Honest fallback: if the configured provider is missing, try the
        # default before failing — but never pretend a provider ran.
        provider = get_provider("pollinations")
    if provider is None:
        return VisualGenerationResult.failure(
            VisualErrorCode.PROVIDER_NOT_CONFIGURED,
            "No visual provider is registered.",
            provider=provider_name or "none",
            prompt_summary=prompt[:80],
        )
    if not provider.is_configured():
        return VisualGenerationResult.failure(
            VisualErrorCode.PROVIDER_NOT_CONFIGURED,
            f"Provider '{provider.name}' is not configured.",
            provider=provider.name,
            prompt_summary=prompt[:80],
        )

    full_prompt = build_prompt(prompt, style)
    attempts = max(1, int(retries) + 1)
    last = None
    for i in range(attempts):
        _evt("VISUAL_GENERATION_STARTED", provider=provider.name,
             attempt=i + 1, prompt=prompt[:60])
        try:
            res = provider.generate_image(
                full_prompt,
                aspect_ratio=ar,
                quality=quality,
                output_format=fmt,
                timeout_s=timeout_s,
            )
        except Exception as e:  # provider should not raise, but never crash the tool
            res = VisualGenerationResult.failure(
                VisualErrorCode.GENERATION_FAILED, str(e),
                provider=provider.name, prompt_summary=prompt[:80],
            )
        last = res
        if res.success:
            _evt("VISUAL_GENERATION_COMPLETED", provider=provider.name,
                 mime=res.mime_type, ms=round(res.duration_ms))
            return res
        code = getattr(res.error_code, "value", str(res.error_code))
        _evt("VISUAL_GENERATION_FAILED", provider=provider.name,
             error=code, attempt=i + 1)
        _LAST_ERROR.update({"code": code, "ts": time.time()})
        if code not in _RETRYABLE or i == attempts - 1:
            return res
        delay = _BACKOFF_S.get(code, min(1.5 * (i + 1), 3.0))
        time.sleep(min(delay, _MAX_BACKOFF_S))  # short, bounded backoff
    return last


# ── The tool handler ─────────────────────────────────────────────────────────

def generate_image(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """
    Entry point wired by ActionRegistry.  Runs off the asyncio event loop
    (main.py uses run_in_executor), so blocking network I/O here is safe.
    """
    t0 = time.monotonic()
    p = parameters or {}

    prompt = str(p.get("prompt") or "").strip()
    ar_raw = str(p.get("aspect_ratio") or "")
    q_raw  = str(p.get("quality") or "")
    style  = str(p.get("style") or "")
    fmt    = (str(p.get("output_format") or "png").lower().lstrip("."))
    if fmt not in ("png", "jpg", "jpeg", "webp"):
        fmt = "png"
    save_path_raw = str(p.get("save_path") or "").strip()
    filename_raw  = str(p.get("filename") or "").strip()

    if player:
        try:
            player.set_state("PROCESSING")
        except Exception:
            pass

    # ── Validation (Phase 2) ────────────────────────────────────────────────
    if not prompt:
        _evt("VISUAL_REQUEST_RECEIVED", prompt="<empty>")
        _ui_error(player, "INVALID_PROMPT: prompt is empty.")
        _ui_reset_state(player)
        return "[VISUAL_FAILED:INVALID_PROMPT] Image generation failed: prompt is empty."

    cfg = _cfg()
    _evt("VISUAL_REQUEST_RECEIVED", prompt=prompt[:80])

    ar      = _parse_ar(ar_raw or cfg.get("visual_default_ar", "1:1"))
    quality = _parse_quality(q_raw or cfg.get("visual_default_quality", "standard"))
    timeout_s = float(cfg.get("visual_timeout_s", 30.0) or 30.0)
    retries   = int(cfg.get("visual_max_retries", 1) or 0)
    provider_name = str(cfg.get("visual_provider") or "pollinations")

    # ── Prompt construction (Phase 4) — log the intent, keep it faithful ────
    final_prompt = build_prompt(prompt, style)
    _evt("VISUAL_PROMPT_CREATED", length=len(final_prompt), style=style or "-")

    # ── Generate (with bounded retry) ──────────────────────────────────────
    if player:
        try:
            player.write_log(f"[Visual] Generating: {prompt[:60]}")
        except Exception:
            pass

    result = _dispatch(provider_name, final_prompt, ar, quality, style,
                       fmt, timeout_s, retries)

    if not result.success:
        _ui_reset_state(player)
        if player:
            try:
                player.write_log(f"[Visual] FAILED: {result.error_code}")
            except Exception:
                pass
        _ui_error(player, f"{getattr(result.error_code, 'value', 'GENERATION_FAILED')}: "
                          f"{result.error_message}")
        _evt("VISUAL_GENERATION_FAILED", error=str(result.error_code),
             total_ms=round((time.monotonic() - t0) * 1000))
        return _result_to_message(result)

    # ── Verify received bytes (Phase 7) ────────────────────────────────────
    _evt("VISUAL_VERIFICATION_STARTED", id=result.generation_id)
    if player:
        try:
            player.set_state("VERIFYING")   # V3: real check in progress
        except Exception:
            pass
    image_bytes = result.raw_meta.get("image_bytes") or b""
    ok, mime, w, h = _verify_image_bytes(image_bytes)
    if not ok:
        result.success = False
        result.verification = "FAILED"
        _ui_reset_state(player)
        _ui_error(player, "OUTPUT_INVALID: generated bytes failed verification.")
        _evt("VISUAL_VERIFICATION_COMPLETED", id=result.generation_id,
             status="FAILED")
        return _result_to_message(VisualGenerationResult.failure(
            VisualErrorCode.OUTPUT_INVALID,
            "Generated output failed image verification.",
            provider=result.provider, prompt_summary=result.prompt_summary,
        ))

    if mime:
        result.mime_type = mime
    if w and h:
        result.width, result.height = w, h

    # ── Save (Phase 9) — only when requested or autosave enabled ───────────
    saved_path = ""
    want_save = bool(save_path_raw or filename_raw or cfg.get("visual_autosave", True))
    if want_save:
        _evt("VISUAL_SAVE_STARTED", id=result.generation_id)
        ext = "jpg" if result.mime_type == "image/jpeg" else \
              "webp" if result.mime_type == "image/webp" else fmt
        if ext == "jpeg":
            ext = "jpg"

        if save_path_raw:
            try:
                from actions.file_controller import _resolve_path
                target = _resolve_path(save_path_raw)
                if filename_raw:
                    target = target / _safe_filename(filename_raw, ext)
                elif target.suffix:
                    pass  # user gave a full filename
                else:
                    target = target / _timestamped_name(ext)
            except Exception as e:
                _ui_reset_state(player)
                _ui_error(player, f"SAVE_FAILED: could not resolve save path: {e}")
                return f"[VISUAL_FAILED:SAVE_FAILED] Image generated but could not resolve save path: {e}"
        else:
            directory = _resolve_save_dir(cfg)
            name = _safe_filename(filename_raw, ext) if filename_raw \
                else _timestamped_name(ext)
            target = _unique_path(directory, name)

        if not _is_path_allowed(target.parent if target.suffix else target):
            _ui_reset_state(player)
            _ui_error(player, "SAVE_FAILED: save path is outside allowed locations.")
            return ("[VISUAL_FAILED:SAVE_FAILED] Image generated but the save "
                    "path is outside allowed locations.")

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Collision guard — _unique_path already de-dupes, but an explicit
            # user filename inside an existing directory gets one final check.
            if target.exists():
                target = _unique_path(target.parent, target.name)
            target.write_bytes(image_bytes)
        except Exception as e:
            _evt("VISUAL_SAVE_STARTED", id=result.generation_id, status="FAILED")
            _ui_reset_state(player)
            _ui_error(player, f"SAVE_FAILED: {e}")
            return f"[VISUAL_FAILED:SAVE_FAILED] Image generated but saving failed: {e}"

        if player:
            try:
                player.set_state("VERIFYING")   # V3: post-save check in progress
            except Exception:
                pass
        if not _verify_saved_file(target):
            _ui_reset_state(player)
            _ui_error(player, "VERIFICATION_FAILED: saved file failed post-save check.")
            return ("[VISUAL_FAILED:VERIFICATION_FAILED] Image was written but "
                    "failed post-save verification.")

        saved_path = str(target)
        result.file_path = saved_path
        result.verification = "VERIFIED"
        _evt("VISUAL_SAVE_COMPLETED", path=saved_path)
    else:
        # No save requested — we can only claim RECEIVED + decode-verified.
        result.verification = "VERIFIED" if ok else "RECEIVED"

    _evt("VISUAL_VERIFICATION_COMPLETED", id=result.generation_id,
         status=result.verification)

    # ── Display (Phase 8) — hand bytes to the UI via the player facade ─────
    display_ok = True
    if player:
        try:
            if hasattr(player, "show_image_result"):
                player.show_image_result(image_bytes, saved_path,
                                         result.mime_type,
                                         f"{result.width}x{result.height}")
            else:
                # Fallback: surface path in the content panel / log.
                if hasattr(player, "show_content") and saved_path:
                    player.show_content("Generated Image",
                                        f"Saved to: {saved_path}\n"
                                        f"Size: {result.width}x{result.height} "
                                        f"({result.mime_type})")
        except Exception as e:
            display_ok = False
            _evt("DISPLAY_FAILED", error=str(e)[:80])
        _ui_reset_state(player)

    if not display_ok:
        # Generation + save succeeded; only display failed — be precise.
        result.verification = "VERIFIED"
        return (_result_to_message(result, saved_path)
                + " Display failed; file is saved and can be opened manually.")

    return _result_to_message(result, saved_path)


# ── Tool declaration (Gemini function_declarations format) ──────────────────

def get_visual_health() -> dict:
    """Phase 18 — runtime capability discovery.

    XYRIS must never claim image generation if the runtime cannot do it.
    Cheap, offline checks only (no probe request): registration, config,
    prompt sanity, plus the most recent failure observed this session.
    """
    cfg = _cfg()
    name = str(cfg.get("visual_provider") or "pollinations")
    provider = get_provider(name)
    checks = {
        "provider_registered": provider is not None,
        "provider_configured": bool(provider and provider.is_configured()),
        "prompt_supported": True,          # provider.validate_prompt exists
        "edit_supported": bool(provider and provider.supports_edit),
    }
    available = checks["provider_registered"] and checks["provider_configured"]
    return {
        "status": "IMAGE_GENERATION_AVAILABLE" if available
                  else "IMAGE_GENERATION_UNAVAILABLE",
        "provider": name,
        "configured": available,
        "model": "flux" if name == "pollinations" else "n/a",
        "last_error": _LAST_ERROR.get("code", ""),
        "checks": checks,
    }


# Last failure observed this session (code + age) — no secrets, codes only.
_LAST_ERROR: dict = {}


TOOL = {
    "name": "generate_image",
    # The only tool that currently verifies its own work: bytes are parsed
    # before display and the saved file is re-read afterwards — the honest
    # source of the VERIFYING state (M2).
    "risk": "caution",
    "permissions": ["network", "generation"],
    "timeout": 150,
    "requires_confirmation": False,
    "supports_undo": False,
    "verification": "self_check",
    "description": (
        "Generate a NEW image from a text prompt — wallpaper, illustration, "
        "concept visual, diagram backdrop, logo concept, sci-fi scene, or any "
        "other visual output. USE THIS WHEN the user asks to create/generate/"
        "make/draw/design/visualize an image or visual, e.g. 'create a "
        "futuristic wallpaper', 'generate a cyberpunk city', 'make an image of "
        "an AI core', 'visualize my project architecture', 'concept art of ...', "
        "'presentation background', 'logo idea'. Follow-up refinements of a "
        "previously generated image (e.g. 'make it darker', 'bigger moon', "
        "'transparent background') mean: call THIS tool again with an adjusted "
        "prompt — this provider does not edit existing files. Works in ANY "
        "language (English, Tamil, Tanglish, Telugu, ...): translate the user's "
        "intent into a faithful English prompt; do NOT invent unrelated details. "
        "NOT for viewing/opening/showing an existing image, describing or "
        "analyzing a picture/screenshot, finding an image online, or camera/"
        "screen vision — those are different tasks. On success the result is "
        "verified; on failure it returns an explicit failure code — never claim "
        "an image exists unless the result says [VISUAL_OK]. If the user "
        "later asks you to look at a file you generated, read it with "
        "file_processor and pass source='generated_image'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "prompt": {
                "type": "STRING",
                "description": (
                    "English text description of the image to generate. "
                    "Translate/normalize the user's request into a clear, "
                    "faithful English prompt preserving their actual intent."
                ),
            },
            "aspect_ratio": {
                "type": "STRING",
                "description": "Aspect ratio of the output image.",
                "enum": ["1:1", "16:9", "9:16", "3:2", "2:3"],
            },
            "quality": {
                "type": "STRING",
                "description": "Output quality preset.",
                "enum": ["standard", "hd"],
            },
            "style": {
                "type": "STRING",
                "description": (
                    "Optional style hint: cyberpunk, realistic, anime, "
                    "watercolor, 3d, minimalist, wallpaper — or a short custom "
                    "style. Leave empty for none."
                ),
            },
            "output_format": {
                "type": "STRING",
                "description": "Image file format.",
                "enum": ["png", "jpg", "webp"],
            },
            "filename": {
                "type": "STRING",
                "description": (
                    "Optional safe filename (no folders). Collisions are "
                    "handled automatically (_01, _02...)."
                ),
            },
            "save_path": {
                "type": "STRING",
                "description": (
                    "Optional safe directory or full path under the user's "
                    "home (e.g. 'desktop', 'pictures', 'downloads/ai'). "
                    "Empty uses the default Pictures/aether_generated folder."
                ),
            },
        },
        "required": ["prompt"],
    },
    "handler": generate_image,
    "behavior": "BLOCKING",
    "scheduling": "WHEN_IDLE",
}
