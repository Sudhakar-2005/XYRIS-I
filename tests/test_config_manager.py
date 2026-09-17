"""
Tests for the config manager — API key, voice, HUD style, and toggle management.

Verifies read/write operations, defaults, and persistence.
"""
import unittest
import sys
import json
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from memory.config_manager import (
    get_gemini_key, is_configured, get_assistant_name, get_voice,
    get_wake_word_enabled, get_push_to_talk_enabled, get_hud_style,
    get_brief_enabled, get_proactive_audio_enabled, get_media_resolution,
    get_thinking_enabled, get_input_device, get_output_device,
    AVAILABLE_VOICES, DEFAULT_VOICE,
)


class TestConfigManagerReads(unittest.TestCase):
    """Test reading from the actual config file."""

    def test_is_configured(self):
        result = is_configured()
        self.assertIsInstance(result, bool)

    def test_get_gemini_key(self):
        key = get_gemini_key()
        if is_configured():
            self.assertIsInstance(key, str)
            self.assertGreater(len(key), 0)

    def test_get_assistant_name(self):
        name = get_assistant_name()
        self.assertIsInstance(name, str)

    def test_get_voice(self):
        voice = get_voice()
        self.assertIsInstance(voice, str)

    def test_get_hud_style(self):
        style = get_hud_style()
        self.assertIn(style, ("face", "core"))

    def test_get_wake_word_enabled(self):
        result = get_wake_word_enabled()
        self.assertIsInstance(result, bool)

    def test_get_push_to_talk_enabled(self):
        result = get_push_to_talk_enabled()
        self.assertIsInstance(result, bool)

    def test_get_brief_enabled(self):
        result = get_brief_enabled()
        self.assertIsInstance(result, bool)

    def test_get_proactive_audio_enabled(self):
        result = get_proactive_audio_enabled()
        self.assertIsInstance(result, bool)

    def test_get_media_resolution(self):
        result = get_media_resolution()
        self.assertIsInstance(result, str)

    def test_get_thinking_enabled(self):
        result = get_thinking_enabled()
        self.assertIsInstance(result, bool)

    def test_get_input_device(self):
        result = get_input_device()
        # Can be None or a string
        self.assertTrue(result is None or isinstance(result, str))

    def test_get_output_device(self):
        result = get_output_device()
        self.assertTrue(result is None or isinstance(result, str))

    def test_available_voices_is_nonempty(self):
        self.assertIsInstance(AVAILABLE_VOICES, (list, tuple, dict))
        self.assertGreater(len(AVAILABLE_VOICES), 0)

    def test_default_voice_is_string(self):
        self.assertIsInstance(DEFAULT_VOICE, str)
        self.assertGreater(len(DEFAULT_VOICE), 0)


class TestConfigManagerPersistence(unittest.TestCase):
    """Test that config writes persist correctly."""

    def setUp(self):
        self.config_path = PROJECT_ROOT / "config" / "api_keys.json"
        self.backup = self.config_path.read_text(encoding="utf-8")

    def tearDown(self):
        self.config_path.write_text(self.backup, encoding="utf-8")

    def test_config_file_is_valid_json(self):
        data = json.loads(self.backup)
        self.assertIsInstance(data, dict)

    def test_config_has_required_keys(self):
        data = json.loads(self.backup)
        self.assertIn("gemini_api_key", data)
        self.assertIn("assistant_name", data)
        self.assertIn("hud_style", data)

    def test_roundtrip_config(self):
        data = json.loads(self.backup)
        original_name = data.get("assistant_name", "")
        # Write a modified value
        data["assistant_name"] = "TEST_NAME"
        self.config_path.write_text(json.dumps(data, indent=4), encoding="utf-8")
        # Verify it was written
        loaded = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(loaded["assistant_name"], "TEST_NAME")
        # Restore
        data["assistant_name"] = original_name
        self.config_path.write_text(json.dumps(data, indent=4), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
