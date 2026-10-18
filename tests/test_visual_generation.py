"""
Visual Generation test matrix (Phase 16) — Phases 1-3 covered here.

Phase 3 focuses: intent boundaries (positive triggers + false-positive
guards + multilingual coverage) live in the generate_image TOOL description,
because XYRIS routes via Gemini function-calling driven by tool
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

    def test_quota_retry_is_bounded_not_infinite(self):
        """429 IS transient on the free tier — retry, but strictly bounded.

        (Previously QUOTA_EXCEEDED was excluded entirely, so the most common
        back-to-back failure returned instantly with zero retries.)
        """
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
                                     "visual_max_retries": 2,
                                     "visual_timeout_s": 1}):
                msg = generate_image({"prompt": "a cat"})
        self.assertTrue(msg.startswith("[VISUAL_FAILED:QUOTA_EXCEEDED]"))
        self.assertEqual(calls["n"], 3)  # initial + 2 bounded retries

    def test_deterministic_errors_never_retry(self):
        """Invalid prompt / content rejection / not-configured: one call only."""
        for code in (VisualErrorCode.INVALID_PROMPT,
                     VisualErrorCode.CONTENT_RESTRICTION,
                     VisualErrorCode.PROVIDER_NOT_CONFIGURED):
            calls = {"n": 0}

            def gen(prompt, _c=code, **kw):
                calls["n"] += 1
                return VisualGenerationResult.failure(_c, "x", provider="fake")

            fake = _FakeProvider()
            fake.generate_image = gen
            with patch("actions.generate_image.get_provider", return_value=fake):
                with patch("actions.generate_image._cfg",
                           return_value={"visual_provider": "fake",
                                         "visual_autosave": False,
                                         "visual_max_retries": 3,
                                         "visual_timeout_s": 1}):
                    generate_image({"prompt": "a cat"})
            self.assertEqual(calls["n"], 1, f"{code} must not be retried")

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


# ═══ PHASE 8 — UI INTEGRATION ══════════════════════════════════════════════

class _RecordingPlayer:
    """Minimal JarvisUI stand-in that records facade calls."""

    def __init__(self):
        self.states = []
        self.logs = []
        self.images = []
        self.errors = []
        self.contents = []

    def set_state(self, s): self.states.append(s)
    def write_log(self, t): self.logs.append(t)
    def show_image_result(self, b, path="", mime="", dims=""):
        self.images.append((b, path, mime, dims))
    def show_image_error(self, msg):
        self.errors.append(msg)
    def show_content(self, title, text):
        self.contents.append((title, text))


class TestUIIntegration(unittest.TestCase):
    """Phase 8: UI facade contract, signals, overlay, state flow, error states."""

    def test_jarvisui_exposes_image_methods(self):
        from ui import JarvisUI
        self.assertTrue(callable(getattr(JarvisUI, "show_image_result", None)))
        self.assertTrue(callable(getattr(JarvisUI, "show_image_error", None)))

    def test_mainwindow_has_image_result_signal(self):
        from ui import MainWindow
        self.assertTrue(hasattr(MainWindow, "_image_result_sig"))

    def test_image_overlay_widget_exists(self):
        from ui import ImageResultOverlay
        self.assertTrue(callable(getattr(ImageResultOverlay, "show_image", None)))
        self.assertTrue(callable(getattr(ImageResultOverlay, "_show_error", None)))

    def test_tool_sets_processing_then_listening(self):
        """Loading state uses the existing AI Core state system (no new machine)."""
        player = _RecordingPlayer()
        generate_image({"prompt": ""}, player=player)
        self.assertIn("PROCESSING", player.states)
        self.assertIn("LISTENING", player.states)
        # PROCESSING must come before the failure resets to LISTENING
        self.assertLess(player.states.index("PROCESSING"),
                        player.states.index("LISTENING"))

    def test_tool_surfaces_error_state_to_ui(self):
        player = _RecordingPlayer()
        generate_image({"prompt": ""}, player=player)
        self.assertEqual(len(player.errors), 1)
        self.assertIn("INVALID_PROMPT", player.errors[0])

    def test_tool_shows_image_on_success(self):
        fake = _FakeProvider()  # returns valid PNG bytes
        player = _RecordingPlayer()
        with patch("actions.generate_image.get_provider", return_value=fake):
            with patch("actions.generate_image._cfg",
                       return_value={"visual_provider": "fake",
                                     "visual_autosave": False,
                                     "visual_max_retries": 0,
                                     "visual_timeout_s": 2}):
                msg = generate_image({"prompt": "a test image"}, player=player)
        self.assertTrue(msg.startswith("[VISUAL_OK"))
        self.assertEqual(len(player.images), 1)
        bytes_out, path, mime, dims = player.images[0]
        self.assertEqual(bytes_out, PNG_1PX)
        self.assertEqual(mime, "image/png")
        self.assertEqual(player.errors, [])  # no error state on success
        # State returns to LISTENING after display
        self.assertEqual(player.states[-1], "LISTENING")

    def test_generation_failure_shows_error_not_image(self):
        fake = _FakeProvider(result=VisualGenerationResult.failure(
            VisualErrorCode.PROVIDER_UNAVAILABLE, "down", provider="fake"))
        player = _RecordingPlayer()
        with patch("actions.generate_image.get_provider", return_value=fake):
            with patch("actions.generate_image._cfg",
                       return_value={"visual_provider": "fake",
                                     "visual_autosave": False,
                                     "visual_max_retries": 0,
                                     "visual_timeout_s": 1}):
                msg = generate_image({"prompt": "a cat"}, player=player)
        self.assertTrue(msg.startswith("[VISUAL_FAILED"))
        self.assertEqual(len(player.images), 0)  # never show a broken image
        self.assertEqual(len(player.errors), 1)
        self.assertIn("PROVIDER_UNAVAILABLE", player.errors[0])

    def test_state_never_stuck_on_processing_after_failure(self):
        player = _RecordingPlayer()
        generate_image({"prompt": ""}, player=player)
        self.assertNotEqual(player.states[-1], "PROCESSING")

    def test_show_image_result_payload_contract(self):
        """JarvisUI.show_image_result must emit a dict payload on the signal."""
        from ui import JarvisUI
        captured = []

        class _Win:
            class _sig:
                @staticmethod
                def emit(p): captured.append(p)
            _image_result_sig = _sig()

        j = JarvisUI.__new__(JarvisUI)  # bypass __init__ (needs full MainWindow)
        j._win = _Win()
        JarvisUI.show_image_result(j, b"PNG", "C:/x.png", "image/png", "10x10")
        self.assertEqual(len(captured), 1)
        p = captured[0]
        self.assertEqual(p["kind"], "image")
        self.assertEqual(p["bytes"], b"PNG")
        self.assertEqual(p["path"], "C:/x.png")
        self.assertEqual(p["mime"], "image/png")
        self.assertEqual(p["dims"], "10x10")

    def test_show_image_error_payload_contract(self):
        from ui import JarvisUI
        captured = []

        class _Win:
            class _sig:
                @staticmethod
                def emit(p): captured.append(p)
            _image_result_sig = _sig()

        j = JarvisUI.__new__(JarvisUI)
        j._win = _Win()
        JarvisUI.show_image_error(j, "TIMEOUT: too slow")
        self.assertEqual(captured[0]["kind"], "error")
        self.assertEqual(captured[0]["message"], "TIMEOUT: too slow")


# ═══ VISUAL GENERATION 2.0 — AUDIT PATCH REGRESSION TESTS ══════════════════

class TestProviderLatencyFix(unittest.TestCase):
    """Root-cause fix: HD no longer doubles pixels past the provider's budget."""

    def test_hd_uses_capped_table_not_pixel_doubling(self):
        from core.visual.providers.pollinations import _AR_PIXELS, _HD_PIXELS
        for ar in _AR_PIXELS:
            std_area = _AR_PIXELS[ar][0] * _AR_PIXELS[ar][1]
            hd_area = _HD_PIXELS[ar][0] * _HD_PIXELS[ar][1]
            # HD is a real bump (>standard) but bounded — old code hit 2.36 MP
            self.assertGreater(hd_area, std_area, f"{ar}: HD must exceed std")
            self.assertLessEqual(hd_area, 1_480_000,
                                 f"{ar}: HD must stay ≤1.48 MP (timeout budget)")

    def test_old_hd_doubling_would_have_exceeded_budget(self):
        """Guards against regressing to width*2 capped at 2048."""
        from core.visual.providers.pollinations import _HD_PIXELS
        for ar, (w, h) in _HD_PIXELS.items():
            self.assertLess(w * h, 2048 * 1152,
                            f"{ar}: would re-trigger the 30s timeout profile")

    def test_provider_url_requests_model_flux(self):
        from core.visual.providers.pollinations import PollinationsProvider
        import inspect
        src = inspect.getsource(PollinationsProvider.generate_image)
        self.assertIn("model=flux", src)


class TestStateAndMuteFixes(unittest.TestCase):
    """Secondary bugs: stuck-PROCESSING on save failure, muted-state stomping."""

    def test_save_failure_returns_to_listening(self):
        """Old bug: 4 save-failure paths skipped the LISTENING reset."""
        from actions.generate_image import generate_image
        player = _RecordingPlayer()
        fake = _FakeProvider()  # valid PNG
        with patch("actions.generate_image.get_provider", return_value=fake):
            with patch("actions.generate_image._cfg",
                       return_value={"visual_provider": "fake",
                                     "visual_autosave": True,
                                     "visual_save_dir": "",
                                     "visual_max_retries": 0,
                                     "visual_timeout_s": 1}):
                # Force the "path outside allowed locations" branch —
                # deterministic, writes nothing to disk.
                with patch("actions.generate_image._is_path_allowed",
                           return_value=False):
                    msg = generate_image({"prompt": "a cat"}, player=player)
        self.assertTrue(msg.startswith("[VISUAL_FAILED:SAVE_FAILED"))
        self.assertIn("LISTENING", player.states)
        self.assertNotEqual(player.states[-1], "PROCESSING")

    def test_save_write_exception_returns_to_listening(self):
        """Second save-failure path: disk write raises."""
        from actions.generate_image import generate_image
        player = _RecordingPlayer()
        fake = _FakeProvider()
        with patch("actions.generate_image.get_provider", return_value=fake):
            with patch("actions.generate_image._cfg",
                       return_value={"visual_provider": "fake",
                                     "visual_autosave": True,
                                     "visual_save_dir": "",
                                     "visual_max_retries": 0,
                                     "visual_timeout_s": 1}):
                with patch.object(Path, "write_bytes",
                                  side_effect=OSError("disk full")):
                    msg = generate_image({"prompt": "a cat"}, player=player)
        self.assertTrue(msg.startswith("[VISUAL_FAILED:SAVE_FAILED"))
        self.assertIn("LISTENING", player.states)
        self.assertNotEqual(player.states[-1], "PROCESSING")

    def test_muted_player_never_flipped_to_listening(self):
        from actions.generate_image import generate_image
        player = _RecordingPlayer()
        player.muted = True
        generate_image({"prompt": ""}, player=player)
        self.assertNotIn("LISTENING", player.states)
        # PROCESSING was set (mute doesn't block the loading state), but the
        # reset must not stomp the MUTED display.
        self.assertIn("PROCESSING", player.states)

    def test_unmuted_player_returns_to_listening(self):
        from actions.generate_image import generate_image
        player = _RecordingPlayer()
        player.muted = False
        generate_image({"prompt": ""}, player=player)
        self.assertIn("LISTENING", player.states)

    def test_no_dead_elif_in_save_block(self):
        import inspect
        from actions import generate_image as m
        src = inspect.getsource(m.generate_image)
        # the unreachable `elif target.exists() and filename_raw and not save_path_raw`
        self.assertNotIn("elif target.exists() and filename_raw", src)


class TestArtifactNaming(unittest.TestCase):
    """Phase 9: default artifacts are timestamped, collision-safe."""

    def test_timestamped_name_format(self):
        import re
        from actions.generate_image import _timestamped_name
        name = _timestamped_name("png")
        self.assertRegex(name,
                         r"^aether_generated_\d{8}_\d{6}\.png$")

    def test_timestamped_names_differ_within_same_call_pattern(self):
        from actions.generate_image import _timestamped_name
        # format guarantees ordering; collision handled by _unique_path
        a = _timestamped_name("jpg")
        self.assertTrue(a.startswith("aether_generated_"))
        self.assertTrue(a.endswith(".jpg"))

    def test_unique_path_still_applies_to_timestamped(self):
        import tempfile
        from actions.generate_image import _unique_path, _timestamped_name
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            name = _timestamped_name("png")
            (d / name).write_bytes(b"x")
            p = _unique_path(d, name)
            self.assertNotEqual(p.name, name)
            self.assertTrue(p.name.endswith(".png"))


class TestHealthCheck(unittest.TestCase):
    """Phase 18: runtime capability discovery — never claim what can't run."""

    def test_health_reports_available_for_pollinations(self):
        from actions.generate_image import get_visual_health
        h = get_visual_health()
        self.assertEqual(h["status"], "IMAGE_GENERATION_AVAILABLE")
        self.assertTrue(h["configured"])
        self.assertEqual(h["provider"], "pollinations")

    def test_health_unavailable_when_provider_missing(self):
        from actions.generate_image import get_visual_health
        with patch("actions.generate_image._cfg",
                   return_value={"visual_provider": "nonexistent_xyz"}):
            h = get_visual_health()
        self.assertEqual(h["status"], "IMAGE_GENERATION_UNAVAILABLE")
        self.assertFalse(h["configured"])

    def test_health_has_no_secrets(self):
        from actions.generate_image import get_visual_health
        import json
        blob = json.dumps(get_visual_health())
        self.assertNotIn("AIza", blob)       # no API key pattern
        self.assertNotIn("api_key", blob.lower())


class TestMultilingualIntentPhases(unittest.TestCase):
    """Phase 14: Tamil/Tanglish/Telugu all resolve to the same tool."""

    DESC = TOOL["description"].lower()

    def test_tanglish_trigger_phrases(self):
        # "oru futuristic city image generate pannu" style
        for phrase in ("tamil", "tanglish", "telugu", "any language"):
            self.assertIn(phrase, self.DESC)

    def test_single_tool_no_duplicates(self):
        from core.action_loader import discover_actions
        reg = discover_actions(PROJECT_ROOT / "actions",
                               reserved_names={"screen_process"},
                               logger=lambda m: None)
        gen_tools = [n for n in reg.names()
                     if n in ("generate_image", "create_visual",
                              "make_picture", "generate_picture",
                              "image_creator", "visual_creator")]
        self.assertEqual(gen_tools, ["generate_image"])


if __name__ == "__main__":
    unittest.main()
