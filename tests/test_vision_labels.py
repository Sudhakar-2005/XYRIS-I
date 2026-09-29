"""M7 — vision source labelling (audit §3.12).

The audit's ask: "sources (screen/camera/user image/generated image/browser)
are not explicitly labeled at the perception boundary. V3: add source tagging
in one choke point. Test: source-label unit tests." Covered here without any
network call:

* ``vision_labels`` vocabulary — the five sources, their labels, uniqueness,
  and the fail-loud behaviour for anything unknown.
* ``with_source_label`` — the side-call choke: prepends only for images, is
  a no-op without a source or without pixels, and never double-labels.
* ``_flush_pending_vision`` — the main-session choke: webcam vs screen, and
  the camera-close/busy bookkeeping around it.
* ``gemini.call(..., source=...)`` — the wiring: the label is applied before
  the ladder is even walked, so both the Live and the REST path get it.
* ``file_processor`` — the user-file path: default ``user_image``, a
  model-provided source honoured, junk falling back honestly.
* Prompt + source guards: all five labels are explained in [SELF], and no
  file outside the choke module may spell a label itself.
"""
from __future__ import annotations

import asyncio
import base64
import importlib
import inspect
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main  # noqa: E402
from core import gemini  # noqa: E402
from core import vision_labels  # noqa: E402

MAIN_PY     = (ROOT / "main.py").read_text(encoding="utf-8")
GEMINI_PY   = (ROOT / "core" / "gemini.py").read_text(encoding="utf-8")
PROMPT_TXT  = (ROOT / "core" / "prompt.txt").read_text(encoding="utf-8")
LABELS      = [vision_labels.label_for(s) for s in vision_labels.SOURCES]


class TestVocabulary(unittest.TestCase):
    """The five sources and nothing else — the whole point of one choke."""

    def test_sources_complete(self):
        self.assertEqual(
            vision_labels.SOURCES,
            ("screen", "camera", "user_image", "generated_image", "browser"),
        )

    def test_labels_unique(self):
        self.assertEqual(len(set(LABELS)), 5)

    def test_labels_formatted(self):
        for label in LABELS:
            self.assertTrue(label.startswith("[IMAGE SOURCE: "), label)
            self.assertTrue(label.endswith("]"), label)

    def test_label_for_unknown_raises(self):
        with self.assertRaises(ValueError) as ctx:
            vision_labels.label_for("webcam2")
        self.assertIn("unknown vision source", str(ctx.exception))

    def test_source_text_leads_with_label(self):
        out = vision_labels.source_text("camera", "what is this?")
        self.assertEqual(
            out, "[IMAGE SOURCE: WEBCAM]\n\nwhat is this?")

    def test_source_text_unknown_raises(self):
        with self.assertRaises(ValueError):
            vision_labels.source_text("nope", "text")


class TestImageDetection(unittest.TestCase):
    """What counts as pixels — decides whether a label is warranted."""

    def test_strings_and_bytes_are_not_images(self):
        self.assertFalse(vision_labels.is_image_item("just text"))
        self.assertFalse(vision_labels.is_image_item(b"\x89PNG raw"))
        self.assertFalse(vision_labels.is_image_item(None))

    def test_part_dict_and_pil_are_images(self):
        part = SimpleNamespace(
            inline_data=SimpleNamespace(data=b"x", mime_type="image/png"))
        self.assertTrue(vision_labels.is_image_item(part))
        self.assertTrue(vision_labels.is_image_item(
            {"inline_data": {"mime_type": "image/png", "data": "AA=="}}))
        from PIL import Image
        self.assertTrue(vision_labels.is_image_item(Image.new("RGB", (1, 1))))

    def test_mime_typed_object_is_image(self):
        obj = SimpleNamespace(mime_type="image/jpeg", other=1)
        self.assertTrue(vision_labels.is_image_item(obj))


class TestWithSourceLabel(unittest.TestCase):
    """The side-call choke point (core/gemini.py path)."""

    def setUp(self):
        from PIL import Image
        self.img = Image.new("RGB", (2, 2), (255, 0, 0))

    def test_no_source_returns_contents_untouched(self):
        contents = [self.img, "describe it"]
        self.assertIs(vision_labels.with_source_label(contents, None),
                      contents)

    def test_text_only_gets_no_label(self):
        contents = ["what is 2+2?", "also text"]
        self.assertIs(vision_labels.with_source_label(contents, "screen"),
                      contents)

    def test_label_precedes_the_image(self):
        out = vision_labels.with_source_label(
            [self.img, "describe it"], "user_image")
        self.assertEqual(out[0], "[IMAGE SOURCE: USER-PROVIDED FILE]")
        self.assertIs(out[1], self.img)
        self.assertEqual(out[2], "describe it")

    def test_single_bare_image_wrapped(self):
        out = vision_labels.with_source_label(self.img, "screen")
        self.assertEqual(out[0], "[IMAGE SOURCE: SCREEN CAPTURE]")
        self.assertIs(out[1], self.img)

    def test_already_labelled_is_idempotent(self):
        contents = ["[IMAGE SOURCE: SCREEN CAPTURE]", self.img, "q"]
        self.assertIs(
            vision_labels.with_source_label(contents, "screen"), contents)

    def test_unknown_source_raises(self):
        with self.assertRaises(ValueError):
            vision_labels.with_source_label([self.img, "q"], "hallucinated")


class _FakeSession:
    def __init__(self):
        self.sent = []

    async def send_client_content(self, **kw):
        self.sent.append(kw)


class TestFlushPendingVision(unittest.TestCase):
    """The main-session choke (main.py), by behaviour not by source."""

    @staticmethod
    def _bare(angle):
        live = main.JarvisLive.__new__(main.JarvisLive)
        live._pending_vision = (b"\x89PNG\r\n", "image/png",
                                "what is this?", angle)
        live.session          = _FakeSession()
        live._vision_cam_active    = (angle == "camera")
        live._vision_close_pending = False
        live._vision_busy          = True
        return live

    def _send(self, angle):
        live = self._bare(angle)
        sent = asyncio.run(live._flush_pending_vision())
        self.assertTrue(sent)
        self.assertIsNone(live._pending_vision)
        self.assertEqual(len(live.session.sent), 1)
        return live, live.session.sent[0]

    def test_webcam_frame_labelled_and_question_kept(self):
        live, call = self._send("camera")
        parts = call["turns"]["parts"]
        self.assertEqual(
            parts[0]["inline_data"]["data"],
            base64.b64encode(b"\x89PNG\r\n").decode("ascii"))
        self.assertTrue(
            parts[1]["text"].startswith("[IMAGE SOURCE: WEBCAM]"))
        self.assertIn("what is this?", parts[1]["text"])
        self.assertEqual(call["turns"]["role"], "user")
        self.assertTrue(call["turn_complete"])

    def test_screen_capture_labelled(self):
        live, call = self._send("screen")
        parts = call["turns"]["parts"]
        self.assertTrue(
            parts[1]["text"].startswith("[IMAGE SOURCE: SCREEN CAPTURE]"))
        self.assertIn("what is this?", parts[1]["text"])

    def test_camera_stays_busy_for_answer_then_closes(self):
        live, _ = self._send("camera")
        self.assertFalse(live._vision_cam_active)
        self.assertTrue(live._vision_close_pending)
        self.assertTrue(live._vision_busy)   # not cleared until speak

    def test_screen_frame_releases_busy_flag(self):
        live, _ = self._send("top")
        self.assertFalse(live._vision_busy)

    def test_no_pending_sends_nothing(self):
        live = self._bare("camera")
        live._pending_vision = None
        self.assertFalse(asyncio.run(live._flush_pending_vision()))
        self.assertEqual(live.session.sent, [])


class TestGeminiCallSource(unittest.TestCase):
    """gemini.call's source= wiring — applied before any model is walked."""

    def test_call_signature_exposes_source(self):
        params = inspect.signature(gemini.call).parameters
        self.assertIn("source", params)
        self.assertIsNone(params["source"].default)

    def test_label_applied_before_the_ladder(self):
        part = SimpleNamespace(
            inline_data=SimpleNamespace(data=b"x", mime_type="image/png"))
        seen = {}
        real = vision_labels.with_source_label

        def spy(contents, source):
            seen["source"] = source
            seen["out"] = real(contents, source)
            return seen["out"]

        with mock.patch.object(gemini, "with_source_label", spy), \
             mock.patch.object(gemini, "api_key", lambda: ""):
            # No key: returns None before touching the network — but the
            # label must already have been applied by then.
            self.assertIsNone(gemini.call([part, "q"], source="screen"))

        self.assertEqual(seen["source"], "screen")
        self.assertEqual(seen["out"][0], "[IMAGE SOURCE: SCREEN CAPTURE]")

    def test_no_source_leaves_contents_alone(self):
        seen = {}

        def spy(contents, source):
            seen["source"] = source
            return contents

        with mock.patch.object(gemini, "with_source_label", spy), \
             mock.patch.object(gemini, "api_key", lambda: ""):
            gemini.call(["pure text"])

        self.assertIsNone(seen["source"])

    def test_gemini_py_uses_the_choke(self):
        self.assertIn(
            "contents = with_source_label(contents, source)", GEMINI_PY)


class TestFileProcessorSource(unittest.TestCase):
    """The user-file path: honest default, model hint, junk fallback."""

    def setUp(self):
        from PIL import Image
        self.dir = Path(tempfile.mkdtemp(prefix="vl_test_"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.png = self.dir / "pic.png"
        Image.new("RGB", (4, 4), (0, 128, 255)).save(self.png)
        self.fp = importlib.import_module("actions.file_processor")

    def _call(self, params):
        captured = {}
        fake = SimpleNamespace(text="a description of the image")

        def fake_call(contents, **kwargs):
            captured.update(kwargs)
            captured["contents"] = contents
            return fake

        with mock.patch.object(gemini, "call", fake_call):
            out = self.fp._process_image(self.png, "describe", params)
        self.assertEqual(out, "a description of the image")
        return captured

    def test_default_source_is_user_image(self):
        kwargs = self._call({"save": False})
        self.assertEqual(kwargs["source"], "user_image")
        self.assertEqual(kwargs["timeout_ms"], 90000)

    def test_model_supplied_source_is_honoured(self):
        kwargs = self._call({"save": False, "source": "browser"})
        self.assertEqual(kwargs["source"], "browser")

    def test_junk_source_falls_back_to_user_image(self):
        kwargs = self._call({"save": False, "source": "banana"})
        self.assertEqual(kwargs["source"], "user_image")

    def test_tool_schema_documents_all_five_sources(self):
        desc = (self.fp.TOOL["parameters"]["properties"]["source"]
                ["description"])
        for token in vision_labels.SOURCES:
            self.assertIn(token, desc)


class TestScreenCallSites(unittest.TestCase):
    """Both screenshot side-calls must declare their source."""

    def _has_screen_source(self, relpath):
        code = (ROOT / relpath).read_text(encoding="utf-8")
        return 'source="screen"' in code

    def test_code_helper_screenshot_is_screen(self):
        self.assertTrue(self._has_screen_source("actions/code_helper.py"))

    def test_computer_control_screenshot_is_screen(self):
        self.assertTrue(self._has_screen_source("actions/computer_control.py"))


class TestPromptExplainsEveryLabel(unittest.TestCase):
    """The [SELF] block is the only place a label's *meaning* is written."""

    def test_all_five_labels_appear_verbatim(self):
        for label in LABELS:
            self.assertIn(label, PROMPT_TXT, f"{label} missing from [SELF]")

    def test_self_block_exists(self):
        self.assertIn("[SELF]", PROMPT_TXT)


class TestChokePointGuards(unittest.TestCase):
    """No file outside core/vision_labels.py may spell a label itself."""

    def _violators(self):
        bad = []
        for relpath in ["main.py", "core/gemini.py", "core/prompt.txt", *[
                str(p.relative_to(ROOT))
                for p in (ROOT / "actions").glob("*.py")]]:
            text = (ROOT / relpath).read_text(encoding="utf-8")
            # prompt.txt names the labels to *explain* them — that is its job.
            if relpath == "core/prompt.txt":
                continue
            if "[IMAGE SOURCE:" in text:
                bad.append(relpath)
        return bad

    def test_labels_are_defined_in_one_place(self):
        self.assertEqual(self._violators(), [])

    def test_vision_labels_defines_exactly_five(self):
        text = (ROOT / "core" / "vision_labels.py").read_text(
            encoding="utf-8")
        self.assertEqual(text.count("[IMAGE SOURCE:"), 5)

    def test_flush_route_through_source_text(self):
        self.assertIn(
            "source_text(CAMERA if angle == \"camera\" else SCREEN, question)",
            MAIN_PY)


if __name__ == "__main__":
    unittest.main()
