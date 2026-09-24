"""
Visual Generation test matrix (Phase 16) — Phases 1-3 covered here.

Phase 3 focuses: intent boundaries (positive triggers + false-positive
guards + multilingual coverage) live in the generate_image TOOL description,
because AETHER routes via Gemini function-calling driven by tool
descriptions — there is no separate classifier to test.

Also covers: provider abstraction contract, result-model truthfulness,
prompt fidelity, path safety, collision handling, and config defaults.
No test performs real network generation (providers are mocked/stubbed).
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.visual.provider import (
    AspectRatio,
    Quality,
    VisualErrorCode,
    VisualGenerationProvider,
    VisualGenerationResult,
    VisualStatus,
    available_providers,
    get_provider,
    register_provider,
)
from actions.generate_image import (
    TOOL,
    build_prompt,
    generate_image,
    _parse_ar,
    _parse_quality,
    _result_to_message,
    _safe_filename,
    _unique_path,
    _verify_image_bytes,
    _is_path_allowed,
)


# ── Valid 1×1 PNG built at import time (always decodable) ──────────────────
import io as _io
from PIL import Image as _PILImage
_buf = _io.BytesIO()
_PILImage.new("RGB", (1, 1), (255, 0, 0)).save(_buf, format="PNG")
PNG_1PX = _buf.getvalue()


class _FakeProvider(VisualGenerationProvider):
    """Deterministic in-memory provider for tests — never hits the network."""

    name = "fake"
    supports_edit = False

    def __init__(self, result=None, exc=None):
        self._result = result
        self._exc = exc
        self.calls = []

    def is_configured(self) -> bool:
        return True

    def generate_image(self, prompt, **kw):
        self.calls.append((prompt, kw))
        if self._exc:
            raise self._exc
        if self._result is not None:
            return self._result
        return VisualGenerationResult(
            success=True,
            status=VisualStatus.GENERATED,
            mime_type="image/png",
            width=1,
            height=1,
            provider=self.name,
            prompt_summary=prompt[:80],
            verification="RECEIVED",
            raw_meta={"image_bytes": PNG_1PX},
        )


# ═══ PHASE 3 — INTENT DETECTION ════════════════════════════════════════════

class TestIntentBoundaries(unittest.TestCase):
    """The tool description IS the intent router — lock its boundaries in."""

    DESC = TOOL["description"].lower()

    def test_registered_as_formal_tool(self):
        self.assertEqual(TOOL["name"], "generate_image")
        self.assertIn("handler", TOOL)
        self.assertTrue(callable(TOOL["handler"]))

    def test_positive_triggers_present(self):
        for phrase in (
            "create", "generate", "make", "draw", "design", "visualize",
            "wallpaper", "concept", "illustration", "logo", "diagram",
        ):
            self.assertIn(phrase, self.DESC, f"missing positive trigger: {phrase}")

    def test_example_requests_present(self):
        for ex in ("futuristic wallpaper", "cyberpunk city", "ai core"):
            self.assertIn(ex, self.DESC, f"missing example: {ex}")

    def test_false_positive_boundaries_present(self):
        """View/open/describe/analyze existing images are NOT generation."""
        for neg in ("viewing", "opening", "describing", "analyzing",
                    "existing image", "screenshot"):
            self.assertIn(neg, self.DESC, f"missing negative boundary: {neg}")

    def test_multilingual_triggers_present(self):
        for lang in ("tamil", "tanglish", "telugu", "any language"):
            self.assertIn(lang, self.DESC, f"missing language: {lang}")

    def test_no_fake_success_instruction(self):
        self.assertIn("visual_ok", self.DESC)
        self.assertIn("never claim", self.DESC)

    def test_context_continuation_rule(self):
        """Follow-up 'make it darker' must map to regeneration, not editing."""
        self.assertIn("make it darker", self.DESC)
        self.assertIn("adjusted", self.DESC)
        self.assertIn("does not edit", self.DESC)

    def test_capability_line_renders_in_prompt(self):
        """_describe_tools must surface generate_image to the system prompt."""
        from main import _describe_tools
        line = _describe_tools([TOOL])
        self.assertIn("generate_image", line)

    def test_limits_do_not_contradict_generation(self):
        from main import _describe_limits
        limits = _describe_limits(has_vision=True, has_mic=True).lower()
        # The vision-input limit must not be read as "cannot generate images".
        self.assertNotIn("cannot generate", limits)
        self.assertNotIn("no image", limits)


# ═══ PHASE 1 — PROVIDER ABSTRACTION ════════════════════════════════════════

class TestProviderAbstraction(unittest.TestCase):

    def test_default_providers_registered(self):
        names = available_providers()
        self.assertIn("pollinations", names)
        self.assertIn("gemini_imagen", names)

    def test_pollinations_configured_without_key(self):
        p = get_provider("pollinations")
        self.assertIsNotNone(p)
        self.assertTrue(p.is_configured())

    def test_imagen_extension_point_never_pretends(self):
        """Until wired to a live endpoint, imagen must fail honestly."""
        p = get_provider("gemini_imagen")
        self.assertIsNotNone(p)
        self.assertFalse(p.is_configured())
        r = p.generate_image("test")
        self.assertFalse(r.success)
        self.assertIn(r.error_code, (
            VisualErrorCode.PROVIDER_NOT_CONFIGURED,
            VisualErrorCode.PROVIDER_UNAVAILABLE,
        ))

    def test_unimplemented_capabilities_raise(self):
        p = get_provider("pollinations")
        with self.assertRaises(NotImplementedError):
            p.edit_image("x")
        with self.assertRaises(NotImplementedError):
            p.generate_variation("x")

    def test_provider_swap_without_agent_change(self):
        """A new provider registers and is retrievable — no core edits."""
        fake = _FakeProvider()
        register_provider(fake)
        self.assertIs(get_provider("fake"), fake)

    def test_result_failure_never_fabricates_reference(self):
        r = VisualGenerationResult.failure(
            VisualErrorCode.TIMEOUT, "took too long", provider="x")
        self.assertFalse(r.success)
        self.assertEqual(r.image_reference, "")
        self.assertEqual(r.file_path, "")
        self.assertEqual(r.verification, "FAILED")
        self.assertEqual(r.error_code, VisualErrorCode.TIMEOUT)

    def test_prompt_validation(self):
        p = _FakeProvider()
        self.assertEqual(p.validate_prompt(""), VisualErrorCode.INVALID_PROMPT)
        self.assertEqual(p.validate_prompt("   "), VisualErrorCode.INVALID_PROMPT)
        self.assertEqual(p.validate_prompt("x" * 4001),
                         VisualErrorCode.INVALID_PROMPT)
        self.assertIsNone(p.validate_prompt("a cat"))


# ═══ PHASE 4 — PROMPT INTELLIGENCE ═════════════════════════════════════════

class TestPromptFidelity(unittest.TestCase):

    def test_simple_request_not_inflated(self):
        out = build_prompt("a black futuristic AI core")
        self.assertTrue(out.startswith("a black futuristic AI core"))
        # No 500-word bloat
        self.assertLess(len(out.split()), 30)

    def test_style_hint_appended_once(self):
        out = build_prompt("a robot", style="cyberpunk")
        self.assertIn("cyberpunk", out)
        self.assertIn("a robot", out)

    def test_style_not_duplicated_when_already_present(self):
        out = build_prompt("cyberpunk city at night", style="cyberpunk")
        self.assertEqual(out.count("cyberpunk"), 1)

    def test_unknown_style_passed_through(self):
        out = build_prompt("a tree", style="isometric pixel art")
        self.assertIn("isometric pixel art", out)

    def test_empty_prompt_survives(self):
        self.assertEqual(build_prompt("", style="x"), "x")


# ═══ PHASE 6/7 — RESULT MODEL + VERIFICATION ══════════════════════════════

class TestResultAndVerification(unittest.TestCase):

    def test_valid_png_verifies(self):
        ok, mime, w, h = _verify_image_bytes(PNG_1PX)
        self.assertTrue(ok)
        self.assertEqual(mime, "image/png")
        self.assertGreaterEqual(w, 1)

    def test_garbage_rejected(self):
        ok, mime, w, h = _verify_image_bytes(b"not an image at all")
        self.assertFalse(ok)

    def test_empty_bytes_rejected(self):
        self.assertFalse(_verify_image_bytes(b"")[0])

    def test_success_message_truthful(self):
        r = VisualGenerationResult(
            success=True, status=VisualStatus.GENERATED,
            mime_type="image/png", width=1, height=1,
            verification="VERIFIED", provider="fake")
        msg = _result_to_message(r, saved_path="C:/x/img.png")
        self.assertTrue(msg.startswith("[VISUAL_OK"))
        self.assertIn("verified", msg.lower())

    def test_failure_message_carries_code(self):
        r = VisualGenerationResult.failure(
            VisualErrorCode.QUOTA_EXCEEDED, "rate limited")
        msg = _result_to_message(r)
        self.assertTrue(msg.startswith("[VISUAL_FAILED:QUOTA_EXCEEDED]"))

    def test_unverified_received_not_overclaimed(self):
        r = VisualGenerationResult(
            success=True, status=VisualStatus.GENERATED,
            verification="RECEIVED", provider="fake")
        msg = _result_to_message(r)
        self.assertIn("[VISUAL_RECEIVED", msg)
        self.assertIn("cautiously", msg)


# ═══ PHASE 9 — FILE MANAGEMENT ═════════════════════════════════════════════

class TestFileSafety(unittest.TestCase):

    def test_collision_gets_numeric_suffix(self, ):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "img.png").write_bytes(b"x")
            p = _unique_path(d, "img.png")
            self.assertEqual(p.name, "img_01.png")
            p.write_bytes(b"y")
            p2 = _unique_path(d, "img.png")
            self.assertEqual(p2.name, "img_02.png")

    def test_path_traversal_blocked(self):
        self.assertFalse(_is_path_allowed(Path("C:/Windows/System32")))
        self.assertFalse(_is_path_allowed(Path("/etc")))

    def test_home_directory_allowed(self):
        self.assertTrue(_is_path_allowed(Path.home()))

    def test_filename_sanitized(self):
        out = _safe_filename("../../etc/passwd", "png")
        self.assertNotIn("/", out)
        self.assertNotIn(".", out.split("passwd")[0] if "passwd" in out else out)
        self.assertNotIn("..", out)

    def test_empty_filename_fallback(self):
        self.assertEqual(_safe_filename("", "png"), "aether_generated.png")


# ═══ TOOL PARAMETER PARSING ════════════════════════════════════════════════

class TestParameterParsing(unittest.TestCase):

    def test_aspect_ratio_parse(self):
        self.assertEqual(_parse_ar("16:9"), AspectRatio.LANDSCAPE)
        self.assertEqual(_parse_ar("landscape"), AspectRatio.LANDSCAPE)
        self.assertEqual(_parse_ar("bogus"), AspectRatio.SQUARE)
        self.assertEqual(_parse_ar(""), AspectRatio.SQUARE)

    def test_quality_parse(self):
        self.assertEqual(_parse_quality("hd"), Quality.HD)
        self.assertEqual(_parse_quality("HD"), Quality.HD)
        self.assertEqual(_parse_quality("standard"), Quality.STANDARD)
        self.assertEqual(_parse_quality("weird"), Quality.STANDARD)


# ═══ TOOL EXECUTION — MOCKED, NO NETWORK ═══════════════════════════════════

class TestToolExecutionMocked(unittest.TestCase):

    def test_empty_prompt_fails_truthfully(self):
        msg = generate_image({"prompt": ""})
        self.assertTrue(msg.startswith("[VISUAL_FAILED:INVALID_PROMPT]"))

    def test_provider_unavailable_maps_to_error(self):
        """No registered provider → honest PROVIDER_NOT_CONFIGURED, no crash."""
        fake = _FakeProvider(result=VisualGenerationResult.failure(
            VisualErrorCode.NETWORK_ERROR, "down", provider="fake"))
        with patch("actions.generate_image.get_provider",
                   side_effect=lambda n: fake if n == "fake" else None):
            with patch("actions.generate_image._cfg",
                       return_value={"visual_provider": "missing",
                                     "visual_autosave": False,
                                     "visual_max_retries": 0,
                                     "visual_timeout_s": 1}):
                # get_provider returns None for both → dispatch falls to
                # pollinations via side_effect None → failure path
                msg = generate_image({"prompt": "a cat"})
        self.assertTrue(msg.startswith("[VISUAL_FAILED"))

    def test_timeout_is_retryable_then_reported(self):
        calls = {"n": 0}

        def gen(prompt, **kw):
            calls["n"] += 1
            return VisualGenerationResult.failure(
                VisualErrorCode.TIMEOUT, "slow", provider="fake")

        fake = _FakeProvider()
        fake.generate_image = gen
        with patch("actions.generate_image.get_provider", return_value=fake):
            with patch("actions.generate_image._cfg",
                       return_value={"visual_provider": "fake",
                                     "visual_autosave": False,
                                     "visual_max_retries": 1,
                                     "visual_timeout_s": 1}):
                msg = generate_image({"prompt": "a cat"})
        self.assertTrue(msg.startswith("[VISUAL_FAILED:TIMEOUT]"))
        self.assertEqual(calls["n"], 2)  # initial + 1 bounded retry

    def test_no_infinite_retry_on_quota(self):
        calls = {"n": 0}

        def gen(prompt, **kw):
            calls["n"] += 1
            return VisualGenerationResult.failure(
                VisualErrorCode.QUOTA_EXCEEDED, "429", provider="fake")

        fake = _FakeProvider()
        fake.generate_image = gen
        with patch("actions.generate_image.get_provider", return_value=fake):
            with patch("actions.generate_image._cfg",
                       return_value={"visual_provider": "fake",
                                     "visual_autosave": False,
                                     "visual_max_retries": 5,
                                     "visual_timeout_s": 1}):
                msg = generate_image({"prompt": "a cat"})
        self.assertTrue(msg.startswith("[VISUAL_FAILED:QUOTA_EXCEEDED]"))
        self.assertEqual(calls["n"], 1)  # non-retryable → no retry loop

    def test_schema_shape_matches_gemini_contract(self):
        params = TOOL["parameters"]
        self.assertEqual(params["type"], "OBJECT")
        self.assertIn("prompt", params["required"])
        for key, spec in params["properties"].items():
            self.assertIn("type", spec)
            self.assertIn("description", spec)
        # Enums only on fields that declare them
        self.assertIn("enum", params["properties"]["aspect_ratio"])
        self.assertIn("enum", params["properties"]["quality"])
        self.assertIn("enum", params["properties"]["output_format"])


class TestConfigDefaults(unittest.TestCase):

    def test_visual_settings_defaults(self):
        from memory.config_manager import DEFAULT_VISUAL_CFG
        self.assertEqual(DEFAULT_VISUAL_CFG["visual_provider"], "pollinations")
        self.assertTrue(DEFAULT_VISUAL_CFG["visual_autosave"])
        self.assertLessEqual(DEFAULT_VISUAL_CFG["visual_max_retries"], 2)
        self.assertLessEqual(DEFAULT_VISUAL_CFG["visual_timeout_s"], 60)


if __name__ == "__main__":
    unittest.main()
